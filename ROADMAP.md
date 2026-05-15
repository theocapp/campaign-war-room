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
