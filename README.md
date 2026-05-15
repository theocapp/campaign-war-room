# Campaign War Room

A low-cost local media monitoring tool for small political campaigns that can't
afford Meltwater. Aggregates local news + Google News RSS, filters for
campaign relevance using a single Groq LLM call per article, and organizes
results by **narrative frames** that the campaign defines.

Designed for one campaign on one device. Not a SaaS product, not a voter
contact tool, not a talking-points generator.

> **Current test campaign:** Cognetti for PA-08 (Scranton).
> **Phase plan:** see [AUDIT.md](./AUDIT.md) and [PHASE_PROMPTS.md](./PHASE_PROMPTS.md).

---

## What the app does

Six pages, all driven by the same per-article LLM call:

- **Briefing** — morning recap: relevant articles in the last 24h, things that
  need a response, race-situation memo, narrative pulse.
- **Narratives** — manage the narrative frames you care about (yours and your
  opponent's), with auto-suggested frames from recent coverage.
- **AI Audit (Review Queue)** — every article scored by the LLM, with one-sentence
  summary, framing, and the LLM's reason. Approve / reject / boost priority.
- **Opponent Tracker** — claims, attacks, and promises extracted from articles
  where the opponent themselves is the subject.
- **RSS Feeds** — list, add, edit, and trigger ingestion of RSS feeds.
- **Campaign Setup** — pick the race from the FEC directory, edit the candidate
  profile, configure filter keywords, reset the workspace.

---

## How the core loop works

Each ingested article makes **exactly one LLM call** (Groq, `llama-3.3-70b-versatile`).
The model returns:

```json
{
  "relevant": true,
  "relevance_score": 75,
  "one_sentence": "Bresnahan attacked Cognetti's healthcare record at a Scranton event.",
  "framing": "opponent_news",
  "needs_attention": false,
  "reason": "Direct opponent activity in the district.",
  "opponent_attacks": [
    { "opponent_name": "Rob Bresnahan", "type": "attack", "text": "..." }
  ]
}
```

The ingestion layer then matches the article against active narrative frames
(one additional LLM call only if `relevant=true`) and persists everything to
SQLite.

There is no knowledge graph, no embeddings, no auto-narrative discovery. Those
were tried and removed — see [PRODUCT_BRIEF.md](./PRODUCT_BRIEF.md) for the
reasoning.

---

## Quick start

```bash
# Backend
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```bash
# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api/*` to `localhost:8000`.

The SQLite DB at `backend/war_room.db` is created on first boot.

---

## LLM configuration

The app uses Groq in production. Put this in `backend/.env`:

```bash
LLM_PROVIDER=openai          # use the OpenAI-compatible Groq endpoint
OPENAI_API_KEY=<your_groq_key>
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile
```

`anthropic` and `mock` providers exist for fallback only — not advertised as
features. With no key configured the app falls back to `MockLLMProvider` and
the UI shows a banner explaining that AI scoring is off.

### Other env vars

| Variable | Default | Description |
|---|---|---|
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173,http://localhost:4173` | Comma-separated allow-list |
| `RSS_AUTO_INGEST_ENABLED` | `true` | Disable to stop the background scheduler |
| `RSS_AUTO_INGEST_INTERVAL_MINUTES` | `60` | Minimum 1 |

---

## Workflow for a real campaign

1. **Campaign Setup → Race directory** — search the FEC directory for your race
   and click Select. The candidate profile and opponents auto-populate from the
   FEC Candidate Master file.
2. **RSS Feeds** — add Google News RSS for your candidate's name + opponent's
   name, plus local outlets. Click "Ingest All Active" or wait for the
   scheduler.
3. **Narratives** — define 3–8 narrative frames you actually want to track
   (your candidate's message, the opponent's attacks, recurring categories of
   local news). Click "Suggest frames" to seed the list from recent articles —
   the LLM proposes 3–5 categories.
4. **AI Audit (Review Queue)** — triage. Skim the LLM's summary + reason for
   each article; mark relevant / irrelevant.
5. **Briefing** — open each morning before press availability or debate prep.

---

## Tech stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2, SQLite, APScheduler.
- **Frontend:** React 18, TypeScript, Vite. Inline styles (no design system yet).
- **LLM:** Groq via the OpenAI-compatible API.

## Project layout

```
campaign-war-room/
  AUDIT.md, PHASE_PROMPTS.md, PRODUCT_BRIEF.md, ROADMAP.md
  backend/
    app/
      main.py             FastAPI app + lifespan
      db.py               SQLite engine + migrations
      models.py           SQLAlchemy models
      schemas.py          Pydantic request/response shapes
      seed.py             Lakeview demo seed (idempotent)
      routes/             One file per feature area
      services/
        campaign_analysis.py  Single per-article LLM call
        narrative_frames.py   Frame suggestion + article matching
        ingestion.py          RSS / URL / text ingest
        opponent_analysis.py  Regex-based opponent extractor (re-analysis path)
        text_utils.py         Shared HTML-strip + entity-decode helper
        llm_provider.py       OpenAI-compatible / Anthropic / Mock providers
        ...
    war_room.db           SQLite (auto-created)
  frontend/
    src/
      api/                Typed fetch client + types
      pages/              One file per page
      components/         Layout, Toast
```

## Testing

```bash
# Backend
cd backend && .venv/bin/python -m pytest

# Frontend
cd frontend && npm test
```

Many backend tests are integration tests that hit the live Groq endpoint;
they fail under daily-quota rate-limiting. Mocking the LLM provider in tests
is a Phase 1 task — see [AUDIT.md](./AUDIT.md).

## Limitations

This app is single-campaign and single-device by design. There is no auth, no
multi-tenant scoping, no encryption at rest. Don't paste sensitive data.
Generated AI scoring is heuristic — the LLM gets things wrong, especially on
local stories where it lacks context. Always read the source before acting.

See [PRODUCT_BRIEF.md](./PRODUCT_BRIEF.md) for the locked architectural
decisions and [AUDIT.md](./AUDIT.md) for the current bug list, debt, and
phase roadmap.
