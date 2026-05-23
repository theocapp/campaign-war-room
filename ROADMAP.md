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

- [x] Backend and frontend built and running
- [x] RSS ingestion working
- [x] Groq configured as LLM provider
- [x] KG narrative projection removed (previous session)
- [ ] **Phase 1: Real data flowing** ← START HERE
- [ ] Phase 2: Single LLM call per article
- [ ] Phase 3: Campaign-defined narrative tracking
- [ ] Phase 4: Morning briefing view
- [ ] Phase 5: Test with real campaign

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
