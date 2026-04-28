# Campaign War Room AI

An AI-powered political campaign intelligence dashboard for local and mid-level candidates.

## What it does

- **Dashboard** — Setup checklist, review queue count, top issues, opponent activity, suggested actions, risk warnings
- **Issue Tracker** — Issues clustered by topic (housing, crime, schools, etc.) with trend, urgency, and linked sources
- **Opponent Tracker** — Claims, attacks, promises, and contradiction notes for tracked opponents
- **Review Queue** — Triage new intelligence: mark reviewed, dismiss noise, boost priority, generate talking point
- **Canvassing Insights** — Aggregate field data by precinct and issue; privacy-preserving
- **Talking Points** — Evidence-grounded messaging with history, risk warnings, and cited sources (door / interview / debate / social)
- **Sources** — Full source library with text paste, URL fetch, and detail drawer
- **RSS Feeds** — Configure persistent feeds; ingest one or all; duplicates skipped automatically
- **Campaign Setup** — Candidate profile, key priorities, election date, campaign message

## Ethics constraints (hard-coded)

No deepfake generation · No impersonation · No fabricated facts · No psychological profiling of individuals · No targeting based on sensitive traits · No voter suppression · No harassment · No automated spam · Evidence weakness is always disclosed · Sources cited

## Quick start

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The database is created automatically at `backend/war_room.db` and seeded with the Lakeview City demo scenario on first run.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

> The Vite dev server proxies `/api` requests to `http://localhost:8000` automatically.

### Both at once (two terminals)

**Terminal 1:**
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
```

**Terminal 2:**
```bash
cd frontend && npm run dev
```

## API reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Service health check |
| GET | `/api/dashboard` | Full dashboard aggregation |
| GET | `/api/sources` | All source items (filter by `source_type`, `urgency`) |
| POST | `/api/sources/text` | Ingest pasted text or transcript |
| POST | `/api/sources/rss` | Ingest RSS feed (up to 10 items) |
| POST | `/api/sources/url` | Ingest a URL |
| GET | `/api/issues` | All tracked issues |
| GET | `/api/issues/{id}` | Issue detail with linked sources |
| GET | `/api/opponents` | Tracked opponents |
| POST | `/api/opponents` | Add an opponent |
| GET | `/api/opponents/{id}/activity` | Opponent claims/attacks/promises |
| POST | `/api/canvassing/upload` | Upload canvassing CSV |
| GET | `/api/canvassing/insights` | Aggregated precinct insights |
| GET | `/api/campaign` | Get campaign profile |
| PUT | `/api/campaign` | Update campaign profile |
| GET | `/api/setup/status` | Setup checklist status |
| GET | `/api/rss-feeds` | List saved RSS feeds |
| POST | `/api/rss-feeds` | Add a persistent RSS feed |
| PUT | `/api/rss-feeds/{id}` | Update feed (rename, pause/resume) |
| DELETE | `/api/rss-feeds/{id}` | Delete feed (sources kept) |
| POST | `/api/rss-feeds/{id}/ingest` | Ingest one feed now |
| POST | `/api/rss-feeds/ingest-all` | Ingest all active feeds |
| GET | `/api/review-queue` | Unreviewed source items (priority sorted) |
| POST | `/api/review-queue/{id}/review` | Mark a source reviewed |
| POST | `/api/review-queue/{id}/dismiss` | Dismiss a source |
| POST | `/api/review-queue/{id}/priority` | Set priority score |
| POST | `/api/talking-points` | Generate and save talking points |
| GET | `/api/talking-points/history` | Recent generated talking points |
| GET | `/api/talking-points/history/{id}` | Single talking point |

Interactive API docs: http://localhost:8000/docs

## Canvassing CSV format

```csv
voter_name,address,precinct,issue,sentiment,notes,date
"",""  ,"7A","housing","negative","Rent up $300 this year","2026-04-15"
```

`voter_name` and `address` are optional (leave blank for privacy). `sentiment`: positive | negative | neutral | mixed

## LLM configuration

All AI calls go through `backend/app/services/llm_provider.py`. The system defaults to `MockLLMProvider` (deterministic, no API key needed). To use a real LLM:

### Environment variables

Copy `.env.example` to `.env` in the `backend/` directory:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `mock` | `mock`, `openai`, or `anthropic` |
| `OPENAI_API_KEY` | — | Required when `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | `gpt-4o` | OpenAI model ID |
| `ANTHROPIC_API_KEY` | — | Required when `LLM_PROVIDER=anthropic` |
| `ANTHROPIC_MODEL` | `claude-opus-4-7` | Anthropic model ID |

