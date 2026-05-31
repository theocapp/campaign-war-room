# Campaign War Room — Claude Guide

## Inter-session review protocol

Multiple Claude Code sessions work on this project simultaneously. To coordinate:

**On every session start:** Read `INTER_SESSION.md` in the project root. It logs decisions, concerns, and open questions from all sessions. If it contains unresolved issues relevant to your work, address them before proceeding.

**When you finish a meaningful chunk of work:** Append a dated entry to `INTER_SESSION.md` describing what you built, the key decisions you made, and any concerns or open questions for other sessions to review.

**When you spot a problem with another session's work:** Write your critique directly into `INTER_SESSION.md` under that session's entry. Be specific — file name, line number, what's wrong and why.

**Format:**
```
## [date] Session: [brief topic]
### Built
- ...
### Key decisions
- ...
### Open questions / concerns for review
- ...

### Review from another session
- ...
```

The goal is consensus through written exchange, not just parallel building. If two sessions disagree on an approach, the disagreement and resolution should be logged here.

---

## Recurring tasks

**Memory audit due: 2026-06-13** (last run: 2026-05-30).

On session start ON OR AFTER the due date, proactively offer to re-run the audit before doing other work. **Methodology** (grep-driven, ~5k tokens, very cheap):
1. `cd /Users/theo/.claude/projects/-Users-theo-noctua/` — the local Claude Code project transcript directory
2. Python-grep all `*.jsonl` files for user-message correction signals: `lmao|lmfao|lol`, `huh\?|wait what`, `^no[,.\s]`, `that'?s not|that'?s wrong`, `why (did|would|are) you`, `how (could|did) you`, `i think you|i'?m not sure`, `i'?m not criticizing`, `^actually|^wait`, `stop (doing|writing)`, `you shouldn'?t`, `^don'?t`
3. Filter to `"operation":"enqueue"` lines (raw user typing); skip content starting with `<task-notification>`, `<system-reminder>`, `<command-`
4. Cluster hits by theme, drop one-offs and situation-specific corrections
5. Cross-reference against existing feedback memories in `/Users/theo/.claude/projects/-Users-theo-noctua/memory/`
6. Propose 3-8 new or refined memory entries WITH evidence trails (dated quotes from transcripts)
7. **Present for user approval — write nothing without sign-off.** Update this date line afterwards.

The previous audit found these patterns (already saved): arbitrary-constants-need-grounding, chatgpt-second-opinion-workflow, sanity-check-metric-directions, simulate-human-state-in-procedures.

---

## What this project is

A political campaign intelligence tool originally built for Paige Cognetti's congressional race (PA-08). It monitors news, RSS feeds, Reddit, Bluesky, and Mastodon; scores articles with AI; tracks narrative frames; and surfaces a daily briefing.

**This is live campaign software — be careful with backend changes.**

It is also being productized as a SaaS platform called **NOCTUA** — a sellable narrative intelligence tool for any political campaign. The brand name and logo (NOCTUA wordmark with barn owl O letterform) are in active development. Keep this dual context in mind: decisions should work for both the current Cognetti race and a generalizable product.

---

## Running the app

The user starts both servers manually in their terminal using `nohup … & disown` so they survive terminal closure and Claude session teardown. A long-running `cloudflared` tunnel is pointed at port 5174, so killing Vite causes 502 Bad Gateway errors in the user's live browser. **Hands off the dev servers** — see the rule below the commands.

**Frontend** (React/Vite — port 5174):
```bash
cd /Users/theo/noctua/frontend-v2 && nohup npm run dev > /tmp/vite.log 2>&1 & disown
```
The Claude Preview tool uses `.claude/launch.json` — server name is `frontend`. `preview_start` will reuse the already-listening port instead of spawning a duplicate.

**Backend** (FastAPI/Python — port 8000):
```bash
kill $(lsof -ti:8000) 2>/dev/null; sleep 2; cd /Users/theo/noctua/backend && nohup .venv/bin/uvicorn app.main:app --reload > /tmp/uvicorn.log 2>&1 & disown
```
Must use the project venv — bare `uvicorn` will fail with import errors. The frontend proxies `/api/*` → `http://localhost:8000` via Vite config.

**Logs**: `tail -f /tmp/vite.log` or `tail -f /tmp/uvicorn.log`.

**Do not stop the user's dev servers from a Claude session.** Concretely:
- Do NOT call the Claude Preview tool's `preview_stop` on the `frontend` server.
- Do NOT run `kill` against ports 5174 or 8000 from Bash, even "just to restart."
- If a dev server is genuinely broken and needs a restart, ASK FIRST — the user may be mid-test, and the cloudflared tunnel returns 502s the moment Vite goes down.
- `preview_start`, `preview_eval`, `preview_console_logs`, `preview_snapshot`, etc. are fine — they don't kill the process.

