# Campaign War Room — Claude Guide

## What this project is
A real political campaign intelligence tool for Paige Cognetti's congressional race (PA-08). It monitors news, RSS feeds, Reddit, and social media; scores articles with AI; tracks narrative frames; and surfaces a daily briefing. This is live campaign software — be careful with backend changes.

## Running the app

**Frontend** (React/Vite — port 5174):
```bash
cd frontend-v2 && npm run dev
```
The Claude Preview tool uses `.claude/launch.json` — server name is `frontend`.

**Backend** (FastAPI/Python — port 8000):
```bash
cd backend && uvicorn app.main:app --reload
```
The frontend proxies `/api/*` → `http://localhost:8000` via Vite config.

**Database**: SQLite at `backend/war_room.db`. Never delete or reset it without asking first.

## Frontend structure

```
frontend-v2/src/
  pages/          # One file per route
    Dashboard.tsx         # Landing page — DDHQ 3-column layout (filter | narratives | watchlist)
    Narratives.tsx        # Full narrative battlefield view
    MorningBriefing.tsx   # Daily briefing document
    ReviewQueue.tsx       # Article triage
    Opponents.tsx
    Monitors.tsx
    Analytics.tsx
    Setup.tsx
  components/
    Layout.tsx            # Top nav bar (DDHQ-style) — NAV array controls nav links
  api/
    client.ts             # All API calls — single `api` object with typed methods
    types.ts              # All TypeScript types shared across frontend
  index.css               # Global styles + DDHQ design tokens
  App.tsx                 # Route definitions
```

**Tech stack**: React 18, TypeScript, Tailwind CSS v4, Vite, Recharts, React Router v6.

**Design system**: DDHQ-inspired dark theme. Tokens defined in `@theme {}` in `index.css`:
- Backgrounds: `#121212` (bg1) / `#171717` (bg2) / `#262626` (bg3)
- Accent: `#ffbf00` (golden yellow) — active nav, CTAs, highlights
- Candidate blue: `#0059c2`, Opponent red: `#d71913`
- Border: `#434343`, radius: `0.625rem`

**Tailwind setup**: Uses `@tailwindcss/vite` plugin (not PostCSS). No `postcss.config.js`.

**Path alias**: `@/` → `./src/` (configured in `tsconfig.json` and `vite.config.ts`).

## Backend structure

```
backend/app/
  main.py           # FastAPI app entry, lifespan, scheduler startup
  models.py         # SQLAlchemy ORM models
  schemas.py        # Pydantic schemas
  db.py             # DB session, init_db
  seed.py           # Seed data
  routes/           # One file per API domain (narrative_frames, analytics, etc.)
  services/
    campaign_analysis.py  # LLM article scoring against narrative frames (v2 prompt + per-claim extraction)
    rescore.py            # Parallel rescore job (ThreadPoolExecutor with per-key pinning)
    narrative_frames.py   # Frame match + suggest + variant reconciliation
    frame_variants.py     # HDBSCAN density-based variant clustering + LLM naming
    frame_momentum.py     # Trend × narrative cross-signal classifier
    embeddings.py         # Gemini gemini-embedding-001 wrapper
    scheduler.py          # APScheduler — runs ingestion/sync on a timer
    ingestion.py          # RSS + crawl orchestration (with Readability + paywall fallbacks)
    wayback.py            # archive.today + Wayback Machine fetch fallbacks
    feed_discovery.py     # Probes GDELT-found domains for RSS feeds
    bluesky_scraper.py    # Bluesky firehose ingestion
    auto_review.py        # Auto-reviews low-relevance items
    briefing_summary.py   # Morning briefing memo (uses get_judge_provider)
    llm_provider.py       # LLM abstraction — get_provider() and get_judge_provider()
    gdelt_backfill.py     # Historical GDELT article backfill
    gdelt_monitor.py      # Real-time GDELT polling + daily tone snapshots
    gdelt_bigquery.py     # GDELT BigQuery loader with V2Tone / V2Persons / V2Themes
    google_trends.py      # Per-DMA + statewide interest-over-time
```

**LLM providers**: `get_provider()` is the ingestion/scoring chain (Groq default with fallbacks); `get_judge_provider()` is OpenAI gpt-4o-mini primary with Groq fallback, used by anything that writes prose or makes judgments (briefing, variant naming, frame reclassification, etc.).

**All API routes are prefixed `/api/`** in the FastAPI app.

## Key data concepts

- **Narrative Frame**: A named message or attack being tracked (e.g. "Bresnahan's Healthcare Record"). Has `owner_type`: `candidate` | `opponent` | `media`.
- **Stage**: `emerging → spreading → mainstream → fading → dormant` — computed from article match velocity.
- **Trend**: `up | flat | down` — week-over-week outlet count direction.
- **Variant**: A specific phrasing/claim within a frame (HDBSCAN-clustered embeddings, LLM-named). Visible in the Narrative Detail page as a stacked variant-evolution chart.
- **Momentum signal**: Per-frame label (`viral` / `missing_coverage` / `elite_only` / `stable`) computed daily by `frame_momentum.py`.
- **Backfill**: One-time historical scoring job. Runs once per campaign setup.
- **Rescore**: Re-scores all articles when frames change. Can take hours.

## Running tests

```bash
# Backend
cd backend && python -m pytest tests/
```

The frontend has no test suite yet — verify changes via the Claude Preview tool against the running dev server.

## Things to be careful about

- **Don't change the backend schema** (models.py) without flagging it — migrations aren't automated and bad schema changes corrupt the SQLite DB.
- **Don't reset the database** without explicit confirmation from the user.
- **The rescore job** (`/api/admin/rescore-articles`) is slow (~2 articles/min over ~1900 articles). Don't trigger it casually.
- **LLM calls cost money** — the scoring and briefing services call Claude/OpenAI. Avoid triggering them in loops.
- **Backend changes affect live data** — this is a real campaign, not a demo.