**Fallback behavior:** If a provider is selected but the API key is missing or the API call fails, the system automatically falls back to `MockLLMProvider` with a warning log. The app never crashes due to LLM failures.

### What each provider does

- **`mock`** — Keyword-matched static responses. Fast, free, always works. Good for development.
- **`openai`** — GPT-4o with JSON mode. Talking points are grounded in actual ingested sources and your campaign profile.
- **`anthropic`** — Claude Opus with structured prompts. Same source-grounding and ethics constraints.

### Campaign profile context

When generating talking points, the system automatically injects:
- Your campaign profile (candidate name, office, message, key priorities)
- Up to 6 ingested source items linked to the issue (with credibility notes)
- Recent opponent attacks relevant to those sources

This context is included in every real LLM prompt. The mock provider uses it to personalize candidate name references.

### Ethics constraints

The following are hard-coded into every real LLM prompt and cannot be overridden by tone or issue selection:

> No deepfakes · No impersonation · No fabricated statistics · No psychological profiling · No voter suppression · No harassment · Evidence weakness always disclosed · All claims must be traceable to ingested sources

## Recommended workflow for a real campaign

1. **Campaign Setup** (`/campaign`) — Enter candidate name, office, district, campaign message, key priorities, election date.
2. **Add opponents** (`/opponents`) — Add each opponent by name. The system then automatically extracts their attacks, claims, and promises from ingested sources.
3. **Configure RSS feeds** (`/feeds`) — Add one or more RSS feed URLs (local news, city council agenda feeds, opponent's campaign site if available). Click "Ingest All Active" to pull items.
4. **Review Queue** (`/review`) — New items land here sorted by priority score. Mark items reviewed, dismiss noise, or click "Generate TP" to draft a talking point directly from the source.
5. **Issue Tracker** (`/issues`) — Review auto-detected issues. Click any issue to see linked source items.
6. **Talking Points** (`/talking`) — Generate evidence-grounded responses for any issue in the tone you need (door knock, interview, debate, social). History is saved automatically.
7. **Check the Dashboard** (`/`) daily — the suggested actions panel reflects the current state of attacks, risk sources, and field data.

## Testing on a real race

The app ships with fictional demo data (Lakeview City District 7). To replace it with a real campaign:

### Step 1 — Reset workspace

Go to **Campaign Setup → Workspace Reset** (at the bottom). This deletes all demo sources, issues, opponents, and talking point history. You'll create a fresh campaign profile in the same step. Check "Preserve configured RSS feeds" if you've already added real feeds you want to keep.

Alternatively, run from the CLI:
```bash
cd backend && source .venv/bin/activate
python -m app.seed --reset
```

### Step 2 — Fill campaign profile

In **Campaign Setup**, enter: candidate name, office (e.g. "U.S. Representative"), district (e.g. "PA-08"), party, location, election date, core campaign message, and key priorities. This context is injected into every AI talking point prompt.

### Step 3 — Add opponents

In **Opponent Tracker**, add each opponent by name. Once sources mentioning them are ingested, the system will automatically extract their attacks, claims, and promises.

### Step 4 — Apply a starter pack

In **RSS Feeds → Starter Packs**, click "Apply" on the **US House Race Starter Pack** (or relevant pack). This creates:
- RSS feed entries for any items with real URLs
- Source reminders for placeholder items (FEC page, opponent site, etc.)

Placeholder items are labeled `[PLACEHOLDER]` in their setup notes — replace the URL or paste content manually before those become useful.

### Step 5 — Configure real source URLs

In **RSS Feeds → Source Reminders**, work through each reminder. Replace placeholder URLs with real ones for your district:
- Your candidate's FEC committee page
- Opponent's campaign website news section
- Local newspaper RSS feed
- Google News RSS for your name and opponent's name
- State SOS / county election board bookmarks

For items that have RSS feeds, edit the reminder URL and add it as an RSS feed in the "Add Feed" form above.

### Step 6 — Import via CSV (optional)

If you have your race setup in a spreadsheet, export it as CSV and import it via **Campaign Setup → Import Race Setup from CSV**. Supported row types: `campaign`, `opponent`, `rss_feed`, `reminder`. See the format example shown in the UI.

Example CSV:
```csv
type,name,url,category,source_type,notes,party,office,district,location,election_date
campaign,Jane Smith,,,,,Democrat,U.S. Representative,PA-08,Scranton PA,2026-11-03
opponent,John Doe,,,opponent_statement,,Republican,U.S. Representative,PA-08,,
rss_feed,Times-Tribune RSS,https://thetimes-tribune.com/feed,,news,Scranton paper,,,,,
reminder,Check FEC Page,https://www.fec.gov/data/committees/,,public_record,Check quarterly filings,,,,,
```

### Step 7 — Ingest sources

In **RSS Feeds**, click "Ingest All Active" to pull from all configured RSS feeds. For non-RSS sources:
- Use **Sources → Paste Text** to add press releases, debate transcripts, endorsement letters
- Use **Sources → Fetch URL** to ingest a specific web page

### Step 8 — Work the Review Queue

In **Review Queue**, triage new intelligence sorted by priority score. For each item:
- Read the summary and credibility note
- Mark reviewed if relevant
- Click "Generate TP" to jump directly to a pre-filled Talking Points form with the source as context
- Dismiss noise

### Step 9 — Generate talking points only from reviewed evidence

In **Talking Points**, select a tracked issue and tone. All generated content is grounded in your ingested sources. The evidence and source citation sections show exactly which sources backed each claim. Do not publish talking points that cite weak or unverified evidence.

### Step 10 — Daily dashboard check

The Dashboard shows today's top issues, opponent attacks, suggested actions, and what changed in the last 24 hours. Check it each morning before press availability, debates, or door-knock sessions.

---

**Important:** This app makes no factual claims about real candidates. All talking points are generated from sources *you* ingest. If you ingest bad sources, you get bad talking points. Source quality determines output quality.

## How RSS feeds work

The `POST /api/rss-feeds` endpoint saves a feed configuration. Use `POST /api/rss-feeds/{id}/ingest` or `POST /api/rss-feeds/ingest-all` to pull new items on demand. All items are deduplicated by `source_url` — re-ingesting the same feed never creates duplicates.

Deleting a feed removes the feed record but does **not** delete any source items already ingested from it.

## How the Review Queue works

Every newly ingested source item starts with `reviewed=false`. The priority score is computed automatically:
- High urgency: +30 points
- Medium urgency: +10 points
- Opponent activity detected: +20 points
- Issue linked: +10 points
- Has credibility warning: +15 points
- Published in the last 3 days: +10 points, last 7 days: +5 points

Items with higher scores appear first. You can also manually boost priority from the Review Queue page.

## How seeding works

The app seeds the Lakeview City demo scenario on first run (no-op if data already exists).

To re-seed from scratch:
```bash
cd backend && source .venv/bin/activate
python -m app.seed --reset
```

`--reset` drops all tables and re-creates them before seeding. Use this to start fresh during development.

## Limitations before production use

The following require human review by qualified campaign and legal staff before real-world use:

- **All generated talking points** should be reviewed by the candidate and/or campaign manager for accuracy, tone, and legal compliance before use
- **Credibility notes and risk warnings** are heuristic, not legal analysis
- **Issue clustering** is keyword-based and may miss nuance or misclassify items
- **Opponent activity detection** may produce false positives; verify before responding
- **RSS and URL ingestion** fetches public content — do not ingest paywalled, embargoed, or private material
- **No encryption at rest** — do not store sensitive voter data (beyond the minimal canvassing notes) in this app
- **No authentication** — this app is designed for single-campaign internal use on a secured device or network

## Demo scenario

The seed data models a fictional 2026 city council race:

- **Candidate:** Maria Chen (Democrat, challenger)
- **Opponent:** Roy Harmon (Republican, 2-term incumbent)
- **Race:** Lakeview City Council, District 7
- **Issues:** Housing & Affordability (HIGH/rising), Public Safety (MEDIUM), Education (MEDIUM/rising), Infrastructure (LOW), Downtown Development (LOW)
- **Precincts:** 7A (housing), 7B (schools), 7C (crime), 7D (infrastructure)

## Project structure

```
campaign-war-room/
  backend/
    app/
      main.py           FastAPI app + lifespan
      db.py             SQLite engine + session
      models.py         SQLAlchemy ORM models
      schemas.py        Pydantic v2 request/response schemas
      seed.py           Demo data
      routes/           One file per feature area
      services/
        llm_provider.py Abstract + Mock/OpenAI/Anthropic providers
        intelligence.py Thin wrappers around LLM calls
        ingestion.py    RSS / URL / text / CSV ingestion
        issue_clustering.py Keyword-based issue assignment
        opponent_analysis.py Claim/attack/promise extraction
        risk_checks.py  Credibility and risk flagging
    war_room.db         SQLite database (auto-created)
  frontend/
    src/
      api/              Typed fetch client + TypeScript interfaces
      pages/            One file per page
      components/       Shared UI components
```
