# Campaign War Room — Roadmap

> Read PRODUCT_BRIEF.md first. That file defines what we're building and why.
> This file tracks what to do and in what order.
> Check off items as they're done. Add notes when things change.

---

## Phase 1: Get Real Data Flowing
**Goal: 20+ relevant articles per day about Cognetti and PA-08**
**Do this before any code changes. Nothing else matters without data.**

### 1a. Add Google News RSS feeds (do this in the UI → Sources → Add RSS Feed)

These are free, no API key, live feeds of everything published about your search terms:

```
https://news.google.com/rss/search?q=Cognetti+Pennsylvania&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q="PA-08"+Pennsylvania&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q=Lackawanna+County+politics&hl=en-US&gl=US&ceid=US:en
https://news.google.com/rss/search?q=Scranton+politics+2025&hl=en-US&gl=US&ceid=US:en
```
Add opponent name once you know it:
```
https://news.google.com/rss/search?q=[OPPONENT+NAME]+Pennsylvania&hl=en-US&gl=US&ceid=US:en
```

### 1b. Add local Pennsylvania news RSS feeds

```
https://www.thetimes-tribune.com/search/?f=rss    (Scranton Times-Tribune)
https://www.citizensvoice.com/search/?f=rss        (Citizens' Voice, Wilkes-Barre)
https://www.pahomepage.com/feed/                   (WBRE/WYOU local TV news)
https://www.politicspa.com/feed/                   (Pennsylvania political news)
https://www.penncapital-star.com/feed/             (PA Capital-Star, Harrisburg)
https://www.inquirer.com/feeds/rss/                (Philadelphia Inquirer PA politics)
```

Note: Some of these RSS URLs may need to be verified — paste them into a browser to confirm
they return XML before adding to the app.

### 1c. Verify it's working

After adding feeds:
- Trigger a manual ingest (Sources page → Refresh)
- Check that articles appear in the Review Queue
- Look at what's coming in — is it actually about Cognetti/PA-08?
- If national noise is dominating, that's a signal problem (Phase 2 will address this)

**Phase 1 is done when:** You're seeing 10-20 articles per day that are at least tangentially
related to your race, flowing automatically.

---

## Phase 2: Fix the Analysis Pipeline
**Goal: Each article gets one focused Groq call that correctly judges race relevance**
**The current pipeline makes 4-6 LLM calls per article. We replace it with 1.**

### What changes

Replace the multi-step analysis in `backend/app/services/ingestion.py` with a single Groq call
that answers everything at once:

```
Given this campaign context:
- Candidate: Cognetti, running for PA-08 congressional seat
- Location: Scranton / Lackawanna County, Pennsylvania
- Key issues: [from campaign config]
- Opponent: [name]

Article: [title + first 600 words]

Return JSON:
{
  "relevant": true/false,
  "relevance_score": 0-100,
  "one_sentence": "what happened, politically, in one sentence",
  "framing": "helps_candidate | hurts_candidate | opponent_attack | neutral | background",
  "needs_attention": true/false,
  "reason": "brief explanation of relevance judgment"
}
```

### What gets removed
- `issue_clustering.py` keyword matching (replaced by the single LLM call)
- `opponent_analysis.py` sentence splitting (replaced by framing field)
- The KG pipeline call from ingestion (already removed in previous session)
- Multiple separate LLM calls for summarize, classify, urgency, issues, opponent

