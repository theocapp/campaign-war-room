/**
 * Narrative Landscape — established-narrative ecosystem map.
 *
 * As of V13.10 this page is established-only. Proposed-cluster review
 * lives on the Review Queue page (`/review`). The Landscape stays
 * focused on a single question: "where do the narratives we already
 * track sit in topic space, and which ones are the strongest?"
 *
 * Three nested layers in the chart:
 *   - Topics (auto-labeled by HDBSCAN over frame centroids + LLM naming)
 *   - Narratives (the tracked frames)
 *   - Article-extract dots (one per LLM-extracted quote)
 *
 * Sidebar mirrors that hierarchy as a file-tree. Click a row to drill
 * in (zoom the chart + expand the children); click again to drill back.
 * Topic positions reflect TOPIC similarity, not subject — the backend
 * strips candidate/opponent names before embedding so e.g. Cognetti's
 * healthcare and Bresnahan's healthcare sit next to each other.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  forceCollide, forceLink, forceSimulation, forceX, forceY,
} from 'd3-force'
import { api } from '@/api/client'
import { quadrantColor, quadrantNamedLabel } from '@/lib/quadrantColor'
import { formatArticleDate } from '@/lib/formatDate'
import type {
  DotLandscape, EstablishedLandscape,
  ExtractDot, NarrativeGroupInfo, OwnerType, TopicGroupInfo,
} from '@/api/types'

/** Title-cased surname from "First Last" or FEC "LAST, FIRST" format. */
function lastName(raw?: string): string {
  if (!raw) return ''
  const t = raw.trim()
  const last = (t.includes(',') ? t.split(',')[0] : t.split(/\s+/).pop() || '').trim()
  return last ? last[0].toUpperCase() + last.slice(1).toLowerCase() : ''
}

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)', bg4: 'var(--bg-4)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)',
  // V13.19 — 4-quadrant palette. Cool family (blue + cyan) = our side
  // (narratives that benefit the candidate). Warm family (red + orange)
  // = their side. The "outer" colors (cyan, orange) signal OFFENSE
  // (narrative ABOUT the opposing party), while the "primary" colors
  // (blue, red) signal DEFENSE (narrative ABOUT one's own party). At a
  // glance the chart still reads candidate-vs-opponent by warmth; the
  // intra-team distinction reveals "are we attacking or defending?"
  our_defense: '#0059c2',   // = candidate (kept identical to old blue)
  our_offense: '#06b6d4',   // cyan: our attacks on opponent
  their_defense: '#d71913', // = opponent (kept identical to old red)
  their_offense: '#f97316', // orange: their attacks on us
  tier_national: '#22c55e', tier_regional: '#3b82f6',
  tier_local: '#a78bfa', tier_blog: '#f59e0b', tier_social: '#ef4444',
}

// 4-quadrant key matching backend's subject_classifier.quadrant_key:
//   our_defense   = owner=candidate × subject=candidate  (blue)
//   our_offense   = owner=candidate × subject=opponent   (cyan)
//   their_defense = owner=opponent  × subject=opponent   (red)
//   their_offense = owner=opponent  × subject=candidate  (orange)
//   media         = owner=media OR subject=media         (gray)
type QuadrantKey = 'our_defense' | 'our_offense' | 'their_defense' | 'their_offense' | 'media'

function quadrantKey(owner: OwnerType, subject: OwnerType | undefined): QuadrantKey {
  if (owner === 'media' || !subject || subject === 'media') return 'media'
  if (owner === 'candidate' && subject === 'candidate') return 'our_defense'
  if (owner === 'candidate' && subject === 'opponent') return 'our_offense'
  if (owner === 'opponent' && subject === 'opponent') return 'their_defense'
  if (owner === 'opponent' && subject === 'candidate') return 'their_offense'
  return 'media'
}

// quadrantColor now imported from @/lib/quadrantColor (canonical source).

