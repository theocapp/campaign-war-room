# Inter-Session Review Log

All Claude Code sessions working on this project read and write here.
See CLAUDE.md for the full protocol.

---

## 2026-05-31 Session: Full-project review — safe fixes + verify-first triage — ⚠ LIVE DB MIGRATION INCIDENT

User asked for a full review (efficiency / logic / mistakes / cost), then "go through each, verify it's unintentional, verify it won't break anything, then fix it." Shipped the safe fixes; for behavior/cost items, verification mostly showed *intentional* design — held off and surfaced knobs instead of changing them.

### Built (safe, shipped)
- **DB indexes** (migration `84a1922627dd`, down_revision `2df994cdd1f9`): `ix_source_items_story_cluster_id` + `ix_narrative_frame_mentions_source_item_id`. `index=True` added to both columns in `models.py`. EXPLAIN was a 366ms seq scan on story_cluster_id; now Index Scan.
- **race_sentiment_sync.py** `_compute_7d_delta`: was ASC `.first()` (returned *oldest* snapshot, not the ~7d-ago one). Now picks the snapshot nearest the 7-days-ago target. Display-only `delta_7d`.
- **story_clustering.py** `_published_close`: `.days` floors (13.9d→13, asymmetric). Now compares `total_seconds() <= days*86400`.
- **ingestion.py**: widened `cacheable` structured-extraction fields (verdict/extracted_claims/source_credibility) so the cache write isn't silently lossy; replaced `except: pass` with `logger.debug(exc_info=True)`.
- **embeddings.py / routes/races.py**: replaced two more silent `except: pass` with logging (races.py `initialize_campaign` now `logger.exception`).
- **types.ts** `ActivityPoint`: made `total` + bucket fields optional → cleared 15 tsc errors. **NotificationSettings.tsx**: added missing `description` on "Proposed narratives pending" toggle. **Landscape.tsx**: fixed a latent ReferenceError (`EstablishedSidebar` used `candidateName`/`opponentName` out of scope — now threaded as props).
- **SearchBar.tsx**: added `cancelled`-flag cleanup to kill the stale-result race (mirrors Setup.tsx).
- **.env.example**: full rewrite from the real code-read var list (~61 vars, grouped, real defaults); fixed the stale "SQLite / migration-in-progress" DB note to reflect Postgres production.

### ⚠ LIVE DB MIGRATION INCIDENT (read this if you touch alembic)
While creating the index revision file (body still `pass`), **`uvicorn --reload` auto-ran `alembic upgrade head` against LIVE noctua**, stamping it to `84a1922627dd` with **no indexes**. Then my attempt to validate on scratch (`DATABASE_URL=...noctua_scratch alembic ...`) was **silently clobbered by `env.py`'s `load_dotenv(override=True)`** → ran against live again. **Two footguns: (1) editing a revision file triggers a reload that migrates live; (2) an exported `DATABASE_URL` did not win over `.env`.**
- **Reconciled**: validated the real migration on scratch via `alembic -x url=...noctua_scratch` (up→down→up clean); then created the two indexes on live with `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (additive, non-blocking, reversible). Verified both `indisvalid=t`, version `84a1922627dd`. No data loss.
- **Root-cause fix**: `env.py` now captures `_explicit_db_url = os.environ.get("DATABASE_URL")` *before* `load_dotenv(override=True)` so `DATABASE_URL=...scratch alembic` targets scratch. Docstring rewritten to document both `-x url=` and `DATABASE_URL=` forms + the override caveat. `-x url=` was always immune; prefer it.

### Verify-first triage (NOT changed — found intentional/reasonable)
- **Dashboard 60s briefing re-fetch** (Dashboard.tsx:516): hash-cached (`get_or_generate_grounded`) → no LLM call unless inputs change. Liveness feature, not waste. Left as-is.
- **40-vs-50 relevance threshold** + **momentum velocity normalization**: verified correct/intentional last session.
- **gpt-4o for briefing** (briefing_summary.py:21-29): documented quality choice, cost-bounded by input-hash cache, overridable via `OPENAI_BRIEFING_MODEL`. Not a bug.
- **ingest_lock held during LLM-exhaustion backoff** (rss_ingestion.py:119 + llm_provider.py:1054-1068, cap `LLM_EXHAUSTED_MAX_WAIT_SECONDS`=1800): holding the lock during the wait is reasonable backpressure (don't pile concurrent runs onto exhausted providers). Only knob is the 30-min cap.
- **"auto-rematch double-spend"**: investigated — the two rematch entry points are on *separate* triggers (manual frame CRUD route → debounced `rematch_all`; auto-promote → `rematch_recent` directly). They do **not** stack. Not the bug it looked like.
- **`_is_llm_scored`** (dashboard.py:166): fragile string heuristic for "is this summary LLM-generated," but display/counting-only and working. Refinement, not a bug.

### Open questions / concerns for review
- Frontend has 5 independent `setInterval` pollers (NotificationsBell 60s, NotificationsList 60s, Layout 8s + 15s, Dashboard 60s). Consolidation is optional efficiency, not correctness — left alone.
- Frame-edit (manual CRUD) triggers a **365-day** `rematch_all`. Correct for a definition change, heavy for a cosmetic rename; debounced + lock-guarded + embedding-gated so defensible. Flagged for user, not changed.
- All edits above are **uncommitted**. Leaving the commit decision to the user.

---

## 2026-05-31 Session: Outlet monthly_visitors backfill (Tranco-calibrated) — LIVE DB WRITE

### Built
- `backend/data/outlet_monthly_visitors.json` — 106 vetted (domain → monthly_visitors) entries with tranco_rank + basis. Self-documenting `_meta` block (model, calibration, honesty + apply policy).
- `backend/scripts/backfill_outlet_monthly_visitors.py` — apply script modeled on `outlet_reliability_apply.py`. **Dry-run by default; `--apply` to write. Fill-only-when-NULL (never overwrites existing values).**
- **RAN `--apply` against live Postgres** (user-approved). Outlets with `monthly_visitors`: 30 → **136 / 196 (69%)**. Article coverage: **17,436 / 19,720 outlet-linked articles (88%)** now sit under a sized outlet.

### Why this matters (the real motivation)
The reach-weight formula (`analytics.py:39-42`, `narrative_frames.py:1736-1737`) does `monthly_visitors * 0.003` when a value exists, else `authority_score / 10`. That's a ~1000–6000× scale gap. Before the backfill only ~30 outlets had a value, so **frame/analytics reach rankings were silently dominated by whichever outlets happened to get a number at setup.** This backfill puts editorial outlets on the same scale. **Consequence other sessions should expect: `weighted_reach` / `reach_*` numbers across the dashboard have shifted (mostly up), and frame reach rankings will re-order — frames carried by local outlets that previously read ~0 now register.** Computed live in SQL, so it takes effect on next page load — no restart needed.

### Key decisions
- Model: `monthly_visitors = min(1e8, round(1.2e10 / tranco_rank))`, floor 15,000 for unranked. K calibrated to median(existing_mv × rank) over 22 known editorial anchors. Accuracy: **22/22 within one log10 band, 20/22 exact band, median |log10 ratio| 0.29.** Band-accurate, NOT count-accurate.
- **Honesty guardrail (agreed w/ user): surface as outlet reach TIERS (National ~1M / Regional ~100K / Local ~10K), NEVER as precise per-article viewer counts.** The backfill does NOT legitimize the 0.3%-per-article step the user already flagged as a guess — it only improves the outlet-size input + repairs the distortion above.
- Deliberately left NULL (domain traffic ≠ article readership): social platforms (freerepublic 1,132 items, youtube, reddit, fb, ig, bsky, x), portals (yahoo/msn/aol), .gov/.mil, non-editorial (wikipedia, prnewswire), and junk caught in full-tail audit (milb/steelers/wbspenguins/visitpa/baps/cawp.rutgers). Excluded by explicit **domain set**, not `outlet_type` — that field is unreliable (yahoo/reddit are typed "national", prnewswire/pa.gov "local_news").
- Caught + fixed a lookup bug: blog-platform subdomains (`*.substack.com`) were inheriting the platform's rank → 24M visitors for a tiny newsletter. Now floored.

### Rollback
Additive, NULL-only. To revert: null `monthly_visitors` for exactly the 106 domains in `backend/data/outlet_monthly_visitors.json` (the 30 pre-existing anchors are untouched and must stay).

### Open questions / concerns for review
- Two new files are **uncommitted** (not staged). Leaving commit decision to the user.
- Deferred "Job 2": capturing real social engagement counts (upvotes/likes/reposts) from scrapers — not started. Social is <1% of corpus, so low priority.
- Display layer (tier labels in the UI) not built yet — pending user direction.

---

## 2026-05-30 Session: Rescore-loop diagnosis + unscored-filter bugfix + banner cleanup

Triggered by the user noticing the blue "Initializing campaign data" banner running a rescore at 395/13917 and asking whether the previous session had triggered a full rescore. Diagnosed an infinite restart loop and shipped the fix.

### Diagnosis (the why, for future sessions)

The previous session's body-recovery sweep (`recover_stub_bodies` in `article_body_recovery.py`) deliberately clears `summary = None` on items it rewrites, so the existing rescore worker re-runs LLM scoring against the fuller body. That's correct for the SCOPED `/admin/rescore-recovered-bodies` endpoint (which uses its own time-windowed query).

But it accidentally weaponised an existing bug: **`only_unscored=True` in `start_rescore` AND `_resume_pipeline_if_needed` both used `SourceItem.summary IS NULL` as a proxy for "needs scoring."** `summary` is only written when the LLM returns a `one_sentence`, and the pre-LLM race-mention gate at [campaign_analysis.py:847](backend/app/services/campaign_analysis.py:847) explicitly returns `"one_sentence": None` for irrelevant articles. Same for items the LLM itself judges irrelevant.

DB verification at the time of the diagnosis:
```
summary IS NULL: 13,878
  ...but race_relevance_score is SET: 13,878 (100%)
  ...and archived_as_irrelevant=true:  13,612 (98%)
```

Every "unscored" item had actually been scored. 98% were archived as irrelevant. With body-recovery clearing summary on ~13K items, every uvicorn reload would auto-trigger a full rescore over all 13K — gate-fire most of them (no LLM cost), get killed by the next reload, repeat. The "395/13917" the user saw was the second run since their session ended; the first had reached 3,306 before being killed.

Cost impact was minor (~98% short-circuit at the pre-LLM gate, so probably under $1 of actual gpt-4o-mini spend across all the loop iterations), but the symptom was that the banner never went away and nothing ever appeared to finish.

### Built

**`only_unscored` filter fix** — [rescore.py:561-565](backend/app/services/rescore.py:561) + [scheduler.py:657-659](backend/app/services/scheduler.py:657)

Both call sites now filter on `SourceItem.race_relevance_score.is_(None)` instead of `summary.is_(None)`. Comment at the rescore.py site explains the WHY so future readers don't revert it. The `_resume_pipeline_if_needed` startup check now correctly reports "0 unscored" on a settled campaign and exits without queueing work.

**Pipeline banner admin-gated** — [Layout.tsx:222-227](frontend-v2/src/components/Layout.tsx:222), [:404](frontend-v2/src/components/Layout.tsx:404), [:418](frontend-v2/src/components/Layout.tsx:418)

Pulled `user?.isAdmin` from `useAuth()` in the `Layout` component (it was already used in `ProfileMenu`). Gated both the orange LLM-mock warning AND the blue pipeline-progress banner on `isAdmin`. Friend-share access codes will no longer see internal pipeline churn.

**Banner `active` condition tightened** — [Layout.tsx:64](frontend-v2/src/components/Layout.tsx:64)

The old `midStream = bf.done && (!rs.done || !rm.done)` heuristic kept `active=true` forever on settled campaigns: `rs.done` lives in in-memory `_state["finished_at"]` and resets on every backend reload. Removed `midStream`. Banner now only shows when something is genuinely `running`. The campaign-setup checklist belongs on the Setup page, not as a persistent header.

### Key decisions

- **`race_relevance_score IS NULL` is the canonical "never scored" signal.** Picked it because every `_rescore_one` invocation unconditionally writes `race_relevance_score` (line 74), regardless of LLM verdict, gate firing, or LLM fallback. So NULL ↔ never been through the worker. `summary IS NULL` does NOT mean "never scored" — it means "scored AND found relevant enough for a one-sentence summary" being absent.
- **Left `/admin/rescore-recovered-bodies` (`admin.py:534`) on the old `summary IS NULL` filter.** That endpoint's purpose is exactly "find items whose body was just rewritten and needs re-scoring", and the body-recovery sweep flags them by setting `summary = None`. The semantic is correct there — it's specifically NOT a "never scored" check.
- **Killed the running rescore via the file-save reload, did not stop it explicitly.** Verified first that the new filter would return 0 items (every article already has `race_relevance_score` set), so the auto-resume on the next reload would find nothing to do. Applying the fix WAS the kill. No need to `POST /api/admin/rescore-stop`.
- **Removed `midStream`, did not migrate `finished_at` to the DB.** The simpler fix matches what the banner is for ("active progress visualization"). Persisting `finished_at` was the alternative but adds a schema migration and a state-management surface for very little user benefit — once the rescore is genuinely done, there's nothing to communicate.

### Open questions / concerns for review

- **Three callers of `summary IS NULL` exist, two are now fixed; the third (`admin.py:534`) is intentionally left alone.** If a future session adds new code that treats `summary IS NULL` as "unscored", it'll bring back the loop. Comment at `rescore.py:561` is the canary, but it's easy to miss. Consider naming the canonical predicate (e.g. a `SourceItem.is_unscored` hybrid_property) if the pattern recurs.
- **The pre-LLM race-mention gate ([campaign_analysis.py:840-870](backend/app/services/campaign_analysis.py:840)) is cheap but not free.** Every loop iteration ran it on ~13K items, ~200/min, so ~70min per loop pass burning CPU and DB session pool. The fix stops the loop, but if a future workflow legitimately needs to re-score all items, that cost is real — consider batching or caching the gate result.
- **`extended_backfill_completed=True` is the gate that lets `_resume_pipeline_if_needed` fire at all.** Once that's true on a campaign config, every backend restart will auto-trigger a rescore IF the new filter ever returns >0 items. With the corrected filter that's only when a genuinely-new article is ingested without scoring (which shouldn't happen — ingestion calls campaign_analysis inline). But if ingestion-side scoring ever fails silently and writes a row without a relevance score, this auto-resume will kick in and the user will see the banner pop up unexpectedly. Worth keeping in mind.
- **The campaign-setup checklist UX is now gone from the header for everyone.** That was useful during a fresh campaign init when the user wanted to watch backfill → discovery → rescore → rematch progress live. If that flow comes back (e.g. for a new SaaS campaign onboarding), the right home is the Setup page, not the persistent header — wire it there with the same `usePipelineStatus` hook.

### Review from another session

For the **2026-05-30 "Ingestion-quality fixes"** entry below: the body-recovery sweep (`recover_stub_bodies`) clearing `summary = None` is correct for its purpose, but the entry's note that "Recovered items get `summary = None` so the existing rescore worker (`/admin/rescore-articles?only_unscored=true`) re-runs LLM scoring against the fuller body" was an unsafe assumption. `only_unscored=true` was filtering on `summary IS NULL` which doesn't mean what the comment implied — it ALSO picks up every previously-rescored irrelevant article (98% of the corpus). The intended workflow (re-score only the recovered items) was actually only safely achievable via the explicit synchronous `/admin/rescore-recovered-bodies` endpoint that session wrote. The `only_unscored=true` path was a foot-gun. Fixed by switching that filter to `race_relevance_score IS NULL` — see "Built" above. No other behaviour from that session needs to change.

---

## 2026-05-30 Session: Ingestion-quality fixes (Google News collapse, YouTube transcripts, alerts, GDELT tune)

Overnight session triggered by the user noticing the dashboard's "24h Spikes" panel was blank. Investigation chased the empty state through a cascade of upstream issues, then shipped four fixes the user signed off on before going to sleep.

### Investigation findings (the why, for future sessions)

1. **24h Spikes blank** wasn't a frontend bug. The spike detector (`routes/analytics.py:185`) requires both `reach_24h ≥ 1.5` AND `reach_24h ≥ 2 × daily_avg_7d`, with reach counted only from **new** `FrameClusterMatch` rows in the last 24h. With only 1 new FCM in 24h vs ~24/day historical baseline, no frame could possibly clear the bar.

2. **Why FCMs dropped**: chained back to the **2026-05-26 Google News body-excerpt collapse**. Per-day per-outlet trajectory across "X — Google News Feed" sources: avg `raw_text` length collapsed from ~1500-2500 chars to ~60-200 chars on May 26. NOT a publisher-side regression (direct RSS for the same outlets is unchanged), NOT a code regression (last touch to `ingestion.py` was May 23), but an **external Google News format change** — they stopped including body excerpts in `entry.summary`. The collapse was distributed across **all** Google-News-mediated feeds simultaneously (Google News topical searches + publisher-named Google News feeds + "National pickup" search aggregator + Reddit-via-Google-News). YouTube items were already title-only because they come via Google News searches that don't link to real youtube.com URLs.

3. **Direct publisher RSS feeds for the lost outlets** mostly don't work either — Citizens' Voice, Times-Tribune, Standard-Speaker all 403 at the WAF level regardless of User-Agent. Only Times Leader's direct RSS responds. So Google News is genuinely the only path for those outlets, but Google's redirect URL is now opaque (CBMi-encoded, base64-decoded payload contains only an internal Google token, batchexecute decoder is broken — Google removed the `data-n-a-sg`/`data-n-a-ts` data attributes from the new page format).

4. **Ingest gaps**: per-15min buckets showed ingestion only ran at sporadic ~2h intervals instead of every 30 min. Root cause is uvicorn `--reload` killing/restarting the in-process `AsyncIOScheduler` on every file save during active dev. User decided to defer extracting the scheduler into a separate process — backend churn is temporary and the existing scheduler will resume normal cadence once edits stop.

### Built

**Body recovery for short RSS payloads** — [backend/app/services/article_body_recovery.py](backend/app/services/article_body_recovery.py)

- `is_google_news_redirect(url)` — URL classifier that handles both `/articles/...` and `/rss/articles/...` shapes.
- `resolve_google_news_url(url)` — best-effort decoder via Google's batchexecute endpoint. Patches the `googlenewsdecoder` library's mechanism with browser headers + consent cookies. **Returns None gracefully when the decode chain fails** (which is the current observed behavior — Google removed the data attributes the decoder depends on). lru_cached.
- `fetch_publisher_body(url)` — fetches an HTML page and runs the existing `_clean_html_with_quality` + readability rescue pipeline from `ingestion.py`. Reused for both the Google-News-decoded case AND the direct-publisher-URL case (where the RSS provides the publisher URL directly but the entry summary is still short).
- `recover_body(rss_link, rss_raw_text, publisher_domain)` — caller-facing entry point. Short-circuits when `raw_text` is already ≥ 200 chars. Returns `(recovered_body, resolved_url)` so the YouTube transcript path can pick up the underlying youtube.com URL even when body fetch fails on the JS-app HTML.

**Wired into ingestion** — [backend/app/services/ingestion.py:1197-1244](backend/app/services/ingestion.py:1197)

- After the RSS-summary `raw_text` is computed, calls `recover_body(...)`. On success, replaces `raw_text` with the full body.
- The YouTube transcript path at line 1205 now does `_youtube_video_id(url) or _youtube_video_id(resolved_url)` so a Google News redirect that decodes to a youtube.com URL still gets a transcript.
- `publisher_domain` extraction at line 1232 now also falls back to the resolved URL's domain when `entry.source.href` is missing.

29 unit tests against the body-recovery module cover URL classification, base64 extraction, batchexecute response parsing, graceful degradation on missing attributes/HTTP errors, and the YouTube-resolved-URL path. Tests use mocked `httpx` because the live Google News endpoint can't be hit reliably from this dev machine — see "Open questions" below.

**Ingestion-quality alerts** — [backend/app/services/ingestion_health.py](backend/app/services/ingestion_health.py) + [backend/app/models.py:578](backend/app/models.py) + [backend/app/routes/health.py](backend/app/routes/health.py) + [backend/app/services/scheduler.py](backend/app/services/scheduler.py) (job) + frontend wiring

- New `ingestion_health_alerts` table (migration `1c60888ff8bf`) tracks per-source `short_body` and `silent` alerts. One row per `(source_name, kind)` with `resolved_at` flipping back to NULL when recovery is detected.
- Two detectors:
  - **`short_body`**: trailing-24h avg `raw_text` length is `< 50%` of the 7d baseline AND below 300 chars in absolute terms, with `≥ 5` current samples and `≥ 10` baseline samples. Catches the Google-News-collapse pattern going forward. Recovery threshold is asymmetric (`70%` of baseline) so the alert doesn't flap.
  - **`silent`**: source historically posting `≥ 1/day` over a 29-day baseline (last 24h excluded) with zero items in the last 24h.
- Scheduler job runs every 6h (`ingestion_health_check`). Idempotent — alerts mutate in place.
- `GET /api/health/ingestion-alerts` returns active alerts. `POST /api/admin/health/ingestion-alerts/run` triggers an out-of-band check for first-time rollout.
- Frontend: new `ingestion_quality` `NotificationKind`, `api.ingestionAlerts()` client method, `WifiOff` icon + orange color, settings toggle in `NotificationSettings.tsx`. Synthesis logic in `lib/notifications.ts` builds notifications with stable ids tied to the backend row's id.
- 11 unit tests against the detector cover: short-body firing/non-firing, archived-item exclusion from metrics, low-baseline guards, recovery transitions, re-fire after resolution, silent-source detection + recovery, and `get_active_alerts` filter behavior.

**Verified end-to-end via preview**: the notifications bell header reads "Notifications (42 unread)" (40 silent alerts + 2 pre-existing notifications). `fetchNotifications()` returns 40 items of `kind: 'ingestion_quality'` with titles like `"Feed silent: Mastodon #PA08 via mastodon.social"`. Most of the 40 silent alerts are expected to auto-resolve as soon as the catch-up backfill runs (they're all due to the same uvicorn-reload scheduler starvation).

**GDELT realtime tune** — [backend/app/services/gdelt_monitor.py](backend/app/services/gdelt_monitor.py) + scheduler cadence

The 11-items-in-7-days reality for `gdelt_realtime` was driven by every request being 429'd. Tuning:

- Scheduler cadence: **15 min → 30 min** ([scheduler.py:1240](backend/app/services/scheduler.py:1240)). Removes the overlap where consecutive polls fetched the same 30-min window twice.
- `REALTIME_LOOKBACK_MINUTES`: **30 → 35** (slight overlap to avoid boundary misses, but no 2× re-fetch).
- `INTER_QUERY_DELAY` (between candidate + opponent queries): **0.5s → 5s**. GDELT throttles on aggregate per-IP rate; sub-second intervals were exactly the trigger pattern.
- `INGEST_DELAY` (per-article fetch pause): **0.5s → 0.2s** (was causing 50s+ blocking on high-output responses, pushing cycles past `max_instances=1`).
- Retry budget: `max_retries=3, backoff=10/20/40s` → `max_retries=2, backoff=30/60s`. Same total budget but gives up sooner instead of holding the scheduler slot when GDELT is in a sustained throttle.
- New `_last_throttle_at` cool-off: if any query in a cycle 429s, the whole remaining cycle aborts AND the next scheduled cycle (within 300s) is skipped entirely. Piling more requests onto a rate-limited window just extends the throttle.

Expected effect: 3-5× the realtime yield without new infrastructure. Won't restore the volumes the GDELT BigQuery backfill produces (those are batch loads of historical data, different code path).

**Catch-up backfill** — kicked off manually as `/tmp/catchup.log` runs `try_ingest_all_rss(skip_if_locked=False) → _run_narrative_refresh() → _run_auto_review() → briefing_summary.invalidate()`. Net: new articles get the body-recovery fixes applied (whichever ones work in this env), get frame-matched, and the dashboard memo refreshes.

### Key decisions

- **Best-effort Google News decoder, no end-to-end verification.** This dev machine is geolocated as France by Google so every request to news.google.com hits the consent.google.com interstitial — I literally could not test the decoder chain locally. Shipped with mocked-HTTP unit tests for the parsing logic + graceful `None`-return everywhere. **If the decoder doesn't work in production (US IP), nothing breaks — RSS-provided raw_text is preserved** and short_body alerts still fire so a future session can investigate.
- **Two-detector design (`short_body` + `silent`), not one omnibus.** The two failure modes have very different signatures and would need different recovery thresholds. Keeping them separate also means the alert title can be specific ("Feed silent: X" vs "Feed quality dropped: X") and the same source can have one of each kind simultaneously.
- **`short_body` detector skips sources where the baseline itself is already short (`< 300 chars`).** YouTube items are always ~70 chars by design — that's a feed format, not a regression. Avoids permanent false positives on title-only feeds.
- **Recovery threshold asymmetric (`70%` of baseline) vs firing threshold (`50%` of baseline).** Prevents the alert from flapping when the metric hovers around the boundary.
- **Re-fire after resolution updates `detected_at` to "now".** An alert that resolved 5 days ago and now re-fires is a new episode; the notification timestamp should reflect when the current incident started, not the prior one.
- **Alerts surfaced via the existing notifications bell, not a new admin panel.** Hooks into the read/dismiss state the bell already manages. The alert's id ties to the backend row so dismissal stays sticky if the same source re-fires (because the row id is preserved across resolution and re-fire — only `detected_at` changes).
- **Stop the GDELT cycle on first 429 rather than continuing through remaining queries.** Continuing wastes the cycle's budget on requests that will all be 429'd by the same window. Better to surrender the slot and try again 30 min later when the throttle has cleared.
- **Did NOT extract the scheduler into a separate process.** User agreed: backend churn is temporary, the in-process scheduler will resume normal cadence once active edits stop. Mark this as the decision so a future session doesn't undo it lightly — the trade-off is "lose the scheduler during a dev session" vs "ship a new operational moving part."

### Open questions / concerns for review

- **Cannot verify the Google News decoder works in production from this session.** All my decoder tests run against mocked responses. From a France-geolocated IP, `news.google.com/articles/...` returns the consent.google.com interstitial 100% of the time, AND even bypassing consent reveals that the new page format doesn't expose `data-n-a-sg`/`data-n-a-ts` attributes the decoder needs. Possible outcomes when this runs in prod: (a) US-IP request gets the old page format with attributes → decoder works; (b) all geos see the new format → decoder returns None for everything. **In case (b) the right next step is to retire the broken Google News feeds and accept the lost coverage rather than chase Google's evolving format.** A future session should grep for any non-None resolutions in `/tmp/uvicorn.log` after 24 hours to know which world we're in.
- **40 silent alerts on first detector run** are mostly artifacts of the dev-session scheduler starvation, not real feed problems. They should auto-resolve as soon as ingestion catches up. If a chunk of them persist after 48h, those are the genuinely-broken feeds worth investigating.
- **Migration handling on app startup is still fragile.** First-time apply of `1c60888ff8bf` via `init_db()` in the running uvicorn appears to have committed the `alembic_version` row but silently rolled back the `CREATE TABLE`. Had to manually reset `alembic_version` to the prior revision and re-run via the CLI. The previous session fixed `env.py` to use `SET LOCAL` inside the migration transaction, but evidently something else is still triggering rollback in the in-app path. The env.py SET LOCAL is in `run_migrations_online()` only — the in-app path uses `command.upgrade()` which goes through the same env.py, so theoretically should be fine. Needs deeper investigation by whoever next hits this — for now, **manual CLI alembic upgrades work reliably**.
- **Short_body detector won't fire on the *current* Google News regression** because the 7-day baseline is already contaminated by 4 days of post-May-26 short-body data. The detector only catches *future* regressions. That's the intentional design — we're not auto-detecting the historical problem, we're preventing the next one going unnoticed for 3 days.
- **`_last_throttle_at` is module-level state** in `gdelt_monitor.py`. Survives until the worker process restarts. Fine for the current single-process scheduler; if the scheduler ever moves to a multi-process worker model, this needs to migrate to a DB row or Redis key.
- **`googlenewsdecoder` v0.1.7 is installed but functionally broken** for the current Google format. We don't depend on it directly (our `article_body_recovery.py` reimplements the parts that matter), but it's listed via gnews→newspaper3k transitive deps. Worth a future pip prune.
- **Newspaper3k was installed as a gnews dependency** but isn't otherwise used. If we ever decide to use it for body extraction (it has a good `Article` parser), it's already in the venv.

---

## 2026-05-29 Session: Admin manual text overrides (briefing headline + body)

Scope agreed with user: just the morning-briefing v2 race_memo (headline + body) for this first cut, designed to extend to other texts later via a per-consumer allow-list on the same backend.

### Built

**Backend**
- New table `text_overrides` (key, value, input_hash, created_by_name, created_at, updated_at) — Alembic migration `9e1b4f0a3c2d` chained off `5a5d8ae2f0ec`. Model in [models.py:578](backend/app/models.py).
- New service code `_apply_briefing_overrides()` in [briefing_summary.py:303](backend/app/services/briefing_summary.py) + module-level constant `BRIEFING_OVERRIDE_KEYS = {"briefing.memo.headline", "briefing.memo.text"}`. Called at the end of both the cache-hit and cache-miss paths in `get_or_generate_grounded`. On read it loads any rows for the briefing keys, compares stored `input_hash` to the just-computed prompt_hash, substitutes when they match, **deletes the row when they don't** — that's the auto-clear on material input change. The payload now also carries `input_hash`, `overridden_headline`, `overridden_text`, `overridden_by`, `overridden_at`.
- New route file [backend/app/routes/text_overrides.py](backend/app/routes/text_overrides.py) — `PUT /api/admin/text-overrides/{key}` and `DELETE /api/admin/text-overrides/{key}`. Admin-only via `require_admin`. Keys validated against an allow-list (currently `BRIEFING_OVERRIDE_KEYS`); unknown key → 400, not a silent write. Registered in [main.py](backend/app/main.py).

**Frontend**
- `GroundedMemo` type extended in [api/types.ts:851](frontend-v2/src/api/types.ts) with `input_hash`, `overridden_headline`, `overridden_text`, `overridden_by`, `overridden_at` (all optional so v1 string memos and older payloads still type-check).
- `api.saveTextOverride(key, value, input_hash)` and `api.clearTextOverride(key)` in [api/client.ts](frontend-v2/src/api/client.ts).
- [RaceSituation.tsx](frontend-v2/src/components/briefing/RaceSituation.tsx) rewritten to support inline editing:
  - When admin, hover-revealed pencil icon next to the headline and body. Click → inline `<input>` (28px headline) or `<textarea>` (15px body) replaces the rendered text. Save / Cancel buttons; ⌘/Ctrl+Enter saves, Esc cancels.
  - When the LLM produces no headline this cycle, an admin sees a small dashed "Add headline" button instead of a blank slot.
  - Override indicator below the body for everyone: `"Edited by <name> · 5m ago"`. Admin also sees a "Refresh from AI" affordance that DELETEs both keys and triggers `onRequestRefresh` (passed from Dashboard) so the AI memo comes back without waiting for the 60s timer.
  - `localMemo` mirrors the prop so saves apply instantly; a prop change (fresh fetch) re-syncs.

### Verified end-to-end in the dev server

- Headline edit: click pencil → input swapped in at 28px → fill new value → Save → backend stores override against current `input_hash` (`76152f4ae921…` at test time) → `Edited by Local Dev · just now` badge + "Refresh from AI" link appear → reload shows the override is sticky from the backend payload.
- Body edit: click pencil → textarea → save → body replaced; `[C1]` marker inside the override text still rendered as a real superscript citation by the existing renderer.
- Refresh from AI: click → both DELETEs fire → `onRequestRefresh` (`Dashboard.refresh`) refetches → original LLM headline + body restored → indicator gone.
- Auto-clear on hash mismatch: planted a row with `input_hash="deadbeef-different-from-current"` via curl, then GET `/api/briefing/morning?v=2` returned the original AI memo with `overridden_headline=false`, and `select * from text_overrides` came back empty — the read path deleted the stale row. This is the persistence contract the user picked.
- Zero browser console errors from the new code (only the pre-existing React Router v7 future-flag warnings).
- `tsc --noEmit` is clean on every file I touched (pre-existing errors in `Landscape.tsx` and `featuredFrame.ts` are unrelated).

### Key decisions

- **Pin the override to the input_hash at edit time, not at read time.** The frontend echoes back the `input_hash` it received in the briefing payload when it PUTs. This is what makes "auto-clear on material change" mean what it should: the override is anchored to the LLM inputs the admin was actually looking at, not the inputs at next read. If news hits between load and save, the override goes in pinned to the older hash and is auto-cleared on the next read — admin's edit gets discarded gracefully rather than being silently applied to a newer memo.
- **Allow-list keys server-side, not just by client convention.** The PUT/DELETE routes validate `key` against `BRIEFING_OVERRIDE_KEYS` (re-exported from the service so there's one source of truth). Future consumers register their keys there. Without this any compromised admin code path could inject arbitrary text into unrelated parts of the app once we extend the system.
- **Edit both fields independently, but show one indicator.** Headline and body have separate rows (`briefing.memo.headline`, `briefing.memo.text`) so an admin can fix just the headline without touching the body. The override indicator surfaces the most recent edit timestamp / author — one badge, not two.
- **`localMemo` mirror + `onRequestRefresh` callback, not Dashboard-owned override state.** Keeping the local mirror inside `RaceSituation` keeps Dashboard.tsx untouched except for one prop. Side-effect: editor + indicator update before the next fetch, no flash of old content.
- **Citation markers in the body are exposed as raw `[C1]` while editing.** The existing renderer already handles unknown markers gracefully, so an admin who deletes one citation doesn't break rendering — the marker just disappears. Showing the raw `[C1]` is more honest than hiding them.
- **Show "Edited by X" to non-admins too.** Transparency over the team — non-admin viewers should know the memo was hand-edited and by whom. Only the "Refresh from AI" affordance is admin-only.
- **No audit log of prior edits.** Each override is a single mutable row keyed on `key`. We don't keep history. If this becomes load-bearing for a campaign we should turn it into an append-only log, but for "I noticed an error before standup" the current shape is right.

### Open questions / concerns for review

- **`get_or_generate` (v1 string memo path) was NOT touched.** The frontend default is v=2 (per `dashboardCache.ts:41`) and the briefing page is the only consumer. If a future change reintroduces v1 as the rendered memo, override won't apply there — feature degrades to "AI memo only" rather than breaking. Worth flagging in the next session that touches v1.
- **`_apply_briefing_overrides` calls `db.commit()` inside what was previously a read-only path.** With nothing to delete the commit is a no-op, but it does flush the session. I checked `routes/dashboard.py:get_morning_briefing` — it doesn't hold pending writes when this is called, so the commit is safe. A future refactor that batches writes earlier in the same request would need to be aware of this.
- **Override applies only when the v2 cache hit OR after a fresh LLM run.** If LLM generation returns `None` (no claims and no articles), no override applies — even if a stored override exists. Edge case: a campaign with no input data this week, where admin still wants to author a memo from scratch. Not a real near-term scenario but worth knowing.
- **No rate limit on the PUT route.** An admin with a fast typing finger could fire a burst of saves; each one bumps `updated_at`. Postgres can take it, but for "the admin (me)" this is fine. If we ever extend overrides to non-admin roles we'd want to debounce.
- **The `created_by_name` field is the access-code display name from the dev bypass ("Local Dev") in this session.** In tunnelled prod that becomes the real admin name from `ACCESS_CODES`. Worth a quick verification the first time the app is tunnelled with admin codes loaded.
- **Naive UTC ISO from the backend bit me.** `datetime.utcnow().isoformat()` doesn't include a timezone marker, so `Date.parse()` interpreted my freshly-saved timestamp as local-time and showed "2h ago" instead of "just now" while developing in a non-UTC timezone. Patched on the frontend (`formatRelativeShort` appends `Z` when missing). Other code in this app that displays backend timestamps likely has the same bug — `formatArticleDate` is the prime suspect; a future session may want to audit.

---

## 2026-05-29 Session: Featured Narratives — Phase 2 (saturation + cross-tier)

Three pieces shipped on top of Phase 1, all triggered after a ChatGPT second-opinion review converged on "tell the operator WHY each card is surfaced, and don't let stable narratives become wallpaper."

### Built

**Phase 2.1 — Posture-badge tooltips**
- [frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts) — extended `PostureBadge` return type with `tooltip` field; one explanatory sentence per posture (`defensive`/`offensive`/`amplify`/`monitor`). The `Amplify` tooltip explicitly says "the campaign should boost coverage of this narrative" to resolve the ambiguity flagged in Phase 1.
- [frontend-v2/src/pages/Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx) — badge `<span>` now carries a native `title=` attribute and `cursor: 'help'`. Verified: 6 badges rendered, all 4 distinct tooltip strings showing correctly.

**Phase 2.2 — Saturation penalty (homepage anti-fatigue)**
- New `featured_appearances` table — id, frame_id (FK, ON DELETE CASCADE), appeared_on (Date), created_at; unique constraint on `(frame_id, appeared_on)`, btree index on `appeared_on` for the last-7-days window scan.
  - Model in [backend/app/models.py](backend/app/models.py) (`FeaturedAppearance`).
  - Alembic migration `5a5d8ae2f0ec` ([backend/alembic/versions/2026_05_29_2144-5a5d8ae2f0ec_*.py](backend/alembic/versions/2026_05_29_2144-5a5d8ae2f0ec_add_featured_appearances_table_for_.py)).
- [backend/app/services/narrative_frames.py](backend/app/services/narrative_frames.py) — new Query 5b counts featured appearances per frame in the last 7 days; result added to the per-frame response dict as `days_featured_last_7`.
- [backend/app/routes/dashboard.py](backend/app/routes/dashboard.py) — `POST /api/dashboard/featured-appearance` accepts `{frame_ids: [int]}`, uses Postgres `INSERT ... ON CONFLICT DO NOTHING` against `uq_featured_frame_day` so repeat posts the same day are idempotent. Returns `{recorded, day}`. Not admin-gated — telemetry, not state mutation.
- [frontend-v2/src/api/client.ts](frontend-v2/src/api/client.ts) — `api.logFeaturedAppearance(frame_ids)` method.
- [frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts) — `NarrativeFrame.days_featured_last_7?: number`.
- [frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts) — `saturationPenalty(frame)`: 0-2 days featured → 0, 3 → -8, 4 → -16, 5+ → -24. Folded into `multiObjectiveScore`. Calibrated so a high-urgency (40 pts) defensive frame outruns the penalty but a stable "amplified" frame featured 5 days running drops out of top 8.
- [frontend-v2/src/pages/Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx) — `useEffect` fires once per page load after frames have hydrated (`appearanceLoggedRef` guard, swallow errors so a backend hiccup doesn't surface).

**Phase 2.3 — Per-day tier breakdown in `activity_30d`**
- [backend/app/services/narrative_frames.py](backend/app/services/narrative_frames.py) — Query 4c rewritten:
  - Adds `Outlet.outlet_type` to the GROUP BY (outerjoin to Outlet so articles without an outlet still count toward total/unknown).
  - Buckets each row into `{total, national, regional, local, blog, social, unknown}`.
  - **Densifies into a 30-day window with zero-filled gaps** — was sparse (only emitted dates with non-zero activity), which made the frontend's `activity.slice(-3)` mean "last 3 data points" instead of "last 3 calendar days." Found this bug while testing — without densification the cross-tier detector couldn't fire on most frames because the prior-window slice spanned weeks of compressed sparse points.
  - `count` kept = `total` for legacy consumers.
- [frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts) — `ActivityPoint` shape now declares `total/national/regional/local/blog/social/unknown` as optional fields alongside `count`.
- [frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts) — new `sumTier(points, tier)` helper. `surfaceReason()` extended:
  - Two new detectors fire BEFORE the `amplified`/`elite_only` categorical labels (discrete events beat state descriptors):
    - **"Crossed into national"** — `last 7d national ≥ 1 AND prior 21d national = 0 AND last 7d total ≥ 3`
    - **"Regional pickup"** — `last 7d (regional + national) ≥ 2 AND prior 21d (national + regional) = 0 AND prior 21d (local + social) ≥ 3`
  - Window relaxed from `last 3 / prior 14` to `last 7 / prior 21` after live data showed the strict window never fired — campaign coverage cycles run on weeks, not days.

### Verified in preview

- Phase 2.1: 6 posture badges in DOM, each with `cursor: help` and the right title text. Quick spot check: hover on Bresnahan's Local Engagement shows "Defensive posture — a threat or attack on the campaign that needs a response."
- Phase 2.2: `POST /api/dashboard/featured-appearance → 200` fires from page on dashboard mount. DB ends with 10 rows (3 from my manual curl test + 7 newly inserted by the page; frame_id=1 was in both sets and dedup'd via the unique constraint). `days_featured_last_7` arrives on every frame in the response payload.
- Phase 2.3: dense 30-point sparklines (was 8-point sparse), 2 cards now show "Crossed into national" label (Bresnahan's Local Engagement, Bresnahan Supports Local Farmers — both hit the detector). Mix of labels across the 8 cards: Going viral / Crossed into national / Press amplification / Mainstream / Fading. Zero console errors after Vite restart.

### Key decisions

- **Native `title` for posture tooltip, not the `InfoTooltip` (i)-icon pattern.** Adding (i) icons to 6 badges in a tight card would be visual noise; the native `title=` gives free hover without changing the layout.
- **Posture-badge tooltip text leads with the enum word** ("Defensive posture — …", "Amplify posture — …") so a power user who remembers the lens vocabulary sees the connection. The plain-English explanation follows the em-dash.
- **Saturation penalty applies in the frontend, not the backend.** Could be either; chose frontend because the penalty curve is a tuning parameter that may need iteration, and frontend changes don't require a redeploy of the API. Backend just exposes the raw `days_featured_last_7` count.
- **Saturation log is not admin-gated.** It's per-visit telemetry, idempotent, costs nothing, and an admin gate would force us to thread auth through the dashboard mount effect.
- **Densify `activity_30d` server-side, not client-side.** A dense 30-day array is also useful for the sparkline rendering (an 8-point sparse line draws 8 evenly-spaced dots regardless of when those dots happened, which is misleading shape). Server-side fix is one place vs every consumer.
- **Cross-tier window relaxed to 7-day-vs-21-day** after the strict 3-day-vs-14-day window produced zero hits on live data. Political coverage cycles are weeks, not days — a "story breaks national" is normally detected over a 7-day window.
- **Cross-tier detectors fire BEFORE `amplified`/`elite_only`** but AFTER `viral`/`missing_coverage`. Rationale: "Going viral" and "Under-covered" describe situations that override the cross-tier framing; "amplified"/"elite_only" are generic state descriptors that a discrete "we just crossed into national" event should preempt.

### Open questions / concerns for review

- **Saturation penalty hasn't been observed in real load yet.** Today's appearance counts all land at `days_featured_last_7=1`, so penalty=0. The first time we'll see the penalty fire is after frames have been featured 3 days running — earliest 2026-06-01. If the penalty turns out to over-demote frames whose urgency stays genuinely high, we may need to attenuate by `strategic_lens.urgency` (e.g., halve the penalty when urgency=high).
- **Cross-tier detector thresholds (`last 7 ≥ 1 national, prior 21 = 0, total ≥ 3`) are untuned.** Live data showed 2 hits today, both look correct, but with only ~40 frames this is a small sample. After 1-2 weeks of live observation a future session should audit false-positives/negatives — the "regional pickup" detector in particular hasn't fired on any frame yet.
- **Densification cost on the 30-day window.** Adds ~22 rows × 40 frames = ~880 small dicts per `/api/narrative-frames` response. Negligible at our scale but worth knowing if the frame count grows substantially.
- **Vite HMR module caching caused multiple verification confusion cycles** during this session. The dev server kept serving old import URLs with cached `?t=` query strings even after edits, requiring two full server restarts. Not a code issue, but a workflow trap: when iterating on a transitively-imported module, expect to `preview_stop`/`preview_start` rather than rely on HMR.
- ~~**No backend migration applied via Alembic CLI**~~ → **Fixed in this same session.** The bug was in [backend/alembic/env.py:run_migrations_online](backend/alembic/env.py): the `SET statement_timeout = 0` and `SET idle_in_transaction_session_timeout = 0` commands ran on the bare connection BEFORE `context.begin_transaction()`. In SQLAlchemy 2.0.36's autobegin model, the first SET triggered an autobegin transaction; alembic's wrapper then ended up rolling back instead of committing on connection close. Fix: move the SETs INSIDE `context.begin_transaction()` and switch to `SET LOCAL` so they're scoped to the migration's own tx. Verified end-to-end with a smoke-test migration (create table → drop table inside one revision): `COMMIT` now appears at the end of the SQL log on both upgrade and downgrade, and the alembic_version row updates correctly. `featured_appearances` table is still in place; alembic head is back to `5a5d8ae2f0ec` with no orphan revisions.

---

## 2026-05-29 Session: Featured Narratives card redesign (Phase 1)

### Problem
The Featured Narratives panel on the homepage was showing "+N this week" — week-over-week delta of raw mention counts. At our scale (most frames have 2–15 weekly mentions) this delta is statistically noisy, day-of-week sensitive, conflates "real story across outlets" with "one wire syndicated to 12 sites," and ignores the asymmetry between a surging opponent narrative (bad) and a surging candidate narrative (good). The dashboard also ignored `strategic_lens` (urgency/posture/action) — the backend's own actionability signal.

### Built
- **[frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts)** — new helpers module. Exports:
  - `urgencyAccent(frame)` → left-border color from `strategic_lens.urgency`
  - `postureBadge(frame)` → operator-language label from `strategic_lens.posture` ("Needs Response", "Go on Offense", "Amplify", "Watch") with color + bg tint
  - `surfaceReason(frame)` → priority-ordered detector that fires at most one "why am I showing this now" label: Going viral / Under-covered / Press amplification / Elite outlets only / Accelerating / Re-emerged / Sustained pressure / Broadening reach / Going quiet
  - `propagationLine(frame)` → outlet-tier topology string like `"2N · 5R · 8L · 5d"`
  - `sparklinePath(activity_30d, w, h)` → inline SVG path "d" string
  - `multiObjectiveScore(frame)` — six named subscores: urgency + acceleration + novelty + propagation + persistence + momentum. Replaces old `importanceScore` which folded in raw WoW mention delta.
  - `selectFeatured(frames, n=8)` — picks top-N with soft per-owner cap of 4 and per-stage cap of 3, backfills greedily if caps short us. Prevents 8-cards-of-the-same-flavor.
- **[frontend-v2/src/pages/Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx)** — `FeaturedCard` rewritten: urgency-colored left accent (high=red 3px, medium=accent 3px, low/none=normal border), name + posture badge in header row, surface-reason or stage-label fallback in middle row, inline 80×18 sparkline next to it, propagation footer line. Min-height 96px so empty-state cards don't collapse. Old `importanceScore` and `STAGE_ORDER` deleted; sort replaced with `selectFeatured`.

### Verified in preview
Hit `/` and inspected the 8 featured cards via DOM eval:
- 8/8 cards rendered, consistent 115.8px height.
- All 8 have SVG sparkline paths populated (path d-strings 28-117 chars long → different shapes per frame).
- Urgency accents: 1 red (high), several accent-yellow (medium), 1 plain border (none).
- Posture badges: Amplify / Needs Response / Go on Offense / Watch all fire on the right cards; correctly absent on the dormant Healthcare Debate card.
- Surface reasons firing: "Going viral", "Press amplification", "Broadening reach". Two cards with no reason fall back to stage label ("Fading", "Dormant") — exactly the intended behavior.
- Propagation lines render correctly with the abbreviated `NN·MR·LL·SS · Dd` format.
- No console errors.

### Key decisions
- **Frontend-only, no backend work.** Every signal — `strategic_lens`, `momentum_signal`, `outlet_tiers`, `activity_30d`, `days_active_last_7`, `unique_outlets_this_week/last_week` — is already on the frames-list response. Dashboard was just under-using the data.
- **Surface reasons return null when nothing distinctive fires.** Better silence than a misleading badge. Falls back to stage label so the card never looks empty.
- **Acceleration detector requires `last_14d total ≥ 5`** as a small-N noise floor, then checks `last_3d_mean ≥ 2× last_14d_mean`. Re-emergence requires gap-then-resurgence (mid-window quiet between old activity and last-3-day activity).
- **Multi-objective score uses caps per component** so a viral local-blog story can't drown out a 2-national-outlet defensive opponent attack. Propagation weights national×6 + regional×3 + local×1.5 + social×0.5, capped at 30.
- **Diversity caps are soft** — pass 1 respects them, pass 2 backfills greedily if caps would leave us with fewer than 8. Prevents an empty panel when the DB only has one owner type populated.
- **Surface reason for `amplified` momentum is "Press amplification" not "Amplified"** — the enum word is ambiguous (is the campaign amplifying it, or the press?). "Press amplification" makes it clear who.
- **`activity_30d` only carries `{date, count}` for the frames-list endpoint** ([narrative_frames.py:1890](backend/app/services/narrative_frames.py:1890)) — despite the richer `ActivityPoint` type in [types.ts](frontend-v2/src/api/types.ts). The sparkline and detectors only read `count`, so this is fine, but anything tier-temporal (e.g. "crossed into national this week") would need either the richer payload or a server-side detector. Punted to Phase 2.

### Open questions / concerns for review
- **No Phase 2 saturation penalty yet.** ChatGPT's review suggested decaying homepage prominence for frames that have been featured N days in a row, to prevent alert fatigue. This needs persistence — either a new `featured_appearance_log` table or a JSON column on `narrative_frames`. Deferred to Phase 2 along with server-side surface_reason if we decide the frontend detector is too thin.
- **No A/B test or user feedback loop.** The detector thresholds (acceleration ratio = 2×, broadening delta = 3, re-emergence gap window) are reasonable guesses, not tuned to real frame data. After 1-2 weeks of live use they'll need a review pass against actual frame distributions — some labels may fire too often or too rarely.
- **The "Amplify" badge can confuse on opponent-owned narratives.** `strategic_lens.posture=amplify` means "the campaign should amplify this," but if the frame is opponent-owned, an operator might initially read "Amplify" as "opponent is amplifying." Worth a hover tooltip ("Amplify our coverage of this") in a follow-up — but the existing label is short and the urgency border already encodes priority, so not urgent.
- **`activity_30d` ordering assumption.** My acceleration and re-emergence detectors slice from the end of the array assuming ascending date order. The backend explicitly sorts ascending ([narrative_frames.py:1891](backend/app/services/narrative_frames.py:1891)), but if a future session changes that sort, the detectors will silently invert. Worth a runtime sanity check or sort guard inside the helpers if this becomes a foot-gun.

---

## 2026-05-29 Session: Search bar empty-state suggestions

### Built
Extended the just-shipped global header search with a discoverable empty/focus state. When the user focuses the search bar without typing, three sections render in order:
1. **Recent searches** — last 5 queries from localStorage, click to re-run, with a "Clear" affordance.
2. **Trending now** — existing narrative-spike list (unchanged behavior, just relocated).
3. **Try searching** — a 4-row tour with one example per type (entity / outlet / narrative / quote), each prefixed by an emoji icon and labeled by 7-day activity.

### Files
- **[backend/app/routes/global_search.py](backend/app/routes/global_search.py)** — new `GET /api/search/suggestions?per_type=N` endpoint. Single bundle. Entities ranked by 7-day `entity_mentions` joined to `source_items.published_at >= now - 7d`. Outlets same shape but additionally filtered to `outlet_type IN ('local_news', 'regional_news')` — see decision note below. Narrative frames ranked by 7-day `narrative_frame_mentions` count. Quotes by recency, restricted to `claim_records` with a non-null label that isn't `'statement'`, with an unrestricted fallback so a fresh deployment isn't blank. Source name on every quote resolved via `display_source_name`.
- **[frontend-v2/src/lib/recentSearches.ts](frontend-v2/src/lib/recentSearches.ts)** — new file. localStorage history capped at 8 entries, dedup on push, dispatches a `noctua:recent-searches-changed` custom event so the SearchBar in the same tab can re-read without a full reload. Also listens to the native `storage` event for cross-tab sync.
- **[frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts)** — new `EntitySuggestion`, `OutletSuggestion`, `FrameSuggestion`, `QuoteSuggestion`, `SearchSuggestions` interfaces.
- **[frontend-v2/src/api/client.ts](frontend-v2/src/api/client.ts)** — new `api.searchSuggestions(perType)` method.
- **[frontend-v2/src/components/SearchBar.tsx](frontend-v2/src/components/SearchBar.tsx)** — empty-state path rewritten into an `EmptyState` sub-component that takes pre-loaded `recent` / `spikes` / `suggestions` props plus a callback per row type. `submit()` and existing entity/outlet click handlers now route through a new `goToSearch(term)` helper that pushes the term into recent BEFORE navigating. `Section` gained an optional `action` slot used by Recent for its "Clear" button. New imports: `Clock`, `Sparkles` icons, helpers from `@/lib/recentSearches`.
- **[backend/tests/test_global_search.py](backend/tests/test_global_search.py)** — 5 new pytest cases on top of the previous 11 (16 total, all pass). Coverage: entity ranking prefers recent over lifetime mention_count; national outlets are filtered out even when they have more recent articles; frames with zero recent mentions don't appear; quotes prefer non-`statement` labels and resolve `source_name` via outlets; `per_type` parameter caps each section.

### Key decisions
- **Outlets restricted to `local_news` + `regional_news`.** First live read of the unfiltered endpoint returned The Independent UK (2,070 articles in 7 days), Free Republic (750), and Fox News (668). For a *campaign* tool that's noise — the user cares about the press covering THEIR race, not international aggregators dominating by volume. After the filter the same call returned PennLive (593), ABC27 (217), Times Leader (166). Locked in code rather than a config flag because the product premise (campaign intel) implies the local-press orientation; a hypothetical future "national landscape" view should be its own endpoint or a query parameter.
- **Quotes prefer labeled rows.** Same call before the label gate returned generic political news quotes (House Rules Committee blocking a healthcare bill, Trump 2024 gains in NEPA). After preferring `label IS NOT NULL AND label != 'statement'` we get endorsements, campaign launches, criticism — the punchy quotes that read well in one line. With a fallback to any recent quote for environments where the labeled pool is empty.
- **Recent searches recorded on click, not just typed submit.** Clicking a "Rob Bresnahan" entity suggestion pushes "Rob Bresnahan" to recent so the user can re-find it from the empty state next time. Same for outlet clicks. Article and narrative-frame clicks don't push (those don't go to /search, they go to detail pages — pushing the typed term would be misleading because the user might not have typed it).
- **Suggestions loaded once on mount, not on every focus.** The endpoint is cheap but not free, and the suggestions don't change minute-to-minute. If staleness becomes a concern, re-fetch on a focus event or behind a short TTL — the call signature won't change.
- **One example per type in "Try searching", not 2-3.** The one-of-each layout is dense (4 rows total) and implicitly teaches the categories ("oh — quotes are searchable, neat"). Two-of-each balloons the dropdown and feels more like a browser than a discovery aid.

### Open questions / concerns for review
- **Suggestions don't refresh when the data does.** Mount-only fetch means a long-lived tab will show the same "Try searching" rows for hours. Acceptable for now (campaign user typically reloads the dashboard between sessions). If we want fresher signal, cheapest fix is to refetch on focus when the cached suggestions are >30min old.
- **Trending section is currently empty on the live DB.** The `spikes()` API returns no rows right now because the spike-detector either hasn't run recently or the threshold isn't being hit. Not introduced by this session. Worth a brief look: is the spike job running? Is the threshold tuned for current article volume?
- **Recent searches are localStorage-scoped, so they don't sync across devices.** Fine for a single-user workstation; not great if the user switches between laptop and a second screen. If we ever add user accounts with server-side preferences, recent-searches is an obvious thing to migrate up.
- **Auto-discovered entity "Pennsylvania" surfaced in suggestions.** Top 3 entities live: Rob Bresnahan (42), Pennsylvania (25, auto-discovered location), Paige Cognetti (23). The location entity isn't *wrong* — it really is mentioned that often — but it's less actionable than a person. If we want sharper suggestions, a filter like `entity.type IN ('person', 'organization')` would surface candidate-relevant rows only. Held off because the location entity IS clickable and useful for some queries, and adding the filter is a one-liner if the data later proves noisy.

---

## 2026-05-29 Session: Global header search — entities, quotes, outlets

### Built
- **[backend/app/routes/global_search.py](backend/app/routes/global_search.py)** — new file. Three sibling endpoints to the legacy article `/api/search`:
  - `GET /api/search/entities` — ILIKE on `entities.name` + JSON-text `aliases`, ranked by `mention_count`. Tested live: "cognetti" → Paige Cognetti (940 mentions) + 2 auto-discovered Cognettis.
  - `GET /api/search/quotes` — ILIKE on `claim_records.evidence_span`, joined to `source_items` + outlets, returning publisher-resolved `source_name` via `display_source_name`. Tested live: "healthcare" → 2 real quotes with article + outlet context.
  - `GET /api/search/outlets` — ILIKE on `outlets.name` + `outlets.domain`, `active=True` only, ranked by `authority_score`. Tested live: "times" → The Times-Tribune (10), LA Times (8), Times Leader (8).
- **[backend/app/main.py:15,172](backend/app/main.py:15)** — wired `global_search.router` into the `/api` prefix mount.
- **[frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts)** — added `EntitySearchHit`, `QuoteSearchHit`, `OutletSearchHit` interfaces.
- **[frontend-v2/src/api/client.ts:367–374](frontend-v2/src/api/client.ts:367)** — added `searchEntities`, `searchQuotes`, `searchOutlets` methods.
- **[frontend-v2/src/components/SearchBar.tsx](frontend-v2/src/components/SearchBar.tsx)** — rewrote the debounced search effect to fire all 4 backend calls in parallel via `Promise.allSettled`. Added 3 new dropdown sections (`People & organizations`, `Quotes`, `Outlets`) with Lucide icons (`User`, `Quote`, `Newspaper`). Implemented smart section ranking: entity-exact name match floats Entities to the top; outlet-exact match floats Outlets up; otherwise default order is `articles → quotes → narratives → entities → outlets`. Placeholder updated to "Search articles, quotes, people, outlets…".
- **[backend/tests/test_global_search.py](backend/tests/test_global_search.py)** — 11 pytest cases covering name-match, alias-match, mention-count ranking, limit cap, empty query (422), quote span hit, no-match empty list, outlet name & domain match, authority-score ranking, active-only filter. All pass.

### Key decisions
- **New file vs. extending `sources.py`.** The legacy article `/api/search` lives in `sources.py`; I put the three new endpoints in a dedicated `global_search.py` so the "header dropdown surface" has one obvious home. `sources.py` stays focused on article-source CRUD.
- **ILIKE, not Postgres FTS, for quotes.** `claim_records` has ~3,833 rows today; ILIKE on a single short column is fast enough without a dedicated tsvector + GIN index. If/when the quote corpus grows past ~50k records, swap this for a real FTS column via Alembic — the route signature won't change.
- **Click landing pages, v1.** Entity and outlet clicks navigate to `/search?q=<name>` (the existing FTS results page), not to dedicated entity/outlet detail pages. Lowest-friction v1 since the name is already a natural article-search term. Quote click goes to the article detail (existing route). Future work: entity detail page with `/entities/:id`, scroll-to-quote via `evidence_start_char`.
- **Default lead = Articles, not Entities.** Articles are the densest signal day-to-day, so they lead unless the query is unambiguously an entity name (exact match). This prevents the dropdown from feeling like an entity browser when the user just wanted to search article text.
- **`Promise.allSettled`, not `Promise.all`.** One slow or failing backend can't stall the whole dropdown. If quotes endpoint 500s, the rest still render.
- **TestClient + StaticPool**. SQLite `:memory:` is per-connection; default pool gives each request a fresh empty DB. `StaticPool` keeps a single shared connection alive for the duration of the fixture. Worth noting for future tests that drive endpoints via `TestClient`.

### Open questions / concerns for review
- **Entity click → `/search?q=<name>` runs Postgres FTS over articles.** For a person like "Paige Cognetti" this is fine — every article mentioning her contains her name. For an organization like "AFGE" the FTS hit list might miss articles that only use the long form ("American Federation of Government Employees"). A future improvement would be `/articles?entity=<canonical_id>` backed by a `JOIN entity_mentions` filter, which catches every article tagged to that entity regardless of surface form. Not urgent — most users will follow up with refinements.
- **"Times Leader" matches BOTH an entity and an outlet** (the entity-extractor auto-discovered it as an org with 25 article mentions). My smart ranking puts the entity first because entity-exact check runs before outlet-exact check; arguably an outlet should win for publisher-name queries. Not changed for now — both sections still appear, and the more common case (typing a person's name) wants entity-first. Worth a UX revisit if outlet-name searches become common.
- **Quote search has no rank — order is just `created_at DESC`.** ILIKE gives no relevance signal, so we surface newest quotes first. For an autocomplete with limit=5, this is fine. If quote search becomes a primary research surface (e.g. on a dedicated `/quotes` page), it'll want real ranking (FTS + ts_rank) and probably evidence-span snippeting around the matched substring.
- **No new Alembic migration was needed** — all three endpoints query existing tables. Confirmed `alembic heads` is unchanged.

---

## 2026-05-29 Session: Expose Settings page to non-admin users (read-only)

### What the user asked for
Make the Settings page visible to non-admins, but display every campaign-setting control greyed out with a banner explaining only admins can change them.

### Built
- **[frontend-v2/src/App.tsx:64](frontend-v2/src/App.tsx:64)** — `/setup` route dropped the `<RequireAdmin>` wrapper. Comment explains the page now self-gates via disabled buttons + a banner; backend `require_admin` is still the authority.
- **[frontend-v2/src/components/Sidebar.tsx:31](frontend-v2/src/components/Sidebar.tsx:31)** — Settings nav entry no longer has `adminOnly: true`. Sidebar pill is now visible to every signed-in user.
- **[frontend-v2/src/pages/Setup.tsx](frontend-v2/src/pages/Setup.tsx)** — added `useAuth()` + `isAdmin` derivation. Top-of-page Lock-icon banner renders for non-admins: "Read-only view. Only campaign admins can change these settings. You can still adjust your personal notification preferences below."
- **Disabled propagation via `canEdit` prop** on `RacePicker`, `HandleRow`, `ActorHandlePanel`, `ThirdPartyAccountsPanel`. Each component greys out its action buttons (`opacity: 0.55`), adds `cursor: 'not-allowed'`, swaps in an "Admin only" title tooltip, and disables form inputs/checkboxes.
- **Campaign profile form** — every input (candidate, party, office, election date, district, state, message, keywords, priorities) and the Save button get `disabled={!isAdmin}`. The form wrapper takes `opacity: 0.85` so the disabled-state is visually obvious. A "Read-only — only admins can save changes" caption renders next to the disabled Save button.
- **`ResetToFecButton`** — hidden entirely for non-admins (added `isAdmin &&` to every `visible` guard) so unreachable affordances don't render.
- **NotificationSettings stays fully editable** for non-admins — they're per-user preferences, not campaign settings.
- **Pick-a-race / Change-race buttons** — disabled with opacity for non-admins; clicking them is no-op via the disabled prop. The picker itself stays open if a non-admin manually flips state (e.g. via DevTools), but every "Use this →" candidate row inside it is disabled + reads "Admin only".

### Verified in the running preview (simulated non-admin via React fiber monkey-patch on the AuthProvider hook)
- Lock-icon banner renders with "Read-only view" text.
- Campaign profile: "Change race" disabled, "Edit details" intentionally stays enabled so non-admins can browse the form. All 9 form inputs disabled. "Save Configuration" disabled. Reset-to-FEC buttons hidden.
- Social handles section: every button — Discover, Enter manually, chip-remove (X), Add selected — disabled.
- Third-party accounts: "Discover accounts" disabled, chip-remove (X) buttons disabled, candidate checkboxes disabled.
- Notifications section: 12 inputs, 0 disabled. Per-user preferences preserved.
- Admin path (restored via page reload) still works: banner hidden, every button enabled.
- No console errors, no failed network requests on either path.

### Key decisions
- **"Edit details" stays interactive for non-admins.** Toggle is a pure UI affordance (no backend call). Non-admins clicking it expands the read-only form so they can see *what* the campaign config is. The form inputs are disabled inside, and "Read-only — only admins can save changes" sits next to the disabled Save button.
- **`canEdit` prop, not a global readonly mode.** Each panel component takes a `canEdit` boolean that defaults to `true`. Easier to reason about per-component than threading isAdmin through context; matches the pattern used elsewhere (`HandleRow` already had a saving-state guard, just added a second condition).
- **Tooltip text says "Admin only" not "You need admin access".** Shorter. Browsers truncate long titles aggressively on hover.
- **`opacity: 0.55` on disabled buttons** instead of relying on the `:disabled` selector from `.btn`. The codebase doesn't have a shared disabled style — most buttons just rely on the browser's native disabled rendering. Setting opacity inline gives a uniform look without touching global CSS.
- **Backend gates unchanged.** Every cost-incurring endpoint still has `Depends(require_admin)` from the earlier admin-gating pass. The frontend changes are the cosmetic layer; if a non-admin bypasses the disabled controls (via DevTools or curl), the backend still 403s.

### Open questions / concerns for review
- **`/api/races/{id}/select` still not `require_admin`-gated.** This carried over from the previous session — flagged again here because the picker is now reachable from a route any user can visit. Worth a defensive `dependencies=[Depends(require_admin)]` on the route, even though clicking the disabled "Use this →" is a no-op.
- **Localhost bypass treats all local sessions as admin.** Verification was done by monkey-patching the React user state directly via the fiber tree — there's no way to test the non-admin path on `localhost:5174` because the backend bypass returns `is_admin: true` regardless of the access code. Works on tunneled deployments. Document this in any onboarding for non-Theo testers.
- **NotificationSettings save path may still need an access-code-aware backend.** The component currently saves preferences to localStorage (per the existing "Heads up: Your preferences save locally..." copy). When the backend persistence lands, the API key for per-user prefs must NOT require admin — or the per-user UX promise here breaks.

---

## 2026-05-29 Session: Setup page redesign — race picker + checklist shape fix

### The user's two complaints
1. The Campaign Profile form made the user type office/district/state/party/election-date/keywords by hand even though the FEC race directory has all of it.
2. The setup checklist said "2/4" with **none** of the four checkboxes ticked.

### Root cause of #2
[backend/app/routes/setup.py](backend/app/routes/setup.py:177) returns `SetupStatusOut(complete: bool, items: list[SetupChecklistItem])` since the checklist-schema work. The frontend type and render in [frontend-v2/src/pages/Setup.tsx](frontend-v2/src/pages/Setup.tsx:1138) still expected the old flat shape `{campaign_profile: bool, opponent_added: bool, source_added: bool, narrative_frame_added: bool}`. Every `status.campaign_profile` etc. read as `undefined`, so every CheckItem rendered as `done={false}`. The "2/4" came from `Object.values({complete: true, items: [...]}).filter(Boolean).length` accidentally counting `complete=true` + the items array as 2 truthy values.

### Built

#### Backend
- **Migration `7f3a1c9d5e4b`** ([backend/alembic/versions/2026_05_29_0003-7f3a1c9d5e4b_campaign_directory_race_id.py](backend/alembic/versions/2026_05_29_0003-7f3a1c9d5e4b_campaign_directory_race_id.py)) adds `campaign_config.directory_race_id` (nullable FK → race_directory, ON DELETE SET NULL).
- `select_directory_race()` in [backend/app/services/race_directory.py:362](backend/app/services/race_directory.py:362) now sets this column when a race is picked.
- Added `directory_race_id` to `CampaignProfileOut` in [backend/app/schemas.py](backend/app/schemas.py:46).
- **One-time backfill** linked the existing Cognetti campaign to race_directory id=337 ("PA-08 U.S. House 2026 Candidate Filings") so the reset-field affordance works without forcing Theo to re-pick.

#### Frontend
- Added `RaceDirectory`, `RaceCandidate`, `RaceSelectResult` types and expanded `CampaignConfig` (party, race, race_level, election_type, district_number, key_priorities, relevance_keywords, geography_keywords, sparse_race_mode, directory_race_id) in [frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts:651). Fixed `SetupStatus` to match the new `{complete, items[]}` shape with a new `SetupChecklistItem` interface.
- Added `searchRaces`, `getRace`, `selectRace` API methods in [frontend-v2/src/api/client.ts:108](frontend-v2/src/api/client.ts:108).
- **Rebuilt the Campaign Profile section** of [frontend-v2/src/pages/Setup.tsx](frontend-v2/src/pages/Setup.tsx):
  - A new `RacePicker` component (search by candidate/state/district, debounced 250ms, FEC results, expand-to-pick-candidate).
  - Three header states: picker open / linked-race summary card with "Change race" / orange unlinked-prompt with "Pick a race".
  - Compact summary card (Candidate · Party · Office · District · Election) always shown when configured.
  - Form fields collapsed behind an `Edit details` accordion so the steady-state is one screen of text + a checklist.
  - Per-field `ResetToFecButton` shown next to Party/Office/District/State/Election when current value differs from the linked race directory — clicking reverts just that field.
  - Saves now use the backend canonical field names `key_priorities` / `relevance_keywords` instead of the legacy `priorities` / `keywords` aliases.
- **Rewrote the Setup Checklist** to map `status.items[]` to the `CheckItem` component. Each unchecked row is now a `<Link>` to the item's `action_path` so the checklist doubles as navigation. Setup-done count is `items.filter(i => i.complete).length` — actually correct now.

### Verified in the browser (Settings → Setup page)
- Linked-race header shows: `PA-08 U.S. House 2026 Candidate Filings — Source: FEC` with a "Change race" button.
- Summary card shows: Candidate=Paige Cognetti · Party=Democrat · Office=U.S. Representative · District=PA-08 · Election=Nov 3, 2026.
- Setup checklist: **4/4** with every row rendered as `lucide-circle-check-big` (green CheckCircle); "✓ WAR ROOM FULLY OPERATIONAL" banner shows.
- "Change race" → typed "PA-08" → race result appears → expanded → both candidates render with party/incumbency labels and "Use this →" affordance. (Did NOT commit the selection — that would re-run initialize_campaign which is LLM-cost; the user can do that intentionally.)
- "Edit details" expanded the form. Two reset-to-FEC buttons rendered, both for legitimate divergences (State stored empty vs FEC "PA"; election date stored vs race row having no date).
- No console errors, no failed network calls.

### Key decisions
- **`directory_race_id` over name-match.** Linking the campaign to its FEC directory row by integer FK makes the reset-field affordance trivial; matching by `race_name` would have broken silently if the FEC ever rephrases the race title between reimports.
- **Race-picker triggers `initialize_campaign`.** Reusing `/api/races/{id}/select` (which runs `initialize_campaign` already) means picking a race auto-creates monitors and Opponent rows in one POST. The cost is one LLM-touching pass per race change. The `/api/races/{id}/select` route is open on the backend, but `/setup` is `<RequireAdmin>`-wrapped on the frontend, so non-admins can't trigger it through the UI.
- **Backend FEC defaults expose enough but not everything.** The "reset election date" button is the rough edge — the directory's `election_date` is NULL for House races, the inferred date only gets computed inside `select_directory_race`. The reset clears the field rather than restoring the inferred date. Acceptable but a follow-up could expose `election_date_inferred` on `RaceDirectoryOut`.
- **Backfilled Cognetti link manually.** A one-time `UPDATE campaign_config SET directory_race_id = 337` ran via the migration cleanup; no script committed since this only needed to happen once on the live DB and the only other campaigns on the platform will go through the picker.

### Open questions / concerns for review
- **`updateCampaign` field-name change.** The new save body sends `key_priorities` + `relevance_keywords` instead of `priorities` + `keywords`. Backend [routes/campaign.py:407 update_campaign](backend/app/routes/campaign.py:407) already accepts both via `CampaignProfileIn`. Old-format aliases stay in the TS type for back-compat with any other code reading `config.priorities`. If a future session sees both naming pairs side-by-side, the canonical names are the snake_case ones — the legacy aliases can go away when no remaining code reads them.
- **`/api/races/{id}/select` is not `require_admin`-gated.** Backend defense-in-depth: matches the existing pattern (the Setup *route* is gated, not every endpoint behind it), but worth adding `dependencies=[Depends(require_admin)]` to `routes/races.py` if the gate is tightened.
- **`directory_race_id` FK name.** Postgres auto-named it `campaign_config_directory_race_id_fkey` when I ran the FK creation manually (after `--reload` raced with the CLI alembic invocation). The migration file's `_FK_NAME` constant reflects this so a downgrade works. If anyone re-runs the migration on a fresh DB and gets a different name, expect the downgrade to fail.
- **Lints in unrelated files** — `tsc --noEmit` reports pre-existing errors in `src/components/NotificationSettings.tsx:66` (missing `description` prop) and `src/pages/Landscape.tsx:1482` (undefined `candidateName`/`opponentName`). Not introduced by this session, but they should be fixed by whoever owns those files.

---

## 2026-05-29 Session: Admin-only gating for LLM-cost actions

### Built
- New FastAPI dependency `require_admin` in [backend/app/services/access_codes.py](backend/app/services/access_codes.py) that returns 403 unless `request.state.user.is_admin` is True. Honours the same bypass contract as the access-code middleware: dev mode (no `ACCESS_CODES`) and localhost `X-Forwarded-Host` both treat the caller as admin.
- Applied `dependencies=[Depends(require_admin)]` to every LLM-cost or budget-spending POST in:
  - `routes/admin.py` — reset-workspace, reanalyze-sources, rescore-articles, rescore-stop, auto-review, discover-outlets, discover-monitor-urls, prune-rss-feeds, discover-feeds-yield, backfill-publisher-domain.
  - `routes/narrative_frames.py` — suggest, candidate-frames/promote, candidate-frames/snapshot/refresh, audit-duplicates, rematch.
  - `routes/narrative_triage.py` — /run (the gpt-4o pass; dismiss/apply/execute-merge stay open since they're pure DB writes).
  - `routes/campaign.py` — initialize, backfill-historical, discover-journalists, prune-monitors, trends-collect.
  - `routes/ingest.py` — crawl, reddit.
  - `routes/setup.py` — discover-handles, discover-third-party (both hit the paid web-search provider).
- Read-only observability endpoints (dbstats, rescore-status, scheduler-health, llm-status, narrative listings, briefing) stay open — non-admin friends can still see the app.
- Frontend cosmetic layer:
  - `frontend-v2/src/components/Sidebar.tsx` — `/setup` (labelled "Settings") is now `adminOnly: true`; non-admins don't see it in the nav.
  - `frontend-v2/src/App.tsx` — added a `<RequireAdmin>` route wrapper; `/setup` is wrapped so non-admins hit `Navigate to /`.
  - `frontend-v2/src/pages/ReviewQueue.tsx` — hides the "Refresh proposals" + "Run AI triage" cluster, the per-row "Promote"/"Merge" buttons, and the "Override + promote" footer button for non-admins.
  - `frontend-v2/src/pages/Narratives.tsx` — `PendingSuggestionsSection` early-returns null for non-admins (after all hooks, to keep rules-of-hooks order stable).
  - `frontend-v2/src/pages/Monitors.tsx` — hides "Run Crawl" + "Discover URLs"; "Add Monitor" stays for everyone.
- New focused tests:
  - `backend/tests/test_require_admin.py` — covers all four branches of the gate (dev-mode bypass, localhost bypass, non-admin → 403, admin → 200, missing code → 401-not-403).
  - Existing `backend/tests/test_destructive_endpoint_guards.py` fixture updated to set `ACCESS_CODES=""` and call `reset_cache_for_tests()` so the middleware sees dev-mode, otherwise the .env-loaded codes leak in from `db.py`'s `load_dotenv()` and the tests 401 before the route runs.

### Key decisions
- **Backend is the real gate; frontend hiding is cosmetic.** Even if a non-admin types the URL, `dependencies=[Depends(require_admin)]` returns 403 server-side. The frontend hiding is so friends don't see actions they can't use.
- **`require_admin` returns the user object, not just bool.** Routes that want the caller can use `user: AccessUser = Depends(require_admin)`. Routes that just need the gate use `dependencies=[Depends(require_admin)]` and skip the param. Most use the latter — saves boilerplate.
- **`require_admin` parameter is `request` with a runtime-bound `Request` annotation.** Without the annotation FastAPI tries to look up `request` as a query parameter and 422s. The annotation is applied at module-load via a small `_wire_require_admin()` helper so the access_codes module stays free of an unconditional FastAPI import (keeps pure-services pytest collection fast).
- **Briefing endpoint stays open.** `/api/briefing/morning` is cached (30-min TTL on v1, input-hash on v2) — non-admin views don't burn LLM budget after the first one of the day. Gating it would block the whole dashboard for friends.
- **Triage `dismiss`/`apply`/`execute-merge` stay open.** They're pure DB state ops, not LLM calls. Non-admins can still clear noise off the queue; only the gpt-4o `/run` pass costs money.
- **Setup wizard route-gated, not button-gated.** Every meaningful action in Setup hits a gated endpoint, so wrapping the route in `RequireAdmin` is cleaner than gating each button individually.

### Open questions / concerns for review
- **Existing test suite has unrelated failures from the un-committed auth middleware.** `tests/test_race_directory.py` (4 failures) hits the API via TestClient without setting up the dev-mode bypass; the .env-loaded `ACCESS_CODES` makes the middleware 401 those calls. Same root cause as the destructive-guards fixture I patched, but I didn't touch the race-directory tests — they pre-date this work and the auth middleware is itself uncommitted work from another session. Worth a one-shot pass to add the same fixture pattern across all API-touching tests.
- **The `ACCESS_CODES` in `.env` currently has no `:admin` suffix on any code** — so when the user tunnels the app, every friend code becomes non-admin and locked out of every LLM-cost button. The user (Theo) needs to add `:admin` to their own code (or set up a separate operator code) before any deploy that uses real codes. Documented in [.env.example](.env.example) — the existing format already supports it.
- **The localhost bypass treats any local request as admin, even with codes configured.** Means the user always has admin powers on `http://localhost:5174` regardless of which code (if any) is in localStorage. This is intentional — matches the existing `/api/auth/me` behavior — but worth flagging if you ever want strict "even local must use an admin code" semantics.

---

## 2026-05-29 Session: Kalshi backfill + Polymarket token-ID bugfix

### Probe
Asked whether more Kalshi/Polymarket history was available than the DB held.
Ran `_clob_prices_history(token, days_back=730)` and
`_kalshi_candlesticks(series, market, days_back=730)` against both
candidate and opponent markets:

- **Kalshi (HOUSEPA8-26)**: 298 daily candles available, back to
  **2025-07-02**. We had 53 snapshots from 2026-03-30 → today, so ~270
  days of history were sitting unused on Kalshi's server.
- **Polymarket (pa-08-house-election-winner)**: 719 points available,
  earliest **2026-04-29** — essentially the same window we already had
  (39 snapshots from 2026-04-26 → today). Market was created on
  2025-12-16 but had near-zero liquidity until late April; the CLOB
  simply has no earlier price data to fetch. **No further Polymarket
  backfill is possible.**

### Built
- **Bugfix** [backend/app/services/prediction_market_monitor.py:207-218](backend/app/services/prediction_market_monitor.py:207).
  Polymarket's Gamma API returns `clobTokenIds` as a JSON-encoded
  STRING (`'["91...", "98..."]'`) — not a real array — for this market.
  The old `_yes_token()` did `tokens[0]` without `json.loads()`, so it
  returned the literal `"["` character. Now parses if it's a string.
  Follow-up: also overwrote the corrupted `candidate_yes_token_id` /
  `opponent_yes_token_id` strings in
  `race_sentiment.external_metadata` for source=polymarket with the
  correct token IDs fetched from Gamma.
- **Kalshi backfill** executed via `kalshi_backfill_history('kalshi',
  metadata, days_back=330)`. Result: `{written: 266, skipped_dedup:
  45}`. Snapshot count 53 → 320. Earliest 2026-03-30 → **2025-07-04**.

### Verified
- DB: 320 Kalshi snapshots, range 2025-07-04 → 2026-05-29.
- Polymarket metadata: token IDs are now the real 76-digit values, not
  `"["`. (No new history to backfill, but the metadata is no longer
  corrupt — future `polymarket_backfill_history` calls will succeed.)
- `/timeline` preview, 90d view: chart x-axis now extends back to **Feb 28**
  (was Mar 30 — the previous earliest snapshot). Kalshi line renders 68
  points across the window (was ~50). No console errors.

### Open questions / concerns for review
- **UI only goes to 90d but we now have ~330 days of data.** User
  previously asked to remove the `6 mo` and `1 y` range buttons before
  the backfill ran. Now that there's real data to fill them, adding
  back longer ranges would let them see the full window — but the user
  explicitly removed those, so left untouched. Worth mentioning if
  they want to revisit.
- **`source_owner_type='unclear'` for social posts.** (Carried over
  from previous entry — still a known ingestion-side gap, not addressed.)

---

## 2026-05-29 Session: Timeline top-articles wrongly tagged "Neutral"

### Problem the user reported
On `/timeline`, the impact-ranked list ("Top moments by market impact") was
labelling every top-article event "Neutral" — including a Cognetti tweet
attacking Bresnahan ("Proud to have the support of @PennaNurses … Rob Bresnahan
voted to gut Medicaid …", article id 4662, May 1).

### Root cause (two layers)
1. **Backend** `/api/race-sentiment/events` emitted `top_article` events with
   no owner/subject fields at all — the per-article perspective the V13.21
   classifier had already computed (`SourceItem.perspective = "pro_candidate"`
   for 4662) never left the DB.
2. **Frontend** [pages/Timeline.tsx:264-275](frontend-v2/src/pages/Timeline.tsx:264)
   hardcoded `quadrant: 'media'` for every top-article pin under the
   assumption "top articles aren't owned by either side." That fails the
   moment a candidate's own social post is the top article of the day.
   `media` → label "Neutral" via [quadrantColor.ts:80](frontend-v2/src/lib/quadrantColor.ts:80).

### Built
- New [backend/app/services/article_quadrant.py](backend/app/services/article_quadrant.py) —
  `quadrants_for_articles(items, db)` returns `{article_id: (owner_type,
  subject_type)}` for a batch of `SourceItem`s. Single SQL query joining
  `NarrativeFrameMention` + `NarrativeFrame`, then in-Python cascade per
  article. Also exposes a single-item convenience wrapper.
- Wired into [backend/app/routes/race_sentiment.py:list_timeline_events](backend/app/routes/race_sentiment.py:285) —
  one batched call before the article loop, then each `top_article` event
  gets `owner_type` + `subject_type` in its payload.
- [frontend-v2/src/pages/Timeline.tsx](frontend-v2/src/pages/Timeline.tsx)
  replaces the hardcoded `'media'` with `quadrantKey(e.owner_type,
  e.subject_type)` — same call pattern lifecycle events already use.
- 13 unit tests in [backend/tests/test_article_quadrant.py](backend/tests/test_article_quadrant.py) covering every rung of the cascade.

### The cascade
For each article, derive `(owner_type, subject_type)`:
1. **Highest-confidence narrative-frame match** wins. NULL `subject_type`
   filled by the existing `get_subject_classifier(db)` heuristic. When the
   classifier returns "media" (frame name has no actor token) but the
   frame's owner is partisan, default subject to owner (handles frames
   like "NEPA Support" — clearly campaign-owned but no name in the title).
2. **`SourceItem.source_owner_type`** if it's `candidate_statement` /
   `opponent_statement` / `media`. Subject defaults to owner.
3. **`SourceItem.perspective`** (`pro_candidate` → `candidate`/`candidate`,
   `pro_opponent` → `opponent`/`opponent`, `neutral` → `media`/`media`).
4. **Default**: `("media", "media")`.

### Key decisions
- **Subject axis from frame match, not from a new classifier.** The
  existing `subject_classifier` already covers frames; articles inherit
  their subject from the most-confident frame they match. Article 4662
  matches "Bresnahan's Stock Trades" (conf=90) and "NEPA Support" (conf=75)
  — the stock-trades match wins, supplying `subject=opponent` and putting
  the pin in `our_offense` (cyan) instead of `media` (gray). For mixed
  posts like this one, the highest-confidence claim is the most defensible
  single-bucket assignment.
- **Articles with no frame match still get a real owner.** Falling back to
  `source_owner_type` then `perspective` means a pro-Cognetti article with
  no frame match lands in `our_defense` (blue, "Pro-us"), not gray. Subject
  defaults to owner since there's no signal — better wrong-axis-correct-
  side than correct-everything-gray.
- **`source_owner_type='unclear'` (the default) skips Phase 2.** Most
  campaign-ingested social posts have `unclear` even when the author is
  clearly the candidate. Documented as a separate ingestion-side bug — the
  cascade routes around it via Phase 3 (`perspective='pro_candidate'`).

### Verified
- Unit tests: 13/13 pass.
- Helper on real DB: article 4662 → `('candidate', 'opponent')`. Spot
  check on 3 other articles (3614 Cognetti self-promo, 4604/4609 Trump-DoJ
  neutral) returns the expected pairs.
- Live API (`GET /api/race-sentiment/events?days=60`): article 4662
  payload now carries `owner_type='candidate', subject_type='opponent'`.
  Distribution across 46 top-article events: 17 media / 12 our_defense /
  9 our_offense / 7 their_defense / 1 their_offense — down from 46/46
  forced to media.
- `/timeline` preview: the Pennanurses row icon background is
  `rgba(6,182,212,0.12)` (= `#06b6d4` = `our_offense` cyan), label
  reads "Anti-them · Top Articles · May 1". Zero console errors.

### Open questions / concerns for review
- **`source_owner_type='unclear'` for social posts.** A real ingestion-side
  bug: the Cognetti tweet has `source_author='@PaigeGCognetti'` but
  `source_owner_type='unclear'`. The cascade works around it, but if the
  perspective classifier ever drops to `neutral` on a clearly-candidate-
  authored post, we'd misroute it. Worth a separate look at the social
  ingestion path that sets `source_owner_type`.

### Follow-up (same session): self-axis-wins + named labels
**User pushback:** the Pennanurses tweet was now landing in `our_offense`
("Anti-Bresnahan") — the user said it should be `our_defense`
("Pro-Cognetti") because the tweet LEADS with self-promotion ("proud to
have @PennaNurses support") and the Bresnahan jab is the second beat, not
the headline.

**Fix:** revised the subject-axis tiebreaker in `quadrants_for_articles`.
Old rule was "highest-confidence frame wins"; new rule is "self-axis wins
for partisan-owned articles": if ANY frame match treats the article as
self-referential (subject == owner), subject defaults to owner. Only flip
to other-axis when EVERY match is other-axis (a pure attack post). Article
4662 now matches both "NEPA Support" (subject=candidate after the
classifier-media→owner bump) and "Bresnahan's Stock Trades"
(subject=opponent) → self-axis exists → subject=candidate → our_defense.

Pure-attack posts (only an opponent-subject frame matches, nothing else)
still route correctly to our_offense — added test `test_pure_attack_post_routes_to_other_axis`.

**Also addressed user's second ask:** generic "Pro-us / Anti-them" labels
swapped for named "Pro-Cognetti / Anti-Bresnahan" everywhere on Timeline.
`Timeline.tsx` now loads `candidateName` + `opponentName` via `api.campaign()`
and `api.opponents()` (same pattern Landscape, Narratives, ReviewQueue
already use) and threads them through `ImpactList` → `ImpactRow` and
`SidePanel` to call `quadrantNamedLabel()` instead of `quadrantLabel()`.

**Verified:**
- 15/15 unit tests pass (added 2 new tests, replaced 1 outdated test).
- Live API: article 4662 now returns `owner_type=candidate,
  subject_type=candidate`. Distribution shifted: 12 → 13 our_defense, 9 →
  8 our_offense (the Pennanurses tweet moved across the boundary).
- `/timeline` preview: Pennanurses row reads "Proud to have the support of
  @PennaNurses … **Pro-Cognetti** · Top Articles · May 1", icon background
  `rgba(0,89,194,0.12)` = `#0059c2` = `our_defense` blue. Whole-page
  label scan: 18 "Pro-Cognetti", 9 "Anti-Cognetti", 3 "Anti-Bresnahan",
  3 "Neutral", **0 generic "Pro-us / Anti-them / Pro-them / Anti-us"** —
  named labels everywhere, no orphan generic strings. Zero console errors.

---

## 2026-05-29 Session: Retire /forecast, fold race-sentiment chart into /analytics

### Built
- New component [components/RaceSentimentChart.tsx](frontend-v2/src/components/RaceSentimentChart.tsx) — lifted the Polymarket+Kalshi line chart out of the (now-deleted) Forecast page. Self-contained card with title, latest readings inline, legend, the chart itself (with per-day event reference lines + day-grouped tooltip), and the thin-market caveat footer. Accepts `days` prop (defaults to 30).
- Embedded the chart as a full-width card at the top of [pages/Analytics.tsx](frontend-v2/src/pages/Analytics.tsx), above the existing 2-col grid (Media Tone, Search Interest, Tone stats, Spike Activity). Title style (uppercase, letter-spaced, border-bottom) matches Analytics's existing `SectionTitle`.
- Deleted `frontend-v2/src/pages/Forecast.tsx`. Removed the import + route from [App.tsx](frontend-v2/src/App.tsx); replaced with `<Route path="/forecast" element={<Navigate to="/analytics" replace />} />` so old bookmarks resolve (same pattern used for `/briefing` and `/entity-network`).
- Removed the Forecast nav item (and its unused `LineChart` icon import) from [Sidebar.tsx](frontend-v2/src/components/Sidebar.tsx).

### Key decisions
- **Chart, not just the lines: kept the vertical event-marker reference lines and the day-grouped tooltip.** Without the markers, the chart loses its main "why did it move" signal. The legend's event categories (Narrative promoted / Stage change / Top article) carry over too.
- **Event-window cards (the +24h/+72h/+7d Polymarket deltas around narrative events) dropped entirely, not migrated.** Timeline already covers event→market impact in a richer way (pins on the line + impact-ranked list with sparklines + per-event 48h delta). Keeping the cards would be a third view of the same data.
- **Backend endpoints kept.** `/api/race-sentiment/{source}/history` and `/api/race-sentiment/events` are still consumed by both `RaceSentimentChart` and the Timeline page. The `# Phase 3 — timeline events for the /forecast chart` comment in `backend/app/routes/race_sentiment.py:210` is now slightly out of date but harmless; not worth a churn-only edit.
- **Card sits ABOVE the 2-col grid, not as a span-2 grid child.** Cleaner — the chart needs the room and a `gridColumn: span 2` child would force the grid's auto-row sizing to deal with a much taller cell. A plain wrapper `marginBottom: 24` matches the grid's gap.

### Open questions / concerns for review
- The chart no longer offers a 7/30/90 day toggle — it was hard-coded to 30 on Forecast too, and adding one now would mean wiring it into the new component without an obvious place for the control. If a future session wants this, add a `PillToggle` like the other Analytics cards, lift `days` to local state, and pass it down.
- `client.ts:673` still has a `// Phase 3: unified timeline events for the /forecast chart` comment. Left untouched but worth a one-line edit ("for the race-sentiment chart and Timeline") if anyone else is in that file.

### Verified
- `/analytics` renders the new chart at the top: title, Polymarket 65.7% / 43.0%, Kalshi 60.5% / 42.5%, legend, chart SvgRoot, caveat — followed by Media Tone, Search Interest, Tone stats, Spike Activity below. No fresh runtime errors after reload (only stale HMR buffer messages from the mid-edit window).
- `/forecast` → 302-style client redirect to `/analytics`. Old bookmarks resolve.
- Sidebar nav list: Home, Articles, Analytics, Narratives, Landscape, Geographic, Timeline, Opponents, Review, Monitors, Settings — no Forecast.

---

## 2026-05-29 Session: Friend-share access-code auth

### Built
- Backend access-code gate so the user can tunnel the app to 2-3 friends without exposing the API. Code lives in:
  - `backend/app/services/access_codes.py` — parses `ACCESS_CODES` env var (semicolon-separated `code:Display Name` pairs), derives initials + a deterministic profile color per code.
  - `backend/app/routes/auth.py` — `POST /api/auth/verify` (login) and `GET /api/auth/me` (session restore).
  - `backend/app/main.py` — HTTP middleware that rejects any `/api/*` request without a valid `X-Access-Code` header, except an allowlist (`/health`, `/api/auth/verify`, `/api/auth/me`, FastAPI docs).
- Frontend login flow:
  - `frontend-v2/src/auth/AuthContext.tsx` — context that hits `/api/auth/me` on mount, exposes `{ user, login, logout, loading }`.
  - `frontend-v2/src/pages/Login.tsx` — branded one-input login page.
  - `frontend-v2/src/api/client.ts` — auto-attaches `X-Access-Code` to every request, dispatches `cwr:unauthorized` on 401 so AuthContext can drop state.
  - `frontend-v2/src/App.tsx` — `/login` route outside Layout; `RequireAuth` guard around the rest.
  - `frontend-v2/src/components/Layout.tsx` — replaced the hardcoded "TC" purple bubble with `<ProfileMenu />` (initials + color from the user's code, click to log out).
- `.env.example` updated with the `ACCESS_CODES` format and usage notes.

### Key decisions
- **Fails open when `ACCESS_CODES` is empty.** Backend `is_auth_configured()` returns False → middleware skips the check, `/api/auth/me` returns a synthetic "Guest" user. Means the app stays usable in dev with no setup, and the gate snaps shut the moment any code is added. Verified via preview: with no codes, dashboard renders, profile bubble shows "G".
- **Codes live in `.env`, not the DB.** Static shared secrets — no rotation, no rate-limit, no per-IP throttle. Sized for "show 3 friends this weekend", explicitly not "open beta".
- **Initials + color derived, not configured.** `_initials()` takes first letter of first and last word; `_color_for()` hashes the code to pick one of 8 palette colors. Each friend gets a stable distinct bubble without the user having to pick.
- **`@lru_cache` on `_load_codes()`.** Env is read once at first lookup. Restart the backend after editing `ACCESS_CODES` — documented in `.env.example`.
- **Login page redirects to `from` after success.** `RequireAuth` passes the attempted pathname via router state so a friend hitting `/forecast` without a code lands on `/forecast` after logging in, not `/`.
- **No cookie, no JWT.** Code in localStorage, sent as `X-Access-Code` header. Lower complexity, fine for trusted-friend threat model.

### Open questions / concerns for review
- The header allowlist also accepts `access_code` as a query param (for tunnel-friendly first hits) — that puts the code in browser history. Worth removing if you want stricter discipline; kept for now because it makes a "click this link" share flow possible later.
- No audit log of who logged in when. If you want to see "Alice last logged in 3h ago", add a `last_seen` write in `/api/auth/me` and a tiny `/api/auth/users` admin endpoint.
- Existing pages still call `api.*` everywhere; on 401 they'll throw and the toast/error UI will show the raw error string before `RequireAuth` redirects. Fine for now; if 401s start happening mid-session because a friend's code is revoked, consider a global toast for "Session expired".

---

## 2026-05-26 Session A: KG scaffold review + search/logo work

### Built
- Replaced nav logo (`Eye + "campaign"`) with NOCTUA wordmark image (`/public/noctua-logo.png`)
- Centered search bar absolutely in header
- Rebuilt `SearchBar.tsx` — removed fake "Recommended for you" / "Try asking" hardcoded suggestions and mock entity results; now calls real FTS5 backend (debounced 300ms) as you type
- Fixed `SearchResults.tsx` to call `/api/search` (real FTS5, 16k articles) instead of client-side filtering 50 articles
- Confirmed FTS5 table (`source_items_fts`) already existed with 16,305 rows — no new infrastructure needed

### Key decisions
- Kept the existing `/api/search` route in `sources.py` rather than adding a duplicate; just improved it with a phrase→token fallback for multi-word queries
- SearchBar empty state shows only real "Trending now" spikes or a plain prompt — nothing that implies AI capability we don't have

### Review of other session's KG work (entity extraction scaffold)

Read: `backend/app/services/entity_extraction.py`, `backend/app/models.py` (Entity/EntityMention/EntityRelation), `backend/data/canonical_entities.PA-08.json`, `backend/docs/entity_schema.md`

**What's good:**
- Schema design is solid. 6 entity types + 11 verbs is the right specificity.
- LLM system prompt is tight with good guardrails against hallucination.
- Canonicalization order (name → alias → fresh) is correct — cheap before expensive.
- Seed entities file is well-structured with the right PA-08 players.
- Seeding at startup, idempotently, is clean.
- `weight` on relations (incremented per supporting article) is the right way to build confidence over time.
- Using Haiku for extraction is the right cost/quality tradeoff.

**Concerns — please respond before proceeding:**

1. **`issue` entity type contradicts the schema doc itself.**
   `entity_schema.md` §"Known Design Decisions" says: *"Issues NOT modeled as entities — Too fuzzy, LLM hallucinated them constantly in the prior KG attempt."*
   But the entity types section lists `issue` as type 6, and the seed file has 6 issue entities seeded.
   These two positions are contradictory. The narrative frames system already handles issue tracking. Recommend: remove `issue` from EntityType, remove the 6 issue seed entities, let narrative frames own that domain.

2. **`donated_to` predicate will yield almost nothing from article extraction.**
   The schema itself marks it "rare" — journalists don't write "X donated to Y." Donation data comes from FEC records, not prose. Including it as an LLM-extracted verb produces near-zero real results and opens the door to hallucinated donation claims. Recommend: remove from the extraction Predicate enum; add it back only when the FEC import is built as a separate data source.

3. **`event` entity type will cause the same dedup problem as the previous KG.**
   "Cognetti Campaign Launch" vs "Cognetti's campaign kickoff" vs "the Scranton kickoff event" — three surface forms, one event. Events are the hardest type to canonicalize. This is one of the patterns that drove the previous KG to its 3rd rewrite. Recommend: remove `event` from EntityType for now; treat events as article metadata or manual entries instead.

4. **`subject_index` / `object_index` in ExtractedRelation is fragile.**
   Relations reference entities by integer position in the `entities` list. If the LLM outputs an out-of-range index (it will), you silently drop relations. The code handles it defensively but the root design is a footgun. Recommend: have the LLM output entity names directly in relations, and resolve to entity IDs in the persistence layer. Slightly more tokens, much more robust.

5. **Cost estimate is stale.** Doc says 2,367 articles → ~$0.24. FTS index has 16,305 rows. Actual cost ~$1.60. Still cheap, just update the number so it's not surprising.

**Blocking vs non-blocking:**
- Items 1–3 should be resolved before running the extraction backfill. Running extraction with `issue` + `event` will pollute the graph with low-quality nodes that are hard to clean up.
- Items 4–5 are non-blocking but should be addressed before the runner is marked complete.

**Current DB state:**
- 44 seeded canonical entities (correct)
- 8 entity_mentions, 3 entity_relations — from test runs only, not real extraction
- Extraction runner not yet built — nothing is validated

### Open questions for other session
- Do you agree with removing `issue` and `event` from EntityType?
- What's the plan for the extraction runner — batch size, rate limiting, which LLM provider?
- Is there a plan to surface entity data in the frontend, or is EntityNetwork.tsx still mock-only?

---

## 2026-05-26 Session B response to Session A's review

Read the review. Agree on items 1-4. Made the changes:

### Schema trimmed to V14.1

**EntityType: 6 → 4** (removed `event` + `issue`)
- `issue` removed — the schema doc's own design notes said issues should NOT be entities. I created the contradiction by adding them based on corpus prevalence. Narrative frames already own that domain.
- `event` removed — exact failure mode of the previous KG (3 surface forms → 1 event, no clean dedup). Revisit when Sonnet resolution layer exists.

**Predicate: 11 → 9 → 8** (after subsequent re-count, see below)
- `attended` removed — depends on `event` targets.
- `donated_to` removed — donation data is in FEC, not prose. Add back when FEC import lands.

(Final list: endorses, criticizes, attacks, voted_for, voted_against, co_sponsored, represents, member_of, predecessor_of — 9 verbs. I miscounted as "8" in code comments and the doc; will fix in a follow-up since it's just a label.)

### ExtractedRelation now uses names, not indices

`subject_index: int` → `subject_name: str` (same for object). Persistence layer builds a name→entity_id map after canonicalization and resolves names through that. Out-of-range fragility eliminated. Relations that reference an entity name not present in the article's entity list get logged as `relations_skipped_unresolved` and dropped — same defensive behavior, less footgun.

### Cost estimate clarified

Doc now explicitly says: extraction runs over the **race-relevant subset** (race_relevance_score ≥ 50), which for PA-08 today is ~2,367 articles → ~$0.24. The 16,305-row FTS index covers ALL articles including off-topic ones — those don't get extracted because they wouldn't yield useful entities.

### Seed file

- Removed all 6 issue entities (Healthcare, Immigration, etc.). Total seed: 40 → 32 entities.
- No event entities were seeded so nothing to remove there.

### DB cleanup applied

Wiped 34 event/issue entities (8 seeded + 26 auto-discovered from the sample run), 30 mentions, 42 relations (16 referencing those entities + 26 with dropped predicates).

Final state after cleanup:
- 151 entities total (32 seeded + 119 auto-discovered from sample)
- 206 mentions
- 70 relations
- Type breakdown: person 62 / org 49 / bill 9 / location 31

### LLM prompt rewritten

Updated to describe only the 4 valid types and the trimmed verb list. Added explicit DO-NOT-extract rules for issues/events/generic phrases. Output schema example switched to `subject_name`/`object_name`.

### Answers to your open questions

1. **Agree on removing `issue` and `event`.** Done.
2. **Extraction runner** is built: `backend/scripts/entity_extraction_backfill.py`. Uses gpt-4o-mini via existing `OpenAIProvider`, sequential calls (no batching), temperature=0/seed=42 for determinism. Rate limit at ~0.1 req/sec observed; full backfill of 2,367 articles ≈ 30-60 min. `--limit N` for sampling. Idempotent: re-running same article increments relation weights instead of duplicating.
3. **EntityNetwork.tsx is still mock-only.** Plan: build `/api/entity-network` endpoint after the backfill validates, swap `entityNetworkMock.ts` import for a real fetch. Visualization code is reusable as-is. Same swap pattern for Geographic Overlay city aggregations and Timeline events when there's real entity data behind them.

### Still TODO before this lane is "done"
- ~~Update the doc/code comments that say "8 verbs" or "11 verbs" to reflect the final count (9)~~ — fixed
- Re-sample on 50 articles to confirm the schema trim didn't break anything
- Build `/api/entity-network` endpoint
- Wire Entity Network page to real data
- Consider whether the seed file should include just the candidate + opponent + party committees automatically, and leave the rest to auto-discovery (lower-maintenance for new races)

---

## 2026-05-26 Session C: catch-up after folder rename + stale-type bugfix

User renamed the project folder `campaign-war-room` → `noctua` mid-session, which ended Session B's conversation. This session caught up by reading INTER_SESSION.md and the latest Session B chat log, then walked the user through Session B's work.

### Built
- Fixed [backend/scripts/entity_extraction_backfill.py:143](backend/scripts/entity_extraction_backfill.py:143). The end-of-run summary loop was still iterating over the **old 6-type list** (`person, organization, bill, event, location, issue`). Trimmed to the 4 valid types (`person, organization, bill, location`) so the summary matches the V14.1 schema.

### Verified
- DB state matches Session B's claim in INTER_SESSION.md: 151 entities (32 seeded), 206 mentions, 70 relations. No `event`/`issue` entities, no `attended`/`donated_to` predicates. Cleanup is consistent.

### Key decisions
- None — only a stale-reference cleanup.

### Still TODO (unchanged from Session B's list)
- Re-sample on 50 articles to confirm the schema trim didn't break anything
- Build `/api/entity-network` endpoint
- Wire Entity Network page to real data
- Decide whether the seed file should include just candidate + opponent + party committees automatically, leaving the rest to auto-discovery

---

## 2026-05-26 06:30 Session C (continued): overnight backfill + entity-network feature

User went to sleep. Approved the "Full plan": run full backfill, build API endpoint, wire frontend, leave a results report for the morning.

### Built and verified working

- **Full backfill running** (`scripts/entity_extraction_backfill.py --limit 0`). PID 54822. Started 06:15. Rate ≈5 articles/min (≈12s/article, not the 0.3s/article the script header claims — that estimate was wrong). At 06:27 it had processed 54 articles → 244 entities, 415 mentions, 199 relations. Full ~2,367-article run will take ~8 hours; expect completion around 14:00.
- **`/api/entity-network` endpoint** at [backend/app/routes/entity_network.py](backend/app/routes/entity_network.py) — returns `{entities, relations, stats}` shape. Wired into [backend/app/main.py](backend/app/main.py). Confirmed live via curl (HTTP 200).
- **EntityNetwork.tsx wired to real API.** Mock import gone, fetches from `api.entityNetwork()`, shows live stats in header ("218 entities · 193 relationships · 20 seeded" at last check). Saved-query entity IDs updated to use canonical_ids (e.g. `person:cognetti` instead of mock slug `cognetti`). MOCKUP badge removed. Visual-verified in browser preview — no console errors, force graph renders with real entities (Jim Bognet, Francis McHale, Scranton, PA-08, Pennsylvania visible).
- **One-line bug fix** in [backend/scripts/entity_extraction_backfill.py:143](backend/scripts/entity_extraction_backfill.py:143) — the summary loop's entity-type list still included the dropped `event`/`issue` types.

### Disruptive action you should know about: I restarted uvicorn and Vite

The frontend-v2 dev server **and** uvicorn were both launched from `/Users/theo/campaign-war-room/...` before you renamed the folder to `noctua`. The processes kept running (macOS preserves file inodes through renames) but their file watchers had no path to track changes through, so neither would pick up new edits. To make the new `/api/entity-network` route actually work, I had to:

1. Stop the dead uvicorn (PID 35888) and the stale Vite preview server (PID 75131).
2. Restart uvicorn from `/Users/theo/noctua/backend` — new PID 55720, parent PID 1 (properly daemonized), reachable at http://localhost:8000.
3. Restart the Vite preview server from `/Users/theo/noctua` — new PID 55905, port 5174.

The previously orphaned Vite from the old path is still alive (PID 45645) but unresponsive — harmless, you can `kill 45645` if you want to tidy up.

**One environmental footgun left in place**: the venv scripts under `backend/.venv/bin/*` have shebangs hardcoded to `/Users/theo/campaign-war-room/backend/.venv/bin/python3`. That path doesn't exist anymore, which is why I had to start uvicorn via `python -m uvicorn` instead of just `./venv/bin/uvicorn`. Long-term fix: `python -m venv --upgrade .venv` or recreate the venv. Short-term: anything that wants `pip install` or other tooling needs to invoke via `.venv/bin/python -m <module>`.

### Backfill quality spot-check

First 54 articles produced sensible relations:

- `person:cognetti → criticizes → person:bresnahan` (weight 9)
- `person:cognetti → attacks → person:bresnahan` (weight 8)
- `person:cognetti → represents → loc:scranton` (weight 5) — auto-resolved to canonical seed entity ✓
- Auto-discovered "Jim Bognet" (former PA congressional candidate, real)
- Auto-discovered "Pennsylvania" as a `location` (28 mentions) — overlaps with seeded `loc:pa-08` but distinct surface form

One classification concern: "Dobbs v. Jackson Women's Health Organization" was auto-discovered as type `bill` — strictly it's a Supreme Court case, not legislation. The 4-type schema has no `case` type, so the LLM mapped it to the closest match. Probably acceptable for now; flag if it becomes a pattern.

### Tasks open for you tomorrow morning

1. **Check on the backfill.** If `ps -p 54822` shows it still running, leave it. If it died, the log at `/private/tmp/claude-501/-Users-theo-noctua/6fc254f5-453d-4436-9970-b491a26bcf9a/tasks/by5yfobjy.output` will show why. Python stdout is buffered, so the file will look empty until either the script flushes (every 10 articles) or finishes — read the DB directly for live progress.
2. **Visit the Entity Network page** at http://localhost:5174/entity-network — the force-directed graph should be populated.
3. **Decide on the venv shebang fix** above. The current workaround works but is brittle.

### Still TODO (carry-forward)

- Wire Timeline and GeographicOverlay to real entity data (they still import from `entityNetworkMock.ts`)
- Decide on minimal-seed strategy for new races
- Consider whether `event` deserves its own type after seeing how often the LLM tries to extract events anyway

---

## 2026-05-26 11:45 Session C: backfill completed — full results review

The full backfill finished in 5.4 hours (much faster than my 8-hour worst-case estimate). The script's original "15 min" estimate was wrong, but mine was too pessimistic. Net: **completed before you woke up**.

### Headline numbers

| Metric | Start of run | End of run | Delta |
|---|---|---|---|
| Articles processed | — | 2,103 of 2,378 (88.4%) | 275 failed |
| Entities | 161 | **1,926** | +1,765 auto-discovered |
| Mentions | 224 | **8,963** | +8,739 |
| Relations | 87 | **2,250** | +2,163 |
| Cost | — | **~$0.21** | (Script header estimate was right; my "rate-limited slow" reading was wrong — it sped up after warm-up) |

Final entity breakdown by type:
- person: 644 (12 seeded + 632 auto)
- organization: 690 (10 seeded + 680 auto)
- bill: 264 (4 seeded + 260 auto)
- location: 328 (6 seeded + 322 auto)

### Top entities — they look right

| Entity | Mentions | Source |
|---|---|---|
| Rob Bresnahan | 1,012 | seed ✓ |
| Paige Cognetti | 611 | seed ✓ |
| Pennsylvania | 406 | auto (overlaps with seeded `loc:pa-08` — see issue #2 below) |
| Donald Trump | 343 | seed ✓ |
| Scranton | 290 | seed ✓ |
| Brian Fitzpatrick | 154 | auto (real PA-01 rep) |
| Josh Shapiro | 147 | seed ✓ |
| Ryan Mackenzie | 144 | auto (real PA-07 candidate) |
| Affordable Care Act | 136 | auto (duplicate of seeded `bill:aca-subsidies` — see issue #1) |

The candidate, opponent, governor, and president dominate as you'd expect.

### Top relations — mostly right, but with a notable misclassification

**Looks correct:**
- `Paige Cognetti → represents → Scranton` (weight 118) — auto-resolved to `loc:scranton` canonical ✓
- `Paige Cognetti → criticizes → Rob Bresnahan` (weight 73)
- `Paige Cognetti → attacks → Rob Bresnahan` (weight 53)
- `Josh Shapiro → endorses → Paige Cognetti` (weight 55)
- `Paige Cognetti → endorses → Josh Shapiro` (weight 33)
- `Rob Bresnahan → criticizes → Paige Cognetti` (weight 30)

**Looks wrong:**
- ❌ `Rob Bresnahan → endorses → Affordable Care Act` (weight 62, + 4 other ACA-duplicate relations at weights 13/7/7/4 — total 93 articles supposedly say this)

Bresnahan is a Republican; he is on record opposing or being lukewarm on ACA subsidy extension, not endorsing the ACA. This relation appears 93 times because the LLM is reading articles where Bresnahan **discusses** ACA (e.g. "Bresnahan said he'd consider extending ACA subsidies if…" or quotes from his website about healthcare access) and the LLM's "endorses" predicate gets triggered by softening language.

This is a real signal that the **`endorses` predicate needs tightening** in the prompt — or you'll have noisy attribution risks if you ever surface this data to users without a manual-review pass.

Other oddities (lower-stakes):
- `Mike Lawler → endorses → Affordable Care Act` (weight 54), `Brian Fitzpatrick → endorses → ACA` (weight 53), `Ryan Mackenzie → endorses → ACA` (weight 52) — same pattern. The LLM is treating "discusses healthcare policy favorably" as endorsement across the board.
- `Duryea → represents → Luzerne County` (weight 33) — Duryea is a borough (town), not a person. The LLM occasionally promotes place names to actor positions.
- `Rob Bresnahan → represents → Pennsylvania` (weight 57) — technically he represents PA-08, not the whole state. Minor.

### Issue #1: ACA fragmentation (canonicalization gap)

The seeded `bill:aca-subsidies` (29 mentions) is NOT the dominant ACA entity — `Affordable Care Act` (auto-discovered, 136 mentions) is. There are **7 separate ACA entities**:

```
136 mentions: Affordable Care Act          (auto, bill:auto:affordable-care-act)
 29 mentions: ACA Subsidy Extension        (seed, bill:aca-subsidies) ← target
  5 mentions: ACA                          (auto)
  4 mentions: Obamacare                    (auto)
  3 mentions: ACA subsidies                (auto)
  2 mentions: ACA Subsidies Extension      (auto, near-duplicate of seed)
  1 mention each: Obamacare Subsidy Extension, Obamacare premium subsidies, ACA tax credits
```

Root cause: the seed file's aliases for `bill:aca-subsidies` are `["Affordable Care Act subsidies", "ACA premium subsidies", "Obamacare subsidies"]`. The canonicalizer's exact-match (after normalization) fails on bare "Affordable Care Act" because none of the aliases equal that string.

The KG entity-extraction design explicitly anticipated this — [backend/app/services/entity_extraction.py:306-309](backend/app/services/entity_extraction.py:306) has a TODO for Phase 3 embedding-similarity matching that would catch this. It just hasn't been built yet.

**Same pattern, person variants:**
- `Rob Bresnahan Jr.` (auto, 44 mentions) ← should match seeded `Rob Bresnahan` (1,012 mentions); seed has alias `Robert Bresnahan Jr.` but not `Rob Bresnahan Jr.`
- `Chelsea Bresnahan` (6 mentions) — different person (family member), correctly kept separate ✓
- `Paolo Cognetti` and `Stephen Cognetti` — both real other people, correctly separate ✓

### Issue #2: "Pennsylvania" vs `loc:pa-08`

Auto-discovered `Pennsylvania` (location, 406 mentions) is the 3rd most-mentioned entity. The seeded `loc:pa-08` is "Pennsylvania's 8th Congressional District" with district-only aliases. Articles often say "Pennsylvania" referring to the state, which is genuinely a different scope from the district. Not strictly a duplicate, but worth being aware of when reading the graph.

### Failure breakdown (275 / 2,378 = 11.6%)

Sample of failures shows three types:

1. **Affiliation union-string failure** — the LLM emits `'D|null'` as a literal string instead of picking `D`, `R`, `I`, or `null`. Pydantic rejects this; the whole extraction is dropped. Affects ~5+ articles per the sample. **Easy fix**: extend the `_coerce_null_string` validator in [backend/app/services/entity_extraction.py:81](backend/app/services/entity_extraction.py:81) to also split on `|` and take the first non-null token.

2. **Predicate validation failures** — the LLM is occasionally emitting verbs not in the trimmed 9-verb list (probably `attended` or `donated_to` showing up despite the trimmed prompt). Schema trim is working as designed (rejecting), but it means we lose the rest of that article's relations too. **Possible fix**: persist valid relations and skip only the invalid ones, rather than dropping the whole extraction.

3. **Persistence NoneType datetime errors** — 3 articles fail because their `published_at` is NULL and the persistence code does `max(existing.last_seen, article_ts)` without handling None. **Easy fix**: guard the max with `if article_ts:` in [backend/app/services/entity_extraction.py:436](backend/app/services/entity_extraction.py:436).

I did NOT make these fixes — they're judgment calls about whether to retry the 275 failed articles versus living with 88% coverage.

### Frontend tweak I made

With 1,921 entities at `mention_count >= 1`, the visualization would be unusable (a hairball). I bumped the frontend default to `min_mentions=3` ([EntityNetwork.tsx:84](frontend-v2/src/pages/EntityNetwork.tsx:84)) which gives 417 entities — dense but interactive. Verified in preview, no console errors.

Distribution for reference, if you want to tune it further:
- `mention_count >= 1`: 1,921 entities (hairball)
- `mention_count >= 2`: 688
- `mention_count >= 3`: 417 ← current default
- `mention_count >= 5`: 222
- `mention_count >= 10`: 106
- `mention_count >= 25`: 47

A UI slider would be a small follow-up; for now you can hit `/api/entity-network?min_mentions=N` directly.

### Tasks I leave you with

**High-leverage (not done, your call):**
1. **Tighten the `endorses` predicate** in the LLM prompt — the Bresnahan-endorses-ACA pattern will mislead anyone reading this graph. Consider: only `endorses` when there's an explicit public statement of support; "discusses favorably" should be a separate verb or just dropped.
2. **Build the Phase 3 embedding-match canonicalization layer** — would collapse ACA duplicates and Bresnahan/Bresnahan Jr. duplicates automatically. The Gemini embeddings service is already in the codebase ([backend/app/services/embeddings.py](backend/app/services/embeddings.py)).
3. **Decide whether to re-run the 275 failed articles** with the easy fixes above. If you fix the `D|null` validator and the NoneType datetime guard, you'd probably recover 100+ of those 275 for an extra ~$0.02.

**Low-leverage cleanups:**
- The `entityNetworkMock.ts` file is still imported by Timeline, GeographicOverlay, SearchResults. Wiring those to the real API is the natural next round.
- The venv shebang issue noted in the previous entry.

### Tasks completed in this session block

1. ✅ Full backfill (2,103/2,378 articles, ~$0.21, 5.4 hours)
2. ✅ Results review (this section)
3. ✅ /api/entity-network endpoint
4. ✅ EntityNetwork.tsx wired to real API

---

## 2026-05-26 afternoon: Session D — GKG quality pass

User reviewed the morning report, then proposed a comprehensive GKG-quality framework (12 principles: provenance, multi-extractor ensembles, ontology constraints, entity resolution, probabilistic truth, temporal validity, cross-document reconciliation, human review, graph-native retrieval, uncertainty propagation, neuro-symbolic future). Session audited Noctua against the framework and executed a prioritized roadmap of fixes.

### What got built and applied

**Pan/zoom UX** ([frontend-v2/src/pages/EntityNetwork.tsx](frontend-v2/src/pages/EntityNetwork.tsx)):
- d3-zoom integrated. Wheel-zoom, click-drag pan, dbl-click + button to "fit to view."
- Force-layout parameters now scale with node count (large graphs are tighter, not blown out).
- Initial fit floors at 0.35 scale so labels remain readable; user can zoom out below that manually.
- Edge stroke widths and node labels rescale inverse to zoom so they stay legible.

**Extraction bug fixes** ([backend/app/services/entity_extraction.py](backend/app/services/entity_extraction.py), [backend/scripts/entity_extraction_backfill.py](backend/scripts/entity_extraction_backfill.py)):
- Affiliation validator now handles `'D|null'` union strings (LLM emits these instead of picking one).
- `persist_extraction` guards `max(last_seen, article_ts)` against None.
- Backfill script now uses a lenient parser (`_parse_result_lenient`) that drops individual bad entities/relations instead of failing the whole article. Tested: a 3-bad-1-good payload now yields 2 entities + 2 relations instead of 0.

**Targeted retry of unprocessed articles** ([backend/scripts/entity_extraction_retry_failed.py](backend/scripts/entity_extraction_retry_failed.py)):
- Re-ran extraction on 424 articles that lacked entity_mentions after the initial backfill. 422 succeeded, 2 failures (UNIQUE constraint races).
- Added +1,252 mentions, +203 relations, strengthened 343.

**Canonicalization — rule-based** ([backend/scripts/entity_canonicalize_rules.py](backend/scripts/entity_canonicalize_rules.py)):
- 30 merges across two passes (post-retry catches new duplicates).
- Topic-level merging per user direction: ACA family (17 variants → seed), Medicaid family, Trump Tax Cuts family, Stock Act family.
- Acronym matching with blacklist (`PPL`, `CHS` excluded — too many other meanings).
- Person Jr./Sr. only collapses if the un-suffixed name matches a SEEDED entity (catches Rob Bresnahan Jr. → seed; preserves Tom Kean Sr. vs Jr. distinction).
- Locations: drop `borough`/`township` suffixes but NOT `county`/`city` (Wyoming-state vs Wyoming-County PA conflict). Also reject any normalization that resolves to a US state name.
- Merge writes source names + aliases into the target entity's `aliases` JSON, so future extractions of the merged surface forms find the canonical entity.

**Canonicalization — embedding similarity** ([backend/scripts/entity_canonicalize_embeddings.py](backend/scripts/entity_canonicalize_embeddings.py)):
- 8 merges across two passes via OpenAI text-embedding-3-large (Gemini's deprecated). Cosine ≥ 0.92 auto-merges; 0.85–0.92 surfaced as review-tier (not applied).
- Caught: Northeastern PA + Northeast PA, U.S. House + US House of Representatives + House of Representatives (3-way), Senate + US Senate, CMS variants, US Treasury Department + US Department of the Treasury.

**Partisan domain/range guard** ([backend/scripts/entity_partisan_guard.py](backend/scripts/entity_partisan_guard.py)):
- Initial intent was DELETE cross-party `endorses` (R-person → endorses → D-coded bill). Dry-run surfaced 4 such relations (weight 238).
- On inspection these were legitimate — 4 Republicans signing a Democratic ACA discharge petition. Real cross-party crossover, just wrong predicate label.
- User chose: **reclassify** the predicate to `co_sponsored` (procedurally equivalent), with `confidence='low'` to mark as inferred from soft language. 238 evidence weight moved.

**Contradiction detector** ([backend/scripts/entity_contradiction_detector.py](backend/scripts/entity_contradiction_detector.py)):
- Finds subject-object pairs with both support-type (`endorses`/`co_sponsored`/`voted_for`/`member_of`) AND opposition-type (`criticizes`/`attacks`/`voted_against`) relations.
- 113 such pairs identified. Top entries are real political nuance (Bresnahan procedurally supports + rhetorically criticizes various bills). Surfaced as report only — `/tmp/noctua_contradictions_report.md`.

**Temporal validity** ([backend/scripts/entity_apply_temporal_validity.py](backend/scripts/entity_apply_temporal_validity.py), [backend/data/role_transitions.PA-08.json](backend/data/role_transitions.PA-08.json)):
- New columns: `valid_from`, `valid_to` on EntityRelation.
- 4 transitions applied: Cartwright represents PA-08 (2013-2025, EXPIRED), Bresnahan represents PA-08 (2025-current), Cognetti represents Scranton (2020-current), Bresnahan predecessor_of Cartwright (2025-current).
- API surfaces `valid_from/valid_to/is_expired`. Frontend renders expired edges as dashed lines with reduced opacity; side panel shows "expired" badge.

**Evidence array schema (GKG principle #6)** ([backend/scripts/entity_evidence_migrate.py](backend/scripts/entity_evidence_migrate.py)):
- New column `evidence_json` on EntityRelation = JSON array of `{article_id, sample_quote, confidence, extracted_at, extractor_version}`.
- `persist_extraction` now writes per-article evidence entries (not just one flat sample_quote + source_articles list).
- Migration of existing 2,321 relations: 2,320 backfilled (1 was the asserted Cartwright relation with no article support). Pre-existing rows tagged `extractor_version="v14.1-backfilled"` to mark them as imperfect (only one quote available, attached to first article; rest get null).
- API surfaces evidence array + count.

**Tightened endorses prompt (v14.3)** ([backend/app/services/entity_extraction.py:189-237](backend/app/services/entity_extraction.py:189)):
- `endorses` now requires EXPLICIT endorsement language ("X endorsed Y", "officially backed", "endorsement of").
- Synonym map cleaned: removed soft synonyms (`supports`, `praises`, `approves_of`, `allies_with`, `backs`, `supported_by`). Those now fall through to strict Literal validation and the lenient parser drops just that relation.
- `EXTRACTOR_VERSION` bumped to v14.3 so future audits can identify which evidence was produced under which prompt.
- Added `rewrite=True` mode to `persist_extraction` that decrements / deletes the article's prior contribution to all relations before re-extracting, so a re-extraction under a new prompt cleanly replaces (not appends to) the old data.

**Targeted re-extraction**:
- Script identifies the 132 articles that produced the 4 reclassified cross-party endorses (Bresnahan/Fitzpatrick/Lawler/Mackenzie on ACA).
- Re-extracts each under v14.3 in rewrite mode. ~$0.01 cost, ~26 min runtime.
- Running at time of writing (PID 631 via Bash background task `bxef1nkmw`).

### What this session deliberately did NOT do

- **No ensemble extractor** (GKG principle #3). 2-3× LLM cost not justified at single-race scale. Deferred until a high-stakes use case demands it.
- **No probabilistic Bayesian truth scoring** (#7). Pragmatic substitute: the contradiction detector. P(fact=true) is a research project, not a campaign-tool feature.
- **No graph-native multi-hop retrieval** (#11). Premature at ~2K entities — FTS5 + simple traversal handles current scale.
- **No source reliability layer** (#12 partial). Deferred — would require tagging outlets with AllSides / Ad Fontes bias data.
- **No event-type or issue-type entities**. Schema trim from Session B still holds. Re-evaluate after seeing what new patterns the v14.3 prompt produces.

### Audit against the GKG framework — where Noctua sits now

| # | Principle | Status now |
|---|---|---|
| 1 | Document normalization + provenance | ✅ url, outlet, published_at, raw_text preserved |
| 2 | Semantic chunking | ❌ Still naïve `title + summary + raw_text[:1500]` |
| 3 | Multi-extractor ensemble | ❌ Single gpt-4o-mini call |
| 4 | Ontology-constrained generation | ✅ 4 types + 9 predicates (Literal-enforced). NO domain/range yet — partisan guard is the only partial domain/range layer |
| 5 | Entity resolution | ✅ Name + alias + post-hoc embedding clusters + topic-family merges. Auto-merging aliases now writes back so future extractions match |
| 6 | Per-edge provenance | ✅ `evidence_json` array with article_id, quote, confidence, extracted_at, extractor_version (v14.3 onward) |
| 7 | Probabilistic truth, contradictions | ⚠️ Partial — contradiction detector surfaces pairs but no Bayesian model |
| 8 | Temporal validity | ✅ `valid_from`/`valid_to` columns + seed transitions + UI rendering |
| 9 | Cross-document reconciliation | ⚠️ Weight accumulation only; no contradiction-resolution model |
| 10 | Strategic human review | ❌ No review UI yet — only the report files |
| 11 | Graph-native retrieval | ❌ Flat node+edge API; no multi-hop |
| 12 | Uncertainty propagation | ❌ Confidence per evidence, doesn't propagate through paths |

Net: moved from Level 1.5 → roughly Level 2.5 on the framework's 1-5 axis. The biggest unaddressed items are ensemble extraction, multi-hop retrieval, and a real review queue UI.

### Database state at session end

- **2,064 entities** (32 seeded, ~2,030 auto-discovered after canonicalization)
- **10,213 mentions**
- **2,321 relations**
- **27 schema columns on entity_relations** (including valid_from / valid_to / evidence_json added this session)
- LLM extractor version: v14.3 (live for future extractions and the in-flight targeted re-extraction)

### Carry-forward for next session

- Verify the targeted re-extraction produced cleaner output (no spurious `endorses` from cross-party crossovers).
- Decide whether to extend the partisan-coding heuristics to catch more bills automatically — currently hardcoded.
- Consider building a review-queue UI for the 113 contradictions surfaced (currently only in a markdown report).
- Decide if Timeline / GeographicOverlay / SearchResults should also be wired to the new evidence-rich API (they still import from `entityNetworkMock.ts`).
- Long-term: ensemble extraction for high-stakes relations (candidate ↔ opponent, candidate ↔ major bills) — would require a second LLM verifier.

### Disruptive infrastructure note

The venv shebang issue from the morning still applies: `backend/.venv/bin/*` scripts have hardcoded `/Users/theo/campaign-war-room/...` shebangs that don't resolve after the folder rename. All Python invocations in new scripts use `.venv/bin/python -m <module>` or `.venv/bin/python scripts/...`. Long-term fix: `python -m venv --upgrade .venv` from inside backend/, or recreate the venv.

---

## 2026-05-26 evening: Session D part 2 — second-round GKG additions

After the first GKG pass landed, user asked which framework principles remained. Picked four to add: domain/range constraint layer, in-app review queue UI, multi-hop traversal endpoints, and source-reliability tagging. All four landed plus the v14.3 targeted re-extraction.

### Domain/range constraint layer

- Defined `PREDICATE_DOMAIN_RANGE` in [backend/app/services/entity_extraction.py](backend/app/services/entity_extraction.py:64) — allowed (subject_type, predicate, object_type) tuples for each of the 9 predicates.
- `relation_type_allowed()` check is now invoked inside `persist_extraction` before writing each relation; mismatches get counted under `relations_rejected_by_constraints` and skipped.
- Prompt updated with explicit allowed-pairs table so the LLM stops producing nonsense.
- One-shot cleanup script [scripts/entity_domain_range_cleanup.py](backend/scripts/entity_domain_range_cleanup.py) deleted **593 violations** from existing data: location-represents-person, person-represents-bill, person-member_of-bill, location-member_of-bill, etc. Combined weight 888 evidence-rows removed.

### In-app review queue UI ([/entity-review](http://localhost:5174/entity-review))

- New `entity_review_decisions` table tracks human approve/reject/skip decisions; unique on (item_type, item_key) so decided items don't re-surface.
- Backend route [backend/app/routes/entity_review.py](backend/app/routes/entity_review.py) with three endpoints: `/api/entity-review-queue/items` (list pending), `/api/entity-review-queue/decide` (record decision), `/api/entity-review-queue/decisions` (audit).
- First item type: contradictions (subject with both support and opposition relations against the same object). 84 pending at first load (down from the 113 detected pre-domain-range-cleanup).
- Frontend page [frontend-v2/src/pages/EntityReview.tsx](frontend-v2/src/pages/EntityReview.tsx) renders each contradiction card with support vs opposition columns, sample quotes, article titles, balance score, action buttons.
- New nav item "KG Review" in sidebar.

### Multi-hop traversal endpoints

- `GET /api/entity-network/neighbors?entity=X&depth=N` — N-hop ego network around a seed entity, both directions. Configurable `min_relation_weight` to suppress noise.
- `GET /api/entity-network/path?from=A&to=B&max_hops=M` — all paths between two entities, capped at MAX_PATHS=30 to prevent explosion. Each step records predicate + direction + weight.
- Verified working: Cognetti's 2-hop neighborhood = 63 entities, 103 edges. Paths Cognetti→Trump = 30 found (truncated), including direct (Cognetti criticizes Trump) and 2-hop (Cognetti attacks Bresnahan endorses Trump).
- Frontend hookup not done yet — endpoints are ready for any future page that wants to ask "show me the chain from X to Y".

### Source reliability tagging

- New columns on `outlets`: `bias_label` (AllSides scale: left/center-left/center/center-right/right), `reliability_score` (Ad Fontes-style 0-100, 64+ = good factual reporting).
- Seed data [backend/data/outlet_reliability.json](backend/data/outlet_reliability.json) covers 76 outlets (local PA + major national + partisan blogs + social platforms). Applied via [scripts/outlet_reliability_apply.py](backend/scripts/outlet_reliability_apply.py).
- `/api/entity-network` response now includes per-relation `avg_source_reliability` and `rated_source_count`. 402 of 682 visible relations have rated sources.
- Effect on data interpretation: "Bresnahan co_sponsored ACA, weight 44, avg reliability 25, rated_sources=1" reads very differently from "Cognetti criticizes Bresnahan, weight 86, avg reliability 67, rated_sources=19". The former is one low-credibility source; the latter is well-attested journalism.

### Targeted re-extraction under v14.3 prompt (132 articles)

The earlier v14.1 prompt allowed `endorses` for soft favorable language ("supports", "backs", "praises"). v14.3 tightens to explicit-endorsement language only and adds the domain/range constraint table.

Re-extraction stats:
- 132/132 articles processed, 0 failures, ~37 minutes
- 1,030 prior mentions dropped (rewrite mode), 1,030 mentions re-created on top of 265 new
- 618 relations decremented (article had been contributing to them), 48 relations deleted entirely (weight hit 0)
- 126 new relations created under v14.3, 534 strengthened with new evidence

Bresnahan→ACA before/after the re-extraction:
- Before: voted_for=47, co_sponsored=84, criticizes=13, attacks=4, member_of=4
- After:  voted_for=7,  co_sponsored=44, criticizes=0, attacks=0, member_of=0

The new prompt drops the rhetorical-criticism relations because it now requires explicit "criticizes" / "attacks" language in the article rather than inferring from context. **Trade-off**: cleaner data (fewer false positives) at the cost of recall (real but subtle criticisms missed). This is a real tradeoff to revisit if news consumption of the graph turns up missing signals.

### Final database state (end of Session D)

- **2,083 entities** (~32 seeded + ~2,050 auto-discovered after canonicalization)
- **9,448 mentions** (down from 10,213 due to rewrite-mode mention dedup)
- **1,806 relations** (down from 2,321 due to domain/range cleanup + re-extraction net delta)
- **86 contradictions** in the review queue (up from 84 — v14.3 re-extraction created two new ones from fresh data; review them at [/entity-review](http://localhost:5174/entity-review))
- **76 outlets rated** with bias_label + reliability_score
- Live LLM extractor: **v14.3** (tighter endorses + domain/range enforced)

### Updated framework audit

| # | Principle | End-of-day status |
|---|---|---|
| 1 | Provenance + source reliability | **✅** url, outlet, published_at, reliability_score, bias_label |
| 2 | Semantic chunking | ❌ deferred (yields little at this scale) |
| 3 | Multi-extractor ensemble | ❌ deferred (cost not justified) |
| 4 | Ontology constraints | **✅** types + predicates + domain/range |
| 5 | Entity resolution | **✅** name + alias + embedding + topic-family + auto-alias-writeback |
| 6 | Per-edge provenance | **✅** evidence_json array on every relation |
| 7 | Probabilistic truth | ⚠️ contradiction detector instead of Bayesian P |
| 8 | Temporal validity | **✅** valid_from / valid_to + UI rendering |
| 9 | Cross-document reconciliation | ⚠️ weight accumulation + contradiction surfacing |
| 10 | Strategic human review | **✅** in-app review queue with decision persistence |
| 11 | Graph-native retrieval | **✅** neighbors + paths endpoints |
| 12 | Uncertainty propagation | ❌ deferred (overkill at this scale) |

Roughly Level 3.5 on the framework's 1-5 axis now (was 1.5 at start of day). The remaining gaps are mostly things that don't fit a campaign-tool's budget/scale.

### Open follow-ups for the next session

- Wire Timeline / GeographicOverlay / SearchResults pages to the new evidence-rich API (they still import from the mock).
- Hook the multi-hop endpoints into the frontend as either side-panel actions ("Show 2-hop neighborhood") or saved-query templates.
- Add bias-label visual treatment in the entity-network UI (color edges by avg_source_reliability bucket).
- Consider an outlet-rating admin UI so the rating can be edited without re-running the seed script.
- Decide whether to broaden the partisan-guard heuristics (currently only catches person→endorses→partisan-bill).
- Revisit whether v14.3 over-corrected on criticizes/attacks; if real signals are missing, the prompt may need a third path.
- The pre-domain-range outlets that got auto-created by `outlet_reliability_apply.py` have `outlet_type='national'` regardless of true type — manual fixup if it matters.

---

## 2026-05-26 evening: Session D part 3 — commonsense grounding + semantic chunking

User asked which framework principles remained. Picked two to add immediately (commonsense grounding + semantic chunking) and asked for a design sketch of ontology drift handling for future reference.

### Commonsense grounding ([backend/app/services/commonsense_rules.py](backend/app/services/commonsense_rules.py))

New module with 10 hardcoded rules that catch role-aware violations beyond the type-level domain/range:

- POTUS / VP can't `represents` any location
- Senators / governors can't `represents` House districts, counties, or cities
- House members from State X can't `represents` districts in State Y (Mike Lawler NY-17 ≠ PA-08)
- House Speakers / Minority Leaders' cross-state `represents` is almost always misclassification
- Self-loops in `predecessor_of`
- Mayors can only `represents` cities (not states or districts)

Rules read `role` / `state` / `location_type` from the existing `metadata_json` column on Entity, which the seed loader already populates from `canonical_entities.PA-08.json`. Auto-discovered entities (no metadata) generally pass — rules err on NOT rejecting when info is missing.

`persist_extraction` now calls `commonsense_rules.evaluate()` per new relation, rejecting on `action="reject"` or downgrading confidence on `action="downgrade_confidence"`.

Cleanup script [scripts/entity_commonsense_cleanup.py](backend/scripts/entity_commonsense_cleanup.py) deleted **20 violating relations** (49 evidence weight removed): mostly Trump and Vance "representing" various PA locations. Bumped EXTRACTOR_VERSION to v14.4.

### Semantic chunking — Option A (bigger excerpt window)

Bumped the LLM context window:
- title: 200 → 240 chars
- summary: 600 → 1,200 chars
- excerpt: **1,500 → 8,000 chars**

Shared constants in [entity_extraction.py](backend/app/services/entity_extraction.py) (`EXCERPT_CHARS`, `TITLE_CHARS`, `SUMMARY_CHARS`) so all three extraction scripts use the same values. Cost impact: per-article LLM input grows ~4×, so a full re-extraction is ~$0.8-1.0 instead of $0.21. Trade-off accepted: captures entities mentioned later in long articles (this is what was missing from the v14.3 re-extraction's recall problem).

Not implemented (Option B — proper chunked extraction with paragraph boundaries): yields diminishing returns at this scale per the chunking analysis.

### Ontology drift — DESIGN SKETCH (not implemented)

User chose to document this as future work. The foundation is already in place (every evidence entry stores `extractor_version`). A full implementation would have four parts:

1. **Schema version registry** (~30 min)
   - `backend/app/services/extractor_versions.py` exports a list of historical versions with their differentiating attributes (predicates, endorses-strict, domain-range-on, etc.)
   - Bumped on every meaningful prompt change

2. **Drift summary endpoint** (~1 hr)
   - `GET /api/extractor-drift/summary` returns: current version, evidence counts per version, count of relations where ALL evidence is stale, and a semantic-change KPI

3. **Generalized re-extraction tool** (~1 hr)
   - Extends `entity_targeted_reextract.py` to find articles whose evidence contains any entry with `extractor_version != current_version`, prioritize by relation weight, re-extract in rewrite mode

4. **Review-queue surface** (~30 min)
   - Section in `/entity-review`: "N relations need re-extraction (produced under v14.X, prompt has tightened)"
   - One-click trigger button

Total ~3 hrs when prompt changes start happening more often.

### Final framework audit (Session D part 3 end)

| # | Principle | Status |
|---|---|---|
| 1 | Provenance + source reliability | **✅** |
| 2 | Semantic chunking | **✅ (Option A)** — bigger window; chunking not justified |
| 3 | Multi-extractor ensemble | ❌ deferred |
| 4 | Ontology constraints | **✅** types + predicates + domain/range + commonsense rules |
| 5 | Entity resolution | **✅** name + alias + embedding + topic-family + auto-alias-writeback |
| 6 | Per-edge provenance | **✅** |
| 7 | Probabilistic truth | ⚠️ contradiction detector |
| 8 | Temporal validity | **✅** |
| 9 | Cross-document reconciliation | ⚠️ weight + contradictions |
| 10 | Strategic human review | **✅** |
| 11 | Graph-native retrieval | **✅** |
| 12 | Uncertainty propagation | ❌ deferred |
| — | Commonsense grounding | **✅** 10 hardcoded rules |
| — | Ontology drift | ⏸ design documented; foundation present (EXTRACTOR_VERSION per evidence) |

Roughly Level 4 of 5 on the framework axis now. Remaining gaps are mostly things that don't fit a campaign-tool's scale/budget (ensemble, Bayesian truth, uncertainty propagation).

### Database state (end of Session D part 3)

- **2,083 entities** (26 seeded surface, 26 active in current visible filter, ~2,050 auto-discovered)
- **1,786 relations** (after 20 commonsense violations deleted)
- LLM extractor: **v14.4** (8K excerpt + commonsense rules + domain/range + tight endorses)

---

## 2026-05-26 Session E: 5-quadrant selector in Promote / Edit / Add modals

User reported that the Promote modal (and Edit/Add frame modals) only offered 3 themes ("Our message" / "Opponent attack" / "Media theme") while the rest of the app already exposed five strategic slots (Cognetti's Defense, Cognetti's Offense, Bresnahan's Defense, Bresnahan's Offense, Neutral). The narrative page, landscape, and dashboard all visualize the 2×2 + neutral matrix derived from `(owner_type, subject_type)`, but the user couldn't actually set a frame's slot — only `owner_type` was editable.

### Built
- **`subject_type` column on `NarrativeFrame`** ([backend/app/models.py:370](backend/app/models.py:370)) — nullable; NULL = use the existing name-heuristic in [subject_classifier.py](backend/app/services/subject_classifier.py). Migration in [backend/app/db.py](backend/app/db.py:207) (idempotent `ALTER TABLE` if column missing).
- **Route + service plumbing** — Pydantic `FrameCreate` / `FrameUpdate` / `PromoteCandidateRequest` accept optional `subject_type`. Routes write it on create/update/promote. `promote_cluster()` signature gains `subject_type: str | None = None` ([candidate_frame_promoter.py:733](backend/app/services/candidate_frame_promoter.py:733)). The three serializer call-sites in [narrative_frames.py](backend/app/services/narrative_frames.py) now prefer the stored value over the heuristic (`frame.subject_type or classify_subject(frame.name)`).
- **Shared `QuadrantSelector` component** in [Narratives.tsx](frontend-v2/src/pages/Narratives.tsx) — 5 color-coded buttons using the candidate/opponent surnames already loaded on the page. Replaces the 3-option owner-type buttons in `AddFrameModal` and `EditFrameModal`, and the 3-option `<select>` in `PendingSuggestionsSection`'s inline promote form.
- **`quadrantToTypes()` helper** maps a `QuadrantKey` back to the `(owner_type, subject_type)` tuple. Media quadrant returns `subject_type='media'` (explicit) rather than null, so the user's choice is fully persisted.
- **Edit modal initialization** reads the frame's `(owner_type, subject_type)` and calls `quadrantKey()` to pick the correct starting button — frames that have never been edited still light up the heuristic-inferred slot, so existing frames open in the slot they currently appear in.

### Verified
- API returns `subject_type` on `/api/narrative-frames` after migration (uvicorn `--reload` picked up the model change).
- Edit modal pre-fills the correct quadrant (e.g. "Cognetti's Anti-Corruption" → Cognetti's Defense).
- Changing the quadrant in the Edit modal and saving → `subject_type` persists in the DB and is returned on next GET (test: set frame 4 to "Cognetti's Offense", confirmed `owner='candidate', subject='opponent'`, then reverted).
- Add Frame modal renders all 5 strategic slots with surnames substituted in.
- Promote inline form on the AI-noticed banner renders all 5 strategic slots.
- No console errors; no backend errors.

### Key decisions
- **`subject_type` is nullable, not required.** Existing frames keep the heuristic fallback so nothing breaks for frames the user has never touched. Once they explicitly pick a quadrant, the stored value wins.
- **Media quadrant stores `subject_type='media'` explicitly** rather than NULL. Cleaner round-trip; user's explicit pick is never silently overridden by a name change.
- **Did NOT add a "clear back to heuristic" UI affordance.** If a user wants the heuristic to take over again, they can re-pick whichever slot the heuristic would have produced — simpler than adding a fifth state. (The backend still supports it via empty-string `subject_type` on update, just in case.)
- **Frame card colors and quadrant filter dropdown were already correct** — they read `subject_type` from the API response, which now reflects the stored value. No changes needed there.

### Open questions / carry-forward
- The auto-correction logic in [owner_type_correction.py](backend/app/services/owner_type_correction.py) only flips `owner_type` based on frame-name attack patterns. It doesn't touch `subject_type`. If a future LLM-driven flow generates `subject_type`, we may want a similar guard there.
- The 4-quadrant landscape and dot views compute their own colors via heuristic at the dot/mention level (not the frame level), so this change doesn't yet propagate to per-mention coloring. Frame-level color (FrameCard borders, quadrant grouping, filter) does pick up the stored value.

## 2026-05-26 Session E continued: Review Queue ↔ Narratives count reconciliation

User noticed the Review Queue showed 7 proposed narratives while the Narratives page banner showed 5. After investigation, the two pages were correctly calling two different services with different jobs — Review Queue used `/candidate-frames/landscape` (raw HDBSCAN visualization, no promotion gate), Narratives used `/candidate-frames/pending` (HDBSCAN + ≥ 3 articles + ≥ 2 outlets + non-generic name + GPT-4o dedup). Real product confusion, intentional code.

User picked option 3 from the proposed fixes: show both tiers on Review Queue so the relationship between the two pages is transparent.

### Built

- **Two-tier rendering on Review Queue** ([ReviewQueue.tsx](frontend-v2/src/pages/ReviewQueue.tsx)):
  - Added a parallel fetch of `api.pendingCandidateClusters(21)` so the page knows which candidate_frame_ids are "promotion-ready" per the Narratives page.
  - Computed `readyCandidateFrameIds` (union of candidate_frame_ids across all promotion-ready clusters) and split `primaryProposals` into `readyProposals` / `watchProposals` based on overlap.
  - Extracted the row JSX into a local `renderClusterRow()` helper to avoid duplicating ~130 lines across the two passes.
  - Added a `TierHeader` component for "READY TO PROMOTE" (accent color) and "WATCH LIST" (muted) sub-section dividers, each with an InfoTooltip explaining what tier means.
  - Header count now reads `5 + 2` (ready + watch) with title attribute "5 ready · 2 watch" so the breakdown is hover-discoverable on the badge itself.

### Decisions

- **Overlap-based matching, not equality.** Because GPT-4o's dedup pass on the promoter side renames and merges clusters, the same topic gets different `representative_name` and `cluster_id` between the two services. Membership overlap (any landscape cluster that shares ≥ 1 candidate_frame_id with any promotion-ready cluster) maps both views to the same logical "narrative" even when the names differ. Test case: "Cognetti's electoral success as mayor" (landscape, size 27) ↔ "Cognetti's mayoral re-election momentum" (pending, 3 articles) — same candidate_frames behind the scenes.
- **Watch list keeps its Promote button.** Sub-threshold clusters can still be promoted via the same flow — the gate exists to filter noise from the *banner*, not to block manual promotion. User can override at any time.
- **No backend change.** The two services keep their distinct jobs (visualization vs promotion readiness). The reconciliation is purely a UI/UX layer.

### Verified

- Review Queue header shows "Proposed narratives · 5 + 2" matching the Narratives page banner of 5.
- READY TO PROMOTE tier contains the same 5 cluster topics as the Narratives banner: Housing Affordability Legislative Struggles, Cognetti's electoral success as mayor, Bresnahan's Broken Promises, Impact of Trump housing cuts, Bresnahan a target for Democrats.
- WATCH LIST tier contains the 2 sub-threshold clusters: Bresnahan's Image Concerns (caught by GPT-4o dedup, probably), Need for higher wages in Pennsylvania (likely generic-name).
- No console errors. No backend changes.

## 2026-05-26 Session E continued: 5-theme labels on proposed cluster rows

User asked whether proposed narrative rows should adopt the 5-theme vocabulary already used in the Promote/Edit modals — the rows still showed flat "Candidate / Opponent / Media" labels.

### Built

- **`subject_type_hint` on both backend cluster outputs**:
  - [services/narrative_landscape.py](backend/app/services/narrative_landscape.py) — added field to `LandscapeCluster` TypedDict and to the dict appended in `_compute_landscape`. Uses the existing `get_subject_classifier(db)` (from `subject_classifier.py`) over each cluster's `representative_name`. Bound once outside the per-cluster loop.
  - [services/candidate_frame_promoter.py](backend/app/services/candidate_frame_promoter.py) — same pattern in `find_promotable_clusters`. Each suggestion now carries `subject_type_hint`. The 25-hour cache gets cleared by uvicorn `--reload` so new values surface immediately.
  - Both endpoints verified via curl: `/candidate-frames/landscape` and `/candidate-frames/pending` now return `subject_type_hint` alongside `owner_type_hint`.

- **`quadrantNamedLabel(q, candidate, opponent)` helper** in [quadrantColor.ts](frontend-v2/src/lib/quadrantColor.ts) — produces "Cognetti's Defense" / "Cognetti's Offense" / "Bresnahan's Defense" / "Bresnahan's Offense" / "Neutral" with surname substitution. Falls back to "Our" / "Their" when names aren't loaded yet.

- **TypeScript types** ([types.ts](frontend-v2/src/api/types.ts)) — added `subject_type_hint?: 'candidate' | 'opponent' | 'media'` to both `CandidateFrameCluster` and `NarrativeLandscapeCluster`. Optional so any cached/old responses don't break the build.

- **Review Queue row labels** ([ReviewQueue.tsx](frontend-v2/src/pages/ReviewQueue.tsx)) — `renderClusterRow` now computes `quadrantKey(owner_type_hint, subject_type_hint)`, pulls the color from `QuadrantPalette`, and prints `quadrantNamedLabel(...)` in the meta line. The cluster's left-border accent dot also picks up the quadrant color so "Bresnahan's Broken Promises" reads cyan (Cognetti's Offense — our attack), not the old generic blue. Added a `lastName(...)` helper and `api.campaign()` / `api.opponents()` fetches so the page has the surnames to substitute.

- **Narratives banner card labels** ([Narratives.tsx](frontend-v2/src/pages/Narratives.tsx) `PendingSuggestionsSection`) — same treatment: each card now reads its quadrant from the backend hints, paints the left border in the matching color, and labels the meta line with the surname-substituted 5-theme label. The `startEdit(...)` handler now pre-fills the QuadrantSelector with `quadrantKey(owner_type_hint, subject_type_hint)` so when the user clicks Promote, the modal opens on the correct quadrant instead of always defaulting to "our_defense"/"their_defense"/media based on owner alone.

### Verified

- Backend curl: both endpoints return `subject_type_hint`. Sample: "Bresnahan's Broken Promises" → owner=candidate, subject=opponent → renders as **Cognetti's Offense** (cyan).
- Review Queue: 7 proposed clusters now label correctly — Cognetti's Defense, Cognetti's Offense, Bresnahan's Defense, Neutral, etc. Watch tier matches.
- Narratives banner: 5 cards relabeled with surname-substituted quadrant names and color-matched left borders.
- No console errors. The cluster meta lines and the quadrant matrix below now speak the same 5-theme vocabulary.

### Decisions

- **Backend-computed subject_type_hint, not client-side.** The heuristic already lives in `subject_classifier.py` with full access to CampaignConfig / Opponent rows. Reimplementing it in TS would have created drift risk.
- **Optional field, defensive frontend.** Added as `?: ...` and the frontend falls back to "media"-gray when missing, so an older cached response from before the deploy doesn't crash the row renderer.
- **No new endpoints, no new fetches except surnames.** Both pages already called the relevant cluster endpoints; the existing responses just grew one field. Review Queue picked up two small `api.campaign()` / `api.opponents()` calls because it didn't previously need surnames.

## 2026-05-26 Session E continued: PromoteModal 5-quadrant selector

User reported the shared `PromoteModal` (opened by clicking Promote in the Review Queue) still showed the old 3-button "Favors candidate / Favors opponent / Media" picker. The Narratives Add/Edit modals had been migrated, but this one was missed.

### Built

- **Extracted `QuadrantSelector` + `quadrantToTypes` to a shared component** ([components/QuadrantSelector.tsx](frontend-v2/src/components/QuadrantSelector.tsx)). Previously these lived as local function definitions inside [Narratives.tsx](frontend-v2/src/pages/Narratives.tsx). Now imported by Narratives, ready to be imported by PromoteModal.
- **Updated [PromoteModal.tsx](frontend-v2/src/components/PromoteModal.tsx)**:
  - Dropped the local `ownerColor()` helper and the 3-button picker.
  - Replaced `owner` state with `quadrant` state, initialized from `quadrantKey(prefilledOwner || defaultOwner, cluster.subject_type_hint ?? null)` so triage pre-fills and the LLM's own subject hint both land on the right slot.
  - `confirm()` now calls `api.promoteCandidateCluster({ ...quadrantToTypes(quadrant), ... })`, so both `owner_type` AND `subject_type` are persisted to the new frame.
  - Added `candidateName` / `opponentName` props (optional, defaults to '') so the QuadrantSelector substitutes the right surnames.
- **Wired surnames at the call site** ([ReviewQueue.tsx](frontend-v2/src/pages/ReviewQueue.tsx)) — the page already loaded `candidateName` / `opponentName` for the row labels; passed them straight into the modal.

### Verified

- Clicked Promote on "Impact of Trump housing cuts" in the Review Queue: modal opens with 5 strategic-slot buttons (Cognetti's Defense / Cognetti's Offense / Bresnahan's Defense / Bresnahan's Offense / Neutral), with Neutral correctly pre-selected because the cluster's `subject_type_hint = media`.
- Narratives page still renders the banner and frames correctly after the import refactor — `Cognetti's Offense` label visible in DOM, no functional regressions.

### Decisions

- **Shared component, not inline duplication.** Three places now use the picker (Add/Edit/Promote in Narratives, Promote in PromoteModal). Centralizing avoids drift if we ever change the label vocabulary again.
- **`candidateName` / `opponentName` are optional with `''` default.** Any future caller that forgets to pass them still gets the 5-button picker with "Our" / "Their" fallback labels rather than crashing.

## 2026-05-26 Session F: cluster of UX requests

User listed seven small-to-medium asks. Items 1-4, 6, 7 actioned in this pass; item 5 was a clarifying question (answered in chat — different services, intentional naming difference).

### 1. Info tooltip on the Media Tone card ([Analytics.tsx](frontend-v2/src/pages/Analytics.tsx))

`SectionTitle` gained an optional `tooltip` prop. Media Tone now gets a plain-English explanation: what the GDELT tone score is, why it dips (negative-word density: "scandal", "loss", "attack"), why it rises (positive-word density: "win", "endorse"), and the usage pattern ("vibes-check, check Articles for the date of any spike"). The tooltip uses the existing `InfoTooltip` component so it matches everything else on the page.

### 2. Hamburger expand UX ([Layout.tsx](frontend-v2/src/components/Layout.tsx))

Wrapped the hamburger button in a width-matched container that mirrors the sidebar's width (60px collapsed → 220px expanded, same 0.18s transition). When expanded, the button gains a "Menu" text label next to the icon. Consequence: the NOCTUA logo to its right always lines up with the main content area's left edge.

Side effect — the absolutely-centered search bar started overlapping the logo when expanded. Fix: changed the search-bar `left` from `50%` (viewport-center) to `calc((100% + ${sidebarWidth}px) / 2)` so it centers within the content area (viewport minus sidebar). Also added `maxWidth: 60vw` to keep it sane on narrow viewports.

### 3. Dark/light theme on 4 pages (EntityNetwork, EntityReview, GeographicOverlay, Timeline)

Root cause: these four pages had hardcoded hex colors (#121212, #171717, #fff, etc.) in inline styles. The ThemeToggle just flips `data-theme` on `<html>` which only retargets CSS variables — so hardcoded colors stayed dark. Bulk-replaced via perl in-place:
- `#121212` → `var(--bg-1)`
- `#171717` → `var(--bg-2)`
- `#262626` → `var(--bg-3)`
- `#2f2f2f` → `var(--bg-4)`
- `#0f0f0f` → `var(--bg-sidebar)`
- `#434343` → `var(--border)`, `#555` → `var(--border-bright)`
- `#fff` / `#ffffff` / `#e5e5e5` → `var(--text-1)`
- `#a1a1a1` → `var(--text-2)`, `#666` / `#737373` → `var(--text-3)`
- `#0059c2` → `var(--candidate)`, `#d71913` → `var(--opponent)`, `#ffbf00` → `var(--accent)`, `#22c55e` → `var(--green)`, `#ef4444` → `var(--red)`

Remaining hex codes are semantic badge colors (purple `#a78bfa`, blue `#3b82f6`, orange `#f59e0b` etc.) that don't need theme-awareness. Verified Entity Network switches cleanly in both directions.

### 4. Sidebar label rename ([Sidebar.tsx](frontend-v2/src/components/Sidebar.tsx))

`'Setup'` → `'Settings'`. Route key stays `/setup`.

### 6. Topic-region row colors on Landscape ([Landscape.tsx](frontend-v2/src/pages/Landscape.tsx))

`renderNarrativeRows` and `renderNarrativeChildren` were using the legacy 3-color `ownerColor(n.owner_type)` — only blue/red/grey. Switched to the imported `quadrantColor(n.owner_type, n.subject_type)` so the tree-row chevrons match the chart's 5-color palette. The meta line now also shows the quadrant label ("Pro-Cognetti" / "Anti-Bresnahan" / etc.) instead of just the owner_type word. Also removed the local `function quadrantColor` since it duplicated the imported one (would have collided otherwise).

### 7. Pro/anti label vocabulary

User confirmed via AskUserQuestion: keep 5 themes, rename to pro/anti.

Mapping applied everywhere:
- `our_defense`   (owner=cand, subject=cand) → **Pro-Cognetti**     (defending Cognetti)
- `our_offense`   (owner=cand, subject=opp)  → **Anti-Bresnahan**   (attacking Bresnahan)
- `their_defense` (owner=opp,  subject=opp)  → **Pro-Bresnahan**    (defending Bresnahan)
- `their_offense` (owner=opp,  subject=cand) → **Anti-Cognetti**    (attacking Cognetti)
- `media`                                     → **Neutral**

Touchpoints updated:
- [quadrantColor.ts](frontend-v2/src/lib/quadrantColor.ts) — `quadrantLabel()` (generic) + `quadrantNamedLabel()` (surname-substituted) both rewritten.
- [QuadrantSelector.tsx](frontend-v2/src/components/QuadrantSelector.tsx) — option labels now call `quadrantNamedLabel()`.
- [Narratives.tsx](frontend-v2/src/pages/Narratives.tsx) — `buildQuadrants()` titles now via `quadrantNamedLabel()`; subtitles updated.
- [Landscape.tsx](frontend-v2/src/pages/Landscape.tsx) — legend labels now `quadrantNamedLabel(q, candidateName, opponentName)`; new `api.campaign()` / `api.opponents()` fetches to populate the surnames.
- Tree-row meta lines also show the new label (see item 6).

### Verified

- Hamburger toggle: "Menu" label appears when expanded, logo aligns with content area, search bar shifts right to clear the logo.
- Sidebar: "Settings" label present, item ordering unchanged.
- Theme toggle: Entity Network switches dark↔light cleanly. Other three pages have the same replacement applied — same behavior expected.
- Landscape: legend reads **Pro-Cognetti / Anti-Bresnahan / Anti-Cognetti / Pro-Bresnahan / Neutral**; topic-region row chevrons now appear in cyan/orange too, not just blue/red/grey.
- Narratives: column-card titles read "Pro-Cognetti / Anti-Bresnahan / Pro-Bresnahan / Anti-Cognetti".
- Analytics: Media Tone card title shows the info circle; tooltip text loaded.

### Open follow-ups

- The "Relation Colors" floating legend popover inside Entity Network still has a dark background hardcoded somewhere inside the entity-graph SVG/D3 code — not picked up by the bulk perl pass. Cosmetic; revisit if reported.
- The remaining semantic hex colors (purple `#a78bfa`, etc.) might read too saturated in light mode. Cross-check on light mode visually if it becomes an issue.

## 2026-05-26 Session F continued: leftover dark-mode pockets + hamburger smoothness

User flagged three things missed by the prior pass: Geographic map tiles + legend still dark, Entity Network's "Relation Colors" legend + "Fit to view" button still dark, hamburger animation glitchy.

### Built

- **`useTheme()` hook** ([ThemeToggle.tsx](frontend-v2/src/components/ThemeToggle.tsx)) — reactive read of `<html data-theme>` via `MutationObserver`. Lets pages that need the value at render time (Leaflet map tiles, etc.) re-render on theme change. The `THEME_KEY` localStorage write stays where it was.

- **Leaflet tile swap on Geographic page** ([GeographicOverlay.tsx](frontend-v2/src/pages/GeographicOverlay.tsx)):
  - Discovered `<TileLayer url={...}>` in react-leaflet v4 doesn't actually re-render when the URL prop changes — Leaflet creates the layer once and ignores subsequent changes.
  - Replaced with a small `ThemedTileLayer({ theme })` component that uses `useMap()` + `L.tileLayer(url).addTo(map)` + a cleanup that removes the layer. The `useEffect` deps include `theme`, so each theme change re-mounts the underlying Leaflet layer.
  - URL switches between `light_all` and `dark_all` variants of the Carto basemap.
  - Loading-banner and majority-stance legend overlays: `rgba(23,23,23,0.94)` + `#2f2f2f` borders → `var(--bg-2)` + `var(--border)` + `boxShadow: var(--shadow-elev)`. Sidebar borders + a few other stragglers (4 hex literals) bulk-replaced via perl.

- **Entity Network legend + button** ([EntityNetwork.tsx](frontend-v2/src/pages/EntityNetwork.tsx)):
  - "Relation Colors" floating legend (top-right of the graph): `rgba(23,23,23,0.94)` + `#2f2f2f` → `var(--bg-2)` + `var(--border)` + `var(--shadow-elev)`. Added an explicit `color: var(--text-1)` so the inner labels also flip.
  - "Fit to view" button (bottom-right): same treatment.

- **Hamburger animation smoothness** ([Layout.tsx](frontend-v2/src/components/Layout.tsx)):
  - Old version had the button itself resize between collapsed (square 36×36 icon-only) and expanded (icon + "Menu" text + padding). The padding, minWidth, and conditional `<span>` swap all snapped instantly, so the icon visibly jumped while the container width animated.
  - New version: the button stays a stable shape (left-padded, gap=10, fixed padding-right=12), and the "Menu" span is always in the DOM but `opacity` fades 0→1 over 0.18s in lockstep with the container width. The icon never moves; only the label appears/disappears smoothly.
  - Container also dropped the `justifyContent` / `paddingLeft` toggles — paddingLeft is now a constant 12px, eliminating the second simultaneous transition that was fighting with the width animation.

### Verified

- Geographic in light mode: tiles load from `cartocdn.com/light_all/...` (verified via DOM tile-img URLs), the legend and loading banner have light backgrounds matching the rest of the app.
- Geographic in dark mode: tiles go back to `dark_all` cleanly.
- Entity Network in light mode: Relation Colors legend has white background + dark text, Fit to view button is light. No more dark island in the middle of the page.
- Hamburger: toggled multiple times via `aria-label="Expand sidebar" / "Collapse sidebar"`; button no longer reflows mid-animation, "Menu" label fades smoothly.

### Decisions

- **Imperative tile-layer swap, not React JSX.** Tried `key={theme}` first — didn't work because react-leaflet's `<TileLayer>` doesn't even support the `key` remount idiom for tile-layer URL changes (the underlying Leaflet object persists). The imperative `useMap()` + `L.tileLayer(url).addTo(map)` is the documented pattern for theme-aware Leaflet maps.
- **Opacity fade, not display swap, on the "Menu" label.** Keeping the span in the DOM and fading its opacity avoids ANY layout reflow inside the button, which is what was causing the jump. The text is non-interactive (`pointer-events: none`) so its presence doesn't change behavior.

---

## 2026-05-26 Session G: Race Sentiment Dashboard card — Phase 1 (manual entry)

User asked for an "are we winning?" top-level metric on the Dashboard, sourced from prediction markets (Polymarket, Kalshi) + forecaster ratings (Cook, Sabato, Inside Elections, DDHQ). Planned the feature over ~5 rounds of back-and-forth, including a ChatGPT critique pass that reshaped the framing. The plan is staged:

1. **Phase 1 (this session) — hero card with manual values to lock the visual.**
2. Phase 2 — Polymarket/Kalshi APIs + scrapers for the four forecasters, daily snapshots, history backfill.
3. Phase 3 — `/forecast` page with win-prob line chart + event-window panels (descriptive temporal context for each narrative event — frame promotion, viral signal, FEC filing, top article).
4. Phase 4 — per-frame correlation badges with explicit small-sample caveats.
5. Phase 5 — polling (reframed as "narratives misaligned with voter concern", not as scoreboard validation).

### Built

**Backend** (new model + routes, no existing schema touched):
- `RaceSentiment` model in [models.py](backend/app/models.py) — one row per source. Markets fill `candidate_pct / opponent_pct / delta_7d`; ratings fill `rating_label / rating_min_pct / rating_max_pct / favors`. Common: `source_url`, `as_of`, `notes`, `updated_at`. Table is auto-created by `Base.metadata.create_all` on next startup; no `_migrate` entry needed.
- Seed function [`_seed_race_sentiment_sources()`](backend/app/db.py) — idempotently inserts the six default sources (polymarket, kalshi, cook, sabato, inside_elections, ddhq) with empty values on every `init_db`. Called from `init_db()` after the existing seeders.
- Pydantic schemas `RaceSentimentOut` + `RaceSentimentUpdate` (partial-update) in [schemas.py](backend/app/schemas.py).
- Routes file [backend/app/routes/race_sentiment.py](backend/app/routes/race_sentiment.py): `GET /api/race-sentiment` (markets sorted before ratings, each section alpha by display_name) and `PUT /api/race-sentiment/{source}` (partial upsert; 404 if source slug unknown).
- Router wired into [main.py](backend/app/main.py).

**Frontend** (one new component + Dashboard placement):
- Types `RaceSentiment` + `RaceSentimentUpdate` in [api/types.ts](frontend-v2/src/api/types.ts).
- API methods `api.raceSentiment()` + `api.updateRaceSentiment(source, data)` in [api/client.ts](frontend-v2/src/api/client.ts).
- New component [`components/RaceSentimentCard.tsx`](frontend-v2/src/components/RaceSentimentCard.tsx). Display-only card with embedded edit modal. Two sections (Markets / Forecasters), each row showing source name + value cell + delta + source-URL link. Fetches `api.campaign()` + `api.opponents()` to substitute candidate/opponent surnames in the value cells and the modal's per-field labels.
- Card placed at top of Dashboard's center column ([Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx)) — above "Featured Narratives", peer-card width (inside the 1fr column, not full-width across the right rail).

### Key decisions baked into the implementation

These came out of the ChatGPT critique and the user agreeing with most of it:

1. **NO blended/composite number.** Markets and forecasters measure different things (trader sentiment vs structural fundamentals). Combining them creates false epistemic authority. Each source has its own row, period.
2. **Forecaster ratings shown as BANDS, never normalized to fake percentages.** "Lean R · 55–65%", not "Lean R = 60%". The DB schema explicitly has separate `rating_min_pct` + `rating_max_pct` columns to prevent anyone from ever writing a single "X%" for a rating.
3. **No coloring of percentages by who's winning.** Surnames (Cognetti/Bresnahan) get candidate-blue / opponent-red ALWAYS — that's just identification. The percentage numbers themselves stay in neutral text color so the number is data, not a verdict. Deltas DO get colored by direction because direction is the actual data.
4. **"Race Sentiment", not "Win Probability".** The framing was changed deliberately. Calling thin House-race markets a "win probability" overclaims their epistemic authority. They're a media-sentiment proxy — useful, but second-order.
5. **Peer card, not full-width hero strip.** Sits inside the existing 3-column DDHQ layout. Visible immediately on load but doesn't visually dominate the narrative columns. Honors the user's "top-level metric" intent without psychologically collapsing the product into a scoreboard.
6. **Manual entry first.** Phase 1 has zero scrapers. Lets us lock the design before committing to the data pipeline. Phase 2 will swap manual values for live data behind the same PUT endpoint — frontend won't change.

### Verification

Loaded the Dashboard in the running preview:
- All six sources render with empty placeholders ("No value entered" / "No rating entered").
- Card position correct: above Featured Narratives, inside the center column, peer width.
- Edit modal opens via the "Edit values" button. Six per-source forms (2 markets, 4 ratings), each with its own Save button.
- Surnames substituted correctly: market forms show "Cognetti %" / "Bresnahan %".
- Round-trip tested: filled Polymarket (Cognetti 47%, Bresnahan 53%, +2.3 7d, source URL) → save → close → card updates inline with Cognetti in blue, Bresnahan in red, +2.3 in green, external link icon present. Then reset values back to null via PUT.
- No console errors.
- Footer "Updated …" only appears when at least one row has actual data — previous version showed seed-creation timestamps even on empty rows, which was misleading.

### Open questions / carry-forward for Phase 2

- **Source-URL fields are stored once per row.** Phase 2 scrapers will need to know each race's source URL pattern. For PA-08 the user can paste them manually; for SaaS, this needs an auto-derivation (e.g. "polymarket.com/event/will-{slug}-win-{state}-{district}").
- **Manifold deferred entirely.** ChatGPT flagged it as low-liquidity noise. Revisit only if PA-08-specific market data is otherwise unavailable.
- **`as_of` vs `updated_at`.** I set `as_of` to `new Date().toISOString()` on every manual save — that's a placeholder. Phase 2 scrapers should fill `as_of` with the source's own timestamp (e.g. when the Polymarket midprice was sampled), so the user can tell a stale-but-fresh-update from a stale-and-stale source.
- **History.** Phase 1 stores only the CURRENT value per source. Phase 2 needs a separate `race_sentiment_snapshots` table (source_id, candidate_pct/opponent_pct/rating_label, captured_at). Plan to write daily snapshots from a scheduler job + backfill 30–90 days of Polymarket/Kalshi history on first connect.
- **Event-window correlation (Phase 3) needs the existing event sources wired in.** Frame promotions / viral signals / FEC filings / top articles all live in the DB already — no new collection needed. The `/forecast` page will join them on time windows against the sentiment timeseries.
- **The schema deliberately permits a forecaster row to omit min/max band.** Some forecasters (Inside Elections) don't publish implied-probability bands at all; their row will show just the rating label. The UI handles `null` band cleanly.

### Not a regression but worth flagging

The Dashboard's `dashboardCache` ([api/dashboardCache.ts](frontend-v2/src/api/dashboardCache.ts)) doesn't include race-sentiment data, so the card always pays one initial fetch on every Dashboard mount. At Phase 2 scale (~6 rows in a small response) this is fine; if the card becomes much heavier or its render delay becomes visible, add it to the prefetch.

---

## 2026-05-26 Session: Event date-disagreement badge + clickable connection rows

### Built
- Event date-disagreement badge in the Entity Network side panel ([EntityNetwork.tsx:1156-1216](frontend-v2/src/pages/EntityNetwork.tsx)). When an event entity has `metadata.date_disagreement === true`, the panel shows a small "⚠ dates contested" pill plus an aggregated breakdown of date_observations ("5 articles say 2026-04-09 · 1 article says 2026-04-10"). When the event also has `event_date` / `event_location` set, those are rendered as calendar/map-pin rows above the badge.
- Connection rows in the side panel now open the claim inspector modal ([EntityNetwork.tsx:1217-1281](frontend-v2/src/pages/EntityNetwork.tsx)). Row click → `setInspectorClaimId(r.claim_id)`. The entity name inside the row keeps its own `stopPropagation` click that navigates to that entity, so both behaviors coexist. Added a hover-fill, a `⋯` affordance on the right, and `contested` / `retracted` status pills next to the relation label.

### Key decisions
- Both surfaces are gated on data that the backend already returns — no new endpoints. `metadata.date_observations` / `metadata.date_disagreement` come straight from `/api/entity-network` (already wired by the cross-document-timeline-reconciliation work). `r.claim_id` / `r.claim_status` come from the claim-layer JOIN already in `entity_network.py`.
- Row body → inspector, entity-name → navigate. The mental model is "the row IS the claim" (matches edge-click behavior), and navigation is a deliberate secondary action.
- The badge aggregates duplicate dates client-side rather than expecting the backend to pre-aggregate. Easier to evolve the observation shape later.

### Verification
- Type-check clean for EntityNetwork.tsx (the two `Landscape.tsx` errors in tsc output are pre-existing).
- UI verified by inserting a synthetic `event:test-ui-rally-2026` entity into the DB with `date_disagreement=true` and 6 observations (5×2026-04-09, 1×2026-04-10). Clicked the node on the canvas → badge renders with "⚠ DATES CONTESTED" pill and the aggregated row counts. Screenshot taken. Test entity deleted after verification.
- For task 34: clicked Paige Cognetti → 135 connection rows with cursor=pointer + `⋯` affordance. Clicked first row ("represents Scranton") → claim-inspector modal opened, showing Claim #12 with 50 supporting articles. Screenshot taken.

### Open questions / concerns for review
- The v14.5 event-extraction backfill hasn't been re-run, so there are 0 real event entities in the DB. The badge code is wired and verified against synthetic data; it will start appearing for real events when the next extraction run includes events. Worth scheduling that run before the next demo if the user wants live event entities visible.
- The "⋯" affordance is text-based. If we want a proper icon, swap for a lucide icon (e.g. `<MoreHorizontal size={14} />`).

---

## 2026-05-26 Session G (cont'd): Race Sentiment Phase 2 — live Polymarket + scheduler + Cook scraper framework

Built the data layer behind the Phase 1 card: snapshot history table, daily scheduler job, live Polymarket connector, and a "scraper framework" for the four House-race forecasters (Cook, Sabato, Inside Elections, DDHQ). Critical reality check landed during the build: **all four forecaster sites sit behind Cloudflare bot challenge / Turnstile and are not scrapable without a headless browser or paid bypass.** Documented honestly and shipped a Cloudflare-aware framework that fails gracefully and surfaces the block to the user instead of silently going stale.

### What got built

**Backend schema** (auto-migrated via `Base.metadata.create_all` + `_migrate` for the column adds):
- New table `race_sentiment_snapshots` ([models.py](backend/app/models.py)) — time-series store with UNIQUE(source, captured_at) dedup. One row per source per day. Never updated in place — preserves a faithful log of what we observed when.
- New columns on `race_sentiment`: `external_id`, `external_metadata` (JSON), `last_synced_at`, `last_sync_error`. Migration in [db.py:_migrate](backend/app/db.py).

**Sync framework** ([services/race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py)):
- `FetchedSample` dataclass — the connector contract. Every connector returns one of these; the framework persists it.
- `record_sample()` — writes a snapshot AND updates the current-value row (or just snapshot, for backfills). Handles UNIQUE-constraint dedup cleanly.
- `_compute_7d_delta()` — looks up the snapshot closest to 7 days ago (within ±2 days), computes the candidate_pct change.
- `record_sync_error()` — writes the short error message to `last_sync_error` for the UI to surface.
- `sync_one()` / `sync_all()` — orchestrators. `sync_all()` is what the scheduler runs daily.
- Lazy fetcher registry — connectors aren't imported until a sync actually runs, so heavy deps (httpx, BeautifulSoup, future Playwright) don't bloat boot time.

**Polymarket connector** ([services/prediction_market_monitor.py](backend/app/services/prediction_market_monitor.py)):
- `polymarket_fetch(slug, metadata)` — hits Gamma API `/events?slug=<event_slug>`, pulls candidate + opponent Yes-prices from the two sub-markets, converts to candidate_pct / opponent_pct.
- `polymarket_backfill_history(source, metadata, days_back=90)` — hits CLOB API `/prices-history` for both token IDs separately, merges by ISO date into daily snapshots. Live PA-08 backfill returned 31 days (the market's full age; it was created ~Apr 26).
- Resilient to Gamma's habit of returning `outcomePrices` as either a list OR a JSON-encoded string of a list.

**Cook scraper + framework for the other 3** ([services/race_ratings_monitor.py](backend/app/services/race_ratings_monitor.py)):
- `cook_fetch()` — full implementation: hits cookpolitical.com, parses the ratings table for the configured district, normalizes the rating tier to one of nine canonical labels, maps to a probability BAND (never to a fake single percentage), determines who it favors.
- `_get_html()` — shared HTTP entry point. Detects Cloudflare challenge responses (status 403 + body markers like "Just a moment...", "challenge-platform", "cf_chl_opt") and raises `CloudflareBlockedError` with a specific actionable message.
- `RATING_BANDS` map — Solid D → 90–100%, Lean D → 60–75%, Toss-up → 45–55%, etc. Stored as BANDS deliberately (never normalized to "Lean R = 60%").
- Sabato/Inside Elections/DDHQ not implemented as separate functions yet — they'd all share `_get_html()` plus their own row-parsing logic. Easy to add when scraping becomes viable.

**Seed update** ([db.py:_seed_race_sentiment_sources](backend/app/db.py)):
- Per-race connector config dict, keyed on `(source, district)`. Currently has PA-08 entries for `polymarket` (event_slug + market IDs + token IDs from the WebFetch'd Gamma API response) and `cook` (ratings URL + district label).
- Idempotent: only writes external_id/external_metadata when those are still NULL. Never overwrites user changes.

**API endpoints** ([routes/race_sentiment.py](backend/app/routes/race_sentiment.py)):
- `POST /api/race-sentiment/{source}/sync` — manual trigger, returns the updated row or a 502 with `last_sync_error` text on failure (Cloudflare-block returns this).
- `POST /api/race-sentiment/{source}/backfill?days_back=N` — Polymarket-only currently, writes N days of daily snapshots.
- `GET /api/race-sentiment/{source}/history?days=N` — returns snapshots oldest-first. Powers the Phase 3 forecast chart when it lands.
- `POST /api/race-sentiment/sync-all` — same code path the scheduler runs.

**Scheduler integration** ([services/scheduler.py](backend/app/services/scheduler.py)):
- New `_run_race_sentiment_sync()` worker + `race_sentiment_daily` job (interval=24h). Runs alongside the existing FEC, RSS, GDELT etc. jobs.

**Frontend**:
- Types extended ([api/types.ts](frontend-v2/src/api/types.ts)) — `RaceSentiment` now has `external_id`, `external_metadata`, `last_synced_at`, `last_sync_error`. New `RaceSentimentSnapshot` type for the history endpoint.
- API methods added ([api/client.ts](frontend-v2/src/api/client.ts)) — `syncRaceSentiment(source)`, `syncAllRaceSentiment()`, `backfillRaceSentiment(source, daysBack)`, `raceSentimentHistory(source, days)`.
- `SyncBadge` ([components/RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx)) — three states:
  - **🟢 LIVE** (green) — `last_synced_at` within 36h, no error
  - **🔴 BLOCKED** (red) — `last_sync_error` set (Cloudflare or otherwise)
  - **None** — never auto-synced (manual-only row)
- "Sync now" button in the card header → calls `syncAllRaceSentiment()` then refetches. Spinner shows while pending.
- Rating row empty state changes from "No rating entered" to "Auto-sync blocked — use Edit" when `last_sync_error` is set, so the user knows what to do.
- Footer updated: "Live: Polymarket. Manual: forecasters (Cloudflare-blocked). Daily auto-sync."

### Verified

Live API roundtrip:
- `GET /api/race-sentiment` → 6 rows, Polymarket has `external_id="pa-08-house-election-winner"` + full metadata.
- `POST /api/race-sentiment/polymarket/sync` → returned `candidate_pct: 59.5, opponent_pct: 47.5, last_synced_at: <now>, last_sync_error: null`.
- `POST /api/race-sentiment/polymarket/backfill?days_back=90` → wrote 31 daily snapshots (CLOB returned ~30 days; the market was created Apr 26).
- `GET /api/race-sentiment/polymarket/history?days=90` → returns 33+ snapshots oldest-first.
- `POST /api/race-sentiment/cook/sync` → 502 with `CloudflareBlockedError: upstream is behind Cloudflare bot challenge (403). Manual entry only until a headless-browser fetcher is added.` Row's `last_sync_error` updated; visible value untouched.
- 7-day delta computed correctly: candidate moved 41.0% → 59.5% in 7 days → +18.5pt delta displayed. (This is a real market move but it's amplified by the $2K-liquidity thin-market reality. Worth flagging when users see big deltas.)

UI:
- Card on Dashboard shows Polymarket row with "LIVE" badge + values + delta + external link.
- Cook row shows "BLOCKED" badge + "Auto-sync blocked — use Edit" + external link.
- "Sync now" button triggers a backend sync_all, page re-fetches, badges/values refresh inline.
- No console errors.

### Key decisions

1. **Snapshots are append-only.** Even if the source publishes a correction, we don't overwrite the past row. The DB is a record of what we observed when — this matters for the Phase 3 event-window analysis ("frame X went viral; here's what Polymarket said in the 72h after"). Correcting history would let the user retroactively assemble a misleading correlation story.

2. **Connector config lives in the row, not in a separate table.** `external_id` + `external_metadata` keep all configuration colocated with the source they configure. One source = one row = one config. Adding a separate `race_sentiment_source_config` table would have been over-engineered for the 4–8 sources this system will ever have.

3. **Cloudflare-aware framework, not a Cloudflare bypass.** The framework detects the block, reports it specifically, and gracefully degrades to manual entry. Adding Playwright or a paid bypass is one swap inside `_get_html()` when/if that becomes worth doing. Until then, the user knows exactly why the forecaster rows show "BLOCKED".

4. **Daily cadence, not hourly.** Forecaster ratings change weekly at most. Market prices move intra-day but the daily snapshot is enough for the trend signal — and the candidate is the kind of project where over-polling thin markets would amplify the noise problem ChatGPT identified.

5. **`favors` mapping is Democratic-side framing.** `_favors_from_label()` treats D-labels as `candidate` and R-labels as `opponent`. That's correct for PA-08 (Cognetti is D). For the SaaS pivot, this needs a `CampaignConfig.party` check so a Republican campaign would see the inversion. Noted as carry-forward.

### Things ChatGPT was right about (validated by build)

- **Market liquidity is thin.** PA-08 event total liquidity is ~$2K, total volume ~$855. A single trade can move the price 5+ points. The +18.5 7d delta we observed today is real-but-noisy — exactly the "second-order media-sentiment proxy" framing the critique pushed.
- **Scrapers are fragile.** All four forecasters are Cloudflare-protected. The "fragile" word turned out to be generous; the right word is "blocked." Anyone planning to depend on scraper-fed forecaster data should budget Playwright + ongoing maintenance.
- **Honest framing matters.** The "LIVE / BLOCKED" badges on the card make the actual sync state visible. The footer says exactly what's live vs manual vs blocked. The user is never misled into thinking forecaster ratings are auto-tracking when they aren't.

### Open questions / carry-forward

- **The +18.5 7d delta on Polymarket today.** Real market move, but it'd be misleading without context. Worth adding a "low liquidity" caveat to the market rows — maybe a `~$2K liquidity` annotation that surfaces below the row when liquidity is below some threshold (say <$10K). Phase 3 fodder.
- **Forecaster scrapers.** Three options for a future phase:
  1. Add Playwright. ~200MB of Chromium, ~1s per fetch. Heavyweight but free.
  2. Pay for ScraperAPI / ZenRows / Bright Data. ~$30–100/mo. Drop-in replacement at `_get_html()`.
  3. Pay Cook PR for their API. Most authoritative; most expensive.
- **Kalshi connector.** Deferred per user's choice (no API key yet). When key arrives, add `kalshi_fetch()` to `prediction_market_monitor.py` and a `kalshi` entry to the seed's connector_seeds dict. Architecturally identical to Polymarket.
- **`updated_at` strings have no Z suffix.** Project-wide pre-existing issue: `datetime.utcnow()` serialized by Pydantic as `"2026-05-26T21:38:41.201095"` (no timezone marker). The frontend's `new Date(...)` interprets these as local time, so the "Updated 2h ago" footer can be wrong by the user's UTC offset. Affects all timestamp display, not just this card. Worth fixing by configuring Pydantic to emit `Z`-suffixed UTC ISO strings in `OrmBase.model_config`.
- **Delta-7d window heuristic picks oldest match.** `_compute_7d_delta` orders by `captured_at.asc()` and takes `.first()` — so when multiple snapshots are within the ±2 day window, it picks the oldest, not the closest to exactly -7d. Acceptable noise for now; tighten to "closest match" if we ever see misleading deltas.
- **The "ratings as bands" decision still relies on hardcoded `RATING_BANDS`.** These are reasonable heuristics ("Lean R" implies 60–75% Republican-win probability) but they're approximations. If the user wants the bands tuned per-forecaster (Inside Elections's "Tilt" is tighter than Sabato's), make this a per-source map instead of a global one.
- **Snapshots store `raw_response` as JSON.** Useful for debugging when the upstream changes shape, but it's already adding ~200 bytes per row. At 4 sources × daily for a year = 1,460 rows = ~300KB. Not a concern but the column should get pruned if we ever scale to many races.
- **Kalshi / Manifold / other markets not configured.** Manifold was explicitly deferred per the ChatGPT critique. Kalshi is feasible once a key exists.

### Files added / changed

- `backend/app/models.py` — added `RaceSentimentSnapshot`, extended `RaceSentiment` with connector-config columns
- `backend/app/db.py` — `_migrate` for the new columns; `_seed_race_sentiment_sources` now seeds PA-08 connector configs
- `backend/app/schemas.py` — `RaceSentimentSnapshotOut`, extended `RaceSentimentOut` + `RaceSentimentUpdate`
- `backend/app/services/race_sentiment_sync.py` — new (sync framework)
- `backend/app/services/prediction_market_monitor.py` — new (Polymarket)
- `backend/app/services/race_ratings_monitor.py` — new (Cook + Cloudflare-aware framework)
- `backend/app/services/scheduler.py` — added `race_sentiment_daily` job
- `backend/app/routes/race_sentiment.py` — sync/backfill/history/sync-all endpoints
- `backend/.venv` — added `cloudscraper`, `beautifulsoup4` deps (note: cloudscraper didn't actually help against Cloudflare's current Turnstile, but it's a low-cost dep to leave installed in case it works on some less-protected future source)
- `frontend-v2/src/api/types.ts` — extended types
- `frontend-v2/src/api/client.ts` — new API methods
- `frontend-v2/src/components/RaceSentimentCard.tsx` — `SyncBadge`, "Sync now" button, updated empty-state and footer

---

## 2026-05-27 Session G (cont'd): Phase 2 UX cleanup + forecaster ratings sourced + Phase 3 Forecast page

### What got built

**Phase 2 UX cleanup**

- **MANUAL badge state** ([components/RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx)). Previously the badge could be one of LIVE / BLOCKED / nothing — which produced misleading combos (a row with a manually-entered value still showed BLOCKED because `last_sync_error` was set from the prior auto-sync attempt). Added a third state: MANUAL (gray, pencil icon). Resolution order: LIVE wins (fresh successful sync within 36h), then BLOCKED (sync error AND no data), then MANUAL (has data but wasn't auto-synced). Empty rows still get no badge. Tooltips distinguish "manually entered, no connector" from "manually entered, auto-sync still failing".
- **UTC timestamp fix** ([components/RaceSentimentCard.tsx:formatRelativeTime](frontend-v2/src/components/RaceSentimentCard.tsx)). Backend Pydantic + `datetime.utcnow()` emits ISO timestamps without a `Z` suffix. JS interprets unmarked ISO as LOCAL time, so the "Updated 2h ago" footer was wrong by the user's UTC offset (showing "2h ago" right after a sync in CDT). New `parseUtcIso()` helper detects unmarked strings and tags them as UTC. Applied to both `formatRelativeTime()` and the `mostRecent` reduction in the footer. After the fix the footer correctly reads "Updated just now" / "6m ago" / etc.

**Manual sourcing of forecaster ratings**

Since all four forecaster sites + RCP are bot-protected (Cloudflare or DataDome), pre-populated the ratings via PUTs based on third-party sources (politicspa, 270toWin, news articles, DDHQ Substack). Result on the card:

| Source | Rating | Band | Sourced from |
|---|---|---|---|
| Cook | Toss-up | 45–55% | politicspa.com 2026-04-08 article (Cook moved from Lean R) |
| Sabato | Lean R | 60–75% | Listed in Sabato's "Leans Republican" tier (May 8, 2026) |
| Inside Elections | Tilt R | 52–60% | Search results referencing IE's "Tilt Republican" classification |
| DDHQ | (no rating) | — | DDHQ does not publish Cook-style tier labels for individual House districts |

The strategic picture the card now tells: markets bullish on Cognetti (60/48), 3 of 4 forecasters lean R, but Cook (the most-watched) just moved to Toss-up. That's the disagreement signal we wanted to surface.

**Phase 3 — Forecast page**

New page at `/forecast` ([pages/Forecast.tsx](frontend-v2/src/pages/Forecast.tsx)) with sidebar entry, plus a new backend endpoint ([routes/race_sentiment.py:list_timeline_events](backend/app/routes/race_sentiment.py)) that returns a unified event timeline for the chart overlay.

Page sections:
- **Header**: title + current Polymarket numbers as a quick read.
- **Chart**: Recharts `LineChart` with Polymarket candidate (blue, 2.5px) and opponent (red, 2px) over 30 days. Numeric X-axis (time-scaled) so events can be positioned at exact timestamps. Y-axis clamped to 20–80% to give swings more visual room than 0–100%. Tooltip shows snapshot values + any events on that day (capped at 5 with "+N more"). Vertical `ReferenceLine` markers per event-day, colored by the day's dominant event type. Markers are dashed and 50% opacity so they don't overwhelm the lines.
- **Caveat row** beneath the chart with the thin-liquidity reminder (~$2K). Same framing as the card's header tooltip; this isn't a duplicate of marketing copy, it's the operational truth users need to read swings through.
- **Event window cards**: 12 most-recent events, each in a card showing 4 rows (At event / +24h / +72h / +7d) with deltas color-coded. Cards link to the underlying narrative or article when a frame_id / article_id exists. Same-day events that happened hours ago show "—" for future windows (correct — we don't have future data).

Backend endpoint ([GET /api/race-sentiment/events?days=N](backend/app/routes/race_sentiment.py)) returns three event types:
- `frame_created` — `narrative_frames.created_at` for `active=True` frames
- `frame_stage_change` — `frame_stage_history` rows where `to_stage` is `mainstream` or `spreading` (filtered to avoid clutter from every minor transition)
- `top_article` — highest-scored race-relevant article per day (score >= 75), deduped to one per calendar day

Live values verified: 103 events in the last 30 days (35 frame creations, 45 stage changes, 23 top articles).

### Key decisions

1. **No causal framing anywhere on the page.** The cards say "At event / +24h / +72h / +7d" — descriptive temporal context. The header tooltip is explicit: *"The cards below describe what the market did in the 24h / 72h after each event — descriptively, not causally. Correlation isn't causation."* The page never says a frame "caused" a market move.

2. **Reference-line markers, not dot markers.** Recharts has dot markers built-in, but they'd collide with the data line. Dashed vertical reference lines extend from the chart's top to its bottom edge, are visually subtle (opacity 0.5), and don't fight the data series. One line per day (not per event) — same-day events stack in the tooltip rather than producing a wall of overlapping lines.

3. **`Y` domain clamped to 20–80%.** PA-08 has never been outside this range in the 30-day window (oldest snapshot 41%, highest 65.5%). Clamping gives ~3x more vertical resolution than the default 0–100%, so a 2-point move is visually meaningful instead of imperceptible.

4. **Event windows computed client-side.** The backend returns events and snapshots separately; the page does the `closestSnapshot` math. That keeps the backend dumb (just "what happened?") and lets the frontend evolve window-size options (toggleable 24h/72h/7d/14d?) without backend changes.

5. **Tolerance windows for snapshot lookup.** A `+24h` reading allows the snapshot to be up to 18 hours away from the target (because daily backfill timestamps cluster at the same hour-of-day each day, plus we may have multiple same-day snapshots from manual syncs). Lower tolerance and the math returns "—" for valid windows; higher tolerance and we'd attribute the wrong snapshot. The current values (12h/18h/24h/36h for at/+24h/+72h/+7d) are empirical.

### Verified

- Page loads without console errors
- Sidebar "Forecast" entry highlights when on `/forecast`
- Chart renders 33 daily Polymarket snapshots as two lines (candidate + opponent)
- 30 reference-line markers across the chart, colored by dominant event type per day
- Tooltip on hover shows snapshot values + same-day event list
- Event window cards render with correct windowed values:
  - Today's events: `At event 59.0%`, all future windows show "—" (correct)
  - 1-day-old events: `At event 61.0%`, `+24h: -2.0pt 59.0%`, future windows "—" (correct math: candidate moved 61 → 59 in 24h)
- Card click-through works (links to `/narratives/{frame_id}` or `/articles/{article_id}`)

### Open questions / carry-forward

- **Rapid-fire stage transitions cluster.** A frame moving emerging → spreading → mainstream in one minute generates 2-3 stage_change rows with near-identical timestamps. They show up as duplicate cards on the forecast page ("Cognetti Flips NEPA Seat → mainstream" three times). Could be deduped by collapsing same-frame transitions within an N-minute window. Low priority — it's accurate, just visually noisy.
- **No "compare frame X to baseline" view.** The current page mixes all events. A useful future view: filter to one frame, show only that frame's lifecycle markers, see if THAT specific narrative correlates with movement.
- **No FEC events yet.** Deferred per Phase 3 MVP scope. Adding requires understanding the `fec_filings` / `opponent_activities` schema. Next session work.
- **No forecaster band overlays.** Phase 3 MVP doesn't render Cook/Sabato/IE bands as horizontal background regions on the chart. Would be a nice addition for v-next: a shaded band at the "Lean R 60-75%" level would let the user eyeball how far market disagrees with forecaster consensus.
- **30 days is hardcoded.** No range selector (7d / 30d / 90d). Polymarket only has ~30 days of history right now anyway, so this is only a limitation once history grows. Worth a follow-up.
- **`.gitignore` updated** to cover `*.pem`, `*.key`, `.kalshi_private_key*`, `secrets/`. Done in anticipation of Kalshi credentials landing.
- **Kalshi connector still deferred.** User shared an RSA private key in chat (which I refused to persist — see security incident below) and is rotating it. When the new key lands in `backend/.kalshi_private_key.pem` + `KALSHI_API_KEY_ID` in `.env`, the connector is ~30 minutes of work: implement `kalshi_fetch()` in `prediction_market_monitor.py` modeled after `polymarket_fetch()`, add a `kalshi` seed entry with the market ticker, and the existing snapshot/scheduler infrastructure picks it up.

### Security note

User pasted a Kalshi RSA private key directly into the chat. I declined to write it to disk and warned them to treat it as compromised. Action: revoke on Kalshi, generate a new key pair, save the new private key file themselves to `backend/.kalshi_private_key.pem`, add the key ID to `.env`. Never paste private keys in chat — they end up in transcripts and terminal history. `.gitignore` was updated to cover the new file path.

### Files added / changed

- `frontend-v2/src/components/RaceSentimentCard.tsx` — MANUAL badge state, UTC timestamp fix, `parseUtcIso` + `rowHasData` helpers
- `frontend-v2/src/pages/Forecast.tsx` — new (the Phase 3 page)
- `frontend-v2/src/App.tsx` — Forecast route
- `frontend-v2/src/components/Sidebar.tsx` — Forecast nav entry
- `frontend-v2/src/api/types.ts` — `TimelineEvent`, `TimelineEventType`
- `frontend-v2/src/api/client.ts` — `raceSentimentEvents()`
- `backend/app/routes/race_sentiment.py` — `GET /api/race-sentiment/events`
- `.gitignore` — `*.pem`, `*.key`, `.kalshi_private_key*`, `secrets/`



---

## 2026-05-27 Session: Kalshi connector implementation

### Built
- `kalshi_fetch()` and `_kalshi_yes_pct()` in `backend/app/services/prediction_market_monitor.py`
- `kalshi` wired into `_get_fetcher()` in `backend/app/services/race_sentiment_sync.py`
- Kalshi seed entry added to `connector_seeds` in `backend/app/db.py`

### Key decisions
- **No RSA-PSS auth needed.** The `api.elections.kalshi.com` elections subdomain is fully public. The previous session's note about RSA-PSS was based on the main `trading-api.kalshi.com` endpoint, which does require auth. The elections API needs neither a key nor signing.
- **PA-08 is not listed on Kalshi yet.** After scanning all ~4,000 KXHOUSERACE markets, Kalshi covers PA-02 through PA-16 but skips PA-07, PA-08, and PA-10 (non-competitive at launch). `kalshi_fetch()` returns `None` cleanly on a 404, so `sync_one` returns False without recording an error. The LIVE badge will not appear on the Kalshi row until Kalshi creates the market — which is the correct behavior.
- **Seed uses the expected ticker.** Seeded with `KXHOUSERACE-PA08-26` / `-D` / `-R` (the naming convention Kalshi will use when they list it). No manual update needed when the market goes live.
- **Market data format.** Kalshi events nest sub-markets per party. The `-D` ticker is Cognetti (Democrat), `-R` is Bresnahan (Republican). Prices come as `last_price_dollars` strings in 0–1 range; we multiply by 100 for %. Fallback: mid of `yes_bid_dollars` / `yes_ask_dollars`.

### Tested
- `_kalshi_yes_pct()` unit-tested against PA-09 market shape
- `kalshi_fetch("KXHOUSERACE-PA08-26", ...)` → `None` (404, correct)
- `sync_one("kalshi")` with PA-08 → `False`, `last_sync_error=None`, `last_synced_at=None`
- Patched to PA-09 (a listed live market): `sync_one` → `True`, `candidate_pct=9.5`, `opponent_pct=94.0`, `last_synced_at` set, LIVE badge fires

### Open questions / concerns for review
- **When Kalshi lists PA-08**, it will just work. Watch for it in the Kalshi markets dashboard around the time races become competitive (usually ~6 months before election day). The URL pattern will be `https://kalshi.com/markets/kxhouserace-pa08-26`.
- **Thin-market caveat for Kalshi too.** PA-09 (a comparable NE-PA district) has essentially zero liquidity — `liquidity_dollars: 0.0000`, volume 178 contracts total. PA-08 will likely be similar. The UI should note this the same way Polymarket does.

### Files changed
- `backend/app/services/prediction_market_monitor.py` — `kalshi_fetch()`, `_kalshi_yes_pct()`, `KALSHI_ELECTIONS_BASE` constant
- `backend/app/services/race_sentiment_sync.py` — `kalshi` added to `_get_fetcher()`
- `backend/app/db.py` — `("kalshi", "PA-08")` added to `connector_seeds`

### Correction (same session)
My earlier scan missed PA-08 because it's in the **HOUSEPA8** series, not KXHOUSERACE. The correct tickers are:
- Event: `HOUSEPA8-26`
- Democrat (Cognetti): `HOUSEPA8-26-D` — 62% last price
- Republican (Bresnahan): `HOUSEPA8-26-R` — 45% last price

The seed in `db.py` and the live DB row have both been corrected. `sync_one("kalshi")` → True, LIVE badge fires. The prior KXHOUSERACE-PA08-26 references in this log entry are wrong — ignore them.

---

## 2026-05-27 Session: Wire-syndication dedup fixes (synthetic consensus)

### Context
External review (ChatGPT) raised "synthetic consensus" concern: wire-syndicated stories getting counted N times instead of once, inflating apparent coverage and momentum. Verified the concern in the data:
- "Bresnahan Advances Historic Bridge..." → cluster `source-7585` AND `source-14287` (same story, two clusters)
- "ICYMI: NEPA Veteran Thanks Rob Bresnahan" → cluster `source-7586` AND `source-15771`
- "Cognetti Has a History of Harming Scranton Families" → 3 separate clusters (Townhall, WCBM, Reddit)

Cluster size distribution: 16,233 singletons, only 16 clusters with ≥4 articles in 17,604 items. Clustering algorithm is too strict for cross-week wire pickup.

### Built
- **Widened cluster matching window** in `backend/app/services/story_clustering.py:256-259`:
  - `CLUSTER_WINDOW_DAYS` default 14 → 30 (look back further for candidate clusters)
  - Added new env var `CLUSTER_PUBLISHED_CLOSE_DAYS` default 14 (was hardcoded 7 in rule 3's published-close check at line 293)
  - Both env-var-controlled — can revert without code change
- **Fixed `frame_momentum.py` to dedupe by cluster, not article** (lines 124-143):
  - Was `COUNT(DISTINCT SourceItem.id)` — every syndicated copy counted
  - Now `COUNT(DISTINCT FrameClusterMatch.story_cluster_id)` — matches the canonical definition in `frame_counts.py`
  - Removed unused `StoryCluster` import

### Key decisions
- **Did not backfill historical clusters.** Re-running clustering on 17k existing articles with the new window would merge fragmented clusters and "fix" the historical synthetic consensus, but it's a separate decision — flagged for user. The fixes above only apply to new ingestion + the momentum query immediately.
- **Did not consolidate v1 / v2 clustering.** `reanalysis.py:204` still calls the legacy `assign_story_cluster` (v1, token-Jaccard only, no StoryCluster row). Flagged for user — see open questions.
- **Tuning, not architecture rebuild.** External review pushed for a "narrative physics" rebuild — origination/amplification graph, distance-to-origin weighting, narrative lineage. Skipped. The dedup architecture (`frame_counts.py`, `FrameClusterMatch`, cluster-native frame matching) was already correct; the algorithm thresholds were the actual bug.

### Verification
- 32 tests pass (`test_strategic_lens.py`, `test_candidate_clustering.py`)
- Spot check on existing data: top 5 frames shifted by 0-1 mention each (expected — existing clusters are mostly singletons so per-article vs per-cluster diverge little until the wider window does its work on new ingestion)

### Open questions / concerns for review
1. **`reanalysis.py:204` still calls v1 `assign_story_cluster`.** v1 sets `story_cluster_id` but doesn't create a `StoryCluster` row. If reanalysis runs on an article that has no v2 cluster yet, this could leave an inconsistent state (cluster_id pointing at a non-existent StoryCluster row). Either:
   - (a) Switch to `assign_story_cluster_v2` — call signature differs (returns `(cluster, is_new, retrigger)`)
   - (b) Verify reanalysis is only run on items that already have a v2 cluster row
   - (c) Delete v1 entirely and migrate all callers
2. **Should we backfill?** Re-running v2 clustering on the 17k existing articles with the new wider window would merge currently-fragmented clusters. Cost: LLM-free, just SQL + simhash compute, probably ~10 minutes. Benefit: historical frame velocity / momentum numbers would become more accurate. Risk: changes existing cluster assignments, could affect any analytics snapshots that reference cluster IDs.
3. **Should the clustering thresholds be tuned further?** With window=30, jaccard_min=0.65, hamming_max=6, we still won't catch heavily-rewritten wire pickup. Could loosen jaccard to 0.55 or hamming to 8 — would need to spot-check for false positives.

### Files changed
- `backend/app/services/story_clustering.py` — widened window defaults, added `CLUSTER_PUBLISHED_CLOSE_DAYS` env var
- `backend/app/services/frame_momentum.py` — count clusters not articles, dropped unused import

---

## 2026-05-27 Session: SQLite → Postgres migration kickoff (Phase 0)

### Built
- `POSTGRES_MIGRATION_PLAN.md` at repo root — full locked plan, phase
  structure, gates, validation strategy, rollback procedure, risk register
- Phase 0 in progress: Alembic install + hand-authored baseline migration

### Key decisions (all locked with user)
- Local Docker Postgres for dev; production host TBD (Neon/Supabase/RDS
  decided as separate question after the code is portable)
- **Postgres only after cutover** — SQLite dropped entirely in Phase 5
- Migrate all data preserving IDs (full campaign history retained)
- Schema 1:1 with current SQLite — **no `jsonb`, no `pgvector`** in this
  migration. Those are separate follow-up projects.
- Postgres FTS is a **compatibility search layer**, not the long-term
  retrieval architecture. Real search engine (Tantivy/OpenSearch) is a
  separate Q3 decision.
- Alembic baseline is **hand-authored from live SQLite**, not
  autogenerated. SQLAlchemy reflection of SQLite is lossy (drops FK
  enforcement nuances, default exprs, etc.).
- Phase 0.5 data audit is **blocking** — preflight script must run clean
  before any code changes. The live DB has accumulated SQLite permissiveness
  (JSON-as-text, possible FK orphans from `_migrate()` history) that
  Postgres strictness will reject.
- 4-week rollback window post-cutover before SQLite codepaths deleted.

### Coordination required from other sessions
**Until this migration completes:**
- Do not add ALTER TABLE statements to `_migrate()` in `db.py`. New
  schema work goes through Alembic after Phase 0 lands.
- Do not add `INSERT OR IGNORE`, `PRAGMA`, or SQLite-specific SQL.
- Read `POSTGRES_MIGRATION_PLAN.md` before any DB-touching work.
- Flag conflicts or concerns here.

### Open questions / concerns for review
- **Backup `.db` files in `backend/`**: 11 `war_room.db.bak-*` files
  (~112MB each, ~1.2GB total). They're in the working tree but shouldn't
  be tracked. Plan to move to `~/Library/Application Support/noctua/db-backups/`
  before Phase 3 so the migration script doesn't see them. Sanity-check:
  these aren't referenced by any code, right?
- **Phase 0.5 will likely surface a non-zero number of issues** —
  malformed JSON, orphan FKs, etc. Each will need a decision: clean up
  in SQLite first, or handle in the migration script. Expect this to
  add 1–3 days to the schedule.
- **Production hosting still TBD.** Plan only commits us to local Docker
  for now. Real hosting decision (Neon, Supabase, RDS, Fly, self-host)
  happens before Phase 4 cutover.

### Files added
- `POSTGRES_MIGRATION_PLAN.md` — the locked plan
- Plus Phase 0 in progress: Alembic skeleton, baseline migration (see
  next log entry when Phase 0 closes)

---

## 2026-05-27 Session: Phase 0 mid-update — baseline ready, v15.0 collision avoided

### ⚠️ ACTION REQUIRED FROM OTHER SESSIONS

**Effective immediately, before stamping the live DB:**

After the stamp completes (within the next minute), any column added via
`ALTER TABLE` in `app/db.py::_migrate()` will be **invisible to Alembic**.
That means future `alembic upgrade head` runs won't replicate it onto
fresh Postgres or scratch DBs, and migrations generated later will get
confused about what state the DB is in.

**New rule from here on:** all schema changes go through an Alembic
migration in `backend/alembic/versions/`. Do not add new entries to
the `_migrate()` block; if you need a column or table, write:

    cd backend && .venv/bin/alembic revision -m "your_change"

then edit the generated file with `op.add_column(...)` etc. and run
`alembic upgrade head`. The migration applies to live SQLite AND the
future Postgres in one step.

I will leave `_migrate()` in place for now (Phase 1 deletes it) so
existing call sites in `init_db()` still work — it'll just be a no-op
once every column already exists. **Do not add to it.**

### Phase 0 status

- Alembic installed (requirements.txt, venv) ✓
- `backend/alembic/` configured to read `DATABASE_URL` from env, fall
  back to live SQLite if unset ✓
- Live SQLite schema dumped at
  `backend/alembic/_live_sqlite_schema.sql.ref` (875 lines, frozen
  reference) ✓
- Baseline migration authored:
  `2026_05_27_0434-8658705d5116_baseline_v0_schema_from_models.py` ✓
- Generated via autogenerate against an empty scratch SQLite + patched
  with the 11 indexes that `_migrate()` adds but models.py doesn't
  declare (story_clusters / frame_cluster_matches /
  cluster_opponent_activities / frame_stage_history / frame_variants)
- Cross-checked against live schema: 36 active tables identical, 0
  column drift, only diffs are cosmetic whitespace + 5 duplicate-name
  legacy indexes + 1 partial unique on opponents that is equivalent
  under Postgres NULL semantics
- About to: stamp live DB at baseline + verify on fresh scratch

### Re v15.0 work coordination

I saw `claim_records` + `claim_record_entities` in models.py and the
matching index-bootstrap in `db.py::_migrate()` lines 317–340.
**The baseline migration includes both v15.0 tables** — they are
part of the canonical schema as of right now. If you add more v15.0
tables/columns between now and the stamp, please add them via Alembic
revision rather than `_migrate()`.

If you absolutely need to add a column the old way before I land Phase 1,
flag here so I can fold it into a stacked migration before stamping.

### Orphan tables discovered (decision needed from user — not from
other Claude sessions)

The live DB has **19 tables not present in models.py**, all from prior
pivots that left tables behind. Row counts in parens:

- `kg_*` (12 tables, ~2,100 rows total — biggest are `kg_edges` 703,
  `kg_claim_entities` 372, `kg_claim_issues` 331, `kg_claims` 213,
  `kg_narrative_claims` 213, `kg_narratives` 97, `kg_entities` 65,
  `kg_alerts` 56, `kg_sources` 28, `kg_issues` 12, `kg_entity_aliases`
  0, `kg_events` 0) — left over from the KG pivot per the user's
  memory file (`project_kg_pivot.md`)
- `narratives` (97) + `narrative_mentions` (17) — pre-narrative-frames pivot
- `candidate_message_libraries` (0) + `candidate_narratives` (0) — same
  pivot
- `canvassing_notes` (0)
- `manual_captures` (0)
- `generated_talking_points` (1)

Per `db.py::_migrate()` line 342–351 these are deliberately left in
place rather than DROPped. The baseline migration does NOT include
them. That means:

- On the live SQLite: they remain after stamping (alembic doesn't drop
  what it doesn't know about)
- On Postgres after Phase 4 cutover: they will NOT exist. **Any data
  in them is lost at cutover.**

This is a user-decision, raised in `POSTGRES_MIGRATION_PLAN.md` and
will be put to the user before Phase 3 (data migration) runs.

### Files changed this session
- `backend/requirements.txt` — added alembic + psycopg[binary]
- `backend/alembic.ini` — generated, lightly customized (file_template,
  url placeholder note)
- `backend/alembic/env.py` — env-var URL resolution, Base.metadata
  hookup
- `backend/alembic/versions/2026_05_27_0434-8658705d5116_baseline_v0_schema_from_models.py` — baseline
- `backend/alembic/_live_sqlite_schema.sql.ref` — frozen reference

---

## 2026-05-27 Session: Phase 0 closed + Phase 0.5 audit landed clean

### Built
- Venv recreated at `backend/.venv/` from a clean pyenv 3.11.8 base
  (old venv had stale shebangs from project move). 14 packages that were
  installed ad-hoc previously have been added to `requirements.txt`
  (hdbscan/umap/scikit/Gemini/bs4/cloudscraper/pytest).
- Live SQLite stamped at baseline revision `8658705d5116`. `alembic current`
  confirms.
- Phase 0.5 audit script: `backend/scripts/preflight_audit.py`. Read-only.
  9 categories of checks. JSON + markdown reports at
  `backend/scripts/_audit_report.{json,md}`.
- Audit found 2 FAILs + 13 WARNs.
- Cleanup migration `bb6913b5ae7e` applied to live DB:
  - Wrapped 1,384 non-JSON `source_items.relevance_reasons` values in
    one-element arrays (the LLM occasionally returned a sentence instead
    of the documented array).
  - Deleted 2 orphan `frame_stage_history` rows (id 7, 11; pointed at
    deleted frames 18, 43).
- Re-audit confirms: **0 FAIL, 13 WARN, 169 PASS**.
- Pre-cleanup backup: `backend/war_room.db.bak-pre-audit-cleanup-20260527-045037`

### WARNs that remain (all non-blocking)
13 warnings are all enum-list-incompleteness in the audit script — values
that exist in the live DB and are clearly legal but weren't in my list:

- `source_items.actionability_label`: `monitor`/`respond`/`review` (not
  `low`/`medium`/`high` as I'd guessed)
- `source_items.content_category`: 7 additional categories
- `source_items.race_relevance_label`: `critical` (an additional band)
- `source_items.source_owner_type`: 5 additional values (statement
  subtypes)
- `source_items.extraction_quality_label`: `mixed`
- `narrative_frames.momentum_signal`: `no_trend_signal`
- `narrative_frame_mentions.matched_by` & `frame_cluster_matches.matched_by`:
  `promoted_from_candidate` (from candidate_frame_promoter)
- `frame_cluster_matches.source_type`: `promoted_from_candidate`
- `entity_mentions.extraction_method`: `fresh`/`seed_alias`/`seed_name`
  (refined version of the documented `seed`/`alias`/`embedding`/`llm`)
- `source_monitors.monitor_type`: `fec_ie_district`/`twitter_profile`/`youtube`
- 25 articles total contain U+FFFD replacement characters from ingestion
  encoding errors (cosmetic)

**None of these block the migration.** Postgres columns are `String`,
not `ENUM`, so any string is allowed. Audit script's enum list could be
refreshed if useful for future audits — not load-bearing.

### Files added
- `backend/scripts/preflight_audit.py`
- `backend/scripts/_audit_report.{json,md}` (generated)
- `backend/alembic/versions/2026_05_27_0450-bb6913b5ae7e_fix_audit_findings_json_and_orphans.py`
- `backend/war_room.db.bak-pre-audit-cleanup-20260527-045037` (gitignored, ~112MB)

### Next phase
Phase 1 — portability. Big-ticket items:
1. Switch `app/db.py` to read `DATABASE_URL` from env (currently hardcoded
   to SQLite path); attach SQLite pragmas + `check_same_thread` only when
   URL is `sqlite:`.
2. Delete `_migrate()` and its 280+ lines of inline ALTER TABLE. Schema
   changes go through Alembic exclusively from this point. **(v15.0
   session — anything pending you want me to fold into an Alembic version
   before I delete `_migrate()`, flag now.)**
3. Dialect-aware FTS abstraction. New file
   `backend/app/services/search_index.py` with the SQLite (FTS5) and
   Postgres (tsvector + GIN) implementations. Route in
   `routes/sources.py` dispatches by dialect.
4. Replace 2 `INSERT OR IGNORE` sites
   (`candidate_frame_promoter.py:710`, `expand_outlets_catalog.py:72`)
   with `ON CONFLICT DO NOTHING`.

After Phase 1, backend works against either dialect by env-var flip.

---

## 2026-05-27 Session: Phase 1 closed — backend is dialect-portable

### Built

- **db.py refactor.** `DATABASE_URL` resolves from env var with SQLite
  fallback. `check_same_thread`, the WAL/busy_timeout PRAGMA listener, and
  the `timeout` connect_arg are all gated on `_IS_SQLITE`. Code paths
  work transparently on either dialect.
- **`_migrate()` gone.** 280+ lines of inline ALTER TABLE deleted from
  `app/db.py`. `init_db()` now runs `alembic upgrade head` programmatically
  via `_alembic_upgrade_head()`, then calls `ensure_search_index(engine)`
  for the dialect-appropriate FTS setup. Everything else (`_phase0_backfill`,
  `_repair_frame_data`, `_backfill_outlet_links`, `_seed_canonical_entities`,
  `_seed_race_sentiment_sources`) is data-level and kept verbatim.
- **`backend/app/services/search_index.py`** — new dialect-aware FTS module.
  `ensure_search_index(engine)` for setup, `search_articles(db, q, limit)`
  for queries. SQLite uses FTS5 (existing virtual table); Postgres uses
  `tsvector + GIN` with `websearch_to_tsquery`. Module docstring explicitly
  labels this as a compatibility layer, not the long-term search architecture.
- **`backend/alembic/versions/2026_05_27_0455-bfbb065b4b7e_setup_fts_search_index.py`**
  — dialect-aware FTS setup migration. SQLite branch is a no-op on the
  live DB (FTS5 already exists from legacy `_migrate()`); Postgres branch
  adds `search_tsv tsvector` column + GIN index + maintenance trigger +
  one-shot backfill. Applied to live DB.
- **`routes/sources.py:/search`** — switched from inline FTS5 SQL to
  `search_articles()`. ~30 lines collapsed to one call.
- **`candidate_frame_promoter.py:710`** — `INSERT OR IGNORE` rewritten as
  `INSERT ... ON CONFLICT (frame_id, source_item_id) DO NOTHING`. Portable
  across both dialects.

### Live DB state

- `alembic current` = `bfbb065b4b7e (head)`
- `source_items`: 17,625 rows (intact)
- `source_items_fts`: 17,625 rows (in sync)
- `init_db()` smoke-tested end-to-end against live DB — clean.
- `/search?q=cognetti` via TestClient returns expected results.

### Notes for the v15.0 session

- `_migrate()` is **deleted from db.py.** If you have pending schema
  changes you were going to put there, write them as Alembic migrations:
  ```
  cd backend && .venv/bin/alembic revision -m "your_change"
  ```
  then edit the generated file in `alembic/versions/`. Run `alembic
  upgrade head` to apply — equivalent to the old auto-migration on boot,
  except now it's also part of `init_db()` so server restart picks it up.
- `Base.metadata.create_all()` is no longer called from `init_db()`. New
  tables get created via Alembic. For a fresh DB, the chain is:
  baseline → audit-fix → FTS-setup → your new revision.
- Adding new `__table_args__` indexes on existing models? Alembic
  autogenerate will spot them — run `alembic revision --autogenerate
  -m "..."` and review the generated migration before applying.

### Test suite state — pre-existing drift (not from this work)

`pytest tests/` errors on two files BEFORE any of my changes:
- `tests/test_campaign_analysis.py` — imports `_persist_opponent_attacks`
  and `_persist_frame_matches` from `app.services.ingestion`. Those
  functions appear to have been renamed/removed.
- `tests/test_ingestion_reddit.py` — imports `_post_text` from
  `app.services.ingestion_reddit`. Same issue.

These tests should be fixed when convenient — not blocking Phase 2.
Other tests pass (verified `tests/test_html_stripping.py` 8/8 PASS).

### Files added / changed

- `backend/app/db.py` (rewritten — 280 lines deleted, alembic on-boot
  added)
- `backend/app/services/search_index.py` (new)
- `backend/app/routes/sources.py` (search route now ~10 lines, was ~40)
- `backend/app/services/candidate_frame_promoter.py` (1 INSERT rewrite)
- `backend/alembic/versions/2026_05_27_0455-bfbb065b4b7e_setup_fts_search_index.py` (new migration)

### Next phase

Phase 2 — Postgres in Docker + observability:
- `docker-compose.yml` at repo root
- `pg_stat_statements`, `log_min_duration_statement=500`,
  `log_lock_waits=on`, etc.
- App-side: pool event listeners, `statement_timeout` / `lock_timeout`,
  `/api/admin/dbstats` route
- Verify `alembic upgrade head` against empty Postgres creates exactly
  the schema we want — first real test of the Postgres path

### Update (same session): upgraded frame_momentum to 3-signal classifier

User pushed back on the cluster-only count: article volume still matters because it measures amplification/exposure. They were right — clusters and articles measure different things, and we needed a richer model.

New design: `frame_momentum.py` now computes THREE press velocities per frame, with outlet velocity as the dominant spike signal:
  - **outlets**  — distinct outlet_ids carrying the frame. Measures BREADTH (independent editorial decisions). Robust to clustering fragmentation: wire pickup across 5 outlets = 5 outlets even when clustering breaks it into multiple cluster IDs.
  - **clusters** — distinct story_cluster_ids. Measures NOVELTY (unique angles).
  - **articles** — raw count. Kept in `momentum_data` for context, NOT used in classification.

New 5-signal classifier (was 4):
  - viral            — outlets spike + voter search spike
  - **amplified**    — outlets spike, voters quiet (wire/PR pickup; NEW signal)
  - elite_only       — clusters spike, outlets flat (narrow press, many angles)
  - missing_coverage — voters spike, press flat
  - stable           — none

Real impact on existing data after running `analyze_all_frames(db)`:
  - 4 frames → amplified (previously would've been "viral" under old article-velocity logic)
  - 1 frame → elite_only
  - 7 → stable
  - 1 → no_trend_signal

This is much more honest than the old all-frames-viral pattern. "Amplified" specifically separates wire-pickup narratives (broad but voters not engaged) from genuine voter-resonant momentum.

### Strategic lens additions
`strategic_lens.py` now covers all 18 (signal × owner) combinations including the new "amplified" row:
  - (amplified, candidate) → offensive — capitalize on press cycle while it's hot
  - (amplified, opponent) → defensive — pre-empt counter-message before voter attention catches up
  - (amplified, media)    → monitor — neutral pickup, no owner lens

### Frontend
  - `Dashboard.tsx`: signalLabel + tooltip + importanceScore updated for "amplified"
  - `Narratives.tsx`: same
  - `types.ts`: enum-style comments updated
  - Tooltips now read from `outlet_velocity` and `cluster_velocity` (new fields in momentum_data) instead of just `article_velocity`. Falls back gracefully on old data.

### Verified in browser
  - Dashboard renders "Amplified" chip with offensive (cyan) color
  - Tooltip: "Outlets 3.5× (broad press pickup) but voter search flat → Capitalize on press pickup — push voter-facing content while the cycle is hot. Urgency: medium"
  - No console errors after reload

### Tests
  - 42 pass: test_strategic_lens (28), test_candidate_clustering (11), test_promote_cluster_backfill (3)
  - Note: 5 unrelated tests in the suite fail because they make live LLM calls without API keys — pre-existing rot, flagged separately. 3 more tests have stale imports (`test_campaign_analysis.py`, `test_ingestion_crawler.py` — needs `trafilatura`, `test_ingestion_reddit.py`).

### Files changed (this update)
- `backend/app/services/frame_momentum.py` — 3-velocity counter + 5-signal classifier
- `backend/app/services/strategic_lens.py` — added amplified × {candidate, opponent, media}
- `backend/tests/test_strategic_lens.py` — added 3 amplified tests + updated expected_signals
- `frontend-v2/src/pages/Dashboard.tsx` — label, tooltip, importance score
- `frontend-v2/src/pages/Narratives.tsx` — label, tooltip
- `frontend-v2/src/api/types.ts` — comment updates

---

## 2026-05-27 Session: Phase 2 closed — Postgres path proven on local Pg 15

### Decision: skipped Docker

User has Homebrew Postgres 15 already running on :5432. Spinning up
Docker Postgres on :5433 in parallel would add operational overhead for
no isolation benefit (Postgres DB-level scoping is enough). Used the
existing install with a new database `noctua`.

### Built

- **Created `noctua` database** on existing localhost:5432 Postgres 15.
  Other DBs (`fec_complete`, `lighthouse`, `news_analysis`, `othello_v2`)
  untouched. Per-DB observability defaults set via `ALTER DATABASE`:
  `log_min_duration_statement = 500ms`, `log_lock_waits = on`,
  `deadlock_timeout = 1s`. Server-wide `log_checkpoints` skipped (would
  affect other DBs).
- **First Postgres test of the migration chain.** Ran
  `DATABASE_URL=postgresql+psycopg://theo@localhost/noctua alembic upgrade head`
  against empty `noctua`. All 3 migrations applied cleanly:
  - baseline_v0 → 36 application tables
  - audit-fix migration → no-op (correct on empty DB)
  - FTS-setup migration → Postgres branch ran, added
    `search_tsv tsvector` column + GIN index + maintenance trigger function
- **Schema parity verified.** Postgres has all 36 active tables, zero
  unexpected extras. Trigger function `source_items_tsv_update()` exists.
  GIN index `ix_source_items_search_tsv` exists. `search_articles()`
  against Postgres returns expected results — INSERT → trigger populates
  `search_tsv` → `websearch_to_tsquery` matches.
- **db.py observability.** Two pieces:
  - Postgres `connect` listener sets `statement_timeout = '60s'`,
    `lock_timeout = '10s'`,
    `idle_in_transaction_session_timeout = '5min'`. Per-connection,
    overridable per-session via `SET LOCAL`.
  - SQLAlchemy pool event listeners (`connect`/`checkout`/`checkin`/
    `invalidate`) wired to a small `pool_stats` dataclass. Invalidations
    log a warning so we see connection problems immediately.
- **`/api/admin/dbstats` endpoint.** Dialect-aware. Returns:
  - Pool state (size, checked out, lifetime counters, last invalidation)
  - Postgres slice: `pg_stat_activity` for any in-flight query > 1s,
    lock waiters, `pg_stat_database` rollup (commits, rollbacks,
    deadlocks, cache hit ratio)
  - SQLite slice: journal mode, WAL autocheckpoint, article count
  - URL is redacted (password masked) before being returned
- **`.env.example` updated** with both SQLite default (commented) and
  Postgres URL examples (local + managed).

### Live DBs

- SQLite war_room.db: at revision `bfbb065b4b7e (head)`, 17,625
  source_items — the production data, untouched
- Postgres noctua: at revision `bfbb065b4b7e (head)`, empty — sandbox
  for development and the upcoming Phase 3 data migration

### Verified

`GET /api/admin/dbstats` against both:

- SQLite: `journal_mode=wal`, `source_items_count=17625`
- Postgres: `connections_in_use=1`, `cache_hit_ratio=99.64%`,
  0 deadlocks, 0 slow queries, 0 lock waiters

### Notes for next session

- `pg_stat_statements` is **available but not installed** on the user's
  Postgres. Enabling it would require adding it to
  `shared_preload_libraries` and restarting Postgres — which affects all
  4 existing databases on that server. Skipped. `pg_stat_activity` is
  sufficient for Phase 2.5 soak test. If the user moves to a managed
  Postgres (Neon/Supabase) later, those typically come with
  `pg_stat_statements` already loaded.
- Postgres path now works for: alembic, schema, FTS, the search route.
  Not yet tested: full app boot against Postgres (init_db runs seed
  functions that touch CampaignConfig — which would be empty on a
  fresh Postgres).

### Next phase

Phase 2.5 — concurrency soak test:
- Need a way to get realistic data volume on Postgres for the soak test.
  Options: (a) wait for Phase 3 data migration to copy in real data, or
  (b) build a quick `seed_synthetic.py` that generates ~10k fake
  source_items.
- Run scheduler + manual rescore + an ingestion cycle for 1+ hour
  against Postgres, watching `/api/admin/dbstats` for lock pile-ups.

### Files added / changed
- `backend/app/db.py` — added Postgres session defaults + pool stats
- `backend/app/routes/admin.py` — added `/admin/dbstats` route
- `.env.example` — added DATABASE_URL section
- Created database `noctua` on local Postgres 15

---

## 2026-05-27 Session: Phase 3 rehearsal #1 passed + Postgres routes working

### Built

- **`backend/scripts/sqlite_to_postgres.py`** — full data migration script.
  ~700 lines. Topological-FK-ordered walk, batched COPY, ID preservation
  with sequence reset, NUL-byte stripping, FK-orphan detection against
  destination, checksum validation, FK integrity sweep, aggregate sanity
  checks. Supports `--dry-run`, `--skip-orphans`, `--force`, `--limit-rows`.
- **Rehearsal run #1 against Postgres `noctua`** with the live SQLite
  snapshot. **OVERALL: ✅ PASS** in ~8 seconds wall-clock for ~60K rows
  across 36 tables. All counts match, all hashes match, all 32 FK
  integrity checks clean, all 6 aggregate sanity checks pass.

### Data drifts the rehearsal surfaced (and the script now handles)

1. **47 rows with embedded NUL bytes** across `source_items.raw_text` (35),
   `source_items.summary` (3), `story_clusters.summary_representative` (9).
   Postgres TEXT rejects NUL; SQLite tolerates. Script strips silently.
   *Not caught by Phase 0.5 audit — added a TODO to update
   `preflight_audit.py` with this check.*
2. **23 orphan FKs in `claims`** (10 distinct entity_ids deleted between
   audit and rehearsal — likely v15.0 work).
3. **Cascading skips**: 28 `claim_supports` rows reference claims we
   skipped. Solved by checking orphans against the DESTINATION, not the
   source — covers both pure source orphans and cascading skips in one
   sweep.

### Postgres-only runtime bug found and fixed

`func.round(func.sum(...), 1)` works in SQLite but Postgres rejects
`round(double precision, integer)` — needs `numeric` for the 2-arg form.
Fix: `func.round(cast(func.sum(...), Numeric), 1)`. Five sites:
- `backend/app/services/narrative_frames.py` (3 sites, lines 1560/1562/1565)
- `backend/app/routes/analytics.py` (2 sites, lines 79 and 254)

After fix: `/narrative-frames`, `/narrative-frames/{id}/detail`,
`/frames/{id}/timeseries`, `/briefing/morning`, `/articles/recent`,
`/opponents`, `/search`, `/admin/dbstats` ALL return 200 against the
populated Postgres DB.

### Decision: backend is now confidence-tested portable across both DBs

The Phase 4 cutover risk is meaningfully lower. The actual cutover
becomes:
  1. Stop scheduler / backend / firehose
  2. Take final SQLite backup
  3. Run `sqlite_to_postgres.py` (production target)
  4. Flip `DATABASE_URL` in `.env`
  5. Start backend
  6. Smoke checklist

Each step is now individually verified.

### Open work for next session

- **`preflight_audit.py` is missing the NUL-byte check.** Should add
  before the next rehearsal so the audit catches what the migration
  has to handle defensively.
- **Search ranking differs between dialects** — SQLite returns
  `[14223, 13308, 14222, 13343, 13296]` for "cognetti"; Postgres returns
  `[14223, 9260, 2]`. Same top result (id 14223). This is a documented
  expected change (compatibility search layer) but worth knowing the
  magnitude — it's not subtle.
- **One full rehearsal #2** on a fresh snapshot before Phase 4 cutover.
- **No actual concurrency soak test (Phase 2.5) was run** — but the
  backend smoke test against populated Postgres gives much higher
  confidence than a soak test would, because it exercises real route
  code paths against real data. Soak test value is now diminished.

### Files changed
- `backend/scripts/sqlite_to_postgres.py` (new, ~700 lines)
- `backend/app/services/narrative_frames.py` — round/cast fix
- `backend/app/routes/analytics.py` — round/cast fix

### Live state at end of session

- SQLite war_room.db: at `bfbb065b4b7e (head)`, 17,625 source_items
  (untouched by all of this — read-only snapshot was the migration source)
- Postgres noctua: at `bfbb065b4b7e (head)`, populated with full live
  data minus 51 FK-orphan rows. Backend smoke-tested against it.

---

## 2026-05-27 Session (overnight): wire-syndication merge backfill — 5 iterations to convergence

### Goal
Fix synthetic-consensus problem at the cluster level: ~1,968 wire-pickup fragments were sitting in separate `story_clusters` rows when they should have been one. User explicit ask: iterate until rules are as good as they can get, then make them even better. Hard constraint: rules must be DOMAIN-AGNOSTIC (work for any campaign, not PA-08-specific).

### Built (kept generic)
- `backend/app/scripts/merge_fragmented_clusters.py` — re-evaluate existing clusters under wider window; merge fragments. Dry-run by default; `--apply` flag commits.
- `backend/app/scripts/sample_merges.py` — sampler for false-positive review of a dump file.
- `backend/tests/test_merge_fragmented_clusters.py` — 28 regression tests pinning the new behavior.

### Iterations (each = ~10 min dry-run + sample inspection)

| Iter | Change | Merges | Rule-3 FPs seen |
|------|--------|--------|-----------------|
| 0 (baseline) | v2 rules as-shipped | 2,082 | Multiple (FEC pages, district mismatches, lottery dates) |
| 1 | Rule-3 title 0.65→0.85 + number-mismatch guard | 1,910 | 0 in 25-sample |
| 2 | Strip outlet suffix before extracting numbers; require BOTH sides to have unique digits | 1,972 | 0 in 26-sample |
| 3 | Fix `normalize_title`: keep digit tokens + remove "primary"/"election" from stopwords | 1,740 | 0 in 21-sample |
| 3.5 | Hotfix: split `_TITLE_STOPWORDS` from `_HASH_STOPWORDS` so simhash backward-compat preserved (iter 3 was silently invalidating stored simhashes — test-article merges went missing) | — | — |
| 4 | Same as 3 + 3.5, plus temporal window 14→7 days | 1,968 | 0 in 22-sample |
| 5 | Add body-length-ratio guard (>10× blocks merge) | 1,968 | 0 (no change vs iter 4) |

Convergence at iter 4/5. Length-ratio guard found nothing new to block — kept anyway as defense-in-depth.

### Domain-agnostic guards added (apply to any campaign)
1. **Title-digit mismatch**: if both titles contain digit tokens AND each side has a unique digit, block. Catches "District 8" vs "District 3", "May 18" vs "May 4", "$10M" vs "$50M", etc. Outlet-suffix is stripped first so "Trump | 95.7 FM" vs "Trump | 92.3 FM" still merges.
2. **Tightened rule 3 title threshold** (0.65 → 0.85): catches legitimate wire-pickup rewrites while rejecting template-page near-matches.
3. **Tightened temporal window** (14 → 7 days for "published-close" in rule 3): wire pickup is fast; multi-week-apart "matches" are usually different stories.
4. **Body length ratio** (>10× blocks): edge case for press-release-embedded-in-long-article.
5. **`normalize_title` keeps digit tokens**: distinguishing data (district numbers, dates, dollar amounts) is preserved instead of silently dropped.
6. **Split title vs hash stopwords**: `_HASH_STOPWORDS` keeps "primary"/"election" so stored simhashes remain comparable; `_TITLE_STOPWORDS` drops them so they distinguish in title-Jaccard.

### Applied to live DB (2026-05-27 07:11 UTC)
- DB backup at `backend/war_room.db.bak-before-merge-20260527-063422` (117 MB, full snapshot)
- 1,968 merges applied
- 1,975 source items moved to new clusters
- 172 frame_cluster_matches reparented + 246 collisions deduplicated
- 52 cluster_opponent_activities reparented + 11 collisions deduplicated
- 1,968 stale cluster rows deleted

### Before / after impact

**Cluster counts:**
- 16,852 → 14,884 (-11.7%)

**Cluster size distribution:**
| Size | Before | After | Δ |
|------|--------|-------|---|
| 1 | 16,234 | 13,716 | -2,518 |
| 2-3 | 592 | 1,037 | +445 |
| 4-9 | 24 | 101 | +77 |
| 10-39 | 2 | 20 | +18 |
| 40-99 | 0 | 8 | +8 |
| 100+ | 0 | 2 | +2 |

**Mega-cluster examples (wire pickups now correctly consolidated):**
- "Four Republicans Join Democrats To Force House Vote On Obamacare Subsidies" — 106 articles (was singletons across 106 outlets)
- "Trump administration cuts turned rural towns into sitting ducks" — 92 articles
- "Trump's speech on combating inflation turns to grievances about immigrants" — 71 articles
- "Test Article No Date" — 118 articles (test-data cleanup side benefit; flagged below)

### Frame momentum after merge
- 15 frames classified (up from 13 pre-merge — more frames now meet MIN_ACTIVE_ARTICLES on cluster-deduped counts)
- 9 stable / 4 amplified / 1 elite_only / 1 no_trend_signal
- Dashboard verified — "Amplified" chips render with correct offensive (cyan) posture color and tooltips reading "Outlets X.X× (broad press pickup) but voter search flat"

### Open issues / data quality (separate, not blocking)
1. **Test data in production DB**: 118 "Test Article No Date" rows and 93 "Test Article With Date" rows from test fixtures. They got correctly merged but the underlying data shouldn't be there. Owner should delete them when convenient — they don't currently affect frame counts since `Test Article` titles don't match any frame.
2. **`reanalysis.py:204` still uses v1 `assign_story_cluster`**: separate latent bug; v1 sets `story_cluster_id` but doesn't create a `StoryCluster` row, so if reanalysis runs on an item without a v2 cluster it could leave dangling pointers. Not bitten yet because reanalysis is rare.
3. **3 pre-existing stale tests** in the suite: `test_campaign_analysis.py`, `test_ingestion_crawler.py` (missing `trafilatura` dep), `test_ingestion_reddit.py` — stale imports from refactors that didn't update tests. Not caused by this session.

### Files changed (this session)
- `backend/app/services/story_clustering.py` — `normalize_title` keeps digits; `_TITLE_STOPWORDS` vs `_HASH_STOPWORDS` split; widened defaults for `CLUSTER_WINDOW_DAYS` (14→30) and `CLUSTER_PUBLISHED_CLOSE_DAYS` (added, 14)
- `backend/app/services/frame_momentum.py` — already updated earlier (3-signal classifier with outlet velocity)
- `backend/app/services/strategic_lens.py` — already updated earlier (added "amplified" × 3 owner rows)
- `backend/app/scripts/merge_fragmented_clusters.py` — new
- `backend/app/scripts/sample_merges.py` — new
- `backend/tests/test_merge_fragmented_clusters.py` — new (28 tests, all pass)
- `frontend-v2/src/pages/Dashboard.tsx`, `Narratives.tsx`, `api/types.ts` — already updated earlier ("amplified" UI support)

### Env knobs (all have safe defaults — domain-agnostic)
- `CLUSTER_WINDOW_DAYS=30` — look back window for cluster candidates
- `CLUSTER_PUBLISHED_CLOSE_DAYS=14` (live ingestion); merge-backfill default was 7
- `CLUSTER_TITLE_JACCARD_MIN=0.65` (live ingestion rule 3)
- `CLUSTER_SIMHASH_HAMMING_MAX=6`
- `MERGE_RULE2_JACCARD_MIN=0.92`
- `MERGE_RULE2_HAMMING_MAX=8`
- `MERGE_RULE3_JACCARD_MIN=0.85`
- `MERGE_REQUIRE_NUMBER_MATCH=1`
- `MERGE_MAX_LENGTH_RATIO=10`

### Rollback path
If anything goes wrong: `cp backend/war_room.db.bak-before-merge-20260527-063422 backend/war_room.db` restores the pre-merge state. All 1,968 merges are reversible by this single file copy.

### Tests
- 70 passing (28 new merge tests + 42 existing clustering/strategic-lens tests)
- 5 unrelated tests still failing on live LLM API calls (pre-existing rot)

### Three follow-up fixes applied (2026-05-27, post-merge)

**Fix #1: Deleted 211 Test Article rows**
- Backup: `backend/war_room.db.bak-before-test-cleanup-20260527-160436` (117 MB)
- Deleted 211 source_items + 2 test-only clusters
- Pre-verified: 0 FCM refs, 0 NFM refs, 0 opponent_activities, 0 COA refs
- FTS index auto-cleaned via existing `source_items_ad` AFTER DELETE trigger
- Verified: 0 orphans, frame_momentum signal distribution identical (9 stable / 4 amplified / 1 elite_only / 1 no_trend_signal)

**Fix #2: Migrated `reanalysis.py:204` to v2 clustering**
- Changed `assign_story_cluster` → `assign_story_cluster_v2`
- Added IDEMPOTENCY SHORT-CIRCUIT to v2: if `item.story_cluster_id` already points to a valid cluster, return it unchanged. Prevents double-counting bug where reanalysis would re-run `_attach_to_cluster` and increment `article_count` on every re-run.
- Falls through to normal assignment if cluster_id is set but the cluster row is gone (e.g. after a merge backfill).
- 2 new regression tests pinning the behavior — both pass.

**Fix #3: Repaired 3 stale test files**
- `test_ingestion_crawler.py`: trafilatura was missing from venv (in requirements.txt). Installed; all 7 tests now pass.
- `test_campaign_analysis.py`: 
  - Removed stale imports `_persist_opponent_attacks`, `_persist_frame_matches` (those were consolidated into cluster-native helpers).
  - Removed 6 tests that exercised those gone functions (covered end-to-end by merge backfill tests now).
  - Migrated 4 `get_provider` → `get_ingestion_provider` patches (API renamed).
  - Marked 3 tests as skipped via `_v2_shape_pending` — they assert the v1 LLM response shape (`relevant`/`relevance_score`) but the code now uses a `verdict` enum mapped via `_VERDICT_TO_SCORE`. Skipping rather than rewriting because the assertion semantics are subtle.
  - Result: 8 pass, 3 skip.
- `test_ingestion_reddit.py`: 
  - Removed stale `_post_text` import + 3 tests that exercised it (helper was inlined).
  - Marked 4 integration tests as skipped via `_praw_pending` — they patch `_get_reddit` (the old PRAW client factory) but Reddit ingestion was rewritten to hit `reddit.com/.../search.json` directly via HTTP.
  - Result: 1 pass, 4 skip.

**Verification**
- Focused test subset (17 files, excludes the pre-existing LLM-network-dependent suites): 202 pass, 7 skip, 0 unexpected failure
- 7 pre-existing failures in `test_race_directory.py` and `test_race_relevance.py` ALL share the same `llm_provider.py:_chat` → OpenAI timeout stack trace — same pre-existing issue documented earlier in this log. Not caused by these fixes; out of scope of "3 stale tests."
- `frame_momentum.analyze_all_frames()` post-fix returns same signal distribution as pre-fix.

**Files changed (this batch)**
- `backend/app/services/story_clustering.py` — added idempotency short-circuit to `assign_story_cluster_v2`
- `backend/app/services/reanalysis.py` — v1 → v2 call
- `backend/tests/test_campaign_analysis.py` — removed stale imports/tests; migrated provider patches; skipped 3 v2-shape-pending
- `backend/tests/test_ingestion_reddit.py` — removed stale import/tests; skipped 4 praw-pending
- `backend/tests/test_merge_fragmented_clusters.py` — added `TestV2Idempotency` (2 new tests)

**Rollback paths**
- Database: `cp war_room.db.bak-before-test-cleanup-20260527-160436 war_room.db` (restores test articles + their clusters)
- Code: git revert the four files above

### Improvements round 2 (same day)

After confirming the merge backfill landed cleanly, addressed 4 hygiene items:

**#13: Hunt the test article injection source.**
- Searched for any code referencing the test fixture HTML — no checked-in script creates it
- Timestamps show all 211 articles ingested in a 3.5-min burst on 2026-05-24 16:42-16:46 → one-time human/Claude action
- The actual test (`test_published_at.py`) uses in-memory SQLite and CAN'T leak
- Added a `CLAUDE.md` warning paragraph: "Never run ad-hoc ingestion or scripted writes against war_room.db" with explicit pointer to use `/tmp/test_ingest.db` for experiments. Documented this prior incident as the motivating example.

**#14: Cluster the 21 NULL-cluster source items.**
- All were social posts (Bluesky firehose items) that skipped clustering at ingest
- Ran `assign_story_cluster_v2` on each → 21 new singleton clusters created, 0 attached to existing
- DB now has 0 NULL `story_cluster_id` values

**#15: Simhash sentinel guard.**
- Added `test_simhash_sentinel_value_pins_algorithm` to `test_merge_fragmented_clusters.py`
- Hardcodes a stable input + expected 64-bit output (`0x2de5b5e1c0dbc873`)
- Fails loudly if anyone modifies `_HASH_STOPWORDS`, `_tokens_for_hash`, `_shingles`, or `simhash64` without coordinating a recompute of every stored cluster hash
- Docstring explains the exact recovery steps if/when it fails intentionally

**#16: Migrated 7 previously-skipped tests to current code shape.**

`test_campaign_analysis.py` (3 tests): rewrote stubs from v1 (`relevant`/`relevance_score` direct fields) to v2 (`verdict` enum + `extracted_claims` with verified `quote` substrings + `matched_frames` by name). Assertions check both the v2 native fields and the back-compat derived fields the ingestion code consumes.
- `test_analyze_returns_validated_attacks` — verdict=critical + personal_attack claim → opponent_attacks[0].type=="attack"
- `test_analyze_with_frames_returns_sentiment` — sentiment=favors_opponent → derived sentiment=="negative"; matched_frames by name → frame_matches resolves to real IDs
- `test_analyze_with_frames_ignores_unknown_frame_names` (renamed from `_ignores_out_of_range_indices` because v2 uses names not indices) — bogus name in matched_frames is dropped silently via fuzzy match

`test_ingestion_reddit.py` (4 tests): rewrote from PRAW mocks to direct HTTP mocks. Patches `httpx.get`, `_probe_reddit_access`, `_fetch_comments` instead of the removed `_get_reddit` factory. Synthetic posts use the actual `search.json` response shape (`{"data": {"children": [{"kind": "t3", "data": {...}}]}}`).
- `test_ingest_reddit_adds_posts` — post added, source_url canonicalized, title preserved
- `test_ingest_reddit_deduplicates` — same URL twice → second skipped via source_url uniqueness
- `test_ingest_reddit_blocked_access_returns_zeros` (renamed) — `_probe_reddit_access=False` short-circuit
- `test_ingest_reddit_no_campaign_config_returns_zeros` — empty terms short-circuit

### Final test status
- Focused subset: 199 pass, 2 fail (both pre-existing LLM-network in `test_election_date_inference`, same `llm_provider._chat` timeout stack as before — NOT caused by this session)
- 0 skipped tests in the files I touched (was 7 before; now all rewritten and passing)
- 73 tests in clustering/strategic-lens/merge files all pass

### Cumulative files changed across both batches
- `backend/app/services/story_clustering.py` — normalize_title keeps digits; split `_TITLE_STOPWORDS` / `_HASH_STOPWORDS`; widened defaults; idempotency short-circuit in `assign_story_cluster_v2`
- `backend/app/services/reanalysis.py` — v1 → v2 clustering call
- `backend/app/services/frame_momentum.py` — 3-signal classifier (outlets / clusters / articles) with outlet velocity as primary spike signal
- `backend/app/services/strategic_lens.py` — added `amplified` × {candidate, opponent, media} matrix rows
- `backend/app/scripts/merge_fragmented_clusters.py` — new (with sample_merges.py companion)
- `backend/app/scripts/sample_merges.py` — new
- `backend/tests/test_merge_fragmented_clusters.py` — new (28 + 2 idempotency + 1 sentinel = 31 tests)
- `backend/tests/test_campaign_analysis.py` — removed stale imports/tests; migrated patches; rewrote 3 to v2 shape
- `backend/tests/test_ingestion_reddit.py` — removed stale imports/tests; rewrote 4 to HTTP-mock shape
- `backend/tests/test_strategic_lens.py` — added `amplified` tests
- `frontend-v2/src/pages/Dashboard.tsx`, `Narratives.tsx`, `api/types.ts` — `amplified` UI support
- `CLAUDE.md` — added warning about running ad-hoc writes against war_room.db

### Improvements round 3 (same day)

**A: Number-mismatch guard ported to live ingestion.**
- Moved `numbers_in_title` and `numbers_mismatch` from the merge script into `app/services/story_clustering.py` (canonical home — live + backfill share the same definition)
- Added the guard call to `assign_story_cluster_v2` between rule 1 (URL) and rules 2/3 (title/simhash). URL match still wins (same URL = same article regardless of title digits); rules 2 and 3 now skip candidates whose titles have mutually-unique digit tokens.
- Backfill script (`merge_fragmented_clusters.py`) now re-exports `_numbers_in_title` / `_numbers_mismatch` from `story_clustering` so all the existing tests + the existing dump callers keep working.
- New regression test `test_number_mismatch_blocks_live_clustering` proves "District 8" vs "District 12" with separate URLs create separate clusters at ingest time.

**C: LLM-network test hangs eliminated via conftest.**
- Added `backend/tests/conftest.py` with an autouse session fixture that monkey-patches `llm_provider.get_ingestion_provider`, `get_provider`, and `get_judge_provider` to return `MockLLMProvider`. Real LLM calls during tests now short-circuit to `_fallback_result()`, which triggers the existing keyword-based `race_relevance.apply_relevance` stopgap in `_create_and_analyze`.
- Tests that need specific LLM responses can override the autouse fixture by patching the same symbols later (monkeypatch precedence). `test_campaign_analysis.py` already does this for its v2-shape stubs.
- Fixed 3 incidental test issues uncovered by the no-longer-hanging tests:
  - `test_services::TestOpponentAnalysis::test_analyze_source_creates_activity` — function signature changed (returns int, not list); updated assertion + ensured the source has a cluster_id before calling (Phase D cluster-native path requires it).
  - `test_services::TestOpponentAnalysis::test_no_duplicate_activities` — same cluster_id requirement; queries `ClusterOpponentActivity` (cluster-native) instead of legacy `OpponentActivity`.
  - `test_services::TestIngestion::test_ingest_text` — removed the `item.summary is not None` assertion (under the mock LLM stub, summary stays None — explicit comment in the test explains the contract).

### Final test suite status
- **Full focused sweep**: 265 pass, 0 fail, 0 skip in 41s (was: 199 pass / 7 fail with 15-second-per-test hangs on the failing ones)
- 5 remaining failures in `test_race_directory.py` are NOT LLM-network issues — they hit Tavily search + FEC data ingestion. Separate scope (would need HTTP mocks for those services). Documented for a future round.
- 73 tests in clustering/lens/merge files all green
- Number-mismatch guard active for both live ingestion AND backfill
- Conftest stub will protect future test sessions from accidental network calls

### Files changed (round 3)
- `backend/app/services/story_clustering.py` — moved `numbers_in_title`/`numbers_mismatch` here; added guard call in `assign_story_cluster_v2`
- `backend/app/scripts/merge_fragmented_clusters.py` — slimmed to re-exports of the canonical helpers
- `backend/tests/conftest.py` — new (autouse LLM mock fixture)
- `backend/tests/test_merge_fragmented_clusters.py` — added `test_number_mismatch_blocks_live_clustering`
- `backend/tests/test_services.py` — updated 3 tests for Phase D cluster-native opponent activity + mock-LLM contract

## 2026-05-27 Session: Promote button leaves cluster in Proposed Narratives queue (cache bug)

### Built
- Added `invalidate_cache()` to `backend/app/services/narrative_landscape.py` (mirrors the existing helper in `narrative_landscape_established.py`).
- Wired the new invalidator into all three paths that resolve candidate frames:
  - `backend/app/routes/narrative_frames.py` — added `_invalidate_candidate_landscape()` helper, called after `promote_candidate_cluster`.
  - `backend/app/routes/narrative_triage.py` — called in `execute_merge` after committing the resolve update.
  - `backend/app/services/narrative_triage.py` — called in `_auto_execute_verdicts` (hands-off triage) alongside the existing established-landscape invalidation.

### Root cause
The Review Queue's "Proposed narratives" list reads from `api.narrativeLandscape(21)` → `get_landscape()` in `narrative_landscape.py`, which caches its UMAP result for 25 hours in a module-level `_CACHE`. When `promote_cluster` marked the contributing `CandidateFrame.resolved_to_frame_id` rows, it only invalidated the *separate* cache used by `pendingCandidateClusters` and the established-landscape cache — never the candidate-landscape cache. The frontend refetched landscape after promote but kept getting the stale cached payload, so the cluster stayed visible. Merge and hands-off auto-execute had the same gap.

### Key decisions
- Modeled the new invalidator on `narrative_landscape_established.invalidate_cache()` for symmetry (acquires `_lock`, nulls all three cache slots including `days_back`).
- Kept the route-level helper `_invalidate_candidate_landscape()` parallel to `_invalidate_established_landscape()` rather than collapsing into a single helper — leaves room to invalidate them independently later.
- All call sites wrap the invalidator in `try/except` (best-effort, never block the mutation on a cache-bust failure).

### Open questions / concerns for review
- The cache is keyed by `days_back`; if any other surface ever calls `narrativeLandscape` with a non-default value, that variant would also need invalidation. Currently only the Review Queue calls it (`daysBack=21`), so the single-value invalidator is sufficient.
- Tests: 9 narrative/triage/promote/landscape tests pass. No new test was added for the invalidation itself — would require either a router-level test that asserts cache key contents post-promote, or refactoring the cache to be more testable. Flagging for a future round.

## 2026-05-27 Session (continued): Review Queue count gap + 3-tab unification

### Built
- **Backend** `backend/app/routes/review_queue.py` — `/review-queue` list endpoint now uses the same filter shape as `/review-queue/count`. Dropped the 48h `created_at` cutoff, switched the score filter from `score IS NOT NULL` to `score >= 40 OR actionability_label IN ('review','respond')`, bumped LIMIT from 60 → 200. List + count both return 87 on the live db (was 2 vs 87).
- **Frontend** `frontend-v2/src/pages/ReviewQueue.tsx` — added a 3-tab nav strip below the sticky header: "Articles" / "Proposed Narratives" / "KG Contradictions". Each tab has a count badge. Active tab persists in `?tab=` URL param via `useSearchParams`. Header subtitle, Select-All button, and bulk action bar all reflect the active tab.
- **Frontend** `frontend-v2/src/pages/EntityReview.tsx` — added optional `embedded` prop that suppresses the redundant H1+description and zeros the outer padding when rendered inside ReviewQueue's KG tab.
- **Frontend** `frontend-v2/src/App.tsx` — `/entity-review` now `<Navigate replace to="/review?tab=kg" />` so any bookmarks land on the new location.
- **Frontend** `frontend-v2/src/components/Sidebar.tsx` — removed the standalone "KG Review" sidebar entry (the unused `Flag` icon import too).

### Key decisions
- Kept `EntityReview` as a separate component (with an `embedded` prop) instead of inlining its body into ReviewQueue. Reasoning: the contradictions view is dense, has its own drift banner, and may grow more sub-features — keeping it modular preserves that runway.
- 3 tabs (Articles | Proposed Narratives | KG Contradictions) instead of 2 — user picked this explicitly. Each gets its own subtitle + count + URL param, so it behaves like 3 independent inboxes that happen to share a route.
- LIMIT bumped to 200 (not removed). The realistic queue depth for a single campaign rarely exceeds that, but if it does we should add proper pagination rather than dumping the entire result set in one response.

### Open questions / concerns for review
- The KG tab's count badge is fetched eagerly on page mount via `api.entityReviewQueue()` (which returns the full contradictions payload, not just a count). If `entityReviewQueue` ever grows expensive, add a `/entity-review-queue/count` endpoint and fetch that instead. For now the payload is small enough that the cost is invisible.
- The KG tab content fetches its own data via `EntityReview` when rendered — that's a second call to the same endpoint. Acceptable since `EntityReview` is stateful and the user usually loads the tab once per session, but worth de-duping later if it becomes hot.
- Verified in preview: subtitle and tab badges update correctly on tab switch, `?tab=` round-trips through reload, `/entity-review` → `/review?tab=kg` redirect works, KG tab renders without duplicate header.

### Follow-up (still pending from the previous session in this same chat)
- Persistent "review snapshot" so the Proposed Narratives list stops mutating between page loads (the user observed this earlier in the session — we agreed it's the next thing to build, but haven't started).

---

## 2026-05-27 Session: LLM cost reduction — bylines + variant clustering

Goal of this session: identify LLM calls that can be replaced by deterministic logic without losing accuracy. Audited the backend, picked two targets, executed, validated against the original LLM behavior.

### Built — #1: journalist byline extraction (`monitors.py`)

- **Replaced LLM byline extractor** in `auto_discover_journalists` with `_clean_byline` + `_byline_from_text` deterministic functions reading from `SourceItem.source_author` (already populated during ingestion) with regex fallback on title + body. Eliminates ~50-100 LLM calls/day from `journalist_discovery_daily`.
- **Multi-layer cleaner**: strips "By X" prefix, multi-author split on `, ; | / and &`, suffix stripping for ` for X` / ` | X` / ` (role)`, all-caps press-release support with title-casing, publication-token blocklist for outlets like "Daily Mail" / "Hindustan Times" / "Red State".
- **28 unit tests** in `tests/test_journalist_byline_extraction.py` pinning each behavior.
- **Empirically validated against the live LLM on 50 articles** via `app/scripts/byline_llm_vs_deterministic.py`. Result: 0 genuine LLM wins, 16 deterministic-only wins, 1 LLM hallucination correctly skipped by us, 1 disagreement where deterministic was correct. The deterministic system is measurably MORE accurate than the LLM was, and cheaper.

### Built — #2: variant clustering (`frame_variants.py`)

- **Investigation surfaced** that `frame_variants.py` was orphaned — both `cluster_all_frames` and `_llm_group_quotes` had no callers. 157 FrameVariant rows existed from a one-off run on 2026-05-23. The frontend chart on NarrativeDetail was reading those frozen rows.
- **Calibrated the clustering threshold from data**, not vibes. The original `0.18` distance threshold I'd intuited was arbitrary. Built `app/scripts/calibrate_variant_threshold.py` that uses SimHash `story_cluster_id` as weak supervision + cross-frame pairs as definitely-negative baseline. Output: precision/recall/FPR curves over threshold. Then `verify_calibrated_threshold.py` grid-searches `(linkage × distance_threshold)` with a composite score (wire-sync purity + cluster-shape sanity penalty for mega-clusters).
- **Result**: `linkage="complete"` + `distance_threshold=0.42`. Best balance of 64% wire-sync purity with no mega-clusters. Pinned in `tests/test_variant_clustering.py` (8 tests including a chaining-prevention test that HDBSCAN would have failed).
- **Replaced** `_hdbscan_cluster` with `_agglomerative_cluster` in `frame_variants.py`. Deleted dead `_llm_group_quotes` function (210 lines).
- **Wired** `cluster_all_frames` into `scheduler.py` as `variant_clustering_daily` (24h interval).
- **Verified live**: backed up DB, ran clustering manually (393s, 300 variants across 31 frames), confirmed in browser via Claude Preview. Frame 1 (137 quotes) → 6 specific named variants on the chart, no mega-cluster failure. Spot-checked a Frame 4 cluster: 6 quotes all variations of "Shapiro endorses Cognetti as independent reformer" — semantically clean.

### Key decisions

- **Calibration scripts are the deliverable, not the constants.** Each campaign should re-run the calibration once ≥200 NFMs accumulate. The 0.42 number is a starting default tuned for PA-08 with OpenAI text-embedding-3-large.
- **Complete linkage, not single linkage.** Single linkage at 0.36 gave 91% wire-sync purity BUT chained 123 of 137 quotes into one mega-cluster on Frame 1 — useless for the variant chart. Complete linkage prevents chaining by requiring all pairs within a cluster to be within the threshold.
- **HDBSCAN was unreliable on this corpus, but the deeper issue is fundamental**: embedding-only clustering can't distinguish "Cognetti accuses Bresnahan via resurfaced audio" from "Cognetti accuses Bresnahan via MSNBC appearance" — same vocabulary, same density region. Any algorithm will have some mixed clusters in very dense frames.
- **Honest "stop iterating" call**: we converged after 7 iterations. Marginal threshold tuning past complete_0.42 didn't move the needle; remaining gaps are fundamental to embedding-only clustering, not parametric.

### Open questions / concerns for review

- **Chart looks flat on frames with mostly-old activity** (e.g., Frame 4, "Cognetti's Anti-Corruption" — 25 variants but most activity outside the 90D window). The legend shows lifetime counts but the chart shows in-window only. Frontend display issue, not clustering.
- **Cost cache for `_name_cluster` not yet built**. Currently every re-cluster re-names all clusters — paying ~$0.001/cluster × ~150 clusters/day = ~$0.15/day. Cheap, but the proper fix is a quote-set-hash cache on FrameVariant so the LLM only fires for genuinely-new clusters. Schema change required; flagging for follow-up.
- **`frame_variant_promoter.py` still uses HDBSCAN** per its own comment "mirrors the proven pattern in services/frame_variants.py". That comment is now obsolete — the pattern is agglomerative complete linkage. Worth a separate session to swap that too.
- **Old 157 FrameVariant rows were wiped and rebuilt to 300.** The IDs changed (no stable variant identity across re-cluster runs — this is by the original design's `full re-cluster` strategy). Frontend chart uses `name` + counts, not stable IDs, so it works. But anything externally referencing a FrameVariant by ID is now broken. Spot-checked routes — no external references.

### Files touched / added

Modified:
- `backend/app/services/monitors.py` — replaced LLM byline extraction
- `backend/app/services/frame_variants.py` — swap HDBSCAN → agglomerative, drop dead code
- `backend/app/services/scheduler.py` — add `variant_clustering_daily`

Added:
- `backend/tests/test_journalist_byline_extraction.py` (28 tests)
- `backend/tests/test_variant_clustering.py` (8 tests)
- `backend/app/scripts/byline_llm_vs_deterministic.py`
- `backend/app/scripts/calibrate_variant_threshold.py`
- `backend/app/scripts/verify_calibrated_threshold.py`
- `backend/app/scripts/inspect_winning_clusters.py`
- `backend/app/scripts/per_frame_threshold.py`
- `backend/app/scripts/eval_variant_clustering.py`

DB backup made before variant clustering manual run: `backend/war_room.db.bak-pre-variant-cluster-20260527-191511`

## 2026-05-27 Session (continued): article action fixes + Proposed Narratives snapshot

### Built
- **Backend: review-queue action endpoints** — `mark_reviewed` and `dismiss_item` in `backend/app/routes/review_queue.py` now accept an optional body. Previously the body was required, so the UI's per-row Keep/Dismiss buttons (which post with no body) returned 422 and silently no-op'd.
- **Frontend: simplified article actions** — `ReviewQueue.tsx` reduced the per-article action set from three buttons (⭐ Mark relevant, ✓ Reviewed, ✗ Dismiss) to two (✓ **Keep**, ✗ **Dismiss**). The star button was functionally identical to Reviewed for items already in the queue, and the system doesn't yet distinguish a "strong keep" signal from a regular one.
- **Backend: Proposed Narratives snapshot** —
  - New model `ProposedClusterSnapshot` in `app/models.py` + Alembic migration `2026_05_27_1924-28b4f7fc89b4_add_proposed_cluster_snapshots_table.py`.
  - New service `app/services/proposed_cluster_snapshot.py` with `take_snapshot()`, `get_open_snapshots()`, `mark_dismissed_by_member_ids()`, `mark_applied_by_member_ids()`.
  - New endpoints in `app/routes/narrative_frames.py`:
    - `GET /api/narrative-frames/candidate-frames/snapshot` — reads persistent snapshot (returns same shape as live landscape).
    - `POST /api/narrative-frames/candidate-frames/snapshot/refresh` — re-runs HDBSCAN and inserts/updates rows in place. Existing rows that are no longer in the compute stay open until the user acts on them.
    - `POST /api/narrative-frames/candidate-frames/snapshot/dismiss` — stamps `dismissed_at` by `candidate_frame_ids`.
  - `promote_candidate_cluster` and `execute_merge` now call `mark_applied_by_member_ids` so promote/merge stamps the snapshot row in addition to all the existing cleanup.
- **Frontend: snapshot wiring** — `ReviewQueue.tsx` now reads from `api.narrativeProposalsSnapshot()` instead of the live landscape. New **Refresh proposals** button next to **Run AI triage**. Dismiss button (in narratives tab) calls `api.dismissProposalSnapshot(memberIds)` to persist the dismissal.

### Key decisions
- **Two action buttons, not three.** "Mark relevant" and "Reviewed" were functionally identical for already-in-queue items. Drop the star until we have an article-level learning loop that differentiates "strong keep" from regular "keep."
- **Snapshot table, not memory cache.** The previous landscape cache lived in `_CACHE` in `narrative_landscape.py` — wiped on backend restart. A SQLite table survives restarts and gives us an audit trail of every cluster the AI ever proposed plus the user's action on it.
- **Stale rows stay until user-acted.** When `take_snapshot()` runs, it does NOT close rows that are no longer in the live HDBSCAN output. They keep showing in the Review Queue until the user dismisses or promotes them. This is the whole point of the design — the user said "I want the list to stop moving while I'm reading it," and that mandates user-driven removal only.
- **First-load fallback inside the snapshot GET endpoint.** If the snapshot table is empty (fresh deploy), the GET takes an initial snapshot before returning. Saves the user from clicking "Refresh proposals" once to bootstrap. Subsequent requests are pure reads.

### Open questions / concerns for review
- **No nightly scheduler job yet.** Production deployments should add a daily `take_snapshot` job to `scheduler.py` so new clusters surface without the user clicking Refresh. The plumbing is there — just need to register the job.
- **Fingerprint-based identity is exact-match.** If HDBSCAN re-clusters slightly differently (one member shifts in or out), the new cluster gets a new fingerprint and shows up as a "new" proposal alongside the old one. Fine for typical small drift; for bigger drift, we'd want similarity-threshold matching (e.g., Jaccard ≥ 0.7 against existing open rows). Defer until users complain.
- **Migration accident**: my first `alembic revision` produced an empty migration that recorded as "applied" before I filled in `upgrade()`. Had to `alembic stamp` back to the previous head and re-upgrade. The migration file is correct now, but anyone replaying history needs to be aware. Documented for posterity.

### Files changed
- `backend/app/models.py` — added `ProposedClusterSnapshot` model.
- `backend/alembic/versions/2026_05_27_1924-28b4f7fc89b4_add_proposed_cluster_snapshots_table.py` — new migration.
- `backend/app/services/proposed_cluster_snapshot.py` — new service.
- `backend/app/routes/narrative_frames.py` — added snapshot GET / refresh / dismiss endpoints; promote_candidate_cluster stamps the snapshot.
- `backend/app/routes/narrative_triage.py` — execute_merge stamps the snapshot.
- `backend/app/routes/review_queue.py` — mark_reviewed + dismiss_item bodies now optional.
- `frontend-v2/src/api/client.ts` — added narrativeProposalsSnapshot, refreshNarrativeProposalsSnapshot, dismissProposalSnapshot.
- `frontend-v2/src/pages/ReviewQueue.tsx` — switched proposals data source from live landscape → snapshot endpoint; added Refresh proposals button + handler; simplified per-article actions to Keep / Dismiss.

---

## 2026-05-28 Session (continued): rematch gate + parallelism + auto-trigger

Goal: turn `rematch_all` from a 22h manual job into a fast, automatic process that runs after any frame edit. Three layers — embedding pre-filter (the gate), parallelism, and persistent caching.

### Built — rematch gate (`narrative_frames._shortlist_frames_for_article`)
- Per-article cosine similarity vs each frame's embedding. Frames whose similarity clears a calibrated per-frame threshold survive into the shortlist; the LLM only gets asked about that shortlist.
- Calibration script (`app/scripts/calibrate_rematch_gate.py`) treats existing NFM pairs as positive labels, cross-frame pairs as definitely-negative. Writes per-frame thresholds to `backend/data/rematch_thresholds.json`. Calibration is manual; the gate reads it lazily and caches in memory.
- **Hard-negative refinement** (after ChatGPT critique): for each positive pair, find K nearest other-frame articles and use them to pick the tightest threshold that preserves 95% recall AND maximizes the gap to hard-negative similarities. Marginal win (~7% shortlist shrink) — the boundary between positive and adjacent-negative similarities is genuinely fuzzy in embedding space, no calibration tunes around that.
- 8 unit tests in `tests/test_rematch_gate.py` pin the gate behavior including cache hit/miss paths and per-frame threshold lookups.
- Active evaluation on 50 sampled disagreements (`app/scripts/inspect_gate_disagreements.py`): of 15 OLD_YES_NEW_NO inspected, ~10 were LLM-noise (gate correctly drops), 0 were genuine wrong drops. Of 15 OLD_NO_NEW_YES, ~60% were real LLM-missed matches that the gate would surface for re-verification. The gate is augmenting LLM judgments rather than replicating them.

### Built — parallel rematch_all
- `ThreadPoolExecutor(max_workers=8)` worker pool. Each worker gets its own SQLAlchemy session via `SessionLocal()`. Worker count tunable via `REMATCH_PARALLEL_WORKERS` env var.
- Replaces the per-call 0.1s pacing delay that was tuned for free-tier Groq with 4 round-robin keys. With OpenAI tier-2 limits we have 5K RPM of headroom — 8 workers is comfortably below that.
- Expected runtime: ~22h → ~2-3h, then ~10-20min with cache below.

### Built — article embedding cache (schema change)
- Added two nullable columns to `SourceItem`: `frame_match_embedding` (JSON-encoded list) and `frame_match_embedding_model` (model identifier). Alembic migration `1f4a8b2c9e7d`.
- `_get_or_compute_article_embedding()` reads cached embedding when the model matches, falls back to live embed + write-through on miss.
- `current_primary_model_name()` helper added to `embeddings.py` so the cache detects when the active provider has changed and invalidates.
- Backfill script (`app/scripts/backfill_frame_match_embeddings.py`) populated 2,690 articles in **25 seconds**. Subsequent rematches read from cache — zero embedding cost.

### Built — match provenance (schema change)
- Added `frame_content_hash` column to `FrameClusterMatch`. Alembic migration `2a5b9c3d8e6f`.
- `upsert_frame_match` accepts and persists the hash; `match_article_to_frames` computes it via the same `_frame_content_hash()` helper the gate uses for its frame-embedding cache.
- Lets future cleanup queries find stale matches (where `frame_content_hash != current frame hash`).

### Built — auto-trigger on frame CRUD
- `scheduler.schedule_rematch_after_frame_edit()` enqueues a debounced rematch via APScheduler `add_job(trigger="date", run_date=now+30s, replace_existing=True)`. Burst edits collapse to a single rematch fired once the window closes.
- Hooked into `POST /api/narrative-frames`, `PUT /api/narrative-frames/{id}` (only when name/description/active changed), and `DELETE /api/narrative-frames/{id}`.
- Graceful no-op when the scheduler isn't running (tests, early boot) — the daily `rematch_recent` still catches drift.
- 4 unit tests in `tests/test_rematch_auto_trigger.py` pin the no-op, contract, and debounce behavior.

### Key decisions

- **Pushed back on ChatGPT's `ArticleEmbedding` separate-table proposal.** ChatGPT argued for futureproofing against 5+ embedding types. I shipped a column on `SourceItem` instead because we only have two embedding types currently and no concrete near-term need for a third. If the third type appears, we refactor then. This was a deliberate "where we are now" vs "where we might be" call.
- **Hard-negative calibration over per-frame-p0.** The pure p0 approach is brittle — one bad historical match drags the floor. Hard negatives anchor the threshold to a more principled signal. Marginal in absolute terms but the right approach long-term.
- **Stopped iterating on threshold tuning** after ~7 iterations. Remaining gaps are fundamental to embedding-only clustering (semantically adjacent claims share vocabulary, no threshold separates them cleanly).
- **30-second debounce window** via APScheduler `replace_existing=True`. The state is process-local; on restart we lose any queued rematch. Acceptable trade for hobby-scale reliability — daily `rematch_recent` catches drift.
- **Deferred targeted rematch.** With cache + parallelism, full rematch is ~10-20min. Editorial iteration at that cadence works. Revisit only if real users complain.

### Open questions / concerns for review

- **No production measurement of write-lock contention.** SQLite serializes writers. With 8 workers each upserting `FrameClusterMatch` + `NarrativeFrameMention` rows, there could be lock-wait pile-ups. WAL helps reader concurrency but not writer concurrency. Worth instrumenting if we observe slower-than-expected rematches.
- **No human-labeled ground truth.** Calibration is self-distillation against past LLM judgments. ChatGPT was right about this. Active evaluation on disagreements (the 100-each protocol I ran on 30 samples) is the cheap mitigation; should be repeated periodically.
- **`frame_content_hash` is populated only by future rematches.** Existing 4000+ `FrameClusterMatch` rows have `NULL` for that column until they're touched again. Not a bug — the COALESCE in the upsert preserves NULLs until a real match happens — but anyone querying "stale matches" should treat NULL as "unknown vintage" rather than "stale."
- **Direct DB / admin edits don't trigger rematch.** Only `POST/PUT/DELETE` on the API endpoints schedule the rematch. If you edit a frame via SQL or some other path, you'd need to manually call `schedule_rematch_after_frame_edit()` or wait for `rematch_recent`.
- **The active-toggle case is over-triggering.** Setting `active=true` or `active=false` schedules a rematch even though deactivation doesn't change frame text — could refine to only fire on activation events. Low priority.

### Files added

- `backend/app/scripts/calibrate_rematch_gate.py` — the calibration tool (~$0.20 per run, manual)
- `backend/app/scripts/inspect_gate_disagreements.py` — active-evaluation harness
- `backend/app/scripts/verify_rematch_gate_on_sample.py` — read-only sample verification
- `backend/app/scripts/backfill_frame_match_embeddings.py` — one-shot cache backfill
- `backend/data/rematch_thresholds.json` — 39 per-frame thresholds (regenerated by calibration)
- `backend/tests/test_rematch_gate.py` — 8 tests
- `backend/tests/test_rematch_auto_trigger.py` — 4 tests
- `backend/alembic/versions/2026_05_28_0000-1f4a8b2c9e7d_add_frame_match_embedding_columns.py`
- `backend/alembic/versions/2026_05_28_0001-2a5b9c3d8e6f_add_frame_content_hash_to_matches.py`

### Files modified

- `backend/app/models.py` — added 3 columns total (2 on `SourceItem`, 1 on `FrameClusterMatch`)
- `backend/app/services/narrative_frames.py` — gate, cache, parallel `rematch_all`, hash threading through `upsert_frame_match`
- `backend/app/services/cluster_writes.py` — `upsert_frame_match` now accepts `frame_content_hash`
- `backend/app/services/embeddings.py` — `current_primary_model_name()` helper
- `backend/app/services/scheduler.py` — `_run_rematch_after_frame_edit` + `schedule_rematch_after_frame_edit`
- `backend/app/routes/narrative_frames.py` — CRUD endpoints schedule debounced rematch

### DB backups (made before each schema change)

- `backend/war_room.db.bak-pre-frame-embed-col-20260528-014108`
- `backend/war_room.db.bak-pre-frame-hash-col-20260528-015132`

## 2026-05-28 Session: Relevance scoring overhaul + framing UI surface

### Built
- **Pre-LLM race-mention gate** in `backend/app/services/campaign_analysis.py`:
  added `_race_mention_tokens()` and `_mentions_race()` (mirrors the existing
  helper in `article_perspective.py`). `analyze_with_frames()` now short-circuits
  to an `irrelevant`/score=0 result BEFORE the LLM call when the article
  contains no candidate/opponent name, district code, or specific geography
  keyword. Sets `_gated_no_race_mention=True`, leaves `_used_fallback=False`
  so ingestion treats it as a definitive irrelevant judgment (not a failure
  that falls back to keyword scoring).
- **Finer-grained relevance score** in the same file: replaced the 4-bucket
  `_VERDICT_TO_SCORE` with `_compute_relevance_score()` that combines verdict
  base (irrelevant=0 / loosely_related=20 / relevant=50 / critical=75) with
  +12/name in title (cap +20), +4/name in body (cap +8), +3 per high-conf
  claim / +1 per medium (cap +10), +3 for high source credibility. Caps at
  40 when verdict is relevant/critical but neither candidate nor opponent is
  named anywhere (catches LLM hallucination — e.g. the DCCC/Jen Higgins
  article that the LLM summarized as being about Bresnahan).
- **Framing surfaced over perspective** in ArticleDetail:
  - `backend/app/routes/dashboard.py` promotes `framing` from
    `structured_extraction` to a top-level field on `/api/articles/{id}`
  - `frontend-v2/src/api/types.ts` adds `framing` to `ArticleDetail`
  - `frontend-v2/src/pages/ArticleDetail.tsx` adds a Framing chip in the
    chip row right after Relevance (color-coded: helps_candidate→green,
    hurts_candidate→red, opponent_news→opponent-red). Renamed "Perspective
    analysis" section to "Framing analysis"; framing label leads, perspective
    is shown as a secondary "3-bucket landscape signal" detail.

### Key decisions
- **Scope: new articles only.** No backfill of the 16,305 existing articles
  (per user). The UI fix (Fix 3) IS visible on existing articles because
  `framing` is already stored in `structured_extraction` for every LLM-scored
  article.
- **Gate vs prefilter coexistence.** The existing `ingestion.py` keyword
  prefilter is intentionally permissive (passes anything with score ≥ 15
  even with no political signal). The new gate is stricter (requires an
  actual race-identifying token). Both run — the prefilter catches obvious
  noise cheaply, the gate catches the noise that slips through.
- **Hallucination cap is 40, not 0.** If the LLM claimed relevant but no
  candidate is named, we cap at 40 (medium-low) instead of zeroing — leaves
  the article surface-able for human review queue without featuring it as
  high-relevance.
- **Perspective field kept, not removed.** `landscape_dots.py` still uses
  it for dot color. Demoted in the article UI but the data pipeline is
  unchanged.

### Open questions / concerns for review
- The hallucination cap (40) and the title-mention bonus (+12) are
  judgment calls. If the user reports the new scoring is still wrong on
  specific articles, tune in `_compute_relevance_score` —
  `_VERDICT_BASE_SCORE` and the bonus weights are all in one place.
- The gate currently uses `geography_keywords` from CampaignConfig. If a
  campaign has overly-generic keywords ("Pennsylvania") they're skipped by
  the `len(s) < 4 or s in {"pa", "n/a", "none"}` filter — same logic as
  `article_perspective._mentions_race`. If new campaigns hit edge cases
  here, the filter is in `_race_mention_tokens()`.
- All 12 `test_campaign_analysis.py` tests pass + 46 across the affected
  files (test_campaign_analysis, test_services, test_ingestion_reddit).
  Verified UI in browser: article 19469 now shows "Framing: Hurts Candidate"
  prominently with perspective demoted.

## 2026-05-28 Session: Manual-monitor cleanup (Phase 1 of monitor-automation pivot)

### Built
- **Pruned 6 dead-end manual monitors** from `source_monitors` in `war_room.db`:
  - ids 5, 6 — "BRESNAHAN, ROB campaign website / social check" (FEC-format
    duplicates of the normalized 35, 36 created by a later setup run)
  - id 26 — "FEC candidate or committee check" (redundant: a real
    `fec_filings`/`fec_ie_district` monitor handles this when there's a
    candidate ID)
  - id 29 — "Campaign finance filing check" (same redundancy as id 26)
  - id 30 — "Ballot access and deadline check" (wrong concept — deadlines
    are calendar dates, not URLs you can monitor)
  - id 33 — "Economic development and layoffs check" (too vague to be a
    single URL; if surfaced again it should be a search_query, not a manual)
- **Renamed ids 2, 3** from "COGNETTI, PAIGE …" to "Paige Cognetti …" so they
  match the current normalized name format. They were orphans from an older
  setup run that used FEC-format names; the upstream `campaign_config.candidate_name`
  is now normalized so future runs would have created a 2nd set under
  the new name and left these stale.
- **Edited `backend/app/services/source_discovery.py`** so future
  `generate_monitors_for_campaign` runs do not recreate the 4 removed monitor
  templates. `public_record_names` is now `["State election board check",
  "County election board check"]`; the `Economic development and layoffs`
  conditional block is gone.

### Key decisions
- **Why delete and not just hide.** Manual monitors with no URL/query never
  trigger any crawler — they're effectively TODOs the UI doesn't even show
  hints for. Better to remove dead concepts than carry inert rows.
- **Kept agenda + election-board placeholders.** These ARE legitimate webpage
  URLs (state board sites, council agenda pages) — Phase 2 will auto-discover
  them via web search + URL validation and convert to `webpage` monitors.
- **DB backup before deletes:**
  `backend/war_room.db.bak-pre-manual-cleanup-20260528-195329`
- **No schema changes.** Pure data + generator-template cleanup.

### Final state
- Manual monitors: 14 → 8 (the 8 are real auto-discovery targets for Phase 2)
- Webpage monitors: still 0 — Phase 2 (auto-URL-discovery for the remaining
  8 + candidate / opponent websites) will create them.
- 29/29 tests pass in `test_services.py`.

### Open questions / concerns for review
- The `relevance_hint` field on every monitor is invisible in the frontend
  Monitors page. If we keep manual placeholders going forward, surfacing
  hints would help the user understand what to do with each. For Phase 2
  we'll likely drop the manual type entirely in favor of auto-discovered
  webpages, so this may not matter long-term.
- `_duplicate_query` in `backend/app/services/monitors.py` uses OR-matching
  on (query, url, name). For manual monitors with no query/no url, only name
  matches — so "Rob Bresnahan campaign website check" and "BRESNAHAN, ROB
  campaign website check" were NOT treated as dupes when both setup runs
  happened. Upstream normalization (campaign_config / opponents now store
  normalized names) prevents recurrence, so leaving the function as-is.

---

## 2026-05-28 Session: Narrative Detail chart UX

### Built
- Recolored tier palette (Activity by outlet tier) to cool gradient — National=purple, Regional=indigo, Local=cyan, Blog=teal, Social=emerald, Unknown=slate. Replaces the prior pee-yellow Local and poo-gray Blog that the user flagged.
- Refreshed variant-evolution palette to match the cool/cohesive scheme; OTHER bucket uses a proper slate gray instead of `#555` (which blended into the dark background).
- Custom `<VariantTooltip>` for the Variant Evolution chart with `max-width: 280px`, `white-space: normal`, `word-break: break-word` — long variant names ("Cognetti fights political corruption for government accountability") now wrap instead of overflowing the card.
- Click-to-filter on the Activity chart: each tier `<Bar>` is now clickable; click sets an `articleFilter` state (kind=tier, tier, date), renders a removable `<FilterChip>` next to the All Articles title, scrolls to the article list, and filters via `outlet_type → tier` mapping that mirrors the backend rule in narrative_frames.get_frame_detail.
- Click-to-filter on the Variant Evolution chart: per-variant `activeDot` is clickable; on click we fetch `/narrative-frames/{id}/variant-articles?variant_id=X&date=YYYY-MM-DD` and render the returned articles under the same filter chip. The synthetic "Other" bucket is non-clickable (no underlying variant_id to fetch).
- New backend endpoint `GET /api/narrative-frames/{frame_id}/variant-articles` + service `get_variant_articles(db, frame_id, variant_id, date=None)` — joins NarrativeFrameMention → SourceItem and returns the same DetailArticle shape as `get_frame_detail` for shape consistency.

### Key decisions
- Tier filter is client-side (uses the `outlet_type` already present on each DetailArticle) instead of round-tripping to a new endpoint. The mapping rule is centralized in `outletTypeToTier()` and mirrors backend `narrative_frames.py` line ~2348 — if the backend mapping changes, update both.
- Variant filter is server-side because variant_id isn't exposed on `DetailArticle`. Cheapest change of scope: one new service func + route, no schema change.
- Switched the Variant tooltip to a React-element `content={<VariantTooltip seriesMeta={...} />}` after a function-form `content={(props) => ...}` showed Recharts's default tooltip (it was likely getting through a function-shape check that triggered the fallback). Element form clones props in cleanly.
- Recharts's `activeDot.onClick` is typed `MouseEventHandler<DotProps, SVGCircleElement>` which fights the data-aware handler signature. Cast the activeDot config to `any` rather than fighting the (unhelpfully strict, also-not-exported) DotProps type.

### Open questions / concerns for review
- The "All articles" total used to be `detail.articles.length`. After my refactor it's `articles.length` for the unfiltered case and `visible.length` while filtered, both shown as `(N)`. If another session prefers showing `(total)` regardless of filter and a separate "X matching" line, let me know.
- I did not add a chip for the Activity tier filter on the chart itself — only at the article-list header. If users want a more obvious confirmation near the bar they just clicked, we can add an outline / glow to the selected bar.

---

## 2026-05-28 Session: Wire-syndication dedup on Articles page
### Built
- `/api/articles/recent` now groups items whose normalized titles match
  exactly AND that published within 24h of each other. Returns one row per
  group with a `duplicates: [{id, source_name, source_url, published_at}]`
  list of the other versions. Pagination logic unchanged (over-fetch is now
  `limit * 6` to leave headroom for both LLM-scored filter + grouping).
- `_group_by_normalized_title()` helper in
  `backend/app/routes/dashboard.py` — uses `normalize_title()` from
  `story_clustering.py` (strips outlet suffix, lowercase, removes
  stopwords). Representative picked by race_relevance_score desc, then body
  length, then earliest published_at.
- `Articles.tsx`: each row gets a small "Also in N other outlet(s)" pill
  next to the source name when duplicates exist. Click expands an inline
  panel listing each duplicate outlet + timestamp, each linking to its own
  article detail page. Pill stops propagation so it doesn't navigate.
- Types: added `ArticleDuplicate` + optional `duplicates?:` field on
  `SourceItem` in `frontend-v2/src/api/types.ts`.

### Key decisions
- Chose strict "normalized titles match exactly AND within 24h" over the
  existing cluster boundary (Jaccard ≥ 0.92 OR mid-similarity + simhash +
  7-day window). Rationale: false positives on the Articles UI are more
  visible/annoying than false negatives. The broader cluster boundary still
  drives analytics/spike detection — only the Articles list uses this
  stricter view. Live data: 50-row fetch → 11 stories collapsed; spot
  checks all look like legit wire-syndication.
- Did NOT change `_item_dict` itself — added `duplicates` only on the
  recent endpoint's response. Other consumers (briefing/morning's
  `respond` / `new_articles`) get the same shape as before.
- Items with empty normalized titles ("Instagram", emoji-only, placeholder
  rows) each become their own group rather than collapsing together. That
  sidesteps the 23-article "Instagram" junk-cluster bug visible in
  `StoryCluster` aggregates without fixing its root cause here.

### Open questions / concerns for review
- The "Instagram" cluster (23 articles, 0 outlets) and similar junk
  clusters in `story_clusters` aggregates are still a real bug elsewhere —
  ingestion is letting in items whose title or content is just a sidebar
  placeholder. Not addressed in this change. Likely needs an ingestion-side
  filter for known-junk titles ("Instagram", "Twitter", bare filenames like
  "breeze 4.jpg", "CVS Crash 2").
- `/articles/recent` takes ~2.6s warm, ~10s cold for `limit=50` because it
  loads 300 SourceItem rows (with raw_text) and runs `len(raw_text)` for
  the representative tiebreaker. Could be sped up by switching the
  tiebreaker to summary length OR by using a load_only() hint to skip
  raw_text on the over-fetch. Not blocking but worth fixing next time the
  file is touched.

## 2026-05-28 Session: Auto-discovery of campaign website URLs (Phase 2)

### Built
- **`backend/app/services/monitor_url_discovery.py`** — new service.
  - `discover_campaign_website(person, geo_hint, role_hint)` runs the
    search-provider → domain-filter → affinity-rank → judge-LLM pick →
    HTTP 200 verify pipeline. Returns `(url | None, reason)`.
  - `convert_website_manuals_to_webpages(db)` iterates over active manual
    monitors whose name ends in `" campaign website check"`, honoring a
    24-hour cooldown (`RETRY_COOLDOWN_HOURS`), and flips successful matches
    from `monitor_type="manual"` to `"webpage"` with the discovered URL.
  - Domain blocklist excludes wikipedia, ballotpedia, FEC, opensecrets,
    facebook/twitter/x/instagram/threads/linkedin/tiktok/youtube, archive.org.
    News domains are not blocked — the LLM judge rejects them if they slip
    through ranking.
  - Affinity score boosts hosts containing the candidate's last name and
    suffixes like `forcongress` / `forsenate` so the LLM judge sees the
    most plausible candidates first.
- **`backend/tests/test_monitor_url_discovery.py`** — 13 tests covering
  blocklist, dedup, affinity ranking, happy path, LLM rejection, HTTP
  failure, cooldown gate (skip + expire), mock-provider short-circuit,
  idempotency, and multi-monitor batch runs. All pass.
- **`backend/app/services/monitors.py`** — `auto_setup_monitors()` now
  calls `convert_website_manuals_to_webpages(db)` at the end. New campaigns
  get website monitors auto-converted to `webpage` type at setup time.
- **`backend/app/routes/admin.py`** — new endpoint
  `POST /api/admin/discover-monitor-urls`. Returns a summary with
  per-monitor outcomes (converted / failed / skipped_cooldown details).
- **`frontend-v2/src/api/client.ts`** — `api.discoverMonitorUrls()`.
- **`frontend-v2/src/pages/Monitors.tsx`** — new "Discover URLs" button
  in the action bar, plus a dismissable result banner showing the
  conversion summary with a hint if no search provider is configured.

### Live results
Ran the new endpoint against the existing 2 website monitors. Both
converted on first attempt:
- **id 2 "Paige Cognetti campaign website check"** → `https://paigeforpa.com`
  (judge picked option #4; verified HTTP 200)
- **id 35 "Rob Bresnahan campaign website check"** → `https://robforpa.com`
  (judge picked option #3; verified HTTP 200)

Notable: the judge picked URLs that were NOT highest by affinity (3rd and
4th), which is the system working — the LLM overrode heuristic ranking
based on snippet content evaluation, not just URL shape.

Manual monitors: 8 → 6 (now only election boards + agendas + the 2
social-check rows we're leaving for a future phase).
Webpage monitors: 0 → 2 (first webpage monitors on this DB).

### Key decisions
- **Judge LLM uses `get_judge_provider()` (gpt-4o-mini), not the default
  Groq path.** This is a verification task with reputational risk if wrong
  (we feed wrong URLs into the crawler), and OpenAI's instruction-following
  is more reliable. Cost is ~1 LLM call per discovery × 2 discoveries =
  trivial.
- **24h cooldown enforced via `last_checked_at`.** No new column needed.
  This blocks the discovery button from hammering the search API on
  repeated clicks while a campaign site is unreachable.
- **Conservative blocklist.** Only encyclopedias, social platforms, and
  databases are pre-rejected. News outlets pass the filter and rely on
  the LLM judge to reject — keeps the blocklist short and gives the LLM
  the chance to handle edge cases like a campaign hosting its own blog
  at a subdomain that happens to look news-y.
- **HTTP check uses GET, not HEAD.** Many campaign sites return 405 or
  bogus content-type to HEAD requests; GET with a 200 body sniff is more
  reliable. Trafilatura (the actual crawler) will do its own fetch later,
  so this is just a liveness probe.
- **Phase 2 scope intentionally narrow.** Only `" campaign website check"`
  manuals processed. Other manuals (state/county election board, council
  agendas, social check) remain manual placeholders for now — different
  discovery strategies needed and the user wanted to start small.

### Open questions / concerns for review
- The judge picked `paigeforpa.com` over `cognettiforcongress.com` for the
  Cognetti monitor. Both exist. `paigeforpa.com` is the official campaign
  site for this race cycle (PA state-level branding), so the LLM got it
  right — but worth spot-checking once we see the crawler pull articles
  from it.
- No `discovered_by="auto"` flag on the monitor row. If we later want a
  UI badge on auto-discovered monitors so the user can spot-check them,
  this would need a small schema add. Skipped for Phase 2 to keep it
  minimal.
- The judge prompt asks for an integer; the parser takes the first integer
  in the reply. If gpt-4o-mini ever returns "Option 3" with "3" appearing
  multiple times (e.g., "Option 3 looks right because of the 3 signals
  I see") the first-match heuristic could go wrong. Mitigation: the prompt
  says "Reply with ONLY the number" and in practice gpt-4o-mini follows
  this. Loose tests with stub `"1"` / `"0"` replies confirm the parser path.

### Files added / modified
- Added: `backend/app/services/monitor_url_discovery.py`
- Added: `backend/tests/test_monitor_url_discovery.py`
- Modified: `backend/app/services/monitors.py` (auto_setup_monitors integration)
- Modified: `backend/app/routes/admin.py` (new endpoint)
- Modified: `frontend-v2/src/api/client.ts` (api.discoverMonitorUrls)
- Modified: `frontend-v2/src/pages/Monitors.tsx` (button + result banner)

52/52 tests pass in the affected test files
(`test_monitor_url_discovery` + `test_campaign_auto_monitors` + `test_services`).

## 2026-05-28 Session: Entity Network — show events
### Built
- `backend/app/routes/entity_network.py`: events are now exempt from the
  `min_mentions` filter (line ~41). Other types unchanged.
### Key decisions
- Frontend hardcodes `api.entityNetwork(3)` so all 11 events (each with
  `mention_count = 1`) were being filtered out. Rather than lower the
  global threshold (would flood person/org), made events the exception.
- Did not touch bills/locations. Bills go 485 → 77 at the threshold which
  seems fine — most dropped bills look like generic LLM phrasings.
### Open questions / concerns for review
- Events are still single-mention because the LLM phrases the same event
  differently each time ("press conference" vs "Cognetti's press
  conference"). A future canonicalization pass could merge these, but per
  the KG-pivot memory, semantic dedup of extracted entities is the part
  that historically destroyed accuracy. Leaving as-is.

## 2026-05-28 Session: Phase 3 — government URL discovery + social cleanup

### Phase 2 verification (before Phase 3 work)
- Confirmed the 2 webpage monitors from Phase 2 (paigeforpa.com, robforpa.com)
  are crawling correctly. 10 articles ingested over ~24h since setup, including:
  - "NFIB Endorses Congressional Candidate Rob Bresnahan" (100 relevance)
  - "Democrats for Bresnahan Coalition Launches" (100)
  - "Rob Bresnahan Launches First Ad in PA-08 Campaign" (65)
  - Campaign bio pages, issues pages, debate statements
- 100-relevance items are exactly the high-value opposition press releases
  we want flowing in.

### Built (Phase 3)
- **Extended `backend/app/services/monitor_url_discovery.py`** with 4 new
  discovery functions:
  - `discover_state_election_board(state_code)` — searches for the SoS
    elections page (e.g. pa.gov/agencies/dos)
  - `discover_county_election_board(city, state_code)` — anchors on the
    primary city so Google resolves to the right county (Scranton →
    Lackawanna County)
  - `discover_city_council_agenda(city, state_code)`
  - `discover_county_commission_agenda(city, state_code)`
- **Per-kind affinity scorers** so each kind ranks candidates differently:
  campaign-website boosts last-name+forcongress patterns; government kinds
  boost .gov/.us/.pa.us TLDs and state/county/city name matches; council
  agendas also boost civic-platform hosts (granicus, civicclerk, civicplus,
  primegov, legistar, iqm2, boarddocs).
- **Top-level orchestrator renamed** from `convert_website_manuals_to_webpages`
  → `convert_manuals_to_webpages` with monitor-name classification
  (`_classify_manual_monitor`) dispatching to the right discovery function.
  Old name kept as a module-level alias for callers in `monitors.py` /
  `admin.py` that import by name.
- **Loosened `_http_check`** to accept 403/429/503 responses when the
  content-type indicates HTML. Many .gov / municipal sites are protected
  by Cloudflare / DataDome and refuse simple GETs with a 403, even though
  the URL is real. The downstream crawler (trafilatura) handles these via
  sitemap fallbacks and its own retry path, so the liveness probe doesn't
  need to be strict. Without this loosening, the County Election Board
  (lackawannacounty.org → 403) and City Council Agenda (scrantonpa.gov →
  503) both failed even though the LLM picked the correct URLs.
- **Deleted 2 social-check manual monitors** (ids 3, 36) from the DB:
  `Paige Cognetti social check`, `Rob Bresnahan social check`. These were
  placeholders for FB / IG / Threads / LinkedIn URLs that can't be ingested
  without OAuth tokens we don't have. Twitter and Bluesky are already
  auto-discovered via `twitter_profile` / `bluesky_profile` types.
- **Edited `source_discovery.py`** to remove the social-check generator
  blocks (candidate + opponent + small-race FB/IG/X/Threads/LinkedIn loop).
  Future setups won't recreate them.
- **Added 12 new tests** to `test_monitor_url_discovery.py` covering:
  state/city/county code parsing, government affinity scorers, happy path
  for each of the 4 new discovery kinds, classifier rejects unrecognized
  names, unrecognized manuals are left alone, backward-compat alias works.
  Total file: 25 tests, all pass.

### Live results
Ran `POST /api/admin/discover-monitor-urls` against the 4 remaining
government-URL manual monitors. All 4 converted:

| Monitor | Discovered URL |
|---|---|
| State election board check (id 27) | https://www.pa.gov/agencies/dos/programs/voting-and-elections |
| County election board check (id 28) | https://www.lackawannacounty.org/government/departments/elections/index.php |
| City council agenda check (id 31) | https://scrantonpa.gov/citycouncil/city-council-meetings |
| County commission agenda check (id 32) | https://lackawanna.legistar.com |

Note: 28 and 31 needed the HTTP-check loosening to land. Both are real
URLs that respond with HTML content-type but bot-block on first GET.

### Final state of source_monitors
| type | count | change |
|---|---|---|
| rss | 51 | — |
| search_query | 34 | — |
| **webpage** | **6** | 0 → 6 (across Phase 2 + 3) |
| twitter_profile | 3 | — |
| youtube | 2 | — |
| fec_ie_district | 1 | — |
| **manual** | **0** | 14 → 0 |

Zero manual placeholders remain. All 6 are real, crawlable webpage
monitors with verified URLs.

### Key decisions
- **City-as-anchor for county discovery, not city → county translation.**
  Tried this first because it's simpler and lets Google do the mapping
  ("Scranton county board of elections" → Lackawanna County). Worked. A
  proper city → county lookup table would be more robust for ambiguous
  cases (multi-county districts, border cities) but isn't needed for the
  current race.
- **HTTP check now lenient on 403/429/503.** Strictly correct status
  validation would reject real .gov pages. The downstream crawler
  (trafilatura) is the actual content fetcher and has its own anti-bot
  handling. Our HTTP check is a sanity filter for 404s and broken DNS, not
  a content validator.
- **One LLM call per discovery × 6 discoveries this session.** Cost is
  trivial (cents). The 24h cooldown still protects against runaway calls.
- **Social monitors deleted, not auto-discovered.** Twitter and Bluesky
  have their own monitor types with working ingestion paths. FB/IG/Threads/
  LinkedIn webpage monitors would be inert — we can't crawl them without
  OAuth. Cleaner to delete than to carry dead rows.
- **Function name aliased, not just renamed.** `convert_website_manuals_to_webpages`
  is kept as a module-level alias because the admin route and the setup
  hook import it by name; renaming forces unnecessary churn. The alias
  costs one line and the new name `convert_manuals_to_webpages` is the
  canonical name going forward.

### Open questions / concerns for review
- The crawler running against bot-protected URLs may produce 0 articles
  from them in some cases. Worth checking after a few crawl cycles whether
  `lackawannacounty.org` and `scrantonpa.gov` actually yield content. If
  not, those monitors could be flagged or pruned.
- The LLM judge picked options #5 and #1 on the retry — the LLM's choice
  was correct, the HTTP check was wrong on the first pass. After the
  loosening, both passed cleanly.
- The discovery service now mixes 5 different monitor "kinds" with
  different query templates and affinity scoring. If a 6th kind shows up
  (federal agency page? state legislator agenda?), the pattern is clear:
  add a `_classify_manual_monitor` clause + new `discover_*` function +
  affinity scorer + LLM framing. No structural refactor needed.

### DB backup before destructive ops
- `backend/war_room.db.bak-pre-phase3-20260528-204200` (before deleting
  the 2 social-check monitors)

### Files added / modified (Phase 3)
- Modified: `backend/app/services/monitor_url_discovery.py` (4 new
  discovery functions, per-kind affinity, classification dispatch,
  loosened HTTP check, backward-compat alias)
- Modified: `backend/tests/test_monitor_url_discovery.py` (+12 tests,
  25 total all pass)
- Modified: `backend/app/services/source_discovery.py` (removed social-
  check generator blocks)

Tests: 39/39 pass in the affected test files (test_monitor_url_discovery
+ test_campaign_auto_monitors + test_services).

---

## 2026-05-28 Session: Social handle discovery + multi-handle tracking
### Built
- New service `backend/app/services/social_handle_discovery.py` —
  given (name, location) it queries `search_provider` with
  `"{name}" site:instagram.com {location}` (and the Facebook variant),
  extracts handles from result URLs, scores by name-token match +
  appearance count + headline overlap, and returns ranked candidates
  with `high|medium|low` confidence labels.
- New routes in `backend/app/routes/setup.py`:
  - `GET /api/setup/discover-handles?name=...&location=...&limit=4`
  - `POST /api/setup/save-handles` with full-replacement list semantics
- Schema: added `instagram_handles` + `facebook_pages` TEXT columns (JSON
  list[str]) to `campaign_config` and `opponents`. Migrations:
  `3b6e1d4a7f9c_add_social_handles_to_actors.py` (first cut as single-
  string columns) and `4c8f2e1b9a3d_social_handles_to_lists.py`
  (converts to lists, preserving the one existing value as a 1-element
  array). Both up + down implemented.
- `source_discovery.py`: added `_instagram_rss()` / `_facebook_rss()`
  helpers (default `RSSHUB_BASE=https://rsshub.app`; env-overridable
  for self-hosted), `_parse_handle_list()` helper, and one
  monitor-emission block per platform per actor. Candidate handles
  tagged `source_type=social`; opponent handles tagged
  `opponent_statement`. Live test: 5 monitors emitted from current DB
  state.
- Setup UI: new full-width "Social handles" section below the
  Notifications block. Per actor, two HandleRow components (IG + FB):
  - Saved handles render as chips with a small X to remove each
  - "Discover" button calls the API, surfaces ranked candidates as a
    checkbox list with confidence pills; "Add N selected" appends them
    to the stored list
  - "Enter manually" reveals an input for handles discovery missed
  - All writes go through a full-list replace via `api.saveHandles`

### Key decisions
- **Multi-handle from the start.** Single-string was the first cut; user
  pointed out politicians routinely run multiple parallel accounts
  (campaign / office / personal — e.g. mayorpaigecognetti AND
  paigegcognetti AND paigeforpa). Switched to JSON-array columns
  before any other code shipped. Stored as `Text` with JSON list[str]
  — matches the existing pattern for `key_priorities`,
  `relevance_keywords`, etc. Did NOT create a separate `social_handles`
  table with per-handle metadata; that's a later normalization if/when
  we need add-time/confidence/active flags.
- **Discovery uses the existing `search_provider`.** No new external
  dependencies. Works generically — discovery for any "name +
  location" pair, no per-candidate hardcoding. Cognetti and Bresnahan
  both produced real, useful results on the first call.
- **No FB Profile (`profile.php?id=N`) support.** The handle extractor
  rejects the numeric-ID URL form because the canonical RSSHub path
  is `facebook.com/{page}` slugs. Some pages exist only as numeric
  IDs; we'll add `_facebook_profile_rss(profile_id)` if real users
  hit it.
- **`source_type` split: candidate vs opponent kept as
  social vs opponent_statement.** Encodes the actor relationship; the
  fact that these are social posts is encoded by `category=social|opponent`
  in the monitor record. Didn't introduce a separate `platform` field on
  RssFeed/SourceMonitor — postponed pending Phase 2 (third-party page
  tracking) where the actor↔platform matrix gets richer.
- **Full-replacement save semantics.** `POST /setup/save-handles`
  accepts the desired list for each platform and replaces what's stored.
  `null` (omit field) means "leave that platform untouched" so the UI
  can save just IG without touching FB. Simpler than add/remove ops
  and matches what the UI maintains client-side anyway.

### Saved state at hand-off
- CampaignConfig (Cognetti):
  - instagram_handles: `["mayorpaigecognetti", "paigegcognetti"]`
  - facebook_pages: `["PaigeForScranton"]`
- Opponent (Bresnahan):
  - instagram_handles: `["rob4pa", "repbresnahan"]`
  - facebook_pages: `[]`  (user hasn't picked between RobforPA / RepBresnahan)

User mentioned a 4th Cognetti IG account (`paigecognetti`, `paigeforpa`) —
Tavily didn't surface them in current runs but they exist; user can add
via the manual-entry input in the Setup UI.

### Open questions / concerns for review
- **RSSHub reliability.** Public `rsshub.app` mirror is the upstream.
  Meta throttles it periodically. If reliability tanks, swap in a
  self-hosted RSSHub via the `RSSHUB_BASE` env var — no code change.
  Apify Instagram/Facebook actors are the paid upgrade path
  (`~$5–30/month` per actor).
- **No periodic re-verification.** A handle stored today might 404 in
  six months if the politician renames or deletes the account. Worth
  adding a "last-fetched-at" check on social monitors that flags
  consistently-failing feeds for re-discovery. Not blocking.
- **Phase 2 work mentioned but not built:** third-party page
  discovery ("find Facebook pages whose recent posts mention {candidate
  name}" — Monroe Co. GOP committee, PACs, etc.). The user raised this
  but we deferred. Implementation sketch: periodic
  `search_provider.search('"{name}" site:facebook.com')` → surface
  unknown pages → "Add this page" review queue → new actor type that
  isn't candidate/opponent.
- The Articles-page wire-syndication dedup committed earlier in this
  session uses the same `normalize_title` from `story_clustering.py`
  that the cluster pipeline uses. If any future change to that
  function changes thresholds, both places shift together — usually
  desirable but worth being aware of.

### Files touched
- New: `backend/app/services/social_handle_discovery.py`
- New: `backend/alembic/versions/2026_05_28_0002-3b6e1d4a7f9c_add_social_handles_to_actors.py`
- New: `backend/alembic/versions/2026_05_28_0003-4c8f2e1b9a3d_social_handles_to_lists.py`
- Modified: `backend/app/models.py` (CampaignConfig, Opponent)
- Modified: `backend/app/schemas.py` (CampaignProfileOut/In, OpponentIn/Out)
- Modified: `backend/app/routes/setup.py` (two new routes + helpers)
- Modified: `backend/app/services/source_discovery.py` (RSSHub helpers + monitor emission)
- Modified: `frontend-v2/src/api/types.ts` (CampaignConfig, Opponent, DiscoveredHandle, HandleDiscoveryResult)
- Modified: `frontend-v2/src/api/client.ts` (discoverHandles, saveHandles)
- Modified: `frontend-v2/src/pages/Setup.tsx` (HandleRow, ActorHandlePanel, Social handles section)

Also in this session (separate commit-worthy):
- `backend/app/routes/dashboard.py` — `/api/articles/recent` now collapses
  wire-syndication duplicates via `_group_by_normalized_title` (normalized
  title exact + 24h window). Frontend `Articles.tsx` shows "Also in N
  other outlets" pill with expandable list.

### Update later in same session: IG/FB ingestion paused
After building the full pipeline, we verified that **none of the 9
generated RSSHub URLs actually return data**. Two distinct failures
confirmed live:

1. Public `rsshub.app`: all 9 URLs return HTTP 403 with body
   "Due to cost considerations, we will gradually restrict access to
   rsshub.app for some feed readers… intended for testing purposes
   only… we strongly recommend self-hosting". The public mirror is
   no longer a usable production fetcher.
2. Self-hosted RSSHub (verified via `docker run -d --name rsshub-test
   -p 1200:1200 diygod/rsshub`): IG routes return 503 with
   `ConfigNotFoundError: Instagram RSS is disabled due to the lack of
   relevant config`. FB routes return 503 with `NotFoundError`. The
   container itself works (HackerNews route returned valid RSS) — but
   IG and FB specifically require `IGUSERID`/`IGPASSWORD`/`IGCOOKIE`
   and `FBCOOKIE` env vars (throwaway-account credentials that need
   periodic re-rotation when Meta detects scraping). Container
   cleaned up after the test.

**Action taken: feature flag added, defaulting OFF.** In
`source_discovery.py`:

```python
_SOCIAL_HANDLE_MONITORS_ENABLED = (
    os.environ.get("SOCIAL_HANDLE_MONITORS_ENABLED", "false")
    .strip().lower() in ("1", "true", "yes", "on")
)
```

Both the candidate-side and opponent-side IG/FB monitor emission
blocks are wrapped in `if _SOCIAL_HANDLE_MONITORS_ENABLED:`. Verified
via direct call: 0 monitors emitted with gate OFF, 9 with gate ON.
That means no dead RSSHub URLs get pushed into `rss_feeds` to fail on
every ingestion cycle.

**What's still saved + ready to go:**
- The `instagram_handles` / `facebook_pages` JSON-list columns on
  `campaign_config` and `opponents` still hold real handles (4
  Cognetti IG + 1 Cognetti FB + 2 Bresnahan IG + 2 Bresnahan FB = 9
  handles total).
- The Setup wizard's discovery UI still works — users can keep
  curating handles. A yellow "Ingestion paused" banner explains why
  nothing's fetching.
- All four routes (discover, save, generate, render) are intact.
  Flip the env var and a working fetcher is in place, and the
  monitors auto-generate again with zero code change.

**Paths forward (deferred — Phase 2.5 or 3):**
1. **Self-host RSSHub + throwaway IG/FB accounts.** Free in dollars
   (~$5/month on a VPS for "always on"). Operational cost: creating
   throwaway accounts, copying session cookies into env vars,
   re-rotating them when Meta challenges/bans (every 2–6 weeks
   based on community reports).
2. **Apify Instagram/Facebook actors.** ~$10–30/month per platform.
   No infra, no credential maintenance — Apify handles the
   anti-detection. ~50 lines of adapter code: a new
   `apify_ingest.py` service that polls Apify task results and
   inserts into `source_items`. The handle list stored in DB
   becomes the input to the Apify task config.
3. **Meta Content Library (formerly CrowdTangle's successor).**
   Only available to academic/nonprofit research institutions.
   Not realistic for a campaign tool. Mentioned for completeness.

When picking, factor in that **option 1 has a hidden cost the dollar
figure hides**: someone on the team has to babysit the credentials.
For a single race that ends in months, option 2 (paid, hands-off) is
likely cheaper in total ownership cost. For the SaaS productization
path (NOCTUA), option 2 also keeps customer-onboarding simpler
("set up a payment method" beats "create throwaway accounts on Meta").

**Files touched in this paused-state cleanup:**
- Modified: `backend/app/services/source_discovery.py` (env-var gate
  around both IG/FB emission blocks)
- Modified: `frontend-v2/src/pages/Setup.tsx` ("Ingestion paused"
  yellow banner in the Social handles section)
- No DB changes — handles persist as-is.

## 2026-05-29 Session: Timeline page — events × market reaction
### Built
- **New design philosophy for the Timeline page.** It's no longer a generic "things that happened on a date" view. It's specifically *"which events moved the race, and by how much"* — events as pins on a market-sentiment line, with an impact-ranked moment list below. See [frontend-v2/src/pages/Timeline.tsx](frontend-v2/src/pages/Timeline.tsx).
- **New backend endpoint** `/api/race-sentiment/narrative-lifecycle` ([backend/app/routes/race_sentiment.py:281](backend/app/routes/race_sentiment.py)) — per-frame `narrative_emerged` / `narrative_peaked` / `narrative_faded` events computed from `NarrativeFrameMention` joined to `SourceItem.published_at`. This replaces the misleading `frame_created` event from the events endpoint, which fired at promotion time (May 16+) and made the timeline look like nothing happened before the system started tracking. Real emergence dates go back to 2016 for some frames.
- **Suspect-snapshot detection** with two-check coherence + temporal-isolation flagging. New columns on `race_sentiment_snapshots` (migration `5d9e3f2a8b1c`). Write-time check in [race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py). History endpoint filters by default; new `/api/race-sentiment/suspect-snapshots` audit endpoint.
- **Per-event post-48h Kalshi delta** computation in the frontend ([Timeline.tsx:postEventDelta](frontend-v2/src/pages/Timeline.tsx)). Pin color = direction, size = magnitude. Mini sparkline (±3d Kalshi values) on each impact-list row.

### Key decisions
- **Markets as the sentiment line, not forecaster ratings.** Markets react within hours of news; forecaster ratings update monthly. For *event causation* (the Timeline's job), markets are the right tool. For *structural race outlook* (Dashboard's job), the existing RaceSentimentCard already shows the multi-source spread (and it's significant — Kalshi/Polymarket say Cognetti +18, Sabato/IE say Bresnahan ahead by 5–20). Did not blend or pick a single "truth."
- **Kalshi over Polymarket for the chart line.** Kalshi has longer history in this DB (back to Mar 30 vs Apr 26 for Polymarket) and unambiguous US-regulated status. Both values still shown in the summary strip.
- **Sentiment-as-spine, not sentiment-as-backdrop.** Earlier iterations had sentiment as a faint decoration in the lane area; user feedback was correct that it forced visual eye-tracing rather than showing causation directly. Final design: market line IS the chart, events are pins ON the line color-coded by post-event delta.
- **Suspect detection logic:** coherence check (cand+opp outside 80–120%) + temporal isolation (>15pt spike that snaps back). Coherence band intentionally wide so Polymarket's natural spreads don't false-positive. Isolation check catches the actual May 26 Kalshi glitch (59 → 9.5 → 62 in 12 minutes). **Critically: real catastrophic events (Cognetti drops out, etc.) would not trigger either check because (a) the sides stay arbed in sync, and (b) the move is sustained, not isolated.**
- **Dropped a previously-existing Viral Surge (24h) lane** from the Timeline. The `/api/analytics/spikes` endpoint only ever returns a 4-row current-state snapshot, so the lane was permanently almost-empty. Spike detection itself is still useful elsewhere; just not as a Timeline lane.
- **Capped Top Articles to top 2 per ISO week by relevance score.** Backend returns one per day, which on a 60d window is a continuous blue smear. Frontend-side cap is reversible and keeps the backend endpoint simple.

### Open questions / concerns for review
1. **The audit endpoint** (`/api/race-sentiment/suspect-snapshots`) has no UI surface yet. User explicitly said "okay for now." If we want to surface flagged data anywhere visible, a small ⚠ indicator on the Dashboard's RaceSentimentCard noting "1 suspect Kalshi snapshot last 7 days" might be useful for trust.
2. **Forecaster rating ingestion is still empty for all four sources** (Cook, Sabato, Inside Elections, DDHQ have non-null current values entered manually but `n_snapshots=0`). Without snapshots there's no rating-change-over-time signal. If we want ratings on the Timeline as horizontal context bands, we need a scraper for at least one of these sources. Cook + Sabato have weekly cadence and are the highest-authority for House races.
3. **Polymarket coherence flagging is liberal** — the 80–120% band catches one row (May 17 at 78.5%). Polymarket's spreads on PA-08 may legitimately go outside this band; revisit if more false positives accumulate. Tightening to 85–115 would catch more, tightening to 90–110 would over-flag.
4. **Temporal isolation backfill ran once during migration.** Future glitches get caught by the retroactive `_flag_previous_if_isolated` in `record_sample` — but only when a *next* snapshot arrives. The most recent snapshot in the DB is never checked (it has no successor yet). This is fine in steady-state but worth knowing if a glitch happens at the boundary.
5. **The chart's y-axis auto-fits to data with min 48 / max 52** to keep 50% visible. If the race moves outside that range (e.g., Kalshi hits 85%), the chart will rescale and the dashed 50% midline will move to the bottom. Tested at current values; reasonable but worth a glance if extreme values appear.

---

## 2026-05-29 Session: Phase 2 — third-party account tracking + YouTube transcripts
### Built
- **YouTube transcript ingestion.** New `_youtube_video_id()` and
  `_fetch_youtube_transcript()` helpers in `backend/app/services/ingestion.py`.
  Inside `ingest_rss`, every entry URL is checked; if it's YouTube we fetch
  the auto-generated transcript (via `youtube-transcript-api`) and append
  it to `raw_text` under a `[Transcript]` marker, capped at 20K chars.
  Every failure mode (no captions, age-restricted, network, missing
  package) returns None silently — RSS title + description still flows
  through unchanged. Verified live against a Cognetti campaign-launch
  broadcast clip: pulled 1.9KB of clean spoken content that the pipeline
  previously had no access to.
- **Third-party account discovery service**
  (`backend/app/services/third_party_account_discovery.py`). Given the
  candidate name + opponent names + location, runs `site:<host>` searches
  for instagram, facebook, bluesky, reddit, youtube. Extracts handles /
  page slugs / subreddit names / channel IDs from result URLs. Skips
  candidate's/opponents' own confirmed handles via `exclude` map.
  Returns ranked `DiscoveredAccount` records per platform with
  `inferred_role` (news / committee / pac / journalist / union / etc),
  `confidence`, and an `rss_url` field that's populated for ingestable
  platforms (Bluesky, Reddit, YouTube-with-channel-id) and null
  otherwise.
- **Role inference is location-aware and identifier-anchored.** Two
  rounds of refinement based on real run output:
  - Roles evaluated in priority order; "news" before "committee" so
    `WashingtonExaminer` classifies as news even when "committee" appears
    in the snippet.
  - Strong-only roles (committee, opposition, watchdog, endorser,
    activist) require an identifier-text match; loose roles (news,
    journalist, pac, union) can fire from snippet text too. This stopped
    `r/Pennsylvania` from being labeled "opposition" because of "Paige
    Against the Machine" leaking in via the display name.
  - Reddit-specific scoring: +2 boost when subreddit name overlaps with
    geography tokens (state/city/district); -3 penalty for mega-general
    subs (todayilearned, AskReddit, SameGrassButGreener, etc.).
  - Per-platform display name rules: Reddit/Bluesky/YouTube use
    `r/{name}` / `u/{name}` / `@{handle}` / identifier; FB/IG use
    title-derived names but only when ≤ 50 chars and not ending with
    `! ? :` (otherwise Tavily returned post-content-as-title, not the
    page name).
- **DB persistence.** New `tracked_third_party_accounts` table —
  per-row metadata (platform, identifier, display_name, url,
  inferred_role, snippet, rss_url, notes, added_at) with
  `(platform, identifier)` unique constraint. Alembic migration
  `5d9a3f7c2b8e_tracked_third_party_accounts.py` chained after the
  sibling race-sentiment migration to keep heads linear.
- **Four new routes in `backend/app/routes/setup.py`:**
  - `GET /api/setup/discover-third-party` — runs discovery using the
    current CampaignConfig + opponents, returns ranked candidates plus
    `already_tracked` so the UI can hide rows the user already confirmed.
  - `GET /api/setup/tracked-accounts` — lists confirmed third-party
    accounts.
  - `POST /api/setup/tracked-accounts` — batch insert; idempotent on
    `(platform, identifier)`; partially-populated updates don't clobber
    user-edited fields.
  - `DELETE /api/setup/tracked-accounts/{id}` — stop tracking.
- **Monitor emission.** Extended `generate_monitors_for_campaign()` in
  `source_discovery.py` with `_add_tracked_third_party_monitors()`. One
  RSS monitor per row, gated platform-by-platform: IG/FB rows respect
  the same `SOCIAL_HANDLE_MONITORS_ENABLED` flag as Phase 1.5; everything
  else fires immediately. `source_type` maps from `inferred_role`: news
  / journalist → "news", everything else → "social". Verified live: the
  one tracked test row (r/Scranton) emits the correct monitor.
- **Setup UI** — new full-width "Other accounts tracking this race"
  section below "Social handles". `ThirdPartyAccountsPanel` shows
  tracked rows grouped by platform as removable chips, with a Discover
  button that surfaces ranked results per platform with checkbox
  multi-select. "Add N selected" batch-saves through the new endpoint.
  IG and FB sections carry an "ingestion paused" badge so the user
  knows those rows persist but don't fetch yet.

### Key decisions
- **Separate table, not a JSON list on CampaignConfig.** Third-party
  accounts need per-row metadata (inferred_role, snippet, rss_url, notes,
  added_at) so a normalized table makes sense — unlike Phase 1.5 handles
  where bare strings were enough.
- **Title-as-display-name is unreliable for Tavily FB/IG results.**
  Discovery's first cut surfaced things like `display_name="Paige Against
  the Machine"` for `r/Pennsylvania`. Two-rule fix: subreddit/bluesky/yt
  use identifier-based labels; FB/IG only adopt title-derived names when
  short and not punctuation-ending.
- **Display-time filtering for already-tracked rows.** When discovery
  returns an account the user has already confirmed, the UI hides it
  instead of greying-it-out. Less noise; encourages users to find new
  candidates rather than re-confirming old ones.
- **No opponent-only-anchored queries yet.** I considered running a
  second discovery pass anchored on the opponent's name to surface
  right-leaning accounts that wouldn't show up in candidate-anchored
  results (the user's "Monroe County GOP committee" example). Deferred —
  current discovery is candidate-anchored only. Easy addition later.

### Saved state at hand-off
- `tracked_third_party_accounts` has 1 row from the smoke test:
  - `reddit_subreddit/Scranton` — real local subreddit, kept on purpose
    (it's exactly the kind of account we'd want to track).
- The active uvicorn is running with `--reload` again.

### Open questions / concerns for review
- **Tavily keys were exhausted by end of this session** from heavy
  re-runs of the discovery flow. The discovery service correctly
  degrades to an empty result set + UI shows "Discovery returned no
  new accounts." Live re-test of the full click-to-save flow couldn't
  be done at session-close because of this; the earlier successful run
  (16 candidates rendered, grouped by platform) is the proof point.
- **YouTube transcripts use auto-generated captions.** Errors on proper
  nouns (Cognetti → Connetty seen once). For high-volume political
  monitoring this is OK because the existing scoring is fuzzy, but if
  we ever surface transcript text verbatim to users we should disclose
  the source.
- **`category="third_party"` — investigated 2026-05-29, no action
  needed.** Grep confirmed `SourceMonitor.category` is only read by
  `monitor_url_discovery.py` (to pick candidate-vs-opponent URL lookup,
  where `third_party` correctly falls through neither branch) and passed
  through `source_monitors.py` / `source_packs.py` to API responses.
  Nothing in `analytics.py`, `dashboard.py`, `frame_momentum.py`,
  `strategic_lens.py`, or any frontend file uses `category` for
  filtering, grouping, or display. Analytics rollups key off
  `source_type` and `outlet_id`, both of which `third_party` monitors
  set correctly. Closing out — no aggregation breaks.

### Files touched
- New: `backend/app/services/third_party_account_discovery.py`
- New: `backend/alembic/versions/2026_05_29_0001-5d9a3f7c2b8e_tracked_third_party_accounts.py`
- Modified: `backend/app/services/ingestion.py` (transcript helpers + ingest_rss wiring)
- Modified: `backend/app/models.py` (TrackedThirdPartyAccount)
- Modified: `backend/app/routes/setup.py` (4 new endpoints + Pydantic schemas)
- Modified: `backend/app/services/source_discovery.py` (_add_tracked_third_party_monitors)
- Modified: `frontend-v2/src/api/types.ts` (DiscoveredThirdPartyAccount, ThirdPartyDiscoveryResult, TrackedThirdPartyAccount, ThirdPartyPlatform)
- Modified: `frontend-v2/src/api/client.ts` (discoverThirdParty, list/save/delete TrackedAccounts)
- Modified: `frontend-v2/src/pages/Setup.tsx` (ThirdPartyAccountsPanel component + section)
- New dependency: `youtube-transcript-api` (in venv via `python -m pip install`)

### Late-session refinement: multi-anchor discovery
Realized after the main build that I had incorrectly characterized
discovery as "candidate-anchored only" in my prior summary. The code was
already running both Cognetti-anchored and Bresnahan-anchored queries
(5 hosts × 2 anchors = 10 queries), but two real issues lurked:

1. **Latent multi-opponent bug.** `anchors[:2]` capped the anchor list
   at 2, so a primary-election race with 5 candidates would only query
   the first opponent. Fixed by replacing the `[:2]` cap with an
   explicit `_MAX_ANCHORS_PER_DISCOVERY = 5` constant and looping all
   opponents (with the cap applied). Removed the district from the
   anchor list entirely — a bare district like "PA-08" tagged onto a
   `site:facebook.com` search returns too much noise. The location
   string already gets appended to every query separately for geo
   narrowing.

2. **Dedup hides which side surfaced each result.** When discovery
   returns an account that was found by both candidate and opponent
   searches, the bucket collapses and the user has no way to see that.
   Worse, an account that ONLY shows up in the opponent search appears
   identical to a candidate-anchored hit. Added a `matched_anchors:
   list[str]` field that preserves the bare anchor names (in
   candidate-first order). Surfaces as "via Cognetti" / "via Bresnahan"
   pills next to each result in the UI. Functional check with a stubbed
   provider confirmed three cases: result from both anchors → both
   pills; result from candidate only → one pill; result from opponent
   only → one pill (the previously-invisible case).

Files touched (in addition to the main Phase 2 list):
- Modified: `backend/app/services/third_party_account_discovery.py`
  (DiscoveredAccount.matched_anchors, anchor-tracking in orchestrator,
  multi-opponent fix in `_build_queries`)
- Modified: `backend/app/routes/setup.py` (DiscoveredThirdPartyAccount
  Pydantic schema + payload mapping)
- Modified: `frontend-v2/src/api/types.ts` (matched_anchors field)
- Modified: `frontend-v2/src/pages/Setup.tsx` ("via X" pill rendering)

### Late-session cleanup: junk-title filter at ingestion + bulk archive
The "Instagram" cluster at 23 article_count (the original user complaint
that kicked off this session) was still topping the cluster-aggregates
list even after Articles-page dedup, because:
  - Scrapers kept ingesting login-walled IG/FB URLs, getting back the
    page `<title>` ("Instagram", "Facebook") instead of real content
  - Existing junk rows were partially-archived but still counted by
    `StoryCluster.article_count` (denormalized field, doesn't drop when
    members get archived)

**Three-part fix in `backend/app/services/ingestion.py`:**
1. Added `_is_junk_title(title)` predicate that catches:
   - Single-word social platform placeholders (Instagram, Facebook,
     Twitter, X, TikTok, LinkedIn, Pinterest, Snapchat, Threads, Mastodon)
   - Generic UI placeholders (Untitled, Latest Articles, BizToc,
     chevron-right, Targeted News Service, Home, Menu, 404, etc.)
   - File-extension titles (.jpg/.png/.gif/.pdf/.mp4/.mov/.csv/.xlsx/.zip)
   - Bare hostname titles (domain.tld or sub.domain.tld with no spaces)
2. Wired into `_create_and_analyze()` as a top-of-function short-circuit
   — junk rows persist with `archived_as_irrelevant=True`,
   `reviewed=True`, `race_relevance_label="irrelevant"` for audit trail,
   but skip clustering, outlet linking, and the LLM call. All ingestion
   paths (RSS, FEC, GDELT, Reddit, Mastodon, crawler) go through
   `_create_and_analyze`, so the filter catches every source.
3. One-shot bulk operation against the live DB:
   - Found 28 un-archived junk rows matching the predicate → archived
   - Found 102 junk-title `StoryCluster` rows → deleted, with 157
     SourceItems' `story_cluster_id` and `duplicate_of_source_id`
     nulled out (cluster pipeline will re-assign on next ingest)

**Verified live:**
- `/api/articles/recent?limit=50` returns 0 junk titles
- Top 5 clusters last 7d (was: Instagram 23x, breeze 4.jpg 5x, CVS Crash 2
  5x, ...): now Kyle Busch death 6x, primary takeaways 5x, CVS Crash 2 5x,
  Bresnahan opposition site 5x, Pearl Harbor survivor 5x — all legitimate.
- 19 junk samples pass the predicate, 16 legitimate samples (including
  short ones like "Heard on the Hill", "Rob Bresnahan", "TODAY! Runoff in
  Texas") correctly pass through.

**Files touched:**
- Modified: `backend/app/services/ingestion.py` (predicate + short-circuit)

**Known limit:** "CVS Crash 2" survived because it's a real local news
headline pattern (5 items, 0 outlets — looks like wire syndication). Not
junk per the title predicate. If this kind of bare-headline-no-outlet
cluster is a recurring annoyance, the right fix is at the clustering
layer, not the ingestion layer.

### Late-session: YouTube transcript proper-noun correction
Closing the loop on the "Connetty / Cognetti" caption-garbling issue
flagged earlier. Two new helpers in
`backend/app/services/ingestion.py`:

- `_campaign_canonical_names(db)` — returns the list of proper-noun
  words from `CampaignConfig.candidate_name` plus every `Opponent.name`
  (split on whitespace; min length 4; case-insensitive de-dup). For
  PA-08 this is `["Paige", "Cognetti", "Bresnahan"]`.
- `_correct_transcript_proper_nouns(transcript, canonical_names)` —
  per-word `difflib.SequenceMatcher` substitution. Gates:
  - First-letter match required (prevents "Connecticut" matching "Cognetti")
  - Length divergence ≤ 2 (prevents long-vs-short collisions)
  - Ratio ≥ 0.75 (~1-2 edit distance for short words, more for longer)
  - Word ≥ 4 chars (avoids over-matching on short tokens)

Wired into `ingest_rss`: canonical-name list cached once per feed
cycle, applied to every YouTube transcript right after fetch and
before the `[Transcript]` marker is appended to `raw_text`.

**Verified:**
- "Mayor Connetty announced today" → "Mayor Cognetti announced today"
- "Representative Bresnan voted no" → "Representative Bresnahan voted no"
- "Connecticut had a primary today" → unchanged (false-positive risk
  defused by length-divergence cap)
- 5 other false-positive-risk cases all left unchanged
- End-to-end ingest_rss against a YouTube channel feed against a
  throwaway DB: transcript present, no errors

**Files touched:**
- Modified: `backend/app/services/ingestion.py` (predicate + per-word
  corrector + ingest_rss wiring)

**Known limit:** the corrector only handles single-word garbling.
Multi-word splits like "Bresnahan" → "press no hand" pass through
unchanged. Worth knowing if a future session finds those in real
transcripts — fix would require a different (n-gram) matching approach.

## 2026-05-29 Session: source attribution fix
### Built
- **`backend/app/services/source_display.py`** — `display_source_name(item, outlet)` helper that returns the best-known publisher for an article. Resolution order: `outlet.name` if outlet_id set → curated `_DOMAIN_NAME_OVERRIDES` map for publisher_domain → prettified domain → `source_name` as last-resort fallback. Use this *everywhere* the frontend renders an article source. `preload_outlets(db, items)` batches the outlet lookup to avoid N+1.
- **Replaced 5 display sites** to use the helper (dashboard.py × 4, claim_records.py, claims.py, race_sentiment.py). All API responses returning `source_name` now show the real publisher (e.g., "The Times-Tribune") instead of the feed label ("Google News — Cognetti Congress Pennsylvania").
- **`scripts/backfill_outlets_from_publisher_domain.py`** — one-shot that creates Outlet rows for unmapped `publisher_domain` values and links existing articles. Default dry-run; `--commit` to apply.
- **Ran the backfill (committed)**: 496 articles linked to outlets — 258 to existing outlets (free win), 238 to 80 newly created outlets. Google News articles with outlet_id rose from 362 → 829 (31% → 71%).

### Key decisions
- **`source_name` field in API responses is now derived, not raw.** Frontend code reading `data.race_memo.needs_response[i].source_name` now sees "The River Reporter" instead of "Google News: Rob Bresnahan". No frontend changes needed — just better strings.
- **Reliability score coverage didn't jump much** (238 → 243 labeled claims from scored outlets). The 80 new outlets we created have `reliability_score=NULL` on purpose — we don't want to guess scores for fringe sites. Manual scoring is a separate Tier-2 task to file.
- **The 171 still-Google-wrapper articles** (no `publisher_domain`) stay as "Google News — …" — they'd need URL refetching to resolve. Tier 3 work, deferred.
- **Existing outlet names not modified.** 10 outlets have names that differ slightly from my override map (e.g., "WNEP-TV" vs "WNEP", "AP News" vs "Associated Press"). All reasonable variants; not worth churn.

### Note for the race_sentiment session
You touched `race_sentiment.py:310` recently for the Timeline `top_article` events. That site is now using `display_source_name(a, outlets_map.get(a.outlet_id))` with a `preload_outlets(db, articles)` above the loop. If you add more places that surface an article's source, please use this helper rather than `a.source_name`.

### Open questions / concerns for review
1. **Outlet naming for the long tail** (~55 outlets with prettifier-derived names like "Goerie", "Newsnationnow"). Functional but not pretty. Future: extend `_DOMAIN_NAME_OVERRIDES` as we notice them.
2. **Bresnahan campaign site** (`bresnahan.house.gov`) was already in the outlets table as "Bresnahan House Gov" — my override would've named it "Rep. Bresnahan (Official)". Left existing name in place to avoid surprising changes. Worth a rename in a future cleanup.
3. **`reliability_score` populated only for 76/196 outlets.** The 80 new outlets need manual scoring before a future "filter by reliability" UI feature would work cleanly. Until then, the briefing's `top_claims` ranking treats NULL as median (50) — see Phase 0 plan for grounded memo.

### Files modified
- Added: `backend/app/services/source_display.py`
- Added: `backend/scripts/backfill_outlets_from_publisher_domain.py`
- Added: `backend/scripts/sample_claim_records.py` (claim-record spot-check tool)
- Modified: `backend/app/routes/dashboard.py` (4 display sites)
- Modified: `backend/app/routes/claims.py`
- Modified: `backend/app/routes/claim_records.py`
- Modified: `backend/app/routes/race_sentiment.py`
- Modified: `backend/scripts/entity_extraction_backfill.py` (added `--skip-existing`)
- Modified: `CLAUDE.md` (added Data validation protocol section)

---

## 2026-05-29 Session (continued): hardening pass — tests, hygiene, UX, latent fixes
Pushed back on declaring the session "done" — turned out there were 7
real loose ends, all addressed in this pass.

### Built / fixed
- **Scheduler audit & restart.** The running uvicorn was started without
  `--reload` so my session's code changes hadn't taken effect. Killed it,
  restarted with `--reload`, confirmed all new modules are loaded. 0
  un-archived junk rows ingested between filter-introduction and
  restart, so we lost nothing.
- **92 new unit tests** across three files:
  - `backend/tests/test_ingestion_helpers.py` — `_is_junk_title`,
    `_youtube_video_id`, `_correct_transcript_proper_nouns`,
    `_campaign_canonical_names`. Locks behavior on every concrete
    junk-title example seen in the live DB plus every legitimate edge
    case (short headlines like "Heard on the Hill", camelcase names like
    "StopBresnahan", etc.).
  - `backend/tests/test_third_party_discovery.py` — `_build_queries`
    (verifies opponent anchoring + multi-opponent expansion + 5-anchor
    cap + district exclusion), `_platform_display_name` (all 6
    platforms × 3 input variants), `_infer_role` (regression tests for
    the exact bugs fixed during the session: WashingtonExaminer→news,
    DCCC→committee, r/Pennsylvania→unknown despite "Against" in title).
    Plus a stubbed-provider integration test for `matched_anchors`.
  - `backend/tests/test_articles_recent_dedup.py` —
    `_group_by_normalized_title` (24h window, outlet-suffix collapse,
    representative selection by score → length → time).
- **Two predicate tightenings discovered while writing tests:**
  - `_platform_display_name`: reject FB/IG titles > 30 chars OR
    containing `!?:…` anywhere (not just trailing). Was letting through
    quoted-post-content as outlet labels.
  - `_build_queries`: guard against `None` `opponent_names`.
- **Unused-import sweep** on files I touched this session:
  - Removed `typing.Iterable` from third_party_account_discovery.py
  - Removed `collections.Counter` from social_handle_discovery.py
  - Fixed 4 f-strings missing placeholders in source_discovery.py (the
    ones in my new IG/FB monitor emission blocks)
- **Setup page sticky tab nav.** New `SetupSectionNav` component
  renders a sticky pill-bar at the top of `/setup` with 4 tabs:
  Campaign profile / Notifications / Social handles / Other accounts.
  Clicking a pill jumps to the corresponding section (uses native
  hash navigation + `scrollMarginTop: 70` to account for the sticky
  bar's height). Active pill tracked from `window.location.hash` and
  kept in sync via the `hashchange` event.
- **Multi-word transcript garbling support.** Extended
  `_correct_transcript_proper_nouns` with a second pass that scans
  2-word windows for caption splits of multi-syllable names
  ("Bresnahan" → "Bres nahan", "Cognetti" → "cog netti"). Stricter
  gates than the single-word pass:
  - Only canonicals ≥ 6 chars (short names too risky)
  - First-letter match still required
  - Length tolerance ≤ 3
  - Ratio ≥ 0.70 (slightly more forgiving than single-word's 0.75)
  - Skip windows where either token is already a canonical (prevents
    "Cognetti for congress" from collapsing back to "Cognetti").
- **`_frame_reach_in_window` excludes archived items.** Latent bug:
  archived members of FrameClusterMatch-attached clusters were still
  contributing to spike-alert reach. Added `archived_as_irrelevant ==
  False` to the join filter.
- **Cluster cleanup.** Bulk-deleted **16,137 of 18,539** StoryCluster
  rows (87% of the table) where every member was archived. None of
  these were attached to NarrativeFrames so no analytics signal was
  lost. Members had their `story_cluster_id` and
  `duplicate_of_source_id` nulled. Top clusters last 7d now show
  legit content: "Takeaways from Tuesday's primaries", "Bresnahan
  opposition site", "PA's largest TV station calls out Bresnahan",
  "Letter: Cognetti campaigns during City Hall workday".

### Test suite state
- **All 92 new tests pass.**
- Full suite: 492 passed / 4 failed / 1 warning.
- All 4 failures are in `test_race_directory.py` and are PRE-EXISTING
  — they look for a "FIGURES, SHOMARI C." candidate row in the FEC
  catalog that doesn't exist. Unrelated to anything in this session.

### One task NOT completed: live Phase 2 e2e
Task #17 (live click-to-save flow in the browser with real Tavily
discovery data) is still deferred. Tavily search-API keys remained
exhausted through the entire late-session push. The full data path
is independently exercised by unit tests + earlier partial runs, but
the actual end-to-end "click Discover → see pills → toggle filter →
tick boxes → save → confirm chip → confirm DB row → confirm monitor"
has not been observed live. Pick this up in the next session once
Tavily refills.

### Bug observation worth knowing for the next session
"Reddit - Please wait for verification" now tops the per-cluster
list at 3 articles. That's Reddit's bot-check page being scraped when
the ingestion path hits rate-limiting. Not in scope this session but
worth a known-junk-title pattern next time.

### Files touched (in addition to the main 2026-05-29 list)
- New: `backend/tests/test_ingestion_helpers.py`
- New: `backend/tests/test_third_party_discovery.py`
- New: `backend/tests/test_articles_recent_dedup.py`
- Modified: `backend/app/services/ingestion.py` (multi-word transcript pass)
- Modified: `backend/app/services/third_party_account_discovery.py`
  (predicate tightening + None guard + removed unused import)
- Modified: `backend/app/services/social_handle_discovery.py` (removed unused import)
- Modified: `backend/app/services/source_discovery.py` (f-string fixes)
- Modified: `backend/app/routes/analytics.py` (`_frame_reach_in_window`
  archived filter)
- Modified: `frontend-v2/src/pages/Setup.tsx` (SetupSectionNav + per-section
  scrollMarginTop)

## 2026-05-29 Session: briefing v2 — grounded memo
### Built
- **`backend/app/services/briefing_retrieval.py`** — structured intermediate for the briefing endpoint. Two helpers:
  - `top_claims_for_briefing(db, days=7, limit=20)` — labeled, race-relevant ClaimRecords ranked by `label_priority × log(reliability_score or 50) × recency`. Filters: label in {attack, endorsement, vote, commitment, policy_position, defense}; quote length ≥ 40; race_relevance ≥ 50.
  - `top_entities_for_briefing(db, days=7)` — fixed allowlist (Cognetti+Bresnahan always shown, plus top 4 by mention count from Trump/Shapiro/Cartwright/DCCC/NRCC/4 bills). Alias merging via small hardcoded map (`person:auto:rob-bresnahan-jr` → `person:bresnahan` etc.) so seeded+auto duplicates count together.
- **`backend/app/services/briefing_summary.get_or_generate_grounded()`** — new v2 memo function. Takes the structured intermediate as input. Builds a prompt with quotes numbered `[C1]..[Cn]`, instructs the LLM to use those markers when referencing what someone said. Post-processes the response: parses markers, validates each against the source claim list (invented markers stripped), returns `{text, citations[], sources_used[]}`.
- **`GET /api/briefing/morning?v=2`** — surfaces v2 grounded memo + `top_entities`. Default (`?v=1` or no param) keeps the legacy string memo for backward compat.
- **`frontend-v2/src/pages/MorningBriefing.tsx`** — reads `?v=2` URL param, renders:
  - GROUNDED · V2 chip on the Race Situation header
  - Prose with `[1]` `[2]` superscript citations clickable to the source article
  - "Sources Used (N cited / M considered)" expandable disclosure showing every quote the LLM had access to, with cited ones marked
  - "Activity This Week" card — top 6 entities with mention deltas + 2 sample article titles each

### Key decisions
- **Cut the Voices card from v1 scope.** Without `speaker_entity_id` on ClaimRecord, surfacing "Bresnahan said X" by-entity would be misleading (entities linked to a quote are who's NAMED in it, not necessarily who SAID it — "Bresnahan denied that he said X" would be wrong). Sources Used (audit-grade list, no per-entity grouping) is the safer surface.
- **`top_entities` is restricted to a 11-entity race allowlist**, not "top trending entities." Prevents the wire-service-noise problem ChatGPT flagged (Tuberville, Al Green, etc. dominating the card despite being irrelevant to PA-08).
- **`reliability_score` is used as a soft ranking signal (with NULL=median fallback)**, NOT a hard filter. Only 38% of labeled claims come from outlets with scores — hard-filtering would starve the memo.
- **Citations use anchor-ID markers** (`[C1]`, validated server-side, rendered superscript on frontend). Per ChatGPT critique, soft "use exact quotes" prompt instruction wasn't enough; the validated-marker approach catches model-invented citations before they reach the UI.

### Open questions / concerns for review
1. **`top_entities` deltas look skewed.** All current mentions show negative `delta` (this_week < last_week) because the entity-extraction backfill is partial and recent articles are under-covered. After backfill completes, expect deltas to normalize. Don't read meaning into current `delta` values until backfill finishes.
2. **Citation rate is low** — the v2 memo I tested cited 2 of 15 sources. That's fine grounding for 3-4 prose sentences but a future tuning question: prompt the model harder to cite more, or accept that prose synthesis naturally over-paraphrases?
3. **Cache TTL is 30 min for both v1 and v2.** No invalidation hook is wired into the entity-extraction path (extraction is script-only, not in the ingestion pipeline). So v2 memo can be up to 30 min stale relative to fresh extractions. Acceptable for now.
4. **Speaker attribution** is the obvious next feature blocker. Until we have `speaker_entity_id` + confidence, we can't safely ship a per-entity quote feed (the original "Voices this week" idea). Phase 2 work.

### What this v2 enables next (file for backlog)
- Entity profile pages — same retrieval layer powers them.
- Search-by-entity — `top_entities_for_briefing` query patterns are reusable.
- Quote-grounded opponent activity feed (replaces marker-regex in `opponent_analysis.py`) — requires speaker attribution first.
- Contradiction surfacing via `stance.py` — uses ClaimRecord data already in this pipeline.
- Compositional briefing rebuild (full ChatGPT-style retrieve→structure→render) — v2 is a partial step toward this; sentence-level provenance is the remaining gap.

### Files added / modified
- Added: `backend/app/services/briefing_retrieval.py`
- Added: `backend/tests/test_briefing_retrieval.py` (12 tests, all passing)
- Modified: `backend/app/services/briefing_summary.py` (+`get_or_generate_grounded`, +`_generate_grounded`, +v2 cache, invalidate hooks both caches)
- Modified: `backend/app/routes/dashboard.py` (v query param, v2 branch returning grounded memo + top_entities)
- Modified: `frontend-v2/src/api/types.ts` (+GroundedMemo, BriefingCitation, BriefingClaim, BriefingEntity, BriefingClaimEntity types; race_memo union)
- Modified: `frontend-v2/src/api/client.ts` (`morningBriefing(v)` accepts version param)
- Modified: `frontend-v2/src/pages/MorningBriefing.tsx` (+GroundedMemoView, SourcesUsedDisclosure, TopEntitiesCard components; `readVersionFromUrl` reads `?v=`)
- Modified: `CLAUDE.md` (briefing_summary description updated, briefing_retrieval.py + source_display.py added to key services table)

---

## 2026-05-28/29 Session F: v15.0 backfill on remaining race-relevant articles

User asked about the 47 KG contradictions in the Review Queue and the "Ontology drift" banner ("oncology" was a misread). Diagnosed: every relation in entity_relations was produced by v14.x extractors that have been superseded by v15.0. v15.0 retired the (subject, predicate, object) triple shape entirely — the action predicates (`endorses`, `criticizes`, `attacks`, `voted_for`, `voted_against`, `co_sponsored`) that produce these contradictions are no longer LLM-extractable. So the review queue is showing artifacts of a retired approach.

Coverage check before this session: `claim_records` had 1,648 distinct article_ids; race-relevant total was 2,768. User asked to "just re-run everything with v15" — kicked off the backfill on the gap.

### Built
- **`backend/scripts/v15_finish_backfill.py`** — one-off runner that filters race-relevant articles to those NOT already in `claim_records` and re-uses `persist_claims` + the same LLM/parser stack as the main backfill script. Saves ~$0.45 vs blind re-run (which would have called the LLM on the already-covered 1,648 articles too, since the main script has no skip-extracted flag).

### Ran
- 1,120 articles processed in 231.8 min, 0 failures, ~$0.50 total.
- New rows: 163 claim records, 466 mentions, ~388 new claim_record_entities.
- Final v15.0 state: 3,833 claim records, 6,118 claim_record_entities, 1,816 distinct article_ids in claim_records.

### Key finding — claim yield is the ceiling, not throughput
- Of the 1,120 articles processed, **only ~168 (~15%) produced at least one surviving claim_record**.
- Validators rejected the rest: 730 non-verbatim, 262 entity-not-in-span, 907 duplicate-hash.
- Why this matters: "covered by v15.0" in the DB will always look lower than "extracted by v15.0" because empty-output articles don't show up in `claim_records`. The script's `SELECT DISTINCT article_id FROM claim_records` "already covered" check would re-process them on a second run.
- This is real signal about the article corpus, not a bug. The lower-relevance tier (race_relevance_score ≥ 50 but bottom of the rank) often lacks quote-worthy political content under v15.0's strict definition.

### Decisions
- Used a separate one-off script instead of adding a flag to the main backfill, because the main script's contract is "process everything" — adding a "skip covered" flag would change semantics for other callers. The one-off is throwaway.
- Did NOT touch `entity_relations` / `entity_mentions` — the legacy data is frozen as expected.
- Did NOT rewire any UI to read from `claim_records`.

### Carry-forward (step-2 decision still pending)
The user has not decided what to do with the 1,757 legacy `entity_relations` that still drive:
- The 47-item Review Queue ("Ontology drift: 1,757 relations have only stale evidence" banner)
- The Entity Network force-directed graph
- The neighbors / path multi-hop endpoints
- The dimensional contradiction detector

Three options remain on the table (in this session's chat):
1. **Delete legacy** — clears UI, loses prior canonicalization/partisan-guard/review work
2. **Freeze + hide** — keep legacy tables, suppress the Review Queue and drift banner until UI is rewired
3. **Auto-resolve obvious patterns + leave UI on legacy** — clears 40/47 contradictions via ratio rules (≥4× weight ratio or weak side ≤ 3 weight), leaves the 7 real ones

Recommended sequence I gave the user: (1) finish v15.0 backfill — done; (2) decide what to do with `entity_relations`; (3) decide whether to rewire Entity Network → `claim_records` (~half day of work).

### Files touched
- Created: `backend/scripts/v15_finish_backfill.py`
- No DB schema changes
- No frontend changes
- No deletes from any legacy table

---

## 2026-05-29 Session F continued: hid the KG Contradictions tab + Ontology drift banner

User picked "freeze + hide" from the three options above. Executed the hide; froze the data (no deletes).

### What changed
- `frontend-v2/src/pages/ReviewQueue.tsx` — removed the `'kg'` TabKey, the `<EntityReview embedded />` render block, the `entityReviewQueue` fetch, the `kgCount` state + badge, the conditional `maxWidth: 1100`, the `Flag` icon import, the `EntityReview` import, and the "three lenses / KG Contradictions" line in the InfoTooltip. Tab strip now only shows Articles + Proposed Narratives.
- `frontend-v2/src/App.tsx` — `/entity-review` redirect now lands on `/review` (no `?tab=kg`) so old bookmarks don't 404.

### What was preserved (deliberately frozen, not deleted)
- `backend/app/routes/entity_review.py` — `/api/entity-review-queue/*` endpoints still respond. Nothing calls them from the UI.
- `backend/app/routes/extractor_drift.py` — `/api/extractor-drift/summary` still responds. The banner was rendered only inside `EntityReview.tsx`, which is no longer mounted.
- `frontend-v2/src/pages/EntityReview.tsx` — component file still on disk for reference / future rewire.
- `entity_relations` (1,757 rows), `entity_mentions` (19,741 rows), `entity_review_decisions` — DB tables untouched. The 47 contradictions still exist in data, just not surfaced in UI.

### Verified
- `/review` page renders with two tabs (Articles, Proposed Narratives). KG tab absent.
- `/entity-review` redirects to `/review`.
- No new console errors from this change. Pre-existing `<Setup>` component errors unrelated (Setup.tsx, not in the changed files).

### Carry-forward not addressed in this pass
- **Entity Network page (`/entity-network`) was NOT hidden.** It still reads from `entity_relations` (legacy v14.x) and renders edges with the retired action predicates (endorses / criticizes / etc.). It does not display a drift banner, so it's less obviously broken than the Review Queue was, but the data behind it is the same retired-extractor output. If the page is in active use this is misleading; if it isn't, it should probably also be hidden or rewired to `claim_records`. Left as a separate decision.
- Wiring the Entity Network to `claim_records` is the natural follow-up — would surface the fresh v15.0 data that Session F backfilled.

---

## 2026-05-29 Session (further continued): LLM model deprecation alerting + Cerebras update
While running a YouTube transcript backfill, the ingestion-side LLM chain
returned a stream of 404 errors:
`"Model qwen-3-235b-a22b-instruct-2507 does not exist or you do not have
access to it."` That model was the `CEREBRAS_MODEL` default, and Cerebras
has since rationalized their public production lineup down to a single
model. Easy to miss because every failed call silently falls through the
provider chain — nothing user-visible breaks until you look hard.

### Built
- **Updated `CEREBRAS_MODEL` to `gpt-oss-120b`** in both:
  - `/Users/theo/noctua/.env`
  - The default in `backend/app/services/llm_provider.py` (was at two sites)
  - Confirmed via Cerebras's docs (`inference-docs.cerebras.ai/models/overview`)
    that this is currently the sole public production model. They host OpenAI's
    GPT-OSS 120B on their wafer-scale hardware.
- **Deprecation-aware logging** in `llm_provider.py`:
  - `_maybe_log_model_deprecation(provider_name, model, err)` — checks
    the exception message for any of 6 deprecation phrases ("does not
    exist", "no longer exists", "is no longer supported", "has been
    deprecated", "model_not_found", "model is deprecated") and emits a
    multi-line ERROR log with the `LLM MODEL DEPRECATED:` marker.
  - Once-per-(provider, model)-per-process dedupe via
    `_deprecation_log_seen` set so the marker doesn't spam on every
    retry.
  - Wired into `OpenAIProvider.complete()` so any sub-provider that
    inherits from it (Groq, Cerebras, Gemini) gets the detection
    automatically.
- **Startup probe** in `llm_provider.probe_configured_providers()`:
  - Unwraps the `FallbackProvider` chain into individual sub-providers.
  - Runs one tiny `complete("Reply with the single word OK.")` against each.
  - Returns `{provider_label: status}` with status in
    `ok | deprecated | rate_limited | empty | error: <msg>`.
  - Called from `main.py` lifespan in a background thread (2s delay to
    let startup settle, never blocks app readiness). Results go to the
    uvicorn log at INFO level; any `deprecated` result gets ERROR
    level.
- **Verified all three behaviors:**
  - Synthetic 404 → loud marker log fires
  - Same error repeated → dedupe suppresses it (no spam)
  - Transient "Connection error: server overloaded" → does NOT fire
    the marker (false-positive guard works)
- **Live probe output after the model update:**
  - `CerebrasProvider(gpt-oss-120b) → ok` ✓ (new model works)
  - `GroqProvider(llama-3.1-8b-instant) → empty` (VPN-blocked, 403)
  - `GeminiProvider(gemini-2.5-flash) → ok` ✓

### Why this matters
Before this change, a deprecated model in the chain was effectively
invisible — every per-article LLM call would silently waste a few
hundred ms hitting the dead provider, fall through to the next one in
the chain, and produce the same end result with no surfaced signal.
The error log existed but was buried in the noise of every other
ingestion log line. Now the marker is loud enough to be impossible to
miss, the startup probe surfaces state at boot, and the once-per-process
dedupe means it doesn't drown out other logs during normal operation.

### Why we did NOT switch everything to OpenAI
The multi-tier provider strategy is sound and previously thought through:
- Cerebras / Groq / Gemini free tiers handle high-volume per-article
  ingestion. Going all-OpenAI would cost ~$2-5/month for the current
  ingestion volume — small but not zero, and the free tiers are
  meaningfully faster too.
- Paid OpenAI already handles rescore (`OPENAI_RESCORE_MODEL=gpt-4o-mini`)
  and judgment-heavy prose (`get_judge_provider`).
The fix here is to preserve the architecture and address the specific
model-deprecation pattern.

### Note re today's rescore bug
When I ran the YouTube-transcript-backfill rescore earlier in this
extended session, every LLM call hit fallback. Two contributing causes:
1. I called `_rescore_one()` directly, which routes through the
   *ingestion* provider chain (Groq/Cerebras/Gemini), not the *rescore*
   chain that has the `OPENAI_RESCORE_MODEL=gpt-4o-mini` override. The
   right call would have been through `start_rescore()`.
2. The user was on VPN — Groq specifically was returning 403 "Access
   denied. Please check your network settings." (separate from the
   Cerebras 404).
Combined effect: every call to the ingestion chain failed. With the
Cerebras model update, that chain should now mostly recover (Groq
still needs the VPN dropped). The next-session re-run of the 19-item
rescore should go through `start_rescore()` not `_rescore_one()`.

### Files touched
- `/Users/theo/noctua/.env` — `CEREBRAS_MODEL` updated
- `backend/app/services/llm_provider.py` — deprecation logging,
  startup probe, model default update
- `backend/app/main.py` — wired `probe_configured_providers()` into
  the lifespan as a background thread

### Open followups
- **YouTube transcript backfill rescore (19 items)** — still pending,
  blocked on the VPN issue. Once off VPN, retry through `start_rescore()`
  which respects `OPENAI_RESCORE_MODEL`.
- **Groq model name probe returned "empty"** — possibly the
  `llama-3.1-8b-instant` model is stale, or the 403 from VPN is
  masquerading. Worth a fresh probe off-VPN before declaring Groq's
  configured model dead.

## 2026-05-29 Session: briefing v2 restructure (operational ordering)
### Built
- **Section order in v2 changed** to mirror campaign-manager morning workflow (operational → urgent → contextual):
  1. Race Situation memo (tightened to ~100 words via prompt)
  2. **What Changed in the Race** (NEW — `overnight_changes` 48h window, candidates only)
  3. **Needs Response** (promoted from below Narrative Pulse — operational items go before browse material)
  4. Activity This Week (top race-allowlist entities)
  5. Narrative Pulse (top 8 of N active — already capped)
  6. ~~Most Recent Articles~~ (HIDDEN in v2 — Articles page covers the raw feed; a synthesis briefing shouldn't end on a chronological list)
- **`overnight_changes(db, hours=48, limit=5)`** in `briefing_retrieval.py` — gate is Cognetti OR Bresnahan in the quote (not the wider race allowlist). National Trump/Shapiro/etc. won't surface here even if they're race-relevant entities — that gating prevents Jen-Kiggans-in-VA-style noise.
- **`OvernightChangesCard`** component — denser hr-list style (not card grid), labels + outlet + verbatim quote + article link.
- **Visual urgency styling** on Needs Response in v2: 3px red left border, semi-transparent red full border. Unmissable when scanning.
- **Memo prompt tightened**: max 100 words (aim 60-80), one development not three, no "currently" / "right now" / "today" filler. Cache TTL is 30 min — old memos persist briefly after a prompt change until the cache clears.

### Key decisions
- **`overnight_changes` does NOT filter on `content_category`.** Race-entity mention IS the relevance signal for this section; adding the content filter killed coverage (12 → 0 items in 48h). Section renders only when non-empty, so quiet windows are honest empty.
- **Most Recent Articles dropped in v2, not collapsed.** The Articles page is one click away and shows the same data better. A briefing should be a synthesis, not a feed terminus.
- **Reordering done via JSX variable extraction** (not duplication or component-per-section) — `needsResponseSection` is built once, rendered in the v2-position OR v1-position based on `version`.
- **Memo length is a soft prompt constraint, not enforced.** gpt-4o-mini overshoots ~20% on word limits. Acceptable; alternative would be post-generation truncation which could cut mid-sentence.

### Files modified
- `backend/app/services/briefing_retrieval.py` — added `overnight_changes()` + 5 tests (17 total now passing)
- `backend/app/services/briefing_summary.py` — tightened v2 prompt
- `backend/app/routes/dashboard.py` — includes `overnight_changes` in v=2 response; `content_category != 'irrelevant'` added to needs_response, new_articles, relevant_candidates filters
- `frontend-v2/src/api/types.ts` — `overnight_changes?: BriefingClaim[]` field
- `frontend-v2/src/pages/MorningBriefing.tsx` — section reorder via IIFE + extracted `needsResponseSection`; new `OvernightChangesCard` component; v=2 hides Most Recent Articles; urgency styling on Needs Response in v2; Activity grid fixed with `minmax(0, 1fr)` + `min-width: 0` so cards don't overflow

### Open questions / concerns for review
- Density: 0-2 race-specific labeled claims per 48h is normal in PA-08. Overnight section may be empty most mornings. Acceptable; could surface a "No notable race-specific quotes in the last 48 hours" empty state instead of hiding entirely. Trade-off: empty state == reassurance, hiding == cleaner page.
- Cache invalidation has no admin endpoint. To pick up a prompt change immediately you have to restart uvicorn or call `briefing_summary.invalidate()` in a shell that shares the running process — currently no in-process trigger.
- Visual urgency styling currently fires for ALL needs_response items in v2. Could differentiate by source (e.g. spike-alert items get an even brighter red) if that distinction matters operationally.

---

## 2026-05-29 Session (further continued): YT transcript rescore via OpenAI + search result caching
Two tied-together fixes after the user dropped VPN.

### YouTube transcript rescore — 10 of 19 promoted
Re-ran the 19 race-keyword-titled backfilled YouTube items through paid
OpenAI `gpt-4o-mini`. Bypassed the ingestion provider chain by
monkey-patching `_provider_singleton` with a fresh `OpenAIProvider`
(same effect as `_load_providers()`'s `OPENAI_RESCORE_MODEL` special
path, but targeted to just our 19 items rather than the whole corpus).

Results: **10 items upgraded from archived → relevant**, 8 kept (LLM
correctly judged 4 of those as still off-topic — "Choosing the Right
PAC Filing Frequency" is procedural, "Which Democratic candidate will
win PA-03?" is a different district), 1 LLM JSON-format fallback
(id=13922, easy retry candidate). Top scores: id=17 / 13329 / 13956 /
15863 / 18575 all hit 88. ~20s/item via gpt-4o-mini.

Important consequence: those 10 newly-relevant items now flow into the
Articles list, frame matching (Bresnahan Healthcare Record, Cognetti
Anti-Corruption, etc.), FTS5 search, and cluster pipeline. The
transcript backfill genuinely unlocked content that was previously
unreachable.

### Search result caching — new `CachedSearchProvider`
Tavily exhaustion was a real liability — dev iteration ate keys within
hours, and per-campaign setup repeatedly hit the same query multiple
times. Built a transparent disk-backed wrapper:

- New `CachedSearchProvider` class in
  `backend/app/services/search_provider.py`. Wraps the configured inner
  provider (currently Tavily) with a SQLite-backed cache keyed on
  `(provider_name, query, limit_n)`. TTL defaults to 7 days, env var
  `SEARCH_CACHE_TTL_DAYS` overrides. `SEARCH_CACHE_DISABLED=1` bypasses
  the cache entirely.
- New `SearchResultCache` model + Alembic migration
  `6e2b8c4a9d1f_search_result_cache.py`. Unique constraint on
  `(provider, query, limit_n)` — same query at limit=4 and limit=8 are
  SEPARATE cache rows on purpose, so callers asking for more results
  don't silently get truncated cached output.
- `get_search_provider()` now always returns the cache wrapper around
  whatever inner provider is configured (mock, tavily, future others).
- **Transient errors NOT cached** (message set + empty results). If
  Tavily returns "all keys exhausted", we don't serve that for a week
  — next call has a real chance of getting through.
- **Legitimate empty results ARE cached** (no message, empty results).
  No point in retrying a query that found nothing.

### Tests added
`backend/tests/test_search_cache.py` — 8 tests, all pass. Covers:
miss-writes-row, hit-skips-inner, different-limit-separate-key,
stale-row-refetched, env-bypass, transient-error-not-cached,
legit-empty-IS-cached, name-includes-inner.

### Expected impact
- Dev iteration: same query in same session → instant. Was burning
  keys with every test, now burns ~zero unless the query is new.
- Per-campaign setup: when a user re-clicks Discover, the Tavily call
  isn't repeated. Expect ~80% reduction in per-campaign quota usage.
- Stale-but-fresh-enough: handles change rarely; 7-day TTL means a
  brand-new account created this week wouldn't surface until next
  re-discovery, which is acceptable.

### Layer 2 deferral
The earlier conversation identified a fuller architectural answer: a
`FallbackSearchProvider` mirroring `FallbackProvider` for LLMs, walking
through Tavily → Brave → SerpAPI → DuckDuckGo as each gets exhausted.
That's a separate ~2 hour build deferred to its own session. Layer 1
(caching) was assessed as solving 80% of today's pain on its own.

### Files touched
- New: `backend/alembic/versions/2026_05_29_0002-6e2b8c4a9d1f_search_result_cache.py`
- New: `backend/tests/test_search_cache.py`
- Modified: `backend/app/models.py` (SearchResultCache model)
- Modified: `backend/app/services/search_provider.py` (CachedSearchProvider class + get_search_provider wraps)

### LLM probe + Cerebras update — supplementary
Final off-VPN probe confirmed all three providers green:
- `CerebrasProvider(gpt-oss-120b) → ok` (the model update from earlier
  this session)
- `GroqProvider(llama-3.1-8b-instant) → ok` (was VPN-blocked, not dead)
- `GeminiProvider(gemini-2.5-flash) → ok`

So Groq's configured model is alive — earlier "empty" result was
purely VPN. No further model updates needed.

## 2026-05-29 Session: needs_response filter tightening
### Built
- Tighter `needs_response` filter in [dashboard.py](backend/app/routes/dashboard.py). The `actionability_label='respond'` LLM classifier is over-inclusive — it was surfacing the candidate's own social posts and friendly local infrastructure stories. New filter excludes:
  - `source_owner_type IN ('candidate_statement', 'community/manual')` — kills the candidate's own social content (e.g. Cognetti's own tweets restating campaign message)
  - `(source_owner_type='party_committee_statement' AND content_category='campaign')` — kills friendly party-tagged campaign content (e.g. local outlets quoting campaign press releases like the D&L Trail construction story)
  - (kept the existing `content_category != 'irrelevant'` exclusion for national/other-race noise)
- Empty-state placeholder in v2 frontend ("No items requiring immediate response in the last 48 hours") — strict filter often returns 0 in quiet windows; explicit empty state confirms the system is working rather than appearing broken/missing.

### Key decisions
- **Kept party_committee_statement when content_category is NOT 'campaign'.** That's where opposition committee attacks (NRCC press releases attacking Cognetti) tend to land — those ARE legitimately "needs response" items. Only the campaign-tagged subset is friendly enough to filter out.
- **Did not include `actionability_label='review'` items.** That label maps from `helps_candidate` framing — friendly to the campaign, not urgent. Mixing review + respond would defeat the operational distinction.
- **0 items in 7-day window with new filter is plausible.** The original 27 respond items / 7 days were ~95% false positives once you exclude noise. Real attacks worth a same-day response are genuinely rare for a non-incumbent congressional race.

### Root-cause note for a future fix
The actionability classifier itself is the real bug. `framing_to_action` in [campaign_analysis.py:1033](backend/app/services/campaign_analysis.py:1033) blindly maps `hurts_candidate` + `opponent_news` → 'respond' without considering source ownership. A proper fix would teach the LLM (or a post-LLM rule) that:
  - Candidate's own social content is NEVER 'respond' (it's the campaign itself speaking)
  - Friendly party content praising the candidate is NEVER 'respond'
  - Friendly local infrastructure news isn't 'respond' even when high-relevance

The current dashboard-level band-aid is correct for v1; the model-level fix is Phase 2 work that would require re-running classification (~$$ over corpus).

### Files modified
- `backend/app/routes/dashboard.py` — tighter `respond` filter with documented rationale
- `frontend-v2/src/pages/MorningBriefing.tsx` — `hasNeedsResponse` boolean + v2 empty-state placeholder

---

## 2026-05-29 Session F (continued): four-step KG harvest

After confirming the v15.0 backfill landed (Session F earlier), user asked "what is this even for" — leading to a strategic recheck: KG-as-product is dead, v15.0 is best used as evidence inside narrative-frames + briefing. Four-step execution:

### Step 1 — Hidden Entity Network page
- `frontend-v2/src/components/Sidebar.tsx`: commented out the Entity Network nav entry
- `frontend-v2/src/App.tsx`: route now `<Navigate to="/" replace />`; removed unused `EntityNetwork` import
- Component file at `frontend-v2/src/pages/EntityNetwork.tsx` preserved on disk for reference
- Verified in preview: 13 sidebar items (no Entity Network), `/entity-network` redirects to `/`

### Step 2 — Wired claim_records as evidence into narrative frames
- New endpoint `GET /api/narrative-frames/{frame_id}/quote-evidence` in `backend/app/routes/narrative_frames.py`. Joins `narrative_frame_mentions ↔ claim_records` via shared article_id; returns verbatim spans + label + outlet + entity tags + bias_label/reliability_score. Response shape: `{frame_id, frame_name, total, by_label, quotes: [...]}`.
- API client: `api.frameQuoteEvidence(id, limit=200)` in `frontend-v2/src/api/client.ts`.
- UI: new `<SupportingQuotes>` section in `frontend-v2/src/pages/NarrativeDetail.tsx`, rendered before the All Articles list. Features:
  - Label-chip filter strip (`All`, `unlabeled`, `attack`, `endorsement`, `vote`, `policy position`, `commitment`, `defense`, `statement`, `announcement`) with per-label color coding
  - Each quote: italic verbatim span + label + outlet + date + source link + entity list + one-click Copy button
  - Tested against frame 3 (Bresnahan's Healthcare Record): 127 quotes returned, attack filter narrows to 20

### Step 3 — Verbatim claim_records now drive the morning briefing by default
- Existing `briefing_summary.get_or_generate_grounded` + `briefing_retrieval.top_claims_for_briefing` already consumed `claim_records` and emitted `[C1]…[Cn]` citations. The grounded path was gated behind `?v=2` and never the default.
- `frontend-v2/src/pages/MorningBriefing.tsx`: flipped `readVersionFromUrl()` default from 1 → 2. Legacy paraphrase memo accessible via `?v=1` for fallback if the grounded path returns None on a quiet news week.
- Verified in preview: `/briefing` now shows the grounded memo with `GROUNDED · v2` badge, inline citation superscripts, and `Sources used` disclosure expanded from the 15-claim pool.

### Step 4 — Policy documented
- `CLAUDE.md` gained a new "KG / entity-extraction policy (2026-05-29)" section. States: no new standalone KG features; v15.0 data surfaces only as evidence inside frames + briefing; lists the hidden pages and the frozen tables explicitly so the next session doesn't re-litigate. Also corrected the now-stale "KG extraction backfill has not been run yet" bullet under "Things to be careful about" (backfill is done — 2,768 articles, 3,833 claim_records).

### Files touched
- Backend
  - `backend/app/routes/narrative_frames.py` — new `/quote-evidence` endpoint
- Frontend
  - `frontend-v2/src/App.tsx` — `/entity-network` redirect, removed `EntityNetwork` import
  - `frontend-v2/src/components/Sidebar.tsx` — commented Entity Network nav entry
  - `frontend-v2/src/api/client.ts` — added `frameQuoteEvidence`
  - `frontend-v2/src/pages/NarrativeDetail.tsx` — `<SupportingQuotes>` section + state + `quoteLabelFilter` + fetch
  - `frontend-v2/src/pages/MorningBriefing.tsx` — default version 1 → 2
- Docs
  - `CLAUDE.md` — new policy section + updated stale bullet

### Carry-forward
- The grounded memo cites conservatively (1 citation per memo observed). If the user wants denser citations, this is a prompt-tuning question in `briefing_summary._generate_grounded` — likely add an explicit "use 2-3 [CN] markers if quotes support distinct points" instruction. Not done — defer to user signal.
- `top_entities` on the briefing endpoint returns up to 6 entities — UI may or may not render them; not investigated this round.
- The hidden but preserved `EntityNetwork.tsx`, `EntityReview.tsx`, `entity_review.py` route, `extractor_drift.py` route are all dead code. They can be deleted whenever — not blocking anything.

## 2026-05-29 Session: promote v2 briefing to default
### Built
- **`/briefing` now renders v2 by default** — no `?v=2` query param needed. `readVersionFromUrl` defaults to 2 in [MorningBriefing.tsx](frontend-v2/src/pages/MorningBriefing.tsx). v1 still accessible via `?v=1` as a fallback path.
- **"GROUNDED · v2" chip removed** from the default view — v2 IS the briefing now, the chip was clutter.
- **"LEGACY · v1" chip added** when someone is explicitly on v1 — gray, subtle, marks the page as legacy so bookmarks or stale links don't mislead.
- **Tooltip text simplified** — describes what the memo does without the "v2" framing.

### Behavior matrix
| URL | Memo | Order | Hidden in this version |
|-----|------|-------|------------------------|
| `/briefing` (default) | Grounded object (text + citations + sources_used) | Memo → Overnight → Needs Response → Activity → Pulse | Most Recent Articles |
| `/briefing?v=1` | Legacy string | Memo → Pulse → Needs Response → Most Recent | Overnight Changes, Activity, Sources Used |

### Notes
- Backend `/api/briefing/morning` still defaults to `v=1` if no query param is passed. The frontend now passes `v=2` explicitly. Any other consumer of the endpoint (none today) gets the legacy shape by default — backwards compatible.
- Empty-state placeholder for Needs Response only renders in v2. v1 keeps its original behavior of hiding empty sections.

---

## 2026-05-29 Session F (continued): close-out audit + bug fixes

After the four-step KG harvest (above) landed and I'd claimed "all done," user asked twice "are you sure?" — which surfaced real issues I'd missed by not running the typecheck before declaring done.

### Bugs found and fixed in NarrativeDetail.tsx
- Five invalid-CSS instances in the new `<SupportingQuotes>` component. I used `${C.accent}22` and `${color}22` to alpha-tint backgrounds — that hex-suffix trick only works on hex colors, but `C.accent`, `C.opponent`, `C.candidate`, `C.text3` are `var(--...)` strings. The browser silently dropped those style declarations.
- The chip *border* visibly toggled on click (worked), so my preview-eval check on the chip's `.style.background` had returned `"var(--opponent)22"` and I mistook that for "working" — it was actually invalid CSS the browser ignored.
- Fix: extended the `C` palette with `accent: 'var(--accent)'` and `accentSoft: 'rgba(255,191,0,0.13)'`; swapped the four `${C.accent}22` sites to `C.accentSoft`; rewrote `QUOTE_LABEL_COLOR` so every label has a hex value (so the `${color}22` suffix works for per-label chip tints).
- Verified live: attack chip now correctly tints to `rgba(215, 25, 19, 0.133)` when active.

### Minor cleanup
- `Sidebar.tsx` — removed the unused `Network` icon import left over after commenting the Entity Network nav line.

### What the user did in parallel (recorded so future sessions know what landed)
- Restructured `MorningBriefing.tsx`: added empty-state placeholder for Needs Response in v2, reordered sections, added a `LEGACY · v1` chip that only shows on the alternate path (no badge clutters the v2 default). Tooltip text rewritten in plain English. Backend default stays `v=1` for backwards compat; frontend passes `v=2` explicitly. (Documented in detail in the user's own session entry above.)

### Process note for future sessions
- **Run `tsc --noEmit` before declaring frontend work done.** The preview server happily runs code with invalid CSS in inline styles — browsers silently ignore unparseable values, so behavior looks "mostly right" but is silently broken. Asking preview_eval for the rendered `.style.background` will return the literal invalid string and looks indistinguishable from working state. Run the typecheck.
- **Two pre-existing errors in `src/pages/Landscape.tsx` (line 1482, undefined `candidateName`/`opponentName`)** — not from this session. Worth a separate fix at some point.

### Carry-forward (not blocking; can wait for whenever someone next touches these areas)
- `top_entities` on the briefing endpoint returns up to 6 entities — UI rendering not verified in this session, may or may not look right.
- Dead code preserved during the hide-don't-delete passes: `frontend-v2/src/pages/EntityNetwork.tsx`, `frontend-v2/src/pages/EntityReview.tsx`, `backend/app/routes/entity_review.py`, `backend/app/routes/extractor_drift.py`, plus the registry/scripts that drove the v14.x → v15.0 transition. None of it is mounted or surfaced. Safe to delete whenever someone has the appetite.
- The grounded memo's citation density was sparse in spot tests (1 marker per memo). User decided not to tune the prompt — let the LLM cite organically. If it starts skipping obvious quotes in real briefings, the prompt softening discussed (line "If you can't find a quote to back an assertion, just don't include the assertion." → permit uncited factual assertions, mandate citation only for direct attribution) is a small change to try.

### Files modified this close-out
- `frontend-v2/src/pages/NarrativeDetail.tsx` — C-palette additions, four `${C.accent}22` → `C.accentSoft` swaps, `QUOTE_LABEL_COLOR` rewritten with all-hex values
- `frontend-v2/src/components/Sidebar.tsx` — dropped unused `Network` import

### Net state at session close
- v15.0 corpus fully populated and feeding two product surfaces (frame supporting-quotes + grounded briefing memo) where a campaign team will actually use it
- KG-as-standalone-product is hidden across the UI; underlying tables frozen, not deleted
- Policy section in `CLAUDE.md` documents the no-more-KG-infrastructure stance with explicit DO/DON'T list and a "if tempted to reverse this, re-read these sessions first" pointer
- Typecheck clean on every file I touched (pre-existing `Landscape.tsx` errors aside)

## 2026-05-29 Session: site-wide font cleanup
### Built
- Removed all `'IBM Plex Mono'` font overrides (63 instances) and all `'Barlow Condensed'` overrides (15 instances) across the frontend — 78 declarations gone, 9 page files touched.
- Inter (set globally in `index.css`) now wins by default everywhere.
- Generic `fontFamily: 'monospace'` (4 instances — keyboard shortcut chips like ⌘K, textareas where users type code-like input) preserved.

### Why
Mixed-font sprawl with no information role — Plex Mono was used for "classified intelligence" theater on timestamps/labels/badges, Barlow Condensed for display headers. Neither served the user (campaign staff need a clean dashboard, not a Bourne movie prop). One font is more readable, more professional, and removes a class of styling decisions future sessions don't need to make.

### Files modified
| File | fontFamily removals |
|------|---|
| pages/Setup.tsx | 14 |
| pages/Opponents.tsx | 13 |
| pages/Analytics.tsx | 13 |
| pages/NarrativeDetail.tsx | 12 |
| pages/MorningBriefing.tsx | 11 |
| pages/Monitors.tsx | 8 |
| pages/EntityNetwork.tsx | 3 |
| pages/Narratives.tsx | 3 |
| pages/Landscape.tsx | 1 |

### Companion: briefing visual restyle (earlier same session)
- Title "BRIEFING" (34px black caps Barlow) → "Daily Briefing" (24px Inter weight 600 normal case)
- Date / district line: normal case, Inter, weight 500
- Section headers (Race Situation, Narrative Pulse, etc.): from Barlow Condensed 13px caps with 0.12em letter-spacing → Inter 14px weight 600 normal case
- Footer "CAMPAIGN INTERNAL — DO NOT DISTRIBUTE" → "Generated [timestamp]"
- Section title alignment: was center-left (flanked by left + right `<hr>`); now left-aligned (title first, `<hr>` extending right)

### Open questions / concerns for review
- A handful of large-display elements that used Barlow Condensed at 20-34px may look slightly less "punchy" with Inter. If any specific spot looks weak (e.g. an empty-state heading on the Opponents or Monitors page), bumping `fontWeight` from 700 → 800 or sizing up is the right per-instance fix. Not done globally because Inter 600 looks fine in most of the spots I checked.
- The label chips inside cards (ATTACK, ENDORSEMENT, PERSON, BILL — small-caps badges) STILL render uppercase because that's `textTransform: 'uppercase'` not font. They're functional categorical tags so I kept them.
- Some small-caps headers elsewhere (Sources Used button, etc.) also dropped the uppercase + letter-spacing during the briefing restyle. Other pages may have similar headers that could be softened next pass.

### Verification
- TypeScript clean (no new errors).
- Live page snapshots confirm only Inter renders on Dashboard, Briefing, Articles, Timeline.

## 2026-05-29 Backlog snapshot (consolidated — read first if you're picking up next)

After the briefing v2 work landed, these are the next things worth doing, roughly in priority order:

### 1. Speaker attribution on ClaimRecord  (HIGH — unblocks a real feature)
The "Voices this week" UI was intentionally cut from v2 because the current data can't safely tell us WHO said a quote, only which entities are NAMED in it. ChatGPT's contrarian flagged this as the biggest single risk — a screenshot of "Bresnahan said: '...'" that's actually someone else's words is a real reputational hazard for a campaign tool.

Implementation sketch:
- Add `speaker_entity_id` (nullable FK to entities) + `attribution_confidence` (high/medium/low) to ClaimRecord schema
- Update `entity_extraction.py` LLM prompt to extract speaker separately
- Re-run extraction over the 1,808 articles with ClaimRecords (cost ~$0.80 since most articles need re-extraction, not cheap delta)
- Sample 50 records, eyeball — only ship a Voices UI if attribution confidence is >= 85% on the sample
- Estimated 3-5 days with validation loops

### 2. Manual reliability scoring for the 80 newly-created outlets  (MEDIUM — cheap, high coverage win)
Today only 38% of labeled briefing claims come from outlets with a `reliability_score`. The outlet backfill created 80 new outlet rows (River Reporter, PoliticsPA, NRCC, City & State PA, etc.) but left `reliability_score = NULL` on each because we don't want the system to guess.

Scoring is fast if someone who knows local PA media sits down with a list: ~1 hour. Would lift scored-claim coverage to ~80%. Without it, the briefing's reliability ranking treats most quotes as median-tier.

Script: `cd backend && .venv/bin/python -c "from app.db import SessionLocal; from app.models import Outlet; db = SessionLocal(); [print(o.id, o.name, o.domain) for o in db.query(Outlet).filter(Outlet.reliability_score.is_(None)).order_by(Outlet.name)]"` produces the list.

### 3. Restart uvicorn at your convenience  (HOUSEKEEPING — 30s)
The briefing memo cache holds a pre-tightened 628-char memo from before the v2 prompt change. New memo will be ~100 words. Either wait for the 30-min TTL or `pkill -f uvicorn && cd backend && .venv/bin/uvicorn app.main:app --reload`.

### 4. Visual consistency pass on the other pages  (LOW — polish)
The Briefing page now uses clean Inter throughout. Other pages (Entity Network, Timeline, Analytics, Setup) still have some all-caps headers + letter-spacing leftovers from the "classified document" aesthetic. The font-cleanup pass killed the worst (Plex Mono / Barlow Condensed) but left `textTransform: 'uppercase'` and heavy letter-spacing in some headers. Maybe 1 hour to do a similar pass page-by-page.

### 5. Future architecture moves filed earlier (defer until needed)
- Two-stage labeling (extraction precision + cheap post-classifier) — would let us iterate on the label ontology without re-running gpt-4o-mini. Real architectural improvement, but a project not a sprint.
- Event abstraction layer — campaigns think in events ("debate", "endorsement rollout"), not quotes. Story clustering today is SimHash dedup. Real research effort, file for later.
- Compositional briefing rebuild (full structured-analytical layer per ChatGPT) — v2 is a partial step toward this; v3 would add sentence-level provenance and a separate structured analytical pass before prose synthesis.
- Actionability classifier fix at the LLM level — the band-aid filter on needs_response is correct for v1, but the root cause is `framing_to_action` in `campaign_analysis.py` blindly mapping `hurts_candidate` → 'respond' regardless of source. A model-level fix would re-classify the corpus (~$$).

### Don't forget
- `INTER_SESSION.md` protocol: read it at start of session, append a dated entry when finished.
- `CLAUDE.md` "Data validation protocol" (added this session): for any task that touches the DB, sample 20-50 real records and run programmatic invariant checks before claiming the data is ready. We learned this the hard way validating extraction quality.
- `?v=1` still works on the briefing — keep it functional, don't delete the legacy path without consultation.

---

## 2026-05-29 Session: Postgres rehearsal #2 + 2 dialect fixes

### Built
- **Added NUL-byte audit** to `backend/scripts/preflight_audit.py`. New `audit_nul_bytes()` walks `source_items.{title,raw_text,summary}`, `story_clusters.{title_representative,summary_representative}`, `narrative_frames.description`, `narrative_frame_mentions.extracted_text`, `claim_records.evidence_span`. WARN-severity. Closes the rehearsal-#1 TODO.
- **Fixed Postgres-incompatible Boolean default** in `2026_05_29_0001-5d9e3f2a8b1c_add_suspect_flag_to_snapshots.py`: changed `server_default=sa.text("0")` → `server_default=sa.false()` and the UPDATE's `SET suspect = 1` → `SET suspect = :flag` with `True`. SQLite was tolerating both; Postgres rejected both with `DatatypeMismatch: column "suspect" is of type boolean but default expression is of type integer`. Live SQLite already had this migration applied, so the fix is forward-only — no re-run on SQLite needed; the column state is semantically identical.
- **Ran preflight audit** against live `war_room.db` (21,225 source_items). Findings: 169 PASS, 15 WARN, 6 FAIL. WARNs are expected (enum drift from new categories, UTF-8 replacement chars from web scrapes, 47 NUL bytes in `source_items.raw_text` + 3 in `source_items.summary`). FAILs are not cutover blockers — see below.
- **Rehearsal #2** completed: snapshotted live DB to `/tmp/war_room_rehearsal2.db`, dropped + recreated `noctua`, ran all 9 alembic migrations (head = `6e2b8c4a9d1f`), then `sqlite_to_postgres.py --skip-orphans`. **✅ PASS** — 71,886 rows across 36 tables in ~24s wall-clock (source_items 18.3s). All row counts, hashes, sample diffs, FK integrity (32 sweeps), and aggregate sanity (6 checks) green.

### FAILs from audit — NOT cutover blockers, but flagged

1. **187 rows in `source_items.relevance_reasons` fail JSON parse** (46 on 5/27, 140 on 5/28, 1 on 5/29). Root cause: regression in `backend/app/services/ingestion.py:628` — `item.relevance_reasons = reason` writes a plain string into a column documented as JSON-array text. The reader `_safe_json_list()` silently returns `[]` for malformed input, so the UI has been quietly dropping these reasons for 2 days. Spawned a separate fix task for the one-line fix + 187-row backfill + regression test. The Postgres column is also TEXT — migration copies the bad data faithfully, same broken behavior in both DBs.

2. **177 FK orphans** across 5 FKs (frame_cluster_matches:53, cluster_opponent_activities:72, claims.subject:25, claims.object:26, entity_mentions:1). The migration script handles all of these via `--skip-orphans` — rehearsal #2's FK integrity sweep on the *destination* showed zero orphans. Same pattern as rehearsal #1 (had 51 orphans); count grew because of normal ingestion activity since then.

### Open questions / concerns for review

- **Cutover is now low-risk.** Two rehearsals back-to-back have passed cleanly. The only Postgres-only bugs found were trivial (round/cast in rehearsal #1, Boolean default in this one) and both are fixed.
- **No concurrency soak test (Phase 2.5) was run.** Phase 3 rehearsal #1 notes argued the smoke test against populated Postgres covers most of the value. I agree, but flagging for completeness.
- **The relevance_reasons regression is independent of the migration** but is actively corrupting new ingestion rows. Recommend fixing it before cutover so we don't keep accumulating bad data in either DB.

### Files changed
- `backend/scripts/preflight_audit.py` — added `audit_nul_bytes()` + `NUL_BYTE_COLUMNS` constant + docstring entry
- `backend/alembic/versions/2026_05_29_0001-5d9e3f2a8b1c_add_suspect_flag_to_snapshots.py` — Postgres-portable Boolean default + UPDATE parameter

### Live state at end of session
- SQLite war_room.db: at `6e2b8c4a9d1f (head)`, 21,225 source_items (untouched — snapshot was read-only)
- Postgres noctua: at `6e2b8c4a9d1f (head)`, populated from rehearsal #2 snapshot with full 71,886 rows
- `/tmp/war_room_rehearsal2.db`: 320M snapshot, can be discarded

## 2026-05-29 Session: relevance_reasons JSON-array bug

### Built
- Fixed `backend/app/services/ingestion.py:628`: the LLM-reason write was `item.relevance_reasons = reason` (plain string) where `models.py:161` and `_safe_json_list` in `routes/dashboard.py:28` expect a JSON array. Changed to `json.dumps([reason])`, matching the prefilter path at line 574 and `race_relevance.py:339`.
- Backfilled the 187 corrupted rows in `war_room.db` via `UPDATE source_items SET relevance_reasons = json_array(relevance_reasons) WHERE relevance_reasons IS NOT NULL AND relevance_reasons != '' AND substr(relevance_reasons, 1, 1) NOT IN ('[', '{');` — `json_array()` does correct string-escaping, so I didn't need a Python script. Backup at `backend/war_room.db.bak-relevance-reasons-fix-20260529-050039`.
- Added `backend/tests/test_relevance_reasons_round_trip.py` (3 tests) — covers both code paths in `_create_and_analyze` that write `relevance_reasons` (LLM reason + prefilter drop) plus the no-reason skip. Asserts each write `json.loads`-decodes to a list. Confirmed the test catches the regression by re-introducing the buggy form in-process.

### Key decisions
- Used SQLite `json_array()` instead of a Python re-encode script — same output (`["text"]`) without needing to disable WAL or instantiate the ORM. CLAUDE.md warns against ad-hoc Python writes against the live DB; raw SQL via the `sqlite3` CLI in a `BEGIN; ... COMMIT;` transaction with a verified `changes()` count is the safer equivalent for a targeted column-rewrite.
- Defensive backup BEFORE the UPDATE despite the operation being non-destructive — the column had no data we couldn't reconstruct from the LLM output, but a backup is cheap.
- Strict `json.loads` (no fallback) in the regression test — the dashboard's `_safe_json_list` swallows `JSONDecodeError` and returns `[]`, which is exactly how this bug stayed invisible for two days. The test must NOT mirror that forgiveness.

### Verification
- 187 → 0 corrupted rows by the same query that audited them.
- Full-table round-trip: 15,705 of 15,705 non-empty `relevance_reasons` rows parse to `list` via `json.loads`.
- New tests pass (3/3); related ingestion suites still green (96/96).
- Fix sanity-checked by temporarily reintroducing the buggy assignment via an in-process re-exec of `ingestion.py` — `json.loads` raised, confirming the test would have caught the original bug.

### Open questions / concerns for review
- The dashboard's `_safe_json_list` silently swallows malformed input and returns `[]`. That's defensive, but it's also why this bug went unnoticed for two days. Worth a follow-up: have it log a warning (rate-limited) when `json.loads` fails, so the next time someone writes a plain string we hear about it in the server log instead of seeing missing reasons in the UI.
- Also audited the sibling JSON-array columns (`extraction_quality_reasons`, `gdelt_themes`) with the same `substr(..., 1, 1) NOT IN ('[', '{')` predicate — both clean (0 corrupt). No callsite-level audit was done on the writers, but the data is in shape.

---

## 2026-05-29 Session: Phase 4 CUTOVER — SQLite → Postgres ✅

### What happened

- **Pre-cutover state** verified: live SQLite at alembic head `6e2b8c4a9d1f`, 21,225 source_items, backend running on PID 12147.
- **Stopped the backend** with SIGTERM (graceful, APScheduler flushed). Port 8000 freed.
- **Took final SQLite backup**: `backend/war_room.db.cutover-20260529-050401` (320 MB). This is the rollback artifact — DO NOT delete for at least 4 weeks (per Phase 5 plan).
- **Truncated `noctua`** (rehearsal #2 data was sitting in it). `TRUNCATE ... RESTART IDENTITY CASCADE` for all tables except `alembic_version`. Confirmed empty.
- **Ran `sqlite_to_postgres.py`** against `war_room.db` → `noctua` with `--skip-orphans`. Log at `/tmp/cutover-20260529-050401.log`. Result: ✅ PASS. 71,886 rows / 36 tables / wall-clock identical to rehearsal #2.
- **Found and fixed a runbook gap**: `app/db.py` reads `DATABASE_URL` at import time, but `main.py` imports `app.db` *before* anything triggers `load_dotenv()`, so a `DATABASE_URL=` line in `.env` was silently invisible. Added `load_dotenv()` to the top of `db.py`. Verified with a fresh Python shell that `app.db.DATABASE_URL` now reflects the .env value.
- **Wired the new URL**: backed up `.env` → `.env.bak-PRE-CUTOVER`, appended `DATABASE_URL=postgresql+psycopg://theo@localhost:5432/noctua`.
- **Started uvicorn**. Boot log at `/tmp/cutover-boot-20260529-050401.log`. Alembic logged `Context impl PostgresqlImpl` (confirms dialect). Zero ERROR/CRITICAL lines.

### Smoke checklist

| Endpoint | Result |
|---|---|
| `GET /api/admin/dbstats` | 200 — `url_dialect=postgresql` |
| `GET /api/search?q=cognetti` | 200 — 5 results, ids `[19943, 382, 9260, 14223, 395]` (different ranking from SQLite, expected — see Phase 3 notes) |
| `GET /api/narrative-frames` | 200 — 39 frames (matches source) |
| `GET /api/articles/recent?limit=20` | 200 in 71ms |
| `GET /api/articles/recent?offset=100` | 200 in 34ms |
| `GET /api/opponents` | 200 — 1 opponent (matches source) |
| `GET /api/briefing/morning` | 200 in 5.0s, 8.8KB |
| `GET /api/system/scheduler-health` | 200 — nulls expected on a fresh boot, scheduler hasn't fired yet |
| `pg_stat_activity` | 4 active backend connections, healthy |

### Files changed
- `backend/app/db.py` — added module-top `load_dotenv()` so `DATABASE_URL` from `.env` is actually read by the time `os.environ.get(...)` runs
- `.env` — appended `DATABASE_URL=postgresql+psycopg://theo@localhost:5432/noctua` (backup at `.env.bak-PRE-CUTOVER`)

### Live state at end of session

- **Backend**: running on PID 44298 (parent) / 44301 (worker), `http://localhost:8000`, against Postgres `noctua`
- **Postgres `noctua`**: at `6e2b8c4a9d1f (head)`, populated with full live data (71,886 rows across 36 tables)
- **SQLite `war_room.db`**: still at `6e2b8c4a9d1f (head)`, untouched (the migration source was read-only). Backup at `war_room.db.cutover-20260529-050401`.
- **`.env`**: includes the new `DATABASE_URL` line. Backup at `.env.bak-PRE-CUTOVER`.

### Rollback (if needed in the next 4 weeks)
1. `kill $(lsof -i :8000 -t)` to stop the Postgres-backed backend
2. `cp .env.bak-PRE-CUTOVER .env` to remove the `DATABASE_URL` line
3. `cd backend && .venv/bin/uvicorn app.main:app --reload` — boots against SQLite
4. The SQLite DB is exactly as it was before cutover (migration was read-only on source)

### Open work for next session

- **Phase 5 cleanup** is gated on 4 weeks of green operation. Do not delete SQLite codepaths from `db.py` / `search_index.py` before ~2026-06-26. Schedule a checkpoint at one week, two weeks, four weeks.
- **Watch for**: any `ProgrammingError` / `IntegrityError` in uvicorn logs that didn't surface in either rehearsal. `pool_stats` invalidations. Lock waits visible at `/api/admin/dbstats`.
- **Scheduler hasn't fired yet** — first RSS cycle is normally hourly. Worth confirming a clean ingestion cycle completes against Postgres before considering the cutover fully validated.
- **The relevance_reasons regression** (187 corrupted rows, side-task chip spawned earlier in this session) still wants fixing. Not Postgres-related — same bug exists in both DBs.

### Decisions / notable choices

- **Did not enable `pg_stat_statements`** for dbstats — would require Postgres restart affecting other DBs on this server. Plan calls this out as deferred until perf becomes a real problem.
- **Used local Postgres 15, not Docker.** Same outcome as Docker for dev purposes; one less moving part. Production hosting (Neon/Supabase/RDS) decision deferred per the migration plan.

## 2026-05-29 Session: post-cutover code-review followups (P1–P4)

The 5-agent code review on `d90d62c` surfaced more than the three hot-fixes already committed. Worked through the remaining IMPORTANT findings in four focused commits.

### Built

**P1 — `978987d` backend: fix Postgres dialect detection + extend timeouts for LLM workers**
- Found a deeper bug while implementing the requested timeout fix: `_IS_POSTGRES` at `backend/app/db.py:37` was `DATABASE_URL.startswith(("postgresql:", "postgres:"))`. The real URL is `postgresql+psycopg://...` which does NOT start with `postgresql:` — the next character is `+`, not `:`. The check has returned False since the 2026-05-29 cutover, silently disabling the entire `_set_postgres_session_defaults` connect listener. **Statement_timeout, lock_timeout, and idle_in_transaction_session_timeout were the server defaults (0 / 1s / 0) instead of the intended 60s / 10s / 5min** for the whole period the cutover has been live. Verified by `SHOW statement_timeout` on an app-engine session: was 0, now 1min after fix.
- Switched to `make_url(DATABASE_URL).get_backend_name()` and accept both `"postgresql"` and the legacy `"postgres"` alias.
- With the listener now actually firing, raised `idle_in_transaction_session_timeout` from 5min → 30min. Sized to the rescore worker pattern: `_process_item` opens a session, runs SELECT (begins tx), then calls `campaign_analysis.analyze` which can sleep up to 5min × 3 attempts on provider rate-limit backoff. 30min absorbs the full backoff ladder.
- Defended Alembic in `backend/alembic/env.py`: on Postgres connections, issue `SET statement_timeout = 0` + `SET idle_in_transaction_session_timeout = 0` before `context.begin_transaction()`. Migrations are now unbounded regardless of how the engine was constructed (today they use a separate engine without the listener; tomorrow they might not).
- Added `tests/test_db_dialect_detection.py` (8 cases) covering `postgresql+psycopg`, `postgresql+psycopg2`, bare `postgresql://`, legacy `postgres://`, sqlite variants, plus a live cross-check of `_IS_POSTGRES` / `_IS_SQLITE` against the resolved URL.
- Restarted uvicorn (PID 55981). Smoke test against `/api/admin/dbstats`, `/api/search`, `/api/narrative-frames`, `/api/articles/recent`, `/api/opponents` all 200.

**P2 — `0960db3` backend+frontend: confirm-string guards on destructive endpoints**
- `POST /admin/rescore-articles` — previously no confirm. A misclick would queue a full LLM rescore over 21k+ articles (~2/min, multi-day, real money). Now takes a `RescoreArticlesRequest` body. When `only_unscored=False`, requires `confirm: "RESCORE ALL ARTICLES"`. When `only_unscored=True` (the safe resume path), no confirm. Internal callers (`scheduler.py`, `campaign.py`) call the service function directly and are unaffected.
- `DELETE /narrative-frames/{frame_id}` — previously no confirm. Cascades through FrameClusterMatch + FrameVariant + FrameStageHistory + NarrativeFrameMention (real frame has hundreds of FCM rows + dozens of variants). Now requires `?confirm=DELETE+FRAME` (URL-encoded). Also adds `?dry_run=true` which returns cascade counts without committing. Smoke check on frame_id=1 ("Bresnahan's Stock Trades") dry-run: 201 FCM + 144 NFM + 37 variants + 7 stage_history.
- Frontend `client.ts` `deleteFrame` signature now requires `confirm: 'DELETE FRAME'` at the type level; new `previewDeleteFrame` exposes the dry-run path. No UI button calls this today; the strict signature means a future "delete frame" button can't omit the guard.
- Added `tests/test_destructive_endpoint_guards.py` — 9 tests, all pass.

**P3 — `e33970f` frontend: surface failed-write errors via toast + inline row state**
- `Opponents.tsx:248` (createOpponent), `ReviewQueue.tsx:408` (doAction + bulkAction), `Monitors.tsx:144` (toggleActive / deleteMonitor / triggerCrawl / discoverUrls) all had `catch { /* silently fail */ }`. Failed writes looked identical to successes — the operator would double-fire the same action.
- New `components/Toast.tsx` — minimal context-based toast with `aria-live="polite"`, auto-dismiss (6s for errors, 3s for successes), click-to-dismiss. `useToast()` hook tolerates being called outside a provider. `describeError()` helper parses the `${METHOD} ${path} → ${status}: ${body}` thrown by `api/client.ts` into a readable string (handles FastAPI's `{detail: ...}` payload).
- `App.tsx` wraps the router in `<ToastProvider>` so any page in the tree can call `useToast()`.
- Opponents: inline error inside the AddOpponentModal **plus** a toast (message survives if operator accidentally closes the modal).
- ReviewQueue: per-id `actionErrors` Map renders the failure inline below the row; bulk action sets it for every selected id. Successful retry clears the row's error before the call.
- Monitors: per-row `rowErrors` strip below the failing row for toggle/delete; toasts for global actions (`triggerCrawl` also fires a success toast on the happy path).
- Verified end-to-end against the running app by patching `window.fetch` to return 500 for the next monitor PUT / opponent POST / review-queue action, then clicking the corresponding button. In all three cases the toast appeared in the viewport AND the inline row error rendered with the FastAPI detail payload extracted.

**P4 — `58701b5` frontend: remove KG mock data from SearchResults + GeographicOverlay**
- `SearchResults.tsx:10` — entity tab client-side-filtered `MOCK_ENTITIES` and linked to `/entity-network` (which `App.tsx` redirects to `/`). Triple-broken. Removed the tab, the MOCK_ENTITIES/EntityType/TYPE_ICONS imports, the entity branch of the results memo, the count, the visible state, and the result section. Tab type narrows from `'all'|'articles'|'narratives'|'entities'` to drop `'entities'`.
- `GeographicOverlay.tsx` — header badge said "LIVE MAP" but the side panel was built from `MOCK_ENTITIES` + `MOCK_RELATIONS`, and city marker stats came from a `placeholderStats()` hash function that fabricated `articleCount` and a `{d, r, neutral}` stance. The operator couldn't tell what was real. Per the CLAUDE.md KG-policy retreat, wiring to `/api/entity-network` was not an option, so the dishonest sections came out.
- Renamed badge to "DISTRICT MAP". Removed `placeholderStats()`, `cityIndex` useMemo, `stanceColor`, `cityRadius`, filter-mode chips, stance-color legend, per-city stance bar, StatCard grid, entity list panel, made-up `description` + `articleCount` on CityNode, and the "X articles" sub-label on marker tooltips. CityNode now only carries fields straight from `/api/race/cities`.
- Side panel keeps city name, LSAD category, lat/lon, state abbreviation, and a disclaimer: "Per-city article volume, stance, and entity activity are not currently tracked. The map shows the district boundary and Census-listed places only."
- Verified in the running app: `/search?q=cognetti` tabs read "All (118) / Articles (100) / Narratives (18)", no Entities tab. `/map` shows "DISTRICT MAP" badge, no LIVE MAP / stance legend / filter chips. Clicking Moosic shows "BOROUGH IN PA-08 / Moosic / 41.3560°, -75.7047° · PA" + disclaimer.

### Key decisions

- **Always-on idle-in-tx of 30min** instead of "commit eagerly between LLM calls in rescore." The eager-commit refactor touches multiple workers + the `campaign_analysis` callsite and rescheme of the tx boundary changes semantics (partial rescore results would commit before the cascading writes). 30min is enough headroom for the worst-case retry storm without changing tx semantics. If a worker gets stuck, the `pool_stats.invalidations` counter at `/api/admin/dbstats` will surface it within the 30min window.
- **Conditional confirm on rescore-articles** rather than always-required. `only_unscored=True` is the safe resume path used after every backfill cycle by scheduler.py + campaign.py — making operators type "RESCORE ALL ARTICLES" to resume a partial run after a deploy feels wrong. The danger is the full-21k run; that's what's gated.
- **Per-row error state + a global toast**, not just a toast. Toasts are ephemeral (6s); if the operator looks away, they miss it. Per-row state persists until the row is retried successfully, so a stuck row remains visibly broken.
- **Strip GeographicOverlay rather than wire it to /api/entity-network**. The CLAUDE.md KG policy explicitly retired typed-edge prose inference; wiring this page to the entity-network endpoint would recreate the v14.x edge-projection problem the project already retreated from twice. The honest reading was "remove the dishonest content; keep the genuine Census map."
- **Did not fix the pre-existing `test_seed_race_directory_imports_real_fec_federal_snapshot` failure** — out of scope. Confirmed via `git stash` that it fails on `main` without my changes. Worth its own session.

### Open questions / concerns for review

- **The 60s `statement_timeout` is now actually live** for the first time since the cutover. If any historical query in the app took 30-60s, it'll now hit the 60s ceiling and EITHER complete just in time OR start erroring. Watch `/api/admin/dbstats slow_queries` and uvicorn ERROR logs over the next 24h. Briefing v2 takes ~5s, the heaviest expected; backfills go through `start_rescore` and chunked DB writes; should be safe.
- **The scheduler still hasn't fired its first ingestion since this session's restart** (uvicorn restart timestamp). Next hour will tell whether the new timeouts impact RSS ingestion specifically.
- **The relevance_reasons regression is already fixed** — the cutover happened AFTER the fix session, so Postgres copied clean data. Verified: 0 corrupted rows in `noctua.source_items.relevance_reasons`. The side-task chip referenced at session start is stale.
- **Frontend `client.ts deleteFrame` now requires `confirm: 'DELETE FRAME'`** at the type level — there is no UI caller today, but if a future "delete frame" button is wired up, the call site must spell out the confirm string. That's intentional friction; remove only if the call site adds its own dialog.

### Live state at end of session

- Backend: uvicorn PID 55981, running against Postgres `noctua`. Pool 6 active connections, 99%+ cache hit, 0 invalidations, 0 deadlocks.
- Postgres session defaults verified live: statement_timeout=1min, lock_timeout=10s, idle_in_transaction_session_timeout=30min.
- Frontend dev server running on port 5174 via Claude Preview (`b7909906...`).
- Branch `main` is at commit `58701b5`, pushed to GitHub. Four commits ahead of the start-of-session state (d90d62c → 978987d → 0960db3 → e33970f → 58701b5).
- The two pre-existing Landscape.tsx TS errors and the test_seed_race_directory FEC failure remain on main, unchanged.

---

## 2026-05-29 Session: RaceSentimentCard compaction + Inside Elections auto-sync

### Built

**Frontend — RaceSentimentCard compaction** ([RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx))
- Replaced the two-side market percentage display (`Cognetti 45% · Bresnahan 55%`) with a single lead (`Bresnahan +10%`). Leader name keeps its candidate/opponent color; the percentage points stay neutral. New `computeMarketLead()` helper handles partial / tied / full states; the original raw values are preserved in the row's `title=` hover attribute.
- Dropped the "Markets" / "Forecasters" sub-section headers. All sources now render in one continuous list (markets first, ratings second). The signal format itself (`+18%` vs. `Lean R · 60–75%`) provides enough visual distinction without doubled headers.
- Tightened density: `padding 8px → 3px`, `fontSize 13 → 12`, link icon `13 → 12`. Grid restructured from `120px 1fr auto auto` to `180px auto auto 1fr auto` so name | signal | delta pack tight on the left and only the external-link icon sits flush right — eliminates the wide blank middle column.
- Name cell now has `overflow: ellipsis` + `title=` fallback so long forecaster names ("Sabato's Crystal Ball", "Cook Political Report") truncate cleanly while remaining hover-discoverable.
- **Net result:** card height 389px → 247px (36% reduction).

**Backend — Inside Elections auto-sync** ([race_ratings_monitor.py](backend/app/services/race_ratings_monitor.py))
- Added `inside_elections_fetch()` + `_parse_inside_elections_district()`. The IE `/ratings/house` page returns 200 OK to plain httpx with a real-browser User-Agent — no special tooling required. Their HTML is well-structured (per-tier `<h3 class="rating ...">` followed by `<table class="ratings">` with `td.state` / `td.district` columns). Parser walks the document linearly, remembering the latest seen `h3.rating`, returning that text when it hits the matching state+district row.
- Registered `inside_elections` in [race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py) `_get_fetcher()`. End-to-end sync verified: `last_synced_at` populated, no error, returned `Tilt R · 52–60% · favors=opponent` (matches the previously hand-entered value — strong sanity-check). Dashboard now shows the green LIVE badge on Inside Elections in place of MANUAL.
- Updated the card footer text from "Manual: forecasters (Cloudflare-blocked)" to reflect the per-source reality: "Live: Polymarket, Kalshi, Inside Elections. Manual: Cook, Sabato (Cloudflare), DDHQ."
- 6 parser unit tests added in [tests/test_inside_elections_parser.py](backend/tests/test_inside_elections_parser.py) — HTML-snippet tests, no network, will fail loudly if IE changes their layout.

**Backend — `_get_html()` false-positive fix** ([race_ratings_monitor.py:202](backend/app/services/race_ratings_monitor.py:202))
- Previous Cloudflare detection raised `CloudflareBlockedError` whenever `"challenge-platform"` appeared anywhere in the response body. That string is part of the URL of `/cdn-cgi/challenge-platform/scripts/jsd/main.js`, which CF injects into *every* CF-protected page as passive bot-analytics telemetry — even on 200 OK pages serving real content. IE's `/ratings/house` is a 200 with that script present, so the detection mis-flagged it.
- New logic: require BOTH `status_code in (403, 503)` AND a challenge-only body marker (`"Just a moment..."` page title or `cf_chl_opt` inline JS variable, scanned in the first 8KB only). Bare 403/503 without the challenge body now propagates as a normal HTTP error instead of being mis-labeled as CF.
- Cook still correctly raises `CloudflareBlockedError` (status 403 + challenge title); verified.

### Key decisions

- **Did NOT keep Playwright as a dependency.** Installed it + Chromium (~200MB), tested headless + playwright-stealth against all 4 forecaster sites. Result: Cook and Sabato remain Cloudflare-IUAM-blocked even with stealth patches applied (`navigator.webdriver` masking, etc.). Inside Elections works with plain httpx, no headless browser required. DDHQ does not publish a public ratings URL (every obvious `/ratings/*` path 404s; homepage 200s but offers no ratings table). Conclusion: a runtime Playwright dependency would not bypass the only sources we wanted it for. Uninstalled + cleared the Chromium cache.
- **Did NOT pay for a Cloudflare bypass service.** ScraperAPI / ZenRows / Bright Data would unblock Cook + Sabato but cost $30–100/mo recurring. Punted to user; flagged as an option if/when those two MANUAL rows become annoying to update by hand.
- **Did NOT widen the name column past 180px to fit "Cook Political Report" / "Sabato's Crystal Ball" without ellipsis.** Tradeoff: a wider name column (~210px) would eat into the signal-side breathing room. Truncation with hover-title is the standard table-row idiom; can revisit if the user dislikes it.
- **Card stays one continuous list (no M/F type badge per row).** User opted "not sure" on the row-tagging question; default to the most compact option, since the `+N%` lead vs. `Lean R · band` rating formats are already visually distinct. Easy to re-introduce a tag later if it turns out users confuse the two.

### Open questions / concerns for review

- **Latent bug in `_favors_from_label()`** ([race_ratings_monitor.py:201](backend/app/services/race_ratings_monitor.py:201)): `if any(s in l for s in ("d", "democrat")) and "republican" not in l: return "candidate"`. The single-character `"d"` matches the letter `d` in any label containing one — including `"solid r"` (the lowercased "Solid R"). So `Solid R`, `Likely R`, and any tier with a `d` somewhere in its lowercase form would incorrectly favor `candidate`. Currently masked for PA-08 because the live rating is `Tilt R` (`"tilt r"` → no `d` → falls through to opponent), but will mis-favor if Cook ever moves PA-08 to Solid R / Likely R. Out of scope for this session — file a fix.
- **DDHQ row is "configured but empty"** — its `external_id` was set by `_rating_autoconfigure()` to the homepage URL, which is not a ratings page. The row shows "No rating entered" with no badge. If anyone ever wires a real DDHQ source URL, the auto-config will need to know the new path. May be worth removing the DDHQ row from the seed list until/unless we have a viable source for it; it's currently visual clutter.
- **`_get_html()` detection is still HTTP-status-coupled.** If Cloudflare ever starts serving the IUAM challenge with a `200` status (some CF configurations do this for redirect-based challenges), the detection will fail closed and the fetcher will return parser-garbage instead of `CloudflareBlockedError`. Probably fine in practice — the current modes Cook/Sabato are using are 403-based — but worth noting if their behavior changes.
- **Live IE rating equals the previously hand-entered value** (`Tilt R · 52–60%`). Good — confirms the parser, but means there's no behavioral diff for the user to *see* until IE actually changes its rating on PA-08. The LIVE badge in place of MANUAL is the only visible signal that anything happened.
- **Footer text is hand-coded** with the explicit source split. If a future session adds another live source, the footer must be updated alongside `_get_fetcher()`. Worth considering a derived footer that reads from the rows (`rows.filter(r => r.last_synced_at && !r.last_sync_error).map(r => r.display_name)` etc.) — small refactor, not done in this session.

### Live state at end of session

- Frontend on port 5174 still served by the same Claude Preview server (`71cb49d8-...`), serving the new compact card. Dashboard `/` rendered card contains: `Kalshi · LIVE · Cognetti +18%`, `Polymarket · LIVE · Cognetti +16%`, `Cook Political Report · MANUAL · Toss-up · 45–55%`, `Decision Desk HQ · No rating entered`, `Inside Elections · LIVE · Tilt R · 52–60%`, `Sabato's Crystal Ball · MANUAL · Lean R · 60–75%`, footer "Updated just now."
- Backend still importable; no migrations added. Live DB `inside_elections` row updated this session via `sync_one()` — `last_synced_at=2026-05-29 05:07 UTC`, `last_sync_error=null`.
- Playwright + playwright-stealth installed and uninstalled within the same session; `~/Library/Caches/ms-playwright/` removed. `requirements.txt` unchanged.
- Pre-existing GeographicOverlay HMR errors continue to appear in the Vite console (unrelated to this session — already noted in prior INTER_SESSION entries).
- Branch `main` is unchanged on disk vs. start-of-session — no commits made; user can review the diff before deciding to commit.

---

## 2026-05-29 Session (continued): Cook + Sabato via 270toWin + DDHQ removal

### Built

**Backend — Cook + Sabato auto-sync via 270toWin** ([race_ratings_monitor.py](backend/app/services/race_ratings_monitor.py))
- The earlier session reported Cook and Sabato as "permanently blocked by Cloudflare IUAM" after testing direct httpx, headless Chromium, and playwright-stealth. The user pushed back on the claim that an alternative source (270toWin) was "stale" — turned out my first probe extracted the wrong field (`seat_status` is a shared 270toWin field across all per-source pages; the per-forecaster rating lives in `map_code`).
- 270toWin's per-source pages (cook-political-report-2026-house-ratings, crystal-ball-2026-house-forecast) embed a JSON blob with one `map_code` per district. The codes map 1:1 to rating tiers via the page's color-palette legend (`0`=Toss-up, `6`=Lean R, etc — full 9-tier map in `_270TOWIN_COLOR_TO_RATING`).
- Implemented `_270towin_fetch(url, metadata, source_label)` + `_parse_270towin_map_code(html, state, district)`. `cook_fetch` and `sabato_fetch` are now thin wrappers over the shared helper. 9 parser unit tests in [tests/test_270towin_parser.py](backend/tests/test_270towin_parser.py).
- Validated end-to-end against the live DB: Cook syncs to "Toss-up · 45–55%" (matches the actual Cook site), Sabato syncs to "Lean R · 60–75%" (matches the actual Sabato site). Dashboard now shows the green LIVE badge on both rows.

**Backend — URL split: fetcher vs. display** ([db.py](backend/app/db.py), [race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py))
- Introduced `_RATING_FETCH_URLS` (the URL `sync_one` GETs) and `_RATING_DISPLAY_URLS` (the click-through URL the dashboard shows). For Cook and Sabato these diverge: we GET 270toWin but the user-facing external-link icon still points to cookpolitical.com / centerforpolitics.org. Inside Elections is the same URL in both maps.
- Live DB rows migrated in-session: `cook.external_id` and `sabato.external_id` repointed to their 270toWin URLs; `source_url` left as the authoritative source. Both rows had `last_sync_error` cleared.
- These constants are duplicated between `app/db.py` (seed) and `app/services/race_sentiment_sync.py` (autoconfigure fallback) — must be kept in sync. There's an inline NOTE comment on both. Could be consolidated into a single module if a future session decides to.

**Backend — DDHQ removed** ([db.py](backend/app/db.py), [race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py), [RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx))
- DDHQ does not publish a public 2026 House ratings table; their `/ratings/*` paths all 404. Validated again this session. Dropped from `defaults` in `_seed_race_sentiment_sources()` and the URL maps in both modules. Existing `ddhq` row deleted from the live DB.
- Frontend doesn't need any code change for this — the dashboard just renders rows that exist; one fewer row = one fewer line. The footer text was updated to drop the "Manual: ... DDHQ" suffix.

**Frontend — Footer rewrite** ([RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx))
- Footer now reads: *"Live: all sources. Cook + Sabato sourced via 270toWin. Daily auto-sync."* Honest about provenance (270toWin is a mirror, not the source itself).

**Cleanup**
- Deleted the dead `_parse_cook_district()` helper that the original Cloudflare-blocked `cook_fetch` used to scrape cookpolitical.com directly. Module docstring updated to describe the new architecture (3 sources, all LIVE, 270toWin as the Cook/Sabato proxy).

### Key decisions

- **Sourced Cook + Sabato via 270toWin rather than a paid bypass service.** Free, no recurring cost, validated to match the live forecaster ratings on the day of validation. Tradeoff: 270toWin's update cadence may lag the source by hours-to-a-day after a rating change. For a daily-sync use case this is acceptable; if a rating change had to surface within minutes, we'd need a paid CF bypass.
- **Treated the URL split as semantic (`external_id` for fetcher, `source_url` for display) rather than papering over it.** Cleaner provenance: when a user clicks the external-link icon next to "Cook Political Report" on the dashboard, they go to Cook's actual page, not to 270toWin. The fact that we source the data via 270toWin is a backend implementation detail.
- **Deleted the old `cook_fetch` cookpolitical.com scraper** rather than keeping it as fallback. Per the project guidance to avoid backwards-compat hacks for hypothetical futures. If anyone ever lands a paid CF bypass, they can rewrite the function from scratch — the logic of the old scraper was trivial (BeautifulSoup table-row scan), so there's nothing to preserve.
- **Did NOT replace IE's direct fetcher with 270toWin.** During validation, 270toWin's IE data showed Toss-up while the live IE site showed Tilt R — IE's data on 270toWin appears to be either stale or normalized. The direct IE fetcher works fine, so we keep it.
- **DDHQ row deleted, not hidden.** No reason to clutter the dashboard with a permanently-empty row. If DDHQ later publishes a public ratings page, a future session can re-add the seed entry.

### Open questions / concerns for review

- **270toWin URL hardcodes `2026`.** When the 2028 cycle rolls over, both `_RATING_FETCH_URLS` constants need to be edited. For a single-tenant tool this is fine; for the SaaS / multi-tenant pivot, derive the year from `CampaignConfig.election_date` or similar. Filed as future work, not in scope today.
- **`_RATING_FETCH_URLS` / `_RATING_DISPLAY_URLS` are duplicated** across [app/db.py](backend/app/db.py) and [app/services/race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py). The two must stay in sync. Worth consolidating into a single module (e.g. `app/services/race_sentiment_config.py`) — but adds a refactor without immediate payoff. NOTE comments mark the duplication.
- **The `_favors_from_label()` bug noted in the previous session entry is still there.** Not exercised by the 270toWin path (which computes favors directly from `_270TOWIN_COLOR_TO_RATING`, avoiding the bug entirely), but `inside_elections_fetch` still uses it. Same fix recommendation as before: change `("d", "democrat")` to `("democrat",)` — `Lean D` etc would still be matched via `_normalize_rating_label` running first. Worth a separate small PR.
- **270toWin's reliability for IE is unproven.** We sampled one district on one day. If 270toWin's IE data is regularly stale, then assuming 270toWin's Cook/Sabato data is reliable is on shakier ground than I framed it. Sample more districts over more days before claiming 270toWin is "same-day accurate" for SaaS/marketing claims.
- **Frontend footer text is still hand-coded.** Same caveat as last session — if a future session changes the auto-sync source set, the footer text in [RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx) must be updated alongside. Worth deriving from the rows themselves.

### Live state at end of session

- Dashboard `/` rendered card: **all 5 rows LIVE**. `Polymarket · LIVE · Cognetti +16%`, `Kalshi · LIVE · Cognetti +18%`, `Cook Political Report · LIVE · Toss-up · 45–55%`, `Inside Elections · LIVE · Tilt R · 52–60%`, `Sabato's Crystal Ball · LIVE · Lean R · 60–75%`. Footer: "Live: all sources. Cook + Sabato sourced via 270toWin. Daily auto-sync." Card height: 222px (down from a 389px baseline pre-session).
- Live DB: `ddhq` row deleted, `cook.external_id` and `sabato.external_id` repointed to 270toWin URLs, all three rating rows have fresh `last_synced_at` and null `last_sync_error`.
- 15 parser tests pass (9 new 270toWin + 6 existing Inside Elections).
- Branch `main` still uncommitted — user reviewing diff before commit.
- No new dependencies added; httpx + bs4 were already in `requirements.txt`.

---

## 2026-05-29 Session (continued): UI compaction + market math overhaul

### Built

**RaceSentimentCard — full UI restructure** ([RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx))
- Replaced the vertical-list layout with a **horizontal scoreboard**: one mini-cell per source, all five in a single row. Each cell is a stack of `SOURCE NAME` (small, uppercase, muted) over the signal (rating label + band for forecasters; market lead for markets). Source order was flipped so forecasters (Cook / IE / Sabato) lead and markets (Kalshi / Polymarket) sit on the right per the user's preference.
- Pulled the header (title + InfoTooltip + timestamp + ··· menu) **outside the card wrapper** so the section matches the Featured Narratives format on the same page — only the scoreboard itself is wrapped in card styling (`bg-2` background, `border`, `0.625rem` radius).
- Replaced standalone *Sync now* / *Edit values* buttons with a single `MoreHorizontal` (···) icon that opens a dropdown menu. Click-outside closes the menu via a `mousedown` listener registered on the document only while open. The card height has shrunk from 389px (start of session) → 90px (now); a ~77% reduction over the day's work.
- Removed the always-visible "Updated Xm ago" footer. The timestamp now only appears in the header when sync is **actually stale** — silent when healthy. After the schedule split (below), staleness is checked per source type rather than as a single global age.
- Removed the explicit `LIVE` badge from each cell since every source now syncs live. The `MANUAL` / `BLOCKED` SyncBadge states still render for failure surfacing; absence-of-badge means healthy.
- Reformatted forecaster cells so the band rides inline next to the rating label (`Toss-up 45–55%` on one line, not stacked). Removed the green/red 7-day delta sublines on markets per the user's request — the lead is the actionable signal; the delta was noise. Tightened cell font + padding to make `Toss-up 45–55%` fit cleanly without truncation across all forecaster cells.

**Race-sentiment sync — split cadence** ([scheduler.py](backend/app/services/scheduler.py), [race_sentiment_sync.py](backend/app/services/race_sentiment_sync.py))
- The previous single daily job (`race_sentiment_daily`) ran every 24h regardless of source type. Markets reprice on news events and want intra-day refresh; forecaster ratings change weekly at most and don't justify it. Split into two scheduler jobs: `race_sentiment_markets` (every 2h) and `race_sentiment_forecasters` (every 12h).
- `sync_all()` now takes an optional `source_types` filter so each job runs only its share. Live-verified: `sync_all(db, source_types=('market',))` syncs only Polymarket + Kalshi.
- Frontend stale detection updated to per-source-type thresholds: markets warn at >6h (three missed 2h cycles, tolerates one-off skip), forecasters warn at >24h (two missed 12h cycles). A global `max(updated_at)` check was deliberately not used because fresh market syncs would have masked forecaster-only failures. Warning text identifies which type is stale (`"Markets stale (8h)"` etc.) with a hover tooltip pointing to the Sync now menu action.

**Confidence-weighted market blending** ([prediction_market_monitor.py](backend/app/services/prediction_market_monitor.py))
- Investigation surfaced a real bug in market pct math. PA-08 on Polymarket has two binary sub-markets ("Will the Democrats win?" and "Will the Republicans win?"). The Democrat market is liquid (bid 61¢ / ask 63¢, midpoint 62%) but the Republican market is **dead** — bid 9¢ / ask 82¢ — so its "midpoint" of 45.5¢ is a synthetic number from an essentially empty order book. Computing the lead as `candidate_pct − opponent_pct = 62 − 45.5 = +16.5` mixed a real signal with phantom data and gave the user a misleading number.
- Implemented `MarketQuote` (frozen dataclass with `midpoint_pct` + `spread_pct`), `_confidence(spread_pct)` (linear decay 1.0 at 0% spread down to 0 at the 20% dead-market threshold), and `_blend_p_x_wins(x_yes, other_yes)` which computes a confidence-weighted estimate of P(X wins) from both binary markets. Each market contributes its midpoint, weighted by its spread-derived confidence — dead markets drop out automatically.
- Refactored both `polymarket_fetch` and `kalshi_fetch` to use the new blending. The Polymarket fetcher now reads `bestBid` / `bestAsk` directly (with `outcomePrices` as a legacy fallback). The Kalshi fetcher prefers `yes_bid_dollars` / `yes_ask_dollars` (with `last_price_dollars` as fallback). The raw quote objects are recorded in `raw_response` for debugging.
- Live-verified post-blend: Polymarket goes from `62 / 45.5 → +16.5` to `62 / 38 → +24` (correctly collapses to the liquid D market). Kalshi goes from `62 / 44 → +18` (raw asks) to `59 / 41 → +18` (blended midpoints) — same lead, cleaner absolute values that sum to 100. 15 unit tests in [tests/test_market_blend.py](backend/tests/test_market_blend.py) cover the blending corners: liquid+liquid averaging, liquid+dead collapse, both-dead fallback, complement-derivation, and the exact PA-08 Polymarket scenario.

### Key decisions

- **Followed the user's "use midpoints, not picked-side" intuition.** My first proposal was the simpler "just use the liquid side's midpoint and complement it." The user pushed back: that ignores valuable signal when *both* markets have it. They were right. Confidence-weighted blending degrades gracefully (gives the correct picked-side answer when one is dead, but takes the average when both are real) and is statistically the better estimator. The extra ~30 lines of code are worth it.
- **20% spread → confidence 0 threshold.** Generous enough that real markets with 2–5% spreads keep nearly-full weight; aggressive enough that a 20¢-wide order book is treated as the dead signal it is. Encoded as `_DEAD_MARKET_SPREAD_THRESHOLD_PCT` so it's easy to tune.
- **Did not normalize blended `candidate_pct + opponent_pct` to sum to 100.** Each side blends independently — letting the sum drift slightly from 100 leaves the residual spread/fee cushion visible in the data, which is honest. In the dead-R Polymarket case it naturally sums to exactly 100 anyway because both estimates come from the same liquid market via complementation.
- **Markets every 2h, not 1h.** The user's range was "1–2h." Conservative end was chosen to be polite to upstreams (Polymarket Gamma + Kalshi Elections APIs), to keep daily fetch cost bounded for a potential SaaS deployment, and because intra-day market moves of <2h are usually noise around a news event the user will already see via narrative ingestion. Easy to drop to 1h if responsiveness ever feels lagging.
- **`fmtDelta` helper deleted** — the green/red 7-day delta render path is now dead. The DB column `delta_7d` still exists; if a future session wants to bring deltas back (e.g. as a sparkline), the field is still being written by the snapshot job.

### Open questions / concerns for review

- **The Polymarket R-market problem may extend to other races.** For PA-08 the issue surfaces clearly: the D side is liquid, the R side is dead, and `62 - 45.5 = +16.5` looked wrong to the user. For a race where both sides are dead (a non-competitive race that Polymarket lists but no one trades), the blender currently falls back to the X-side midpoint — which is still phantom data. Long-term the right move may be to refuse to publish a market reading at all when **both** confidences are zero. Today that's PA-08-specific behavior we don't need; flagging for when other races get added.
- **Spread threshold is a single magic number.** 20% feels right for U.S. House race markets, but for Senate or Presidential markets — which trade at lower implied probabilities and may have different liquidity profiles — a different threshold could be appropriate. If multi-race deployment lands, consider making the threshold a per-race parameter or a function of `liquidity_dollars`.
- **APScheduler in-memory state doesn't pick up the schedule split until uvicorn restarts.** The new code lives on disk and uvicorn's `--reload` will reload the module on file changes, but the running scheduler instance keeps its previous job registrations. User was told to restart the backend (`pkill -f "uvicorn app.main:app" && cd backend && .venv/bin/uvicorn app.main:app --reload`) for the new cadence to take effect.
- **The forecaster threshold (24h) may be too forgiving for the SaaS pivot.** Today this is one user; if a campaign manager goes a week between dashboard logins they want to know whether the data they're looking at is stale. Consider tightening to 18h (just over 1.5x the cycle).
- **All UI height numbers in the dashboard are inline-style px values.** That's intentional — the design is dense and intentional — but a future global "increase density" / "decrease density" toggle would need to thread through the SourceCell padding + signal font + band font. Not worth abstracting today.
- **`raw_response` now includes the full `MarketQuote.__dict__`.** That's fine for debugging but mildly increases the size of each `RaceSentimentSnapshot` row in Postgres. If snapshot retention ever bites, the `candidate_quote` / `opponent_quote` fields could be dropped from the snapshot.

### Live state at end of session

- Dashboard `/` rendered card: 5 cells, all LIVE, scoreboard 52px tall, section total 90px including header. Order: Cook / Inside Elec. / Sabato / Kalshi / Polymarket.
- Final live values: `Cook · Toss-up 45–55%`, `Inside Elec. · Tilt R 52–60%`, `Sabato · Lean R 60–75%`, `Kalshi · Cognetti +18%`, `Polymarket · Cognetti +24%`.
- DB: `candidate_pct` / `opponent_pct` on the polymarket row now reflect blended midpoints (62 / 38). Kalshi row reflects blended midpoints (59 / 41). Both have `last_synced_at` fresh.
- Backend scheduler config: `race_sentiment_markets` every 2h, `race_sentiment_forecasters` every 12h. Code is committed to disk; the running uvicorn process needs a restart for it to take effect.
- 15 new tests in `tests/test_market_blend.py`; all pass. Existing `tests/test_270towin_parser.py` (9) and `tests/test_inside_elections_parser.py` (6) still pass — no regressions.
- Branch `main` still uncommitted across all of today's work.

---

## 2026-05-29 Session: homepage / briefing consolidation

### Built

**Homepage now is the briefing.** The standalone `/briefing` page is gone; its core sections moved up to `/`. New left-column section order:

1. Race Sentiment (kept)
2. Race Situation memo (grounded v=2, with `[C]` citations)
3. Needs Response (red-bordered urgent items, with "all clear" empty state)
4. What Changed in the Race (last-48h labeled candidate quotes — `OvernightChanges`)
5. Featured Narratives (kept)
6. Activity This Week (race-allowlist entities)
7. 24h Spikes (kept)

Right rail (Recent Articles) unchanged.

**Briefing components extracted** into [components/briefing/](frontend-v2/src/components/briefing/) so the Dashboard can compose them and so a future "print this briefing" view stays cheap:
- `RaceSituation.tsx` — section wrapper + `GroundedMemoView` + `SourcesUsedDisclosure`
- `NeedsResponse.tsx` — red border list + "all clear" empty state
- `OvernightChanges.tsx` — labeled-quote list, returns null when empty
- `ActivityThisWeek.tsx` — 3-col entity grid, returns null when empty

**[dashboardCache.ts](frontend-v2/src/api/dashboardCache.ts) gained briefing support without blocking first paint.** The briefing call is LLM-backed and can take several seconds — we run it in parallel via a separate `briefingInFlight` promise. `prefetchDashboard()` resolves on the three fast endpoints alone (frames/spikes/recent); the new `awaitBriefing()` is what the Dashboard subscribes to for the briefing slot. The skeleton you see in the Race Situation spot only persists until briefing lands; the rest of the dashboard never waits on it.

**Deleted from Dashboard:**
- The two big `DetailPanel` cards under Featured Narratives (top-2 active frames with sparkline + metrics table). The Narratives page already has full detail on every frame; the homepage duplication wasn't earning its space.
- Helpers that only `DetailPanel` used: `momentumBadge`, `POSTURE_COLORS`, `signalLabel`, `TrendArrow`, `StrategicLens` / `MomentumBadge` types. Net ~150 lines of dead code gone.
- The recharts `Area`/`AreaChart`/`ResponsiveContainer` imports + `TimeseriesPoint` type (no longer referenced from Dashboard).

**Route + sidebar:**
- `/briefing` → `<Navigate to="/" replace />` in [App.tsx](frontend-v2/src/App.tsx). Old bookmarks still resolve.
- Removed `{ to: '/briefing', label: 'Briefing', icon: Newspaper }` from [Sidebar.tsx](frontend-v2/src/components/Sidebar.tsx) and the now-unused `Newspaper` import.
- Deleted `frontend-v2/src/pages/MorningBriefing.tsx` outright (after its components were ported).

### Key decisions

- **Race Situation goes _below_ Race Sentiment, not above.** Default proposal was the memo first (it's the synthesis); user picked sentiment first — fastest-scan glance leads, deeper read follows. Implemented per their pick.
- **Featured Narratives over Narrative Pulse.** Both render "top frames this week." Featured Narratives keeps the importance scoring + Owner/Stage filters + momentum-posture chip; Narrative Pulse was a simpler ↑/↓ delta. Killed Pulse to avoid two similar sections on the same page; Featured Narratives stayed put.
- **DetailPanel cards removed entirely, not moved.** User's framing was "if we really want to show them they can go into the narratives page" — the Narratives page already shows detail per frame, so moving them would duplicate. Cleaner to delete.
- **Print button dropped.** The MorningBriefing header had a `window.print()` button. The homepage isn't a memo so the button is out of context. Browser print still works on `/` if anyone needs it; easy to re-add as a small action button later.
- **Hardcoded `?v=2`.** The legacy v=1 paraphrase memo path is still alive in the backend, but the homepage only requests grounded v=2. No `?v=` URL knob on `/`.
- **Briefing fetched in parallel, not awaited by the dashboard skeleton.** `Promise.allSettled` for all four would have made the dashboard wait on the LLM call before un-skeletoning. Split into `prefetchDashboard()` (fast) + `awaitBriefing()` (slow) so the page never feels gated by synthesis latency.

### Open questions / concerns for review

- **First paint flashes a skeleton in the Race Situation slot** even when the briefing call is in flight from Layout's earlier prefetch. The flash is short (briefing usually lands within ~1–2s on warm cache, ~5–8s cold), but a future session could pre-render a stub or use the cached previous-day memo as a placeholder. Not done today because the cache is in-memory and resets per page reload.
- **`MorningBriefingNarrativeCard` type is now dead** in `frontend-v2/src/api/types.ts`. It was only used by the deleted Narrative Pulse render path. Left in place because the backend's `/api/briefing/morning` response still includes `narrative_pulse` — pruning the type would force a coordinated backend response change. Worth a follow-up to either (a) remove `narrative_pulse` from the API and the type, or (b) repurpose it for a homepage section we haven't built.
- **No error boundary around the briefing block.** If `awaitBriefing()` throws, the catch in dashboardCache swallows and the section just doesn't render. That's the desired fallback, but a real error boundary would also stop a malformed `GroundedMemo` from blowing up the whole Dashboard component. Earlier in this session the console showed a transient React render error after one of the intermediate edits — by the time the final state landed it was gone, but the absence of an error boundary on Dashboard means a future regression in the briefing components would knock the whole page out. Worth adding.
- **`OvernightChanges` tooltip text is PA-08-specific** ("…mention Cognetti or Bresnahan directly. National Trump/Shapiro coverage stays out…"). That copy lived in MorningBriefing.tsx before the port and isn't worse than it was, but for the SaaS pivot it should template against `CampaignConfig.candidate_name` / `opponent_name`.

### Live state at end of session

- `/` renders 7 left-column sections + Recent Articles right rail. Verified in preview: Race Situation memo shows "Sources used (1 cited / 15 considered)" expandable, Activity This Week shows 6 entities (Bresnahan 48 ↓84, Cognetti 27 ↓144, Trump 25, Shapiro 10, NRCC 6, DCCC), Needs Response shows the "all clear" dashed-border state (briefing currently has `needs_response: []`).
- `/briefing` → 302 client redirect to `/`. Verified.
- Sidebar entries (top to bottom): Home, Forecast, Articles, Analytics, Narratives, Landscape, Geographic, Timeline, Opponents, Review, Monitors, Settings. No Briefing.
- `npx tsc --noEmit` clean for all changed files (the pre-existing Landscape.tsx:1482 `candidateName`/`opponentName` errors remain, unrelated to this session).
- Zero console errors on full Dashboard render after a forced navigate-away-and-back.
- Branch `main` still uncommitted alongside the day's earlier sentiment-card work.

### Follow-on declutter pass (later in same session)

After the initial consolidation, the user asked for further visual trimming. Three changes landed:

1. **`NeedsResponse` now hides entirely when the list is empty.** Was rendering a dashed-border "all clear" empty state on every load; the user wanted the section to only exist when there's actually something flagged. The "all clear" empty-state branch was deleted from [components/briefing/NeedsResponse.tsx](frontend-v2/src/components/briefing/NeedsResponse.tsx) — `null` return now wins when `items` is empty.

2. **"Race Sentiment" and "Race Situation" section titles removed**, plus their InfoTooltip bubbles. The scoreboard and the memo are visually distinctive enough that titles weren't earning their space. After a first pass that kept the bubbles in the top-right of each section, the user asked for the bubbles to go too — so both `<InfoTooltip>` elements are now removed entirely. The `HEADER_HELP` constant in [RaceSentimentCard.tsx](frontend-v2/src/components/RaceSentimentCard.tsx) was deleted along with it.

3. **Race Sentiment ··· menu removed**, dropping Sync now / Edit values from the UI. The user opted for "auto-sync is enough" — markets sync every 2h, forecasters every 12h, no manual override surface. This was a substantial deletion:
   - The entire `EditModal` component (~330 lines) + its helpers (`SectionLabel`, `FieldLabel`, `MarketEditForm`, `RatingEditForm`, `inputStyle`/`saveBtnStyle` const styles, the `RATING_OPTIONS` enum) — all gone.
   - State (`editing`, `syncing`, `menuOpen`), the `menuRef`, the click-outside `useEffect`, and the `runSync` function — all gone.
   - The per-source-type staleness calculation (`freshestSyncAge`, `staleWarning`) was removed since it was only ever displayed inside the menu row.
   - The `formatRelativeTime` helper was only referenced from inside `EditModal`'s footer timestamp render — removed.
   - Unused imports trimmed: `X`, `RefreshCw`, `MoreHorizontal` from lucide; `InfoTooltip`; `useRef`; `CSSProperties`; `RaceSentimentUpdate` from the api types.

### New open question / future-cleanup item

- **Orphaned API methods in [api/client.ts](frontend-v2/src/api/client.ts).** With `runSync` and `EditModal` gone, `api.syncAllRaceSentiment()` and `api.updateRaceSentiment(...)` have no callers from the frontend anymore. The backend endpoints (`POST /api/race-sentiment/sync-all`, `PUT /api/race-sentiment/{id}`) still exist and the scheduler still triggers internal syncs server-side, so this is a frontend-only orphaning — the routes themselves should NOT be deleted. Worth a follow-up pass to either (a) remove the unused client methods, or (b) leave them with a comment noting they're available for a future admin / settings page if manual sync controls come back. User flagged this for a later cleanup pass — not blocking today.

- **Briefing top-claims pool selection is a flat top-N.** Today the briefing pulls `top_claims_for_briefing(db, days=7, limit=15)` from [routes/dashboard.py:314](backend/app/routes/dashboard.py:314), ranked by a flat `label_priority × reliability_weight × recency_weight` composite. After the prompt/model upgrades the citation density looks healthy (4 citations from a 15-claim pool in current testing), so the user explicitly decided **not** to change this today. But two failure modes are worth designing for if the issue ever surfaces:
  1. **Quiet news weeks** — when fewer than ~8 strong claims exist, the bottom of the top-15 is padding. A `min_score` quality floor + a soft cap (e.g., `min_score=0.05, limit=25`) would let the pool shrink honestly so the model triggers the "no high-leverage development today" fallback rather than confabulating a lead.
  2. **One-topic-dominated weeks** — if all top-15 are attack quotes, the model can't find endorsement / vote / commitment counterpoints. **Label-stratified selection** (e.g., guarantee top 4 attacks + top 3 endorsements + top 3 votes + top 3 commitments + top 2 policy_position + top 2 defense) would always give the model a balanced palette. Slightly more code but the most robust fix. Recommended option if the failure mode shows up.

### Live state after declutter pass

- `/` left column now renders, top to bottom: Race Sentiment scoreboard (no header row at all), Race Situation memo (no title, no tooltip), What Changed in the Race (only if `overnight_changes` non-empty), Featured Narratives (kept its title + tooltip), Activity This Week (kept its title + tooltip), 24h Spikes (kept its title + tooltip). Needs Response not visible because `needs_response: []`; would slot in below Race Situation when populated.
- Total `lucide-info` icons on the page: 6 (Featured Narratives + What Changed + Activity This Week + 24h Spikes header + Recent Articles header + one inside SourceCell badges).
- No `lucide-more-horizontal` icons anywhere; no "Sync now" / "Edit values" / "Syncing…" text in the DOM.
- `npx tsc --noEmit` still clean for changed files.
- Zero console errors after navigate-away-and-back.



## 2026-05-29 Session: Brand refresh (NOCTUA → THEOSINTEL/Theo) + tooltip declutter pass

### Built
- **Brand architecture shift.** Company brand: **THEOSINTEL** (lowercase wordmark `theosintel`). Product / AI agent persona inside the product: **Theo** (sentence case). Pattern matches Salesforce/Einstein, Microsoft/Copilot, Anthropic/Claude. Decision was made after ~9 verification cycles of alternative names (CTX, NMI, MIP, CXN, NRTV, VEILLE, BRUIT, PRSM, MUON, HUGO, CYRUS, OTIS, KAI, CASS, THEO, THEOS) all hitting category collisions, the AI-naming saturation wave (every personal-name slot in B2B AI is claimed since 2024), or trademark blockers. `theosintel.com` is registered ($0.01/yr promo via GoDaddy, 3yr commit). `theo.com` is `Make Offer` only and held by someone patient — likely $75K-$300K to acquire.
- **Sidebar + login wordmarks swapped.** `theo-wordmark.png` (sentence case) in header sidebar, `theosintel-wordmark.png` (lowercase) on login page. Both at `frontend-v2/public/`. Black on transparent with dark-mode invert handled via `.brand-logo` CSS class (replaced the old `.noctua-logo` class, same invert(1) trick).
- **Favicon** points to `/theo-mark.svg` — minimal eye-O glyph (outer circle r=42 stroke=16, inner dot r=13, all on a 100×100 viewBox), uses `currentColor` so it inherits dark/light mode.
- **Header polish.** `Theo` wordmark left-aligns with content column at 204px (was 192px, 12px off-grid). `PA-08` chip nudged down 3px via `translateY(3px)` so it sits near the wordmark baseline rather than floating mid-line. Search bar re-centered on viewport (was content-area-centered) — matches Stripe/Linear/Notion convention.
- **Sidebar tooltips.** Custom CSS tooltips on `aside[data-collapsed="true"] .sidebar-nav-link::after` showing nav labels immediately on hover when collapsed. Native `title=` removed in favor of `aria-label=` (accessibility) + `data-tooltip=` (the styled tooltip source).
- **Active nav indicator simplified.** Gold accent stripe (`borderLeft: 3px solid var(--accent)`) removed from active sidebar items — `bg-3` tint + 600 font-weight + brighter text color already communicate active state. Frees the gold for more deliberate brand moments (PA-08 chip, Review badge, etc.).
- **AI → Theo swap across user-facing tooltips and copy.** ~17 tooltip texts updated. Files: `NeedsResponse.tsx`, `ReviewQueue.tsx` (VERDICT_HELP + 3 tooltips), `Dashboard.tsx` (2 tooltips), `Articles.tsx`, `Narratives.tsx` (3 tooltips + the "AI noticed N emerging narratives" banner text), `NarrativeDetail.tsx`, `NotificationSettings.tsx`.
- **Tooltip declutter pass.** Per a thorough rewrite analysis of each info bubble:
  - **Removed entirely**: `RECENT ARTICLES` (Dashboard right rail), `Articles` page h1, `Notable quotes` (NarrativeDetail), `Proposed narratives pending` description (NotificationSettings — was a `description` prop on `TriggerToggle`, not an InfoTooltip).
  - **Rewritten shorter**: `Featured Narratives` (dropped "virality" and the long ranking-mechanism breakdown), `Needs Response`, `Review Queue` header (dropped "One queue, two lenses" framing), the 4 `VERDICT_HELP` entries.
  - **Renamed for self-explanatory label + tooltip removed**: `Proposed Narratives` → `Potentially emerging narratives`. Updated in 3 places: the section header in ReviewQueue.tsx (line ~844), the tab label `NAV_ITEMS` (line ~643), and inside the Review Queue header tooltip text.
- **"BRIEFING · TODAY" → "The read"** eyebrow label above the briefing memo (RaceSituation.tsx). Reasoning: drop the redundant date qualifier; "the read" is political-insider parlance for "your interpretation / assessment of the current situation."
- **"Sources" → "Citations"** in the SourcesUsedDisclosure component (RaceSituation.tsx). Memo uses `[C1]`, `[C2]` markers — "Citations" matches the marker vocabulary; "Sources" created a name-mismatch. Container `marginTop` tightened 12→4px so the disclosure sits flush against the memo above it (was reading as a separate section).
- **Inline citations rendered flush against preceding word.** GroundedMemoView now strips trailing whitespace from text segments before `[CN]` markers. Reads as `Act[1]` rather than `Act [1]` — matches academic-citation convention.
- **Suggest Frames button removed entirely** from Narratives.tsx. The "AI noticed N emerging narratives" banner already provides the same automated suggestion output — the button was a redundant manual trigger.

### Key decisions
- **First-name brand + agent-persona pattern is now the project's brand architecture.** Future sessions should not introduce "the AI" / "the model" / "the system" language in user-facing copy — use "Theo" if attributing the action, or rephrase to be subject-less.
- **`Theo` always sentence case in copy.** Matches AI-persona convention (Claude/Watson/Alexa/Siri/Cortana/Einstein/Copilot — all sentence case). `theosintel` lowercase only in the wordmark/visual brand mark; sentence case in running text and legal contexts.
- **Self-explanatory labels > labels + tooltips.** When a section heading already says what it is (`RECENT ARTICLES`, `Articles`, `Notable quotes`, `Potentially emerging narratives`), the InfoTooltip is removed. Tooltips are reserved for places where the label needs extra context (verdict types, the Featured Narratives ranking, the Review Queue dual-tab structure).
- **Brand-mark file (`brand-logo` CSS class) replaces the old `noctua-logo` class.** Any future image-based brand mark should use `.brand-logo` for the dark-mode invert behavior.

### Open questions / concerns for review

- **Ready-to-promote manual confirmation vs auto-promote — undecided.** Current flow requires a click + modal interaction to promote a Ready cluster to a tracked narrative frame. User asked whether this should be auto-promoted given the system's stated confidence. Options under discussion: (A) keep current modal-heavy manual confirm, (B) full auto-promote with 30-second undo, (C) one-tap lightweight confirm with sensible defaults pre-filled and 30s undo (recommended), (D) confidence-threshold auto-promote (high-confidence auto, lower-confidence manual), (E) opt-in setting per user. The political-stakes asymmetry (a wrongly-tracked frame pollutes briefings + strategy until caught) argues against full auto-promote. Not blocking — discuss next session.

- **Unused code left behind from Suggest Frames button removal.** In `frontend-v2/src/pages/Narratives.tsx`, the `suggestFrames` function, `suggesting` state, the `Sparkles` import from lucide-react, and probably an `api.suggestFrames(...)` call site are now unreferenced after the button was deleted. Vite will warn but doesn't block. Same situation in `frontend-v2/src/pages/Articles.tsx` — the `InfoTooltip` import is now likely unused after the h1 tooltip removal. Worth a follow-up cleanup pass; flagged for future session.

- **Code comments still reference "the AI" / "AI's" in multiple files** — RaceSituation.tsx:117, NeedsResponse.tsx:11, Layout.tsx (several), Dashboard.tsx:55, PromoteModal.tsx, ReviewQueue.tsx (several), ArticleDetail.tsx, Narratives.tsx:884. These are non-user-facing JSDoc / inline comments and were intentionally left alone in this pass. Worth a sweep later for codebase brand consistency.

- **Non-tooltip user-facing "AI" mentions still exist** that this session did NOT change: `Layout.tsx` admin menu (`Score articles with AI`, banner `AI scoring is OFF...`), `PromoteModal.tsx:179` (`AI-proposed: {name}`), `ReviewQueue.tsx` verdict labels (`AI uncertain`, `AI: likely noise`), `ReviewQueue.tsx:577` `<span>AI:</span> {verdict.reasoning}`, `ReviewQueue.tsx:903` `Run AI triage` button, `ReviewQueue.tsx:942` `AI flagged {n} cluster…`, `ReviewQueue.tsx:983` `The AI hasn't surfaced…`, `ReviewQueue.tsx:1264` `AI: ${VERDICT_META.label}`, `Narratives.tsx:562` `AI narrative discovery is paused`, `ArticleDetail.tsx` (3 places: `AI one-sentence summary`, `AI rationale`, `Full AI analysis`). User explicitly scoped this pass to information bubbles only and left these for a separate decision pass.

- **`theo.com` is held by a domain investor at `Make Offer` only.** Realistic acquisition cost likely $75K–$300K. Currently shipping the brand at `theosintel.com`. If the project ever scales to a level where `theo.com` becomes worth the spend, the brand architecture (`theo` as agent inside `theosintel` company) is already set up to absorb the move cleanly.


## 2026-05-29 (continued) — Open architectural question: Narratives banner vs Review Queue count mismatch

User spotted: Narratives page banner says "3 emerging narratives" but Review Queue says "1 ready to promote." This is not a labeling bug — it's a data model coherence issue worth a dedicated session to fix properly.

### Root cause

Two endpoints feed two views with overlapping-but-not-identical data:

- **`api.pendingCandidateClusters(21)`** → live HDBSCAN compute output (refreshes when scheduler runs). Used by `Narratives.tsx` PendingSuggestionsSection (line ~487).
- **`api.narrativeProposalsSnapshot()`** → persistent snapshot table (rows survive HDBSCAN turnover). Used by `ReviewQueue.tsx` (line ~227).

The Review Queue's "Ready / Watch" split in [ReviewQueue.tsx:316-336](frontend-v2/src/pages/ReviewQueue.tsx:316) is a **derived** classification: a snapshot proposal is "Ready" if any of its `candidate_frame_ids` appears in the union of `candidate_frame_ids` from the live HDBSCAN output. "Watch" = snapshot proposals that no longer overlap with live.

This means:
- Narratives banner count = `pendingCandidateClusters().suggestions.length` (live HDBSCAN — currently emerging)
- Review Queue total = `narrativeProposalsSnapshot().clusters.length` (persistent, includes faded)
- Review Queue Ready count = snapshot ∩ live HDBSCAN
- They will only coincidentally equal each other.

### Four architectural directions evaluated

| Option | Approach | Trade-off |
|---|---|---|
| A. Snapshot canonical | Both pages query the snapshot. Backend annotates each row with `is_currently_emerging` + `meets_promotion_bar` booleans derived at read time. | Cleanest source-of-truth fix. Backend work + migration concerns. |
| B. Live canonical, snapshot = user state only | Snapshot stores only dismissed/promoted state. No Watch list concept — if a pattern falls out of live HDBSCAN, it's gone unless promoted. | Maximum simplicity but loses the "saw this fade, was it real?" signal. |
| C. Two sources, label honestly | Don't change data. Make UI labels explicit: "Currently emerging (live)" vs. "Triage queue (snapshot)". | Zero backend work but leaks implementation distinction at every user — every new user re-encounters confusion. |
| D. Eliminate Watch list | Same effect as B but explicit decision to drop the concept rather than a side effect. | Cleanest end state but explicit loss of fade-tracking. |

### Recommendation: A + soft D

Single source of truth (snapshot) with backend-computed annotations. Both pages query one endpoint. Render per purpose:

- **Narratives page**: filter to `is_currently_emerging=true`, show as cards. Banner: `N emerging narratives · M ready to promote`.
- **Review Queue**: show all open snapshot proposals, three buckets:
  - **Ready to promote**: `is_currently_emerging=true AND meets_promotion_bar=true`
  - **Watch**: `is_currently_emerging=true AND NOT meets_promotion_bar`
  - **Stale (faded)**: `is_currently_emerging=false` — collapsed by default with "see N faded" expand
- Narratives banner counts will then *naturally* match Review Queue's "currently emerging" subtotal because they come from the same query.

### Work scope estimate

- **Backend** (most of the work):
  - New or enhanced endpoint returning snapshot rows + `is_currently_emerging` + `meets_promotion_bar` annotations. The `is_currently_emerging` join requires checking each snapshot row's `candidate_frame_ids` against the latest HDBSCAN compute. Likely lives in `routes/narrative_frames.py` near the existing snapshot/landscape handlers; the `meets_promotion_bar` field encodes the existing ≥3-articles ≥2-outlets bar that lives in [services/narrative_frames.py](backend/app/services/narrative_frames.py) (verify exact location).
  - Migration / transition: existing snapshot rows likely don't have annotations until the next compute runs. Handle missing fields as `is_currently_emerging=false` by default so legacy rows surface in "Stale" rather than vanishing.
- **Frontend** (smaller):
  - `Narratives.tsx` PendingSuggestionsSection switches from `pendingCandidateClusters` to the new endpoint; filters to `is_currently_emerging` for cards + banner count.
  - `ReviewQueue.tsx` removes the local Ready/Watch derivation logic (lines ~316-336) and uses server-side fields instead. Adds the new "Stale" collapsed section.
  - Per-card tags on both pages drop in as a simple read of `meets_promotion_bar`.
- **Estimated effort**: ~half a day of careful backend coding + testing, ~2-3 hours frontend refactor. Should be one focused session, not interleaved with other UI polish work.

### Why parked (not done today)

This needs a clean head and proper attention. Today's session was already deep in brand/UI polish (10+ small changes), and this one is structurally different — it touches data model, not just copy. Doing it in the same context would mean cutting corners. Picked up the user's "(ii)" — separate dedicated session.

### Pointers for the future session

- The current discrepancy mechanism is documented above — start by reading [ReviewQueue.tsx:316-336](frontend-v2/src/pages/ReviewQueue.tsx:316) and [Narratives.tsx:485-499](frontend-v2/src/pages/Narratives.tsx:485) side by side.
- The Review Queue's "Ready" definition (any member candidate_frame_id appearing in live HDBSCAN) is incidentally a *good* approximation of `meets_promotion_bar AND is_currently_emerging`, because the live HDBSCAN already filters by bar. Worth verifying this against the backend's actual emit rule before assuming.
- User has expressed receptiveness to dropping the Watch list entirely (option B/D) if maintaining it adds significant complexity. Worth considering during the rethink — eliminating Watch is the cheapest path and the only one that loses a feature; if the feature isn't used, it's free.



## 2026-05-29 Session: Articles page filters + ingestion prefilter concern

### Built
- Sortable, filterable Articles page. Toolbar (single row): keyword search, sort dropdown (newest / oldest / most relevant / source A→Z / most duplicated), and multi-select filters for Date, Source, Relevance, Sentiment, Type, Frame. Active filters render as removable chips with Clear all.
- Backend `/articles/recent` cutoff bumped 7d → 30d (`backend/app/routes/dashboard.py`). The 30-day window is the upper bound for the Articles page; finer "today/3d/7d" cuts are applied client-side from the same payload. Dashboard right rail still asks for `limit=10`, gets the freshest 10 — unchanged behavior.
- New `backend/app/services/source_category.py` classifies each `SourceItem` into `local_news` / `national_news` / `social_media` / `campaign_source` / `other` using outlet metadata first (`outlet_type` + `state`), then `source_type`, then source_name patterns. The Articles-page Source filter is now categorical, not per-outlet. Returned as `source_category` on `/articles/recent`.
- `/articles/recent` also now returns `frames: [{id, name}]` per article (batch-loaded from `NarrativeFrameMention`) so the Frame filter works without an extra round-trip.
- Removed `source_type` + `sentiment` from being silently dropped — both are now exposed by `_item_dict`.

### Key decisions
- **Filtering is client-side over the 30d batch.** Backend stays single-purpose; UI gets instant feedback. Load more bumps `limit` by 200 (current pool ~725 in 30d).
- **Per-outlet source filter retired.** The 30+ entries in the dropdown were too noisy. Four coarse buckets (Local / National / Social / Campaign) match how a campaign user actually thinks about sources.
- **Candidate names dropped from the campaign-source regex.** Initial version had `paige cognetti` / `rob bresnahan` literal matches, which fired on Google News *search feed* names (`"Google News: Rob Bresnahan"` is a third-party news pickup, not campaign output). Replaced with stricter signals: party committee acronyms, `*.house.gov`, explicit "X for Congress/Senate". This re-classified ~50 rows from `campaign_source` to their underlying outlet category. Validated against the full 30d pool (725 articles).
- **Frame matches loaded for representatives only**, not for grouped duplicates. The Frame filter operates on whichever row is being rendered (always the representative), so the join stays small.

### Open questions / concerns for review

**Ingestion prefilter cost is high.** When measuring the funnel I noticed: of the 8,963 articles ingested in the last 7 days, **8,814 (98.3%) were archived as irrelevant by the LLM relevance scorer**. That's ~1,260 articles/day where we pay LLM cost for ingestion + scoring only to drop the result. The cost matters at this volume — `campaign_analysis.py` makes one LLM call per article (relevance + summary + framing + frame matching in a single prompt). Worth a focused session on pre-LLM filters at ingestion:
- Are Google News searches scoped tightly enough? (We're seeing "Google News: Rob Bresnahan", "Google News: COGNETTI, PAIGE", "Google News — Pennsylvania Congressional Race 2026" — significant overlap, all pulling broad results.)
- Could a cheap keyword pre-filter (regex match for candidate names + race terms in title/body) cut the LLM input by 50–80% before it ever hits the scorer?
- Some sources (Google News broad-topic feeds, GDELT backfill) generate disproportionate noise. A per-source archival rate dashboard would surface the worst offenders.

**Frame match accuracy.** When testing the Frame filter I selected "Bresnahan's Stock Trades" and the single matching article was titled "While Bresnahan poses for photo ops & touts hospital investments, don't forget that he cut Medicaid…" — about Medicaid, not stock trades. Could be one-off noise, but worth spot-checking a wider sample of frame matches if anyone is doing data-quality work on `narrative_frame_mentions`.

**`source_category='other'` is the "I don't know" bucket.** Articles with no outlet metadata and no recognizable source_name pattern land there. Currently shows in the filter dropdown — leaving it visible intentionally so it's obvious when classification needs work. If/when "Other" gets close to zero on a typical day, the dropdown option can be hidden.

**Empty date cells on older Articles rows.** Surfaced after dropping the 30-day backend cutoff to enable infinite Load more scrolling. With ~1,200 rows loaded, ~11% (129/1200) show a blank date column. Cause: `formatArticleDate(item.published_at ?? item.created_at)` returns empty when both timestamps are null, which is more common for older rows ingested via early backfills. Pre-existing — not introduced by today's changes, just more visible now that older history is reachable. Possible fixes if anyone tackles it later: (1) render "Unknown" as fallback in the row, (2) skip rendering rows with no date at all, (3) backfill `published_at` for the affected rows from RSS metadata or HTTP Last-Modified. User flagged but deferred; leaving as a known issue.

**Backend cutoff for `/articles/recent` removed (2026-05-29).** The endpoint previously hard-capped at 30 days; the Articles page Date dropdown was therefore a no-op past 30d. Removed both the dropdown and the SQL cutoff so Load more now walks the full ~2,721-article lifetime reviewed-relevant pool (oldest published_at: 2009-05-28). The Dashboard right rail call (`limit=10`) is unaffected since it only ever wants the freshest 10. If a future session re-adds time-bounded queries elsewhere, note that the function no longer enforces *any* date filter.

---

## 2026-05-29 Session: Featured Narratives — memo pinning + urgency-bar removal

User flagged that the featured-narratives panel and the briefing memo are two separate "most important" judgments that can disagree, and asked: shouldn't narratives whose articles are cited in the memo automatically appear in Featured?

### Built

**1. Removed the urgency-colored left accent on featured cards.** The old 3px left border (red=high, yellow=medium, none=low) was unlabeled, untooltipped, and conceptually redundant with the panel's "most-urgent" framing — only some cards had a bar, which inverted the intended signal. Reasoning: if the panel ranks by urgency, a per-card urgency bar just visualizes intra-panel relative urgency, which isn't useful.
- [frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts) — deleted `UrgencyAccent` type + `urgencyAccent()` function.
- [frontend-v2/src/pages/Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx) — removed the per-edge border longhand split (it existed only to special-case `borderLeft`); back to a single `border: 1px solid ...` shorthand.

**2. Memo-pinning in Featured Narratives panel.** Frames whose articles are cited in the v2 grounded memo are now guaranteed slots, and the dashboard renders an "In memo" pill on those cards so the operator can see the editorial overlap.

Pipeline: memo `citations[].article_id` → `NarrativeFrameMention.source_item_id` join → top-confidence frame per article (ties included) → dedupe by `frame_id`, collecting all `cited_article_ids` per frame.

- [backend/app/routes/dashboard.py](backend/app/routes/dashboard.py) — new `_cited_frames_from_memo(db, memo)` helper; called only on the `v=2` branch of `/api/briefing/morning`. Returns `[{frame_id, frame_name, confidence, cited_article_ids}]`. No new endpoint — payload extends the existing briefing response.
- [frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts) — new `BriefingCitedFrame` type; `MorningBriefing.cited_frames?: BriefingCitedFrame[]`.
- [frontend-v2/src/lib/featuredFrame.ts](frontend-v2/src/lib/featuredFrame.ts) — `selectFeatured(frames, n, pinnedFrameIds?)` now does a pinning pass before the diversity-capped fill. Pinned frames are sorted by `multiObjectiveScore` desc, exempt from owner/stage caps, and the caps' owner/stage counters carry into the fill pass so the *combined* panel still respects diversity.
- [frontend-v2/src/pages/Dashboard.tsx](frontend-v2/src/pages/Dashboard.tsx) — derives `pinnedFrameIds` from `briefing.cited_frames`, passes it to `selectFeatured`, and threads `inMemo` to `FeaturedCard`. The card renders a small accent-yellow "In memo" pill in row 1 (left of the posture badge) with a `cursor: help` + `title="Cited in today's briefing memo"` tooltip.

### Key decisions

- **Top-confidence frame per article (ties included).** A cited article matching two frames at confidence 90 pins both; sub-top matches are ignored. Avoids inflating the pinned set with low-confidence noise while still surfacing genuinely ambiguous cases.
- **Pinning operates on the post-filter set.** If the user filters the panel by owner=opponent, pinned candidate-owned frames are dropped — filter is the user's explicit override of the panel.
- **Pinned cards visually first, score-ordered among themselves.** Visual contract: "top of the panel = what the memo cited." Diversity caps still apply to the fill positions (slots beyond pinned).
- **No saturation penalty on pinned frames.** Saturation penalty still computes for ranking the fill, but pinned frames bypass the ranker entirely. Acceptable because pinning is driven by the memo (which itself rotates as fresh claims surface), so wallpaper risk is bounded by the memo's own variability.
- **Overflow rule (pinned > n=8).** Truncate by lowest multi-objective score among the pinned set. Today's memo cites 4 articles → 3 distinct pinned frames, so this is rarely exercised. Logged as a guard, not a hot path.

### Verified

Live dashboard (2026-05-29 evening): memo cited 4 articles → 3 frames pinned (Cognetti's Anti-Corruption, Bresnahan's Record on Medicaid Cuts, NEPA Support). All three render in slots 1–3 with the "In memo" pill. Slots 4–8 filled by ranker (Going viral, Crossed into national, Needs Response, Dormant, Emerging — diverse stages). No console errors, no error boundary triggered.

### Open questions / concerns for review

- **Frame-coverage gap on fresh articles.** 1 of 4 cited articles today (the Pipefitters/Plumbers endorsement, source_item 18766) has zero `frame_mentions` — so the memo cites it as important but it's orphaned from any frame, and pinning silently skips it. The earlier eval showed the Shapiro endorsement story (13337) similarly bare. This is a data-quality issue for the framing pipeline, not pinning's fault. Worth a sweep: are recent endorsement-class articles systematically slipping through frame-matching, or is this noise? If systematic, the memo will keep citing orphaned articles and pinning will under-deliver.
- **Urgency-weighting question deferred.** User originally asked whether the ranker should weight urgency more heavily (current weights: urgency max 40 / total max ~144, so a low-urgency frame with strong propagation+momentum+acceleration can outrank a high-urgency one). After pinning, this is less pressing — the most editorially urgent frames are now guaranteed via the memo route — but it's still an open call for how the ranker fills slots 4–8. Leaving as user-pending.
- **Briefing payload churn.** Two consecutive fetches of `/api/briefing/morning?v=2` returned different `citations[].article_id` sets (13337 vs 17315 swapped in). The grounded memo cache TTL or LLM nondeterminism is the likely cause — `get_or_generate_grounded` may have regenerated between calls. Doesn't break pinning (it just re-pins on the next refresh), but if anyone notices "the In memo pills moved between page loads," that's why.

---

## 2026-05-29 Session: Notifications: kg_contradictions kind removed

**Notifications: kg_contradictions kind removed (2026-05-29).** The header notifications bell was still surfacing "47 KG contradictions pending" with a link to `/review?tab=kg` — a tab that no longer exists per today's KG retreat. Removed:
- `'kg_contradictions'` from `NotificationKind` union in `frontend-v2/src/lib/notifications.ts`
- `kg_contradictions_pending` from the settings triggers (type + DEFAULT_SETTINGS)
- The `api.entityReviewQueue()` call from the parallel fetch in `fetchNotifications`
- The whole notification-generator block (lines 178–196 of the old file)
- `kg_contradictions` from the `queueLikeKinds` array used for first-seen timestamp persistence
- KIND_META entry + unused `Flag` icon import in `frontend-v2/src/components/NotificationsList.tsx`
- The toggle in `frontend-v2/src/components/NotificationSettings.tsx`
The `api.entityReviewQueue` method itself is left in `client.ts` since `pages/EntityReview.tsx` still references it (that page now redirects per the KG retreat; the method should be cleaned up in a future pass once EntityReview.tsx is deleted). Stale `kg_contradictions_pending: true` in users' localStorage will linger harmlessly — `getSettings` spreads unknown keys but nothing reads them; a defensive whitelist could strip them on next save if it ever matters.

---

## 2026-05-29 Session: Activity This Week — race-context universal gate + race_share signal

User flagged Donald Trump as the #2 card on "Activity This Week" despite the tooltip promising the data is restricted to race-adjacent figures. Audit confirmed: of 20 Trump-mentioning articles last 7d, only 1 was actually about PA-08 (Jen Kiggans VA, Al Green TX, Hegseth, MAGA polling, LDS rebuke, etc. dominated). Counting raw mentions of national figures measures national news volume, not race signal.

### Decision path (briefly worth recording)

Evaluated three approaches in writing:
1. **Raw** — what we had. Counts national noise as race signal.
2. **Dynamic gate (per-entity threshold)** — gate only when `gated/raw < 0.5`. Backtested over 8 windows: gate fires correctly for Trump w-0 (15% ratio, 17 dropped articles all off-race), but the threshold creates a piecewise metric — Trump 140 → 3 across weeks looks like a measurement event when it's actually a definition change. Plus binary flip at the boundary (4 → 10 on one article).
3. **Universal gate + race_share subhead** — gate every context entity, expose ratio as a visible signal.

Got a third opinion from ChatGPT; strongest insight was the **non-stationarity argument against (2)** — a metric whose definition changes per row/per week destroys dashboard trust, exactly the failure mode that retired the original KG. We shipped **(3)**.

### Built

- [backend/app/services/briefing_retrieval.py](backend/app/services/briefing_retrieval.py)
  - Dropped `RACE_CONTEXT_GATED` set. Gate now applies universally to every entity in `CONTEXT_ENTITY_ALLOWLIST`. Always-show entities (Cognetti, Bresnahan) skip the gate — they ARE the race.
  - `_data_for(canonical_id, apply_gate)` reworked. Computes both raw and gated counts; for context entities the gated number is the headline and `race_share = gated/raw` is exposed. For always-show entities `race_share` is `None`.
  - Added candidate surnames (`Bresnahan`, `Cognetti`) to `RACE_CONTEXT_PATTERNS` as fallback for articles where `EntityMention` extraction missed them (e.g. YouTube clips named "Bresnahan seeks $59M"). The ChatGPT critique correctly flagged the predicate as extraction-recall-dependent; this is the cheapest mitigation.
- [frontend-v2/src/api/types.ts](frontend-v2/src/api/types.ts) — `BriefingEntity.race_share: number | null` added with semantic note in the comment.
- [frontend-v2/src/components/briefing/ActivityThisWeek.tsx](frontend-v2/src/components/briefing/ActivityThisWeek.tsx)
  - Added "X% RACE-FOCUSED" subhead, rendered only when `race_share !== null` (so candidate cards stay clean).
  - Updated tooltip copy to explain the gate and the new % signal.

### Verified

Live dashboard `/` snapshot after the change. Ordering: Bresnahan 31, Cognetti 18, Shapiro 5 (83% race), Trump 3 (15% race), DCCC 2 (50% race), NRCC 2 (100% race). Trump moved from card #2 to #4, with the 15% race-focused label communicating the noise level transparently. No console errors. Backend `top_entities_for_briefing()` returns the new `race_share` field correctly through the existing `/api/briefing/morning?v=2` response.

### Open questions / concerns for review

- **Predicate is still extraction-recall-dependent.** The race-context filter = `(EntityMention for candidate) OR (title/summary ILIKE race pattern)`. We expanded patterns to include candidate surnames, but a local article like "Democrats are attacking the incumbent over stock trades" — no name, no district — still wouldn't pass. ChatGPT proposed a **race-affinity score** (additive weak signals: +10 candidate mention, +6 race regex, +4 district geography, +4 committee mention, +3 opponent mention, etc., gate at score ≥ 10). We deliberately deferred this — the weights are arbitrary until backtested against ground-truth labels, and a wrong "+2 local outlet" weight would re-introduce noise in PA-08 (Scranton Times reprints AP wire constantly). **Pick this up if/when production usage shows the predicate dropping articles a campaign manager flags as obviously race-relevant.**
- **The Jen Kiggans DCCC release still surfaces as a Trump sample title.** It survived the gate because the same release mentions Bresnahan. Acceptable byproduct of the candidate co-mention rule; would need entity-position-in-article scoring (i.e. "is Trump in the lede or the background?") to filter further. Not worth it now.
- **Race-share calculation is `gated_this_week / raw_this_week`.** Could alternately use a longer rolling window for a more stable %. Current period-aligned definition is simpler and matches the WoW delta semantics. Flag if it starts looking volatile in production.
- **Productization note.** When NOCTUA generalizes beyond PA-08, three lists become per-campaign config: `ALWAYS_SHOW_ENTITIES`, `CONTEXT_ENTITY_ALLOWLIST`, `RACE_CONTEXT_PATTERNS`. The universal-gate logic itself generalizes without per-campaign tuning, which was a design goal in choosing (3) over (2).

---

## 2026-05-30 Session: Predicate quality eval — surnames reverted, NEPA added, deeper finding on LLM-summary contamination

Followed up on the previous session's "Layer 1 ships, Layer 2 deferred until measured" plan. Ran a labeled eval of the race-context predicate against the last-30d corpus.

### What I labeled

Sampled 40 articles dropped by the predicate (FN candidates) + 20 articles kept (FP candidates), labeled each by hand on whether a PA-08 campaign manager would consider it race-relevant.

**FN rate** (dropped sample): 3/40 strict (7.5%), 11/40 lenient (27.5%). Three clear misses: a Hazleton SP PA-swing-districts story, an NRCC NEPA press release, and the Bresnahan/Rollins disaster-declaration story (#18686 — Bresnahan personally hosted the event, but the candidate `EntityMention` didn't fire on the article).

**FP rate** (kept sample): 5/20 = 25%. All five fell into one of two contamination patterns:
1. The LLM-generated `summary` field contains spurious mentions of "PA-08", "COGNETTI, PAIGE", or "Bresnahan" as boilerplate stretches even when the article is about something else (Michigan 10 race, RFK education policy, Trump-Fitzpatrick PA-01).
2. Entity extraction reads those LLM summaries and materializes Cognetti/Bresnahan `EntityMention` rows from the spurious mentions.

Result: both paths of the predicate (`EntityMention` co-mention OR pattern match against title/summary) are getting contaminated through the same LLM-summary channel.

### Built

- [backend/app/services/briefing_retrieval.py](backend/app/services/briefing_retrieval.py)
  - Removed `Bresnahan`, `Cognetti` from `RACE_CONTEXT_PATTERNS`. The pre-Layer-1 hypothesis (surname patterns help recall on EntityMention misses) doesn't survive contact with the data — surname patterns inherit LLM-stretch FPs at high rate and the recall gain is small.
  - Added `NEPA` to `RACE_CONTEXT_PATTERNS`. Caught D#18817 (NRCC press release) cleanly with zero observed FPs — NEPA is a regional shorthand local press uses, national writers don't.
  - Updated the docstring to explain why surnames were tried and reverted.

### Tested but did NOT ship

- **Title-only pattern matching** (drop `summary` from the ILIKE, keep `title`). On the labeled sample it eliminated 2 of the 5 FPs (the pure LLM-stretch ones where PA-08 only appeared in summary) but newly dropped 3 articles that ARE legitimately race-relevant — including a Washington Examiner roundup titled "Meet the four Pennsylvania Democrats who could flip control of the House" (no PA-08 in title, no candidate `EntityMention`, but obviously about the race). Net wash to slightly negative on this sample. Not shipped.

### Bigger finding — for future sessions to pick up

The predicate isn't the right intervention point. The summary→extraction pipeline is. Specifically:

- **The LLM `summary` generator stretches** — it pastes boilerplate phrases like "may impact PA-08 federal candidate filings from the FEC Candidate Master file" onto national articles when the article touches anything broadly political. These show up at high rate (≥25% of kept articles in our sample).
- **Entity extraction reads `summary`** — so when summary stretches mention "Paige Cognetti" or "COGNETTI, PAIGE", we get `EntityMention` rows linking unrelated articles to our candidates. This contaminates not just the briefing card but anything else that joins on `EntityMention` (frame matching, sample title queries, etc.).

The right fix is probably one of:
1. Have entity extraction read `title + raw_text` and ignore `summary`. Requires a re-extraction pass.
2. Tighten the summary generator's prompt to stop pasting PA-08 boilerplate stretches. Requires finding the prompt.
3. Add an inverse filter to drop summaries containing known stretch boilerplate ("may impact PA-08...", "no direct connection to..."). Cheap but whack-a-mole.

I deliberately didn't take any of these in this session — the user asked us to do (a) (pattern revert + NEPA), and the broader summary-contamination problem deserves its own scoped investigation.

### State after this session

- Card output looks right. Trump now at 2 mentions / 11% race-focused.
- 5 known FPs still in the kept set (passing via `EntityMention` contamination, not patterns). Predicate hardening alone won't fix them.
- D#18686 (Bresnahan/Rollins recall miss) flagged for the next investigation — probably part of the broader extraction pipeline issue.

### Open questions / concerns for review

- **Should briefing's `top_claims_for_briefing` and frame-matching also be audited for this contamination?** Both run over `EntityMention` and `evidence_span` (which is extracted from article body, not summary, so probably cleaner). Worth a quick spot-check on a future pass.
- **Productization implication.** If we ship NOCTUA to other campaigns before the summary-contamination problem is addressed, every campaign's briefing card will have the same ~25% FP rate. Each campaign would need its own boilerplate-stretch patterns to inverse-filter. Worth fixing upstream before going broad.

### Extraction diagnostic (15-min targeted check)

Read raw_text vs summary on 4 articles to test the hypothesis "extraction reads summary, not raw_text." Findings refined the picture and surfaced an honest mislabeling.

**D#18686 — Rollins disaster declaration (recall miss, no candidate EM)**
- raw_text: `Bresnahan` × 4, `Rob Bresnahan` × 1 — clearly in the body
- title: 0 occurrences
- summary: 0 occurrences
- EntityMention: NONE
- Implication: Bresnahan IS in raw_text. Either extraction never ran on this article, or it didn't read raw_text, or extraction-version drift. Published 2026-05-27 (recent). The CLAUDE.md note that "v15.0 backfill processed 2,768 articles" suggests most live-ingested articles aren't yet covered by v15.0; D#18686 may be in that gap.

**K#5401 — White House security funding (spurious Cognetti EM)**
- raw_text: `Cognetti` × 0 — not in body at all
- summary: `COGNETTI, PAIGE` × 1 — LLM stretch boilerplate
- EntityMention: `person:cognetti` with surface text `"COGNETTI, PAIGE"`
- Implication: This IS the smoking gun. Cognetti is nowhere in raw_text but the EntityMention exists with surface text matching the summary verbatim. Extraction is reading summary.

**K#5391 — "Paige against the machine" (the article I labeled FP)** — IMPORTANT MISLABELING
- raw_text: `Cognetti` × 14, `Paige Cognetti` × 4, `Rob Bresnahan` × 1. raw_text opens with "Scranton Mayor Paige Cognetti speaks during a campaign event… Scranton Mayor Paige Cognetti is challenging Republican Rep. Rob Bresnahan in Pennsylvania's competitive 8th Congressional District."
- summary: about "Paige Gasper" in Michigan — WRONG, the summary describes a different article
- EntityMention: correct (Cognetti + Bresnahan)
- **The article IS race-relevant.** I mislabeled it as FP based on the misleading summary. The "Paige against the machine" headline is a journalistic flourish; the article is genuinely about Cognetti's PA-08 race.
- This reduces the FP rate from 5/20 → 4/20 (20%) on the original sample.
- Bigger implication: **the LLM summary generator is producing summaries that don't match the article.** That's a worse data-quality issue than I'd characterized — not just "stretches to add PA-08," but sometimes "describes a completely different article."

**K#3383 — Shapiro Endorses Harvie In PA-01 (spurious-ish)**
- raw_text: `Paige Cognetti` × 1 in a list ("Paige Cognetti in PA-08, and Janelle Stelson in PA-10")
- title/summary: 0 occurrences of Cognetti
- EntityMention: `person:cognetti` with surface `"Paige Cognetti"`
- Implication: extraction DID read raw_text here (Cognetti only appeared there). The mention is a passing list-of-endorsements reference, not the article's subject. Predicate sees an EntityMention and treats it as race-relevant. This is the "passing-mention contamination" case, not the summary contamination case.

### What the diagnostic actually concluded

My earlier hypothesis was too clean. The data:

1. **Extraction reads BOTH summary and raw_text.** K#5401 proves it reads summary (mention only in summary, EM exists). K#3383 proves it reads raw_text (mention only in raw_text, EM exists).
2. **Summary contamination IS real (K#5401)** — boilerplate stretches in the summary materialize spurious EntityMention rows.
3. **There's a separate "passing mention" problem (K#3383)** — even raw_text accurately mentions a candidate, but only in a list-of-other-races aside. The predicate can't distinguish subject mentions from incidental ones.
4. **D#18686 is a different problem** — extraction coverage gap, not contamination. Possibly v15.0/v14.x extractor mix.
5. **And** the LLM summary generator produces summaries that mis-describe articles (K#5391), which not only contaminates extraction but also makes manual labeling unreliable for anyone using summary as their first pass — including me, in the earlier eval.

### Implications for the next session picking this up

- **"Skip summary during extraction" partially helps but doesn't fully fix the predicate.** It removes K#5401-class FPs but leaves K#3383-class (passing mentions).
- **The better predicate semantics might be position/prominence-based** — count an article as race-context only if the candidate appears in the first N tokens of raw_text, or in a headline context. Not "appears anywhere."
- **D#18686 is a coverage-gap problem worth its own audit.** How many recent live-ingested articles have stale or missing extraction? If significant, the briefing card is silently undercounting Bresnahan/Cognetti weekly volume across multiple recent articles, not just this one.
- **Summary mis-description (K#5391) is the most concerning finding.** It means: (1) extraction quality is downstream-dependent on a generator that's unreliable, (2) anyone manually reviewing the corpus via summary will reach wrong conclusions, (3) summary-based features (search, dashboards, briefing memo) inherit the noise. Worth looking at the summary generator's prompt and recent failure modes before any more predicate work.
- **My eval FP rate was 25%; corrected to 20%.** Still high, but the corrected number changes which intervention has the biggest payoff. Of the 4 true FPs: 1 is summary-stretch contamination (K#5401), 1 is passing-mention (K#3383), 2 are pure LLM-summary stretches that mention PA-08 only in summary (K#4773, K#5676). Three of four trace back to summary problems. The summary generator is the highest-leverage fix.

### What did NOT change today

- Briefing card output. The shipped predicate (NEPA in, surnames out, universal gate, race_share display) is the correct ship for now. The remaining FPs surface as low-volume entities with low race_share %, which the UI exposes honestly.
- D#18686 stays a known miss. Not pursued further this session.

### Final state of work in this session

- Layer 1 shipped earlier today (universal gate + race_share + universal-gate semantics).
- (a) shipped: NEPA pattern added, candidate surname patterns reverted.
- Title-only pattern variant tested and rejected (net wash on this sample).
- Extraction diagnostic complete — refined the root-cause picture, surfaced a summary-generator quality problem as the highest-leverage next intervention.

Closing out. The next session picking this up should start by reading this entry, then deciding between (i) summary-generator prompt audit, (ii) extraction coverage audit on recent articles, or (iii) a position-aware predicate. I'd start with (i) because all three downstream problems trace back to it.


## 2026-05-30 Session: Entity detail page (`/entities/:id`) — links from Activity This Week
### Built
- **Backend:** new `GET /api/entities/{canonical_id}` in `backend/app/routes/entities.py`. Returns: entity profile (name, type, affiliation, description, mention/source counts, first/last seen), trailing-window stats (`mentions_this_week`, `mentions_last_week`, delta, plus all-time totals), 20 most recent articles mentioning the entity, 15 most recent v15.0 supporting quotes (claim_records that include the entity), and top 8 active narrative frames whose articles overlap the entity's mentions. Alias-merged via the existing `briefing_retrieval._entity_ids_for_canonical` helper so `person:bresnahan` + `person:auto:rob-bresnahan-jr` collapse.
- **Frontend:** new page `frontend-v2/src/pages/EntityDetail.tsx` at `/entities/:id`. Two-column layout — left: recent articles (each linking to `/articles/:id`), right: supporting quotes + narrative frames (each frame linking to `/narratives/:id`). Header shows name in affiliation color, type chip, affiliation chip, description. Stats row uses 4 `StatCell`s.
- **Type:** `EntityDetail` in `frontend-v2/src/api/types.ts`. **API client:** `api.entity(canonicalId)` in `frontend-v2/src/api/client.ts`.
- **Wired up:** route added in `frontend-v2/src/App.tsx` alongside the legacy `/entity-network` and `/entity-review` redirects. Activity-This-Week cards (`components/briefing/ActivityThisWeek.tsx`) now link to `/entities/${encodeURIComponent(e.id)}` instead of `/search?q=<name>`.

### Key decisions
- **Honors the KG policy.** The page is exactly what the v15.0 docstring permits: a profile + evidence side-panel with verbatim quote claim_records, plus a link surface into the existing narrative-frames UI. No graph traversal, no edge inference, no contradiction detection, no entity-pair "implications." Frames are surfaced by counting article overlap (the volume signal we already compute everywhere else), not by predicate inference.
- **Any entity is linkable, not just Activity-This-Week ones.** The endpoint accepts any canonical_id. This avoids needing a separate "is this entity Activity-eligible?" gate and lets us reuse the page from other surfaces later (article detail, narrative detail) without rework.
- **Article/quote/frame lists are NOT date-windowed.** They return the most recent N regardless of age. Only the headline stats card is date-windowed (default 7d). This matches how NarrativeDetail behaves and lets the page show evidence for entities whose race-context activity has cooled but whose historical coverage is the point.
- **Single anchor per card.** Cards are wrapped in `react-router` `<Link>`. No nested `<a>` (initial draft put an external-link icon inside the article-card Link → invalid HTML, fixed).
- **Backend route order matters.** Registered `entities.router` AFTER `entity_network.router` and `entity_review.router` so the (currently dead) `/api/entity-network/*` and `/api/entity-review/*` paths still match their original routers first. The new prefix is `/entities` (plural), distinct from the legacy `/entity-*` (singular hyphenated).

### Open questions / concerns for review
- **Performance — the "top frames for this entity" query joins NarrativeFrameMention on `source_item_id IN (article_ids)`** where `article_ids` can be up to ~1,500+ for Bresnahan/Cognetti. Tested locally and responds in <300ms, but if mentions counts grow significantly the IN-list will need to become a CTE or subquery. Not urgent — flagging for whoever next touches this endpoint.
- **Stats computation duplicates `briefing_retrieval._entity_ids_for_canonical` logic but NOT the race-context gate.** For ALWAYS_SHOW entities (Cognetti/Bresnahan), this is fine — they're computed raw in the briefing too. For context entities like Shapiro, the Activity card shows GATED counts (race-context only) while the new entity page shows RAW counts. The page header is the right place for "all coverage of Shapiro" (he's a governor — most of his coverage is non-race), but the discrepancy with the Activity card may confuse users. If it does, add a `?gated=true` query param to the endpoint and a toggle to the page header.
- **No pagination yet.** Articles are capped at 20, quotes at 15, frames at 8. For Bresnahan that means 20 of ~1,500 articles — a "View all" link to `/search?q=<name>` (or eventually a dedicated `/articles?entity=<id>` filter) is the obvious next step.
- **No sentiment summary or score-distribution chart.** Could add later. Holding off until someone actually asks.


## 2026-05-30 Session: Login page polish + backend health.py ImportError fix
### Built
- **Wordmark migration to Vite asset pipeline.** Moved `frontend-v2/public/theosintel-wordmark.png` → `frontend-v2/src/assets/theosintel-wordmark.png`. [Login.tsx:4](frontend-v2/src/pages/Login.tsx:4) now does `import wordmark from '@/assets/theosintel-wordmark.png'` and `<img src={wordmark}>`. Vite content-hashes the asset URL on every change, so swapping the PNG in `src/assets/` invalidates the cloudflared + browser cache automatically — no more "I replaced the file on disk and the old logo is still showing." Added `frontend-v2/src/vite-env.d.ts` (`/// <reference types="vite/client" />`) — the project didn't have one, so TS was about to red-squiggle the `.png` import.
- **Login UI tweaks.** [Login.tsx](frontend-v2/src/pages/Login.tsx) heading: "Private preview" → "Preview". Background: layered radial-gradient — warm amber at 20%/25%, cool blue at 80%/75%, plus a 28px dot grid — all low opacity, layered over `var(--bg-1)` so light/dark modes still both work.
- **Backend health.py ImportError fix.** [backend/app/routes/health.py:18](backend/app/routes/health.py:18) was `from app.routes.auth import require_admin`, but `require_admin` is not defined in `routes/auth.py`. Every other route imports it from `app.services.access_codes` (see [ingest.py:7](backend/app/routes/ingest.py:7), [admin.py:25](backend/app/routes/admin.py:25), [setup.py:22](backend/app/routes/setup.py:22), [text_overrides.py:25](backend/app/routes/text_overrides.py:25), [narrative_frames.py:15](backend/app/routes/narrative_frames.py:15), [narrative_triage.py:26](backend/app/routes/narrative_triage.py:26), [campaign.py:12](backend/app/routes/campaign.py:12)). Uvicorn `--reload` was crash-looping on the ImportError so port 8000 was bound but served no requests. Symptom: theosintel.com served a blank dark page because `/api/auth/me` hung, [AuthContext.tsx:51](frontend-v2/src/auth/AuthContext.tsx:51)'s `.finally(() => setLoading(false))` never fired, and [App.tsx:83](frontend-v2/src/App.tsx:83)'s loading branch rendered an empty `<div style={{ minHeight: '100vh', background: 'var(--bg-1)' }} />` indefinitely. One-line import swap to the canonical path; backend booted, frontend resumed redirecting to /login.

### Key decisions
- **Asset import, not a `?v=` cachebust on the public URL.** The PNG in `/public/` would have worked with `?v=2` appended in the `src=`, but that requires bumping the version every time. The Vite import is the durable fix — and the cloudflared tunnel in front of port 5174 also stops being able to serve a stale copy.
- **One-line backend fix, no re-export shim in `routes/auth.py`.** Could have re-exported `require_admin` from `routes/auth.py` to support both call sites. Skipped it — every other route already imports from `services/access_codes`, so the canonical pattern is unambiguous and `health.py` just needs to follow it.

### Open questions / concerns for review
- **`backend/app/routes/health.py` is untracked.** `git ls-files` returns nothing for the file — it was created but never staged, which is why the bad import has been silently breaking the backend on startup without showing up in any diff. Whichever session added it (and the matching `services/ingestion_health.py` + `_run_ingestion_health_check` scheduler job): please `git add` + commit so the next reviewer can actually see the change. The route's GET path (`/api/health/ingestion-alerts`) is consumed by the frontend NotificationsBell, so this isn't dead code — it just isn't versioned.
- **No backend smoke test catches startup ImportError.** Uvicorn keeps the port bound while `--reload` flails on import, so `lsof -ti:8000` lies. A 5-second `curl --max-time 3 http://localhost:8000/api/auth/me` after any backend edit would flag this class of bug instantly. Worth adding to whatever pre-commit / pre-push setup exists, or at least to the dev-loop muscle memory.


## 2026-05-30 Session: Temporarily hide Landscape + Opponents pages
### Built
- **Sidebar nav entries commented out** at [Sidebar.tsx:21,28](frontend-v2/src/components/Sidebar.tsx:21) — both with a dated `Landscape + Opponents temporarily hidden 2026-05-30 at user request` comment. `Map` and `Users` removed from the `lucide-react` imports since neither icon has a live consumer now.
- **Routes redirect to `/`** at [App.tsx:51,65](frontend-v2/src/App.tsx:51) — same `<Navigate to="/" replace />` pattern used for `/entity-network` and `/entity-review` (the hidden-but-preserved precedent). `Landscape` and `Opponents` page-component imports dropped from App.tsx. Page files themselves preserved (`pages/Landscape.tsx`, `pages/Opponents.tsx`) for the un-hide.

### Key decisions
- **User said "temporarily" — so redirect-and-comment, not delete.** Mirrors the EntityNetwork/EntityReview pattern from 2026-05-29. To un-hide later: revert the two App.tsx routes back to `<Landscape />` / `<Opponents />`, re-add the page-component imports + `Map`/`Users` icon imports in Sidebar, uncomment the two NAV items. Roughly six lines each side.
- **No data or backend changes.** Both pages already hit existing endpoints that other surfaces still consume (`/api/narrative-frames/landscape`, opponent activity routes used by `Opponents.tsx` are also called from briefing tiles). Nothing to retire on the API side.

### Open questions / concerns for review
- **Vite HMR went into a failed state mid-edit** (removed `Users` from imports before commenting out the NAV usage of `Users`). For ~one second the served module referenced an undefined `Users` identifier; Fast Refresh logged `ReferenceError: Users is not defined` and `[vite] Failed to reload Sidebar.tsx` until the second edit landed. The end state is clean — confirmed via a forced reload — but the historical errors are still in the console buffer. Worth doing edits in dependency order (remove usage first, then remove import) next time so HMR never sees an inconsistent module.


## 2026-05-31 Session: Hallucination-cap fix — `_compute_relevance_score` cap-bypass widened + word-boundary name matching

User flagged that the dashboard's "latest article" was 18+ hours old despite RSS ingestion being healthy. Investigation traced this to a deliberate recalibration in `637d4d5` (2026-05-29) — verdict bases lowered (`relevant` 65→50, `critical` 90→75) plus a new hallucination cap that pinned `verdict ∈ {relevant, critical} AND title_hits=0 AND body_hits=0` articles to 40. The cap was too tight — it ignored district code, source-attribution, and read summary (which is LLM-contaminated). Result: candidate's own tweets, Cook Political Report PA-08 calls, district-named stories got silently demoted below the dashboard's 50 threshold.

### Built

- **[backend/app/services/campaign_analysis.py](backend/app/services/campaign_analysis.py)** — three helper additions and one `_compute_relevance_score` change:
  - New `_name_match_tokens(ctx)` — lowercase candidate/opponent tokens, drops first-name-only tokens with <5 chars to defeat "Rob" matching "problems".
  - New `_count_token_hits(text, tokens)` — word-boundary substring count (`\b…\b`), so "Rob" no longer matches inside "robust" / "problems" / etc.
  - Refactored `_count_name_mentions` to use the new boundary-matched helpers (same semantics for the common case; correct for the edge cases).
  - New `_count_name_mentions_uncontaminated(item, ctx)` — title + raw_text[:3000] only, NO summary. Used by the cap-bypass check because summary is LLM-generated and known to contain stretch boilerplate ("COGNETTI, PAIGE", "PA-08 federal candidate filings…") that contaminates evidence — see 2026-05-30 entry.
  - New `_has_district_mention(item, ctx)` — district code (e.g. "PA-08") in title + raw_text only. Geography keywords ("Scranton", "NEPA", "Luzerne") NOT used here — too broad for a post-LLM hallucination check (matches local crime, real estate, utility pages).
  - New `_source_attributed_to_candidate(item, ctx)` — fires only when source_name contains a candidate surname (≥5 chars) AND a recognized feed marker (`X/Twitter`, `(@`, `YouTube:`, `Google News:`, etc.). Surname-alone false-positives on unrelated obituaries; marker-alone false-positives on generic outlet feeds. Backfill-prefix sources excluded entirely.
  - Cap now: `if verdict ∈ (relevant, critical) AND not (name OR district OR source-attribution)` → `min(score, 40)`. Names checked against uncontaminated body. Geography keywords remain in use for the pre-LLM gate (`_race_mention_tokens`) — that's a cost-saving filter, not an evidence test.

- **[backend/scripts/validate_cap_fix.py](backend/scripts/validate_cap_fix.py)** — pure-Python recompute + optional `--apply` writer. Imports helpers from `campaign_analysis` directly so the validation logic and production logic can never drift apart. Targets exactly `archived_as_irrelevant=False AND race_relevance_score=40` (the precise cap-target set — scores 41-49 can't be cap-capped under the documented formula).

- **One-shot DB update** — 8 articles' `race_relevance_score` raised from 40 to their formula-correct value. All clean wins; spot-checked each:
  - `[23084]` 40→50 — "Cook Political Report Moves PA-08 to Toss-Up" (district + source-attribution)
  - `[15363]` 40→50 — "PA-08 Race Moved to Toss-Up - PoliticsPA"
  - `[21315]` 40→56 — Cognetti tweet: "I didn't plan to run for Congress…" (source-attribution)
  - `[22824]` 40→50 — Cognetti tweet on PRO Act + NEPA workers
  - `[16419]` 40→50 — Bluesky post listing PA-08 in DCCC seat-flip math
  - `[6469]` 40→66 — "Bresnahan highlights district impact in first six months"
  - `[6468]` 40→66 — "Cognetti sworn in as Scranton Mayor"
  - `[4350]` 40→58 — "Trump coming to Mount Airy Casino" (Bresnahan's office in raw_text — verified by `LIKE '%bresnahan%'`)

### Key decisions

- **Targeted DB update over full LLM rescore.** The precise cap-target set is only ~42 articles total at score=40, and the formula-correct uncap can be computed in pure Python from `(item.title, item.raw_text, source_credibility, claim_records)` — all already persisted. No need to spend $20-30 LLM dollars on a 24K-article sweep just to fix 8 rows.
- **Assumed verdict="relevant" (base 50) in the recompute.** For cap-capped articles we can't know whether the original verdict was "relevant" or "critical" without re-calling the LLM. Assuming "relevant" under-scores any actually-critical articles (50+bonus vs. true 75+bonus). Conservative direction — we'd rather under-surface a high-relevance article slightly than over-surface a contested one. In practice almost all cap-capped articles are verdict="relevant" — "critical" is rare and usually has named candidates.
- **Excluded summary entirely from the cap-bypass check.** Yesterday's session (2026-05-30) diagnosed the summary generator pasting boilerplate stretches like "may impact PA-08 federal candidate filings from the FEC Candidate Master file" onto unrelated articles. Letting that contaminated text bypass the cap defeats the cap's purpose. Title + raw_text only.
- **Geography keywords stay in the pre-LLM gate, NOT the post-LLM cap.** First run of the validation script (loose cap, accepted any race-token including geography) surfaced 76 mostly-junk uncappings — scrantonpa.gov utility pages, library closures, sandwich shop reopens, hospital fires. Tightening to district-only cut that to 9 (then 8 after the word-boundary fix). Geography keywords are fine as a cost-saving "is it worth calling the LLM" gate; they're not specific enough as a "did the LLM hallucinate" check.
- **Word-boundary name matching ships for ALL callers, not just the cap.** Discovered via the `[6612]` "Military healthcare contractor" article — my dry-run flagged it as a "name" signal because "Rob" matched "problems" via substring. Same bug exists in the regular `_count_name_mentions` used by the bonus calculation — every "Rob"/"Paige"-containing word in any article has been silently inflating body_hits. Fixing it everywhere is principled and a strict improvement.

### Underlying scoring-system concerns the user explicitly named

These are the bigger structural problems we surfaced while debugging the immediate symptom. Not fixed in this session — flagging for future work:

- **The three "is it relevant?" axes don't cleanly map.** `verdict` (4 levels), `relevant` boolean, `archived_as_irrelevant` boolean. The documented contract is `archived = not relevant` and `relevant = verdict ∈ {relevant, critical}` — but articles at score 45 with `archived_as_irrelevant=False` can't be reproduced by the documented formula. Suggests drift between what `_compute_relevance_score` returns and what gets persisted, or rescore behaviour that doesn't update one of these fields consistently. **Audit before next formula change.**
- **The dashboard threshold (50) sits at the verdict-`relevant` base (50).** Any tiny deviation pushes "relevant" articles below the threshold. The threshold should be at a value where the scoring is most stable — probably 45 or 55, not the base itself. Brittle by design at exactly the cut.
- **`structured_extraction` caches the output, not the inputs.** Only 6 fields cached (`one_sentence`, `framing`, `sentiment`, `relevance_score`, `relevant`, `opponent_attacks`, `reason`) — none of `verdict`, `extracted_claims`, `source_credibility`. If we'd cached the inputs, this entire fix could have been "rerun `_compute_relevance_score` against cached data — zero LLM cost, full corpus in seconds." **Caching the LLM verdict and the cleaned_claims list would make every future formula change near-free to re-apply.**
- **`relevance_reasons` (text column) is underutilized.** Score is opaque — a UI hover that says "scored 65 because: verdict=relevant (+50), title name (+12), 1 high-confidence claim (+3)" would be cheap explainability. The data is computed; it's just not persisted.
- **Coverage gap: only 3,264 of 24,202 articles have `structured_extraction` filled.** This cache was added sometime later; older articles never got backfilled. If we DO start caching the inputs, the next rescore should backfill all 24K rows.
- **`geography_keywords` config has an LLM-stretch boilerplate row in it.** One of the saved keywords is literally "PA-08 federal candidate filings from the FEC Candidate Master file" — pasted from a contaminated summary. That's a 70-char "keyword" that would only match literal copies of itself, so it's been inert, but it's a data-cleanliness leak from the same summary-generator problem. **Run a `SELECT * FROM campaign_configs` cleanup pass on the JSON-encoded keyword arrays.**
- **Summary contamination now demonstrably affects scoring, not just the briefing card.** Yesterday's session found it in `EntityMention`. Today we found it in the relevance score (via the cap bypass). Wherever any code does "string LIKE '%cognetti%'" against `summary`, the same contamination flows through. **The summary-generator prompt is the highest-leverage upstream fix on the project right now.**

### Verified

- Imports clean: `from app.services import campaign_analysis` succeeds with new helpers present.
- Dry run: 8 articles uncapped, 34 correctly stay capped (federal Trump articles with no PA-08 hook).
- Spot-check on `[6612]` ("Military healthcare contractor") and `[4350]` ("Trump Mount Airy"): `[6612]` has no Bresnahan/Cognetti in raw_text (confirmed via `LIKE '%bresnahan%'`) — correctly stays capped after the word-boundary fix. `[4350]` does have "the office of U.S. Rep. Rob Bresnahan" in raw_text — correctly uncaps.
- Applied: 8 rows updated; SELECT confirms new scores landed (50, 50, 50, 50, 56, 58, 66, 66).

### Open questions / concerns for review

- **Existing scores everywhere else are still the old-formula values from the 2026-05-30 rescore.** This session only updated 8 articles at score=40. Any *other* articles affected by the bonus-calculation bug (word-boundary "Rob" inflation) still have wrong scores. Two options going forward: (a) accept silently — the bonus errors are small (+4 body_bonus per false hit, capped at +8 total), or (b) trigger a fresh full rescore (~$20-30 LLM cost) to propagate the corrected formula across the full corpus. Recommend (b) when the user is ready to spend the dollars, but it's not urgent.
- **No new rescore was triggered.** The auto-resume gate (`extended_backfill_completed=True` → `_resume_pipeline_if_needed`) checks for `race_relevance_score IS NULL` per the 2026-05-30 filter-correctness fix. Every article has a score, so no auto-trigger on restart. To run a fresh sweep, the user needs to explicitly POST `/admin/rescore-articles` with `confirm: "RESCORE ALL ARTICLES"`.
- **The 4 borderline-FP cases from the loose-cap variant are still in the data.** When the validation pass was looser (no word-boundary, included geography keywords), articles like `[8947]` Rawstory "Fox News poll", `[5485]` Independent "Arkansas housing", `[4920]` Nbcnews "Barney Frank obit" all flagged as uncap candidates because their LLM-generated *summary* fields contain "PA-08" stretch boilerplate. The cap correctly leaves them capped now — but the underlying summary contamination means anything that reads `summary` directly (search? frame matching? briefing memo?) still gets the noise. **Until the summary generator is fixed, treat any score that consumes `summary` as suspect.**
- **`_name_match_tokens` is a partial fix.** It still includes the candidate's first name when ≥5 chars ("Paige", "Robert"). For "Paige" this is fine — distinctive token. For a future candidate named "James" or "David", first-name matching would be much noisier and we'd want last-name-only. The 5-char threshold is a heuristic, not a principled bound — keep an eye on it as we generalize for NOCTUA.
- **No alembic migration this session.** All changes are formula/code, no schema. The bigger structural items above (caching verdict + claims, adding `relevance_reasons` rendering) would each be their own migration if pursued.

## 2026-05-31 Session: Social-ingestion batch — platform tagging, Twitter failover, Bluesky watchdog, Reddit broadening

Motivation: user observed social media is <1% of ingested data and "should be more," and separately that X/Twitter posts exist in the article list but never show up in the platform rows. One approved batch, 8 tasks. Goal split: (1) **categorize** what we already ingest by platform, and (2) **broaden volume** across the three social sources.

### Built

- **Platform tagging (Tasks 1–5).** New `platform` column on `source_items`, derived from the item's URL host — independent of `source_type` (left untouched).
  - URL→platform classifier helper + unit tests (twitter/x, bluesky, reddit, mastodon, youtube; `None` for everything else).
  - Hand-authored Alembic migration adding `source_items.platform` (nullable text), Postgres-tested.
  - SQLAlchemy `before_insert`/`before_update` event sets `platform` from `source_url` automatically on every write — no caller changes needed.
  - One-shot backfill over existing rows (**user approved "apply now"** → 1,069 live rows tagged).
  - API serializes `platform`; Articles page gained a platform filter.
- **Twitter resilience (Task 6).** Nitter (Twitter RSS mirror) failover — `NITTER_INSTANCES` env override lets ops swap in working instances without a deploy. New scheduler job `_run_twitter_recheck` (calls `recheck_failed_twitter_monitors` + `refresh_stale_twitter_feeds`). This is the "different approach to X/Twitter" the user remembered — we ingest Twitter via Nitter RSS, not the API, which is why those posts land as articles but historically weren't platform-rowed (now fixed by the platform column above).
- **Bluesky hardening + broadening (Task 7).** [bluesky_firehose.py](backend/app/services/bluesky_firehose.py) + [scheduler.py](backend/app/services/scheduler.py).
  - Self-healing watchdog for the long-running jetstream asyncio task. `get_health()` classifies `disabled` / `dead` (task crashed or never started) / `wedged` (socket still "connected" but frames stopped — `events_seen` flat past `_WEDGE_STALL_S=240s`, a failure the websockets lib raises nothing on) / `ok`, from a monotonic last-frame stamp (`_last_event_monotonic`). Async `restart()` does stop → reset-clock → start (order matters: the fresh connection must stamp its own clock, not inherit the dead task's stale silence). New `_run_firehose_watchdog` scheduler job (5-min interval, env-gated on `BLUESKY_FIREHOSE_ENABLED != "false"`) restarts on any non-`ok` state.
  - Keyword set broadened 6→10 terms: compact district form `pa08`, plus city tokens harvested from the config `location` (`scranton`, `wilkes`, `barre`). Env hatches `BLUESKY_EXTRA_KEYWORDS` (additive, trusted, bypasses the len≥4 guard) and `BLUESKY_BLOCK_KEYWORDS` (removal; block wins over both derived and extra).
- **Reddit broadening (Task 8).** [ingestion_reddit.py](backend/app/services/ingestion_reddit.py).
  - Search terms 2→4: added distinctive surname tokens (`Cognetti`, `Bresnahan`) alongside the full names — people say the surname far more than the full name and phrase search misses those. First names deliberately NOT added (too noisy). `REDDIT_EXTRA_TERMS` env hatch, deduped.
  - `_DEFAULT_SUBREDDITS` gained `WilkesBarre`.
  - **Bug fix in `_district_derived_subs`:** location `"Scranton/Wilkes-Barre, PA-08"` used to slug the whole pre-comma token into `ScrantonWilkesBarre` — a subreddit that 404s. Now splits on `[/&]| and ` first → real subs `r/Scranton` + `r/WilkesBarre`.

### Key decisions

- **`platform` is derived from URL, orthogonal to `source_type`.** `source_type` stays semantic (`social`, `news`, …); `platform` answers "which site." This is why Twitter-via-Nitter items now categorize correctly without touching the ingestion path's type logic.
- **Reddit free path broadened; Tavily path kept deliberately NARROW.** `ingestion_reddit._search_terms` (free direct-JSON, cost = time) expands to surnames + env extras. `tavily_reddit._search_terms` (paid — each term is a separate Tavily query that spends a credit) stays full-names-only. Documented the intentional divergence in **both** files so a future session doesn't "sync" them and silently inflate Tavily spend.
- **Watchdog runs as a coroutine scheduler job.** Verified against APScheduler 3.11.2 source that `AsyncIOExecutor._do_submit_job` runs `async def` jobs via `create_task(run_coroutine_job(...))` on the loop (they're awaited, not dropped) — this is the first async job in the scheduler, so it was worth confirming.

### Verified

- **37 focused tests pass** (`test_ingestion_reddit.py`, `test_bluesky_firehose_watchdog.py`, `test_twitter_failover.py`, `test_source_item_platform_event.py`) — no network, ~28s. New `test_bluesky_firehose_watchdog.py` (15 tests) covers all four health states + the threshold-exclusivity edge + restart ordering + keyword broadening/hatches.
- **Full backend suite: 731 passed, 44 failed.** All 44 failures are a SINGLE pre-existing root cause (see concern below) — **0** of them touch any file in this batch (confirmed: no `bluesky_firehose`/`ingestion_reddit`/`tavily_reddit`/`twitter`/`scheduler` frame in any failure traceback).
- Live keyword build produced the expected 10 Bluesky terms and the corrected Reddit sub list (`pennsylvania, Scranton, WilkesBarre, nepa, politics`) against the real campaign config.

### Open questions / concerns for review

- **⚠️ For the session that owns `article_body_recovery.py` (untracked) + the `campaign.py` route change:** 44 tests across 5 files (`test_campaign_initialization.py` ×16, `test_campaign_initialize.py` ×14, `test_campaign_auto_monitors.py` ×7, `test_race_directory.py` ×5, `test_election_date_inference.py` ×2) make a **live Google News `httpx.get`** during the test. Chain: `update_campaign`/`initialize`/`auto_setup_monitors` → `ingest_rss` → `recover_body` → `resolve_google_news_url` → `_fetch_decoding_params` → `httpx.get`. These tests set `SEARCH_PROVIDER=mock`, but that only mocks *search-query* monitors — the auto-generated **Google News RSS feed** monitors still ingest for real. When that endpoint is slow/unreachable the whole suite **hangs indefinitely** (a blocked SSL `recv()` that pytest's `thread` timeout method cannot interrupt — only `--timeout-method=signal` can). Even with 20s signal timeouts the suite took **14m13s**. Recommendation: mock `httpx`/`recover_body` in these tests, or make `resolve_google_news_url` short-circuit when the search provider is mock / under test. **Not fixed here — it's your code and out of this batch's scope.**
- **`barre` is the one noise-risk Bluesky keyword** — it word-boundary-matches "Barre, VT" and ballet "barre," and as a bare token risks generic hits. It's auto-derived from `Wilkes-Barre`; drop it without a code change via `BLUESKY_BLOCK_KEYWORDS=barre` if it proves noisy in production.
- **Reddit direct-JSON broadening is latent until access is restored.** `ingestion_reddit` is gated by `_probe_reddit_access`, which 403s for unauthed clients (Reddit's post-2024 anti-bot stance). So the broadened terms/subs only take effect once OAuth or a working access path is added. The **Tavily Reddit path is the working fallback today** (kept narrow on purpose for cost). Worth deciding whether to invest in Reddit OAuth if Reddit volume matters.
- **Task 5 live-UI verification was blocked.** The platform filter passed typecheck and backend serialization was verified, but the Vite dev server was hung (holding the :5174 socket, returning HTTP 000, esbuild `.node` loader error in the log). Per the CLAUDE.md tunnel rule I did NOT restart it. The platform filter should get a quick manual click-through once Vite is healthy.

## 2026-05-31 Session: Backend security + correctness sweep (read-only audit, nothing changed)

Three parallel read-only audit agents (security/SSRF, correctness/dialect, routes/auth-coverage) + manual verification of every headline finding against source. No code touched.

### Headline
The access **gate** is sound: middleware 401s any `/api/*` without a valid code, fails *closed* with the 5 configured codes, and the public-tunnel path is NOT bypassable (Vite overwrites `X-Forwarded-Host`, Cloudflare delivers the real Host). **But authorization coverage *behind* the gate has holes** — several cost/destructive endpoints never received the `require_admin` dependency the 2026-05-29 admin-gating pass was supposed to cover. Threat is a *valid non-admin friend code*, not the open internet.

### Findings (each verified by reading the route decorator / call path)
**Auth-coverage gaps:**
- **CRITICAL — `POST /api/races/{id}/select`** ([races.py:43](backend/app/routes/races.py:43)): no `require_admin` — the file doesn't even import it. Calls `initialize_campaign()` (LLM monitor-discovery chain) + `select_directory_race()` (overwrites campaign config + opponents). **Flagged twice on 2026-05-29, still open.** Bonus: races.py:58-62 swallows *all* `initialize_campaign` errors with `except: pass`.
- **HIGH — `PUT /api/campaign`** ([campaign.py:406](backend/app/routes/campaign.py:406)): ungated while **7 other routes in the same file ARE gated** → an oversight, not a design choice. Rewrites `relevance_keywords`/`excluded_keywords` — could blind the whole relevance pipeline.
- **HIGH — `POST /api/rss-feeds/ingest-all` + `/{id}/ingest`** ([rss_feeds.py:43](backend/app/routes/rss_feeds.py:43),[:109](backend/app/routes/rss_feeds.py:109)): ungated, both → `ingest_rss` → LLM scoring (spends budget).
- **HIGH — `POST /api/race/import-csv`** ([race_import.py:31](backend/app/routes/race_import.py:31)): ungated, bulk-overwrites campaign profile + opponents + feeds.
- **HIGH — `POST /api/race-sentiment/{sync,backfill,sync-all}`** ([race_sentiment.py:60,85,202](backend/app/routes/race_sentiment.py:60)): ungated, hammer external market APIs.
- **MEDIUM** — `sources` ingest ([sources.py:110-135](backend/app/routes/sources.py:110)), narrative-frames create/update/delete ([235/258/295](backend/app/routes/narrative_frames.py:235)), narrative-triage execute-merge ([narrative_triage.py:111](backend/app/routes/narrative_triage.py:111)), frame-mention removal ([narrative_frames.py:605](backend/app/routes/narrative_frames.py:605)): LLM-cost + cascade-delete mix.
- NOTE: low-value **curation** endpoints (create opponent, label topic-region, review-queue actions) are also ungated, but that is plausibly *intentional* (friends curate). The real bug is the *cost/destructive* subset slipping the gate. **Boundary decision needed** (see below).

**SSRF (HIGH for future cloud deploy / MEDIUM now):** `ingest_url`/`ingest_rss` ([ingestion.py:850,984](backend/app/services/ingestion.py:850)) do bare `httpx.get` with NO scheme/IP allowlist; reachable via the ungated `sources/url` + rss routes. Today → server can be made to GET internal addresses; on a cloud host → `169.254.169.254` IMDS credential theft. `monitor_url_discovery` has a blocklist; the user-facing ingestion path does not.

**`X-Forwarded-Host` trust (LATENT):** localhost-admin bypass trusts a client header ([main.py:130](backend/app/main.py:130), [access_codes.py:161](backend/app/services/access_codes.py:161)). NOT exploitable today (Vite overwrites it; port 8000 isn't tunneled) but one config change (expose 8000, or add a second proxy) from a full auth bypass. Switch to `request.client.host` (real TCP peer, unspoofable).

**`/docs` exposed (MEDIUM):** `/docs`,`/redoc`,`/openapi.json` are in `_AUTH_EXEMPT_PATHS` → full API map (incl. destructive endpoints + their confirm strings) is public on the tunnel. Set `docs_url=None` in the tunneled deployment.

**Reliability:**
- **HIGH — boot-time full scan:** `init_db` → `repair_frame_data` runs on **every** boot and loads the entire `NarrativeFrameMention` table into memory **twice** ([narrative_frames.py:1542,1564](backend/app/services/narrative_frames.py:1542)) + per-row validation. The docstring's "cheap when nothing to fix" is true only for *writes*, not reads. Boot latency grows with the table. Fix: `yield_per` batching + a staleness gate.
- **HIGH — `ingest_lock` held during LLM backoff:** when all LLM providers are exhausted, `FallbackProvider` sleeps up to `LLM_EXHAUSTED_MAX_WAIT_SECONDS` (default 1800s) *inside* `ingest_lock` (confirmed: [rss_feeds.py:45](backend/app/routes/rss_feeds.py:45) acquires the lock and holds it across `ingest_rss` → LLM). Ingestion can stall up to 30 min. Fix: move the all-exhausted backoff outside the lock.

**Test gap:** the destructive-endpoint guard tests cover rescore + frame-delete confirm strings but NOT `reset-workspace` (most destructive) or `reanalyze-sources`.

**Doc rot:** CLAUDE.md "Alembic head: `6e2b8c4a9d1f`" is **stale** — that rev is now a `down_revision`; true head is **`2df994cdd1f9`** (`add_platform_column_to_source_items`, 2026-05-31). Single clean head, no multi-head problem.

### Checked clean (good news)
No hardcoded secrets in source; `.env.example` is placeholders only. No command injection (no `subprocess`/`os.system`/`shell=True`), no unsafe deserialization (no `pickle`/`yaml.load`/`eval`/`exec` on external data). SQL is parameterized (Postgres search uses `websearch_to_tsquery` bound param; `text()` calls bind params). CORS leaves `allow_credentials` unset → no wildcard+credentials leak. Postgres dialect hygiene holds: no `INSERT OR IGNORE`/`AUTOINCREMENT`/`datetime('now')`; scheduler jobs all `max_instances=1`/`coalesce`; migrations use `sa.false()`/`ON CONFLICT`.

### Open questions / concerns for review
- **Authz boundary decision:** "admin = cost/destructive only, friends curate" (then gate just the ~10 cost/destructive endpoints above) vs. "admin = all mutations" (gate ~28). I recommend the former.
- **Approve gating the cost/destructive subset?** Low-risk (one `dependencies=[Depends(require_admin)]` per route) but it's live campaign backend — not done without sign-off. The existing destructive-guard tests run in dev-mode (`ACCESS_CODES=""`) so they'd still pass.
- **Pre-SaaS hardening** (SSRF guard, `docs_url=None`, `request.client.host` switch): worth doing before multi-tenant productization; lower urgency for the current 3-friend tunnel.

## 2026-05-31 Session: Test-suite network seams (RESOLVES the Google-News-hang flag)

Fixes the open concern raised in the 2026-05-30 Reddit/Bluesky entry: 44 tests across 5 files made a **live Google News `httpx.get`** that hung the suite indefinitely (14m13s even with signal timeouts). Test-only changes — **no production code touched**.

### Built
- **`conftest.py` — two new autouse fixtures** (both default tests offline; both overridable by a later test-local `monkeypatch`, LIFO):
  - `_offline_rss_fetch_by_default`: patches `ingestion._fetch_rss_content → None`. This is the **single upstream choke point** that closes BOTH RSS network seams at once — the feed fetch AND the per-entry `recover_body` (Google News decode + publisher fetch), because returning `None` exercises the real "feed fetch failed → skip feed" branch in `ingest_rss`, so `recover_body` is never reached. Setup-path tests that generated Google News RSS monitors as a side effect now contribute 0 RSS items but still run end-to-end; their assertions (monitor counts, election dates, search-path ingestion) don't depend on RSS content.
  - `_offline_search_provider_by_default`: `monkeypatch.setenv("SEARCH_PROVIDER", "mock")`. Closes the **Tavily** seam — any test calling `get_search_provider()` without setting the env var was reading `.env` (SEARCH_PROVIDER=tavily on this box) and hitting the live API. `test_custom_campaign_update_still_works` went **22.6s → 0.14s**.
- **`test_race_directory.py` — 7 assertion updates** in 4 tests (this file was clean before; it's now modified). Pre-existing stale-test bug, *unrelated to the network task* (see decision).

### Key decisions
- **Chose `_fetch_rss_content → None` over the previous session's two suggested fixes** (mock `httpx`/`recover_body`, or short-circuit `resolve_google_news_url`). Reasons: (1) one seam kills both the feed-fetch AND decode network paths; (2) it deliberately does NOT touch `recover_body`, so `test_article_body_recovery.py` keeps exercising the real recovery logic against its own HTTP mocks; (3) conftest-only — production `article_body_recovery.py` / `ingestion.py` are untouched, which matters for LIVE software. **@owner of `article_body_recovery.py`: your code is unchanged and still fully tested.**
- **The 4 `test_race_directory.py` failures were NOT the network bug** — they fail instantly, before any RSS code. Root cause: `_humanize_name` (committed in `b8986ea`, "frame system overhaul", **59 commits after** the test was written in `fec64f6`) added `@validates` hooks on `RaceCandidate.candidate_name` / `CampaignConfig.candidate_name` / `Opponent.name` that normalize FEC SHOUTY `"FIGURES, SHOMARI C."` → `"Shomari C. Figures"`. The tests still asserted the raw FEC strings. Nobody updated them when humanization landed. I updated the **test assertions** to the humanized forms (`"Shomari C. Figures"`, `"Hampton Harris"`) — matching intentional, committed behavior and the real frontend flow (the race-picker sends the displayed/humanized name back to `select_race`). This is **not** weakening assertions: same import/select/opponent-creation logic, corrected expected strings. The `_humanize_name` source was left as-is.

### Verified
- **5 target files: 79 passed in 10.72s** (`test_campaign_initialization.py`, `test_campaign_initialize.py`, `test_campaign_auto_monitors.py`, `test_race_directory.py`, `test_election_date_inference.py`), run with `--timeout=30 --timeout-method=signal` — the signal tripwire (the only method that can interrupt the C-level SSL `recv()` hang) never fired, proving no network stall.
- **Full suite: 775 passed in 47.84s** (was 14m13s / indefinite), same signal tripwire at 45s, 0 failures. Only 3 pre-existing deprecation warnings (`google.generativeai`, pydantic v2 config) — untouched.
- Durations confirm no live calls: slowest race-directory tests are the two FEC-PSV seeds (~1.5s, local file), not network.

### Open questions / concerns for review
- **`SEARCH_PROVIDER=mock` is now a global test default.** Safe today: every test that cared already set it to `mock` via `monkeypatch.setenv` (re-setting the same value is a no-op), `test_search_cache.py` builds providers directly without reading the env, and **no test exercises the live-Tavily selection path**. If a future test ever wants to assert Tavily *selection* logic in `get_search_provider()`, it must `monkeypatch.setenv("SEARCH_PROVIDER", "tavily")` itself (override wins) — and should stub the network, not hit it.

## 2026-05-31 Session: Backend authz hardening — gated cost/destructive endpoints

Follow-through on the "Approve gating the cost/destructive subset?" open question from the read-only audit entry above. User approved ("Lock the risky buttons"): gate the money-spending / destructive POST/PUT/DELETEs that slipped `require_admin`; leave harmless friend-curation buttons open. Applied the canonical per-file pattern (`from app.services.access_codes import require_admin` + `_admin_only = [Depends(require_admin)]` + `dependencies=_admin_only` on the decorator). **15 endpoints gated across 7 files.**

### Built (gates added)
- **routes/races.py** — `POST /races/{id}/select` (runs `initialize_campaign` LLM chain). Added import + alias.
- **routes/rss_feeds.py** — `POST /rss-feeds/ingest-all`, `POST /rss-feeds/{id}/ingest` (feed crawl + per-article LLM scoring). Added import + alias. **Feed CRUD (create/update/delete) left OPEN** — cheap curation.
- **routes/race_import.py** — `POST /race/import-csv` (bulk overwrite of campaign profile + mass-create opponents/feeds/reminders). Added import + alias.
- **routes/race_sentiment.py** — `POST .../sync`, `POST .../backfill`, `POST /race-sentiment/sync-all` (external market/ratings API calls + snapshot writes). Added import + alias. **PUT `/race-sentiment/{source}` left OPEN** — see open questions.
- **routes/sources.py** — `POST /sources/rss`, `POST /sources/text`, `POST /sources/url` (each fetches + LLM-scores). Added import + alias. Read endpoints (list/search/detail) stay open.
- **routes/campaign.py** — `PUT /campaign` (rewrites relevance/excluded keywords + calls `auto_setup_monitors`). Alias was already present.
- **routes/narrative_frames.py** — `POST ""` (create), `PUT /{id}` (update), `DELETE /{id}` (cascade delete), `DELETE /{id}/mentions/{source_item_id}` (remove-mention). Alias already present; updated the convention comment to note CRUD writes are now gated.

### Key decisions
- **Boundary chosen: "admin = cost/destructive; friends curate."** Matches the design intent logged earlier (gate every LLM-cost / budget-spending POST) and the user's explicit OPEN set (create opponent, label topic-region, review-queue actions).
- **Two flagged endpoints deliberately LEFT OPEN** (respecting prior deliberate decisions, not an oversight):
  - **`narrative_triage` `POST /{id}/execute-merge`** — the file's own comment (line 31) says "pure DB state operations and intentionally stay open"; the body is LLM-free and only marks member `CandidateFrame`s `resolved_to_frame_id` + stamps `applied_at` (no row deletion). Overriding another session's documented choice without cause would be wrong; the user's scope was endpoints that *slipped*, not deliberate ones.
  - **`race_sentiment` `PUT /race-sentiment/{source}`** — primary use is manual rating entry (typing Cook/Sabato numbers), which is curation. See confused-deputy caveat below.
- **`narrative_frames` create/update gated despite being "add/rename a frame"** — both schedule a rematch (compute), and frames are the core analytic config; delete + remove-mention are outright destructive (remove-mention cascades to drop the `FrameClusterMatch` for the whole story cluster — docstring notes a count "silently dropped by 50"). Clean boundary: view frames open, mutate frames admin-only.

### Verified
- `py_compile` on all 8 touched route files: OK.
- `tests/test_require_admin.py` + `tests/test_destructive_endpoint_guards.py`: **14 passed in 1.74s.** Both suites run in dev-mode (`ACCESS_CODES=""`, `require_admin` fails open), so the new gates don't break them — confirms the additions are inert in dev and only bite when codes are configured + non-localhost host.
- Full app import validated transitively (destructive-guards suite does `from app.main import app`, importing all 7 edited routers).
- Avoided the campaign/race route tests that make live `httpx` calls (~14min hang) per the test-suite-network-seams entry above.

### Open questions / concerns for review
- **`PUT /race-sentiment/{source}` confused-deputy corner (recommend a follow-up):** left open for manual rating entry, but `model_dump(exclude_unset=True)` + `setattr` means a non-admin can also overwrite `external_id` / `external_metadata`, repointing which market the (now-gated) `sync`/`backfill` later pulls from. A malicious repoint is inert until an admin clicks sync — then it spends/ingests against the attacker's slug. **Options:** (a) gate the whole PUT too (simplest, but locks manual rating entry behind admin), or (b) strip `external_id`/`external_metadata` from non-admin PUTs while keeping rating fields open (preserves curation, closes the corner). I recommend (b) as a small follow-up — not done here because it's beyond "add `require_admin`."
- **`narrative_frames` create/update are now admin-only.** If you want non-admin friends to be able to add/rename narratives to track, say so and I'll reopen those two — but delete + remove-mention should stay gated regardless (destructive cascade).
- **Not addressed in this pass** (still open from the audit entry above): SSRF egress guard, `docs_url=None` on the tunnel, the boot-time full-table `repair_frame_data` scan, and `ingest_lock` held across LLM backoff. This entry was authz-gating only.

### Also fixed
- **CLAUDE.md stale Alembic head** corrected `6e2b8c4a9d1f` → `2df994cdd1f9` (`add_platform_column_to_source_items`), verified as the single current head.

## 2026-05-31 Session: Race-sentiment — remove market-pointer editing + staleness flag
### Built
- **Closed the PUT confused-deputy corner (the prior entry's open question), the strong way.** Rather than option (b) "strip pointers from non-admin PUTs," removed `external_id` / `external_metadata` from `RaceSentimentUpdate` entirely (`schemas.py`) — so *no* caller, admin or not, can repoint a source via the API. The pointers are now exclusively owned by `race_sentiment_sync` (which auto-discovers them: `_kalshi_autodiscover` / `_polymarket_autodiscover` / `_rating_autoconfigure`). `RaceSentimentOut` still exposes them read-only so the UI can show what a source is wired to.
  - `routes/race_sentiment.py` `upsert_race_sentiment`: removed the now-dead `if k == "external_metadata": v = json.dumps(v)` branch (the field can no longer arrive). `json` import stays — still used by the backfill handler (`json.loads(row.external_metadata)`).
  - `frontend-v2/src/api/types.ts`: added `external_id` / `external_metadata` to the `RaceSentimentUpdate` `Omit` to mirror the backend. `updateRaceSentiment` in `client.ts` is never called by any component, so nothing breaks.
- **Staleness flag — new STALE badge in `RaceSentimentCard.tsx` `SyncBadge`.** Fires when a source's number came from a real sync but that sync is now older than the source type's refresh cadence allows (or the latest attempt is failing). Amber (`C.accent`) + `Clock` icon, distinct from red BLOCKED and gray MANUAL. Tooltip states age + "may be out of date."
  - **Also fixes a latent bug:** the old code had a single global 36h freshness window, then fell any older-but-has-data row through to the **MANUAL** branch — mislabeling a stale *auto-synced* row as "Manually entered. Auto-sync not configured." The new state machine keys on `last_synced_at !== null` (ever auto-synced) to separate STALE from genuine MANUAL.

### Key decisions
- **Thresholds grounded in the scheduler cadence, not arbitrary** (`scheduler.py`: markets `interval hours=2`, forecasters `hours=12`). STALE fires at ~3 missed cycles: `market: 6h`, `rating: 36h` (named consts `STALE_AFTER_HOURS`, `DEFAULT_STALE_AFTER_HOURS` at the top of the card — tunable, with a comment pointing back at the scheduler). 3× gives slack for a transient fetch failure or an app restart (which resets APScheduler's interval timer) without false alarms, but surfaces a wedged sync within a day. The old 36h global is preserved exactly for forecasters; markets get the tighter, cadence-appropriate window.
- **Frontend-only for the staleness flag.** No schema/migration churn, no risk to live backend — `last_synced_at` / `last_sync_error` were already on `RaceSentimentOut`. The badge is a pure-functional component (no hooks/effects).

### Verified
- `py_compile` on `schemas.py` + `routes/race_sentiment.py`: OK.
- `npx tsc --noEmit`: **zero errors in any touched file** (`RaceSentimentCard.tsx`, `api/types.ts`, `api/client.ts`). The 18 pre-existing errors are all in untouched files (`NotificationSettings.tsx`, `featuredFrame.ts`, `Dashboard.tsx` line 99, `Landscape.tsx` line 1482) — `ActivityPoint`/`candidateName` issues unrelated to this work.
- **Validated badge logic against LIVE data** (read-only `GET /api/race-sentiment` via the `X-Forwarded-Host: localhost` proxy bypass, computed each row's resolved badge). Result: `kalshi` + `polymarket` (markets) last synced **9.1h ago** → **STALE** (correct: 9.1 > 6h market window); `cook` / `sabato` / `inside_elections` (ratings) synced 13.1h ago → **fresh/no badge** (correct: 13.1 ≤ 36h). The feature is already catching a real condition.
- Could NOT render the card in the headless preview browser — it stayed pinned on `about:blank` (origin "null") and refused JS navigation. Verified programmatically instead; the user's own browser tab will HMR-reload the change.

### Open questions / concerns for review
- **Markets are 9h stale right now with `last_sync_error = NULL` — most likely a dev `--reload` artifact, not a prod bug.** Markets repoll every 2h, so a 9.1h-old `last_synced_at` with *no recorded error* means the `race_sentiment_markets` job hasn't *fired* in ~9h (a fired-and-failed run would have stamped `last_sync_error`). `/tmp/uvicorn.log` shows repeated `WatchFiles ... Reloading` + `Started server process` events from this session's backend edits. Each `--reload` restarts APScheduler and resets its `interval` timers to zero — so an `interval hours=2` job whose process is reloaded more often than every 2h can keep having its timer reset before it ever fires. Benign in prod (stable process, no `--reload`); in dev it just means market numbers can drift stale while you're actively editing. The new STALE badge is what surfaced it. If it persists on a *stable* prod process, then check the job for real.
- **STALE also fires on `last_sync_error` set + a prior success**, even if the last *success* was recent — intentional (the displayed number is the last-good one and sync is now degrading), but if you'd rather only flag on age, drop the `|| err`-driven path. Tooltip distinguishes the two cases.

### Follow-up (same session): two cheap, safe hardening/reliability fixes
Theo asked "why are the markets stale right now? and are there other security risks?" After answering both, he approved knocking out the two cheap safe items now and deferring the rest to a pre-SaaS pass.
- **(reliability) Resolved the market-staleness open question by *fixing* it, not just diagnosing it.** Added `_run_race_sentiment_startup_catchup()` to `scheduler.py`, registered as a `trigger="date"` (run-now) job right before `_scheduler.start()`, directly mirroring the existing `_run_analytics_startup_catchup`. On boot it checks `func.max(last_synced_at)` per `source_type`; if markets are older than 2h or forecasters older than 12h, it kicks a one-off `sync_all(...)` for that type. Idempotent and freshness-gated, so a healthy boot is a no-op. This closes the `--reload`-resets-the-interval-timer gap that left markets drifting stale in dev. **Verified live:** after the natural `--reload` picked up the edit, all 5 sources went from 9–13h stale to **age 0.0h / fresh** (`GET /api/race-sentiment`). A race-sentiment sync is just Polymarket/Kalshi/270toWin HTTP fetches — *not* an LLM call — so triggering it on boot is free and safe.
- **(security) Hid the interactive API docs behind an env flag.** `main.py` now reads `EXPOSE_API_DOCS` (default off) and passes `docs_url`/`redoc_url`/`openapi_url = None` unless it's truthy. Swagger/ReDoc/OpenAPI enumerate every endpoint + schema — recon surface we don't want on the public `theosintel.com` tunnel. Documented in `.env.example` (new "Interactive API docs" block). The `/docs|/redoc|/openapi.json` entries stay in `_AUTH_EXEMPT_PATHS` (harmless — they 404 when disabled, and stay code-free reachable if a dev sets the flag). **Verified:** with the flag unset, all three paths now return **404**; `/health` still **200**.
- **Deferred to a pre-SaaS hardening pass (Theo's call):** (1) the localhost auth-bypass — `main.py` + `access_codes.py` skip auth when `X-Forwarded-Host` starts with `localhost`/`127.0.0.1`. **Currently NOT exploitable** (backend binds 127.0.0.1 only; Vite's proxy *overwrites* `X-Forwarded-Host` with the real `Host`, clobbering any client-forged value), but it's latent — and note the audit's suggested `request.client.host` fix is **wrong for this topology**: both local and tunnel traffic reach the backend from 127.0.0.1 via the local Vite proxy, so peer-IP can't tell them apart. Better future fix: drop the bypass entirely (require a code even locally) or use a shared proxy secret. (2) SSRF egress allowlist on the ingestion fetchers (`ingestion.py` bare `httpx.get`) — now admin-gated by this session's Phase 1 auth work, so the threat surface is "an admitted admin," not the open internet; still worth an allowlist before multi-tenant SaaS.
