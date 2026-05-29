# Feature Spec — Daily Narrative Diff

The most valuable feature from `02_FEATURE_IDEAS.md` (#2). This doc converts the idea into an implementable spec: data model, API, UI, edge cases, validation. Should be enough to hand to a developer (or to yourself in a future session) and implement.

---

## Problem statement

Users currently have to look at the Dashboard and remember what it looked like yesterday to know what changed. That's hard. The signal that matters most is the DELTA, not the snapshot.

Right now:
- Narratives page shows 15 active narratives, each with a "this week" count
- Briefing page shows narrative pulse + recent articles
- Nowhere does the system explicitly say "here's what's new since yesterday"

After this feature:
- One scannable page (and optional email) showing every meaningful change in the narrative landscape in the past 24 hours.

## User stories

> **As the campaign comms director,** when I open the war room each morning, I want to know in 60 seconds what's different since yesterday — so I can decide what (if anything) needs an immediate response today.

> **As the candidate,** I want a weekly summary of what narratives gained ground (mine vs theirs) so I can adjust message focus in upcoming events.

> **As a campaign volunteer with limited time,** I want to glance at "what changed today" before spending an hour on the full dashboard.

## Goals & non-goals

**Goals:**
- One page (or email) showing the day's changes
- Default to 24h diff, with toggle for 7d
- Highlight items that crossed a threshold (stage promoted, momentum spike, dormant→active)
- Surface NEW narratives (promoted from candidate_frames overnight)
- Bottom-line: zero new data sources. Uses existing `frame_stage_history`, `narrative_frames`, `frame_cluster_matches`, `source_items`.

**Non-goals:**
- Auto-generating responses (that's Feature #4 — Talking Point Generator)
- Push notifications (that's Feature #14 — Surge Alert Push)
- AI-generated analysis (race_memo already does this on Briefing page)
- Predictive ("what'll happen tomorrow") — too speculative

---

## Data model

No schema changes needed. All required data exists:

### Reads
| Field | Source | Purpose |
|---|---|---|
| `frame_stage_history.from_stage, to_stage, transitioned_at, frame_id` | existing | Stage promotion/demotion events |
| `frame_stage_history.metrics_snapshot` (JSON) | existing | Why the transition happened |
| `narrative_frames.last_known_stage, momentum_signal, momentum_signal_at` | existing | Current state |
| `frame_cluster_matches.first_seen_at, last_seen_at, frame_id, story_cluster_id` | existing | New FCM rows = new article-frame connections |
| `candidate_frames.resolved_to_frame_id, resolved_at` | existing | Newly promoted candidate frames |
| `story_clusters.first_seen_at, last_seen_at, dormant_since` | existing | New stories, going-dormant clusters |
| `source_items.created_at, race_relevance_label, candidate_mentioned, opponent_mentioned` | existing | Volume / relevance / head-to-head stats |
| `outlets.outlet_type, authority_score` | existing | Tier of outlets joining a story |

### Writes
Nothing during render. Could optionally write a `daily_diff_snapshot` row for historical analysis, but defer to v2.

---

## Backend — new endpoint

### `GET /api/narrative-diff?window=24h|7d`

Returns JSON shaped to drive the UI. Computation is read-only and pure-SQL where possible.

```typescript
interface NarrativeDiff {
  window_start: string  // ISO datetime
  window_end: string    // ISO datetime — typically now
  
  // ── Diff sections ──
  new_narratives: Array<{
    id: number
    name: string
    description: string | null
    owner_type: 'candidate' | 'opponent' | 'media'
    promoted_at: string  // ISO
    article_count: number  // articles in this narrative since promotion
    outlet_count: number
  }>
  
  stage_changes: Array<{
    frame_id: number
    frame_name: string
    owner_type: string
    from_stage: string
    to_stage: string
    transitioned_at: string
    trigger_summary: string  // human-readable: "13 articles this week vs baseline 1/week"
    metrics: object  // raw metrics_snapshot JSON
  }>
  
  momentum_spikes: Array<{
    frame_id: number
    frame_name: string
    owner_type: string
    articles_today: number
    articles_yesterday: number
    velocity_ratio: number  // today / yesterday baseline
    new_outlets: Array<{
      name: string
      tier: string
      first_seen_at: string
    }>  // outlets that joined this narrative in the window
  }>
  
  going_dormant: Array<{
    frame_id: number
    frame_name: string
    owner_type: string
    last_article_days_ago: number
    last_article_at: string  // ISO
    last_article_title: string
  }>
  
  resurrected: Array<{
    frame_id: number
    frame_name: string
    owner_type: string
    days_dormant: number
    new_article_count: number
  }>
  
  // ── Pulse summary ──
  pulse: {
    total_articles_window: number
    total_articles_prior_window: number  // for delta calc
    relevant_articles_window: number
    relevant_articles_prior_window: number
    
    head_to_head_articles: number  // candidate_mentioned + opponent_mentioned both true
    
    top_issue_window: {  // issue with most mentions
      name: string
      mention_count: number
    } | null
    
    sentiment_shifts: {  // GDELT tone snapshots
      candidate: { current_avg: number, prior_avg: number, delta: number }
      opponent: { current_avg: number, prior_avg: number, delta: number }
    } | null
    
    top_outlets_window: Array<{
      name: string
      tier: string
      article_count: number
    }>  // top 5 outlets by volume in window
  }
}
```

### SQL queries (rough sketch)

```sql
-- new_narratives: candidate_frames promoted in window
SELECT nf.id, nf.name, nf.description, nf.owner_type, cf.resolved_at,
       (SELECT COUNT(*) FROM frame_cluster_matches WHERE frame_id = nf.id) as fcm,
       (SELECT COUNT(DISTINCT story_cluster_id) FROM frame_cluster_matches WHERE frame_id = nf.id) as clusters
FROM narrative_frames nf
JOIN candidate_frames cf ON cf.resolved_to_frame_id = nf.id
WHERE cf.resolved_at >= :window_start AND nf.source = 'llm'
ORDER BY cf.resolved_at DESC;

-- stage_changes: any transitions in window
SELECT fsh.frame_id, nf.name, nf.owner_type, fsh.from_stage, fsh.to_stage,
       fsh.transitioned_at, fsh.metrics_snapshot
FROM frame_stage_history fsh
JOIN narrative_frames nf ON nf.id = fsh.frame_id
WHERE fsh.transitioned_at >= :window_start AND nf.active = 1
ORDER BY fsh.transitioned_at DESC;

-- momentum_spikes: frames with articles_today >= 2 * articles_yesterday
-- (computed in Python from the article_velocity stored in momentum_data)

-- going_dormant: frames whose last FCM is N days old, where N just crossed 30
SELECT nf.id, nf.name, nf.owner_type,
       julianday('now') - julianday(MAX(fcm.last_seen_at)) as days_quiet,
       MAX(fcm.last_seen_at) as last_article_at
FROM narrative_frames nf
LEFT JOIN frame_cluster_matches fcm ON fcm.frame_id = nf.id
WHERE nf.active = 1
GROUP BY nf.id
HAVING days_quiet BETWEEN 30 AND 31  -- just crossed threshold today
ORDER BY days_quiet ASC;

-- resurrected: frames where last_known_stage was 'dormant' and there's a new FCM in window
-- (frame_stage_history captures this when momentum classifier runs)

-- pulse stats: straight aggregations on source_items + issue_mentions
```

### Caching
- Compute on-demand for 24h window — small volume, fast (<2s)
- Cache for 5 minutes (data refreshes on each ingestion cycle)
- Optional: schedule a nightly job at midnight UTC that pre-computes the prior day's diff and stores in a cache table for "what was Tuesday's diff?" queries

---

## Frontend — new page

### Route: `/diff` (also linked from Briefing page header)

### Layout

```
┌─ DAILY DIFF — Updated 2 min ago ────── [24h ▼] [Refresh] [Email me] ┐
│                                                                       │
│  Past 24h vs prior period                                            │
│  ─────────────────────────────────                                   │
│  📊 49 relevant articles ingested  (vs 59 yesterday, ↓17%)          │
│  🤝 4 head-to-head articles  (vs 2 yesterday)                       │
│  💬 Top issue: Corruption & Ethics (12 mentions)                    │
│  💟 Sentiment: Cognetti +0.18 ↑, Bresnahan -0.34 ↓                 │
│                                                                       │
├─ 🟢 NEW NARRATIVES (1) ──────────────────────────────────────────────┤
│                                                                       │
│  "Bresnahan's Helicopter Ownership" · 3 articles · 3 outlets         │
│  Promoted 6h ago from candidate suggestions                          │
│  [View narrative]                                                     │
│                                                                       │
├─ 🔥 STAGE PROMOTED (2) ──────────────────────────────────────────────┤
│                                                                       │
│  Bresnahan's Healthcare Record  mainstream → resurfacing             │
│  Trigger: 13 articles this week vs baseline 1/week                   │
│  [View narrative]                                                     │
│                                                                       │
│  Bresnahan Delivers District Funding  spreading → mainstream          │
│  Trigger: 80 total articles, 16 in last 30d                          │
│                                                                       │
├─ 📈 MOMENTUM SPIKES (3) ──────────────────────────────────────────────┤
│                                                                       │
│  Cognetti's Anti-Corruption  · +4 outlets in 24h                     │
│  New outlets: Spotlight PA, PA Capital-Star, Politico, The Hill      │
│  [View narrative]                                                     │
│                                                                       │
│  [other spikes...]                                                   │
│                                                                       │
├─ 💤 GOING DORMANT (1) ───────────────────────────────────────────────┤
│                                                                       │
│  Healthcare Debate · 96 days since last article                     │
│  Last seen: "ACA debate continues in Congress" - Feb 18              │
│  [Auto-archive]  [Manually resurrect]                                │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

### Components

```typescript
// New page component
function DailyDiff() {
  const [window, setWindow] = useState<'24h' | '7d'>('24h')
  const [data, setData] = useState<NarrativeDiff | null>(null)
  const [loading, setLoading] = useState(true)
  
  useEffect(() => {
    api.narrativeDiff(window).then(setData).finally(() => setLoading(false))
  }, [window])
  
  if (loading) return <Skeleton />
  if (!data) return <EmptyState />
  
  return (
    <div>
      <DiffHeader window={window} onWindowChange={setWindow} data={data} />
      <PulseSummary pulse={data.pulse} />
      
      {data.new_narratives.length > 0 && (
        <Section title="🟢 New Narratives" items={data.new_narratives} renderer={NewNarrativeCard} />
      )}
      
      {data.stage_changes.length > 0 && (
        <Section title="🔥 Stage Promoted" items={data.stage_changes} renderer={StageChangeCard} />
      )}
      
      {data.momentum_spikes.length > 0 && (
        <Section title="📈 Momentum Spikes" items={data.momentum_spikes} renderer={SpikeCard} />
      )}
      
      {data.going_dormant.length > 0 && (
        <Section title="💤 Going Dormant" items={data.going_dormant} renderer={DormantCard} />
      )}
      
      {data.resurrected.length > 0 && (
        <Section title="⚡ Resurrected" items={data.resurrected} renderer={ResurrectedCard} />
      )}
    </div>
  )
}
```

Each "Card" component is a small reusable that renders one diff item with appropriate UX (e.g., StageChangeCard shows the stage chip with a → arrow).

### Top nav addition

`Layout.tsx` NAV array gets a new entry:
```typescript
{ to: '/diff', label: 'Diff', badge: 'NEW' }  // badge removable after 2 weeks
```

Position: after "Briefing", before "Analytics" (Diff is the next-day-equivalent of Briefing).

### Empty states

- No diff for window: "No meaningful changes since [timestamp]. The race is quiet."
- API error: "Couldn't compute the diff. Last cached: [time]. [Retry]"
- First-time use: "Welcome! You'll see narrative changes here once we have 2+ days of data."

---

## Edge cases

1. **Brand-new install** — no prior period to compare. Show "Tracking started [date], diffs available starting [date+1]."
2. **Multiple stage transitions for same frame in window** — show the most recent, mention "+ N other transitions earlier in window" if any.
3. **Frame promoted AND already had a stage change** — appear in BOTH "new" and "stage changes" sections. Dedupe by showing only in "new" (it's a new frame, the stage is implicit).
4. **Going-dormant edge:** a frame at exactly 30 days quiet → appears once in "going dormant", then next day disappears (already shown). Track via `frame_stage_history` instead of computing on-the-fly to avoid this churn.
5. **GDELT tone snapshots not available for the window** — `sentiment_shifts: null`, UI handles gracefully ("Sentiment data unavailable for this window").
6. **Issue mention counts are off** — issue_mentions table is updated on extraction; if rescore hasn't run on recent items, may undercount. Note in UI: "Issue counts reflect scored articles only."

---

## Email version (optional, v2)

Daily Diff email at 7 AM ET. Plain HTML, brand colors. Same structure as the page, optimized for mobile reading.

### Backend
- Cron job in scheduler.py
- Calls the same `/api/narrative-diff` endpoint
- Renders via a Jinja template (`templates/daily_diff_email.html`)
- Sends via SMTP (or transactional email provider — SendGrid, Resend, Postmark)

### User config
- "Email me the daily diff" toggle on Setup page
- Time preference (default 7 AM ET)
- Window preference (default 24h)

### Tech notes
- Email isn't strictly necessary for v1; the page-view version is enough
- Add when you have 2+ users (so 1 person isn't reading from 2 places)
- Resend (resend.com) is the easiest dev option — generous free tier, simple API

---

## Acceptance criteria

For v1 (page only, no email):

- [ ] `GET /api/narrative-diff?window=24h` returns a valid NarrativeDiff JSON
- [ ] `GET /api/narrative-diff?window=7d` works with longer window
- [ ] Page renders without errors with empty/partial data (no diff items, no sentiment data, etc.)
- [ ] Stage changes show correct from→to and human-readable trigger reason
- [ ] Momentum spikes correctly identify new outlets (outlets whose first FCM for this frame is in the window)
- [ ] "Going dormant" only shows frames that JUST crossed 30 days (not ones that have been dormant for a year)
- [ ] Resurrection detection works (test: manually fudge a `last_known_stage` to 'dormant', wait, observe)
- [ ] Browser-back from a narrative detail returns to the same diff state
- [ ] Page loads in <2s with current data volume (15 frames, ~1200 FCM rows)
- [ ] "24h" / "7d" toggle works without page reload
- [ ] All section headers + emoji render consistently across Chrome / Safari / Firefox

For v2 (email):
- [ ] User can enable email in Setup
- [ ] Email arrives daily at chosen time
- [ ] Unsubscribe link works
- [ ] Email body works on mobile (320px width minimum)

---

## Implementation estimate

| Phase | Effort |
|---|---|
| Backend endpoint + SQL queries | 4 hrs |
| Cache layer | 1 hr |
| Frontend page + 5 card components | 4 hrs |
| Edge-case handling + tests | 2 hrs |
| Email version (v2) | 4 hrs |
| **v1 total** | **~11 hrs** |
| **v1 + v2 total** | **~15 hrs** |

Recommend ship v1 first, gather user feedback for a week, then add email.

---

## Open questions for you

1. **24h or 7d default?** I went with 24h (matches daily-cadence rhythm). Could argue 7d is more useful for catching slower-moving shifts.
2. **Should "stage demotions" show in the Stage Changes section?** (e.g., mainstream→fading). I included them implicitly via `frame_stage_history`. Could also be its own "Cooling Off" section.
3. **Anything from `frame_variants` worth surfacing as a diff item?** New variant emerged could be a "narrative evolution" signal. Probably v2.
4. **Should the "head_to_head_articles" count in pulse link out to a filtered article list?** Yes, I'd argue, since the head-to-head set is so high-value. Add `Link to Dashboard filtered view`.