// Old 3-color owner palette retained for any UI that still wants the
// pure beneficiary signal (sidebar tier bars, ownership filter chips, etc.).
// The chart itself uses quadrantColor() per V13.19.
function ownerColor(t: OwnerType): string {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

type OwnerFilter = 'all' | OwnerType

// Padding around the layout's actual usable area inside the SVG. Bubbles
// can't drift closer to the edge than this — keeps labels from clipping.
const CHART_PAD = 80

// V13.15 — fixed-dimension layout. The d3 force simulation runs in this
// logical coordinate space (NOT the container's pixel size). The SVG's
// viewBox then scales the rendered output to fit whatever container the
// chart actually lives in.
//
// Why: before this constant, the sim ran on `chartSize.w/h` from a
// ResizeObserver, so different browser widths produced different overlap
// patterns — circles merging on one screen wouldn't merge on another.
// Pinning the sim to a constant size makes the topology identical across
// devices; only the visual scale differs. Aspect ratio is preserved via
// preserveAspectRatio="xMidYMid meet" so circles don't stretch.
//
// 16:9 ratio chosen because the dashboard's main viewport is typically
// widescreen; tweak if the chart usually lives in a non-widescreen
// container.
const LAYOUT_WIDTH = 1600
const LAYOUT_HEIGHT = 900

// ── Smart label fitting (unchanged from V9) ────────────────────────────────


// e.g. a 3-vs-2 topic used to look identical to a 5-vs-0 topic.
function topicColorWithDominance(
  owner_mix: { candidate: number; opponent: number; media: number },
): { color: string; dominance: number; leader: 'candidate' | 'opponent' | 'media' } {
  const cand = owner_mix.candidate || 0
  const opp = owner_mix.opponent || 0
  const med = owner_mix.media || 0
  const total = cand + opp + med
  if (total === 0) return { color: C.media, dominance: 0, leader: 'media' }

  // "Lead" of candidate vs opponent (media doesn't count as a side).
  // Range: 0 (tie or all-media) → 1 (single-side total).
  const partisanTotal = cand + opp
  const lead = partisanTotal === 0 ? 0 : Math.abs(cand - opp) / partisanTotal

  let leader: 'candidate' | 'opponent' | 'media' = 'media'
  let color = C.media
  if (cand > opp) { leader = 'candidate'; color = C.candidate }
  else if (opp > cand) { leader = 'opponent'; color = C.opponent }
  // True ties OR all-media topics: leader stays 'media', color stays gray.

  return { color, dominance: lead, leader }
}

// V13.19 — 4-quadrant topic color. Picks the dominant quadrant
// (our_defense/our_offense/their_defense/their_offense/media) and
// returns its color, with a dominance value 0..1 indicating how far
// the leader is from a true tie. Used by topic rings on the chart.
//
// Quadrant_mix is the authority-weighted contribution sum per quadrant,
// produced by backend's compute() — same numeric scale as owner_mix.
function topicQuadrantColor(
  quadrant_mix: import('@/api/types').QuadrantMix | undefined | null,
): { color: string; dominance: number; leader: QuadrantKey } {
  const q = quadrant_mix || { our_defense: 0, our_offense: 0, their_defense: 0, their_offense: 0, media: 0 }
  const buckets: Array<{ key: QuadrantKey; v: number }> = [
    { key: 'our_defense',   v: q.our_defense   || 0 },
    { key: 'our_offense',   v: q.our_offense   || 0 },
    { key: 'their_defense', v: q.their_defense || 0 },
    { key: 'their_offense', v: q.their_offense || 0 },
    { key: 'media',         v: q.media         || 0 },
  ]
  const total = buckets.reduce((s, b) => s + b.v, 0)
  if (total === 0) return { color: C.media, dominance: 0, leader: 'media' }
  buckets.sort((a, b) => b.v - a.v)
  const leader = buckets[0].key
  // Dominance: leader share minus runner-up share, normalized to [0, 1].
  // 1.0 = leader has all volume; 0.0 = leader tied with runner-up.
  const lead = (buckets[0].v - buckets[1].v) / total
  const colorMap: Record<QuadrantKey, string> = {
    our_defense: C.our_defense,
    our_offense: C.our_offense,
    their_defense: C.their_defense,
    their_offense: C.their_offense,
    media: C.media,
  }
  return { color: colorMap[leader], dominance: Math.max(0, Math.min(1, lead)), leader }
}


// Topic region as the chart/sidebar consume it. Backend's TopicRegion only
// has member_frame_ids; we used to also resolve those to Bubble objects for
// the BubbleChart, but post-V13.10 the chart reads narratives/dots directly
// from DotLandscape so member_bubbles is no longer needed.
interface TopicRegionForChart {
  region_id: number
  persisted_id: number | null
  label: string
  edited_by_user: boolean
  member_frame_ids: number[]
  owner_mix: { candidate: number; opponent: number; media: number }
}

// ── DotChart (V12 — dot-cluster rendering for established mode) ───────────
//
// Atomic unit = one article extract dot. Narrative + topic groupings
// overlay as nested convex hulls with floating labels. The bubble layer
// is gone in this view; the grouping is shown by proximity + soft hulls.

interface DotChartProps {
  dots: ExtractDot[]
  narratives: NarrativeGroupInfo[]
  topics: TopicGroupInfo[]
  width: number
  height: number
  selectedRegionId: number | null
  focusedFrameId: number | null            // selected narrative (for highlighting + camera)
  hoveredDotId: number | null
  hoveredRegionId: number | null
  hoveredNarrativeId: number | null
  onHoverDot: (id: number | null) => void
  onHoverRegion: (id: number | null) => void
  onHoverNarrative: (id: number | null) => void
  onClickDot: (id: number) => void
  onClickRegion: (id: number) => void
  onClickNarrative: (id: number) => void
  onClickBackground: () => void
}

// Linear rescale of UMAP coords into the padded chart rect. Per-axis
// (independent x and y scale) so the dot cloud fills the chart — UMAP
// preserves nearest-neighbor topology, not absolute distances, so a bit
// of stretching is fine and prevents wasted whitespace.
// ── Hierarchical layout ────────────────────────────────────────────────
//
// Replaces the V12 single-pass UMAP rescale that let topics/narratives
// freely overlap. New approach: three nested force-relaxations so
// nothing overlaps catastrophically, but each level still uses the raw
// UMAP coords to pick the SIDE of the parent each child sits on (so
// topically-adjacent groups stay near each other).
//
//   1. Topic level: rescale topic centroids into chart space, then
//      d3-force collide based on topic radius. Topics push apart.
//   2. Narrative level (per topic): center narrative at its UMAP offset
//      from topic centroid, then collide based on narrative radius.
//      Narratives in the same topic push apart but stay inside their
//      parent.
//   2b. Noise narratives (V13.15): each placed at its OWN global UMAP
//       position rescaled into chart coords (same coord frame as
//       topics), force-collided against topic obstacles + each other.
//       NOT corralled into a fake centroid like prior versions.
//   3. Dot level: dots use their UMAP offset from narrative centroid,
//      scaled to fit inside the narrative's circle. No collide — dots
//      naturally cluster by similarity inside their narrative.
//
// Radii are derived from member counts (sqrt scaling so a 100-dot
// narrative isn't 10× bigger visually than a 10-dot one).
//
// ── What this layout preserves (and what it distorts) ─────────────────
// The chart is a meaningful semantic map, but with these specific
// trade-offs you should know:
//
//   Preserved:
//   - Topic-to-topic relative positions: topics that are semantically
//     close in UMAP space appear close on screen (within force-collide
//     spacing margins).
//   - Narrative-to-narrative WITHIN A TOPIC: positions reflect the
//     narratives' UMAP offsets from the topic centroid.
//   - Dot-to-dot WITHIN A NARRATIVE: dots cluster by article similarity
//     inside the narrative bubble (no collision applied at this level).
//   - Noise frame positions: each noise narrative sits at its true
//     global UMAP position; two noise frames that are far apart in
//     UMAP appear far apart on screen.
//
//   Distorted (intentional, in service of readability):
//   - Cross-topic narrative similarity: a narrative in Topic A that's
//     semantically very close to a narrative in Topic B will NOT appear
//     close on screen — both are placed inside their respective topic
//     bubbles. The hierarchy wins over cross-topic UMAP topology.
//   - Exact pairwise distances: force-collide pushes overlapping circles
//     apart, so the distance between two adjacent topics is at least
//     the sum of their radii — not the actual UMAP distance.
//
// If a circle on the chart appears CLOSE to another circle, that's a
// meaningful similarity signal. If two narratives sit in different
// topic bubbles, look at the topic-level positions to understand their
// cross-topic relationship; the narrative-bubble level only tells you
// the WITHIN-topic ordering.

function computeHierarchicalLayout(
  dots: ExtractDot[],
  narratives: NarrativeGroupInfo[],
  topics: TopicGroupInfo[],
  width: number, height: number, pad: number,
): {
  dotPositions: Map<number, { x: number; y: number }>;
  narrativeCenters: Map<number, { x: number; y: number; r: number }>;
  topicCenters: Map<number, { x: number; y: number; r: number }>;
} {
  if (dots.length === 0) {
    return { dotPositions: new Map(), narrativeCenters: new Map(), topicCenters: new Map() }
  }

  // ── frame_id → topic_region_id (or -1 if frame is ungrouped) ──────────
  const frameToTopic = new Map<number, number>()
  for (const t of topics) {
    for (const fid of t.member_frame_ids) frameToTopic.set(fid, t.region_id)
  }

  // ── Group dots by topic and narrative ─────────────────────────────────
  const dotsByTopic = new Map<number, ExtractDot[]>()  // -1 = ungrouped
  const dotsByNarrative = new Map<number, ExtractDot[]>()
  for (const d of dots) {
    const tid = frameToTopic.get(d.frame_id) ?? -1
    if (!dotsByTopic.has(tid)) dotsByTopic.set(tid, [])
    dotsByTopic.get(tid)!.push(d)
    if (!dotsByNarrative.has(d.frame_id)) dotsByNarrative.set(d.frame_id, [])
    dotsByNarrative.get(d.frame_id)!.push(d)
  }

  // Helper: UMAP centroid (raw coords, before any chart rescaling)
  const centroid = (arr: ExtractDot[]) => {
    let sx = 0, sy = 0
    for (const d of arr) { sx += d.x; sy += d.y }
    return { x: sx / arr.length, y: sy / arr.length }
  }

  // V13.17 — declared early so Step 1 can write noise-narrative positions
  // directly when noise frames are folded into the topic-level simulation.
  // Step 2 (per-topic narrative layout) writes its results to the same map.
  const narrativeCenters = new Map<number, { x: number; y: number; r: number }>()

  // ── Step 1: topic-level layout ────────────────────────────────────────
  // Each topic gets a radius from sqrt(dot count) × ~13 px. Smallest
  // topics ≈ 60 px, biggest ≈ 220 px on typical data. Add 30 px padding
  // around each so adjacent topics breathe.
  //
  // V13.9 — "no topics" mode (proposed view): we still build the topic
  // layer because narratives need a parent rectangle to live in, but
  // there's only ONE virtual topic that fills the entire chart. Skip
  // the topic sim entirely in that case; let narratives spread freely
  // across the full chart in step 2.
  type TopicNode = {
    tid: number; rawX: number; rawY: number;
    targetX: number; targetY: number; r: number;
    x: number; y: number;
  }
  const noTopics = topics.length === 0
  const topicNodes: TopicNode[] = []
  // V13.15 — EXCLUDE noise (tid=-1) from the topic-level layout. Previously
  // noise frames were collected into a "ghost topic" centered on their
  // UMAP centroid, which gave the visual impression that unrelated noise
  // frames were a coherent cluster. Now noise frames get their own pass
  // (step 2b below) that places each one at its REAL global UMAP
  // position, with collision against real topic circles only.
  for (const [tid, dotList] of dotsByTopic) {
    if (tid === -1) continue
    const c = centroid(dotList)
    const r = noTopics
      // Single virtual topic spans the full chart — narratives use the
      // whole area instead of being cramped into a sqrt-sized circle.
      ? Math.min(width, height) / 2 - pad
      : Math.max(60, Math.sqrt(dotList.length) * 13)
    topicNodes.push({ tid, rawX: c.x, rawY: c.y, r, targetX: 0, targetY: 0, x: 0, y: 0 })
  }

  // Hoisted out of the if/else below so the noise-narrative pass (step 2b)
  // can reuse the same UMAP-to-chart rescale that positions real topics.
  // Without sharing the rescale, noise narratives would land in a
  // different coordinate frame and the chart would lie about their
  // semantic distance from the real topics.
  let txMin = 0, tyMin = 0, txSpan = 1, tySpan = 1
  let innerW = width - 2 * pad
  let innerH = height - 2 * pad

  if (noTopics) {
    // Park the single virtual topic at chart center, skip topic sim.
    for (const n of topicNodes) {
      n.targetX = width / 2
      n.targetY = height / 2
      n.x = n.targetX; n.y = n.targetY
    }
  } else if (topicNodes.length > 0) {
    // Rescale topic UMAP centroids into the chart's usable area.
    // Per-axis scale so the topics fill the chart shape.
    // V13.15 — the rescale params use ALL frames' UMAP positions (topics
    // + noise) for the bounds so noise frames in step 2b sit in the same
    // coordinate frame as topics. Without this, noise narratives could
    // land outside the chart bounds or be artificially compressed.
    const allRawXs: number[] = []
    const allRawYs: number[] = []
    for (const [, dotList] of dotsByTopic) {
      for (const d of dotList) { allRawXs.push(d.x); allRawYs.push(d.y) }
    }
    txMin = Math.min(...allRawXs)
    const txMax = Math.max(...allRawXs)
    tyMin = Math.min(...allRawYs)
    const tyMax = Math.max(...allRawYs)
    txSpan = Math.max(txMax - txMin, 1e-6)
    tySpan = Math.max(tyMax - tyMin, 1e-6)
    for (const n of topicNodes) {
      n.targetX = (n.rawX - txMin) / txSpan * innerW + pad
      n.targetY = (n.rawY - tyMin) / tySpan * innerH + pad
      n.x = n.targetX; n.y = n.targetY
    }

    // V13.16 — preserve UMAP semantic distances at chart-render time.
    //
    // PRIOR BEHAVIOR (V13.12.4 and earlier): the topic sim used
    // `forceCollide(d => d.r + 6).strength(1)` which strictly prevented
    // overlap, pushing every pair of topics to AT LEAST r1+r2+12 px
    // apart regardless of how semantically similar they were. So
    // topics that SHOULD overlap (e.g. Healthcare Cuts ≈ Healthcare &
    // District Shift, UMAP distance 0.62 — among the closest pairs in
    // the data) got pushed to ~r1+r2 ≈ 200 px apart. The semantic
    // distance signal from UMAP was destroyed at chart-render time.
    //
    // KEY INSIGHT: forceX/Y already pulls topics toward their UMAP-
    // derived targets, which preserves the distance signal. The OLD
    // collide was the active distorter. Replacing it with a WEAK
    // safety-net collide (allowing ~50% overlap before pushing apart,
    // and at low strength so the UMAP targets win) lets semantically
    // close topics actually overlap on the chart. The mask-based
    // overlap clipping renders intersecting topic rings as one
    // continuous merged outline so partial overlap reads cleanly.
    // V13.17 — include NOISE narratives in the topic-level simulation as
    // smaller "satellite" nodes. Without this, noise narratives stay at
    // their pure UMAP-rescaled positions while topics get pushed around
    // by the sim, so a noise narrative that should sit NEXT to a
    // semantically-close topic ends up far away on the chart because
    // the topic moved but the noise didn't. By making noise nodes
    // first-class participants in the sim, they move together with the
    // topics they're closest to — preserving cross-cluster proximity.
    //
    // Satellite nodes get their UMAP-derived target, smaller radius,
    // and a special isNoise marker so we can extract their final
    // positions for narrativeCenters below. They collide-only against
    // topics (not against the same topic they're close to in UMAP —
    // we WANT them to overlap their parent semantic neighborhood).
    type SatelliteNode = TopicNode & { isNoise: true; fid: number }
    const satelliteNodes: SatelliteNode[] = []
    const noiseDotsForTopicSim = dotsByTopic.get(-1) || []
    if (noiseDotsForTopicSim.length > 0) {
      const noiseFrameIds = new Set(noiseDotsForTopicSim.map(d => d.frame_id))
      for (const fid of noiseFrameIds) {
        const memberDots = dotsByNarrative.get(fid) || []
        if (memberDots.length === 0) continue
        const c = centroid(memberDots)
        const tx = (c.x - txMin) / txSpan * innerW + pad
        const ty = (c.y - tyMin) / tySpan * innerH + pad
        // Narrative-sized radius (same as inside-topic narratives)
        const r = Math.max(18, Math.sqrt(memberDots.length) * 9)
        satelliteNodes.push({
          tid: -fid - 1000,   // unique synthetic tid so doesn't collide with real tids
          isNoise: true, fid,
          rawX: c.x, rawY: c.y,
          targetX: tx, targetY: ty,
          r,
          x: tx, y: ty,
        })
      }
    }

    const allTopicLevelNodes: (TopicNode | SatelliteNode)[] = [
      ...topicNodes, ...satelliteNodes,
    ]

    const topicSim = forceSimulation(allTopicLevelNodes as any)
      .force('x', forceX<TopicNode>(d => d.targetX).strength(0.35))
      .force('y', forceY<TopicNode>(d => d.targetY).strength(0.35))
      // Soft collide: allows up to ~50% overlap of topic-to-topic and
      // noise-to-topic. Lets semantically close pairs partially merge
      // while preventing chart-breaking total occlusion. The mask-based
      // overlap clipping renders intersecting rings as one continuous
      // merged outline so partial overlap reads cleanly.
      .force('collide', forceCollide<TopicNode>(d => d.r * 0.5).strength(0.5))
      .stop()
    for (let i = 0; i < 350; i++) {
      topicSim.tick()
      // Clamp inside chart bounds each tick.
      for (const n of allTopicLevelNodes) {
        n.x = Math.max(n.r + pad, Math.min(width - n.r - pad, n.x))
        n.y = Math.max(n.r + pad, Math.min(height - n.r - pad, n.y))
      }
    }
    // Extract satellite final positions into narrativeCenters here so
    // Step 2b can be skipped (noise placement is now done).
    for (const sat of satelliteNodes) {
      narrativeCenters.set(sat.fid, { x: sat.x, y: sat.y, r: sat.r })
    }
  }
  const topicCenters = new Map<number, { x: number; y: number; r: number }>()
  for (const n of topicNodes) topicCenters.set(n.tid, { x: n.x, y: n.y, r: n.r })

  // ── Step 2: narrative-level layout (one mini-sim per topic) ───────────
  // Each narrative inside a topic gets a small radius from sqrt(dot
  // count) × 7. Anchored at its UMAP offset from the topic centroid,
  // then collide so narratives within the same topic don't overlap.
  type NarrNode = {
    fid: number; tid: number;
    rawX: number; rawY: number;
    targetX: number; targetY: number; r: number;
    x: number; y: number;
  }
  // narrativeCenters declared above (line ~237). Noise narratives were
  // already placed during the topic-level sim (Step 1) as satellite
  // nodes — see V13.17 in that block.

  for (const [tid, topicDots] of dotsByTopic) {
    // V13.17 — noise frames (tid=-1) already had their positions computed
    // in Step 1 as satellite nodes inside the topic sim. Skip them here.
    if (tid === -1) continue
    const topicCenter = topicCenters.get(tid)!
    const topicUmap = centroid(topicDots)
    // Build narrative nodes for this topic
    const narrNodes: NarrNode[] = []
    const framesInTopic = new Set(topicDots.map(d => d.frame_id))
    for (const fid of framesInTopic) {
      const memberDots = dotsByNarrative.get(fid) || []
      if (memberDots.length === 0) continue
      const c = centroid(memberDots)
      // V13.5 — bigger narrative circles. The old `sqrt(n) * 7` left
      // topic interiors with visible blank space; bumping to ×9 fills
      // more of the parent without spilling out (the per-pair link
      // distance + topic clamp keeps them inside).
      // V13.12.5 — bigger narratives (sqrt × 7 → 9) so the bubbles +
      // their dots are easier to see. Dot packing at 0.92 still keeps
      // the bubble hugged tight to the cluster (no wasted padding).
      const r = Math.max(18, Math.sqrt(memberDots.length) * 9)
      // Offset = where this narrative sits relative to topic centroid in UMAP space.
      // Scale that offset by 0.5 × topic radius so narratives fit inside.
      const offsetX = (c.x - topicUmap.x)
      const offsetY = (c.y - topicUmap.y)
      // Normalize offsets within topic to fit topic's interior.
      narrNodes.push({
        fid, tid,
        rawX: offsetX, rawY: offsetY,
        targetX: topicCenter.x, targetY: topicCenter.y,  // adjusted below
        r,
        x: topicCenter.x, y: topicCenter.y,
      })
    }
    if (narrNodes.length === 0) continue

    // Rescale within-topic offsets into a circle of radius topicCenter.r * 0.65
    // (leaves edge room for narrative hulls to fit inside topic hull).
    const nxMin = Math.min(...narrNodes.map(n => n.rawX))
    const nxMax = Math.max(...narrNodes.map(n => n.rawX))
    const nyMin = Math.min(...narrNodes.map(n => n.rawY))
    const nyMax = Math.max(...narrNodes.map(n => n.rawY))
    const nxSpan = Math.max(nxMax - nxMin, 1e-6)
    const nySpan = Math.max(nyMax - nyMin, 1e-6)
    // V13.5 — push narratives closer to the topic edge so the topic
    // hull doesn't have a big empty ring around the cluster. Was 0.65,
    // now 0.80. The topic clamp below still keeps every narrative
    // entirely inside the topic boundary.
    // V13.12.3 — tighter narrative packing inside topics (0.80 → 0.90)
    // so narratives fill more of the now-smaller topic interior.
    const allowance = topicCenter.r * 0.90
    for (const n of narrNodes) {
      // Single-narrative topic: park at center
      if (narrNodes.length === 1) {
        n.targetX = topicCenter.x
        n.targetY = topicCenter.y
      } else {
        n.targetX = topicCenter.x + ((n.rawX - nxMin) / nxSpan - 0.5) * 2 * allowance
        n.targetY = topicCenter.y + ((n.rawY - nyMin) / nySpan - 0.5) * 2 * allowance
      }
      n.x = n.targetX; n.y = n.targetY
    }

    // V13.4 — every narrative pair gets a target distance based on UMAP
    // relatedness. The closer two narratives sit in UMAP space, the
    // smaller their target distance — so semantically-related
    // narratives get pulled into slight overlap, while unrelated ones
    // get pushed to clean separation.
    //
    // Why every-pair links (instead of forceCollide + targeted links):
    // forceCollide enforces a hard minimum distance, which beats any
    // link force trying to pull narratives closer than radius-sum.
    // Replacing collide with per-pair distance constraints gives one
    // unified force that handles BOTH separation AND overlap, with
    // a small fallback collide as a safety net against extreme overlap.
    type NarrLink = { source: NarrNode; target: NarrNode; targetD: number }
    const pairs: { i: number; j: number; rawDist: number }[] = []
    for (let i = 0; i < narrNodes.length; i++) {
      for (let j = i + 1; j < narrNodes.length; j++) {
        const a = narrNodes[i], b = narrNodes[j]
        const d = Math.hypot(a.rawX - b.rawX, a.rawY - b.rawY)
        pairs.push({ i, j, rawDist: d })
      }
    }
    let narrLinks: NarrLink[] = []
    if (pairs.length >= 1) {
      const sortedDist = pairs.map(p => p.rawDist).sort((a, b) => a - b)
      // Use median pairwise distance as the "neutral" point. Pairs
      // closer than the median overlap; pairs at the median touch;
      // pairs further apart sit cleanly separated.
      const median = sortedDist[Math.floor(sortedDist.length / 2)] || 1
      narrLinks = pairs.map(p => {
        const a = narrNodes[p.i], b = narrNodes[p.j]
        // Normalize UMAP distance against median. cappedRatio in [0..2]:
        //   0   = pair is identical in UMAP (max overlap)
        //   1   = pair is at the median (target = radius sum, just touching)
        //   2+  = pair is far above median (target = 1.4× radius sum, gap)
        const cappedRatio = Math.min(2, p.rawDist / median)
        // Factor: 0.75 (25% overlap) at closest, 1.0 (touching) at median,
        // up to 1.4 (40% gap) at 2× median. Smooth linear ramp.
        const factor = 0.75 + 0.325 * cappedRatio
        return { source: a, target: b, targetD: (a.r + b.r) * factor }
      })
    }

    // V13.9 — no-topics mode (proposed view): skip per-pair links and use
    // standard forceX/Y + hard collide. Per-pair links work great when
    // narratives share a small bounded topic, but with no topic
    // grouping they collapse every narrative onto the chart center
    // because pairwise target distances are all small relative to the
    // chart. Standard collide + UMAP-target gives the expected spread.
    const narrSim = noTopics
      ? forceSimulation(narrNodes as any)
          .force('x', forceX<NarrNode>(d => d.targetX).strength(0.4))
          .force('y', forceY<NarrNode>(d => d.targetY).strength(0.4))
          .force('collide', forceCollide<NarrNode>(d => d.r + 6).strength(1))
          .stop()
      : forceSimulation(narrNodes as any)
          .force('x', forceX<NarrNode>(d => d.targetX).strength(0.25))
          .force('y', forceY<NarrNode>(d => d.targetY).strength(0.25))
          // Weak fallback collide — only fires when narratives would
          // otherwise overlap by more than 40% (collide radius = 0.6×).
          // The per-pair link force handles normal positioning above this.
          .force('collide', forceCollide<NarrNode>(d => d.r * 0.6).strength(0.4))
          .force('link', forceLink<NarrNode, NarrLink>(narrLinks)
            .distance(l => l.targetD)
            .strength(0.7))
          .stop()
    for (let i = 0; i < 250; i++) {
      narrSim.tick()
      // Soft clamp: keep narratives inside topic's interior bound.
      // We allow slight overhang so collide can still resolve along the
      // edge — the topic hull padding (18 px) absorbs the overhang.
      for (const n of narrNodes) {
        const dx = n.x - topicCenter.x
        const dy = n.y - topicCenter.y
        const dist = Math.sqrt(dx * dx + dy * dy)
        const maxDist = topicCenter.r - n.r
        if (dist > maxDist && dist > 0) {
          n.x = topicCenter.x + dx / dist * maxDist
          n.y = topicCenter.y + dy / dist * maxDist
        }
      }
    }
    for (const n of narrNodes) {
      narrativeCenters.set(n.fid, { x: n.x, y: n.y, r: n.r })
    }
  }

  // (V13.17) Noise-narrative placement was here as Step 2b — now folded
  // into the topic-level sim as satellite nodes (see Step 1). Topics and
  // their semantically-close noise frames now move together so cross-
  // cluster proximity is preserved on the chart.

  // ── Step 3: dot positions ─────────────────────────────────────────────
  // Each dot sits at narrative_center + (dot_umap - narrative_centroid)
  // scaled to fit inside the narrative's radius. Within-narrative UMAP
  // topology preserved (similar dots cluster within the narrative).
  const dotPositions = new Map<number, { x: number; y: number }>()
  for (const [fid, narrDots] of dotsByNarrative) {
    const narrCenter = narrativeCenters.get(fid)
    if (!narrCenter) continue
    const nUmap = centroid(narrDots)
    // Find the max offset to scale within the narrative's radius
    let maxR = 0
    for (const d of narrDots) {
      const dx = d.x - nUmap.x, dy = d.y - nUmap.y
      maxR = Math.max(maxR, Math.sqrt(dx * dx + dy * dy))
    }
    // Pack dots so the furthest one sits ~85% of the way to the narrative
    // boundary — leaves margin for the narrative hull stroke.
    // V13.12.3 — tighter dot packing (0.85 → 0.92) — dots fill nearly
    // the entire narrative bubble, removing the empty padding ring.
    const scale = maxR > 0 ? (narrCenter.r * 0.92) / maxR : 0
    for (const d of narrDots) {
      dotPositions.set(d.id, {
        x: narrCenter.x + (d.x - nUmap.x) * scale,
        y: narrCenter.y + (d.y - nUmap.y) * scale,
      })
    }
  }

  return { dotPositions, narrativeCenters, topicCenters }
}

// Reusable badge-style label: dark rounded rect background + colored
// stroke + dynamically-measured width so the rect hugs the text. Used
// for both topic labels (visible at all zooms) and narrative labels
// (zoom-progressive opacity, passed via the `opacity` prop).
function BadgeLabel({
  cx, cy, text, fontSize, textColor, strokeColor, strokeOpacity, strokeScale,
  opacity = 1,
}: {
  cx: number
  cy: number
  text: string
  fontSize: number
  textColor: string
  strokeColor: string
  strokeOpacity: number
  strokeScale: number
  opacity?: number
}) {
  const textRef = useRef<SVGTextElement>(null)
  const [measuredWidth, setMeasuredWidth] = useState(text.length * fontSize * 0.54)
  useLayoutEffect(() => {
    if (!textRef.current) return
    try {
      const w = textRef.current.getBBox().width
      if (w > 0 && Math.abs(w - measuredWidth) > 0.5) setMeasuredWidth(w)
    } catch {
      // getBBox can throw on detached / hidden elements; safe to ignore.
    }
  })
  const PAD_X = 4
  return (
    <g
      transform={`translate(${cx}, ${cy})`}
      style={{ pointerEvents: 'none', opacity, transition: 'opacity 250ms' }}
    >
      <rect
        x={-measuredWidth / 2 - PAD_X}
        y={-fontSize * 0.85}
        width={measuredWidth + PAD_X * 2}
        height={fontSize * 1.7}
        rx={fontSize * 0.4}
        fill={C.bg1}
        fillOpacity={0.94}
        stroke={strokeColor}
        strokeOpacity={strokeOpacity}
        strokeWidth={1.5 / strokeScale}
      />
      <text
        ref={textRef}
        textAnchor="middle"
        dominantBaseline="central"
        fontSize={fontSize}
        fontWeight={700}
        fill={textColor}
      >
        {text}
      </text>
    </g>
  )
}

function DotChart({
  dots, narratives, topics, width, height,
  selectedRegionId, focusedFrameId, hoveredDotId, hoveredRegionId, hoveredNarrativeId,
  onHoverDot, onHoverRegion, onHoverNarrative,
  onClickDot, onClickRegion, onClickNarrative, onClickBackground,
}: DotChartProps) {
  // V13.12.5 — chart-edge padding reduced (40 → 22) so topics extend
  // closer to the viewport edges. Dots bumped from 4 → 6 for legibility.
  const DOT_PAD = 22          // chart edge padding
  const DOT_RADIUS = 6        // visual size of one dot at zoom 1

  // V13 — hierarchical layout: topics & narratives force-separated so
  // they don't overlap; dots use UMAP offsets inside their narrative.
  const layout = useMemo(
    () => computeHierarchicalLayout(dots, narratives, topics, width, height, DOT_PAD),
    [dots, narratives, topics, width, height],
  )
  const positions = layout.dotPositions

  // Group dots by frame_id (for narrative hulls + labels).
  const dotsByFrame = useMemo(() => {
    const m = new Map<number, ExtractDot[]>()
    for (const d of dots) {
      const arr = m.get(d.frame_id) || []
      arr.push(d); m.set(d.frame_id, arr)
    }
    return m
  }, [dots])

  // Camera transform: zoom to a region (or to a focused narrative).
  // Identity if nothing's selected.
  const transform = useMemo(() => {
    // V13 — zoom uses the explicit circle centers from the hierarchical
    // layout (cleaner camera target than bounding-box of dots).
    let target: { x: number; y: number; r: number } | null = null
    if (focusedFrameId !== null) {
      const nc = layout.narrativeCenters.get(focusedFrameId)
      if (nc) target = nc
    } else if (selectedRegionId !== null) {
      const tc = layout.topicCenters.get(selectedRegionId)
      if (tc) target = { x: tc.x, y: tc.y, r: tc.r + 14 }  // include hull stroke padding
    }
    if (!target) return { translateX: 0, translateY: 0, scale: 1 }
    const pad = 60
    // Target diameter ≈ chart's smaller side minus padding.
    const scale = Math.min((width - pad * 2) / (target.r * 2), (height - pad * 2) / (target.r * 2), 4)
    return {
      translateX: width / 2 - target.x * scale,
      translateY: height / 2 - target.y * scale,
      scale,
    }
  }, [focusedFrameId, selectedRegionId, layout, width, height])

  if (width === 0 || height === 0) return null

  // Dim color helper — narrative isn't the focused one
  // V13.12.8 — topic dim: when the user has zoomed into a topic (or a
  // narrative inside one), all OTHER topics fade to background so the
  // selected one stands out. Mirrors the existing narrative-level
  // isDimmed but at the topic layer.
  const isTopicDimmed = (regionId: number): boolean => {
    if (focusedFrameId !== null) {
      const focusedTopic = topics.find(t => t.member_frame_ids.includes(focusedFrameId))
      if (focusedTopic) return focusedTopic.region_id !== regionId
    }
    if (selectedRegionId !== null) {
      return selectedRegionId !== regionId
    }
    return false
  }

  const isDimmed = (frameId: number): boolean => {
    if (focusedFrameId !== null) return frameId !== focusedFrameId
    if (selectedRegionId !== null) {
      const topic = topics.find(t => t.region_id === selectedRegionId)
      if (topic) return !topic.member_frame_ids.includes(frameId)
    }
    return false
  }

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      // V13.15 — "xMidYMid meet" keeps the aspect ratio of the simulation
      // box (LAYOUT_WIDTH × LAYOUT_HEIGHT) regardless of container shape.
      // The chart letterboxes/pillarboxes as needed but the topology
      // (which circles merge, which sit apart) is identical on every
      // device. Previous "none" stretched the sim output to fit the
      // container, which made overlap patterns visibly different between
      // screens of different aspect ratios.
      preserveAspectRatio="xMidYMid meet"
      style={{
        width: '100%', height: '100%', display: 'block',
        cursor: (focusedFrameId !== null || selectedRegionId !== null) ? 'zoom-out' : 'default',
      }}
      onClick={onClickBackground}
    >
      {/* V13.12.2 — Per-topic masks that hide arc portions lying INSIDE
          other topic circles. Each topic gets a mask: white everywhere
          (= visible) except black inside each OTHER topic's circle (=
          hidden). The topic's stroke is rendered with that mask, so
          overlapping-arc portions disappear and the remaining arcs of
          two intersecting circles read as a single continuous outline.
          No filters, no math — pure SVG composition. */}
      <defs>
        {topics.map(topic => {
          const tc = layout.topicCenters.get(topic.region_id)
          if (!tc) return null
          return (
            <mask
              key={`topic-mask-${topic.region_id}`}
              id={`topic-mask-${topic.region_id}`}
              maskUnits="userSpaceOnUse"
              x={0} y={0} width={width} height={height}
            >
              <rect x={0} y={0} width={width} height={height} fill="white" />
              {topics
                .filter(other => other.region_id !== topic.region_id)
                .map(other => {
                  const oc = layout.topicCenters.get(other.region_id)
                  if (!oc) return null
                  return (
                    <circle
                      key={`mask-hole-${topic.region_id}-${other.region_id}`}
                      cx={oc.x} cy={oc.y} r={oc.r + 12}
                      fill="black"
                    />
                  )
                })}
            </mask>
          )
        })}
        {/* Same mask treatment for narratives — each narrative ring has
            arc portions inside OTHER narrative circles clipped out, so
            overlapping narratives within a topic merge into a continuous
            outline instead of crossing each other. */}
        {narratives.map(n => {
          const nc = layout.narrativeCenters.get(n.frame_id)
          if (!nc) return null
          return (
            <mask
              key={`narr-mask-${n.frame_id}`}
              id={`narr-mask-${n.frame_id}`}
              maskUnits="userSpaceOnUse"
              x={0} y={0} width={width} height={height}
            >
              <rect x={0} y={0} width={width} height={height} fill="white" />
              {narratives
                .filter(other => other.frame_id !== n.frame_id)
                .map(other => {
                  const onc = layout.narrativeCenters.get(other.frame_id)
                  if (!onc) return null
                  return (
                    <circle
                      key={`narr-mask-hole-${n.frame_id}-${other.frame_id}`}
                      cx={onc.x} cy={onc.y} r={onc.r + 3}
                      fill="black"
                    />
                  )
                })}
            </mask>
          )
        })}
      </defs>
      <g
        style={{
          transition: 'transform 500ms cubic-bezier(0.4, 0.0, 0.2, 1)',
          transform: `translate(${transform.translateX}px, ${transform.translateY}px) scale(${transform.scale})`,
          transformOrigin: '0 0',
        }}
      >
        {/* TOPIC HULLS (bottom layer) — solid-fill circles, no ring.
            Topics sit in the visual BACKGROUND of the hierarchy: lowest
            opacity so narratives + dots layered on top read as the
            primary content. Dominance still modulates intensity (one-
            sided topics show as bolder color tint). */}
        {topics.map(topic => {
          const tc = layout.topicCenters.get(topic.region_id)
          if (!tc) return null
          // V13.19 — topic color = dominant of the 4 quadrants + media.
          const { color, dominance } = topicQuadrantColor(topic.quadrant_mix)
          const isSelected = selectedRegionId === topic.region_id
          const isHovered = hoveredRegionId === topic.region_id
          // V13.12.2 — topic rings visible at rest. The per-topic mask
          // hides arc portions inside OTHER topic circles, so overlapping
          // rings merge into one continuous outline without the arcs
          // crossing each other (the cartoon-bubble effect, done by
          // pure SVG masking — no filter, no math).
          //
          // V13.13 — when a topic is SELECTED, fade its ring INTO the
          // background (0.2 opacity, thin stroke). The zoom + the
          // contained narratives/dots already signal "this is the
          // selected region"; keeping the ring at full brightness
          // competed visually with the narratives the user is trying
          // to look at. The selected-topic now reads as "the
          // background frame for these narratives" instead of
          // "another foreground element".
          const baseStrokeOpacity = 0.45 + dominance * 0.40
          const strokeOpacity = isSelected ? 0.2
                                : (isHovered ? Math.min(1.0, baseStrokeOpacity + 0.15)
                                  : baseStrokeOpacity)
          const ringWidth = (isSelected ? 1 : (isHovered ? 2.5 : 2)) / Math.max(1, transform.scale)
          // V13.12.7 — fill ONLY on hover. Once selected (clicked into),
          // fill goes away — the zoom + prominent ring stroke
          // communicate selection. Avoids the heavy filled-circle look
          // when the user has drilled in to look at narratives/dots.
          const hoverFillOpacity = (isHovered && !isSelected) ? 0.30 : 0
          const topicDimmed = isTopicDimmed(topic.region_id)
          return (
            <g key={`topic-${topic.region_id}`}
               onMouseEnter={() => onHoverRegion(topic.region_id)}
               onMouseLeave={() => onHoverRegion(null)}
               onClick={(e) => { e.stopPropagation(); onClickRegion(topic.region_id) }}
               style={{
                 cursor: 'pointer',
                 opacity: topicDimmed ? 0.2 : 1,
                 transition: 'opacity 350ms',
               }}>
              {/* V13.12.6 — invisible hit-target: catches hover/click
                  anywhere inside the full topic circle, including masked-
                  out arc regions where the stroke isn't actually painted.
                  Without this, only the visible stroke received events,
                  which was thin + inconsistent. */}
              <circle
                cx={tc.x} cy={tc.y} r={tc.r + 12}
                fill="transparent"
                pointerEvents="all"
              />
              {/* Hover-fill — rendered WITHOUT the mask so the full
                  circle fills in (not just the visible arc). */}
              {hoverFillOpacity > 0 && (
                <circle
                  cx={tc.x} cy={tc.y} r={tc.r + 12}
                  fill={color}
                  fillOpacity={hoverFillOpacity}
                  pointerEvents="none"
                  style={{ transition: 'fill-opacity 250ms' }}
                />
              )}
              {/* Stroke — masked so overlap arcs disappear. */}
              <circle
                cx={tc.x} cy={tc.y} r={tc.r + 12}
                fill="none"
                stroke={color}
                strokeOpacity={strokeOpacity}
                strokeWidth={ringWidth}
                mask={`url(#topic-mask-${topic.region_id})`}
                pointerEvents="none"
                style={{ transition: 'stroke-opacity 250ms, stroke-width 250ms' }}
              />
            </g>
          )
        })}

        {/* NARRATIVE HULLS (middle layer) — solid-fill circles, no ring.
            Narratives sit in the MIDGROUND of the hierarchy: more
            opaque than topic (so they stand out as distinct groupings
            inside the topic) but still translucent enough that the dots
            inside read as the focal layer. */}
        {narratives.map(n => {
          const nc = layout.narrativeCenters.get(n.frame_id)
          if (!nc) return null
          const color = quadrantColor(n.owner_type as OwnerType, n.subject_type as OwnerType | undefined)
          const isFocused = focusedFrameId === n.frame_id
          const isHovered = hoveredNarrativeId === n.frame_id
          const dimmed = isDimmed(n.frame_id) && !isFocused
          // V13.12.2 — narratives as RINGS with the same mask-clipping
          // pattern as topics: arc portions inside OTHER narratives are
          // masked out, so overlapping narratives merge into one outline.
          // Hover-fill rendered without the mask so the full circle
          // fills in cleanly on selection.
          const zoomFactor = Math.max(0, Math.min(1, (transform.scale - 1.0) / 1.0))
          const restingStrokeOpacity = 0.45 + zoomFactor * 0.40
          const strokeOpacity = isFocused ? 1.0
                                : (isHovered ? Math.min(1.0, restingStrokeOpacity + 0.15)
                                  : restingStrokeOpacity)
          const ringStrokeWidth = (isFocused ? 2.5 : (isHovered ? 2 : 1.5)) / Math.max(1, transform.scale)
          // V13.12.7 — fill ONLY on hover. Click/focus removes fill so the
          // drilled-in narrative is visually "opened up" not "filled in".
          const fillOpacity = (isHovered && !isFocused) ? 0.40 : 0
          return (
            <g key={`narr-${n.frame_id}`}
               onMouseEnter={() => onHoverNarrative(n.frame_id)}
               onMouseLeave={() => onHoverNarrative(null)}
               onClick={(e) => { e.stopPropagation(); onClickNarrative(n.frame_id) }}
               style={{ cursor: 'pointer', opacity: dimmed ? 0.25 : 1, transition: 'opacity 250ms' }}>
              {/* V13.12.6 — invisible hit-target: same fix as topics.
                  Catches hover/click across the full narrative circle. */}
              <circle
                cx={nc.x} cy={nc.y} r={nc.r + 3}
                fill="transparent"
                pointerEvents="all"
              />
              {/* Hover-fill (no mask) — fills full circle on hover/focus. */}
              {fillOpacity > 0 && (
                <circle
                  cx={nc.x} cy={nc.y} r={nc.r + 3}
                  fill={color}
                  fillOpacity={fillOpacity}
                  pointerEvents="none"
                  style={{ transition: 'fill-opacity 250ms' }}
                />
              )}
              {/* Stroke (masked) — overlap arcs with other narratives clipped. */}
              <circle
                cx={nc.x} cy={nc.y} r={nc.r + 3}
                fill="none"
                stroke={color}
                strokeOpacity={strokeOpacity}
                strokeWidth={ringStrokeWidth}
                mask={`url(#narr-mask-${n.frame_id})`}
                pointerEvents="none"
                style={{ transition: 'stroke-opacity 250ms, stroke-width 250ms' }}
              />
            </g>
          )
        })}

        {/* DOTS (top layer) — the actual data. Each one is one article
            extract, colored by parent frame's owner_type.
            V13.11: at default zoom dots are very small + low-opacity so
            they read as texture, not data. Zoom in to inspect. Hover or
            focus still makes them prominent. */}
        {dots.map(d => {
          const p = positions.get(d.id)
          if (!p) return null
          // V13.19 — color by quadrant (owner × subject) so attack dots
          // and defense dots are visually distinguishable within a single
          // narrative's bubble.
          const color = quadrantColor(d.owner_type as OwnerType, d.subject_type as OwnerType | undefined)
          const isHovered = hoveredDotId === d.id
          const dimmed = isDimmed(d.frame_id)
          // V13.12.5 — bigger dot baseline (1.5 → 3) so dots are visibly
          // legible at the default zoom. Still scaled inverse to zoom so
          // they don't grow huge when zoomed in.
          const zoomFactor = Math.max(0, Math.min(1, (transform.scale - 1.0) / 0.8))
          const restingR = (3 + zoomFactor * (DOT_RADIUS - 3)) / Math.max(1, transform.scale)
          const r = isHovered
            ? (DOT_RADIUS * 1.8) / Math.max(1, transform.scale)
            : restingR
          // Progressive opacity too — fade in with zoom so they don't
          // visually dominate the topic regions at the overview.
          const restingOpacity = 0.35 + zoomFactor * 0.65
          const opacity = isHovered ? 1 : (dimmed ? 0.2 : restingOpacity)
          return (
            <circle
              key={d.id}
              cx={p.x} cy={p.y} r={r}
              fill={color}
              fillOpacity={opacity}
              onMouseEnter={(e) => { e.stopPropagation(); onHoverDot(d.id) }}
              onMouseLeave={(e) => { e.stopPropagation(); onHoverDot(null) }}
              onClick={(e) => { e.stopPropagation(); onClickDot(d.id) }}
              style={{ cursor: 'pointer', transition: 'fill-opacity 250ms' }}
            />
          )
        })}

        {/* TOPIC LABELS — V13.11: positioned INSIDE the ring at the top
            edge, like a chapter heading. Width is dynamically measured
            via getBBox in the TopicLabel component so the rect hugs the
            text regardless of character widths. */}
        {topics.map(topic => {
          const tc = layout.topicCenters.get(topic.region_id)
          if (!tc) return null
          const labelFont = Math.max(9, 17 / transform.scale)
          const labelY = tc.y - tc.r + 12 / transform.scale
          const isSelected = selectedRegionId === topic.region_id
          // V13.19 — topic color = dominant of the 4 quadrants + media.
          const { color, dominance } = topicQuadrantColor(topic.quadrant_mix)
          const topicStrokeOpacity = isSelected
            ? 0.3
            : Math.max(0.25, 0.5 * (0.4 + 0.6 * dominance))
          // V13.13 — selected topic's label fades to background (matches
          // the topic ring also fading). Narratives + dots are the
          // foreground when the user has drilled into a topic; the
          // topic's name is a contextual reminder, not the focal element.
          const labelOpacity = (isTopicDimmed(topic.region_id) || isSelected) ? 0.2 : 1
          return (
            <BadgeLabel
              key={`topic-label-${topic.region_id}`}
              cx={tc.x}
              cy={labelY}
              text={topic.label}
              fontSize={labelFont}
              textColor={C.text1}
              strokeColor={color}
              strokeOpacity={topicStrokeOpacity}
              strokeScale={transform.scale}
              opacity={labelOpacity}
            />
          )
        })}

        {/* NARRATIVE LABELS — progressive visibility tied to zoom level.
            - Overview (scale ≈ 1): invisible. Topic-level view stays clean.
            - Topic zoom (scale ≈ 1.4-2.0): all narrative labels in view fade in.
            - Narrative focus: the focused narrative's label is HIDDEN
              (the breadcrumb at the top already names it; rendering it
              twice was confusing — "appears once as label, once in
              breadcrumb underneath").

            V13.14 — narrative labels uniformly ABOVE their bubble.
            Earlier V13.13 moved them below the bubble; user reverted —
            above is the conventional position for a chart label sitting
            with a marker. Topic label fades to 0.2 opacity when its
            topic is selected (see topic-labels block), so narrative
            labels near the top of a topic no longer compete with a
            full-brightness topic title.

            Single-article narratives also get labels now (was
            dot_count >= 2). Tiny narratives are still tracked and
            worth naming on the chart; the previous threshold hid them
            for clutter reasons, but the zoom-progressive opacity
            already handles overview clutter (labels invisible at scale ~1).

            Other label improvements kept:
            - Dynamic truncation budget: 32 chars at default zoom, 48 chars
              when zoomed in (scale > 1.5).
            - Hovered label renders LAST so it paints on top of any
              neighbors. Implemented by sorting narratives so hovered
              is last in render order.

            Font size uses a counter-scale formula so labels stay ~11px
            rendered regardless of zoom. */}
        {(() => {
          // Build the set of frame_ids that ARE inside a real topic, so
          // noise narratives (no parent topic) can be identified for
          // always-visible labels. Without this the noise frames render
          // as unlabeled rings — the user can SEE the circles but has no
          // idea what they are (the topic-label hierarchy doesn't cover
          // them and the narrative-label zoom gate hides them at default
          // scale).
          const framesInTopics = new Set<number>()
          for (const t of topics) {
            for (const fid of t.member_frame_ids) framesInTopics.add(fid)
          }
          // Sort: hovered narrative last so it renders on top. Stable
          // otherwise (other narratives keep their natural order).
          const labelList = [...narratives]
            .filter(n => n.dot_count >= 1)
            .sort((a, b) => {
              const aH = a.frame_id === hoveredNarrativeId ? 1 : 0
              const bH = b.frame_id === hoveredNarrativeId ? 1 : 0
              return aH - bH
            })
          // Truncation budget — longer when zoomed in (there's screen room
          // for it, and the user is focused on details rather than overview).
          const maxChars = transform.scale > 1.5 ? 48 : 32
          return labelList.map(n => {
            const nc = layout.narrativeCenters.get(n.frame_id)
            if (!nc) return null
            const labelFont = Math.max(6, 11 / transform.scale)
            // V13.14 — uniformly ABOVE the bubble for all narratives.
            const labelY = nc.y - nc.r - 8 / transform.scale
            const isFocused = focusedFrameId === n.frame_id
            const isHovered = hoveredNarrativeId === n.frame_id
            const dimmed = isDimmed(n.frame_id) && !isFocused
            // Hide focused narrative's chart label — breadcrumb shows it.
            if (isFocused) return null
            const shortName = n.name.length > maxChars
              ? n.name.slice(0, maxChars) + '…'
              : n.name
            // V13.15 — noise narratives (no parent topic) get
            // always-visible labels. Without a containing topic ring +
            // topic label, the user has no other cue about what the
            // floating ring is — labeling it inline is essential for
            // legibility at default zoom. Narratives INSIDE topics keep
            // the zoom-progressive opacity (the topic name is the
            // contextual cue at overview zoom).
            const isNoise = !framesInTopics.has(n.frame_id)
            const zoomOpacity = Math.max(0, Math.min(1, (transform.scale - 1.05) / 0.45))
            let finalOpacity: number
            if (isHovered) {
              finalOpacity = 0.95
            } else if (isNoise) {
              finalOpacity = 0.85  // always visible
            } else if (dimmed) {
              finalOpacity = zoomOpacity * 0.15
            } else {
              finalOpacity = zoomOpacity
            }
            // V13.19 — narrative label stroke uses quadrant color to
            // match the narrative bubble's ring color.
            const narrColor = quadrantColor(n.owner_type as OwnerType, n.subject_type as OwnerType | undefined)
            return (
              <BadgeLabel
                key={`narr-label-${n.frame_id}`}
                cx={nc.x}
                cy={labelY}
                text={shortName}
                fontSize={labelFont}
                textColor={C.text1}
                strokeColor={narrColor}
                strokeOpacity={isHovered ? 0.9 : 0.55}
                strokeScale={transform.scale}
                opacity={finalOpacity}
              />
            )
          })
        })()}
      </g>

      {/* Watermark removed (V13.1) — it lived at top-right of the chart
          and collided with topic labels whose circles ended up in the
          same corner. The page header above the chart already explains
          what the dots/circles mean. */}
    </svg>
  )
}


