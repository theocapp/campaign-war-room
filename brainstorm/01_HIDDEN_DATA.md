# Hidden Data — what we have but don't show

Read-only audit of 48 tables. Identified columns with real, useful data that the current UI never surfaces. Each item: what it is, sample numbers from your live DB, suggested UI surface.

---

## TL;DR — top 5 hidden goldmines

1. **`source_items.candidate_mentioned + opponent_mentioned`** — 105 articles mention BOTH candidates (all tagged critical). These are head-to-head coverage. There's no "Both" filter or view anywhere in the UI.
2. **`source_items.district_mentioned`** — only 140 articles mention PA-08 specifically. These are the most relevant in the database. No filter for them.
3. **`source_items.urgency`** — 756 HIGH urgency articles, 1,343 medium. Never shown.
4. **`source_items.gdelt_tone`** — JSON with `avg_tone, positive%, negative%, polarity, activity_density, word_count` per article. Completely unsurfaced.
5. **`frame_stage_history.metrics_snapshot`** — per-transition JSON with `art_total, art_this_week, art_last_30d, baseline_weekly, days_since_article`. Powers a "what triggered this stage change" tooltip we don't have.

---

## Full inventory

### `source_items` table — many unsurfaced columns

| Column | Distribution | Why it matters | Suggested surface |
|---|---|---|---|
| **`urgency`** | 11,118 low / 1,343 medium / **756 high** | Independent signal from race_relevance — "high urgency" can be irrelevant-to-race but still important (e.g., breaking news in the district) | Filter chip on review queue + a "high urgency" badge on cards |
| **`candidate_mentioned + opponent_mentioned`** | 105 BOTH / 420 Bresnahan only / 317 Cognetti only / 12,375 neither | The "BOTH" set is **always critical relevance** — these are direct head-to-head articles, the most strategically important coverage | Dedicated "Head-to-head" tab on dashboard. Sort by recency. |
| **`district_mentioned`** | Only **140 articles** mention the district | These are the truly local stories. Almost all from `Google News: PA-08 U.S. Representative` (27) + `Google News — PA-08 Cognetti Bresnahan` (23) | "Local coverage" filter chip. Boost in priority scoring. |
| **`priority_issue_mentioned`** | 110 articles | Articles that hit the issues you defined as priorities | "Priority issues only" filter. Pair with content_category. |
| **`content_category`** | irrelevant (10,489) / campaign (1,598) / **priority_issue (108)** / sports (460) / generic_crime (176) / entertainment (162) / food (136) / weather (88) | Category-level filtering. Currently nothing uses this. | Filter dropdown. Also: hide sports/entertainment/food/weather from review queue by default — that's 800+ rows of obvious noise |
| **`geo_relevance`** | none (12,031) / local (1,046) / **district (140)** | Geographic ranking signal | Filter chip. Sort weight. |
| **`sentiment`** | neutral (9,626) / blank (2,520) / negative (573) / positive (494) / mixed (4) | Per-article sentiment | Color-tag in lists. Per-frame sentiment trend chart over time. |
| **`extraction_quality_label`** | good (9,025) / **poor (4,110)** / mixed (82) | "Poor" = article wasn't fully scraped. 4,110 candidates for re-fetch | Background job: queue all "poor" articles for re-extraction. Also: hide "poor" from analysis until re-fetched. |
| **`structured_extraction`** | 1,354 rows with full LLM JSON | Per-article LLM analysis: `one_sentence, framing, sentiment, relevance_score, opponent_attacks[], reason` | Show `one_sentence` as the card subtitle on review queue. Show `framing` as a badge. Show `opponent_attacks` as bulleted callouts. |
| **`gdelt_tone`** | JSON on every GDELT-ingested article | Sentiment intensity beyond just label: `avg_tone, polarity (positive%-negative%), activity_density, word_count` | A "tone intensity" sparkline on frame detail pages. Polarity can flag whether articles are mild vs aggressive |
| **`gdelt_themes`** | Empty in samples I pulled | GDELT V2 categorical themes (`ECON_INFLATION`, `TAX_FINANCIAL_*`) | Likely a bug — ingestion isn't populating this. Investigate why. Could power issue auto-classification. |
| **`evidence_score`, `credibility_score`** | Both per-article scores | We have them but I don't see them on cards | Could combine into a single "trustworthiness" badge. |
| **`source_credibility`** | text default 'medium' | Per-article credibility category | Triage signal. Combine with outlet authority. |
| **`source_owner_type`** | 88% "unclear" / 6% party_committee / 5% community | When set, it identifies WHO created the content (party press release vs random op-ed) | Tag color on cards. Filter chip. |
| **`source_author`** | Probably populated for some | Author byline | Show on cards. Track "most-active opponents of the campaign by byline". |

