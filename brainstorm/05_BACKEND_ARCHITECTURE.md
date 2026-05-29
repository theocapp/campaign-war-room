# Backend / Architecture / Prompts — review notes

Read-only review of `services/`, `routes/`, and the key LLM prompts. Findings ranked by impact.

**No code changes made.** Each item: what I found, why it matters, suggested action.

---

## 🔴 BUG — `frame_momentum` always returns "viral" (root cause of weird signal distribution)

**File:** `services/frame_momentum.py:178-182`

The smoking gun for the "13 of 15 frames classified as viral" anomaly I flagged in the audit:

```python
matched_terms = _terms_matching_frame(f.name, all_terms)
# If no specific match, fall back to campaign-wide trend signal —
# we still want to know if any campaign-relevant search activity
# correlates with this frame's article spike.
terms_for_trend = matched_terms or all_terms   # ← THE BUG
```

The only trend terms tracked are `"Bresnahan"` and `"Cognetti"` (both your candidate names). Almost no frame name contains those two words exactly — frame keyword matching almost always returns `[]`. The fallback then uses BOTH terms for every frame's trend velocity calculation.

Because "Bresnahan" and "Cognetti" are constantly searched (rising as election approaches), `trend_v` exceeds `SPIKE_THRESHOLD=2.0` for almost every frame. Combined with any article spike → "viral" classification.