// ── Sidebar components ─────────────────────────────────────────────────────
//
// ProposedSidebar — flat cluster list for the proposed view (carries over
// the V12 behavior unchanged).
//
// EstablishedSidebar — V13.2 progressive contextual outline. Three nested
// sections appear as the user drills in:
//   1. Topic Regions (always)
//   2. Narratives in selected topic (when a topic is active)
//   3. Article extracts in focused narrative (when a narrative is focused)
//      + detail card for the selected extract when one is clicked.

// TreeRow — PgAdmin-style file-tree row.
//
// Indentation is BUILT-IN: each TreeRow that's expanded wraps its
// children in a margin-left + border-left container, giving you the
// classic vertical guide line connecting parent to children. This means
// depth is implicit (whatever nesting the caller does), not a prop.
//
// Pattern:
//   <TreeRow ...>
//     <TreeRow ...>      ← auto-indented by parent's wrapper
//       <TreeRow ... />  ← auto-indented again
//     </TreeRow>
//   </TreeRow>
//
// Replaces the V13.5 explicit depth-prop pattern which made it
// awkward to add the guide lines.
function TreeRow({
  expanded, expandable, color, label, meta, onClick, onEdit,
  fontSize, fontWeight, accent, children,
}: {
  expanded: boolean
  expandable: boolean    // false = leaf node (no chevron column)
  color: string
  label: string
  meta?: string
  onClick: () => void
  onEdit?: () => void
  fontSize?: number
  fontWeight?: number
  accent?: boolean       // true = subtle bg even when not expanded
  children?: React.ReactNode
}) {
  return (
    <div>
      <div
        onClick={onClick}
        style={{
          display: 'flex', alignItems: 'flex-start', gap: 4,
          padding: '5px 6px',
          marginBottom: 1, borderRadius: 4,
          background: expanded ? `${color}1f` : (accent ? `${color}10` : 'transparent'),
          cursor: 'pointer',
          transition: 'background 120ms',
        }}
        onMouseEnter={(e) => {
          if (!expanded) e.currentTarget.style.background = `${color}14`
        }}
        onMouseLeave={(e) => {
          if (!expanded) e.currentTarget.style.background = accent ? `${color}10` : 'transparent'
        }}
      >
        {/* Chevron column — fixed width so label baselines align across
            rows. Using inline SVG instead of Unicode triangles so we get
            an OPEN chevron shape (v / >) like a code editor's file tree,
            not the heavy filled triangles (▼/▶) that read as buttons.
            Colored with the row's owner color so it doubles as a level
            indicator (replaces the separate color dot from V13.5). */}
        <span style={{
          flexShrink: 0, width: 14, height: 14,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          marginTop: 2, userSelect: 'none',
        }}>
          {expandable && (
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
                 stroke={color} strokeWidth="1.6"
                 strokeLinecap="round" strokeLinejoin="round">
              {expanded
                ? <path d="M 2 3.5 L 5 6.5 L 8 3.5" />     /* open v pointing down */
                : <path d="M 3.5 2 L 6.5 5 L 3.5 8" />     /* open > pointing right */
              }
            </svg>
          )}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: fontSize ?? 12,
            fontWeight: fontWeight ?? (expanded ? 700 : 500),
            color: C.text1, lineHeight: 1.3,
            overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {label}
          </div>
          {meta && (
            <div style={{ fontSize: 10, color: C.text3, marginTop: 1 }}>
              {meta}
            </div>
          )}
        </div>
        {onEdit && (
          <button onClick={(e) => { e.stopPropagation(); onEdit() }}
                  title="Rename"
                  style={{ background: 'transparent', border: 'none', color: C.text3, cursor: 'pointer', fontSize: 12, padding: '0 4px', flexShrink: 0 }}>
            ✎
          </button>
        )}
      </div>
      {/* Children wrapped in a margin-left container with a left border —
          this is the vertical guide line shown in file-tree UIs like
          PgAdmin. The 13px margin aligns the line with the chevron
          center (6px padding + 7px half-arrow ≈ 13). Each nesting
          level adds another 13px + a new guide line. */}
      {expanded && children && (
        <div style={{
          marginLeft: 13,
          borderLeft: `1px solid ${C.border}`,
          paddingLeft: 4,
        }}>
          {children}
        </div>
      )}
    </div>
  )
}

