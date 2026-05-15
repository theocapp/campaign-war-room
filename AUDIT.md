# Campaign War Room — Audit & Roadmap

_Last full audit: 2026-05-15. Re-run when major changes ship._

This document is the source of truth for the current state of the codebase and the work remaining to reach Meltwater-class capability. Read this before starting a new session.

---

## How to use this document

- Each **Phase** is one Claude Code session and one PR.
- Start a new session with: _"Read PRODUCT_BRIEF.md, ROADMAP.md, and the Phase X section of AUDIT.md, then propose a plan before writing code."_
- Update the **Status** line of a phase when it's done.
- Re-run the audit (delete and regenerate this file) after every 2–3 phases.

---

## What the product is supposed to be

A low-cost Meltwater alternative for small political campaigns. Locked-in design (from `PRODUCT_BRIEF.md`):

- Single Groq LLM call per article returning `{relevant, relevance_score, one_sentence, framing, needs_attention, reason}`.
- No knowledge graph, no embeddings, no auto-discovery.
- Human-defined narrative frames; LLM only suggests, never decides.
- Real test case: **Cognetti for PA-08** (Scranton).

---

## What works today (verified live 2026-05-15)

- Backend boots on Groq, frontend on Vite, full nav across 6 pages.
- 201 articles ingested today, 254 in review queue, 11 auto-suggested frames, 49 opponent attacks extracted.
- All 6 pages render with real data: Briefing (with race-situation memo), Narratives (with pulse), AI Audit, Opponent Tracker, RSS Feeds, Campaign Setup (with FEC directory).
- RSS scheduler runs on interval; manual ingest button works.
- TypeScript compiles clean.
- 269 of 303 backend tests pass.

---

## Critical bugs (live, user-visible)

| # | Bug | File | Fix |
|---|---|---|---|
| 1 | Raw HTML leaks into article summaries (`<a href="…">…</a>` shown as text) | `backend/app/services/ingestion.py` build_source_summary | Strip tags with BeautifulSoup `get_text()` |
| 2 | HTML entities not decoded in opponent quotes (`&#x2019;`) | `backend/app/services/opponent_analysis.py` | `html.unescape()` before storage |
| 3 | Duplicate opponents created by FEC import (`Rob Bresnahan` + `BRESNAHAN, ROB`) | `backend/app/routes/races.py` select endpoint | Dedup by FEC candidate ID before insert |
| 4 | Sidebar review-queue badge shows page size (31), not real count (254) | `frontend/src/components/Layout.tsx:53-55` | Call `getReviewQueueCount()` |
| 5 | Sidebar "Synced Xh ago" never re-fetches | `frontend/src/components/Layout.tsx:65-68` | Make 30s interval re-fetch, not just re-format |
| 6 | Auto-suggested frames mislabel `owner_type` (generic news → "opponent") | `backend/app/services/narrative_frames.py` suggest_frames | Tighten prompt + validate |
| 7 | Frame descriptions hardcode specific events instead of categories | same | Add "describe the category, not a specific event" to prompt |

## Pipeline gaps (the central flow is incomplete)

| # | Gap | Detail |
|---|---|---|
| 8 | Issue clustering not wired to live ingest | `assign_issues_to_source` only runs in `reanalyze_source`. Tables are empty after pivot. |
| 9 | Opponent activity extraction not wired to live ingest | `analyze_source_for_opponents` only runs in reanalyze. The 49 attacks shown were seeded earlier. |
| 10 | Priority bumps silently dead | `_compute_priority_score` reads `issue_mentions` + `opponent_activities` which are never populated on ingest |
| 11 | `rematch_all` will time out | Per-article LLM call in-request; 100 articles = 100 sequential network calls |
| 12 | Per-article ingest = 2 LLM calls | `campaign_analysis.analyze` + `match_article_to_frames`; could be one combined call |
| 13 | `relevance_reasons` column exists but never populated | LLM returns `reason`, ingestion doesn't store it on the field |

## Debt / cleanup

| # | Item |
|---|---|
| 14 | 45 files uncommitted (the pivot). Commit it as one real commit. |
| 15 | 34 backend tests reference deleted modules (`app.routes.issues`, `app.routes.talking_points`, `app.routes.monitors`). Delete them. |
| 16 | 30 of 35 frontend tests fail (`api.getRescoreStatus` not mocked). Fix the mock. |
| 17 | Zero tests for `campaign_analysis.analyze`, `match_article_to_frames`, `suggest_frames`, or `/api/briefing/morning`. Add them. |
| 18 | Makefile is entirely dead (all targets reference deleted scripts). Delete it. |
| 19 | Dead schemas in `models.py`: `Narrative`, `NarrativeMention`, `CandidateMessageLibrary`, `CandidateNarrative`, `CanvassingNote`, `ManualCapture`, `GeneratedTalkingPoint`. |
| 20 | Dead service code: `risk_checks.py`, talking-points machinery in `intelligence.py` + `llm_provider.py`. |
| 21 | ~30 unused endpoints in `frontend/src/api/client.ts`; ~250 lines of dead types in `types.ts`. |
| 22 | `apiToast` registered but never called — all API errors silently swallowed. |
| 23 | `SourceMonitor` CRUD wired in client.ts but no UI page exists. |
| 24 | Reset workspace doesn't delete `Narrative*` or `NarrativeFrame*` rows. |
| 25 | CORS hardcoded to `localhost` in `main.py:33`. |
| 26 | `db.py:139-162` re-runs stance backfill UPDATE on every startup. |
| 27 | `isinstance(v, Mock)` from `unittest.mock` in production validator (`schemas.py:50`). |
| 28 | No mobile/responsive layout; Tailwind installed but unused (inline styles everywhere). |
| 29 | README.md is stale (documents removed KG features, multi-LLM, canvassing). Rewrite or remove sections. |

