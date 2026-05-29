/**
 * Geographic Overlay — district map view.
 *
 * Leaflet + Carto-themed OpenStreetMap tiles. Shows:
 *   - District polygon from /api/race/district-geojson (cached per-district
 *     by the backend, fetched live from US Census TIGERweb)
 *   - City markers from /api/race/cities (top N places by land area in the
 *     district's bounding box, derived from the US Census Gazetteer)
 *
 * Both data sources are real. Click a city to see its name + Census-typed
 * place label.
 *
 * History note: this page previously had a side panel showing entity
 * lists, endorsement/attack/event counts, and a D/R stance bar — all
 * sourced from MOCK_ENTITIES / MOCK_RELATIONS and a `placeholderStats()`
 * hash function. The "LIVE MAP" badge falsely implied that content was
 * real. Stripped 2026-05-29 alongside the KG-policy retreat (see
 * CLAUDE.md). The map continues to surface the genuinely-real district
 * geography; everything that pretended to be real has been removed.
 */
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { MapPin } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'
import { api } from '@/api/client'
import { useTheme } from '@/components/ThemeToggle'

// ── City data model ──────────────────────────────────────────────────────
// Cities come from /api/race/cities — derived from the US Census Gazetteer
// inside the district's bounding box. Every field is real. The earlier
// `articleCount` / `stance` / generated `description` placeholders are
// gone; nothing per-city beyond the Census record is claimed here.

interface CityNode {
  id: string
  name: string
  lat: number
  lon: number
  lsad: string
  /** Human-readable category derived directly from LSAD ("city" / "borough"
   * / "town" / "township" / "community"). NOT a generated bio. */
  lsadLabel: string
  /** State abbreviation, straight from Census. */
  state: string
}

// ── Component ────────────────────────────────────────────────────────────