function SidebarSection({ title, subtitle, children, accent }: {
  title: string; subtitle?: string; children: React.ReactNode; accent?: string;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{
        fontSize: 11, color: accent || C.text3, marginBottom: 4,
        letterSpacing: '0.08em', fontWeight: 600, textTransform: 'uppercase',
      }}>
        {title}
      </div>
      {subtitle && (
        <div style={{ fontSize: 10, color: C.text3, marginBottom: 8, lineHeight: 1.5 }}>
          {subtitle}
        </div>
      )}
      {children}
    </div>
  )
}


function EstablishedSidebar({
  dotData, regionsForChart, selectedRegionId, focusedFrameId, selectedDotId,
  hoveredRegionId, hoveredNarrativeId,
  onHoverRegion, onHoverNarrative,
  onClickRegion, onClickNarrative, onClickDot, onEditRegion,
  sectionTitle, primaryAction,
  candidateName, opponentName,
}: {
  dotData: DotLandscape | null
  regionsForChart: TopicRegionForChart[]
  selectedRegionId: number | null
  focusedFrameId: number | null
  selectedDotId: number | null
  hoveredRegionId: number | null
  hoveredNarrativeId: number | null
  onHoverRegion: (id: number | null) => void
  onHoverNarrative: (id: number | null) => void
  onClickRegion: (id: number) => void
  onClickNarrative: (id: number) => void
  onClickDot: (id: number) => void
  onEditRegion: (region: TopicRegionForChart) => void
  // V13.9 — proposed mode passes these so the same component renders
  // a flat narrative tree (no topic layer) with a Promote button inside
  // the focused narrative's expanded panel.
  sectionTitle?: { title: string; subtitle: string }
  primaryAction?: { label: string; onClick: (frameId: number) => void }
  candidateName: string
  opponentName: string
}) {
  // Hooks MUST come before any early return — React requires consistent
  // hook order across renders. The useMemo was previously after the
  // `if (!dotData) return null` guard which caused React's hook
  // reconciliation to break when dotData transitioned null → non-null
  // (the rendered sidebar would silently fall back to showing only
  // topics, ignoring topic/narrative selection).
  const frameToTopic = useMemo(() => {
    const m = new Map<number, number>()
    if (!dotData) return m
    for (const t of dotData.topics) {
      for (const fid of t.member_frame_ids) m.set(fid, t.region_id)
    }
    return m
  }, [dotData])

  if (!dotData) return null

  // Derive the current topic: the user-selected one wins; if a narrative is
  // focused without a topic explicitly selected, find the topic that
  // contains it. Either way, "currentTopicId" is the active context.
  const currentTopicId: number | null =
    selectedRegionId !== null ? selectedRegionId
    : (focusedFrameId !== null ? (frameToTopic.get(focusedFrameId) ?? null) : null)
  const currentTopic = currentTopicId !== null
    ? regionsForChart.find(r => r.region_id === currentTopicId) || null
    : null
  const focusedNarrative = focusedFrameId !== null
    ? dotData.narratives.find(n => n.frame_id === focusedFrameId) || null
    : null

  // V13.5 — file-tree-style sidebar. All topics always visible as rows.
  // Click a topic → it expands inline; siblings stay collapsed.
  // Same pattern at narrative and article levels. Detail card lives
  // INSIDE the expanded article row, indented further.
  //
  // Removed: italic style + "edited" badge on user-renamed labels
  // (V13.3). Removed: CollapsedHeader pills and middle-of-column
  // chevrons that left blank space (V13.4).

  // Helper: render a flat list of narratives (used both for topic children
  // and for the no-topic proposed mode at the root level).
  const renderNarrativeRows = (memberNarratives: NarrativeGroupInfo[]): React.ReactNode => {
    return memberNarratives.map(n => {
      // 5-quadrant color — uses both owner_type and subject_type so the
      // tree-row arrow matches the chart's quadrant palette (not just the
      // 3-color owner palette).
      const oc = quadrantColor(n.owner_type, n.subject_type)
      const quadrantText = quadrantNamedLabel(
        // Re-derive the quadrant key locally so we don't pull in
        // quadrantKey() unnecessarily — keep this branch in lockstep
        // with quadrantColor() above.
        n.owner_type === 'candidate' && n.subject_type === 'candidate' ? 'our_defense' :
        n.owner_type === 'candidate' && n.subject_type === 'opponent'  ? 'our_offense' :
        n.owner_type === 'opponent'  && n.subject_type === 'opponent'  ? 'their_defense' :
        n.owner_type === 'opponent'  && n.subject_type === 'candidate' ? 'their_offense' :
        'media',
        candidateName, opponentName,
      )
      const isNarrExpanded = focusedFrameId === n.frame_id
      return (
        <TreeRow
          key={`narr-${n.frame_id}`}
          expanded={isNarrExpanded}
          expandable={true}
          color={oc}
          label={n.name}
          meta={`${n.dot_count} ${n.dot_count === 1 ? 'extract' : 'extracts'} · ${quadrantText}`}
          onClick={() => onClickNarrative(n.frame_id)}
          accent={isNarrExpanded}
        >
          {isNarrExpanded && (
            <>
              {/* V13.9 — primary action button (proposed mode only).
                  Sits ABOVE the article rows so it's the first thing
                  the user sees inside the focused narrative panel. */}
              {primaryAction && (
                <div style={{ padding: '4px 4px 6px 4px' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); primaryAction.onClick(n.frame_id) }}
                    style={{
                      width: '100%', padding: '6px 10px', fontSize: 11, fontWeight: 700,
                      background: C.accent, color: '#000', border: 'none',
                      borderRadius: 4, cursor: 'pointer',
                    }}
                  >
                    {primaryAction.label}
                  </button>
                </div>
              )}
              {renderNarrativeChildren(n)}
            </>
          )}
        </TreeRow>
      )
    })
  }

  // Helper to render the full tree for a single topic when expanded.
  // Returns the children to render inside the topic's TreeRow.
  const renderTopicChildren = (region: TopicRegionForChart): React.ReactNode => {
    const narrativeIds = new Set(region.member_frame_ids)
    const memberNarratives = dotData.narratives
      .filter(n => narrativeIds.has(n.frame_id))
      .sort((a, b) => b.dot_count - a.dot_count)
    return renderNarrativeRows(memberNarratives)
  }

  // Helper to render article rows under an expanded narrative.
  // The detail card lives INSIDE the expanded article's TreeRow,
  // pushed one indent deeper as the "leaf" of the tree.
  const renderNarrativeChildren = (n: NarrativeGroupInfo): React.ReactNode => {
    const memberDots = dotData.dots
      .filter(d => d.frame_id === n.frame_id)
      .sort((a, b) => {
        // Newest first (no date → bottom)
        const ad = a.published_at || ''
        const bd = b.published_at || ''
        return bd.localeCompare(ad)
      })
    const oc = quadrantColor(n.owner_type, n.subject_type)
    return memberDots.map(d => {
      const isExpanded = selectedDotId === d.id
      const title = d.source_title || '(no title)'
      const metaParts = [
        d.outlet_name || d.source_name || 'unknown outlet',
        d.published_at?.slice(0, 10),
      ].filter(Boolean) as string[]
      return (
        <TreeRow
          key={`extract-${d.id}`}
          expanded={isExpanded}
          expandable={true}
          color={oc}
          label={title}
          meta={metaParts.join(' · ')}
          fontSize={11}
          fontWeight={isExpanded ? 700 : 400}
          onClick={() => onClickDot(d.id)}
          accent={isExpanded}
        >
          {isExpanded && (
            // Detail card is automatically indented under the article
            // (TreeRow wraps its children in the guide-line container).
            <div style={{
              margin: '4px 0 8px 0',
              padding: 10, borderRadius: 6,
              background: C.bg3, border: `1px solid ${oc}`,
            }}>
              {d.extracted_text && (
                <div style={{
                  fontSize: 12, color: C.text2, fontStyle: 'italic',
                  lineHeight: 1.5, borderLeft: `2px solid ${oc}`,
                  paddingLeft: 8, marginBottom: 8,
                }}>
                  "{d.extracted_text}"
                </div>
              )}
              <div style={{ fontSize: 11, color: C.text3, lineHeight: 1.5 }}>
                <div>
                  <strong style={{ color: C.text2 }}>Outlet:</strong>{' '}
                  {d.outlet_name || d.source_name || 'unknown'}
                  {d.outlet_type && ` (${d.outlet_type})`}
                </div>
                {d.published_at && (
                  <div>
                    <strong style={{ color: C.text2 }}>Published:</strong>{' '}
                    {formatArticleDate(d.published_at)}
                  </div>
                )}
              </div>
            </div>
          )}
        </TreeRow>
      )
    })
  }

  // V13.9 — no-topics mode (proposed view): skip the topic layer entirely
  // and render all narratives at the root, sorted by extract count desc.
  // Same TreeRow component, same drill-in behavior — just no topic wrapper.
  if (regionsForChart.length === 0) {
    const allNarratives = dotData.narratives
      .slice()
      .sort((a, b) => b.dot_count - a.dot_count)
    const title = sectionTitle?.title ?? `Clusters · ${allNarratives.length}`
    const subtitle = sectionTitle?.subtitle
      ?? 'Sorted by size. Click to drill in; click again to drill back up.'
    return (
      <SidebarSection title={title} subtitle={subtitle}>
        {renderNarrativeRows(allNarratives)}
      </SidebarSection>
    )
  }

  // V13.11 — collect narratives that aren't in any topic region (HDBSCAN
  // noise points). They previously got always-visible labels on the chart
  // which felt cluttered; we now surface them in a dedicated sidebar
  // section so they stay discoverable without polluting the chart.
  const categorizedIds = new Set<number>()
  for (const r of regionsForChart) for (const fid of r.member_frame_ids) categorizedIds.add(fid)
  const uncategorizedNarratives = dotData.narratives
    .filter(n => !categorizedIds.has(n.frame_id))
    .sort((a, b) => b.dot_count - a.dot_count)

  const title = sectionTitle?.title ?? `Topic Regions · ${regionsForChart.length}`
  const subtitle = sectionTitle?.subtitle
    ?? 'Click any row to drill in. Click again to drill back up.'
  return (
    <>
      <SidebarSection title={title} subtitle={subtitle}>
        {regionsForChart.map(region => {
          const { color } = topicColorWithDominance(region.owner_mix)
          const isTopicExpanded = currentTopicId === region.region_id
          return (
            <TreeRow
              key={`topic-${region.region_id}`}
              expanded={isTopicExpanded}
              expandable={true}
              color={color}
              label={region.label}
              meta={`${region.member_frame_ids.length} ${region.member_frame_ids.length === 1 ? 'narrative' : 'narratives'}`}
              onClick={() => onClickRegion(region.region_id)}
              onEdit={() => onEditRegion(region)}
              fontSize={13}
              fontWeight={isTopicExpanded ? 700 : 600}
              accent={isTopicExpanded}
            >
              {isTopicExpanded && renderTopicChildren(region)}
            </TreeRow>
          )
        })}
      </SidebarSection>
      {/* Uncategorized narratives — HDBSCAN noise points (frames whose
          topic-space position is too isolated to cluster with anything).
          Surfaced here so they stay discoverable without cluttering the
          chart with always-on labels. */}
      {uncategorizedNarratives.length > 0 && (
        <SidebarSection
          title={`Uncategorized · ${uncategorizedNarratives.length}`}
          subtitle="Narratives without a topic — not enough similar narratives nearby to form a region."
        >
          {renderNarrativeRows(uncategorizedNarratives)}
        </SidebarSection>
      )}
    </>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ width: 9, height: 9, borderRadius: '50%', background: color, display: 'inline-block' }} />
      <span>{label}</span>
    </span>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────