---

## Gap vs. Meltwater (structural)

| Capability | Status | Gap |
|---|---|---|
| Multi-source coverage | RSS only | No direct news crawl, no X/TikTok/Facebook/Reddit, no broadcast/podcasts |
| Full-text search | None | No search bar anywhere |
| Sentiment | None | Not in schema, not in prompt |
| Share of voice | None | No volume %, no by-outlet breakdown |
| Trend / velocity | 7d vs 14d card only | No time-series, no charts, no spike detection |
| Influencer / author ranking | None | `author` column unused |
| Alerts | `needs_attention` bool | No threshold rules, no email/SMS/Slack |
| Exports | None | No PDF/CSV, no scheduled digest |
| Source-detail view | None | Articles open external URL only |
| Filters / saved views | None | Every list is flat |
| Geo | Keywords only | No outlet location |
| Auto narrative discovery | Manual + LLM suggest | No embeddings + clustering (locked-out by brief, revisit later) |
| Multi-tenant / auth | None | Singleton CampaignConfig; single device |
| Collaboration | None | No notes, comments, assignments |
| Fact-checking | None | No claim-vs-claim contradiction tracking |

---

## Roadmap

### Phase 0 — Stop the bleeding *(1 session, ~2–3 hrs, low risk)*

**Goal**: ship a clean, committed baseline with the live bugs fixed.

**Status**: done (2026-05-15, branch `phase-0-cleanup`)

Tasks:
- [x] Fix bugs 1–7 above.
- [x] Wire opponent extraction into the single LLM call (gaps 9, 10) and delete the issue-clustering wiring (gap 8) — Option C from the plan.
- [x] Persist `relevance_reasons` (gap 13).
- [x] Delete 6 stale backend test files + fix the frontend rescore-status mock so all 35 tests pass (debt 15, 16).
- [x] Delete dead schemas, routes, services, and client/types entries (debt 19, 20, 21, 23).
- [x] Delete Makefile (debt 18).
- [x] Make `apiToast` actually fire on API errors (debt 22).
- [x] Make reset_workspace delete narrative_frame tables (debt 24).
- [x] Move CORS origins to `CORS_ALLOW_ORIGINS` env var (debt 25).
- [x] Drop the dead `narratives` / stance-backfill migration that ran every startup (debt 26).
- [x] Remove production `isinstance(v, Mock)` (debt 27).
- [x] Update README.md to match current product (debt 29).
- [x] Idempotent startup backfill cleans pre-existing entity-encoded quotes + merges legacy duplicate-opponent rows so the live PA-08 DB is consistent on first boot after the pivot.

**Acceptance**: backend tests green for new Phase 0 tests (33 added, 0 regressions), frontend tests green (35/35), click through all 6 pages, no HTML in summaries, sidebar badge shows 254, only one Opponent row for Bresnahan, all attack quotes use real apostrophes.

**Remaining backend test debt (not Phase 0 scope)**: ~50 integration tests in `test_campaign_initialization.py`, `test_campaign_initialize.py`, `test_campaign_auto_monitors.py`, `test_election_date_inference.py`, `test_race_directory.py`, `test_services.py` still hit the live Groq endpoint and fail under daily token-quota rate-limiting. Mocking the LLM provider in tests is a Phase 1 task.

---

### Phase 1 — Correct, cheap, searchable ingest *(1 session, ~1 day)*

**Goal**: ingest does the right thing once, full-text search works, source-detail page exists.

**Status**: not started

Tasks:
- [ ] Combine `campaign_analysis` + `match_article_to_frames` into one LLM call returning both verdict and frame matches (gap 12).
- [ ] Move `rematch_all` out of HTTP request → background job (gap 11).
- [ ] Add `sentiment` column on SourceItem + extend the LLM prompt to return it.
- [ ] SQLite FTS5 virtual table over `source_items(title, raw_text)`. Expose `GET /api/search?q=`.
- [ ] Add a source-detail page in the frontend (`/sources/:id`) showing extracted text, framing, sentiment, reasons, frame mentions.
- [ ] Add `AbortController` to OpponentTracker selection.
- [ ] Make the briefing summary cache key per-campaign.