### `narrative_frames` table

| Column | Distribution | Why it matters | Suggested surface |
|---|---|---|---|
| **`momentum_signal`** | 13 "viral" / 2 empty | BROKEN — almost everything classifies as "viral" so the signal is meaningless. (See `05_BACKEND_ARCHITECTURE.md` for fix) | Either fix the classifier or remove the field. As-is it's noise. |
| **`last_known_stage`** | 6 mainstream / 4 emerging / 2 active / 1 spreading / 1 resurfacing / 1 dormant | We surface this on cards already | OK as-is, but the "dormant" frame (Healthcare Debate, last match Feb 18) should be either auto-archived after N days or flagged "resurrect?" |
| **`momentum_data`** | JSON metadata | Detail about why a frame was classified the way it was | Tooltip on the momentum badge |

### `frame_stage_history` table (67 rows, ~5 transitions per active frame)

| Field | What it stores | Why it matters | Suggested surface |
|---|---|---|---|
| **`from_stage`, `to_stage`, `transitioned_at`** | Stage timeline per frame | Powers a "frame lifecycle" timeline | Frame detail page: vertical timeline showing stage changes |
| **`metrics_snapshot`** (JSON) | At each transition: `{"art_total": 80, "art_this_week": 7, "art_last_30d": 16, "baseline_weekly": 3, "days_since_article": 1}` | Explains WHY the stage changed. "Promoted to spreading because weekly articles (7) exceeded 2× baseline (3)" | Tooltip on each timeline marker. Click → expand to see metrics. |

### `frame_variants` table (157 rows)

| Column | What it stores | Why useful |
|---|---|---|
| **`name`** (LLM-generated) | Specific phrasing within a frame | Frame "Bresnahan's Stock Trades" has 58 variants — these are the actual specific claims/wording in the wild |
| **`mention_count`** | How many times each variant appeared | Identify which specific phrasings are gaining traction |
| **`generation`** | Recluster generation number | Detect when new phrasings emerge (newer generations) |

**Surface:** A "Frame variants" panel on the frame detail page already exists (per CLAUDE.md). Verify it's showing `mention_count` + recent-generation flag. If not — easy add.

### `story_clusters` table

| Column | What it stores | Why useful |
|---|---|---|
| **`source_diversity_score`** | float computed at cluster level | "How widely is this story being covered" beyond just outlet count |
| **`known_entities`** | JSON array, e.g. `["COGNETTI, PAIGE"]` | Entities GDELT identified in the article. Could power an entity-mention graph. |
| **`dormant_since`** | datetime | When a cluster stopped getting new articles | "Resurrect alerts" when a dormant cluster gets fresh activity |
| **`structured_extraction`** | JSON | Cluster-level analysis (not per-article) | Show on cluster detail to summarize "what this story is about" |
| **`analysis_anchor_*`** | Points to the source_item used as analysis input | Provenance — "this cluster's framing came from THIS article" |

### `frame_cluster_matches` table

| Column | What it stores |
|---|---|
| **`confidence`** | LLM match confidence 0-100. Distribution: 1,065 are 90+, 108 are 75-89, 21 are 50-74, **zero below 50** | Surface as a "match quality" indicator — and consider lowering matcher's auto-accept threshold |
| **`matched_by`** | "llm" for all 1,194. Could expand to "keyword" or "embedding" when added | When/if you add multiple matchers, show which one fired |

### `issues` + `issue_mentions` (13 issues, 250 mentions)

Already-aggregated data per priority issue:
```
Corruption & Ethics      43 mentions
Economy & Jobs           40
Taxes & Budget           36
Healthcare               27
Education & Schools      20
Infrastructure           16
Public Safety            16
Downtown Development     15
Local Government         11
Environment              10
```

**Suggested surface:** A "What issues are getting talked about" donut/bar chart on the Dashboard. Currently you have frames + clusters but no issue-level rollup, even though the data is there.

### `cluster_opponent_activities` (272 rows)

