/**
 * Geographic Overlay — real map view.
 *
 * Leaflet + OpenStreetMap tiles. Pulls the district boundary GeoJSON from
 * /api/race/district-geojson (backend caches per-district, fetched on demand
 * from US Census TIGERweb). Works automatically for any US House race —
 * just set the campaign config's `district` to standard `STATE-NN` format.
 *
 * Overlays:
 *   - District polygon (yellow outline, slight fill)
 *   - City circle markers (sized by article volume, colored by majority
 *     article stance)
 *   - Filter chips toggle visible activity layer
 *
 * When the campaign config changes district, the map re-fetches and auto-
 * fits the new boundary. City positions are currently hand-curated for
 * PA-08; future work: derive automatically from campaign.geography_keywords
 * + a geocoding lookup.
 */
import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { Calendar, Filter, MapPin, Sparkles, Users } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'
import { api } from '@/api/client'
import { useTheme } from '@/components/ThemeToggle'
import { entities as MOCK_ENTITIES, relations as MOCK_RELATIONS, type Entity } from '@/data/entityNetworkMock'

// ── City data model ──────────────────────────────────────────────────────
// Cities are auto-fetched from /api/race/cities — derived from the US
// Census Gazetteer + district GeoJSON bounding box. Article counts and
// stance breakdown are PLACEHOLDERS for now (TODO: real article-by-location
// aggregation backend). Once Feature A's entity-extraction lands, these
// become real from extracted location entities per article.

interface CityNode {
  id: string
  name: string
  lat: number
  lon: number
  lsad: string
  // Placeholders below — filled in deterministically from name hash so the
  // mockup feels consistent. Real values plug in via a backend aggregation.
  articleCount: number
  stance: { d: number; r: number; neutral: number }
  description: string
}

/** Deterministic placeholder generator — same city always gets same numbers
 *  so the UX feels stable. Replace with real /api/articles/by-location query. */
function placeholderStats(name: string): { articleCount: number; stance: { d: number; r: number; neutral: number } } {
  // Hash the name to a stable 32-bit int
  let h = 0
  for (let i = 0; i < name.length; i++) h = ((h << 5) - h + name.charCodeAt(i)) | 0
  const seed = Math.abs(h)
  const articleCount = 8 + (seed % 200)
  // Lean per-city based on seed parity
  const lean = (seed % 100) / 100  // 0..1
  const total = articleCount
  // Bias toward neutral with a moderate lean either way
  const dShare = 0.25 + (lean > 0.5 ? lean * 0.4 : (1 - lean) * 0.2)
  const rShare = 0.25 + (lean > 0.5 ? (1 - lean) * 0.2 : lean * 0.4)
  const d = Math.round(total * dShare)
  const r = Math.round(total * rShare)
  return { articleCount: total, stance: { d, r, neutral: Math.max(0, total - d - r) } }
}

// ── Component ────────────────────────────────────────────────────────────

type FilterMode = 'all' | 'events' | 'endorsements' | 'attacks'