**Acceptance**: search works, source-detail page renders, ingest LLM cost halved, sentiment shows on each article.

---

### Phase 2 — Real coverage *(1 session, 1–2 days)*

**Goal**: more sources than RSS.

**Status**: not started

Tasks:
- [ ] Generic article crawler with `trafilatura` for outlets without RSS.
- [ ] Reddit ingestion (free API).
- [ ] X/Twitter ingestion (try `snscrape` or nitter RSS bridge; otherwise X API).
- [ ] Decide on Facebook strategy: Apify, a paid vendor, or skip.
- [ ] Author + outlet authority table; manual ranking UI.
- [ ] Geo-tag outlets (state + city); make "PA-08 only" filter real.

**Acceptance**: at least 3 non-RSS sources flowing; outlets have a location and authority score.

---

### Phase 3 — Analytics layer *(1 session, 1–2 days)*

**Goal**: charts, trends, filters.

**Status**: not started

Tasks:
- [ ] Time-series API: `GET /api/frames/{id}/timeseries?bucket=day&days=30`.
- [ ] Sparkline on every frame card.
- [ ] Per-frame detail page with day-by-day chart, top sources, top mentions.
- [ ] Share-of-voice donut: candidate vs opponent vs neutral, per frame, per week.
- [ ] Velocity / spike detection: rolling 24h vs 7d baseline; surface on Briefing.
- [ ] Filter chips on Review Queue, Briefing, Narratives (source, date range, score, owner type, mentions).

**Acceptance**: at least one chart on each list page; filtering works.

---

### Phase 4 — Workflow + alerting *(1 session, 1 day)*

**Goal**: actionable, not just viewable.

**Status**: not started

Tasks:
- [ ] Alert rules: `frame X > Y mentions / 24h`, `opponent attack matches keyword`, etc.
- [ ] Email digests via Resend or SES; daily and weekly cadence.
- [ ] Slack webhook integration.
- [ ] Saved views (`/review?filter=opponent&score>70`).
- [ ] PDF + CSV exports of briefings, frames, articles.
- [ ] "Response drafted" status on articles + free-text team notes per article.

**Acceptance**: alert fires to email + Slack; export produces a clean PDF.

---

### Phase 5 — Multi-tenant + auth *(1 session, 1–2 days)*

**Goal**: more than one campaign at a time, more than one device.

**Status**: not started

_Defer unless we plan to onboard a second campaign. For Cognetti alone this is unnecessary._

Tasks:
- [ ] `Workspace` model; scope every table to a workspace_id.
- [ ] Auth via Clerk or magic-link.
- [ ] RBAC: admin / staff / read-only.
- [ ] Per-workspace LLM keys.

**Acceptance**: two campaigns running side-by-side with isolated data.

---

### Phase 6 — Auto narrative discovery *(1 session, 2+ days)*

**Goal**: surface emerging narratives without a human defining them first.

**Status**: not started (and intentionally locked out by PRODUCT_BRIEF — revisit only after Phase 4 has been used in a real campaign for 2+ weeks)

Tasks:
- [ ] Embed every article (OpenAI `text-embedding-3-small`).
- [ ] Weekly HDBSCAN clustering on the last 30 days of articles.
- [ ] LLM labels each cluster, proposes new frames with proper category descriptions.
- [ ] Merge-duplicate frame UX.

**Acceptance**: weekly job proposes 2–5 new frames; staff can accept/reject/merge.

---

### Phase 7 — Polish vs. Meltwater *(ongoing)*

- [ ] Broadcast ingestion (Otter / AssemblyAI on local TV/radio).
- [ ] Fact-checking layer: extract claims, compare to past claims by same speaker, flag contradictions.
- [ ] Mobile-responsive layout.
- [ ] A11y pass.
- [ ] Real design system (`<Button>`, `<Card>`, `<Modal>`).

---

## Decision log

_(Add an entry when you make a non-obvious choice that future sessions need to know.)_

- **2026-05-15** — Knowledge graph removed (commit `d4c8bcd`). Do not re-add embeddings/clustering until after Phase 4.
- **2026-05-15** — Auto narrative discovery is explicitly locked out by PRODUCT_BRIEF; only revisit in Phase 6 after real-campaign use.
- **2026-05-15** — Groq is the only supported LLM in production; OpenAI/Anthropic providers exist as fallback only and should not be advertised as features.
- **2026-05-15 (Phase 0)** — Option C selected for live-ingest wiring: opponent activity extraction rides in the same per-article LLM call (extending its JSON schema with `opponent_attacks`), and issue-clustering wiring was deleted entirely. Keeps the PRODUCT_BRIEF "one LLM call per article" invariant.
- **2026-05-15 (Phase 0)** — `init_db()` now runs a one-shot idempotent backfill (`_phase0_backfill`) to clean legacy entity-encoded opponent quotes and merge duplicate Opponent rows. Idempotent on a clean DB; safe to leave permanently.