This rule exists because previous sessions killed Vite (via session teardown or explicit stop calls), and the user only finds out when their tunnel URL returns 502. See INTER_SESSION.md 2026-05-29 for the diagnosis.

**Database**: Postgres 15 — local on `postgresql://theo@localhost:5432/noctua` (cutover from SQLite on 2026-05-29 — see INTER_SESSION.md). The `DATABASE_URL` is read from `.env` by `app/db.py`. The pre-cutover SQLite backup (`backend/war_room.db.cutover-20260529-050401`) and `.env.bak-PRE-CUTOVER` are the rollback artifacts — keep them until ~2026-06-26 (Phase 5 cleanup). Never reset the DB without asking first.

---

## Frontend structure

```
frontend-v2/src/
  pages/
    Dashboard.tsx          # Landing page — DDHQ 3-column layout
    Narratives.tsx         # Full narrative battlefield view
    NarrativeDetail.tsx    # Single frame detail + variant chart
    MorningBriefing.tsx    # Daily briefing document
    Articles.tsx           # Article list / feed
    ArticleDetail.tsx      # Single article detail modal
    ReviewQueue.tsx        # Article triage
    Opponents.tsx          # Opponent tracker
    Monitors.tsx           # Source monitors
    Analytics.tsx          # Charts and analytics
    Landscape.tsx          # 2D narrative landscape view
    EntityNetwork.tsx      # Knowledge graph visualization (partially mock)
    GeographicOverlay.tsx  # District geographic view
    Timeline.tsx           # Event/article timeline
    SearchResults.tsx      # Full-text search results page
    Notifications.tsx      # Notification center
    Setup.tsx              # Campaign setup wizard

  components/
    Layout.tsx             # Top nav + sidebar shell
    Sidebar.tsx            # Left nav sidebar
    SearchBar.tsx          # Global search (FTS5 backend, debounced)
    ThemeToggle.tsx        # Light/dark mode toggle
    NotificationsBell.tsx  # Header notification bell
    NotificationsList.tsx  # Notification dropdown
    NotificationSettings.tsx
    PromoteModal.tsx       # Promote narrative frame modal
    InfoTooltip.tsx

  api/
    client.ts              # All API calls — single `api` object with typed methods
    types.ts               # All TypeScript types shared across frontend
  index.css                # Global styles + DDHQ design tokens
  App.tsx                  # Route definitions
```

**Tech stack**: React 18, TypeScript, Tailwind CSS v4, Vite, Recharts, React Router v6.

**Design system**: DDHQ-inspired, supports light and dark mode via `data-theme` on `<html>`. Tokens in `@theme {}` in `index.css`:
- Backgrounds: `#121212` (bg1) / `#171717` (bg2) / `#262626` (bg3)
- Accent: `#ffbf00` (golden yellow) — active nav, CTAs, highlights
- Candidate blue: `#0059c2`, Opponent red: `#d71913`
- Border: `#434343`, radius: `0.625rem`

**Tailwind setup**: Uses `@tailwindcss/vite` plugin (not PostCSS). No `postcss.config.js`.

**Path alias**: `@/` → `./src/` (configured in `tsconfig.json` and `vite.config.ts`).

---

## Backend structure

```
backend/app/
  main.py           # FastAPI app entry, lifespan, scheduler startup
  models.py         # SQLAlchemy ORM models — check before adding any table
  schemas.py        # Pydantic schemas
  db.py             # DB session, engine, dialect detection, init_db (runs Alembic upgrade head)
  alembic/          # Alembic migration tree — all schema changes go here as new version files
  seed.py           # Seed data
  routes/           # One file per API domain — all prefixed /api/
  services/         # ~50 service files — see directory for full list
```

**Key services** (the ones most sessions will touch):