| Type | Count |
|---|---|
| Attacks (cluster attacked the opponent) | 102 |
| Claims (opponent's verified claims) | 154 |
| Promises (opponent's promises) | 30 |

**Surface:** Three timelines per opponent:
- "What they've claimed" (154)
- "What they've promised" (30) → critical for accountability tracking
- "What attacks have been made against them by others" (102)

Already mostly built per code, just verify visibility.

### `gdelt_tone_snapshots` (183 rows, daily entries)

```
Paige Cognetti (candidate)  — 8 days, tone range -2.17 to +1.62, avg -0.29
Rob Bresnahan (opponent)    — 7 days, tone range -2.23 to +0.28, avg -0.21
```

**Surface:** A "Sentiment over time" sparkline on Dashboard showing both candidates' GDELT tones side-by-side. This is a leading indicator — tone shifts often precede polling shifts.

### `google_trend_snapshots` (368 rows)

```
Bresnahan in PA       interest avg 8.87  ←  trending higher
Cognetti in PA        interest avg 7.35
Cognetti in PA-577    interest avg 6.03  (DMA-577 = Scranton/Wilkes-Barre)
Bresnahan in PA-577   interest avg 5.26
```

**Insight:** Bresnahan is searched more statewide BUT Cognetti is searched more in the actual district (Scranton DMA). This is a meaningful signal — show it.

**Surface:** Dashboard widget: "Statewide interest" vs "District interest" — two mini-bars.

### `rss_feeds` — the gap I noticed

98 news / 9 opponent_statement / 3 social / 1 campaign_material

**Glaring gap:** Only **1 campaign_material** feed. There's no automated tracking of Cognetti's OWN press releases, social posts, etc. Adding the candidate's RSS / blog / social as monitored feeds means you can answer "what is OUR campaign saying" alongside "what is the opposition saying."

---

## Patterns hidden in the data

### Pattern 1: 4,110 articles never fully scraped
`extraction_quality_label = 'poor'` for 4,110 articles. These need re-fetching with the readability extractor (or paywall fallbacks). Doing so would unlock:
- Better LLM scoring (more context = better relevance call)
- More candidate_frame generation
- Better outlet linking (some failures are URL-resolution issues)

### Pattern 2: Massive ingestion spike on 2026-05-23
5,213 articles ingested in one day vs typical 200-700. Only 59 relevant out of those 5,213 (1.1%). Something kicked off a GDELT backfill or similar. Check ingestion logs around 2026-05-23 — probably a one-off backfill that should be documented.

### Pattern 3: Most relevant coverage comes from 5 outlets
```
The Times-Tribune     138 relevant articles
YouTube                129  ← surprising, 2nd by volume
Times Leader           101
Citizens' Voice         77
PAHomepage (WBRE/WYOU)  59
WNEP-TV                 47
```

YouTube is 2nd by volume of relevant content but barely surfaced as a "source type" in the UI. Suggests:
- Specific YouTube channels worth monitoring
- A "video coverage" tab

### Pattern 4: Issue-level coverage skews toward attacks (vs policy)
"Corruption & Ethics" and "Taxes & Budget" together = 79 mentions. "Healthcare" + "Education" + "Infrastructure" = 63 mentions. The conversation is more about attacks than policy. Worth showing the user as a strategic insight.

### Pattern 5: Frame stage distribution suggests we're under-tracking emerging narratives
6 mainstream / 4 emerging / 1 spreading. The pipeline favors "I already know about this" over "something new is coming". Combined with the 186 stuck candidate_frames (which Session A unblocked) — the system was actively under-surfacing the early-warning signal.

---

## Cross-table opportunities

### Opportunity A: Outlet authority × frame stage
Cross `outlets.authority_score` with `frame_cluster_matches`. Identify frames that are getting traction in HIGH-authority outlets vs LOW-authority outlets. A frame stuck in low-tier blogs is different from one breaking on Spotlight PA — same row count, very different meaning.

### Opportunity B: GDELT tone × narrative frame
For each frame, compute the avg GDELT tone of its matched articles. A frame whose articles are all negative-toned is "an attack narrative landing" vs one with positive tone is "good news narrative". Currently nothing surfaces this distinction.

### Opportunity C: Daily ingestion vs daily relevance
Plot ingestion volume vs relevant article count per day. Spikes in volume that don't produce relevant articles = wasted ingestion (something to tune). Spikes in relevance with low volume = high-signal day (something to investigate).

### Opportunity D: Frame variant churn = narrative pivot
When `frame_variants.generation` increments AND `frame_variants.name` changes meaningfully — that's the opposition adapting their messaging. Worth alerting on.

### Opportunity E: Outlets × geography
Most "local" outlets are PA news, but we're now also pulling national (Fox News, Newsweek). A "local vs national coverage" ratio chart per frame would show whether a story is staying local or breaking out nationally.

---

## What's NOT hidden (already surfaced)

For completeness:
- Dashboard 3-column layout (filter / narratives / watchlist) — well-used
- Frame detail with variants timeline — used
- Morning briefing — used (after Session 3 fixes)
- Review queue — used
- Opponent activities (basic) — used

But several of the columns above are powered by data the backend computes and stores but the frontend never reads. Lower-effort wins live in adding existing data, not building new data pipelines.