export function GeographicOverlay() {
  const [selectedCity, setSelectedCity] = useState<string | null>(null)
  const [districtGeoJSON, setDistrictGeoJSON] = useState<GeoJSON.FeatureCollection | null>(null)
  const [cities, setCities] = useState<CityNode[]>([])
  const [loadingMap, setLoadingMap] = useState(true)
  const [district, setDistrict] = useState<string>('')
  const theme = useTheme()

  useEffect(() => {
    Promise.all([
      api.raceDistrictGeoJSON()
        .then(g => setDistrictGeoJSON(g))
        .catch(() => setDistrictGeoJSON(null)),
      api.raceCities(12)
        .then(res => {
          setDistrict(res.district)
          const lsadName: Record<string, string> = {
            '21': 'borough', '25': 'city', '43': 'town', '47': 'township', '57': 'community',
          }
          const augmented: CityNode[] = res.cities.map(c => ({
            id: c.id, name: c.name, lat: c.lat, lon: c.lon, lsad: c.lsad,
            lsadLabel: lsadName[c.lsad] ?? 'place',
            state: c.state,
          }))
          setCities(augmented)
        })
        .catch(() => setCities([])),
    ]).finally(() => setLoadingMap(false))
  }, [])

  const selected = selectedCity ? cities.find(c => c.id === selectedCity) : null

  // Every marker shares the same radius — the data doesn't support
  // size-coding cities by anything real (the prior "scaled by article
  // volume" was sourced from placeholderStats, which made up the count).
  const MARKER_RADIUS = 9

  // Map view defaults — center on PA-08 if no boundary yet.
  // Once boundary loads, FitBoundsToGeoJSON inside the map auto-fits.
  const fallbackCenter: [number, number] = [41.25, -75.7]
  const fallbackZoom = 9

  return (
    <div style={{ height: 'calc(100vh - 48px)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)', color: 'var(--text-1)' }}>
      {/* Header bar */}
      <div style={{ flexShrink: 0, padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <MapPin size={18} color="#a78bfa" />
          <div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>
              Geographic Overlay
              <span style={{ fontSize: 11, color: 'var(--text-3)', marginLeft: 6, padding: '2px 7px', background: 'var(--bg-3)', borderRadius: 4, fontWeight: 600, letterSpacing: '0.06em' }}>DISTRICT MAP</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 1 }}>
              {district || 'district'} · {cities.length} cities · US Census Gazetteer + TIGERweb boundary
            </div>
          </div>
        </div>
      </div>

      {/* Main: map + side panel */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden', background: 'var(--bg-sidebar)' }}>
          <MapContainer
            center={fallbackCenter}
            zoom={fallbackZoom}
            scrollWheelZoom
            style={{ width: '100%', height: '100%', background: 'var(--bg-sidebar)' }}
            // Dark-theme integration — the map should feel like part of the app.
            attributionControl
          >
            {/* Carto tiles — themed via ThemedTileLayer below, which uses
                Leaflet's imperative API to swap the URL on theme change
                (react-leaflet's TileLayer doesn't react to the `url` prop
                changing once mounted). */}
            <ThemedTileLayer theme={theme} />

            {/* District boundary overlay */}
            {districtGeoJSON && (
              <>
                <GeoJSON
                  data={districtGeoJSON}
                  style={{
                    color: 'var(--accent)',
                    weight: 2.5,
                    opacity: 0.9,
                    fillColor: 'var(--accent)',
                    fillOpacity: 0.06,
                    dashArray: '4 4',
                  }}
                />
                <FitBoundsToGeoJSON data={districtGeoJSON} />
              </>
            )}

            {/* City markers.
                Label-collision strategy: only the top-N most-mentioned cities
                get a PERMANENT label below the marker. Other cities show
                their name only on hover. The selected city always gets a
                label. Direction alternates above/below by index to stagger
                stacks where cities cluster. */}
            {/* CollisionAwareLabels computes which cities can show a permanent
                label without overlapping higher-priority ones, and emits the
                marker + label children. Recomputes on map pan/zoom. */}
            <CollisionAwareLabels cities={cities} />

            {/* City markers — uniform size and color. Labels are handled by
                CollisionAwareLabels above. Selected city gets a brighter
                outline. */}
            {cities.map(c => {
              const isSelected = selectedCity === c.id
              return (
                <CircleMarker
                  key={c.id}
                  center={[c.lat, c.lon]}
                  radius={MARKER_RADIUS}
                  pathOptions={{
                    color: isSelected ? 'var(--accent)' : 'var(--text-1)',
                    weight: isSelected ? 3 : 1.2,
                    opacity: 0.9,
                    fillColor: 'var(--accent)',
                    fillOpacity: 0.55,
                  }}
                  eventHandlers={{
                    click: () => setSelectedCity(c.id),
                  }}
                />
              )
            })}
          </MapContainer>

          {/* Loading state */}
          {loadingMap && (
            <div style={{
              position: 'absolute', top: 14, left: 14,
              background: 'var(--bg-2)', border: '1px solid var(--border)',
              borderRadius: 8, padding: '8px 12px', fontSize: 12, color: 'var(--text-2)',
              boxShadow: 'var(--shadow-elev)',
            }}>
              Loading district boundary…
            </div>
          )}
          {!loadingMap && !districtGeoJSON && (
            <div style={{
              position: 'absolute', top: 14, left: 14,
              background: 'rgba(127, 29, 29, 0.94)', border: '1px solid #991b1b',
              borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#fef2f2',
            }}>
              ⚠ District boundary unavailable — set <code>campaign.district</code> to STATE-NN format
            </div>
          )}

        </div>

        {/* Side panel: selected city — just the Census facts. No fabricated
            article counts, stance, or entity lists.  */}
        {selected && (
          <div style={{ width: 320, flexShrink: 0, borderLeft: '1px solid var(--border)', background: 'var(--bg-2)', overflowY: 'auto' }}>
            <div style={{ padding: '18px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <MapPin size={12} color="var(--text-2)" />
                <span style={{ fontSize: 10, color: 'var(--text-2)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em' }}>
                  {selected.lsadLabel} in {district}
                </span>
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.2, marginBottom: 6 }}>
                {selected.name}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 12 }}>
                {selected.lat.toFixed(4)}°, {selected.lon.toFixed(4)}° &middot; {selected.state}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 16, fontStyle: 'italic', lineHeight: 1.5 }}>
                Per-city article volume, stance, and entity activity are not
                currently tracked. The map shows the district boundary and
                Census-listed places only.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Sub-components ──────────────────────────────────────────────────────

/** Collision-aware label layer. Computes which cities can show a label
 *  without overlapping a higher-priority city's label, picks the best
 *  direction (top/bottom/left/right) for each that has room, and falls
 *  back to no-label-shown for the lowest-priority cities when space is
 *  tight. Recomputes on map pan/zoom.
 *
 *  Priority order: LSAD tier (city > borough > town > township > CDP),
 *  then within tier by land area. So strategic cities always win when
 *  labels can't all fit. */
function CollisionAwareLabels({ cities }: { cities: CityNode[] }) {
  const map = useMap()
  const [tick, setTick] = useState(0)

  // Re-evaluate label placement when the map view changes
  useEffect(() => {
    const bump = () => setTick(t => t + 1)
    map.on('zoom', bump)
    map.on('move', bump)
    map.on('viewreset', bump)
    return () => {
      map.off('zoom', bump)
      map.off('move', bump)
      map.off('viewreset', bump)
    }
  }, [map])

  // Compute screen positions + which labels fit
  const placement = useMemo(() => {
    void tick // dependency so we recompute on map change
    const LSAD_TIER: Record<string, number> = { '25': 0, '21': 1, '43': 2, '47': 3, '57': 4 }
    const ranked = [...cities].sort((a, b) => {
      const ta = LSAD_TIER[a.lsad] ?? 9
      const tb = LSAD_TIER[b.lsad] ?? 9
      if (ta !== tb) return ta - tb
      return a.name.localeCompare(b.name) // stable tiebreak inside the same tier
    })

    // Approx label bbox in screen pixels: width = 7px/char, height = 26 (name + count)
    const labelSize = (name: string) => ({ w: 9 + name.length * 6.5, h: 26 })
    const placed: Array<{ city: CityNode; px: number; py: number; dir: 'top'|'bottom'|'left'|'right'; bbox: { x: number; y: number; w: number; h: number } }> = []

    function bboxFor(px: number, py: number, name: string, dir: 'top'|'bottom'|'left'|'right', r: number) {
      const { w, h } = labelSize(name)
      switch (dir) {
        case 'bottom': return { x: px - w / 2, y: py + r + 4, w, h }
        case 'top':    return { x: px - w / 2, y: py - r - 4 - h, w, h }
        case 'right':  return { x: px + r + 4, y: py - h / 2, w, h }
        case 'left':   return { x: px - r - 4 - w, y: py - h / 2, w, h }
      }
    }

    function intersects(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
      return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y)
    }

    for (const city of ranked) {
      const pt = map.latLngToContainerPoint([city.lat, city.lon])
      const r = 9 // matches MARKER_RADIUS — all markers are uniform now
      // Try 4 directions; pick the first one that doesn't overlap another LABEL.
      // We allow the label to touch other markers (those are small enough that
      // it doesn't hurt readability, and we'd rather show the label than drop it).
      const dirs: Array<'top'|'bottom'|'left'|'right'> = ['bottom', 'top', 'right', 'left']
      let chosen: { dir: typeof dirs[number]; bbox: { x: number; y: number; w: number; h: number } } | null = null
      for (const dir of dirs) {
        const bbox = bboxFor(pt.x, pt.y, city.name, dir, r)
        if (placed.some(p => intersects(p.bbox, bbox))) continue
        chosen = { dir, bbox }
        break
      }
      if (chosen) {
        placed.push({ city, px: pt.x, py: pt.y, dir: chosen.dir, bbox: chosen.bbox })
      }
      // If all 4 directions overlap a label, this city is unlabeled.
    }
    return placed
  }, [cities, map, tick])

  return (
    <>
      {placement.map(p => (
        <CityLabelDOM
          key={p.city.id}
          city={p.city}
          containerX={p.px}
          containerY={p.py}
          direction={p.dir}
          radius={9}
        />
      ))}
    </>
  )
}