| File | What it does |
|------|-------------|
| `campaign_analysis.py` | Single LLM call per article: relevance + summary + framing + frame matching |
| `rescore.py` | Parallel rescore job — re-scores all articles when frames change |
| `ingestion.py` | RSS + crawl orchestration (Readability + paywall fallbacks) |
| `narrative_frames.py` | Frame match + suggest + variant reconciliation |
| `frame_variants.py` | HDBSCAN variant clustering + LLM naming |
| `frame_momentum.py` | Trend × narrative cross-signal classifier |
| `entity_extraction.py` | KG entity extraction — LLM prompt + canonicalization + persistence |
| `story_clustering.py` | SimHash-based story cluster dedup |
| `llm_provider.py` | LLM abstraction — `get_provider()` and `get_judge_provider()` |
| `briefing_summary.py` | Morning briefing memo. `get_or_generate()` = v1 prose. `get_or_generate_grounded()` = v2 grounded memo with `[C1]` citations. |
| `briefing_retrieval.py` | Structured intermediate for v2 briefing: `top_claims_for_briefing()` + `top_entities_for_briefing()` (race-allowlist, alias-merged). |
| `source_display.py` | `display_source_name()` — best-known publisher for an article (outlet.name → publisher_domain prettified → source_name fallback). Use everywhere article sources surface to the frontend; do NOT use raw `source_name`. |
| `scheduler.py` | APScheduler — runs ingestion/sync on a timer |
| `fec_monitor.py` | FEC filing monitor — campaign finance activity |
| `gdelt_backfill.py` | Historical GDELT article backfill |
| `bluesky_firehose.py` | Bluesky jetstream real-time ingestion |
| `opponent_analysis.py` | Opponent activity extraction and tracking |

Social ingestion: `ingestion_reddit.py`, `mastodon_ingest.py`, `twitter_scraper.py`, `bluesky_scraper.py`

**LLM providers**:
- `get_provider()` — ingestion/scoring chain (Groq default with fallbacks)
- `get_judge_provider()` — OpenAI gpt-4o-mini primary, Groq fallback. Used for anything that writes prose or makes judgments: briefing, variant naming, frame reclassification.

**All API routes are prefixed `/api/`** in the FastAPI app.

---

## Key data concepts

- **Narrative Frame**: A named message or attack being tracked (e.g. "Bresnahan's Healthcare Record"). Has `owner_type`: `candidate` | `opponent` | `media`.
- **Stage**: `emerging → spreading → mainstream → fading → dormant` — computed from article match velocity.
- **Trend**: `up | flat | down` — week-over-week outlet count direction.
- **Variant**: A specific phrasing/claim within a frame (HDBSCAN-clustered embeddings, LLM-named).
- **Momentum signal**: Per-frame label (`viral` / `missing_coverage` / `elite_only` / `stable`) computed daily by `frame_momentum.py`.
- **Story Cluster**: SimHash-deduped article cluster — one wire story across 5 outlets = 1 cluster, not 5 mentions.
- **Entity**: A canonical person, organization, bill, or location extracted from articles by the KG pipeline. Stored in `entities`, `entity_mentions`, `entity_relations` tables.
- **Backfill**: One-time historical scoring job. Runs once per campaign setup.
- **Rescore**: Re-scores all articles when frames change. **Slow — see cautions below.**

---

## Database state (as of 2026-05-29 cutover)

- `source_items`: **21,225 total articles**
- `entities`: 4,389 canonical entities (v15.0 quote-anchored claim corpus, see KG policy below)
- `narrative_frames`: 39 active frames being tracked
- Search index: Postgres `tsvector` GIN index on `source_items.search_tsv`, maintained by a `BEFORE INSERT/UPDATE` trigger — powers `/api/search`. SQLite `source_items_fts` (FTS5) is retired; the dialect-aware code path stays in `services/search_index.py` until Phase 5 cleanup.
- Alembic head: `2df994cdd1f9` (add_platform_column_to_source_items). New schema changes go in a new Alembic version file (`cd backend && .venv/bin/alembic revision -m "..."`), NOT in any imperative migration block.

---

## Running tests

```bash
cd backend && python -m pytest tests/
```

The frontend has no test suite — verify changes via the Claude Preview tool against the running dev server.

---

## Workflow preferences

- **Do not take screenshots in the preview tool.** The user keeps the app open in another browser tab and reviews changes there. Screenshots consume tokens for no value. Use `preview_eval`, `preview_snapshot` (accessibility tree), or `preview_network` to verify behavior programmatically. Only screenshot if the user explicitly asks for one.

---

## Data validation protocol

When asked to assess data quality, validate extraction output, or decide whether downstream features will work on the data:

- **Do not make claims based on aggregate counts alone.** A row count doesn't tell you whether the rows are usable. "We have 600 labeled records" is not validation.
- **Sample concrete records and read them.** 20-50 random samples is enough to spot extraction failures that the counts hide. Fewer is too prone to lucky/unlucky draws.
- **Run programmatic invariant checks on a larger sample (100-200):** verbatim-substring guarantees, length bounds, foreign-key resolution, dedup integrity. State the pass/fail rate explicitly.
- **Look for distributional skew, not just totals:** per-outlet, per-entity, per-time-window. A balanced overall total can hide one-outlet dominance, canonicalization gaps, or label bias.
- **When running tests (`pytest`, etc.)** for a feature that touches the DB, also load and inspect the real data the test mocks. Tests can pass while the real data is unsuitable.
- **State both what looks fine and what looks broken.** Don't hedge toward "looks good" without naming the specific things you checked and what their pass rate was.