**Effect:**
- All 13 frames with momentum signal got "viral"
- The 4 categories (`viral`/`missing_coverage`/`elite_only`/`stable`) collapse into a single label
- The UI uses momentum_signal for nothing useful right now (it's all "viral" = no signal)

**Fix (one line):**
```python
terms_for_trend = matched_terms  # drop the fallback
```
Then in `analyze_all_frames`:
```python
if not matched_terms:
    # No specific trend signal for this frame — skip with "unknown" label
    f.momentum_signal = "no_trend_signal"
    f.momentum_data = json.dumps({"reason": "no matching trend terms"})
    continue
```

**Better fix:** Expand trend terms to track more keywords (issue names, frame-derived keywords). Once `google_trends.py` tracks more than 2 terms, the fallback isn't needed because every frame should find at least one match.

**Effort:** 30 min for the patch, 2 hrs for the expanded-trends version.

---

## 🟠 ARCHITECTURE — Dual-write tables haven't decided their end state

You have three pairs of tables doing "old + new" tracking:

| Legacy | Cluster-native (new) | Status |
|---|---|---|
| `rss_feeds` | `source_monitors` (rss type) | Both written. Session B added sync helper. End state TBD. |
| `narrative_frame_mentions` (NFM) | `frame_cluster_matches` (FCM) | Both written (`rescore.py` + `ingestion._persist_cluster_native`). NFM still load-bearing for variant clustering. |
| `opponent_activities` | `cluster_opponent_activities` (COA) | Both written. COA has 272 rows; OpponentActivity has 0 (you've migrated). |

**Problem:** Every dual-write is two transactions per insert + two code paths to maintain + ambiguity for new developers about which table is canonical.

**Suggested decision matrix:**
- `opponent_activities` → DROP (0 rows, no readers I found — verify before dropping)
- `narrative_frame_mentions` → KEEP as canonical for now (used by frame_variants + detail page quotes). After variant clustering is migrated to FCM-based, can drop NFM.
- `rss_feeds` → KEEP as canonical, drop the 51 `source_monitors.rss` rows as part of cleanup migration

Documented similar in ROADMAP earlier; this confirms the cleanup priority.

---

## 🟠 LLM PROMPT — `campaign_analysis.SYSTEM_PROMPT + user prompt` is 9000+ chars

**File:** `services/campaign_analysis.py:85, 97-360`

The v2 prompt is **really well engineered** — anti-hallucination rules, clear owner_type semantics, two embedded examples, strict schema. I want to be clear: this is good prompt engineering.

But it's a single 280-line prompt sent for EVERY article being scored. At your current volume (~50 new articles/day getting LLM analysis):
- ~3000-5000 input tokens/article × 50 = 150K-250K tokens/day on Groq
- Groq is cheap (~$0.0003/1K) → ~$0.05/day. Negligible.
- BUT if you ever migrate to OpenAI or expand to 10 races at this rate, it'd be ~$5-15/day.

### Optimization 1: Two-pass scoring (saves ~70% of LLM cost)

Today, every article gets the full v2 analysis even though ~80% are irrelevant noise. A cheap pre-classifier could short-circuit them:

```
PASS 1 (50 tokens, ~$0.000001/article):
  "Is this article about [candidate], [opponent], PA-08 district,
   or PA congressional politics? Answer: yes/no/maybe."

If "no" → skip Pass 2, save with relevance=irrelevant.
If "yes"/"maybe" → run full v2 analysis.
```

Estimated savings: 70-80% reduction in LLM costs at current volume. More important for productization (10 races = 500 articles/day).

### Optimization 2: Prompt cache opportunities

The SYSTEM_PROMPT (250 tokens) is constant across calls. Most LLM providers (OpenAI, Anthropic) now offer prompt caching that charges 10% for cached prefix. If you switch from Groq to OpenAI for `get_provider()`, enable caching on SYSTEM_PROMPT — savings compound across articles.

### Optimization 3: Frame list compression

The `frames_section` is the entire list of all active narrative frames (today: 15 frames, ~600 tokens). For each article, only ~3-5 frames are remotely relevant. Pre-filtering candidate frames by keyword overlap with article title + first 200 chars could reduce this to ~150 tokens per article × 50 = 7K tokens/day saved. Small win, free implementation.

### Optimization 4: Truncation may be too aggressive

```python
def _article_text(item: SourceItem, max_words: int = 8000) -> tuple[str, bool]:
```

8000 words is actually generous — most articles are under 1500. But the LLM is told "extract claims only from visible text," which is correct. The 4,110 articles with `extraction_quality_label = 'poor'` are likely under-extracted and would benefit from re-extraction more than from a larger LLM context.

---

## 🟠 LLM PROMPT — opportunities for the briefing summary

**File:** `services/briefing_summary.py` (not read in detail — recommendation based on what the briefing produces)

The current Morning Briefing race_memo is good but generic. Three improvements:

### Make it diff-based instead of snapshot-based
Currently the memo is "here's what's happening." It would be 5x more useful as "here's what CHANGED since yesterday." Pass the prior briefing into the prompt as `previous_state` so the LLM can produce:

> "Yesterday's emerging story about Bresnahan's Medicaid vote has accelerated — 8 outlets picked it up overnight including The Hill. Meanwhile, the Cognetti stormwater initiative narrative went dormant. New today: a Free Republic story attacking Cognetti's maternity leave policy — too low-tier to act on but worth monitoring."

This connects to Feature #2 in `02_FEATURE_IDEAS.md`.

### Add "what to do today" section
The briefing tells you what IS but not what to DO. Add a section that asks the LLM:

> "Based on these narratives, what's the #1 thing the campaign should focus on TODAY? Suggest 1-3 specific actions (a tweet, a statement, a press call, etc.)."

### Tone consistency
The race_memo voice should match the user's preferred tone. Add to the prompt: "Write in [aggressive | analytic | optimistic] voice." Let user pick in Setup.

---

## 🟠 SCORING — `_compute_priority_score` has minor inefficiency

**File:** `services/ingestion.py:330-352`

```python
if db.query(OpponentActivity).filter(OpponentActivity.source_item_id == item.id).count():
    score += 20
```

This is an N+1 query — runs once per article during rescore (~1900 articles). Could batch:

```python
opp_act_item_ids = set(
    db.query(OpponentActivity.source_item_id)
    .filter(OpponentActivity.source_item_id.in_(batch_ids)).all()
)
```

But: `OpponentActivity` has 0 rows. This query is always returning empty. **The cleanup here is to use `ClusterOpponentActivity` via cluster_id lookup, OR drop the +20 bonus entirely since OpponentActivity is dead.**

Effort: 1 hr to clean up.

---

## 🟠 INGESTION — `ingest_lock` is global; one slow feed blocks everything

**File:** `services/rss_ingestion.py:19`

```python
ingest_lock = threading.Lock()
```

Shared across the scheduled job + manual ingest-all + monitor-creation paths. Good for preventing concurrent corruption. BUT: if one RSS feed hangs (Session B mentioned the `feedparser.parse(feed_url)` hang bug), the lock is held and no other ingestion can start until it times out.

**Suggested:** Per-feed timeouts + per-feed locks. Already partially mitigated by the Session B `feedparser` fallback removal.

Alternative: add a global timeout on the lock with `acquire(timeout=300)` so a hung feed times out after 5 min and the system recovers.

---

## 🟡 SCORING — `confidence=75` magic number

**File:** `services/ingestion.py:380`

```python
cluster_writes.upsert_frame_match(
    db,
    frame_id=frame.id,
    cluster_id=cluster.id,
    confidence=75,            # ← hardcoded
    source_type="cluster_runtime",
```

Hardcoded `confidence=75` for new FCM rows during cluster_runtime ingest. The LLM has produced "high/medium/low" confidence for each matched claim — that should map through. Distribution from the data:

```
high (75-89):  108 rows
medium:         21
low:             0
very_high (90+): 1,065  ← so confidence does get overwritten elsewhere
```

The 1,065 very-high rows come from somewhere — probably the dedicated `match_article_to_frames` path in `narrative_frames.py`. So this default is the fallback for cases where the dedicated matcher hasn't run yet.

Worth tracing through and making the default come from the actual claim confidence rather than a constant.

---

## 🟡 SCHEDULER — health endpoint is RSS-only

**File:** `services/scheduler.py:32-37`

```python
_scheduler_health: dict = {
    "last_rss_success": None,
    "last_rss_skip": None,
    "last_rss_error": None,
    "last_rss_error_at": None,
}
```

You added scheduler health observability (good!), but it's RSS-specific. The scheduler also runs: candidate_frame_promoter, frame_momentum, search_monitors, orphan_gc, frame_dedup, reddit, mastodon, gdelt. Each should report success/skip/error timestamps in the same shape.

**Suggested:**
```python
_scheduler_health: dict = {
    "rss": {"last_success": None, "last_skip": None, "last_error": None, "last_error_at": None},
    "candidate_promoter": {"last_success": None, ...},
    "frame_momentum": {"last_success": None, ...},
    # etc.
}

def get_scheduler_health() -> dict:
    return dict(_scheduler_health)
```

When a job hasn't fired in 24h, the UI can show "⚠ Frame momentum hasn't run since X". Currently silent failures across multiple jobs would be invisible.

---

## 🟡 EMBEDDINGS — cost optimization opportunity

**File:** `services/embeddings.py` (Session A rewrite)

The OpenAI fallback uses `text-embedding-3-large` at `dimensions=3072`. This is the most expensive OpenAI embedding tier.

Alternatives:
- `text-embedding-3-small` at `dimensions=1536` — 5× cheaper ($0.02 vs $0.13 per 1M tokens)
- But: dimension mismatch with cached Gemini vectors

**Suggested:** Stick with `large@3072` for the fallback (interchangeability matters more than the trivial cost savings). Pre-compute and confirm: at current volumes, OpenAI fallback costs are ~$0.002 per refresh. Not worth optimizing.

---

## 🟡 STORY CLUSTERS — 96% are size-1 (Session C investigation territory)

I already noted in the audit that 96% of clusters are size-1 (12,129 / 12,608). After we filtered for relevance, the picture improved: 94% of critical clusters are still size-1.

The clustering uses simhash_64 + embedding similarity. Two hypotheses:
1. **Simhash threshold too tight** — typo-or-paragraph variations of the same article fail to match
2. **Embedding similarity threshold too tight** — same story across outlets reads differently enough not to merge

To investigate (Session D territory): pull 30 critical size-1 clusters, find ones that "look like dupes" of each other, run their embeddings + simhashes through the dedup pipeline manually, identify failure pattern.

Likely fixes (don't apply blind — investigate first):
- Lower simhash distance threshold
- Lower embedding cosine threshold
- Add title-fuzz matching as a third strategy

---

## 🟡 RESCORE — `fallbacks` counter exists but no max-retry circuit-breaker

**File:** `services/rescore.py:32, 67-68, 229`

When the LLM returns a fallback (degraded response that should be retried), rescore counts it but doesn't put a circuit breaker on the run. If 100% of articles are fallback'ing (e.g., LLM provider entirely down), the rescore job runs to completion saying "0 processed" without bailing.

**Suggested:** If `fallbacks > N` consecutive (e.g., 20), abort the run with a clear "LLM appears unavailable, last successful score was X" message. Otherwise you waste compute, and the UI shows "rescore complete!" when it actually did nothing useful.

---

## 🟢 GOOD STUFF — patterns worth keeping

Things I noticed that are well-engineered and shouldn't be touched without reason:

1. **Single-call v2 scoring prompt** — owner_type definition (which side BENEFITS) is the right framing
2. **Per-claim quote validation** — `_verify_quote` catches LLM hallucinations cheaply
3. **`get_provider()` vs `get_judge_provider()`** — clean separation of "fast/cheap" vs "smart/expensive" LLM use
4. **Embedding cache + OpenAI fallback** (Session A) — turned an opaque silent failure into a recoverable + observable system
5. **`safe_deletes.py`** (Session 4) — cascade-aware delete helpers prevent the orphan-cluster bug class
6. **APScheduler with health observability** — bounded jobs with retry, exposed via /api/system/scheduler-health
7. **`_REFUSAL_MARKERS` / `_SHORT_REFUSAL_PHRASES`** in `narrative_frames.py` — catches LLM refusal patterns ("I can't help with that") as failed extractions, not silent successes
8. **Idempotent UPSERTs everywhere** — `upsert_frame_match`, `upsert_opponent_activity` use composite keys to prevent duplicates from rescore loops

---

## Test suite state — actually audited

Ran `pytest tests/` during the overnight session. Findings:

### 3 test files can't even import (collection errors)
- `tests/test_campaign_analysis.py` — imports `_persist_opponent_attacks, _persist_frame_matches` from `app.services.ingestion`. These functions don't exist anymore. **Test drift — code was refactored, tests weren't updated.**
- `tests/test_ingestion_crawler.py` — imports `trafilatura` which is not installed in your `.venv`. Either the requirements changed or trafilatura was removed but the test wasn't.
- `tests/test_ingestion_reddit.py` — imports `_post_text` from `ingestion_reddit`. Function doesn't exist. Same drift pattern.

### 6 of 74 collectible tests fail
- 4 in `test_race_relevance.py` — all timing out at 30s because they hit the live LLM API for scoring. They should use mocks. (Note: also revealed that the test run consumed real Gemini quota — multiple keys cooled down during the run.)
- 2 in `test_election_date_inference.py` — failure mode not deeply traced; likely related to recent campaign_initialization changes.

### 68 of 74 tests pass
- `test_outlets.py`, `test_published_at.py`, `test_html_stripping.py`, `test_snapshots.py`, etc. all green.
- This suggests the test infrastructure is fundamentally fine; it's the LLM-dependent + recently-changed-API tests that broke.

### Recommended cleanup
1. **Mock LLM calls in all tests.** Patch `app.services.llm_provider.get_provider` to return a deterministic fake. Tests should never actually hit Gemini/OpenAI/Groq. Effort: ~3 hrs to retrofit.
2. **Fix imports in the 3 broken-import files.** Either update to current API or delete obsolete tests. ~1 hr.
3. **Add `pytest-timeout` to defaults** so any test taking >10s fails loudly instead of hanging the suite. Already installed; just add to `pytest.ini`.
4. **Run pytest in CI** (you may not have CI) — to catch the drift earlier.

This pre-empts a class of bug I'd worry about: someone makes a code change that touches scoring/ingestion, tests "pass" (because the broken ones can't even import → silently skipped), and the change ships broken.

## Suggested test additions

Specific tests I'd add for resilience:

1. **`test_frame_momentum.py`** — assert that no frame returns "viral" when no matched_terms are found (would have caught the fallback bug)
2. **`test_embed_texts_fallback.py`** — mock Gemini to 429, assert OpenAI is called and returns embeddings
3. **`test_outlet_linking.py`** — assert backfill_outlet_links is idempotent (don't double-link)
4. **`test_safe_delete_frame.py`** — assert all child rows cascade including frame_stage_history
5. **`test_compute_priority_score.py`** — pin the scoring formula so regressions are caught

---

## Postgres migration considerations

You have it as a future item. Specific gotchas to plan for:

1. **`source_items_fts` (FTS5)** — Postgres uses `tsvector`, not FTS5. Different syntax + index management. Migration script needs explicit `tsvector` setup.
2. **`StoryCluster.id` is VARCHAR** — derived from `"source-{N}"` (SourceItem.id). Postgres handles this fine but bigserial migration would change ID format. Choose: keep VARCHAR (simpler migration) or migrate to BIGINT (saves bytes, but breaks all existing references).
3. **JSON columns** (`gdelt_tone`, `structured_extraction`, `momentum_data`, `metrics_snapshot`) — switch from `TEXT` to native `JSONB`. Massive query speedup. Plan for migration.
4. **`source_items` is 13K rows + growing** — at 100K rows you'll feel SQLite locking. Postgres scales better. The migration trigger is probably "more than 1 active campaign user" not row count.
5. **Connection pooling** — switch from one-session-per-request to a proper pool (`asyncpg` + SQLAlchemy 2.x async). FastAPI plays nice.

When to migrate: when you onboard race #2 (the productization moment). Postgres makes multi-tenancy + row-level security real.

---

## Recommended priority

If you do 3 things in this category, do them in this order:

1. **Fix the `frame_momentum` bug** (30 min, immediately useful)
2. **Add scheduler-health for all jobs** (2 hrs, prevents silent scheduler failures)
3. **Two-pass scoring (cheap relevance pre-filter)** (4 hrs, prepares for productization scale)

The 4th and 5th would be: tests for the high-value paths + postgres planning.
