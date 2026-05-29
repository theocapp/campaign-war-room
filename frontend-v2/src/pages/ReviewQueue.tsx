import { CheckCircle, CheckSquare, ChevronDown, ChevronRight, Newspaper, RefreshCw, Sparkles, Square, Trash2, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { PromoteModal } from '@/components/PromoteModal'
import { InfoTooltip } from '@/components/InfoTooltip'
import { describeError, useToast } from '@/components/Toast'
import type {
  CandidateFrameCluster,
  NarrativeFrame, NarrativeLandscape, NarrativeLandscapeCluster, NarrativeLandscapePoint,
  NarrativeTriageVerdict, NarrativeTriageVerdictKind,
  OwnerType, ReviewQueueItem,
} from '@/api/types'

// KG Contradictions tab hidden 2026-05-29: the underlying entity_relations
// data was produced by retired v14.x extractors; v15.0 emits quote-anchored
// claim_records instead. The legacy tables and EntityReview component are
// preserved but not surfaced — see INTER_SESSION.md Session F for context.
type TabKey = 'articles' | 'narratives'
import { QuadrantPalette, quadrantKey, quadrantNamedLabel } from '@/lib/quadrantColor'
import { formatArticleDate } from '@/lib/formatDate'

const VERDICT_HELP: Record<NarrativeTriageVerdictKind, string> = {
  auto_promote_suggested: 'Suggest promote — Theo thinks this cluster is a real, new narrative worth tracking. Click "Confirm promote" to add it as a tracked narrative frame.',
  auto_merge: 'Suggest merge — Theo thinks this cluster is just a new angle on a narrative you\'re already tracking. Clicking "Merge" will fold these articles into the existing frame.',
  human_review: 'Theo uncertain — Theo doesn\'t have a strong opinion. Take a look and decide whether to promote, merge, or dismiss.',
  auto_reject: 'Theo: likely noise — small cluster, single outlet, looks more like random co-mention than a real narrative. Safe to ignore in most cases.',
}

const RELEVANCE_HELP: Record<string, string> = {
  critical: 'Critical — directly about your candidate or race, often with attack potential. Look at these first.',
  high: 'High — strongly relevant to your race. Worth reviewing.',
  medium: 'Medium — relevant context (e.g. district news, related policy).',
  low: 'Low — tangentially relevant. Useful background only.',
  irrelevant: 'Irrelevant — not about your race. Usually safe to dismiss.',
}

// Stable cluster key matching the backend's sha256 fingerprint (but in plain
// JS — we don't need the hash itself, just an equal-keying string). Backend
// sorts unique member_candidate_frame_ids and hashes; we sort + join the
// same way and use the joined string as the Map key. Same result, no crypto.
function clusterKey(memberIds: number[]): string {
  return [...new Set(memberIds)].sort((a, b) => a - b).join('|')
}

// Verdict → UI metadata: badge color, label, sort priority.
// Priority order: 1=most-attention, 4=least. Sort within = confidence desc.
const VERDICT_META: Record<NarrativeTriageVerdictKind, {
  label: string; tone: 'suggest' | 'merge' | 'uncertain' | 'noise'; sortPriority: number;
}> = {
  auto_promote_suggested: { label: 'Suggest promote', tone: 'suggest', sortPriority: 1 },
  auto_merge:             { label: 'Suggest merge',   tone: 'merge',   sortPriority: 2 },
  human_review:           { label: 'AI uncertain',    tone: 'uncertain', sortPriority: 3 },
  auto_reject:            { label: 'AI: likely noise', tone: 'noise', sortPriority: 4 },
}

// Colors via CSS variables so dark/light toggle works. See index.css.
const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)',
  accent: 'var(--accent)',
  green: 'var(--green)', red: 'var(--red)',
}

const REL_COLORS: Record<string, { color: string; bg: string; border: string }> = {
  critical: { color: '#f87171', bg: 'rgba(215,25,19,0.08)', border: 'rgba(215,25,19,0.25)' },
  high: { color: '#fb923c', bg: 'rgba(234,88,12,0.08)', border: 'rgba(234,88,12,0.25)' },
  medium: { color: '#fbbf24', bg: 'rgba(202,138,4,0.08)', border: 'rgba(202,138,4,0.25)' },
  low: { color: '#a1a1a1', bg: 'rgba(161,161,161,0.08)', border: 'rgba(161,161,161,0.2)' },
  irrelevant: { color: '#555', bg: 'rgba(85,85,85,0.08)', border: 'rgba(85,85,85,0.2)' },
}

/** Strip Google-News bookkeeping from a source name so the operator sees
 *  the actual outlet (or the search query, when the alias chain runs out).
 *  Patterns seen in the wild:
 *    "WNEP-TV — Google News Feed"            → "WNEP-TV"
 *    "Google News — NEPA … Government"       → "NEPA … Government"
 *  Matches em-dash, en-dash, and plain hyphen separators. */
function cleanSourceName(raw?: string): string {
  if (!raw) return ''
  let s = raw.trim()
  s = s.replace(/\s*[—–-]\s*Google News(?: Feed)?\s*$/i, '')
  s = s.replace(/^\s*Google News\s*[—–-]\s*/i, '')
  return s.trim() || raw.trim()
}

/** Title-cased surname from "First Last" or FEC "LAST, FIRST" format.
 *  Mirrors the Narratives page helper so the same surnames feed the
 *  5-quadrant label substitution on both pages. */
function lastName(raw?: string): string {
  if (!raw) return ''
  const t = raw.trim()
  const last = (t.includes(',') ? t.split(',')[0] : t.split(/\s+/).pop() || '').trim()
  return last ? last[0].toUpperCase() + last.slice(1).toLowerCase() : ''
}

function RelBadge({ label }: { label?: string }) {
  if (!label) return null
  const style = REL_COLORS[label] ?? REL_COLORS.low
  const help = RELEVANCE_HELP[label] ?? ''
  return (
    <span
      title={help}
      style={{
        fontSize: 10, color: style.color, background: style.bg,
        border: `1px solid ${style.border}`, padding: '2px 7px',
        borderRadius: 4, letterSpacing: '0.07em', flexShrink: 0, fontWeight: 600,
        cursor: help ? 'help' : 'default',
      }}
    >
      {label.toUpperCase()}
    </span>
  )
}

function SentimentDot({ s }: { s?: string }) {
  const colors: Record<string, string> = {
    positive: '#4ade80', negative: '#f87171', neutral: '#a1a1a1', mixed: '#fbbf24',
  }
  if (!s) return null
  return (
    <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: colors[s] ?? '#a1a1a1' }} />
  )
}

