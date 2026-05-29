"""Per-race city geocoding.

Returns the set of cities inside the campaign's congressional district
boundary, with name + lat/lon + county + size class. Used by the
Geographic Overlay page to place markers automatically — no per-race
manual curation needed.

Pipeline:
  1. Load US Census Gazetteer Places file at module import (~32k rows).
  2. On request, compute the district's bounding box from its GeoJSON.
  3. Linear scan the gazetteer; keep only places inside the bbox AND
     matching the district's state.
  4. Sort by land area (proxy for population/importance) desc.
  5. Return top N.

The Gazetteer file is US Census public-domain data, refreshed yearly.

Works automatically for any US House race once `campaign.district` is
set. For non-US races this falls back gracefully (returns empty list).
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GAZETTEER_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "2024_Gaz_place_national.txt"

# LSAD codes worth keeping. Source: census.gov/library/reference/code-lists/legal-statistical-area-description-(lsad)-codes
# 21 = borough, 25 = city, 43 = town, 47 = township, 57 = CDP (community)
KEEP_LSAD = {"21", "25", "43", "47", "57"}


@dataclass
class GazetteerPlace:
    state: str    # USPS abbrev, e.g. "PA"
    name: str     # e.g. "Scranton" (cleaned of trailing "city"/"borough"/etc.)
    lsad: str     # type code
    lat: float
    lon: float
    aland: int    # land area in sq meters (proxy for size)


_PLACES_CACHE: Optional[list[GazetteerPlace]] = None


def _load_places() -> list[GazetteerPlace]:
    """Parse the Census Gazetteer file. Cached after first load."""
    global _PLACES_CACHE
    if _PLACES_CACHE is not None:
        return _PLACES_CACHE
    places: list[GazetteerPlace] = []
    if not GAZETTEER_PATH.exists():
        log.warning("gazetteer file not found at %s", GAZETTEER_PATH)
        _PLACES_CACHE = []
        return _PLACES_CACHE

    with GAZETTEER_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        # The Census file has trailing whitespace in column names; strip it.
        # Some rows are blanks at end of file — skip them.
        for row in reader:
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
            try:
                lsad = row.get("LSAD", "")
                if lsad not in KEEP_LSAD:
                    continue
                lat = float(row["INTPTLAT"])
                lon = float(row["INTPTLONG"])
                aland = int(row["ALAND"])
                # Strip trailing legal-type words from the name so "Scranton city"
                # becomes "Scranton". The Gazetteer always appends them.
                name = row["NAME"]
                for suffix in (
                    " city", " town", " borough", " township", " village",
                    " CDP", " (balance)", " municipality",
                ):
                    if name.endswith(suffix):
                        name = name[: -len(suffix)]
                        break
                places.append(GazetteerPlace(
                    state=row["USPS"], name=name, lsad=lsad,
                    lat=lat, lon=lon, aland=aland,
                ))
            except (KeyError, ValueError):
                continue
    log.info("loaded %d gazetteer places", len(places))
    _PLACES_CACHE = places
    return places


def _bbox_from_geojson(geojson: dict) -> Optional[tuple[float, float, float, float]]:
    """Return (min_lon, min_lat, max_lon, max_lat) from a GeoJSON
    FeatureCollection or Feature. Handles Polygon and MultiPolygon."""
    features = []
    if geojson.get("type") == "FeatureCollection":
        features = geojson.get("features", [])
    elif geojson.get("type") == "Feature":
        features = [geojson]
    elif geojson.get("type") in ("Polygon", "MultiPolygon"):
        features = [{"geometry": geojson, "type": "Feature"}]
    if not features:
        return None

    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")

    def absorb(coord_pair):
        nonlocal min_lat, min_lon, max_lat, max_lon
        lon, lat = coord_pair[0], coord_pair[1]
        if lon < min_lon: min_lon = lon
        if lon > max_lon: max_lon = lon
        if lat < min_lat: min_lat = lat
        if lat > max_lat: max_lat = lat

    def walk(geom):
        t = geom.get("type")
        coords = geom.get("coordinates", [])
        if t == "Polygon":
            for ring in coords:
                for c in ring:
                    absorb(c)
        elif t == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    for c in ring:
                        absorb(c)

    for feat in features:
        geom = feat.get("geometry") or {}
        walk(geom)

    if min_lat == float("inf"):
        return None
    return (min_lon, min_lat, max_lon, max_lat)


def get_race_cities(
    district_geojson: dict,
    district_code: str,
    limit: int = 12,
) -> list[dict]:
    """Find cities inside the district boundary, sorted by importance.

    Args:
        district_geojson: GeoJSON FeatureCollection for the district.
            (Comes from /api/race/district-geojson.)
        district_code: e.g. "PA-08" — used to filter cities to the
            district's state.
        limit: max cities to return.

    Returns:
        List of dicts: {id, name, lat, lon, state, lsad, aland}.
        Sorted by ALAND descending (rough population proxy).
        Empty list if input is malformed or no places match.
    """
    if not district_geojson or not district_code or "-" not in district_code:
        return []
    state_abbrev = district_code.split("-")[0].upper()

    bbox = _bbox_from_geojson(district_geojson)
    if not bbox:
        return []
    min_lon, min_lat, max_lon, max_lat = bbox

    places = _load_places()
    inside: list[GazetteerPlace] = [
        p for p in places
        if p.state == state_abbrev
           and min_lat <= p.lat <= max_lat
           and min_lon <= p.lon <= max_lon
    ]
    # Bounding-box filter is loose — a district isn't a rectangle. For a
    # tighter filter, point-in-polygon test the GeoJSON. For V1 the bbox
    # filter is plenty (district shapes are roughly rectangular and we
    # sort by size + cap at `limit`).

    # Tier by LSAD then by land area within tier. Cities (25) before
    # boroughs (21) before towns (43) before townships (47) before
    # CDPs (57). This stops sprawling rural CDPs from crowding out
    # politically important small cities like Hazleton.
    LSAD_TIER = {"25": 0, "21": 1, "43": 2, "47": 3, "57": 4}
    inside.sort(key=lambda p: (LSAD_TIER.get(p.lsad, 9), -p.aland))
    top = inside[:limit]

    return [
        {
            "id": _city_id(p.name),
            "name": p.name,
            "lat": p.lat,
            "lon": p.lon,
            "state": p.state,
            "lsad": p.lsad,
            "aland": p.aland,
        }
        for p in top
    ]


def _city_id(name: str) -> str:
    """Stable lowercase-hyphenated id from a city name."""
    return name.lower().replace(" ", "-").replace("'", "").replace(".", "")
