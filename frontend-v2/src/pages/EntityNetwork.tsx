/**
 * Entity Network — force-directed visualization of entities and relationships
 * extracted from the article corpus.
 *
 * Data comes from /api/entity-network, populated by the LLM extraction
 * pipeline (backend/scripts/entity_extraction_backfill.py). Saved queries
 * below are hand-curated UX shortcuts that highlight common race patterns
 * (endorsements, attacks, voting records).
 */
import * as d3 from 'd3-force'
import { zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3-zoom'
import { select } from 'd3-selection'
import { Building2, Calendar, FileText, MapPin, Maximize2, Search, Sparkles, User } from 'lucide-react'
import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/api/client'
import {
  type Entity,
  type EntityType,
  type Relation,
  type RelationType,
  type SavedQuery,
} from '@/data/entityNetworkMock'
import { formatArticleDate } from '@/lib/formatDate'

// Hand-curated saved-query shortcuts. Entity IDs match canonical_ids
// from backend/data/canonical_entities.PA-08.json.
const SAVED_QUERIES: SavedQuery[] = [
  {
    id: 'q-endorsers-cognetti',
    label: 'Who endorsed Cognetti?',
    description: 'Orgs and individuals with an endorsing relationship.',
    highlight_entities: ['person:cognetti', 'org:emilys_list', 'person:shapiro', 'org:dccc', 'org:aflcio', 'person:cartwright'],
    filter_relation_types: ['endorses'],
  },
  {
    id: 'q-attackers-bresnahan',
    label: 'Who attacks Bresnahan?',
    description: 'Entities critical of Bresnahan in coverage.',
    highlight_entities: ['person:bresnahan', 'person:cognetti', 'org:dccc', 'org:aflcio', 'person:jeffries'],
    filter_relation_types: ['attacks', 'criticizes'],
  },
  {
    id: 'q-bresnahan-votes',
    label: "Bresnahan's voting record",
    description: 'Bills Bresnahan has voted on.',
    highlight_entities: ['person:bresnahan', 'bill:aca-subsidies', 'bill:medicaid-cuts', 'bill:tax-cuts'],
    filter_relation_types: ['voted_for', 'voted_against'],
  },
  {
    id: 'q-trump-network',
    label: "Trump's PA-08 footprint",
    description: 'Trump and everything he touches in this race.',
    highlight_entities: ['person:trump', 'person:bresnahan', 'person:vance', 'person:johnson_mike'],
  },
  {
    id: 'q-nrcc-attacks',
    label: 'NRCC attack vectors',
    description: 'Lines of attack the NRCC is using against Cognetti.',
    highlight_entities: ['org:nrcc', 'person:cognetti', 'loc:scranton'],
    filter_relation_types: ['attacks'],
  },
]

// ── Visual config ────────────────────────────────────────────────────────

const TYPE_LABELS: Record<EntityType, string> = {
  person: 'People',
  organization: 'Organizations',
  bill: 'Bills',
  event: 'Events',
  location: 'Locations',
}

const TYPE_ICONS: Record<EntityType, typeof User> = {
  person: User,
  organization: Building2,
  bill: FileText,
  event: Calendar,
  location: MapPin,
}

function colorForEntity(e: Entity): string {
  if (e.type === 'person' || e.type === 'organization' || e.type === 'event') {
    if (e.affiliation === 'D') return 'var(--candidate)'
    if (e.affiliation === 'R') return 'var(--opponent)'
  }
  if (e.type === 'bill') return '#ea580c'
  if (e.type === 'location') return 'var(--text-3)'
  return 'var(--text-2)'
}

function colorForRelation(r: RelationType): string {
  if (r === 'attacks' || r === 'criticizes') return 'var(--red)'
  if (r === 'endorses' || r === 'allies_with') return 'var(--green)'
  if (r === 'voted_for' || r === 'co_sponsored') return '#3b82f6'
  if (r === 'voted_against' || r === 'opposes_policy_of') return '#f59e0b'
  if (r === 'attended') return '#a855f7'
  return 'var(--text-3)'
}

function relationLabel(r: RelationType): string {
  return r.replace(/_/g, ' ')
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string
  entity: Entity
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  source: string | SimNode
  target: string | SimNode
  rel: Relation
}

// ── Component ────────────────────────────────────────────────────────────

export function EntityNetwork() {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  // Data fetched from /api/entity-network
  const [entities, setEntities] = useState<Entity[]>([])
  const [relations, setRelations] = useState<Relation[]>([])
  const [stats, setStats] = useState({ entity_count: 0, relation_count: 0, seeded_count: 0 })
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.entityNetwork(3).then(data => {
      if (cancelled) return
      setEntities(data.entities as Entity[])
      setRelations(data.relations as Relation[])
      setStats(data.stats)
      setLoading(false)
    }).catch(err => {
      if (cancelled) return
      console.error('Failed to load entity network', err)
      setLoadError(err instanceof Error ? err.message : String(err))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  // Filter / query state
  const [typeFilter, setTypeFilter] = useState<Record<EntityType, boolean>>({
    person: true, organization: true, bill: true, event: true, location: true,
  })
  const [activeQuery, setActiveQuery] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoverEdge, setHoverEdge] = useState<Relation | null>(null)
  const [dims, setDims] = useState({ width: 1000, height: 700 })

  // Pan/zoom state
  const [zoomT, setZoomT] = useState<{ x: number; y: number; k: number }>({ x: 0, y: 0, k: 1 })
  const zoomBehaviorRef = useRef<ZoomBehavior<SVGSVGElement, unknown> | null>(null)
  // Track whether the user has interacted manually — once they have, don't
  // auto-refit on every data change (e.g. filter chip toggle).
  const userHasZoomedRef = useRef(false)

  // Multi-hop neighborhood mode — when set, filter the graph to just the
  // N-hop ego network around the seed entity.
  const [neighborhoodSeed, setNeighborhoodSeed] = useState<string | null>(null)
  const [neighborhoodDepth, setNeighborhoodDepth] = useState<number>(2)
  const [neighborhoodData, setNeighborhoodData] = useState<Awaited<ReturnType<typeof api.entityNetworkNeighbors>> | null>(null)
  const [neighborhoodLoading, setNeighborhoodLoading] = useState(false)

  // Path-finder mode — popover with two entity-pickers + results.
  const [pathPanelOpen, setPathPanelOpen] = useState(false)
  const [pathFrom, setPathFrom] = useState<string>('')
  const [pathTo, setPathTo] = useState<string>('')
  const [pathFromQuery, setPathFromQuery] = useState<string>('')
  const [pathToQuery, setPathToQuery] = useState<string>('')
  const [pathMaxHops, setPathMaxHops] = useState<number>(3)
  const [pathResults, setPathResults] = useState<Awaited<ReturnType<typeof api.entityNetworkPath>> | null>(null)
  const [pathLoading, setPathLoading] = useState(false)
  const [pathError, setPathError] = useState<string | null>(null)
  // When user clicks a path row in the popover, that path gets highlighted
  // on the canvas — entities + edges at normal opacity, everything else
  // dimmed. Stored as {entityIds, edgeKeys} where edgeKey is "src|pred|tgt".
  const [highlightedPath, setHighlightedPath] = useState<{ entityIds: Set<string>; edgeKeys: Set<string> } | null>(null)

  // Claim inspector — opened when the user clicks an edge.
  const [inspectorClaimId, setInspectorClaimId] = useState<number | null>(null)
  const [inspectorData, setInspectorData] = useState<Awaited<ReturnType<typeof api.getClaim>> | null>(null)
  const [inspectorLoading, setInspectorLoading] = useState(false)
  const [inspectorActionPending, setInspectorActionPending] = useState(false)

  useEffect(() => {
    if (!inspectorClaimId) { setInspectorData(null); return }
    let cancelled = false
    setInspectorLoading(true)
    api.getClaim(inspectorClaimId)
      .then(d => { if (!cancelled) setInspectorData(d) })
      .catch(() => { if (!cancelled) setInspectorData(null) })
      .finally(() => { if (!cancelled) setInspectorLoading(false) })
    return () => { cancelled = true }
  }, [inspectorClaimId])

  async function refreshEntityNetwork() {
    // After retract / reactivate, the relation set changes — re-fetch.
    try {
      const data = await api.entityNetwork(3)
      setEntities(data.entities as Entity[])
      setRelations(data.relations as Relation[])
      setStats(data.stats)
    } catch (e) {
      console.error('refresh failed', e)
    }
  }

  useEffect(() => {
    if (!neighborhoodSeed) {
      setNeighborhoodData(null)
      return
    }
    let cancelled = false
    setNeighborhoodLoading(true)
    api.entityNetworkNeighbors(neighborhoodSeed, neighborhoodDepth, 2)
      .then(d => { if (!cancelled) setNeighborhoodData(d) })
      .catch(() => { if (!cancelled) setNeighborhoodData(null) })
      .finally(() => { if (!cancelled) setNeighborhoodLoading(false) })
    return () => { cancelled = true }
  }, [neighborhoodSeed, neighborhoodDepth])

  // Resize observer to keep SVG sized to its container
  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(es => {
      const r = es[0].contentRect
      setDims({ width: r.width, height: r.height })
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  // Apply query / type / search / neighborhood filters
  const { visibleEntities, visibleRelations, highlightSet } = useMemo(() => {
    const q = SAVED_QUERIES.find(x => x.id === activeQuery)
    const highlight = new Set(q?.highlight_entities ?? [])
    const term = search.trim().toLowerCase()

    // Neighborhood mode restricts the entity set to the N-hop ego network
    // returned by /api/entity-network/neighbors.
    const neighborhoodIds = neighborhoodData
      ? new Set<string>(neighborhoodData.entities.map(e => e.id))
      : null

    const filteredEntities = entities.filter(e => {
      if (neighborhoodIds && !neighborhoodIds.has(e.id)) return false
      if (!typeFilter[e.type]) return false
      if (term && !e.name.toLowerCase().includes(term)) return false
      return true
    })
    const visibleIds = new Set(filteredEntities.map(e => e.id))

    const filteredRelations = relations.filter(r => {
      if (!visibleIds.has(r.source as string) || !visibleIds.has(r.target as string)) return false
      if (q?.filter_relation_types && !q.filter_relation_types.includes(r.type)) return false
      return true
    })

    return {
      visibleEntities: filteredEntities,
      visibleRelations: filteredRelations,
      highlightSet: highlight,
    }
  }, [entities, relations, typeFilter, activeQuery, search, neighborhoodData])

  // Force-simulation positions
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({})

  useEffect(() => {
    const nodes: SimNode[] = visibleEntities.map(e => ({ id: e.id, entity: e }))
    const links: SimLink[] = visibleRelations.map(r => ({ source: r.source as string, target: r.target as string, rel: r }))

    // Scale force parameters with node count so large graphs don't blow out
    // to a huge area that's unreadable when zoomed to fit. Small graphs use
    // the original spacious layout; big ones get progressively tighter.
    const n = nodes.length
    const tight = n > 100 ? Math.sqrt(80 / n) : 1   // 1.0 at n=80, 0.44 at n=417
    const charge = -340 * tight
    const linkDistance = 140 * tight

    const sim = d3.forceSimulation<SimNode>(nodes)
      .force('charge', d3.forceManyBody().strength(charge))
      .force('center', d3.forceCenter(dims.width / 2, dims.height / 2))
      .force('collide', d3.forceCollide<SimNode>().radius(d => 14 + Math.log10(Math.max(d.entity.mention_count, 1)) * 6))
      .force('link', d3.forceLink<SimNode, SimLink>(links).id(d => d.id).distance(linkDistance).strength(0.6))
      .alphaDecay(0.04)
      .stop()

    // Run synchronously to settle
    for (let i = 0; i < 220; i++) sim.tick()
    const next: Record<string, { x: number; y: number }> = {}
    nodes.forEach(n => { next[n.id] = { x: n.x ?? dims.width / 2, y: n.y ?? dims.height / 2 } })
    setPositions(next)
  }, [visibleEntities, visibleRelations, dims])

  // Attach d3-zoom to the SVG once it's mounted.
  useEffect(() => {
    if (!svgRef.current) return
    const z = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.05, 8])
      .filter((event) => {
        // Skip zoom-pan if click originated on a node (so node click → select still works).
        const target = event.target as Element | null
        if (target && target.closest('[data-node="true"]')) return false
        // Default filter: left-mouse-down or wheel, not while modifier keys are held the wrong way.
        return (!event.ctrlKey || event.type === 'wheel') && !event.button
      })
      .on('zoom', (event) => {
        const t = event.transform as ZoomTransform
        setZoomT({ x: t.x, y: t.y, k: t.k })
      })
      .on('start', () => { userHasZoomedRef.current = true })
    zoomBehaviorRef.current = z
    const sel = select(svgRef.current)
    sel.call(z)
    // Disable d3-zoom's built-in double-click zoom — we use dblclick for "fit to view" instead.
    sel.on('dblclick.zoom', null)
    return () => { sel.on('.zoom', null) }
  }, [])

  // Compute a "fit to view" transform from the current positions, return it.
  function computeFitTransform(): ZoomTransform | null {
    const ids = Object.keys(positions)
    if (ids.length === 0) return null
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const id of ids) {
      const p = positions[id]
      if (p.x < minX) minX = p.x
      if (p.y < minY) minY = p.y
      if (p.x > maxX) maxX = p.x
      if (p.y > maxY) maxY = p.y
    }
    // Pad for node radius + labels
    const pad = 80
    const bw = (maxX - minX) + pad * 2
    const bh = (maxY - minY) + pad * 2
    const cx = (minX + maxX) / 2
    const cy = (minY + maxY) / 2
    // Floor the scale so labels stay readable. If the natural fit would
    // shrink everything below this, accept that some periphery is off-screen
    // by default — the user can zoom out manually or hit Fit to view to see all.
    const MIN_FIT_SCALE = 0.35
    const naturalK = Math.min(dims.width / bw, dims.height / bh, 1)
    const k = Math.max(naturalK, MIN_FIT_SCALE)
    const x = dims.width / 2 - cx * k
    const y = dims.height / 2 - cy * k
    return zoomIdentity.translate(x, y).scale(k)
  }

  // Initial-fit when the first layout is ready, OR when the user explicitly clicks "Fit to view".
  // We only auto-fit if the user hasn't manually zoomed yet — otherwise filter changes would
  // disorient them by re-centering on every chip toggle.
  useEffect(() => {
    if (!svgRef.current || !zoomBehaviorRef.current) return
    if (userHasZoomedRef.current) return
    const t = computeFitTransform()
    if (!t) return
    select(svgRef.current).call(zoomBehaviorRef.current.transform, t)
    // Reset the "user zoomed" flag right after we trigger the fit ourselves — d3-zoom
    // fires "start" because we called .transform(), but that was OUR action, not the user's.
    userHasZoomedRef.current = false
  }, [positions, dims.width, dims.height])

  function fitToView() {
    if (!svgRef.current || !zoomBehaviorRef.current) return
    const t = computeFitTransform()
    if (!t) return
    select(svgRef.current).call(zoomBehaviorRef.current.transform, t)
    userHasZoomedRef.current = false
  }

  const selected = selectedId ? entities.find(e => e.id === selectedId) : null
  const selectedRelations = selected
    ? relations.filter(r => r.source === selected.id || r.target === selected.id)
    : []

  // Fetch which narrative frames feature the selected entity
  const [framesForSelected, setFramesForSelected] = useState<Awaited<ReturnType<typeof api.framesForEntity>> | null>(null)
  useEffect(() => {
    if (!selected) { setFramesForSelected(null); return }
    let cancelled = false
    api.framesForEntity(selected.id).then(res => {
      if (!cancelled) setFramesForSelected(res)
    }).catch(() => { if (!cancelled) setFramesForSelected(null) })
    return () => { cancelled = true }
  }, [selected?.id])

  // v15.0 quote-anchored claim records for the selected entity.
  const [claimRecordsForSelected, setClaimRecordsForSelected] = useState<Awaited<ReturnType<typeof api.claimRecordsForEntity>> | null>(null)
  useEffect(() => {
    if (!selected) { setClaimRecordsForSelected(null); return }
    let cancelled = false
    api.claimRecordsForEntity(selected.id, 50).then(res => {
      if (!cancelled) setClaimRecordsForSelected(res)
    }).catch(() => { if (!cancelled) setClaimRecordsForSelected(null) })
    return () => { cancelled = true }
  }, [selected?.id])

  function nodeRadius(e: Entity) {
    return 11 + Math.log10(Math.max(e.mention_count, 1)) * 6
  }

  function isDimmed(id: string) {
    // Path highlight takes precedence over everything else — when active,
    // dim every entity that isn't on the highlighted path.
    if (highlightedPath) return !highlightedPath.entityIds.has(id)
    if (highlightSet.size === 0 && !selectedId) return false
    if (selectedId) {
      // Show selected + immediate neighbors
      if (id === selectedId) return false
      const connected = relations.some(r =>
        (r.source === selectedId && r.target === id) ||
        (r.target === selectedId && r.source === id)
      )
      return !connected
    }
    return !highlightSet.has(id)
  }

  function isEdgeOnHighlightedPath(r: Relation): boolean {
    if (!highlightedPath) return false
    // Edge key is direction-agnostic — path traversal may go either way.
    return (
      highlightedPath.edgeKeys.has(`${r.source}|${r.type}|${r.target}`) ||
      highlightedPath.edgeKeys.has(`${r.target}|${r.type}|${r.source}`)
    )
  }

  return (
    <div style={{ height: 'calc(100vh - 48px)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)', color: 'var(--text-1)' }}>
      {/* Header bar */}
      <div style={{ flexShrink: 0, padding: '14px 20px', borderBottom: '1px solid #2f2f2f', display: 'flex', alignItems: 'center', gap: 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Sparkles size={18} color="#a78bfa" />
          <div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>Entity Network</div>
            <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 1 }}>
              {loading
                ? 'Loading entities…'
                : loadError
                ? `Failed to load: ${loadError}`
                : (visibleEntities.length !== stats.entity_count || visibleRelations.length !== stats.relation_count
                    ? `${visibleEntities.length} of ${stats.entity_count} entities · ${visibleRelations.length} of ${stats.relation_count} relationships (filtered)`
                    : `${stats.entity_count} entities · ${stats.relation_count} relationships · ${stats.seeded_count} seeded`)}
            </div>
          </div>
        </div>

        {/* Search */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'var(--bg-2)', border: '1px solid #2f2f2f', borderRadius: 8, padding: '6px 10px', minWidth: 220 }}>
          <Search size={14} color="var(--text-3)" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter entities…"
            style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-1)', fontSize: 13, fontFamily: 'inherit', width: '100%' }}
          />
        </div>

        {/* Type chips */}
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {(Object.keys(TYPE_LABELS) as EntityType[]).map(t => {
            const active = typeFilter[t]
            const Icon = TYPE_ICONS[t]
            return (
              <button
                key={t}
                onClick={() => setTypeFilter(f => ({ ...f, [t]: !f[t] }))}
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '5px 10px',
                  borderRadius: 6,
                  border: '1px solid ' + (active ? 'var(--border-bright)' : 'var(--bg-4)'),
                  background: active ? 'var(--bg-3)' : 'var(--bg-2)',
                  color: active ? 'var(--text-1)' : 'var(--text-3)',
                  cursor: 'pointer', fontSize: 12, fontFamily: 'inherit', fontWeight: 500,
                }}
              >
                <Icon size={12} />
                {TYPE_LABELS[t]}
              </button>
            )
          })}
        </div>

        {/* Active-filter chips + clear */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button
            onClick={() => {
              setPathPanelOpen(p => !p)
              // Pre-fill From with the currently selected entity if there is one.
              if (!pathPanelOpen && selected && !pathFrom) {
                setPathFrom(selected.id)
                setPathFromQuery(selected.name)
              }
            }}
            title="Find paths between two entities"
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '5px 10px', borderRadius: 6,
              border: '1px solid ' + (pathPanelOpen ? 'var(--accent)' : 'var(--border)'),
              background: pathPanelOpen ? 'rgba(255,191,0,0.12)' : 'var(--bg-2)',
              color: pathPanelOpen ? 'var(--accent)' : 'var(--text-2)',
              cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
            }}
          >
            Find path
          </button>
          {neighborhoodSeed && (
            <button
              onClick={() => setNeighborhoodSeed(null)}
              title="Clear neighborhood filter and show the full graph"
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '5px 10px', borderRadius: 6,
                border: '1px solid #ffbf00',
                background: 'rgba(255, 191, 0, 0.12)', color: 'var(--accent)',
                cursor: 'pointer', fontSize: 12, fontWeight: 600, fontFamily: 'inherit',
              }}
            >
              {neighborhoodLoading ? '…' : ''}{neighborhoodDepth}-hop of{' '}
              <strong>{entities.find(e => e.id === neighborhoodSeed)?.name ?? neighborhoodSeed}</strong>
              {neighborhoodData && (
                <span style={{ fontSize: 10, opacity: 0.7, marginLeft: 4 }}>
                  ({neighborhoodData.stats.entity_count} entities)
                </span>
              )}
              <span style={{ marginLeft: 2 }}>✕</span>
            </button>
          )}
          {activeQuery && (
            <button
              onClick={() => setActiveQuery(null)}
              style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid #555', background: 'var(--bg-3)', color: 'var(--text-2)', cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit' }}
            >
              Clear query
            </button>
          )}
        </div>
      </div>

      {/* Saved-query row */}
      <div style={{ flexShrink: 0, padding: '10px 20px', borderBottom: '1px solid #2f2f2f', background: 'var(--bg-sidebar)', display: 'flex', gap: 8, alignItems: 'center', overflowX: 'auto' }}>
        <span style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', whiteSpace: 'nowrap' }}>
          Try asking
        </span>
        {SAVED_QUERIES.map(q => {
          const active = activeQuery === q.id
          return (
            <button
              key={q.id}
              onClick={() => setActiveQuery(active ? null : q.id)}
              title={q.description}
              style={{
                padding: '5px 11px',
                borderRadius: 14,
                border: '1px solid ' + (active ? 'var(--accent)' : 'var(--bg-4)'),
                background: active ? 'rgba(255, 191, 0, 0.12)' : 'var(--bg-2)',
                color: active ? 'var(--accent)' : 'var(--text-2)',
                cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
                whiteSpace: 'nowrap',
              }}
            >
              {q.label}
            </button>
          )
        })}
      </div>

      {/* Path-finder panel — overlay shown when "Find path" is active. */}
      {pathPanelOpen && (
        <div style={{
          position: 'absolute', top: 110, right: 20, zIndex: 50,
          width: 460, maxHeight: '70vh', overflowY: 'auto',
          background: 'var(--bg-2)', border: '1px solid #434343', borderRadius: 10,
          padding: '14px 16px', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            <Sparkles size={14} color="var(--accent)" />
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-1)' }}>Find path between entities</div>
            <button
              onClick={() => { setPathPanelOpen(false); setPathResults(null); setPathError(null) }}
              style={{ marginLeft: 'auto', background: 'transparent', border: 'none', color: 'var(--text-3)', cursor: 'pointer', fontSize: 14 }}
            >
              ✕
            </button>
          </div>

          {/* From/To pickers with autocomplete from loaded entities */}
          {(['from', 'to'] as const).map(role => {
            const query = role === 'from' ? pathFromQuery : pathToQuery
            const setQuery = role === 'from' ? setPathFromQuery : setPathToQuery
            const value = role === 'from' ? pathFrom : pathTo
            const setValue = role === 'from' ? setPathFrom : setPathTo
            const showDropdown = query.trim() && (!value || entities.find(e => e.id === value)?.name !== query)
            const matches = !showDropdown ? [] : entities
              .filter(e => e.name.toLowerCase().includes(query.trim().toLowerCase()))
              .slice(0, 6)
            return (
              <div key={role} style={{ position: 'relative', marginBottom: 8 }}>
                <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase',
                              fontWeight: 700, letterSpacing: '0.06em', marginBottom: 4 }}>
                  {role}
                </div>
                <input
                  value={query}
                  onChange={e => { setQuery(e.target.value); setValue('') }}
                  placeholder={role === 'from' ? 'Search source entity…' : 'Search target entity…'}
                  style={{
                    width: '100%', padding: '6px 10px', borderRadius: 6,
                    border: '1px solid ' + (value ? 'var(--green)' : 'var(--border)'),
                    background: 'var(--bg-sidebar)', color: 'var(--text-1)',
                    fontSize: 13, fontFamily: 'inherit', outline: 'none',
                    boxSizing: 'border-box',
                  }}
                />
                {showDropdown && matches.length > 0 && (
                  <div style={{
                    position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                    background: 'var(--bg-sidebar)', border: '1px solid #434343', borderRadius: 6,
                    marginTop: 2, maxHeight: 180, overflowY: 'auto',
                  }}>
                    {matches.map(m => (
                      <div
                        key={m.id}
                        onClick={() => { setValue(m.id); setQuery(m.name) }}
                        style={{
                          padding: '6px 10px', cursor: 'pointer', fontSize: 12,
                          color: 'var(--text-1)', borderBottom: '1px solid rgba(67,67,67,0.4)',
                        }}
                      >
                        <span style={{ color: 'var(--text-2)', fontSize: 10, textTransform: 'uppercase', marginRight: 6 }}>
                          {m.type}
                        </span>
                        {m.name}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {/* Max-hops picker */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, marginBottom: 12 }}>
            <span style={{ fontSize: 11, color: 'var(--text-2)' }}>Max hops:</span>
            {[1, 2, 3, 4].map(h => (
              <button
                key={h}
                onClick={() => setPathMaxHops(h)}
                style={{
                  padding: '3px 10px', borderRadius: 5,
                  border: '1px solid ' + (pathMaxHops === h ? 'var(--accent)' : 'var(--border)'),
                  background: pathMaxHops === h ? 'rgba(255,191,0,0.12)' : 'var(--bg-2)',
                  color: pathMaxHops === h ? 'var(--accent)' : 'var(--text-2)',
                  fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
                }}
              >
                {h}
              </button>
            ))}
            <button
              onClick={async () => {
                if (!pathFrom || !pathTo) { setPathError('Pick both entities'); return }
                setPathError(null); setPathLoading(true); setPathResults(null)
                try {
                  const data = await api.entityNetworkPath(pathFrom, pathTo, pathMaxHops, 2)
                  setPathResults(data)
                } catch (e) {
                  setPathError(e instanceof Error ? e.message : String(e))
                } finally {
                  setPathLoading(false)
                }
              }}
              disabled={pathLoading}
              style={{
                marginLeft: 'auto', padding: '5px 14px', borderRadius: 6,
                border: '1px solid #22c55e', background: 'rgba(34,197,94,0.12)',
                color: 'var(--green)', cursor: 'pointer', fontSize: 12, fontWeight: 600,
              }}
            >
              {pathLoading ? 'Searching…' : 'Find paths'}
            </button>
          </div>

          {pathError && (
            <div style={{ fontSize: 12, color: 'var(--red)', marginBottom: 8 }}>{pathError}</div>
          )}

          {pathResults && (
            <>
              <div style={{ fontSize: 11, color: 'var(--text-2)', marginBottom: 8 }}>
                Found <strong style={{ color: 'var(--text-1)' }}>{pathResults.path_count}</strong> path{pathResults.path_count === 1 ? '' : 's'}
                {pathResults.truncated && ' (truncated at 30 — try lowering max hops for shorter paths)'}
              </div>
              {pathResults.path_count === 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
                  No path found within {pathMaxHops} hops. Try increasing max hops, or check that both entities exist in the graph above the min-mention threshold.
                </div>
              )}
              {pathResults.paths.slice(0, 12).map((p, pi) => {
                // Compose a stable key for the path's set of edges. Each
                // edge key is "src|predicate|target" (canonical_ids).
                const pathEdgeKeys = new Set<string>()
                const pathEntityIds = new Set<string>(p.map(s => s.id))
                for (let i = 1; i < p.length; i++) {
                  const prev = p[i - 1]
                  const cur = p[i]
                  if (!cur.predicate) continue
                  if (cur.direction === 'backward') {
                    pathEdgeKeys.add(`${cur.id}|${cur.predicate}|${prev.id}`)
                  } else {
                    pathEdgeKeys.add(`${prev.id}|${cur.predicate}|${cur.id}`)
                  }
                }
                // Is this path the currently-highlighted one?
                const isHighlighted = highlightedPath && p.every(s => highlightedPath.entityIds.has(s.id))
                  && Array.from(pathEdgeKeys).every(k => highlightedPath.edgeKeys.has(k))
                return (
                  <div
                    key={pi}
                    onClick={() => {
                      if (isHighlighted) {
                        setHighlightedPath(null)
                      } else {
                        setHighlightedPath({ entityIds: pathEntityIds, edgeKeys: pathEdgeKeys })
                      }
                    }}
                    title={isHighlighted ? 'Click to clear highlight' : 'Click to highlight on canvas'}
                    style={{
                      padding: '8px 10px', borderBottom: '1px solid rgba(67,67,67,0.4)',
                      cursor: 'pointer', borderRadius: 4,
                      background: isHighlighted ? 'rgba(255,191,0,0.12)' : 'transparent',
                      border: isHighlighted ? '1px solid #ffbf00' : '1px solid transparent',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <div style={{ fontSize: 10, color: 'var(--text-3)' }}>
                        Path {pi + 1} · {p.length - 1} hop{p.length - 1 === 1 ? '' : 's'}
                      </div>
                      {isHighlighted && (
                        <div style={{ fontSize: 9, color: 'var(--accent)', fontWeight: 700 }}>● HIGHLIGHTED ON CANVAS</div>
                      )}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center', fontSize: 12 }}>
                      {p.map((step, si) => (
                        <React.Fragment key={si}>
                          {si > 0 && step.predicate && (
                            <span style={{
                              fontSize: 10, color: 'var(--text-3)',
                              padding: '1px 5px', borderRadius: 3,
                              background: 'rgba(115,115,115,0.15)',
                            }}>
                              {step.direction === 'backward' ? '←' : '→'} {step.predicate}
                            </span>
                          )}
                          <span style={{
                            color: step.affiliation === 'D' ? 'var(--candidate)' : step.affiliation === 'R' ? 'var(--opponent)' : 'var(--text-1)',
                            fontWeight: si === 0 || si === p.length - 1 ? 700 : 500,
                          }}>
                            {step.name}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                )
              })}
              {highlightedPath && (
                <button
                  onClick={() => setHighlightedPath(null)}
                  style={{
                    marginTop: 8, padding: '5px 12px', borderRadius: 5,
                    border: '1px solid #555', background: 'var(--bg-3)',
                    color: 'var(--text-2)', cursor: 'pointer', fontSize: 11,
                  }}
                >
                  Clear highlight
                </button>
              )}
            </>
          )}
        </div>
      )}

      {/* Main area: graph + side panel */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {/* Canvas */}
        <div ref={containerRef} style={{ flex: 1, position: 'relative', overflow: 'hidden', background: 'var(--bg-sidebar)' }}>
          <svg
            ref={svgRef}
            width={dims.width}
            height={dims.height}
            onClick={() => setSelectedId(null)}
            onDoubleClick={fitToView}
            style={{ display: 'block', cursor: 'grab' }}
          >
            <g transform={`translate(${zoomT.x},${zoomT.y}) scale(${zoomT.k})`}>
              {/* Edges */}
              <g>
                {visibleRelations.map(r => {
                  const src = positions[r.source as string]
                  const tgt = positions[r.target as string]
                  if (!src || !tgt) return null
                  const dimmed = isDimmed(r.source as string) || isDimmed(r.target as string)
                  const isHover = hoverEdge?.id === r.id
                  const expired = !!r.is_expired
                  const onPath = isEdgeOnHighlightedPath(r)
                  const strokeColor = onPath ? 'var(--accent)' : colorForRelation(r.type)
                  // Expired relations are noticeably dimmer when not hovered/dragged into focus.
                  const baseOpacity = expired ? 0.18 : 0.45
                  const opacity = onPath ? 0.95 : (dimmed ? 0.06 : (isHover ? 0.95 : baseOpacity))
                  const baseW = Math.max(1.2, Math.min(5, Math.log2(r.weight + 1) * 0.8))
                  const w = onPath ? baseW * 2.2 : baseW
                  return (
                    <line
                      key={r.id}
                      x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
                      stroke={strokeColor}
                      strokeWidth={(isHover ? w + 1 : w) / Math.max(zoomT.k, 0.3)}
                      strokeDasharray={expired ? `${6 / Math.max(zoomT.k, 0.3)} ${4 / Math.max(zoomT.k, 0.3)}` : undefined}
                      opacity={opacity}
                      onMouseEnter={() => setHoverEdge(r)}
                      onMouseLeave={() => setHoverEdge(null)}
                      onClick={ev => {
                        ev.stopPropagation()
                        if (r.claim_id) setInspectorClaimId(r.claim_id)
                      }}
                      style={{ cursor: 'pointer', pointerEvents: 'stroke' }}
                    />
                  )
                })}
              </g>

              {/* Nodes */}
              <g>
                {visibleEntities.map(e => {
                  const p = positions[e.id]
                  if (!p) return null
                  const r = nodeRadius(e)
                  const dimmed = isDimmed(e.id)
                  const isSelected = selectedId === e.id
                  return (
                    <g
                      key={e.id}
                      data-node="true"
                      transform={`translate(${p.x},${p.y})`}
                      style={{ cursor: 'pointer', opacity: dimmed ? 0.18 : 1, transition: 'opacity 0.15s' }}
                      onClick={ev => { ev.stopPropagation(); setSelectedId(e.id) }}
                    >
                      <circle
                        r={r}
                        fill={colorForEntity(e)}
                        stroke={isSelected ? 'var(--accent)' : 'rgba(255,255,255,0.15)'}
                        strokeWidth={(isSelected ? 2.5 : 1) / Math.max(zoomT.k, 0.3)}
                      />
                      <text
                        y={r + 11}
                        textAnchor="middle"
                        fill={isSelected ? 'var(--accent)' : 'var(--text-1)'}
                        fontSize={11 / Math.max(zoomT.k, 0.3)}
                        fontWeight={isSelected ? 700 : 500}
                        style={{ pointerEvents: 'none', userSelect: 'none' }}
                      >
                        {e.name}
                      </text>
                    </g>
                  )
                })}
              </g>
            </g>
          </svg>

          {/* Fit-to-view button */}
          <button
            onClick={fitToView}
            title="Fit all entities to view (or double-click the canvas)"
            style={{
              position: 'absolute', bottom: 14, right: 14,
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 10px', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--bg-2)',
              color: 'var(--text-1)', cursor: 'pointer',
              fontSize: 12, fontFamily: 'inherit', fontWeight: 500,
              boxShadow: 'var(--shadow-elev)',
            }}
          >
            <Maximize2 size={12} />
            Fit to view
          </button>

          {/* Claim inspector modal — overlay when an edge has been clicked */}
          {inspectorClaimId && (
            <div
              onClick={() => { setInspectorClaimId(null); setInspectorData(null) }}
              style={{
                position: 'fixed', inset: 0, zIndex: 100,
                background: 'rgba(0, 0, 0, 0.65)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <div
                onClick={ev => ev.stopPropagation()}
                style={{
                  width: 720, maxHeight: '85vh', overflowY: 'auto',
                  background: 'var(--bg-2)', border: '1px solid #434343', borderRadius: 12,
                  padding: '20px 24px', boxShadow: '0 12px 48px rgba(0,0,0,0.7)',
                  color: 'var(--text-1)',
                }}
              >
                {inspectorLoading && <div style={{ color: 'var(--text-2)' }}>Loading claim…</div>}
                {!inspectorLoading && !inspectorData && (
                  <div style={{ color: 'var(--red)' }}>Failed to load claim {inspectorClaimId}.</div>
                )}
                {!inspectorLoading && inspectorData && (() => {
                  const c = inspectorData.claim
                  const statusColor = c.status === 'retracted' ? 'var(--red)'
                    : c.status === 'contested' ? 'var(--accent)' : 'var(--green)'
                  return (
                    <>
                      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: 14 }}>
                        <div style={{ flex: 1 }}>
                          <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase',
                                        letterSpacing: '0.08em', fontWeight: 700, marginBottom: 6 }}>
                            Claim #{c.id}
                            <span style={{
                              marginLeft: 8, padding: '1px 7px', borderRadius: 4,
                              background: `${statusColor}22`, color: statusColor, fontSize: 10,
                            }}>
                              {c.status}
                            </span>
                          </div>
                          <div style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.3 }}>
                            <span style={{ color: c.subject.affiliation === 'D' ? 'var(--candidate)' : c.subject.affiliation === 'R' ? 'var(--opponent)' : 'var(--text-1)' }}>
                              {c.subject.name}
                            </span>{' '}
                            <span style={{
                              fontSize: 12, color: 'var(--text-2)',
                              padding: '2px 8px', borderRadius: 4,
                              background: 'rgba(115,115,115,0.18)',
                            }}>
                              {c.predicate}
                            </span>{' '}
                            <span style={{ color: c.object.affiliation === 'D' ? 'var(--candidate)' : c.object.affiliation === 'R' ? 'var(--opponent)' : 'var(--text-1)' }}>
                              {c.object.name}
                            </span>
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 6 }}>
                            {c.supporting_count} supporting · {c.contesting_count} contesting · stance: proc={c.stance.procedural} / rhet={c.stance.rhetorical} / ideo={c.stance.ideological}
                          </div>
                          {c.first_seen && (
                            <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                              First seen {c.first_seen.slice(0, 10)} · last seen {c.last_seen?.slice(0, 10) ?? '—'}
                            </div>
                          )}
                          {c.status === 'retracted' && (
                            <div style={{
                              marginTop: 8, padding: '6px 10px', borderRadius: 5,
                              background: 'rgba(239,68,68,0.1)', fontSize: 11, color: 'var(--red)',
                            }}>
                              Retracted by {c.retracted_by ?? 'unknown'} on {c.retracted_at?.slice(0, 10)}
                              {c.retracted_reason && <div style={{ marginTop: 2 }}>Reason: {c.retracted_reason}</div>}
                            </div>
                          )}
                        </div>
                        <button
                          onClick={() => { setInspectorClaimId(null); setInspectorData(null) }}
                          style={{ background: 'transparent', border: 'none', color: 'var(--text-3)',
                                   fontSize: 18, cursor: 'pointer' }}
                        >
                          ✕
                        </button>
                      </div>

                      <div style={{ display: 'flex', gap: 8, marginBottom: 16, paddingBottom: 14, borderBottom: '1px solid #2f2f2f' }}>
                        {(c.status === 'active' || c.status === 'contested') && (
                          <button
                            disabled={inspectorActionPending}
                            onClick={async () => {
                              const reason = window.prompt('Reason for retracting this claim? (optional)') ?? undefined
                              setInspectorActionPending(true)
                              try {
                                await api.retractClaim(c.id, reason, 'user')
                                await refreshEntityNetwork()
                                const fresh = await api.getClaim(c.id)
                                setInspectorData(fresh)
                              } finally { setInspectorActionPending(false) }
                            }}
                            style={{
                              padding: '6px 14px', borderRadius: 6,
                              border: '1px solid var(--red)', background: 'rgba(239,68,68,0.12)',
                              color: 'var(--red)', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                            }}
                          >
                            {inspectorActionPending ? '…' : 'Retract claim'}
                          </button>
                        )}
                        {c.status === 'retracted' && (
                          <button
                            disabled={inspectorActionPending}
                            onClick={async () => {
                              setInspectorActionPending(true)
                              try {
                                await api.reactivateClaim(c.id)
                                await refreshEntityNetwork()
                                const fresh = await api.getClaim(c.id)
                                setInspectorData(fresh)
                              } finally { setInspectorActionPending(false) }
                            }}
                            style={{
                              padding: '6px 14px', borderRadius: 6,
                              border: '1px solid var(--green)', background: 'rgba(34,197,94,0.12)',
                              color: 'var(--green)', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                            }}
                          >
                            {inspectorActionPending ? '…' : 'Reactivate claim'}
                          </button>
                        )}
                      </div>

                      <div style={{ marginBottom: 16 }}>
                        <div style={{ fontSize: 11, color: 'var(--green)', textTransform: 'uppercase',
                                      letterSpacing: '0.06em', fontWeight: 700, marginBottom: 8 }}>
                          Supporting articles ({inspectorData.supporting_articles.length})
                        </div>
                        {inspectorData.supporting_articles.length === 0 && (
                          <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>None.</div>
                        )}
                        {inspectorData.supporting_articles.slice(0, 20).map(a => (
                          <div key={a.article_id} style={{ padding: '8px 0', borderBottom: '1px solid rgba(67,67,67,0.4)' }}>
                            <div style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 500 }}>
                              {a.article_url
                                ? <a href={a.article_url} target="_blank" rel="noreferrer" style={{ color: 'inherit', textDecoration: 'underline' }}>{a.article_title ?? '(no title)'}</a>
                                : (a.article_title ?? '(no title)')}
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                              {a.outlet ?? '?'} · {a.published_at ? formatArticleDate(a.published_at) : '?'} · v={a.extractor_version} · conf={a.confidence}
                            </div>
                            {a.sample_quote && (
                              <div style={{ fontSize: 12, color: 'var(--text-2)', fontStyle: 'italic', marginTop: 4 }}>
                                &ldquo;{a.sample_quote.slice(0, 200)}&rdquo;
                              </div>
                            )}
                          </div>
                        ))}
                      </div>

                      {inspectorData.contesting_articles.length > 0 && (
                        <div>
                          <div style={{ fontSize: 11, color: 'var(--red)', textTransform: 'uppercase',
                                        letterSpacing: '0.06em', fontWeight: 700, marginBottom: 8 }}>
                            Contesting articles ({inspectorData.contesting_articles.length})
                          </div>
                          {inspectorData.contesting_articles.map(a => (
                            <div key={a.article_id} style={{ padding: '8px 0', borderBottom: '1px solid rgba(67,67,67,0.4)' }}>
                              <div style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 500 }}>
                                {a.article_url
                                  ? <a href={a.article_url} target="_blank" rel="noreferrer" style={{ color: 'inherit', textDecoration: 'underline' }}>{a.article_title ?? '(no title)'}</a>
                                  : (a.article_title ?? '(no title)')}
                              </div>
                              <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                                {a.outlet ?? '?'} · {a.published_at ? formatArticleDate(a.published_at) : '?'} · v={a.extractor_version}
                              </div>
                              {a.sample_quote && (
                                <div style={{ fontSize: 12, color: 'var(--text-2)', fontStyle: 'italic', marginTop: 4 }}>
                                  &ldquo;{a.sample_quote.slice(0, 200)}&rdquo;
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )
                })()}
              </div>
            </div>
          )}

          {/* Hover-edge tooltip */}
          {hoverEdge && (
            <div style={{
              position: 'absolute', bottom: 14, left: 14,
              background: 'var(--bg-2)', border: '1px solid #434343', borderRadius: 8,
              padding: '10px 14px', maxWidth: 460, fontSize: 12, color: 'var(--text-1)',
              boxShadow: '0 6px 24px rgba(0,0,0,0.5)',
            }}>
              <div style={{ fontSize: 10, color: 'var(--text-2)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.05em', marginBottom: 4 }}>
                <span style={{ color: colorForRelation(hoverEdge.type) }}>
                  {relationLabel(hoverEdge.type)}
                </span>
                {' · '} {hoverEdge.weight} articles
                {hoverEdge.is_expired && (
                  <span style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 3, background: 'rgba(255,191,0,0.15)', color: 'var(--accent)', fontSize: 9 }}>
                    expired
                  </span>
                )}
              </div>
              {(hoverEdge.valid_from || hoverEdge.valid_to) && (
                <div style={{ fontSize: 11, color: 'var(--text-2)', marginBottom: 4 }}>
                  Valid: {(hoverEdge.valid_from || '').slice(0, 10) || '?'} → {hoverEdge.valid_to ? hoverEdge.valid_to.slice(0, 10) : 'current'}
                </div>
              )}
              <div style={{ fontStyle: 'italic', color: 'var(--text-2)', fontSize: 12 }}>
                &ldquo;{hoverEdge.sample_quote}&rdquo;
              </div>
            </div>
          )}

          {/* Legend */}
          <div style={{
            position: 'absolute', top: 14, right: 14,
            background: 'var(--bg-2)', border: '1px solid var(--border)',
            borderRadius: 8, padding: '10px 14px', fontSize: 11, minWidth: 170,
            color: 'var(--text-1)', boxShadow: 'var(--shadow-elev)',
          }}>
            <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
              Relation colors
            </div>
            {[
              { label: 'endorses / allies', c: 'var(--green)' },
              { label: 'attacks / criticizes', c: 'var(--red)' },
              { label: 'voted for / co-sponsored', c: '#3b82f6' },
              { label: 'voted against', c: '#f59e0b' },
              { label: 'attended', c: '#a855f7' },
              { label: 'role / geography', c: 'var(--text-3)' },
            ].map(x => (
              <div key={x.label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ width: 18, height: 2, background: x.c, borderRadius: 1 }} />
                <span style={{ color: 'var(--text-1)' }}>{x.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Side panel: selected entity details */}
        {selected ? (
          <div style={{ width: 360, flexShrink: 0, borderLeft: '1px solid #2f2f2f', background: 'var(--bg-2)', overflowY: 'auto' }}>
            <div style={{ padding: '18px 20px', borderBottom: '1px solid #2f2f2f' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <span style={{
                  width: 10, height: 10, borderRadius: '50%', background: colorForEntity(selected),
                }} />
                <span style={{ fontSize: 10, color: 'var(--text-2)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em' }}>
                  {selected.type}
                  {selected.affiliation && ` · ${selected.affiliation === 'D' ? 'Democrat' : selected.affiliation === 'R' ? 'Republican' : 'Independent'}`}
                </span>
              </div>
              <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.2, marginBottom: 6 }}>
                {selected.name}
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5 }}>
                {selected.description}
              </div>
              <div style={{ display: 'flex', gap: 14, marginTop: 12, fontSize: 11, color: 'var(--text-3)' }}>
                <div><strong style={{ color: 'var(--text-1)' }}>{selected.mention_count}</strong> mentions</div>
                <div>·</div>
                <div>{selected.first_seen} → {selected.last_seen}</div>
              </div>

              {/* Event-specific metadata: date / location / type, plus
                  a "dates contested" badge when multiple articles report
                  conflicting dates for the same event entity. */}
              {selected.type === 'event' && (selected.metadata?.event_date || selected.metadata?.event_location || selected.metadata?.date_disagreement) && (
                <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8,
                              background: 'var(--bg-sidebar)', border: '1px solid var(--border)' }}>
                  {selected.metadata?.event_date && (
                    <div style={{ fontSize: 12, color: 'var(--text-1)', marginBottom: 4 }}>
                      <Calendar size={11} style={{ display: 'inline', marginRight: 6, color: 'var(--text-3)' }} />
                      {String(selected.metadata.event_date).slice(0, 10)}
                    </div>
                  )}
                  {selected.metadata?.event_location && (
                    <div style={{ fontSize: 12, color: 'var(--text-1)', marginBottom: 4 }}>
                      <MapPin size={11} style={{ display: 'inline', marginRight: 6, color: 'var(--text-3)' }} />
                      {String(selected.metadata.event_location)}
                    </div>
                  )}
                  {selected.metadata?.date_disagreement === true && (
                    <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(67,67,67,0.4)' }}>
                      <div style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        padding: '2px 8px', borderRadius: 10,
                        background: 'rgba(255,191,0,0.18)', color: 'var(--accent)',
                        fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                      }}>
                        ⚠ dates contested
                      </div>
                      {Array.isArray(selected.metadata.date_observations) && selected.metadata.date_observations.length > 0 && (() => {
                        // Aggregate the date_observations into "N articles say YYYY-MM-DD" rows.
                        const counts: Record<string, number> = {}
                        for (const obs of selected.metadata.date_observations as Array<{ date?: string }>) {
                          const d = obs?.date ? String(obs.date).slice(0, 10) : null
                          if (!d) continue
                          counts[d] = (counts[d] ?? 0) + 1
                        }
                        const rows = Object.entries(counts).sort((a, b) => b[1] - a[1])
                        return (
                          <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-2)' }}>
                            {rows.map(([date, n]) => (
                              <div key={date} style={{ padding: '2px 0' }}>
                                <strong style={{ color: 'var(--text-1)' }}>{n}</strong> article{n === 1 ? '' : 's'} {n === 1 ? 'says' : 'say'} <span style={{ }}>{date}</span>
                              </div>
                            ))}
                          </div>
                        )
                      })()}
                    </div>
                  )}
                </div>
              )}

              {/* Multi-hop neighborhood — filters the graph to entities
                  within N hops of this one. */}
              <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--text-2)', marginRight: 4 }}>
                  Show neighborhood:
                </span>
                {[1, 2, 3].map(d => (
                  <button
                    key={d}
                    onClick={() => {
                      setNeighborhoodSeed(selected.id)
                      setNeighborhoodDepth(d)
                    }}
                    style={{
                      padding: '3px 10px', borderRadius: 5,
                      border: '1px solid ' + (neighborhoodSeed === selected.id && neighborhoodDepth === d ? 'var(--accent)' : 'var(--border)'),
                      background: neighborhoodSeed === selected.id && neighborhoodDepth === d ? 'rgba(255,191,0,0.12)' : 'var(--bg-2)',
                      color: neighborhoodSeed === selected.id && neighborhoodDepth === d ? 'var(--accent)' : 'var(--text-2)',
                      fontSize: 11, cursor: 'pointer', fontFamily: 'inherit',
                    }}
                  >
                    {d}-hop
                  </button>
                ))}
              </div>
            </div>

            <div style={{ padding: '14px 20px', borderBottom: '1px solid #2f2f2f' }}>
              <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                Connections ({selectedRelations.length})
              </div>
              {selectedRelations.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
                  No relationships in current filter.
                </div>
              )}
              {selectedRelations.map(r => {
                const otherId = r.source === selected.id ? r.target as string : r.source as string
                const other = entities.find(e => e.id === otherId)
                if (!other) return null
                const direction = r.source === selected.id ? '→' : '←'
                return (
                  <div
                    key={r.id}
                    onClick={() => { if (r.claim_id) setInspectorClaimId(r.claim_id) }}
                    title={r.claim_id ? 'Open claim inspector — see supporting articles' : undefined}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8, padding: '7px 4px',
                      borderBottom: '1px solid rgba(67, 67, 67, 0.3)',
                      borderRadius: 4,
                      cursor: r.claim_id ? 'pointer' : 'default',
                      transition: 'background 0.1s',
                    }}
                    onMouseEnter={ev => { if (r.claim_id) ev.currentTarget.style.background = 'rgba(255,255,255,0.04)' }}
                    onMouseLeave={ev => { ev.currentTarget.style.background = 'transparent' }}
                  >
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: colorForEntity(other), flexShrink: 0 }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {/* Entity name — clicking it navigates to the other entity
                          (stopPropagation so it doesn't trigger the row's inspector open). */}
                      <div
                        onClick={ev => { ev.stopPropagation(); setSelectedId(other.id) }}
                        title={`Navigate to ${other.name}`}
                        style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 500, cursor: 'pointer' }}
                      >
                        {other.name}
                      </div>
                      <div style={{ fontSize: 11, color: colorForRelation(r.type), marginTop: 1, opacity: r.is_expired ? 0.55 : 1 }}>
                        {direction} {relationLabel(r.type)} · {r.weight} article{r.weight === 1 ? '' : 's'}
                        {r.is_expired && (
                          <span style={{ marginLeft: 6, padding: '0 5px', borderRadius: 3, background: 'rgba(255,191,0,0.15)', color: 'var(--accent)', fontSize: 9 }}>
                            expired
                          </span>
                        )}
                        {r.claim_status === 'contested' && (
                          <span style={{ marginLeft: 6, padding: '0 5px', borderRadius: 3, background: 'rgba(255,191,0,0.15)', color: 'var(--accent)', fontSize: 9, fontWeight: 700 }}>
                            contested
                          </span>
                        )}
                        {r.claim_status === 'retracted' && (
                          <span style={{ marginLeft: 6, padding: '0 5px', borderRadius: 3, background: 'rgba(239,68,68,0.15)', color: 'var(--red)', fontSize: 9, fontWeight: 700 }}>
                            retracted
                          </span>
                        )}
                      </div>
                    </div>
                    {r.claim_id && (
                      <span style={{ fontSize: 14, color: 'var(--text-3)', flexShrink: 0, marginRight: 2 }} title="Inspect claim">⋯</span>
                    )}
                  </div>
                )
              })}
            </div>

            {/* v15.0 — quote-anchored claim records for this entity.
                Each row is one verbatim quote from an article where this
                entity appears in the quoted text. Distinct from the
                legacy edge-based "Connections" above. */}
            {claimRecordsForSelected && claimRecordsForSelected.records.length > 0 && (
              <div style={{ padding: '14px 20px', borderBottom: '1px solid #2f2f2f' }}>
                <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                  Claims about {selected.name} ({claimRecordsForSelected.records.length})
                </div>
                {claimRecordsForSelected.records.slice(0, 30).map(rec => {
                  const labelColors: Record<string, string> = {
                    attack: 'var(--red)',
                    defense: 'var(--candidate)',
                    endorsement: 'var(--green)',
                    policy_position: '#a855f7',
                    vote: '#3b82f6',
                    announcement: '#0ea5e9',
                    commitment: '#f59e0b',
                    statement: 'var(--text-3)',
                  }
                  const labelColor = rec.label ? (labelColors[rec.label] ?? 'var(--text-3)') : 'var(--text-3)'
                  // Highlight the selected entity's name within the quote.
                  // Match canonical name first, then any alias (best-effort).
                  const renderHighlightedSpan = () => {
                    const text = rec.evidence_span
                    const needles = [selected.name].filter(Boolean) as string[]
                    let lower = text.toLowerCase()
                    let firstHit: { start: number; len: number } | null = null
                    for (const n of needles) {
                      const idx = lower.indexOf(n.toLowerCase())
                      if (idx >= 0 && (firstHit === null || idx < firstHit.start)) {
                        firstHit = { start: idx, len: n.length }
                      }
                    }
                    if (!firstHit) return text
                    const before = text.slice(0, firstHit.start)
                    const hit = text.slice(firstHit.start, firstHit.start + firstHit.len)
                    const after = text.slice(firstHit.start + firstHit.len)
                    return (
                      <>
                        {before}
                        <span style={{ background: 'rgba(255,191,0,0.18)', color: 'var(--accent)', fontWeight: 600, padding: '0 2px', borderRadius: 2 }}>{hit}</span>
                        {after}
                      </>
                    )
                  }
                  const otherEntities = rec.entities.filter(e => e.id !== selected.id)
                  return (
                    <div key={rec.id} style={{
                      padding: '10px 0',
                      borderBottom: '1px solid rgba(67, 67, 67, 0.3)',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        {rec.label && (
                          <span style={{
                            fontSize: 9, padding: '1px 6px', borderRadius: 3,
                            background: `${labelColor}22`, color: labelColor,
                            textTransform: 'uppercase', fontWeight: 700,
                            letterSpacing: '0.05em',
                          }}>
                            {rec.label}
                          </span>
                        )}
                        <span style={{ fontSize: 9, color: 'var(--text-3)', textTransform: 'uppercase' }}>
                          {rec.confidence}
                        </span>
                        {otherEntities.length > 0 && (
                          <span style={{ fontSize: 10, color: 'var(--text-3)', marginLeft: 'auto' }} title={otherEntities.map(e => e.name).join(', ')}>
                            +{otherEntities.length} {otherEntities.length === 1 ? 'entity' : 'entities'}
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-1)', fontStyle: 'italic', lineHeight: 1.4, marginBottom: 4 }}>
                        &ldquo;{renderHighlightedSpan()}&rdquo;
                      </div>
                      {rec.article && (
                        <div style={{ fontSize: 10, color: 'var(--text-3)' }}>
                          {rec.article.source_url ? (
                            <a href={rec.article.source_url} target="_blank" rel="noreferrer"
                               style={{ color: 'var(--text-2)', textDecoration: 'none' }}>
                              {rec.article.title?.slice(0, 75) ?? '(no title)'}
                            </a>
                          ) : (
                            <span>{rec.article.title?.slice(0, 75) ?? '(no title)'}</span>
                          )}
                          {rec.article.source_name && (
                            <span style={{ color: 'var(--text-3)' }}> · {rec.article.source_name}</span>
                          )}
                          {rec.article.published_at && (
                            <span style={{ color: 'var(--text-3)' }}> · {rec.article.published_at.slice(0, 10)}</span>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {framesForSelected && framesForSelected.frames.length > 0 && (
              <div style={{ padding: '14px 20px', borderBottom: '1px solid #2f2f2f' }}>
                <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                  Featured in narratives ({framesForSelected.frames.length})
                </div>
                {framesForSelected.frames.slice(0, 8).map(f => (
                  <a
                    key={f.id}
                    href={`/narratives/${f.id}`}
                    style={{
                      display: 'flex', alignItems: 'baseline', gap: 8, padding: '6px 0',
                      borderBottom: '1px solid rgba(67, 67, 67, 0.3)',
                      textDecoration: 'none', color: 'inherit',
                    }}
                  >
                    <span style={{
                      fontSize: 9, padding: '1px 5px', borderRadius: 3,
                      background: f.owner_type === 'candidate' ? 'rgba(0,89,194,0.15)' : f.owner_type === 'opponent' ? 'rgba(215,25,19,0.15)' : 'rgba(115,115,115,0.15)',
                      color: f.owner_type === 'candidate' ? 'var(--candidate)' : f.owner_type === 'opponent' ? 'var(--opponent)' : 'var(--text-2)',
                      textTransform: 'uppercase', fontWeight: 700, flexShrink: 0,
                    }}>
                      {f.owner_type}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, color: 'var(--text-1)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {f.name}
                      </div>
                    </div>
                    <span style={{ fontSize: 10, color: 'var(--text-2)', fontFamily: 'monospace', flexShrink: 0 }}>
                      {f.article_overlap_count}
                    </span>
                  </a>
                ))}
              </div>
            )}

            <div style={{ padding: '14px 20px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                Recent mentions
              </div>
              {selected.recent_article_titles.length === 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>No recent articles.</div>
              )}
              {selected.recent_article_titles.map((t, i) => (
                <div key={`${i}-${t}`} style={{ fontSize: 12, color: 'var(--text-2)', padding: '7px 0', borderBottom: '1px solid rgba(67, 67, 67, 0.3)', lineHeight: 1.4 }}>
                  {t}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ width: 360, flexShrink: 0, borderLeft: '1px solid #2f2f2f', background: 'var(--bg-2)', padding: '28px 24px', color: 'var(--text-3)', fontSize: 13 }}>
            <div style={{ fontSize: 10, color: '#a78bfa', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 12 }}>
              ⬡ how to read this
            </div>
            <p style={{ lineHeight: 1.6, color: 'var(--text-2)', fontSize: 13 }}>
              Each <strong style={{ color: 'var(--text-1)' }}>circle</strong> is an entity (person, organization, bill, event, location).
              Size = how many articles mention it. Color = party affiliation, or type for non-political entities.
            </p>
            <p style={{ lineHeight: 1.6, color: 'var(--text-2)', fontSize: 13, marginTop: 12 }}>
              Each <strong style={{ color: 'var(--text-1)' }}>line</strong> is a relationship. Hover for details.
              Click any entity to see its connections, recent mentions, and timeline.
            </p>
            <div style={{ marginTop: 24, padding: 14, background: 'var(--bg-sidebar)', border: '1px solid #2f2f2f', borderRadius: 8 }}>
              <div style={{ fontSize: 10, color: '#a78bfa', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                ⬡ live data
              </div>
              <p style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.5, margin: 0 }}>
                Entities are extracted from your article corpus by an LLM (gpt-4o-mini) and matched against a seeded canonical list. {stats.seeded_count} of {stats.entity_count} entities here were pre-seeded; the rest were auto-discovered.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