Apply this standard before claiming a backfill, extraction, migration, or new feature is ready to ship — and before asking the user for approval to proceed.

---

## Things to be careful about

- **Schema changes go through Alembic.** When `models.py` changes, generate a migration with `cd backend && .venv/bin/alembic revision -m "..."` and hand-author the upgrade/downgrade — autogenerate is lossy. Migrations run automatically on app startup via `init_db()`. Do NOT add `ALTER TABLE` in `db.py` — the old `_migrate()` block was retired in the Postgres migration. Test any new migration against Postgres locally before committing; SQLite-only syntax like `DEFAULT 0` for booleans or `INSERT OR IGNORE` will fail on Postgres (use `sa.false()` and `ON CONFLICT DO NOTHING`).
- **Don't reset the database** without explicit confirmation from the user. `DROP DATABASE noctua` deletes everything; the pre-cutover SQLite backup is the only fallback.
- **The rescore job** (`/api/admin/rescore-articles`) runs over **21,225 articles** at ~2 articles/min. That is ~177 hours. Do not trigger it casually. Confirm with the user first.
- **LLM calls cost money** — scoring and briefing services call OpenAI/Groq. Avoid triggering them in loops or during testing.
- **Backend changes affect live data** — this is a real campaign, not a demo.
- **Never run ad-hoc scripted writes against the live Postgres `noctua` DB.** If you need to test ingestion logic against real URLs, set `DATABASE_URL` to a throwaway target (e.g. `sqlite:////tmp/test_ingest.db` or `postgresql+psycopg://theo@localhost:5432/noctua_scratch`) before the script runs. A previous session (2026-05-27) injected 211 "Test Article" rows into the live SQLite via an ad-hoc script with mocked `httpx.get` — easy to repeat now that Postgres also accepts writes from the same env. When in doubt, point at a scratch DB.

## KG / entity-extraction policy (2026-05-29)

**Do not build new standalone KG features.** The knowledge-graph ambition has been retreated from twice on this project — first when the original KG was replaced by narrative-frames (accuracy), then again at the v14.x → v15.0 transition (LLM systematically projecting nonexistent edges onto prose). v15.0 deliberately retired the action predicates (`endorses`, `criticizes`, `attacks`, `voted_for`, `voted_against`, `co_sponsored`) and the `event` entity type. What remains is a **quote-anchored claim corpus** (`claim_records`, `claim_record_entities`, `claim_supports`), not a graph.

**Where v15.0 data should surface:**
- ✅ As supporting-quote evidence inside narrative frames (NarrativeDetail → "Supporting quotes" section, backed by `/api/narrative-frames/{id}/quote-evidence`)
- ✅ As verbatim citations in the morning briefing (the grounded v2 path — now the default — at `briefing_summary.get_or_generate_grounded` + `briefing_retrieval.top_claims_for_briefing`)
- ✅ As an evidence side-panel on entity detail pages, when those exist
- ❌ NOT as a standalone "Entity Network" force-graph (hidden 2026-05-29 — `/entity-network` redirects to `/`, component file preserved in `frontend-v2/src/pages/EntityNetwork.tsx`)
- ❌ NOT as a "KG Contradictions" review queue (hidden 2026-05-29 — backing tables `entity_relations` (1,757 rows) and `entity_mentions` (19,741 rows) are frozen as legacy v14.x output, not deleted; `/entity-review` redirects to `/review`)
- ❌ NOT as new ontology-drift dashboards, partisan-guard layers, contradiction detectors, multi-hop network UI, or anything that asks "what does this entity-pair imply" — those try to derive edges that v15.0 explicitly retreated from

**If a future session is tempted to bring KG features back**, first re-read the v15.0 docstring in `app/services/extractor_versions.py` and the entries in INTER_SESSION.md Sessions A–F. The lessons are: typed-edge inference from prose hits an accuracy ceiling; verbatim quote evidence is the durable abstraction; the campaign workflow runs on narrative-frames + briefing + article scoring, not on graph traversal.

**v15.0 backfill is complete** (as of 2026-05-29): 1,648 + 1,120 = 2,768 articles processed → 3,833 `claim_records`. No re-run needed without a prompt change. If you bump `EXTRACTOR_VERSION`, use `scripts/entity_drift_reextract.py --apply` to re-extract only stale articles.