export function Landscape() {
  // V13.10 — Landscape is established-only. Proposed-cluster review moved
  // to the Review Queue page so this page can focus on "here's where the
  // tracked narratives sit in topic space."
  const [establishedData, setEstablishedData] = useState<EstablishedLandscape | null>(null)
  const [dotData, setDotData] = useState<DotLandscape | null>(null)
  const [hoveredDotV12, setHoveredDotV12] = useState<number | null>(null)
  const [hoveredNarrativeV12, setHoveredNarrativeV12] = useState<number | null>(null)
  // V13.2 — selected extract dot for detail-card display in the sidebar.
  // Distinct from hoveredDotV12 (transient hover for tooltips) and from
  // focusedId (the parent narrative camera zoom).
  const [selectedDotId, setSelectedDotId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [focusedId, setFocusedId] = useState<number | null>(null)
  // V11 — selected topic region. Independent of focusedId so the sidebar
  // can show both the active topic AND the active narrative inside it.
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null)
  const [hoveredRegionId, setHoveredRegionId] = useState<number | null>(null)
  const [editingRegionId, setEditingRegionId] = useState<number | null>(null)
  const [editingLabelValue, setEditingLabelValue] = useState('')
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 })
  const [ownerFilter, setOwnerFilter] = useState<OwnerFilter>('all')
  // ResizeObserver-driven actual chart dimensions.
  const chartRef = useRef<HTMLDivElement>(null)
  const [chartSize, setChartSize] = useState({ w: 0, h: 0 })
  // Candidate / opponent surnames — fed into quadrantNamedLabel for the
  // legend + tree-row metadata.
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')

  // Initial fetch. Both endpoints in parallel: dotData powers the chart,
  // establishedData provides the region persisted_ids for inline label edits.
  useEffect(() => {
    setLoading(true); setError(null)
    setFocusedId(null); setSelectedRegionId(null); setSelectedDotId(null)
    if (dotData && establishedData) { setLoading(false); return }
    Promise.all([
      api.dotLandscape().then(d => { setDotData(d); if (d.error) setError(d.error) }),
      api.establishedLandscape().then(d => { setEstablishedData(d) }),
    ])
      .catch(err => setError(err?.message || 'Failed to load landscape'))
      .finally(() => setLoading(false))
    // Surnames for the 5-quadrant labels — fire-and-forget, errors silenced.
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ResizeObserver: track actual chart container size so layout matches.
  useEffect(() => {
    const el = chartRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const rect = entries[0].contentRect
      setChartSize({ w: rect.width, h: rect.height })
    })
    ro.observe(el)
    const rect = el.getBoundingClientRect()
    setChartSize({ w: rect.width, h: rect.height })
    return () => ro.disconnect()
  }, [])

  // V13.9 — apply owner filter to the established DotLandscape.
  // Filters at the narrative level (a narrative's owner_type wins; all of
  // its extract dots stay or go together), and trims each topic's
  // member_frame_ids to surviving narratives so the topic + sidebar counts
  // match the chart. Dropping topics that lose every member keeps empty
  // hulls off the chart.
  const filteredDotData = useMemo<DotLandscape | null>(() => {
    if (!dotData) return null
    if (ownerFilter === 'all') return dotData
    const keptNarratives = dotData.narratives.filter(n => n.owner_type === ownerFilter)
    const keptFrameIds = new Set(keptNarratives.map(n => n.frame_id))
    const keptDots = dotData.dots.filter(d => keptFrameIds.has(d.frame_id))
    const keptTopics = dotData.topics
      .map(t => ({
        ...t,
        member_frame_ids: t.member_frame_ids.filter(fid => keptFrameIds.has(fid)),
      }))
      .filter(t => t.member_frame_ids.length > 0)
    return { ...dotData, dots: keptDots, narratives: keptNarratives, topics: keptTopics }
  }, [dotData, ownerFilter])

  // Resolve topic regions for the sidebar. Falls back to establishedData
  // until dotData arrives (gives a brief unfiltered flash; fine).
  const regionsForChart: TopicRegionForChart[] = useMemo(() => {
    const source = filteredDotData
      ? filteredDotData.topics.map(t => ({
          region_id: t.region_id,
          persisted_id: t.persisted_id,
          label: t.label,
          edited_by_user: t.edited_by_user,
          member_frame_ids: t.member_frame_ids,
          owner_mix: t.owner_mix,
        }))
      : (establishedData?.regions ?? [])
    return source.map(r => ({
      region_id: r.region_id,
      persisted_id: r.persisted_id,
      label: r.label,
      edited_by_user: r.edited_by_user,
      owner_mix: r.owner_mix,
      member_frame_ids: r.member_frame_ids,
    }))
  }, [filteredDotData, establishedData])

  // Click a topic region — toggle its selection (zoom in / out).
  function handleClickRegion(rid: number) {
    setSelectedRegionId(prev => (prev === rid ? null : rid))
    setFocusedId(null)
    setHoveredRegionId(null)
    setSelectedDotId(null)
  }

  function startEditingRegion(region: TopicRegionForChart) {
    if (region.persisted_id === null) return
    setEditingRegionId(region.persisted_id)
    setEditingLabelValue(region.label)
  }

  async function commitLabelEdit() {
    if (editingRegionId === null || !editingLabelValue.trim()) {
      setEditingRegionId(null)
      return
    }
    try {
      await api.updateTopicRegionLabel(editingRegionId, editingLabelValue.trim())
      // Refetch both — dotData carries labels too, has to refresh.
      setEstablishedData(null)
      setDotData(null)
      setLoading(true)
      try {
        const [d, est] = await Promise.all([api.dotLandscape(), api.establishedLandscape()])
        setDotData(d)
        setEstablishedData(est)
      } finally { setLoading(false) }
    } catch (e) {
      console.error('label update failed:', e)
    } finally {
      setEditingRegionId(null)
      setEditingLabelValue('')
    }
  }

  // Escape: drill back up one level (edit → focus → topic → nothing).
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      if (editingRegionId !== null) { setEditingRegionId(null); return }
      if (focusedId !== null) { setFocusedId(null); return }
      if (selectedRegionId !== null) { setSelectedRegionId(null); return }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [focusedId, selectedRegionId, editingRegionId])

  return (
    <div
      style={{
        // height: 100% matches the <main> in Layout. Don't use 100vh —
        // that's wrong by the nav height and causes an extra scroll.
        height: '100%',
        background: C.bg1,
        padding: '12px 24px',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
        boxSizing: 'border-box',
      }}
      onMouseMove={e => setMousePos({ x: e.clientX, y: e.clientY })}
    >
      {/* Header */}
      <div style={{ marginBottom: 8, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 4 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: C.text1, margin: 0 }}>Narrative Landscape</h1>
          <Link to="/narratives" style={{ fontSize: 12, color: C.text3, textDecoration: 'none' }}>← back to narratives list</Link>
          <Link to="/review" style={{ fontSize: 12, color: C.accent, textDecoration: 'none' }}>review proposed narratives →</Link>
        </div>
        <div style={{ fontSize: 11.5, color: C.text2, maxWidth: 1100, lineHeight: 1.5 }}>
          Each dot = one article extract; clusters of dots form tracked narratives,
          and groups of narratives form auto-labeled topic regions.
          <strong style={{ color: C.text1 }}> Color</strong> = which side it favors,
          <strong style={{ color: C.text1 }}> position</strong> = topical similarity.
          <strong style={{ color: C.accent }}> Click to drill in.</strong>
        </div>
      </div>

      {/* Toolbar */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap', flexShrink: 0 }}>
        <span style={{ fontSize: 11, color: C.text3, letterSpacing: '0.08em', textTransform: 'uppercase' }}>Filter:</span>
        {(['all', 'candidate', 'opponent', 'media'] as OwnerFilter[]).map(o => {
          const active = ownerFilter === o
          const oc = o === 'all' ? C.accent : ownerColor(o)
          return (
            <button key={o} onClick={() => {
                      setOwnerFilter(o)
                      // Clear all selection state on filter change — focused
                      // narrative / selected topic / selected dot may not
                      // survive the filter, and a stale focus zooms the
                      // chart into nothing.
                      setFocusedId(null); setSelectedRegionId(null); setSelectedDotId(null)
                    }}
                    style={{
                      padding: '4px 10px', fontSize: 11, fontWeight: 600,
                      background: active ? `${oc}26` : C.bg2,
                      color: active ? oc : C.text2,
                      border: `1px solid ${active ? oc : C.border}`,
                      borderRadius: 4, cursor: 'pointer', textTransform: 'capitalize',
                    }}>
              {o === 'all' ? 'All' : o}
            </button>
          )
        })}
        {focusedId !== null && (
          <button onClick={() => setFocusedId(null)}
                  style={{ padding: '4px 10px', fontSize: 11, fontWeight: 600, background: `${C.accent}26`, color: C.accent, border: `1px solid ${C.accent}`, borderRadius: 4, cursor: 'pointer', marginLeft: 6 }}>
            ← back to overview
          </button>
        )}
        <span style={{ flex: 1 }} />
        {/* V13.19 — 4-quadrant legend, V14 pro/anti labels. */}
        <LegendDot color={C.our_defense}   label={quadrantNamedLabel('our_defense',   candidateName, opponentName)} />
        <LegendDot color={C.our_offense}   label={quadrantNamedLabel('our_offense',   candidateName, opponentName)} />
        <LegendDot color={C.their_offense} label={quadrantNamedLabel('their_offense', candidateName, opponentName)} />
        <LegendDot color={C.their_defense} label={quadrantNamedLabel('their_defense', candidateName, opponentName)} />
        <LegendDot color={C.media}         label={quadrantNamedLabel('media',         candidateName, opponentName)} />
        <span style={{ marginLeft: 14, fontSize: 11, color: C.text3 }}>
          {filteredDotData?.dots.length ?? 0} extracts · {filteredDotData?.narratives.length ?? 0} narratives · {filteredDotData?.topics.length ?? 0} topics
        </span>
      </div>

      {/* Chart + sidebar */}
      <div style={{
        flex: 1, minHeight: 0,
        display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px',
        gap: 12,
      }}>
        <div
          ref={chartRef}
          style={{
            background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8,
            padding: 6, position: 'relative', minHeight: 0,
          }}
        >
          {loading && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.text3, fontSize: 13 }}>
              Computing 2D projection…
            </div>
          )}
          {error && !loading && (
            <div style={{ padding: 24, color: C.opponent, fontSize: 13, textAlign: 'center' }}>{error}</div>
          )}
          {!loading && filteredDotData && filteredDotData.dots.length > 0 && (
            <DotChart
              dots={filteredDotData.dots}
              narratives={filteredDotData.narratives}
              topics={filteredDotData.topics}
              width={LAYOUT_WIDTH} height={LAYOUT_HEIGHT}
              selectedRegionId={selectedRegionId}
              focusedFrameId={focusedId}
              hoveredDotId={hoveredDotV12}
              hoveredRegionId={hoveredRegionId}
              hoveredNarrativeId={hoveredNarrativeV12}
              onHoverDot={setHoveredDotV12}
              onHoverRegion={setHoveredRegionId}
              onHoverNarrative={setHoveredNarrativeV12}
              onClickDot={(id) => {
                // Click dot → open detail in sidebar + focus parent narrative
                // + switch topic context if needed.
                const dot = filteredDotData.dots.find(d => d.id === id)
                if (!dot) return
                setSelectedDotId(prev => (prev === id ? null : id))
                if (focusedId !== dot.frame_id) setFocusedId(dot.frame_id)
                const dotTopic = filteredDotData.topics.find(
                  t => t.member_frame_ids.includes(dot.frame_id),
                )
                if (dotTopic && selectedRegionId !== dotTopic.region_id) {
                  setSelectedRegionId(dotTopic.region_id)
                }
              }}
              onClickRegion={handleClickRegion}
              onClickNarrative={(frameId) => {
                setFocusedId(prev => (prev === frameId ? null : frameId))
                setSelectedDotId(null)
              }}
              onClickBackground={() => {
                setFocusedId(null); setSelectedRegionId(null); setSelectedDotId(null)
              }}
            />
          )}

          {/* Inline edit popover for the currently-editing region label */}
          {editingRegionId !== null && (
            <div style={{
              position: 'absolute', top: 14, right: 14, zIndex: 6,
              display: 'flex', gap: 6, alignItems: 'center',
              background: C.bg1, border: `1px solid ${C.accent}`, borderRadius: 6,
              padding: '8px 10px', boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
            }}>
              <span style={{ fontSize: 11, color: C.text3 }}>Rename:</span>
              <input
                value={editingLabelValue}
                onChange={e => setEditingLabelValue(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') commitLabelEdit()
                  if (e.key === 'Escape') setEditingRegionId(null)
                }}
                autoFocus
                style={{
                  padding: '4px 8px', fontSize: 12, background: C.bg3,
                  border: `1px solid ${C.border}`, borderRadius: 4,
                  color: C.text1, width: 180,
                }}
              />
              <button onClick={commitLabelEdit}
                      style={{ padding: '4px 10px', fontSize: 11, fontWeight: 600, background: C.accent, color: '#000', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                Save
              </button>
              <button onClick={() => setEditingRegionId(null)}
                      style={{ padding: '4px 8px', fontSize: 11, background: 'transparent', color: C.text2, border: `1px solid ${C.border}`, borderRadius: 4, cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          )}

          {!loading && !error && filteredDotData && filteredDotData.dots.length === 0 && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.text3, fontSize: 13 }}>
              {dotData && dotData.dots.length > 0
                ? 'No established narratives match the filter.'
                : 'No established narratives yet. Review proposed clusters from the Review Queue to promote some.'}
            </div>
          )}
        </div>

        {/* Sidebar — file-tree of topics → narratives → article extracts */}
        <div style={{
          background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 8,
          padding: 12, overflowY: 'auto', minHeight: 0,
        }}>
          <EstablishedSidebar
            dotData={filteredDotData}
            regionsForChart={regionsForChart}
            selectedRegionId={selectedRegionId}
            focusedFrameId={focusedId}
            selectedDotId={selectedDotId}
            hoveredRegionId={hoveredRegionId}
            hoveredNarrativeId={hoveredNarrativeV12}
            onHoverRegion={setHoveredRegionId}
            onHoverNarrative={setHoveredNarrativeV12}
            onClickRegion={handleClickRegion}
            onClickNarrative={(fid) => {
              setFocusedId(prev => prev === fid ? null : fid)
              setSelectedDotId(null)
            }}
            onClickDot={(did) => setSelectedDotId(prev => prev === did ? null : did)}
            onEditRegion={startEditingRegion}
            candidateName={candidateName}
            opponentName={opponentName}
          />
        </div>
      </div>

      {/* Hovered-dot tooltip */}
      {(() => {
        if (hoveredDotV12 === null || !filteredDotData) return null
        const d = filteredDotData.dots.find(x => x.id === hoveredDotV12)
        if (!d) return null
        const n = filteredDotData.narratives.find(narr => narr.frame_id === d.frame_id)
        return (
          <div style={{
            position: 'fixed', left: mousePos.x + 14, top: mousePos.y + 14, zIndex: 999,
            background: C.bg1, border: `1px solid ${C.borderBright}`,
            borderRadius: 6, padding: 10, maxWidth: 420,
            boxShadow: '0 8px 24px rgba(0,0,0,0.7)', pointerEvents: 'none',
          }}>
            <div style={{ fontSize: 10, color: ownerColor(d.owner_type as OwnerType), textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, marginBottom: 4 }}>
              {n?.name || `Frame ${d.frame_id}`}
            </div>
            {d.source_title && (
              <div style={{ fontSize: 12, fontWeight: 700, color: C.text1, marginBottom: 4, lineHeight: 1.3 }}>
                {d.source_title.length > 90 ? d.source_title.slice(0, 90) + '…' : d.source_title}
              </div>
            )}
            {d.extracted_text && (
              <div style={{ fontSize: 11, color: C.text2, fontStyle: 'italic', lineHeight: 1.4, borderLeft: `2px solid ${C.border}`, paddingLeft: 6, marginBottom: 6 }}>
                "{d.extracted_text.length > 240 ? d.extracted_text.slice(0, 240) + '…' : d.extracted_text}"
              </div>
            )}
            <div style={{ fontSize: 10, color: C.text3 }}>
              {d.outlet_name || d.source_name || 'unknown outlet'}
              {d.outlet_type && ` · ${d.outlet_type}`}
              {d.published_at && ` · ${formatArticleDate(d.published_at)}`}
            </div>
          </div>
        )
      })()}
    </div>
  )
}