/** A label rendered as a Leaflet tooltip at a known city.
 *  Placement direction is decided by the collision pass; this just
 *  renders the tooltip at that position. */
function CityLabelDOM({
  city, direction, radius,
}: {
  city: CityNode
  containerX: number
  containerY: number
  direction: 'top' | 'bottom' | 'left' | 'right'
  radius: number
}) {
  const map = useMap()
  useEffect(() => {
    const off: Record<typeof direction, [number, number]> = {
      bottom: [0, radius + 4],
      top: [0, -(radius + 4)],
      right: [radius + 4, 0],
      left: [-(radius + 4), 0],
    }
    const tooltip = L.tooltip({
      permanent: true,
      direction,
      offset: off[direction],
      opacity: 1,
      className: 'city-marker-label',
    })
      .setLatLng([city.lat, city.lon])
      .setContent(
        `<div style="
          font-size: 11px;
          font-weight: 600;
          color: #e5e5e5;
          text-shadow: 0 0 3px #0f0f0f, 0 0 5px #0f0f0f;
          font-family: 'Inter', sans-serif;
          text-align: center;
        ">${city.name}</div>`
      )
    tooltip.addTo(map)
    return () => { try { tooltip.remove() } catch { /* ignore */ } }
  }, [map, city.lat, city.lon, city.name, radius, direction])
  return null
}

/** Auto-fit map view to GeoJSON bounds when boundary loads. */
/** Tile layer whose URL swaps in/out of dark mode imperatively.
 *  react-leaflet v4's <TileLayer> doesn't re-render on `url` prop change;
 *  we add the layer once and call setUrl() on subsequent theme changes. */
function ThemedTileLayer({ theme }: { theme: 'light' | 'dark' }) {
  const map = useMap()
  useEffect(() => {
    const url = theme === 'light'
      ? 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
      : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
    const layer = L.tileLayer(url, {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      maxZoom: 19,
    }).addTo(map)
    return () => { map.removeLayer(layer) }
  }, [map, theme])
  return null
}

function FitBoundsToGeoJSON({ data }: { data: GeoJSON.FeatureCollection }) {
  const map = useMap()
  useEffect(() => {
    try {
      const layer = L.geoJSON(data)
      const bounds = layer.getBounds()
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [40, 40] })
      }
    } catch (exc) {
      console.warn('fitBounds failed:', exc)
    }
  }, [map, data])
  return null
}