### What gets kept
- SourceItem table (core storage, keep as-is)
- Race relevance score (keep field, just set it from the new call)
- Review queue (keep)
- Talking points (keep, it's valuable)

**Phase 2 is done when:** One article flows through ingestion, gets one LLM call,
and shows up in the review queue with a correct relevance score and one-sentence summary.

---

## Phase 3: Campaign-Defined Narrative Tracking
**Goal: Campaign defines 3-5 narratives they're tracking; system shows evidence per narrative**

### The concept

Instead of auto-discovering narratives, you define them:
- "Cognetti - Economic Security" (your message)
- "Cognetti - Healthcare Access" (your message)
- "Opponent - Crime and Safety" (their attack)
- "Opponent - Soft on Border" (their attack)

The system then:
1. Shows articles that relate to each narrative (LLM matches or human-tagged)
2. Shows a count per week (is this narrative getting more or less coverage?)
3. Shows who's amplifying it (which outlets, which sources)

### What changes

Simplify the `narratives` table:
- `id`, `name`, `description`, `owner` (candidate/opponent/media), `active`

Simplify `narrative_mentions`:
- `narrative_id`, `source_item_id`, `confidence` (0-100), `added_by` (human/llm), `added_at`

New UI: Narratives page shows each defined narrative with:
- Mentions this week vs. last week
- 3 most recent supporting articles
- Simple trend arrow (up/down/flat)

**Phase 3 is done when:** You can add a narrative, ingest some articles, and see which
articles the system matched to that narrative (with human ability to confirm/reject).

---

## Phase 4: Morning Briefing View
**Goal: The primary view a staffer opens every morning**

A single page that shows:
1. **New since yesterday** (3-5 cards): most relevant articles from last 24 hours
   - Headline, one-sentence summary, framing label, source
2. **Narrative pulse** (counts): for each tracked narrative, this week vs. last week
3. **Anything flagged** (needs_attention=true): articles that might need a response

That's it. Simple. Fast to scan. No charts, no graphs, no complexity.

**Phase 4 is done when:** A campaign staffer can open this page, read it in 2 minutes,
and know what happened overnight related to their race.

---

## Phase 5: Polish and Test with a Real Campaign
**Goal: Get feedback from someone actually running a campaign**

- Clean up any rough edges from Phases 1-4
- Make sure Google News RSS feeds work reliably
- Add the ability to export the narrative summary as a PDF or email
- Get 1-2 campaigns to actually use it for a week
- Fix whatever they say is wrong

---

## What Is NOT on This Roadmap

These were built and are being removed or ignored:
- Knowledge Graph system (delete knowledge_graph/ directory — Phase 2)
- Cosine similarity clustering
- Canvassing/voter contact (separate product idea, defer indefinitely)
- Multi-LLM provider abstraction (just use Groq)
- Meltwater-style media monitoring at scale

---

## How to Work on This

### Your tools

| Tool | What it's for | How to use it |
|---|---|---|
| **Claude Code** (this) | All coding, architecture, debugging | Start each session by saying "read PRODUCT_BRIEF.md" |
| **Groq** | LLM API calls inside the app | Already configured in .env |
| **Google News RSS** | Free news feed for any search term | Paste URLs directly into Sources page |
| **GitHub** | Version control, don't lose work | Commit after every phase completes |

### Don't use ChatGPT for coding on this project
It doesn't have context of what's been built and will suggest things that contradict
decisions already made. Use it for general questions (what is Meltwater?, how does RSS work?)
but not for code or architecture on this codebase.

### Start every Claude Code session like this
```
I'm working on Campaign War Room. Read PRODUCT_BRIEF.md.
Currently on Phase [X]. Today I want to [specific task].
```

### At the end of every session
Update the "Current Phase" section in PRODUCT_BRIEF.md.
Commit your changes to git: git add -A && git commit -m "what you did"

---

## Current Status

- [x] Backend and frontend built and running (frontend-v2 on port 5174)
- [x] RSS ingestion working
- [x] Groq + OpenAI configured; `get_judge_provider()` routes prose/judgment to OpenAI gpt-4o-mini
- [x] KG narrative projection removed (previous session)
- [x] **Phase 1: Real data flowing** — RSS + Google News + GDELT realtime + GDELT BigQuery backfill + Reddit + Bluesky
- [x] Phase 2: Single-call per-article scoring (v2 prompt with per-claim extraction, quote verification, owner_type rules)
- [x] Phase 3: Campaign-defined narrative tracking — LLM frame suggestion + per-article candidate frames + HDBSCAN variant clustering
- [x] Phase 4: Morning briefing view (uses `get_judge_provider()` for the race-situation memo)
- [ ] Phase 5: Test with real campaign — in progress (Cognetti PA-08 is the live test)

---

## Planned — Engineering follow-ups (from 2026-05-24 bug-hunt session)

Small, scoped items deferred because they aren't bug-fixes or blockers,
just incremental improvements. Pick off when bandwidth allows.

### ~~Wire `last_error` / `embeddings_available` into the Narratives banner~~ — DONE (Session A, 2026-05-24)
Implemented in `frontend-v2/src/pages/Narratives.tsx` + `api/client.ts`. The
`PendingSuggestionsSection` now renders a red diagnostic banner ("AI narrative
discovery is paused — quota recovered on next scheduled run") when
`last_error` is set, instead of silently hiding the section.

Done as part of the larger embeddings rework that added OpenAI fallback +
in-process embedding cache + `EmbedStats` observability — see
`services/embeddings.py` for the full architecture.

### Expand the owner_type inversion heuristic word-list
**Effort:** ~30 min + tests
**Context:** `services/owner_type_correction.py` catches the LLM's common
candidate↔opponent mistake by matching attack VERBS / NOUNS / PHRASES
against the frame name. The current word-list is good but not exhaustive
— observed misses in live data:
- "Bresnahan's Submission to Republicans" (tagged opponent, should be candidate)
- "Bresnahan supports ACA subsidies" (tagged opponent, should be candidate)
- "Bresnahan supports Democratic healthcare policies" (tagged opponent, should be candidate)

Patterns the heuristic doesn't yet cover:
- Possessive-noun + ideologically-loaded word: "X's submission/capitulation/surrender to Y"
- Bare-verb + "supports/backs/endorses [thing voters dislike]" — currently
  only catches "supported controversial" as a phrase; should generalize
  to detect ideological mismatch (Republican congressman + "Democratic policies"
  → attack from the right)

Two options:
1. **Expand word-list + add ideological-mismatch heuristic** (cheap, fragile)
2. **LLM 2nd-opinion on every owner_type assignment** (~$0.001 per check,
   accurate, slow). Could batch: 1 LLM call per ~20 candidate_frames.

For productization (other races), the ideological-mismatch detection
needs the candidate's party (which is in CampaignConfig already) so the
heuristic stays race-agnostic. "Republican supports Democratic policies"
generalizes correctly for any candidate.

### Audit other `SIMILARITY_THRESHOLD`-style constants
**Effort:** ~30 min
**Context:** Switched `candidate_frame_promoter.SIMILARITY_THRESHOLD` to
HDBSCAN (race-agnostic) on 2026-05-24. Other places that still use
hardcoded similarity numbers:
- `services/frame_variants.py:255` — `MERGE_RETRIEVAL = 0.85` (LLM prefilter,
  not a clustering decision, but worth verifying it works for any race)
- `services/narrative_frames.py` — check matcher uses any similarity thresholds
- Anywhere else `cosine_similarity` is compared to a constant

Each should be either: (a) replaced with HDBSCAN/LLM-based decision, or
(b) documented as a race-specific prefilter (not a final decision) with
a re-calibration plan if needed.

### Persist Tavily `_exhausted` set across process restarts
**Effort:** ~30 LOC backend
Currently `TavilySearchProvider._exhausted` is a process-level `set[str]`
that resets on every uvicorn reload. If a Tavily key 429'd late yesterday,
the next morning's first query re-tries that key and immediately fails
before rotating. Cheap fix: persist `{key_suffix: exhausted_until_ts}` to a
tiny JSON file in the same dir as `war_room.db`, load on `__init__`, write
on `mark_rate_limited()`. After a 24h cooldown the key auto-clears.

### Lower candidate-frame promoter thresholds (or surface borderline clusters separately)
**Effort:** ~10 LOC backend, OR ~150 LOC for the full "Raw Inbox" UI
**Context:** Session A fixed the silent failure that was zeroing out clustering.
With Gemini+OpenAI both working, the current thresholds (`MIN_CLUSTER_ROWS=3`,
`MIN_DISTINCT_ARTICLES=3`, `MIN_DISTINCT_OUTLETS=2` in
`services/candidate_frame_promoter.py`) produce 6 high-quality cluster
suggestions. Diagnostic run shows that lowering to `MIN_ROWS=2 + MIN_ARTICLES=2`
(keep `MIN_OUTLETS=2`) would surface ~16 clusters total — including some real
narratives we're missing today:
- "Bresnahan cuts public broadcasting funding" (2 outlets — Citizens' Voice, Times-Tribune)
- "Cognetti's stance on ICE" / "Immigration Position Clarity" (2 outlets)
- "Cognetti's Ethics Concerns" / "Ethical Concerns" (2 outlets)

Two paths, decide after a week of using the current 6:
1. **Quick** — lower thresholds. Risk: more low-confidence noise.
2. **Right** — build a "Raw Inbox" panel below the cards: collapsed-by-default,
   paginated table of the 175 single-source candidate_frames with
   per-row Promote/Dismiss + bulk-dismiss. Lets the user see + triage everything
   the LLM noticed, not just convergent clusters. Requires a new GET endpoint
   for the raw list + a POST dismiss endpoint (mark `resolved_at = now`).

The "Raw Inbox" was the original ask in Session A but deferred once the
clustering bug was found.

### Handle generic-surname candidates in `_name_tokens`
**Effort:** ~40 LOC backend + tests
**Context:** Session C (2026-05-25) tightened `race_relevance._name_tokens`
to return surname-only, eliminating false positives like "Rob Bonta" matching
"Rob Bresnahan". Works well for distinctive surnames (Bresnahan, Cognetti).

But for races with a common surname (Smith, Johnson, Brown, Garcia — anything
in the US top-100), surname-only matching over-fires: every article mentioning
any "Smith" trips `opponent_mentioned=True`, bypassing the prefilter and
burning LLM calls. The system still works correctly downstream (LLM rejects
most), but the LLM-waste savings disappear.

Two-part fix:
1. **At campaign setup** — check candidate/opponent surnames against a
   bundled top-500 US surnames list. If hit, mark the campaign as
   `common_surname=True` in CampaignConfig (new column).
2. **In `_contains_name`** — when `common_surname=True`, require BOTH
   surname AND (first name OR a title like "Rep."/"Sen."/"Mayor"/"Councilman"
   OR another known candidate's surname) to appear in the text. This catches
   "Rep. Smith announces..." while dropping "John Smith, plumber, said..."

Race-agnostic: the top-500 list is generic, the title regex is generic,
and the campaign flag is set once at setup. PA-08 (Bresnahan/Cognetti)
won't be affected.

### Fetch full article bodies for race-relevant items
**Effort:** ~2–3 days backend
**Status:** known, surfaced by v15.0 stage-1 audit on 2026-05-27
**Context:** On the v15.0 50-article stage-1 backfill, **30 of 50 highest-relevance
articles had `raw_text` < 500 chars** — essentially just the title repeated
as the body. The v15.0 verbatim-claim validator correctly refused to
extract quotes from these stubs (you can't quote text we don't have).
Under v14.x this problem was hidden because the triple extractor happily
fabricated relations from title-only text; under v15.0 the noise floor
shifts upstream and becomes visible.

**Why this matters for any campaign:** a "race-relevant" article that
yields zero claim_records is wasted ingestion. We're paying scoring +
storage cost on it but getting no entity-anchored evidence. At scale
(SaaS, thousands of races) this multiplies.

**What it looks like in the DB:**
```
SELECT count(*) FROM source_items
WHERE archived_as_irrelevant = 0
  AND race_relevance_score >= 50
  AND LENGTH(raw_text) < 500;
```
PA-08 shows roughly half of high-relevance items in this bucket today.

**Fix paths** (rough order):
1. Diagnose by ingestion source — which RSS feeds, which crawl paths,
   which Google-News-redirect failures are producing stubs? The
   redirect-URL resolver below (separate roadmap item) is part of this.
2. Where the source URL is fetchable, run a fallback full-page crawl
   (Readability + paywall fallbacks already exist in `services/ingestion.py`)
   to populate `raw_text` properly.
3. For RSS items where only `<description>` came through, augment with
   a follow-up GET on the article URL.

Holding off on a full corpus v15.0 backfill until at least the easy
wins are landed — otherwise we burn LLM budget on stub articles that
the validator will reject anyway.

### Resolve Google News redirect URLs to true publisher URLs
**Effort:** ~80 LOC backend + ~5s extra per RSS item
**Context:** Session B identified 948 articles unlinked from outlets because
they come from Google News with encoded redirect URLs like
`news.google.com/rss/articles/CBMIxxxx?oc=5` (~7% of remaining 4,409
unlinked). The outlet matcher can't extract a publisher domain from these.
Two ways to resolve:
1. **HTTP follow** — issue a HEAD request, capture the Location header,
   extract real publisher domain. ~1-2s per request. Add to ingestion
   pipeline after RSS parse but before outlet linking. Cheap & always
   works.
2. **Google News URL decode** — the `CBMIxxxx` portion is base64 protobuf
   that decodes to the original URL. No HTTP call needed. There's
   open-source decoders (e.g. `googlenews-tools` on pypi) but the format
   has changed twice in 2024-2025. Faster but fragile.

Path 1 is more reliable; tolerate the latency since RSS ingestion is
already async + scheduled. Also stash the resolved URL back in the
`source_items.source_url` so downstream features (article display, dedup)
benefit, not just the outlet matcher.

### Decide canonical RSS-tracking table: `rss_feeds` vs `source_monitors`
**Effort:** ~1hr discussion + ~100 LOC migration once decided
**Context:** Session B fixed the immediate symptom (51 source_monitors RSS
rows had stale NULL `last_checked_at`) by adding `mark_rss_feed_fetched()`
in `services/rss_ingestion.py` that writes to BOTH tables. But the deeper
issue is that the two tables overlap (51 of 111 rss_feeds also exist as
source_monitors). The "right" fix is to pick one canonical table and
deprecate the other. Two options:
- **Keep `rss_feeds`** as canonical, delete the 51 duplicate source_monitor
  rows, and rework the UI to show all 111 rss_feeds (not just the 51 that
  happen to be in source_monitors). Simpler.
- **Migrate everything to `source_monitors`**, copy the missing 60
  rss_feeds rows into source_monitors as monitor_type='rss', then drop
  rss_feeds table. More invasive but unifies feed/monitor concepts.

Deferred because the dual-write helper makes the symptom invisible.

### Move embedding cache to disk (survive uvicorn restarts)
**Effort:** ~50 LOC backend
Session A added an in-process LRU cache in `services/embeddings.py` that holds
text→3072-dim vector pairs to avoid re-embedding the same candidate_frame on
every refresh. The cache is `dict`-based and lost on uvicorn restart, which
means the first refresh after every `--reload` triggers a full re-embed run
(207 OpenAI calls today). Trivial to persist as JSON sidecar
(`backend/.cache/embeddings.json`) or as a tiny separate SQLite file. Even
simpler: pickle/shelve. Either way, keeps quota use minimal across deploys.

### Backend-side caching for Tavily Reddit results
**Effort:** ~50 LOC backend
The Reddit-via-Tavily ingest path runs every 30 min and queries the same
2 terms ("Paige Cognetti", "Rob Bresnahan") site-restricted to reddit.com.
Each query returns mostly the SAME 5-20 results as the previous one (Reddit
content doesn't churn that fast on small races). With 4×1000=4000 Tavily
calls/month and 96 calls/day going to Reddit alone, we're burning ~70% of
the budget on duplicate queries. Cache responses for 6h keyed on
`(query, days_back)`; serve from cache between scheduled runs. Net effect:
4-5 actual Tavily Reddit calls/day instead of 96.

---

## Planned — Product / "AI Campaign Staffer" expansion

### Morning Briefing — risk_warnings + suggested_actions sections
**Status:** placeholder fields existed on the frontend type, never implemented
backend-side. Removed from UI on 2026-05-24 (silent dead sections). Worth
re-adding when implemented as a real feature.

**Why this matters:** Per the brainstorm on 2026-05-23, the product reframing
target is "an AI campaign staffer that reads everything, briefs the team
daily, alerts on important events, and helps draft response language."
The Briefing page is the killer surface for this. Risk Warnings + Suggested
Actions are the two sections that turn the Briefing from "information
delivery" into "action engine" — the difference between "here's what
happened" and "here's what to do about it today."

**What the implementation looks like:**
- Add to `routes/dashboard.py:get_morning_briefing` a step that calls
  the judge LLM (gpt-4o-mini) ONCE with the assembled briefing context
  (today's articles + frame pulse + opponent activity) and prompts:
  > "Based on the last 24h, identify (a) 2-3 risks on the horizon worth
  > flagging to the campaign manager — these are *things to watch* not yet
  > breaking news, AND (b) 3-5 concrete actions for the next 24 hours
  > — what should the candidate post, what should the comms team draft,
  > what should staff call."
- Response fields: `risk_warnings: list[str]`, `suggested_actions: list[str]`
- Cache the result alongside the existing `race_memo` (briefing memo is
  already cached in `services/briefing_summary.py`).
- Re-enable the frontend sections in `pages/MorningBriefing.tsx` (delete
  the comment block where they were removed; restore the prior render
  code from git history).

**Scope estimate:** ~150 LOC + one new prompt + one additional gpt-4o-mini
call per briefing refresh (~$0.01/day per active campaign).

**Why NOT to ship this without thinking:** garbage suggestions will train
the campaign team to ignore the briefing entirely. The prompt needs
careful eval against real PA-08 history — would the model have suggested
useful actions on the days when something actually happened? Worth dry-
running on the last 30 days of data before deploying.

---

## Planned — Knowledge Graph evolution

These items came out of a 2026-05-26 external architecture review of the KG
layer. The pre-flight v14.6 backfill is fine to run today — but these are
medium-term redesigns that should land before a second campaign onboards on
the same system. Items are roughly ordered by "pain caused if deferred."

### Claims as assertion-level objects (not triple-based)
**Status:** known limitation, deliberate medium-term redesign
**Effort:** ~2–3 weeks focused work

**The problem:** Today a "claim" has natural key `(subject_id, predicate, object_id)`.
So `Bresnahan criticized ACA subsidies`, `Bresnahan criticized ACA expansion`,
and `Bresnahan criticized ACA implementation` all collapse into ONE claim row.
That destroys nuance, and it means contradiction/retract logic operates at
too coarse a granularity — one contesting article on the wrong sub-claim
poisons the whole thing.

**What changes:**
- `claims` natural key becomes a **semantic proposition** — quote-cluster or
  extracted-statement-unit — not a triple.
- `entity_relations` stays as the denormalized aggregation layer (don't
  collapse, ChatGPT was clear about this).
- New table `claim_signature` or analogous, computed from the LLM's
  `sample_quote` clustered by embedding similarity.
- `claim_supports.claim_id` re-points at the assertion-level claims.

**Why not now:** the current single-race volume (1,786 claims) isn't large
enough to feel this pain. Becomes acute around 3–5x that scale, or when
sub-claim nuance becomes a sales asset.

### Source-cluster-aware evidence weighting
**Status:** infrastructure present, not yet wired into counts
**Effort:** ~1 day

**The problem:** today `claim.supporting_count` and `entity_relations.weight`
count raw articles. 50 AP-syndicated republications = 50 supports. In the
PA-08 corpus this currently inflates by ~1.1x (small). For a national-noise
race it could 5–10x.

**What changes:**
- Replace article-count with `count(DISTINCT story_cluster_id)` in
  `/api/entity-network`, `/api/claims/{id}`, and the contradiction queue.
- `story_clusters` already exists (16,752 rows, 99.9% coverage); just JOIN
  through it.
- Add a "syndication inflation" badge in the UI when article-count ≫ cluster
  count for a claim (signals "this is one wire story").

**Why not now:** PA-08 doesn't currently suffer from this. Will become urgent
the moment the system processes a national race or a major-network event.

### Continuous consensus score (replace binary "contested" status)
**Status:** binary auto-flip works today, but is over-sensitive
**Effort:** ~1 day

**The problem:** one fringe article emitting `stance=contesting` flips a
50-supporter claim's status to "contested". The UI shows that as a yellow
warning pill, which trains users to ignore it.

**What changes:**
- Compute `consensus_score ∈ [0, 1]` per claim:
  `Σ(supporting confidence × outlet reliability × uniqueness)
   / Σ(all evidence × confidence × reliability × uniqueness)`
- Status bins: `active (≥0.85)` → `weakly_contested (0.5–0.85)` →
  `strongly_contested (<0.5)`.
- UI shows the score as a slider/meter, not a pill.
- Auto-flip becomes "drop a status band when the score crosses a threshold."

**Why not now:** binary works for the current low-volume corpus; the bug
manifests when contesting articles enter at meaningful volume (post-v14.6
backfill we'll have actual data to calibrate from).

### Entity dossier page UI
**Status:** force-directed graph is the only entity surface today
**Effort:** ~2–3 days

**The problem:** force-directed graphs are impressive but operationally weak
for analyst workflows. Real analysts want ranked evidence lists, timelines,
and "everything about X" pages — not a hairball.

**What changes:**
- New page at `/entity/:canonical_id`: header (name, role, affiliation,
  mention timeline), all relations with this entity as subject or object
  grouped by predicate, all narrative frames this entity appears in,
  contradiction history, retract trail.
- The current side panel becomes a peek; "View dossier" opens the full page.
- The Path finder stays — ChatGPT explicitly flagged it as genuinely useful.

**Why not now:** the graph + side panel covers ~80% of current analyst
intent. Dossier becomes essential when we hit ~10K+ relations or when a
power user spends more than 30 min/day in the tool.

### Observation vs inference separation
**Status:** known data-model collapse, low immediate impact
**Effort:** ~1 week

**The problem:** today "Bresnahan attended rally" (a direct observation) and
"Bresnahan supports policy" (an inference from his statements) are stored
identically as relations. Over time, inferences will contaminate observation
data — e.g. multi-hop traversal will mix observed facts with synthesized
opinion, and confidence won't decay correctly.

**What changes:**
- Add `evidence_type ∈ {observed, attributed, inferred}` to `claim_supports`.
- Update the extractor prompt to emit one of those three for each relation.
- Multi-hop / contradiction logic weights observation evidence higher.

**Why not now:** the current corpus is dominated by direct news reporting,
where the observation/inference distinction is mostly synonymous with the
predicate type (`attended` = observed, `endorses` = observed, `criticizes`
= attributed). Pain shows up when we add transcript ingestion or commentary.

### Probabilistic event reconciliation
**Status:** strict-gate version shipped at v14.5
**Effort:** ~3 days

**The problem:** today an event extraction needs `name + (date OR location)`
or it gets dropped. That's conservative — we lose mentions of unnamed events
("the Scranton event", "Tuesday's rally") that articles do refer to.

**What changes:**
- Replace hard gate with a scored merge: `event_type + normalized_title +
  time_window + location + participants` → similarity score → merge above
  threshold, store `possible_duplicate_of` for ambiguity.
- Vague events can land as low-confidence entities that get reconciled later
  when richer mentions appear.

**Why not now:** we genuinely don't know yet whether the strict gate causes
real loss — wait until the v14.6 backfill puts events into the corpus and
we can measure rejection rate.

### Declarative constraint engine (replace Python commonsense rules)
**Status:** 10 rules in Python; works fine at this scale
**Effort:** ~2 days

**The problem:** at 30+ commonsense rules (multi-race deployment) the
hand-written Python becomes unmaintainable.

**What changes:**
- YAML-driven rules engine. Each rule = `{predicate, subject_constraints,
  object_constraints, action}`. Compile to validators at startup.
- Per-race rule files override the base set.

**Why not now:** YAGNI at 10 rules in one file. Revisit when adding 20+ more.

---

## Planned — Infrastructure / Post-Campaign

### Migrate SQLite → Postgres
**Status:** planned, not started. **Do after the current race or during any natural quiet period — not mid-campaign.**

**Why:**
- **Proper datetime typing.** SQLite stores datetimes as TEXT and string-compares them; that's the root cause of the `T`-separator class of bugs (see the 2,754-row data migration around `cluster_writes.py`). Postgres has real `TIMESTAMP` columns — that whole bug class disappears structurally.
- **Concurrent writes.** SQLite serializes through one writer lock. Ingest + rescore + rematch + user requests all fight for it; the `max instances reached` scheduler warnings are partly that. Postgres handles this natively.
- **Real `ALTER TABLE`.** Adding a column/constraint is a one-line statement in Postgres. In SQLite we had to drop and recreate `google_trend_snapshots` to extend a unique constraint.
- **Native JSONB.** Several columns hold JSON-as-TEXT (`quality_reasons`, `trends_keywords`) and would become queryable in Postgres.
- **Multi-campaign / multi-tenant future.** SQLite is one-file, one-process. Any plan to run multiple campaigns simultaneously or move to a server requires Postgres.

**Scope estimate:** ~1–2 focused days. Most queries port via SQLAlchemy unchanged.

**Known migration touchpoints:**
- `app/db.py` — connection string + engine config
- `app/services/cluster_writes.py` — uses raw `INSERT ... ON CONFLICT(...) DO UPDATE` (works in both, but verify syntax parity)
- `app/scripts/recluster_backfill.py` — same pattern
- DB column types: SQLite is lax, Postgres strict — confirm `DateTime` columns receive `datetime` objects (not strings) everywhere
- Boolean `== True` filters with `# noqa: E712` — work in both, worth a sweep
- The T-separator data normalization is already done in the SQLite DB; export will be clean

**High-level steps:**
1. Spin up Postgres locally (Docker container is simplest)
2. Update `app/db.py` connection string
3. Run `Base.metadata.create_all()` against the new DB
4. Dump SQLite data → load into Postgres (`pgloader` handles SQLAlchemy schemas)
5. Run the full pipeline against the new DB; verify every endpoint
6. Fix any SQLite-specific SQL that surfaces (likely 2–5 spots)
7. Swap connection string in `.env`, archive `war_room.db`

---

## Deployment trajectory — own race → friends → SaaS

The current build is single-tenant by design. That's correct for now. The
intended trajectory is to grow into multi-deployment, then SaaS — but each
phase is only built when the previous one's pain points have revealed what's
actually needed.

### Phase 1 — own race (today)

Self-hosted, single tenant, single SQLite DB on Theo's machine. No changes
needed. The "any campaign" code we've already built (auto-discovery, adaptive
stage thresholds, BigQuery integration, monitor auto-prune) has already paid
most of the generalization tax.

### Phase 2 — helping friends' races (3–10 campaigns)

**Don't multi-tenant the code.** Spin up one independent deployment per friend.
Each friend gets:
- Their own server (Railway, Fly.io, Render — ~$10–20/mo each)
- Their own Postgres DB
- Their own LLM API keys + BigQuery service account (or Theo's, with reimbursement)
- A URL with HTTP basic auth so the public can't see their data

Prerequisites before starting Phase 2:
- **Postgres migration done.** Phase 2 with SQLite is workable but painful
  (separate `.db` files per friend, no real backup story).
- **A `./deploy-new-campaign.sh` helper** that automates pushing code to a
  fresh server + clicking through Setup. Target: 1 hour per friend.
- **Lock down admin endpoints.** Today every `/api/admin/*` route is
  unauthed — safe locally because uvicorn binds to 127.0.0.1 by default,
  but the moment you deploy to Railway / Fly / Render the same routes
  become public. Specific risks before deploy:
    * `POST /api/admin/reset-workspace` — wipes all data (gated only by a
      confirm-string guard)
    * `POST /api/admin/reanalyze-sources` — burns LLM tokens (confirm-string only)
    * `POST /api/admin/rescore-articles` — burns LLM tokens (no guard at all)
    * `POST /api/admin/discover-outlets` — burns LLM tokens (no guard at all)
    * `POST /api/admin/auto-review` — DB writes (no guard)
    * `POST /api/admin/rescore-stop` — can interrupt a long job (no guard)

  Minimum-viable lockdown for Phase 2: require a header that matches an
  `ADMIN_TOKEN` env var on every `/admin/*` route. ~20 lines of FastAPI
  middleware. Add confirm-string guards to the 4 endpoints that lack them
  at the same time. Frontend never calls these — no UI work needed.

Phase 2 work to actually deliver per friend:
1. Provision their server
2. Run the deploy helper
3. Hand them the URL

Phase 2 also acts as the requirements-gathering phase for Phase 3 — watch
what real friend-campaigns hit, where API quotas matter, what the UX gaps
are. That intel is what makes Phase 3 cheap when you get there.

### Phase 3 — SaaS / sellable product

Only build this when there's signal from Phase 2 that it's worth it.
Required work:
- Real multi-tenancy: `campaign_id` (or `tenant_id`) on every table,
  every query tenant-scoped
- Auth: pick Clerk / NextAuth / Auth0 — depends on stack at that point
- Self-serve onboarding wizard (sign-up → campaign creation → kicks off
  the same `/campaign/initialize` chain we already have)
- Per-tenant quota management for LLM API keys, GDELT polling, BigQuery scans
- Postgres at scale (already migrated by this point)
- Hosting, monitoring, observability

Effort estimate: ~2–3 weeks focused work. The generalization code is
already done — Phase 3 is purely the productization layer.

### Critical timing rule

The Postgres migration is **the bridge** between phases. Do it in a quiet
period (post-current-campaign or any pause before a friend onboards), NOT:
- During an active campaign — risky
- After friends have populated SQLite with their data — migration is harder
  with multiple DBs to coordinate

### What to NOT do now

- Refactor for multi-tenancy
- Add auth
- Build onboarding wizards
- Set up a SaaS dashboard

Doing any of these before Phase 2 means building blind. Wait until real
friend-campaigns reveal what's needed.
