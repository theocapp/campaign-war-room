# Frontend UX & Copy — concrete improvements

Read-only walk through the 9 pages + Layout. Three categories:
1. **Copy** — labels that are jargon or unclear
2. **Information density** — places where the design under-uses or over-uses screen space
3. **Interaction** — places where the user has to do too much manual work

Each suggestion has before/after where possible.

---

## Copy — labels & wording

### Critical: "Narrative Frame" is product-internal jargon

`Frame` and `Frames` appear everywhere in the UI:
- Top nav: "Narratives" (good)
- Page titles: "Edit frame", "Frame name", "Add frame"
- Toolbar buttons: "Suggest frames"

**Problem:** Outside-the-product reader has no idea what a "frame" is. Internally it's a narrative-matching object; to a user it's a narrative/storyline/talking-point.

**Suggested change:** Drop "frame" entirely from user-facing copy. Replace with `narrative` or `storyline` everywhere:

| Current | Suggested |
|---|---|
| "Edit frame" | "Edit narrative" |
| "Frame name" | "Narrative name" |
| "Add frame" | "Add narrative" |
| "Suggest frames" | "Suggest narratives" |
| "Match articles to narrative frames" (pipeline) | "Connect articles to narratives" |
| "Promote into tracked frames" | "Track this narrative" |
| "AI noticed N emerging narratives" (banner) | already good ✓ |

Database column names (`narrative_frames`, `frame_id`, etc.) can stay; that's developer-facing.

### Stage labels — partially good, partially confusing

Current values: `emerging, spreading, resurfacing, active, mainstream, fading, dormant`

Issues:
- "Mainstream" — good, means "widely covered"
- "Spreading" vs "Active" — what's the difference? Both sound like "currently happening"
- "Resurfacing" — good
- "Fading" — clear
- "Dormant" — clear

**Suggested change:** Consolidate `active` → `spreading` and add a brief description tooltip on each stage chip:

```
Stage              Definition (tooltip on hover)
─────────────────────────────────────────────────────
emerging          First 1-2 weeks of coverage
spreading         Multiple outlets, gaining velocity
mainstream        Widely covered by tier-1 outlets
resurfacing       Returned to coverage after a quiet period
fading            Coverage declining week-over-week
dormant           No coverage in 30+ days
```

The deduplication change (`active` → `spreading`) requires backend change too — defer if scared, do as part of copy pass if you're going to touch stage logic anyway.

### Dashboard column headers — abbreviations save 4px but cost clarity

Current row labels in the per-narrative card:
```
METRIC     TOTAL    1W
Articles   227      3
Outlets    18       3
Reach      45K      2K
```

`1W` is ambiguous. Could be "1 week", "1st week", "week 1"... 

**Suggested:** Change to "7-DAY" or "THIS WEEK" (slightly longer but unambiguous).

Better yet: add a small "vs last" delta column header:
```
METRIC      TOTAL    7-DAY   vs LAST
Articles    227      3       ↑12%
Outlets     18       3       ↓5%
Reach       45K      2K      →
```

The arrows are already there; just clarify what they're comparing.

### Owner type labels — sloppy

Current chips on narrative cards: `Candidate / Opponent / Media`

Issues:
- "Candidate" is ambiguous — your candidate or any candidate?
- "Opponent" — same
- "Media" — too generic

**Suggested:** Use the actual names:
```
Favors Cognetti      (instead of Candidate)
Favors Bresnahan     (instead of Opponent)
Media frame          (instead of Media)
```

This is already done on the Narratives.tsx column headers ("Favors Cognetti", "Favors Bresnahan") — extend it consistently to ALL chips, cards, and filter menus.

### Pipeline step labels

Current:
- "Load historical articles"
- "Discover new RSS sources"
- "Score articles with AI"
- "Match articles to narrative frames"

Issues with #4: "narrative frames" jargon, and "match" is ambiguous (match to what?).