export function ReviewQueue() {
  // Admin gate — non-admins see the queue (titles, sources, actions) but
  // not the per-article CRITICAL/HIGH/MEDIUM/LOW bucket, including the
  // red-border emphasis that highlights critical items.
  const { user } = useAuth()
  const isAdmin = !!user?.isAdmin
  // Tab state — driven by ?tab= so refreshes/bookmarks land back on the
  // same tab. Default to "articles" (the historical landing view).
  const [searchParams, setSearchParams] = useSearchParams()
  const tabParam = searchParams.get('tab')
  const activeTab: TabKey = tabParam === 'narratives' ? tabParam : 'articles'
  function setActiveTab(t: TabKey) {
    const next = new URLSearchParams(searchParams)
    if (t === 'articles') next.delete('tab')
    else next.set('tab', t)
    setSearchParams(next, { replace: true })
  }
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [processing, setProcessing] = useState<Set<number>>(new Set())
  const [done, setDone] = useState<Set<number>>(new Set())
  // Per-row error message — set when an action fails so the operator can
  // distinguish "did nothing" from "tried and failed." Cleared when the
  // row is retried successfully or removed from the list.
  const [actionErrors, setActionErrors] = useState<Map<number, string>>(new Map())
  const toast = useToast()
  // V13.10 — proposed-cluster review section. Lives above the article queue;
  // moved here from the Landscape page so the landscape can focus on the
  // established narrative ecosystem.
  //
  // Phase D — also fetches AI triage verdicts and joins them by cluster
  // fingerprint (sorted member candidate_frame_ids). Verdicts decorate
  // each row with a badge + sort priority, pre-fill the Promote modal,
  // and add a one-click Merge action for auto_merge clusters.
  const [proposed, setProposed] = useState<NarrativeLandscape | null>(null)
  const [proposedExpanded, setProposedExpanded] = useState(true)
  const [showAutoRejected, setShowAutoRejected] = useState(false)
  // Tracked frames — needed to render "Merge into [name]" labels.
  const [trackedFrames, setTrackedFrames] = useState<NarrativeFrame[]>([])
  // Cluster currently being promoted (opens PromoteModal). We carry the
  // joined verdict here so the modal gets the pre-fill values.
  const [promoteTarget, setPromoteTarget] = useState<{
    cluster: NarrativeLandscapeCluster
    verdict: NarrativeTriageVerdict | null
  } | null>(null)
  const [verdicts, setVerdicts] = useState<NarrativeTriageVerdict[]>([])
  const [dismissedClusters, setDismissedClusters] = useState<Set<number>>(new Set())
  // Companion fetch: the *promotion-ready* clusters surfaced on the Narratives
  // page banner. Used to split the Review Queue into two tiers — "ready" (the
  // same clusters the Narratives page shows) and "watch list" (clustered by
  // HDBSCAN but doesn't yet meet the promotion bar: < 3 articles or < 2
  // outlets or post-GPT-4o-dedup drop). The two pages now stay reconciled.
  const [pendingSuggestions, setPendingSuggestions] = useState<CandidateFrameCluster[] | null>(null)
  // Candidate/opponent surnames — used to substitute into the 5-quadrant
  // labels on each proposed-cluster row (e.g. "Cognetti's Offense").
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')
  // Triage-pass state
  const [triageRunning, setTriageRunning] = useState(false)
  const [triageLastResult, setTriageLastResult] = useState<string | null>(null)
  // Recently auto-applied verdicts (hands-off mode) — shown as a banner
  // above the proposals list so the user sees what the AI created/merged
  // automatically.
  const [recentlyApplied, setRecentlyApplied] = useState<Array<{
    triage_id: number;
    action: 'auto_merge' | 'auto_promote';
    frame_id: number;
    frame_name: string;
    candidate_frames_attached?: number;
  }>>([])
  // Merge-in-progress feedback (one cluster at a time).
  const [mergingTriageId, setMergingTriageId] = useState<number | null>(null)
  // Snapshot-refresh state. The user clicks "Refresh proposals" to pull in
  // any new clusters from the live HDBSCAN compute; existing clusters stay
  // unless the user dismissed / promoted / merged them. This is the explicit
  // "I'm ready for new stuff" trigger that the persistent-snapshot design
  // is built around.
  const [refreshingSnapshot, setRefreshingSnapshot] = useState(false)
  const [snapshotRefreshResult, setSnapshotRefreshResult] = useState<string | null>(null)
  // Recently-filtered safety view: items the keyword relevance gate kicked
  // out of the main queue. Loaded lazily on first expand so we don't pay
  // for the extra fetch on every page load.
  const [filteredOut, setFilteredOut] = useState<ReviewQueueItem[] | null>(null)
  const [filteredOutExpanded, setFilteredOutExpanded] = useState(false)
  const [filteredOutLoading, setFilteredOutLoading] = useState(false)

  useEffect(() => {
    api.reviewQueue().then(setItems).catch(() => {}).finally(() => setLoading(false))
    // Proposed-cluster fetch runs in parallel; failure is silent (the
    // section just shows "no proposals" if it errors).
    //
    // Reads from the PERSISTENT SNAPSHOT (not the live HDBSCAN compute) so
    // the list stays stable between visits. New clusters only appear when
    // the user clicks "Refresh proposals" or the nightly scheduler job
    // runs. See backend services/proposed_cluster_snapshot.py for the
    // lifecycle.
    api.narrativeProposalsSnapshot().then(setProposed).catch(() => {})
    // Triage verdicts + tracked frames in parallel.
    api.narrativeTriageVerdicts().then(setVerdicts).catch(() => {})
    api.narrativeFrames().then(setTrackedFrames).catch(() => {})
    // Pull the promotion-ready cluster list so we can split proposals
    // into "ready" vs "watch" tiers. Treat failure as empty (everything
    // becomes "watch") rather than blocking the page.
    api.pendingCandidateClusters(21)
      .then(r => setPendingSuggestions(r.suggestions))
      .catch(() => setPendingSuggestions([]))
    // Candidate / opponent surnames for quadrant labeling.
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, [])

  // Group cluster members by cluster_id for the Promote modal + verdict joining.
  const membersByCluster = useMemo(() => {
    const m = new Map<number, NarrativeLandscapePoint[]>()
    if (!proposed) return m
    for (const p of proposed.points) {
      if (p.cluster_id < 0) continue
      const arr = m.get(p.cluster_id) || []
      arr.push(p); m.set(p.cluster_id, arr)
    }
    return m
  }, [proposed])

  // Phase D — join clusters to verdicts by sorted-ids fingerprint.
  // verdictsByKey: clusterKey → verdict. We compute the cluster's key from
  // its current members, look up the matching verdict, and decorate the row.
  const verdictsByKey = useMemo(() => {
    const m = new Map<string, NarrativeTriageVerdict>()
    for (const v of verdicts) {
      if (v.dismissed_at) continue  // skip dismissed verdicts
      m.set(clusterKey(v.member_candidate_frame_ids), v)
    }
    return m
  }, [verdicts])

  // Tracked-frame lookup by id (for "Merge into [name]" labels).
  const frameById = useMemo(() => {
    const m = new Map<number, NarrativeFrame>()
    for (const f of trackedFrames) m.set(f.id, f)
    return m
  }, [trackedFrames])

  // Each cluster gets a "DecoratedCluster" row: cluster + (optional) verdict
  // + members. Verdict drives sort + UI treatment.
  type DecoratedCluster = {
    cluster: NarrativeLandscapeCluster
    members: NarrativeLandscapePoint[]
    verdict: NarrativeTriageVerdict | null
  }
  const decoratedProposals = useMemo<DecoratedCluster[]>(() => {
    if (!proposed) return []
    const decorated = proposed.clusters
      .filter(c => !dismissedClusters.has(c.cluster_id))
      .map(c => {
        const members = membersByCluster.get(c.cluster_id) || []
        const key = clusterKey(members.map(m => m.candidate_frame_id))
        return { cluster: c, members, verdict: verdictsByKey.get(key) || null }
      })
    // Sort priority: verdict.sortPriority asc, then confidence desc,
    // then cluster size desc as final tie-break.
    decorated.sort((a, b) => {
      const ap = a.verdict ? VERDICT_META[a.verdict.verdict].sortPriority : 3
      const bp = b.verdict ? VERDICT_META[b.verdict.verdict].sortPriority : 3
      if (ap !== bp) return ap - bp
      const aconf = a.verdict?.confidence ?? 0
      const bconf = b.verdict?.confidence ?? 0
      if (aconf !== bconf) return bconf - aconf
      return b.cluster.size - a.cluster.size
    })
    return decorated
  }, [proposed, dismissedClusters, membersByCluster, verdictsByKey])

  // Split for rendering: auto_reject rows hide in a footer toggle.
  const primaryProposals = decoratedProposals.filter(
    d => !d.verdict || d.verdict.verdict !== 'auto_reject',
  )
  const autoRejectedProposals = decoratedProposals.filter(
    d => d.verdict?.verdict === 'auto_reject',
  )

  // Set of candidate_frame_ids that belong to a "promotion-ready" cluster on
  // the Narratives page. We use this to split the Review Queue's proposals.
  // A landscape cluster is "ready" if ANY of its members appears in any
  // promotion-ready cluster on the Narratives page — that way the two views
  // line up even when GPT-4o's dedup pass renames or merges clusters.
  const readyCandidateFrameIds = useMemo(() => {
    const s = new Set<number>()
    for (const sug of pendingSuggestions ?? []) {
      for (const id of sug.candidate_frame_ids) s.add(id)
    }
    return s
  }, [pendingSuggestions])

  // Two tiers: ready-to-promote vs watch-list. Until the pending fetch
  // resolves (readyCandidateFrameIds empty), everything renders as "watch"
  // — that's a tolerable transient state, not a wrong one.
  const { readyProposals, watchProposals } = useMemo(() => {
    const ready: typeof primaryProposals = []
    const watch: typeof primaryProposals = []
    for (const dc of primaryProposals) {
      const hasReady = dc.members.some(m => readyCandidateFrameIds.has(m.candidate_frame_id))
      if (hasReady) ready.push(dc)
      else watch.push(dc)
    }
    return { readyProposals: ready, watchProposals: watch }
  }, [primaryProposals, readyCandidateFrameIds])

  async function runTriagePass() {
    if (triageRunning) return
    setTriageRunning(true); setTriageLastResult(null)
    try {
      // V13.10e — hands_off=true by default. The AI auto-creates new
      // tracked narratives + auto-merges into existing ones for high-
      // confidence verdicts. The auto_executed list tells us what was
      // actually done so we can surface it in the banner.
      const r = await api.runNarrativeTriage(21, false, true)
      const promoted = r.auto_executed.filter(x => x.action === 'auto_promote')
      const merged = r.auto_executed.filter(x => x.action === 'auto_merge')
      setRecentlyApplied(r.auto_executed)
      setTriageLastResult(
        `Auto-created ${promoted.length}, auto-merged ${merged.length}, `
        + `${r.human_review} need review · ${r.elapsed_seconds.toFixed(1)}s`,
      )
      // Refresh verdicts AND proposals + tracked frames (since the
      // auto-executions may have created new tracked narratives and
      // resolved candidate frames).
      const [v, p, f] = await Promise.all([
        api.narrativeTriageVerdicts(),
        api.narrativeProposalsSnapshot(),
        api.narrativeFrames(),
      ])
      setVerdicts(v); setProposed(p); setTrackedFrames(f)
    } catch (e) {
      setTriageLastResult(`Error: ${(e as Error).message}`)
    } finally {
      setTriageRunning(false)
    }
  }

  async function toggleFilteredOut() {
    const next = !filteredOutExpanded
    setFilteredOutExpanded(next)
    if (next && filteredOut === null && !filteredOutLoading) {
      setFilteredOutLoading(true)
      try {
        const r = await api.reviewQueueFilteredOut()
        setFilteredOut(r)
      } catch {
        setFilteredOut([])
      } finally {
        setFilteredOutLoading(false)
      }
    }
  }

  async function handleRefreshSnapshot() {
    if (refreshingSnapshot) return
    setRefreshingSnapshot(true); setSnapshotRefreshResult(null)
    try {
      const r = await api.refreshNarrativeProposalsSnapshot()
      setSnapshotRefreshResult(
        `+${r.inserted} new · ${r.refreshed} updated`,
      )
      const p = await api.narrativeProposalsSnapshot()
      setProposed(p)
    } catch (e) {
      setSnapshotRefreshResult(`Error: ${(e as Error).message}`)
    } finally {
      setRefreshingSnapshot(false)
    }
  }

  async function handleExecuteMerge(triageId: number) {
    if (mergingTriageId !== null) return
    setMergingTriageId(triageId)
    try {
      await api.executeTriageMerge(triageId)
      // Refresh verdicts (the row gets stamped applied_at) and proposals
      // (the merged cluster's candidate frames are resolved → cluster
      // disappears from next landscape compute).
      const [v, p] = await Promise.all([
        api.narrativeTriageVerdicts(),
        api.narrativeProposalsSnapshot(),
      ])
      setVerdicts(v); setProposed(p)
    } catch (e) {
      console.error('merge failed:', e)
    } finally {
      setMergingTriageId(null)
    }
  }

  // Newest first. Falls back to created_at when published_at is missing
  // (e.g. social-feed rows where the platform never gave us a publish time).
  // Items with no date at all sort to the bottom.
  const visibleItems = items
    .filter(i => !done.has(i.id))
    .slice()
    .sort((a, b) => {
      const aDate = a.published_at ?? a.created_at ?? ''
      const bDate = b.published_at ?? b.created_at ?? ''
      if (!aDate && !bDate) return 0
      if (!aDate) return 1
      if (!bDate) return -1
      return bDate.localeCompare(aDate)
    })

  function toggleSelect(id: number) {
    setSelected(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }

  function toggleAll() {
    if (selected.size === visibleItems.length) setSelected(new Set())
    else setSelected(new Set(visibleItems.map(i => i.id)))
  }

  async function doAction(id: number, action: () => Promise<unknown>) {
    setProcessing(p => new Set([...p, id]))
    // Clear any prior error for this row so a retry visually resets.
    setActionErrors(prev => {
      if (!prev.has(id)) return prev
      const n = new Map(prev); n.delete(id); return n
    })
    try {
      await action()
      setDone(d => new Set([...d, id]))
      setSelected(s => { const n = new Set(s); n.delete(id); return n })
    } catch (err) {
      const message = describeError(err, 'Action failed')
      setActionErrors(prev => { const n = new Map(prev); n.set(id, message); return n })
      toast.push(message, 'error')
    } finally {
      setProcessing(p => { const n = new Set(p); n.delete(id); return n })
    }
  }

  async function bulkAction(action: (ids: number[]) => Promise<unknown>) {
    const ids = Array.from(selected)
    if (!ids.length) return
    ids.forEach(id => setProcessing(p => new Set([...p, id])))
    try {
      await action(ids)
      setDone(d => new Set([...d, ...ids]))
      setSelected(new Set())
      // Bulk success implicitly clears any per-row errors on the same ids.
      setActionErrors(prev => {
        if (ids.every(id => !prev.has(id))) return prev
        const n = new Map(prev); ids.forEach(id => n.delete(id)); return n
      })
    } catch (err) {
      const message = describeError(err, `Bulk action failed (${ids.length} items)`)
      setActionErrors(prev => {
        const n = new Map(prev); ids.forEach(id => n.set(id, message)); return n
      })
      toast.push(message, 'error')
    } finally {
      ids.forEach(id => setProcessing(p => { const n = new Set(p); n.delete(id); return n }))
    }
  }

  // Section divider inside the Proposed Narratives panel. Used to label
  // the ready-to-promote tier vs the watch-list tier. Tone drives accent
  // color: ready = bright accent, watch = muted text.
  function TierHeader({ label, count, tone, tooltip }: {
    label: string; count: number; tone: 'ready' | 'watch'; tooltip: string
  }) {
    const accent = tone === 'ready' ? C.accent : C.text3
    const bgFor = tone === 'ready' ? `${C.accent}11` : 'transparent'
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 16px', background: bgFor,
        borderTop: `1px solid ${C.border}`,
      }}>
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
          textTransform: 'uppercase', color: accent,
          display: 'inline-flex', alignItems: 'center',
        }}>
          {label}
          <InfoTooltip text={tooltip} />
        </span>
        <span style={{
          fontSize: 10, color: accent,
          background: `${accent}22`, border: `1px solid ${accent}55`,
          borderRadius: 4, padding: '1px 6px', fontWeight: 700,
        }}>
          {count}
        </span>
      </div>
    )
  }

  // Single proposed-cluster row. Used by both the ready-to-promote map
  // and the watch-list map — keeps the visual treatment identical so
  // demoting a cluster from one tier to the other is purely a re-sort,
  // not a re-style.
  function renderClusterRow({ cluster: c, members, verdict }: DecoratedCluster) {
    // 5-quadrant classification: combine owner_type_hint (who benefits)
    // and subject_type_hint (who it's about) into one of our_defense /
    // our_offense / their_defense / their_offense / media. Falls back
    // to media when subject_type_hint is missing (old cached responses).
    const qk = quadrantKey(c.owner_type_hint, c.subject_type_hint ?? null)
    const oc = QuadrantPalette[qk]
    const quadrantLabelText = quadrantNamedLabel(qk, candidateName, opponentName)
    const claim = verdict?.suggested_description || members[0]?.evidence_quote
    const displayName = verdict?.suggested_name || c.representative_name
    const meta = verdict ? VERDICT_META[verdict.verdict] : null
    const badgeColor = meta?.tone === 'merge' ? C.candidate
                      : meta?.tone === 'suggest' ? C.accent
                      : meta?.tone === 'uncertain' ? C.text3
                      : C.text3
    const mergeTarget = verdict?.verdict === 'auto_merge' && verdict.suggested_merge_frame_id
      ? frameById.get(verdict.suggested_merge_frame_id) || null
      : null
    const isMerging = mergingTriageId === (verdict?.id ?? -1)
    return (
      <div key={c.cluster_id} style={{
        padding: '12px 16px',
        borderTop: `1px solid ${C.border}`,
        display: 'flex', gap: 12, alignItems: 'flex-start',
      }}>
        <div style={{
          flexShrink: 0, width: 10, height: 10, borderRadius: '50%',
          background: oc, marginTop: 5,
        }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
            {meta && (
              <span
                title={verdict ? VERDICT_HELP[verdict.verdict] : ''}
                style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: badgeColor, background: `${badgeColor}1c`,
                  border: `1px solid ${badgeColor}55`, borderRadius: 4,
                  padding: '2px 6px',
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  cursor: 'help',
                }}
              >
                {meta.label}
                {verdict && verdict.confidence > 0 && (
                  <span style={{ opacity: 0.85 }}>
                    · {Math.round(verdict.confidence * 100)}%
                  </span>
                )}
              </span>
            )}
            {mergeTarget && (
              <span style={{ fontSize: 10, color: C.text3 }}>
                → <span style={{ color: C.text2, fontWeight: 600 }}>{mergeTarget.name}</span>
              </span>
            )}
          </div>
          <div style={{ fontSize: 13, fontWeight: 600, color: C.text1, lineHeight: 1.3, marginBottom: 4 }}>
            {displayName}
          </div>
          <div style={{ fontSize: 11, color: C.text3, marginBottom: claim ? 6 : 0 }}>
            {c.size} candidate frame{c.size === 1 ? '' : 's'} ·
            {' '}{c.outlet_count} outlet{c.outlet_count === 1 ? '' : 's'} ·
            {' '}<span style={{ color: oc, fontWeight: 600 }}>{quadrantLabelText}</span>
          </div>
          {claim && (
            <div style={{
              fontSize: 12, color: C.text2, lineHeight: 1.45,
              fontStyle: 'italic', borderLeft: `2px solid ${C.border}`,
              paddingLeft: 8,
              overflow: 'hidden', display: '-webkit-box',
              WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
            } as CSSProperties}>
              "{claim}"
            </div>
          )}
          {verdict?.reasoning && (
            <div style={{
              fontSize: 11, color: C.text3, marginTop: 6,
              lineHeight: 1.4,
            }}>
              <span style={{ fontWeight: 600, color: C.text2 }}>AI:</span> {verdict.reasoning}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0, alignItems: 'flex-start' }}>
          {/* Promote/Merge actions trigger backend writes that cost LLM
              money downstream (and the promote endpoint is admin-gated).
              Non-admins keep the dismiss-only experience so they can still
              clear noise off the queue. */}
          {isAdmin && (verdict?.verdict === 'auto_merge' && mergeTarget ? (
            <button
              onClick={() => handleExecuteMerge(verdict.id)}
              disabled={isMerging}
              style={{
                padding: '6px 12px', fontSize: 11, fontWeight: 700,
                background: C.candidate, color: '#fff', border: 'none',
                borderRadius: 5, cursor: isMerging ? 'wait' : 'pointer',
                opacity: isMerging ? 0.7 : 1,
              }}
            >
              {isMerging ? 'Merging…' : `Merge → ${mergeTarget.name.length > 18 ? mergeTarget.name.slice(0, 18) + '…' : mergeTarget.name}`}
            </button>
          ) : (
            <button
              onClick={() => setPromoteTarget({ cluster: c, verdict })}
              style={{
                padding: '6px 12px', fontSize: 11, fontWeight: 700,
                background: C.accent, color: '#000', border: 'none',
                borderRadius: 5, cursor: 'pointer',
              }}
            >
              {verdict?.verdict === 'auto_promote_suggested' ? 'Confirm promote' : 'Promote'}
            </button>
          ))}
          <button
            title="Dismiss"
            onClick={() => {
              setDismissedClusters(s => new Set([...s, c.cluster_id]))
              if (verdict) {
                api.dismissTriageVerdict(verdict.id).catch(() => {})
              }
              // Stamp the snapshot row too — keeps the proposal off the
              // open list across reloads, not just for the current session.
              const memberIds = members.map(m => m.candidate_frame_id)
              if (memberIds.length > 0) {
                api.dismissProposalSnapshot(memberIds).catch(() => {})
              }
            }}
            style={{
              padding: '6px 8px', background: 'transparent',
              border: `1px solid ${C.border}`, color: C.text3,
              borderRadius: 5, cursor: 'pointer', display: 'flex', alignItems: 'center',
            }}
          >
            <XCircle size={13} />
          </button>
        </div>
      </div>
    )
  }

  // Tab-specific subtitle for the sticky header.
  const proposalsTotal = (proposed?.clusters?.length ?? 0)
  const subtitleByTab: Record<TabKey, string> = {
    articles: loading ? '...' : `${visibleItems.length} articles pending review`,
    narratives: proposed === null ? '...' : `${proposalsTotal} proposed narrative${proposalsTotal === 1 ? '' : 's'}`,
  }

  const TABS: Array<{ key: TabKey; label: string; icon: typeof Newspaper; count: number | null }> = [
    { key: 'articles',   label: 'Articles',            icon: Newspaper, count: loading ? null : visibleItems.length },
    { key: 'narratives', label: 'Potentially emerging narratives', icon: Sparkles,  count: proposed === null ? null : proposalsTotal },
  ]

  return (
    <div style={{ minHeight: '100%', background: C.bg1 }}>
      {/* Header */}
      <div style={{
        padding: '16px 28px 0 28px', borderBottom: `1px solid ${C.border}`,
        background: 'var(--bg-1)', backdropFilter: 'blur(8px)',
        position: 'sticky', top: 0, zIndex: 10,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 22, fontWeight: 800, color: C.text1, letterSpacing: '-0.01em', display: 'inline-flex', alignItems: 'center' }}>
              Review Queue
              <InfoTooltip
                text={'"Articles" need you to confirm they\'re race-relevant. "Potentially emerging narratives" are clusters that may be worth tracking — promote or dismiss.'}
                size={14}
              />
            </div>
            <div className="section-label" style={{ marginTop: 2 }}>
              {subtitleByTab[activeTab]}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {activeTab === 'articles' && visibleItems.length > 0 && (
              <button onClick={toggleAll} className="btn btn-ghost">
                {selected.size === visibleItems.length ? <CheckSquare size={13} /> : <Square size={13} />}
                {selected.size === visibleItems.length ? 'Deselect All' : 'Select All'}
              </button>
            )}
          </div>
        </div>
        {/* Tab nav */}
        <div style={{ display: 'flex', gap: 4, marginTop: 14 }}>
          {TABS.map(t => {
            const Icon = t.icon
            const isActive = activeTab === t.key
            return (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                style={{
                  padding: '8px 14px', background: 'transparent', border: 'none',
                  borderBottom: isActive ? `2px solid ${C.accent}` : '2px solid transparent',
                  color: isActive ? C.text1 : C.text3,
                  cursor: 'pointer', fontSize: 12, fontWeight: 700,
                  letterSpacing: '0.02em',
                  display: 'inline-flex', alignItems: 'center', gap: 7,
                  marginBottom: -1,
                  transition: 'color 0.12s ease, border-color 0.12s ease',
                }}
              >
                <Icon size={13} />
                {t.label}
                {t.count !== null && (
                  <span style={{
                    fontSize: 10, fontWeight: 700,
                    color: isActive ? C.accent : C.text3,
                    background: isActive ? `${C.accent}22` : 'transparent',
                    border: `1px solid ${isActive ? `${C.accent}55` : C.border}`,
                    borderRadius: 4, padding: '1px 6px',
                  }}>
                    {t.count}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Bulk action bar — articles tab only */}
      {activeTab === 'articles' && selected.size > 0 && (
        <div style={{
          padding: '10px 28px', background: C.bg3, borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 13, color: C.text2, fontWeight: 600 }}>
            {selected.size} selected
          </span>
          <div style={{ flex: 1, display: 'flex', gap: 8 }}>
            <button onClick={() => bulkAction(ids => api.bulkReview(ids))} className="btn btn-success">
              <CheckCircle size={13} />
              Bulk Review
            </button>
            <button onClick={() => bulkAction(ids => api.bulkDismiss(ids))} className="btn btn-danger">
              <Trash2 size={13} />
              Bulk Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Content — tab-aware. Each tab renders its own panel; the outer
          wrapper just sets shared padding/centering. KG tab uses a wider
          max-width because contradiction rows are denser. */}
      <div style={{
        padding: '20px 28px',
        maxWidth: 860,
        margin: '0 auto',
      }}>
        {/* ── Proposed Narratives tab ────────────────────────────────── */}
        {activeTab === 'narratives' && (
        <>
        {/* V13.10e — Hands-off auto-applied banner. After a triage pass
            in hands_off mode, the AI may have auto-created new tracked
            narratives or auto-merged candidate frames into existing ones.
            Surface those here so the user sees what happened, with a
            link to the narratives page to audit + undo manually if
            anything's wrong. */}
        {recentlyApplied.length > 0 && (
          <div style={{
            marginBottom: 18,
            background: 'rgba(34, 197, 94, 0.06)',
            border: `1px solid rgba(34, 197, 94, 0.35)`,
            borderRadius: 10, padding: '12px 16px',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
            }}>
              <Sparkles size={14} style={{ color: C.green }} />
              <span style={{ fontSize: 13, fontWeight: 700, color: C.text1 }}>
                Recently auto-applied
              </span>
              <span style={{
                fontSize: 10, color: C.green, background: `rgba(34,197,94,0.15)`,
                border: `1px solid rgba(34,197,94,0.4)`, borderRadius: 4,
                padding: '2px 7px', letterSpacing: '0.05em', fontWeight: 700,
              }}>
                {recentlyApplied.length}
              </span>
              <span style={{ flex: 1 }} />
              <button
                onClick={() => setRecentlyApplied([])}
                title="Dismiss this banner"
                style={{
                  background: 'transparent', border: 'none', color: C.text3,
                  cursor: 'pointer', padding: 4, display: 'inline-flex',
                }}
              >
                <XCircle size={13} />
              </button>
            </div>
            <div style={{ fontSize: 11, color: C.text2, lineHeight: 1.5 }}>
              {recentlyApplied.map((x, idx) => (
                <div key={x.triage_id} style={{
                  padding: '4px 0', borderTop: idx > 0 ? `1px solid rgba(34,197,94,0.15)` : 'none',
                }}>
                  <span style={{
                    fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                    color: x.action === 'auto_promote' ? C.green : C.candidate,
                    marginRight: 8,
                  }}>
                    {x.action === 'auto_promote' ? '+ NEW' : '↪ MERGED'}
                  </span>
                  <span style={{ color: C.text1 }}>{x.frame_name}</span>
                  {x.action === 'auto_merge' && x.candidate_frames_attached !== undefined && (
                    <span style={{ color: C.text3 }}>
                      {' '}· {x.candidate_frames_attached} extracts added
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 11, color: C.text3 }}>
              Audit on the <a href="/narratives" style={{ color: C.accent, textDecoration: 'none' }}>
                Narratives page →
              </a> if anything looks wrong (delete a frame to undo a promotion).
            </div>
          </div>
        )}

        {/* Phase D — Proposed-narrative section with AI triage badges.
            Each row is one HDBSCAN cluster of candidate frames the AI
            hasn't yet tracked. If the cluster has a triage verdict, it
            sorts to the top (auto_promote → auto_merge → human_review)
            and gets a colored badge + smarter primary button (pre-filled
            promote vs one-click merge). Auto-reject rows collapse into
            a footer toggle. */}
        {proposed && decoratedProposals.length > 0 && (
          <div style={{
            marginBottom: 24,
            background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 10,
            overflow: 'hidden',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '12px 16px',
              borderBottom: proposedExpanded ? `1px solid ${C.border}` : 'none',
            }}>
              <button
                onClick={() => setProposedExpanded(e => !e)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: 'transparent', border: 'none',
                  color: C.text1, cursor: 'pointer', textAlign: 'left',
                  padding: 0,
                }}
              >
                {proposedExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '-0.01em', display: 'inline-flex', alignItems: 'center' }}>
                  Potentially emerging narratives
                </span>
                <span style={{
                  fontSize: 10, color: C.accent, background: `${C.accent}22`,
                  border: `1px solid ${C.accent}55`, borderRadius: 4,
                  padding: '2px 7px', letterSpacing: '0.05em', fontWeight: 700,
                }} title={`${readyProposals.length} ready · ${watchProposals.length} watch`}>
                  {readyProposals.length}
                  {watchProposals.length > 0 && (
                    <span style={{ opacity: 0.55 }}> + {watchProposals.length}</span>
                  )}
                </span>
              </button>
              <span style={{ flex: 1 }} />
              {/* LLM-cost controls — refresh proposals + run AI triage
                  (~$0.40 per pass). Hidden for non-admin users; backend
                  also returns 403 if they invoke these endpoints directly. */}
              {isAdmin && (
                <>
                  {snapshotRefreshResult && (
                    <span style={{ fontSize: 10, color: C.text3, marginRight: 6 }}>
                      {snapshotRefreshResult}
                    </span>
                  )}
                  <button
                    onClick={handleRefreshSnapshot}
                    disabled={refreshingSnapshot}
                    title="Re-scan recent articles for new proposals. Existing clusters stay on the list until you act on them — this only adds new ones."
                    style={{
                      padding: '5px 10px', fontSize: 11, fontWeight: 600,
                      background: refreshingSnapshot ? C.bg3 : 'transparent',
                      color: refreshingSnapshot ? C.text3 : C.text2,
                      border: `1px solid ${C.border}`,
                      borderRadius: 5, cursor: refreshingSnapshot ? 'wait' : 'pointer',
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                      marginRight: 6,
                    }}
                  >
                    <RefreshCw size={11} className={refreshingSnapshot ? 'animate-spin' : ''} />
                    {refreshingSnapshot ? 'Refreshing…' : 'Refresh proposals'}
                  </button>
                  {triageLastResult && (
                    <span style={{ fontSize: 10, color: C.text3, marginRight: 8 }}>
                      {triageLastResult}
                    </span>
                  )}
                  <button
                    onClick={runTriagePass}
                    disabled={triageRunning}
                    title="Run gpt-4o triage to score every proposal (merge / promote / noise / uncertain). Costs ~$0.40 per pass."
                    style={{
                      padding: '5px 10px', fontSize: 11, fontWeight: 600,
                      background: triageRunning ? C.bg3 : `${C.accent}22`,
                      color: triageRunning ? C.text3 : C.accent,
                      border: `1px solid ${triageRunning ? C.border : `${C.accent}66`}`,
                      borderRadius: 5, cursor: triageRunning ? 'wait' : 'pointer',
                      display: 'inline-flex', alignItems: 'center', gap: 5,
                    }}
                  >
                    {triageRunning ? <RefreshCw size={11} className="animate-spin" /> : <Sparkles size={11} />}
                    {triageRunning ? 'Triaging…' : 'Run AI triage'}
                  </button>
                </>
              )}
            </div>
            {proposedExpanded && (
              <div>
                {readyProposals.length > 0 && (
                  <TierHeader
                    label="Ready to promote"
                    count={readyProposals.length}
                    tone="ready"
                    tooltip="These clusters meet the promotion bar (≥ 3 articles from ≥ 2 outlets, non-generic name, deduped by Theo). They also appear in the Theo-noticed banner on the Narratives page — promoting from either spot has the same effect."
                  />
                )}
                {readyProposals.map(renderClusterRow)}
                {watchProposals.length > 0 && (
                  <TierHeader
                    label="Watch list"
                    count={watchProposals.length}
                    tone="watch"
                    tooltip="Theo is seeing a pattern but it hasn't crossed the promotion bar yet — usually because it's only on one outlet, or the cluster is small. They stay visible so you can override and promote early if the signal looks real, but they don't show up in the Narratives banner."
                  />
                )}
                {watchProposals.map(renderClusterRow)}
                {/* Auto-rejected (noise) footer */}
                {autoRejectedProposals.length > 0 && (
                  <div style={{
                    borderTop: `1px solid ${C.border}`,
                    background: C.bg1,
                  }}>
                    <button
                      onClick={() => setShowAutoRejected(s => !s)}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                        padding: '10px 16px', background: 'transparent',
                        border: 'none', color: C.text3, cursor: 'pointer',
                        textAlign: 'left', fontSize: 11,
                      }}
                    >
                      {showAutoRejected ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                      AI flagged {autoRejectedProposals.length} cluster{autoRejectedProposals.length === 1 ? '' : 's'} as noise (low size + single outlet)
                    </button>
                    {showAutoRejected && autoRejectedProposals.map(({ cluster: c, verdict }) => (
                      <div key={`noise-${c.cluster_id}`} style={{
                        padding: '8px 16px 8px 36px',
                        fontSize: 11, color: C.text3,
                        borderTop: `1px solid ${C.border}`,
                        display: 'flex', alignItems: 'center', gap: 10,
                      }}>
                        <span style={{ flex: 1 }}>
                          <span style={{ color: C.text2 }}>{c.representative_name}</span>
                          {' · '}{c.size}f / {c.outlet_count}o
                          {verdict?.reasoning && <span> · {verdict.reasoning}</span>}
                        </span>
                        {isAdmin && (
                          <button
                            onClick={() => setPromoteTarget({ cluster: c, verdict })}
                            style={{
                              padding: '3px 8px', fontSize: 10, fontWeight: 600,
                              background: 'transparent', color: C.text2,
                              border: `1px solid ${C.border}`,
                              borderRadius: 4, cursor: 'pointer',
                            }}
                          >
                            Override + promote
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Empty state for the narratives tab when nothing's pending */}
        {proposed && decoratedProposals.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 20px', color: C.text3 }}>
            <Sparkles size={52} style={{ margin: '0 auto 20px', color: C.accent, opacity: 0.4 }} />
            <div style={{ fontSize: 24, fontWeight: 700, color: C.text2, marginBottom: 8 }}>
              No proposed narratives
            </div>
            <div style={{ fontSize: 13 }}>The AI hasn't surfaced any new clusters worth your attention yet.</div>
          </div>
        )}
        </>
        )}

        {/* ── Articles tab ───────────────────────────────────────────── */}
        {activeTab === 'articles' && (
        <>
        {loading && Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 110, marginBottom: 10 }} />
        ))}

        {!loading && visibleItems.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 20px', color: C.text3 }}>
            <CheckCircle size={52} style={{ margin: '0 auto 20px', color: C.green, opacity: 0.4 }} />
            <div style={{ fontSize: 24, fontWeight: 700, color: C.text2, marginBottom: 8 }}>
              Queue Clear
            </div>
            <div style={{ fontSize: 13 }}>All items have been reviewed or dismissed.</div>
          </div>
        )}

        {visibleItems.map(item => {
          // Non-admins don't see the bucket label, so they also don't get
          // the red-border emphasis that derives from it.
          const isCritical = isAdmin && item.race_relevance_label === 'critical'
          const isProcessing = processing.has(item.id)
          const isSelected = selected.has(item.id)

          return (
            <div
              key={item.id}
              style={{
                marginBottom: 8,
                background: isSelected ? C.bg3 : C.bg2,
                border: `1px solid ${isSelected ? C.borderBright : isCritical ? 'rgba(215,25,19,0.35)' : C.border}`,
                borderLeft: `3px solid ${isCritical ? C.opponent : isSelected ? C.accent : C.border}`,
                borderRadius: '0.625rem', overflow: 'hidden',
                opacity: isProcessing ? 0.5 : 1,
                transition: 'all 0.15s ease',
              }}
            >
              <div style={{ padding: '14px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  {/* Checkbox */}
                  <button
                    onClick={() => toggleSelect(item.id)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginTop: 2, color: C.text3 }}
                  >
                    {isSelected
                      ? <CheckSquare size={16} style={{ color: C.accent }} />
                      : <Square size={16} />
                    }
                  </button>

                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', gap: 7, marginBottom: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      {isAdmin && <RelBadge label={item.race_relevance_label} />}
                      {item.actionability_label && (
                        <span style={{
                          fontSize: 10, color: C.text2, border: `1px solid ${C.border}`,
                          padding: '2px 6px', borderRadius: 4, letterSpacing: '0.06em',
                        }}>
                          {item.actionability_label.toUpperCase()}
                        </span>
                      )}
                      {item.source_type && (
                        <span style={{ fontSize: 10, color: C.text3, letterSpacing: '0.06em' }}>
                          {item.source_type.toUpperCase()}
                        </span>
                      )}
                      <SentimentDot s={item.sentiment} />
                    </div>

                    <div style={{ fontSize: 14, fontWeight: 500, color: C.text1, lineHeight: 1.35, marginBottom: 6 }}>
                      {item.title}
                    </div>

                    {item.summary && (
                      <div style={{
                        fontSize: 13, color: C.text2, lineHeight: 1.5, marginBottom: 8,
                        overflow: 'hidden', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                      } as CSSProperties}>
                        {item.summary}
                      </div>
                    )}

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, color: C.text3 }}>
                      {item.source_name && <span>{cleanSourceName(item.source_name)}</span>}
                      {(item.published_at ?? item.created_at) && (
                        <span>{formatArticleDate(item.published_at ?? item.created_at)}</span>
                      )}
                      {item.opponent_attack_count > 0 && (
                        <span style={{ color: '#f87171' }}>
                          {item.opponent_attack_count} opp. attack{item.opponent_attack_count > 1 ? 's' : ''}
                        </span>
                      )}
                      {item.source_url && (
                        <a href={item.source_url} target="_blank" rel="noopener noreferrer"
                          style={{ color: C.accent, textDecoration: 'none' }}
                          onClick={e => e.stopPropagation()}>
                          Source ↗
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Action buttons — Keep / Dismiss. Previously had a third
                      "Mark relevant" (star) button, but it set the same DB
                      fields as Reviewed for items already in the queue. The
                      learning loop doesn't yet differentiate "strong keep"
                      from "keep", so the extra button just confused. */}
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button
                      title="Keep — mark this article as reviewed and remove it from the queue"
                      onClick={() => doAction(item.id, () => api.reviewItem(item.id))}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid ${C.border}`, borderRadius: 6, padding: '5px 10px', cursor: 'pointer', color: C.text2, fontSize: 11, fontWeight: 600 }}
                    >
                      Keep
                    </button>
                    <button
                      title="Dismiss — discard this article and remove it from the queue"
                      onClick={() => doAction(item.id, () => api.dismissItem(item.id))}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid rgba(215,25,19,0.35)`, borderRadius: 6, padding: '5px 10px', cursor: 'pointer', color: C.opponent, fontSize: 11, fontWeight: 600 }}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
                {actionErrors.has(item.id) && (
                  <div role="alert" style={{
                    marginTop: 10, padding: '6px 10px',
                    background: 'rgba(220,38,38,0.12)',
                    border: '1px solid rgba(220,38,38,0.4)',
                    borderRadius: 4,
                    fontSize: 11, color: '#fca5a5', lineHeight: 1.4,
                  }}>
                    {actionErrors.get(item.id)}
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {/* ── Recently filtered (safety view) ─────────────────────────
            Items the keyword relevance gate kicked out of the main queue.
            Collapsed by default; loads lazily on expand so we don't pay
            for the extra fetch when the user doesn't open it. Surfaces
            the spot-check the user can use to verify the gate isn't
            being too aggressive — and the items remain promotable from
            here (Keep / Dismiss work the same as in the main list). */}
        {!loading && (
          <div style={{
            marginTop: 24, background: C.bg2,
            border: `1px solid ${C.border}`, borderRadius: 10,
            overflow: 'hidden',
          }}>
            <button
              onClick={toggleFilteredOut}
              style={{
                width: '100%', padding: '12px 16px',
                display: 'flex', alignItems: 'center', gap: 8,
                background: 'transparent', border: 'none',
                color: C.text2, cursor: 'pointer', textAlign: 'left',
                fontSize: 12, fontWeight: 600,
              }}
            >
              {filteredOutExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span>Recently filtered</span>
              <InfoTooltip
                text="Items the keyword relevance gate excluded from the main queue. The gate keeps the queue focused on items that mention a candidate, opponent, district, federal/election term, or one of your priority issues. Browse here to spot-check what's being held back — if something important is here, dismiss it (it'll stay out) or keep it (counts as feedback for future tuning)."
              />
              {filteredOut !== null && (
                <span style={{
                  fontSize: 10, color: C.text3,
                  background: C.bg3, border: `1px solid ${C.border}`,
                  borderRadius: 4, padding: '1px 6px', fontWeight: 700,
                }}>
                  {filteredOut.length}
                </span>
              )}
              <span style={{ flex: 1 }} />
              {filteredOutLoading && (
                <RefreshCw size={11} className="animate-spin" style={{ color: C.text3 }} />
              )}
            </button>
            {filteredOutExpanded && filteredOut && filteredOut.length === 0 && (
              <div style={{
                padding: '20px 16px', borderTop: `1px solid ${C.border}`,
                color: C.text3, fontSize: 12, textAlign: 'center',
              }}>
                Nothing filtered out right now — the gate didn't block any candidates.
              </div>
            )}
            {filteredOutExpanded && filteredOut && filteredOut.map(item => {
              const isProcessing = processing.has(item.id)
              return (
                <div key={`filtered-${item.id}`} style={{
                  padding: '10px 16px',
                  borderTop: `1px solid ${C.border}`,
                  display: 'flex', alignItems: 'flex-start', gap: 10,
                  opacity: isProcessing ? 0.5 : 1,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', gap: 7, marginBottom: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                      {isAdmin && <RelBadge label={item.race_relevance_label} />}
                      {item.source_name && (
                        <span style={{ fontSize: 10, color: C.text3 }}>{cleanSourceName(item.source_name)}</span>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: C.text2, lineHeight: 1.4 }}>
                      {item.title}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button
                      title="Keep — pull this back into the main triage flow"
                      onClick={() => {
                        doAction(item.id, () => api.reviewItem(item.id))
                        setFilteredOut(fo => fo ? fo.filter(i => i.id !== item.id) : fo)
                      }}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid ${C.border}`, borderRadius: 6, padding: '4px 10px', cursor: 'pointer', color: C.text2, fontSize: 10, fontWeight: 600 }}
                    >
                      Keep
                    </button>
                    <button
                      title="Dismiss — confirm this isn't relevant"
                      onClick={() => {
                        doAction(item.id, () => api.dismissItem(item.id))
                        setFilteredOut(fo => fo ? fo.filter(i => i.id !== item.id) : fo)
                      }}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid rgba(215,25,19,0.35)`, borderRadius: 6, padding: '4px 10px', cursor: 'pointer', color: C.opponent, fontSize: 10, fontWeight: 600 }}
                    >
                      Dismiss
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
        </>
        )}

      </div>

      {/* Phase D — Promote modal. Opens with the cluster + (optional)
          triage verdict for pre-fill. After a successful promote we
          refetch both proposals AND verdicts (the promoted cluster
          disappears from proposals; its verdict gets stamped applied). */}
      {promoteTarget && (
        <PromoteModal
          cluster={promoteTarget.cluster}
          members={membersByCluster.get(promoteTarget.cluster.cluster_id) || []}
          defaultOwner={promoteTarget.cluster.owner_type_hint as OwnerType}
          outletTiers={{
            national: promoteTarget.cluster.outlet_tier_counts.national,
            regional: promoteTarget.cluster.outlet_tier_counts.regional,
            local: promoteTarget.cluster.outlet_tier_counts.local,
            blog: promoteTarget.cluster.outlet_tier_counts.blog,
            social: promoteTarget.cluster.outlet_tier_counts.social,
          }}
          prefilledName={promoteTarget.verdict?.suggested_name}
          prefilledDescription={promoteTarget.verdict?.suggested_description}
          prefilledOwner={promoteTarget.verdict?.suggested_owner_type as OwnerType | null | undefined}
          candidateName={candidateName}
          opponentName={opponentName}
          aiBadge={promoteTarget.verdict ? {
            text: `AI: ${VERDICT_META[promoteTarget.verdict.verdict].label}`
                  + (promoteTarget.verdict.confidence > 0
                      ? ` · ${Math.round(promoteTarget.verdict.confidence * 100)}% confidence`
                      : ''),
            tone: VERDICT_META[promoteTarget.verdict.verdict].tone,
          } : undefined}
          onClose={() => setPromoteTarget(null)}
          onPromoted={() => {
            const triageId = promoteTarget.verdict?.id
            setPromoteTarget(null)
            // Stamp the verdict as applied (audit trail) + refresh both lists.
            if (triageId !== undefined) {
              api.applyTriageVerdict(triageId).catch(() => {})
            }
            Promise.all([
              api.narrativeProposalsSnapshot(),
              api.narrativeTriageVerdicts(),
            ]).then(([p, v]) => {
              setProposed(p); setVerdicts(v)
            }).catch(() => {})
          }}
        />
      )}
    </div>
  )
}