export function GeographicOverlay() {
  const [selectedCity, setSelectedCity] = useState<string | null>(null)
  const [filterMode, setFilterMode] = useState<FilterMode>('all')
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
            description: `${c.name} (${lsadName[c.lsad] ?? 'place'} in ${c.state}).`,
            ...placeholderStats(c.name),
          }))
          setCities(augmented)
        })
        .catch(() => setCities([])),
    ]).finally(() => setLoadingMap(false))
  }, [])

  // For the selected city, aggregate entities + relations geographically.
  const cityIndex = useMemo(() => {
    const idx: Record<string, { entities: Entity[]; eventCount: number; endorsementCount: number; attackCount: number }> = {}
    for (const c of cities) {
      idx[c.id] = { entities: [], eventCount: 0, endorsementCount: 0, attackCount: 0 }
    }
    MOCK_ENTITIES.forEach(e => {
      const lower = e.name.toLowerCase()
      for (const c of cities) {
        if (lower === c.name.toLowerCase() && idx[c.id]) {
          idx[c.id].entities.push(e)
        }
      }
      if (e.type === 'event' || e.type === 'organization') {
        for (const c of cities) {
          if (e.description.toLowerCase().includes(c.name.toLowerCase()) && idx[c.id]) {
            if (!idx[c.id].entities.find(x => x.id === e.id)) {
              idx[c.id].entities.push(e)
            }
            if (e.type === 'event') idx[c.id].eventCount++
          }
        }
      }
    })
    MOCK_RELATIONS.forEach(r => {
      for (const c of cities) {
        const cityEnts = new Set(idx[c.id].entities.map(e => e.id))
        if (cityEnts.has(r.source as string) || cityEnts.has(r.target as string)) {
          if (r.type === 'endorses' || r.type === 'allies_with') idx[c.id].endorsementCount++
          if (r.type === 'attacks' || r.type === 'criticizes') idx[c.id].attackCount++
        }
      }
    })
    return idx
  }, [cities])

  const selected = selectedCity ? cities.find(c => c.id === selectedCity) : null
  const selectedData = selected ? cityIndex[selected.id] : null

  function stanceColor(s: CityNode['stance']) {
    const total = s.d + s.r + s.neutral
    if (total === 0) return 'var(--text-3)'
    const dShare = s.d / total
    const rShare = s.r / total
    if (Math.abs(dShare - rShare) < 0.1) return 'var(--text-3)'
    return dShare > rShare ? 'var(--candidate)' : 'var(--opponent)'
  }

  function cityRadius(count: number) {
    return Math.max(6, Math.min(30, Math.sqrt(count) * 1.2))
  }

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
              <span style={{ fontSize: 11, color: 'var(--green)', marginLeft: 6, padding: '2px 7px', background: 'rgba(34, 197, 94, 0.1)', borderRadius: 4, fontWeight: 600 }}>LIVE MAP</span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 1 }}>
              {district || 'district'} · {cities.length} cities · auto-derived from US Census Gazetteer
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginLeft: 'auto' }}>
          <Filter size={12} color="var(--text-3)" />
          {([
            { v: 'all', label: 'All overlays' },
            { v: 'events', label: 'Events' },
            { v: 'endorsements', label: 'Endorsements' },
            { v: 'attacks', label: 'Attacks' },
          ] as const).map(opt => {
            const active = filterMode === opt.v
            return (
              <button
                key={opt.v}
                onClick={() => setFilterMode(opt.v)}
                style={{
                  padding: '5px 10px', borderRadius: 6,
                  border: '1px solid ' + (active ? 'var(--accent)' : 'var(--bg-4)'),
                  background: active ? 'rgba(255, 191, 0, 0.12)' : 'var(--bg-2)',
                  color: active ? 'var(--accent)' : 'var(--text-2)',
                  cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
                }}
              >
                {opt.label}
              </button>
            )
          })}
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

            {/* City markers — circles only, labels handled by CollisionAwareLabels above */}
            {cities.map(c => {
              const data = cityIndex[c.id]
              const r = cityRadius(c.articleCount)
              const fill = stanceColor(c.stance)
              const isSelected = selectedCity === c.id

              let dim = false
              if (filterMode === 'events' && data.eventCount === 0) dim = true
              if (filterMode === 'endorsements' && data.endorsementCount === 0) dim = true
              if (filterMode === 'attacks' && data.attackCount === 0) dim = true

              return (
                <CircleMarker
                  key={c.id}
                  center={[c.lat, c.lon]}
                  radius={r}
                  pathOptions={{
                    color: isSelected ? 'var(--accent)' : 'var(--text-1)',
                    weight: isSelected ? 3 : 1.2,
                    opacity: dim ? 0.3 : 0.85,
                    fillColor: fill,
                    fillOpacity: dim ? 0.15 : 0.75,
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

          {/* Legend */}
          <div style={{
            position: 'absolute', top: 14, right: 14,
            background: 'var(--bg-2)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '10px 14px', fontSize: 11,
            zIndex: 1000, boxShadow: 'var(--shadow-elev)',
          }}>
            <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
              Majority stance
            </div>
            {[
              { label: 'Lean Dem', c: 'var(--candidate)' },
              { label: 'Lean GOP', c: 'var(--opponent)' },
              { label: 'Mixed / neutral', c: 'var(--text-3)' },
            ].map(x => (
              <div key={x.label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <span style={{ width: 10, height: 10, background: x.c, borderRadius: '50%' }} />
                <span style={{ color: 'var(--text-1)' }}>{x.label}</span>
              </div>
            ))}
            <div style={{ fontSize: 10, color: 'var(--text-3)', marginTop: 10 }}>
              Circle size = article volume
            </div>
          </div>
        </div>

        {/* Side panel: selected city details */}
        {selected && selectedData && (
          <div style={{ width: 340, flexShrink: 0, borderLeft: '1px solid var(--border)', background: 'var(--bg-2)', overflowY: 'auto' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <MapPin size={12} color="var(--text-2)" />
                <span style={{ fontSize: 10, color: 'var(--text-2)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em' }}>
                  {selected.lsad === '25' ? 'City'
                    : selected.lsad === '21' ? 'Borough'
                    : selected.lsad === '43' ? 'Town'
                    : selected.lsad === '47' ? 'Township'
                    : 'Community'} in {district}
                </span>
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.2, marginBottom: 6 }}>
                {selected.name}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 12 }}>
                {selected.description}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                <strong style={{ color: 'var(--text-1)' }}>{selected.articleCount}</strong> articles mention this city
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', background: 'var(--bg-sidebar)' }}>
                  <div style={{ flex: selected.stance.d, background: 'var(--candidate)' }} />
                  <div style={{ flex: selected.stance.neutral, background: 'var(--text-3)' }} />
                  <div style={{ flex: selected.stance.r, background: 'var(--opponent)' }} />
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontSize: 10, color: 'var(--text-2)' }}>
                  <span style={{ color: '#60a5fa' }}>D {selected.stance.d}</span>
                  <span>Neutral {selected.stance.neutral}</span>
                  <span style={{ color: '#f87171' }}>R {selected.stance.r}</span>
                </div>
              </div>
            </div>

            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              <StatCard icon={<Calendar size={14} />} label="Events" value={selectedData.eventCount} />
              <StatCard icon={<Sparkles size={14} />} label="Endorsements" value={selectedData.endorsementCount} />
              <StatCard icon={<Users size={14} />} label="Attacks" value={selectedData.attackCount} />
            </div>

            <div style={{ padding: '14px 20px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                Entities here ({selectedData.entities.length})
              </div>
              {selectedData.entities.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
                  No entities tied to this location yet.
                </div>
              )}
              {selectedData.entities.map(e => {
                const Icon = e.type === 'event' ? Calendar : e.type === 'organization' ? Users : MapPin
                return (
                  <div key={e.id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 8,
                    padding: '8px 0', borderBottom: '1px solid rgba(67, 67, 67, 0.3)',
                  }}>
                    <Icon size={12} color="var(--text-2)" style={{ marginTop: 2, flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 500 }}>
                        {e.name}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>
                        {e.type} · {e.mention_count} mentions
                      </div>
                    </div>
                  </div>
                )
              })}
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
      return b.articleCount - a.articleCount
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
      const r = Math.max(6, Math.min(30, Math.sqrt(city.articleCount) * 1.2))
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
          radius={Math.max(6, Math.min(30, Math.sqrt(p.city.articleCount) * 1.2))}
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
        ">
          ${city.name}<br>
          <span style="font-size: 10px; color: #a1a1a1; font-weight: 500;">${city.articleCount} articles</span>
        </div>`
      )
    tooltip.addTo(map)
    return () => { try { tooltip.remove() } catch { /* ignore */ } }
  }, [map, city.lat, city.lon, city.name, city.articleCount, radius, direction])
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

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div style={{
      background: 'var(--bg-sidebar)', border: '1px solid var(--border)', borderRadius: 6,
      padding: '10px 12px', textAlign: 'center',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, color: 'var(--text-2)', marginBottom: 4 }}>
        {icon}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-1)', lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--text-3)', marginTop: 4, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600 }}>
        {label}
      </div>
    </div>
  )
}
