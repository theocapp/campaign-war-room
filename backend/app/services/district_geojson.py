"""Per-race district GeoJSON loader.

Returns a GeoJSON feature for the current campaign's congressional district.
Caches each district to disk on first request so subsequent requests are
instant. New races automatically work as long as the campaign.district
field is set in the standard format (e.g. "PA-08", "CA-12", "NY-14").

Data source: US Census TIGERweb (current 119th Congressional Districts layer).
This is free, authoritative, and updated each redistricting cycle.

Cache location: backend/data/districts/<DISTRICT>.geojson

If a district isn't in the cache and the upstream fetch fails (network down,
non-standard district code, etc.), returns None and the frontend falls back
to a stylized "no boundary available" map.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Cache dir lives alongside backend code so it's part of the deployment unit.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "districts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# US state postal abbreviation → 2-digit FIPS code.
# Required because TIGERweb queries use FIPS, but campaign configs use postal
# (PA, CA, etc.). This list is stable — FIPS codes don't change.
STATE_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06",
    "CO": "08", "CT": "09", "DE": "10", "DC": "11", "FL": "12",
    "GA": "13", "HI": "15", "ID": "16", "IL": "17", "IN": "18",
    "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23",
    "MD": "24", "MA": "25", "MI": "26", "MN": "27", "MS": "28",
    "MO": "29", "MT": "30", "NE": "31", "NV": "32", "NH": "33",
    "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44",
    "SC": "45", "SD": "46", "TN": "47", "TX": "48", "UT": "49",
    "VT": "50", "VA": "51", "WA": "53", "WV": "54", "WI": "55",
    "WY": "56", "PR": "72",
}

# US Census TIGERweb endpoint. Layer 54 = 119th Congressional Districts.
TIGER_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "tigerWMS_Current/MapServer/54/query"
)

DISTRICT_RE = re.compile(r"^([A-Z]{2})-(\d{2})$")


def parse_district_code(code: str) -> Optional[tuple[str, str]]:
    """'PA-08' → ('42', '08').  Returns None on bad input."""
    if not code:
        return None
    m = DISTRICT_RE.match(code.strip().upper())
    if not m:
        return None
    state_abbrev, district_num = m.group(1), m.group(2)
    fips = STATE_FIPS.get(state_abbrev)
    if not fips:
        return None
    return fips, district_num


def _fetch_from_tigerweb(state_fips: str, district_num: str) -> Optional[dict]:
    """Live-query US Census for the district boundary. Returns the feature
    object (not the wrapper FeatureCollection) or None if upstream fails."""
    params = {
        "where": f"STATE='{state_fips}' AND CD119='{district_num}'",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",  # WGS84 lat/lon
        "f": "geojson",
    }
    try:
        r = httpx.get(TIGER_URL, params=params, timeout=30.0)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("district fetch failed for FIPS=%s CD=%s: %s",
                    state_fips, district_num, exc)
        return None

    features = data.get("features") or []
    if not features:
        log.warning("district not found in TIGERweb: FIPS=%s CD=%s",
                    state_fips, district_num)
        return None
    return data  # Return full FeatureCollection — caller saves as-is.


def get_district_geojson(district_code: str) -> Optional[dict]:
    """Return GeoJSON FeatureCollection for the given district code.

    Pipeline:
      1. Validate code → state FIPS + district number
      2. Check disk cache (~360KB per district)
      3. If miss, fetch from US Census TIGERweb and cache
      4. Return parsed JSON

    Returns None if input is malformed or upstream fails.
    """
    parsed = parse_district_code(district_code)
    if not parsed:
        return None
    state_fips, district_num = parsed

    cache_path = CACHE_DIR / f"{district_code.upper()}.geojson"
    if cache_path.exists():
        try:
            with cache_path.open("r") as f:
                return json.load(f)
        except Exception as exc:
            log.warning("corrupted cache file %s (%s) — refetching", cache_path, exc)
            cache_path.unlink(missing_ok=True)

    data = _fetch_from_tigerweb(state_fips, district_num)
    if data is None:
        return None

    try:
        with cache_path.open("w") as f:
            json.dump(data, f)
        log.info("district geojson cached: %s (%d bytes)", cache_path,
                 cache_path.stat().st_size)
    except Exception as exc:
        log.warning("cache write failed for %s: %s", cache_path, exc)

    return data