**Suggested:**
- "Load historical coverage" (more familiar than "articles")
- "Discover new sources" (drop "RSS" — user doesn't care which protocol)
- "Score relevance with AI" (more specific about what scoring means)
- "Connect coverage to narratives" (drops jargon + clearer about direction)

### Review queue — clearer empty state

Currently when the queue is empty it says... I'd need to check, but the typical pattern is "Nothing to review!" or just blank.

**Suggested:** Make it celebratory + actionable:
```
🎉 Inbox zero!

  You've reviewed all 23 articles from this morning's ingest.
  Next ingestion in ~15 min.
  
  [Browse archive]  [Adjust filters]
```

### Specific labels I'd kill

- "section-label" — internal class name leaking into UI (verify it's not visible)
- "btn btn-ghost" / "btn btn-primary" — same; these are class names, not labels

---

## Information density

### Dashboard — too much vertical scroll for the 3 columns

The 3-column layout (filters / narratives / watchlist) is good in concept but each column scrolls independently and the narrative cards are tall. On a typical 1440px monitor:
- Filters column: ~12 narratives visible
- Narratives column: ~3 narratives visible (each card is ~400px tall)
- Watchlist column: ~8 articles visible

**Suggested:**
- **Compact card mode toggle** at the top: switch between rich cards (current, 400px) and compact rows (60px showing just name, stage chip, 7-day count).
- Default to compact for known narratives, rich for emerging ones.

### Sparkline charts — too small to read

70px tall sparklines at the bottom of each narrative card show the right shape but you can't quickly read "is it going up or down vs last week."

**Suggested:** Either:
- Add a colored background — green if trending up vs last week, red if down. The shape becomes secondary to the color.
- Or add a small "↑3 articles this week" label overlay.

### Briefing page — missing structural hierarchy

The Morning Briefing is a wall of text with a few sections. After Session 3 it now correctly shows race_memo + new_articles + spike_alerts. But:

- The race memo (LLM-generated paragraphs) is the most important content but doesn't visually stand out
- The narrative_pulse cards are useful but cramped
- spike_alerts are buried at the bottom

**Suggested layout:**
```
┌─ TODAY'S TOP STORY ──────────────────────────────────────┐
│  [Single LLM-generated card, hero treatment, "what to    │
│   know in one sentence" pulled from race_memo]           │
└───────────────────────────────────────────────────────────┘

┌─ ⚠ SPIKE ALERTS (3) ─────────────────────────────────────┐
│  • Bresnahan's Healthcare Record — 2.7× normal volume    │
│  • [other spikes]                                        │
└───────────────────────────────────────────────────────────┘

┌─ FULL RACE MEMO ──────────────────────────────────────────┐
│  [LLM paragraph, current treatment]                      │
└───────────────────────────────────────────────────────────┘

┌─ NARRATIVE PULSE ─────────────────────────────────────────┐
│  [Current cards, slightly larger]                        │
└───────────────────────────────────────────────────────────┘

┌─ NEW ARTICLES (47) ──────────────────────────────────────┐
│  [Current treatment]                                     │
└───────────────────────────────────────────────────────────┘
```

Inverted pyramid: most important + most actionable at the top.

### Narratives page — pending suggestions section format

The Pending Suggestions cards (built Session A) are grid-based but the grid wraps strangely on smaller widths. Each card has the same width but variable height.

**Suggested:** Use masonry-style layout (CSS columns or react-masonry-css). Same visual feel, no awkward gaps.

### Review queue cards — too much chrome per card

Each review queue card has ~5 metadata rows (relevance, urgency, sentiment, etc.) before the title. Title is the most important; bury metadata.

**Suggested compact layout:**
```
┌─────────────────────────────────────────────────────────┐
│  Article title here, full visibility                    │
│  Times-Tribune · 2h ago · score 87 · negative           │
│  [▼ Why was this flagged?]  [Read] [Dismiss] [Track]   │
└─────────────────────────────────────────────────────────┘
```

vs current:
```
┌─────────────────────────────────────────────────────────┐
│  RELEVANCE: critical                                    │
│  URGENCY: high                                          │
│  SENTIMENT: negative                                    │
│  PUBLISHED: 2h ago                                      │
│  Article title here                                     │
│  source name                                            │
│  [Buttons]                                              │
└─────────────────────────────────────────────────────────┘
```

Same data, much less scrolling.

---

## Interaction

### Filters don't persist between page visits

User opens Narratives, applies "owner: opponent, stage: emerging" filters, navigates to Briefing, comes back to Narratives — filters reset.

**Suggested:** Store filter state in URL query params (e.g., `/narratives?owner=opponent&stage=emerging`). Browser back/forward works correctly. Bookmarkable. Easy to add.

### "Suggest narratives" button is permanently visible — risky

The big yellow "Suggest narratives" button on the Narratives toolbar fires an LLM job that takes time + costs money. User could accidentally click it multiple times.

**Suggested:**
- Add a confirmation dialog: "This runs an AI analysis (~2 min). Continue?"
- Disable the button while a suggest job is in progress (poll status from backend)
- Show last-run timestamp: "Last suggested 3 hrs ago"
- Better: replace with the existing "AI noticed N emerging narratives" banner — it does the same thing automatically. The button is mostly a foot-gun.

### Dashboard refresh — silent

The Dashboard auto-refreshes every 60s but there's no visible indicator. User can't tell if the data is fresh.

**Suggested:** Subtle "Updated 12s ago" in the top-right of each column. Pulses on refresh.

### Review queue — no bulk actions

Reviewing 50+ articles one at a time is slow. The current "dismiss" / "reviewed" buttons are per-article.

**Suggested:**
- Shift-click to select range
- Bulk actions: "Dismiss all" / "Track all" / "Apply category" 
- A "select all visible" + "dismiss all visible" combination would let you sweep 200 noise articles in one click

### Frame stage chips — not clickable

Stage chips on narrative cards (`emerging`, `mainstream`, etc.) are just labels. Clicking them does nothing.

**Suggested:** Make them clickable filters — click "mainstream" on one card → Narratives page shows only mainstream narratives.

### Setup page — too long for first-time

The Setup page is a 381-line component. First-time user has to:
1. Enter candidate info
2. Add opponent(s)
3. Add narrative frames
4. Add monitors / sources

All on one page. Overwhelming.

**Suggested:** Convert to a step-based wizard:
```
Step 1 of 4 — Your campaign
Step 2 of 4 — Your opponents
Step 3 of 4 — What's already being tracked? (suggested narratives)
Step 4 of 4 — Where to monitor? (suggested feeds based on state)
```

Each step has a "back" + "next" button. Once setup, the page becomes a settings view.

### Narrative card click target

Currently the entire card might or might not be clickable (need to check). Clearer interaction:
- Card body click → opens narrative detail
- Edit pencil icon → opens edit modal
- Stage/owner chips → filter on click

### Dark mode is the only mode

The product is dark-mode-only. Most users tolerate that, but some prefer light. Not urgent, but worth thinking about for productization (some campaign offices have bright lighting and dark-mode reads poorly).

---

## Microcopy nits

| Location | Current | Suggested |
|---|---|---|
| Narratives toolbar | "Clear" (filter clear button) | "Reset filters" |
| Narratives | "Suggest frames" | Remove entirely (see above) |
| Briefing page | "Race memo" | "Today's race recap" |
| Dashboard right rail | "Recent articles" | "New coverage" or "Just in" |
| Layout pipeline banner | "Pipeline active" | "Updating data..." |
| Review queue empty state | (whatever it currently is) | See above suggestion |
| Setup | "Save" | "Save campaign" / "Save opponent" — be specific |

---

## What's already good (don't change)

- The Layout top nav — clean, scannable, the queue badge is well-done
- DDHQ-inspired dark theme — distinctive, signals "professional intelligence tool"
- Golden yellow accent for important info — works well
- Narrative card structure (name → description → stage chip → metrics → sparkline) — solid information hierarchy
- The 3-column dashboard concept — good, just needs density tuning
- The pipeline status banner — informative, gives visibility into long-running jobs
- The "AI noticed N emerging narratives" banner — well-designed, the new error state from Session A is appropriate
