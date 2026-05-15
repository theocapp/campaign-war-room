# Campaign War Room — Product Brief
> Load this into every Claude Code session with: claude --add PRODUCT_BRIEF.md
> Update the "Current Phase" section at the end of every session.
> This file overrides any architectural suggestions that contradict it.

---

## What This Is

A narrative tracking tool for small political campaigns that can't afford professional
media monitoring (Meltwater, Cision cost $2,000+/month). It aggregates local news,
filters for campaign relevance using an LLM, and shows how specific narratives —
both the campaign's own message and the opponent's attacks — are developing over time.

The core value: a campaign staffer opens this tool in the morning and in 2 minutes
knows what was said about their candidate and opponent overnight, organized by
the narrative frames that actually matter to their race.

---

## Who It's For

Campaign managers and staffers at local-to-congressional races (city council, state
legislature, congressional) who have:
- No dedicated communications monitoring team
- No budget for professional tools
- Limited time — they need information fast, not perfectly

---

## What It Is NOT

- Not an AI that auto-discovers narratives (humans define what to track)
- Not a replacement for political judgment
- Not trying to compete with Meltwater/Cision on features
- Not a voter contact or canvassing tool
- Not a SaaS product yet — single campaign, single device

---

## The Product In One Sentence

"Show me what's being said about my candidate and opponent this week,
organized by the narrative frames I'm already tracking."

---

## The Core Loop

1. Ingest articles (RSS feeds, manual paste, URL)
2. One LLM call per article: is this relevant to THIS race? One-sentence summary. Political framing.
3. Campaign defines narratives they care about (their message + opponent's attacks)
4. System matches new articles to defined narratives
5. Dashboard shows: narrative mention counts over time, new evidence, anything urgent

---

## Current Campaign (Test Case)

- Candidate: Cognetti
- Race: PA-08, Congressional, Pennsylvania 8th District
- Location: Scranton / Lackawanna County / Northeastern Pennsylvania
- LLM Provider: Groq API (Llama models)
- Target sources: Local Scranton/NEPA news + Google News RSS for candidate/opponent names

---

## Tech Stack

- Backend: Python 3, FastAPI, SQLite, APScheduler
- Frontend: React 18, TypeScript, Tailwind CSS, Vite
- LLM: Groq API (llama-3.1-70b-versatile or similar)
- DB: SQLite at backend/war_room.db
- Run backend: cd backend && uvicorn app.main:app --reload
- Run frontend: cd frontend && npm run dev

---

## Architectural Decisions — DO NOT REVISIT without strong reason

These have been discussed and decided. Do not suggest alternatives.

| Decision | Reason |
|---|---|
| No Knowledge Graph system | Over-engineered; LLMs handle semantic grouping directly and better |
| No embeddings or cosine clustering | One LLM call per article is simpler, cheaper, more accurate |
| No auto-discovery of narratives | Produces noise (national stories dominate local races); humans define narratives |
| One LLM call per article, not a multi-step pipeline | Simpler, cheaper, fewer failure points |
| SQLite is fine for now | Single campaign, single device; no need for Postgres yet |
| Groq for all LLM calls | Fast, cheap, no OpenAI/Anthropic dependency |
| Campaigns define their own narrative frames | More accurate than auto-clustering; forces strategic thinking |

---

## Things Tried and Rejected

- **Knowledge Graph + embeddings + cosine clustering**: Built, then removed. National stories
  always dominated over locally relevant ones. Complex, expensive, inaccurate for local races.
- **Multi-step LLM pipeline per article** (summarize → extract issues → detect opponent activity
  → run KG extraction → project to legacy table): Too many steps, cumulative errors, expensive.
- **Auto-narrative discovery**: Mar-a-Lago FBI raid showed up as a top narrative for a PA
  congressional race. The algorithm has no concept of "relevant to THIS race."

---

## Current Phase

**Phase 1: Get real data flowing**
- Add Google News RSS feeds for candidate name + opponent name
- Add local NEPA news sources
- Verify 20+ relevant articles are flowing per day
- Status: NOT STARTED

See ROADMAP.md for the full phase breakdown.
