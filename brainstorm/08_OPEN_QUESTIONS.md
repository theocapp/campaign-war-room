# Open Questions — what I want your judgment on

Things I'm genuinely uncertain about, recommendations that depend on context I don't have, and decisions I think are yours to make. Organized roughly in order of how much they'd block other work.

---

## STRATEGIC — affect everything downstream

### Q1. Do you want this to be a product, a tool for friends, or just your own thing?

This determines almost everything: features, copy, security, deployment.

- **Your own thing only** → skip all productization work (multi-tenancy, auth, public press-kit URLs). Focus 100% on Cognetti's race winning.
- **Tool for friends** → Phase 2 in the ROADMAP. Postgres migration becomes urgent. Lockdown of admin endpoints becomes mandatory.
- **Real product** → all of the above + multi-tenant + auth + onboarding wizard + pricing decisions.

**My guess:** You want the optionality but don't want to pay the multi-tenancy tax yet. The ROADMAP Phase 1/2/3 plan handles this exactly right. But if you said "I just want Cognetti to win" I'd recommend dropping ~50% of what I wrote in `06_MARKET_STRATEGY.md`.

### Q2. Single-side or bipartisan?

You're building this for Cognetti (Democrat). The exact same code could serve a Republican campaign. Decisions like:
- Distribution: D-side networks vs R-side networks (mostly disjoint)
- Branding: explicitly partisan? Or technology-neutral?
- LLM prompt tone: "the campaign" vs "your candidate"

This matters most when (and if) you go to Phase 2 (friends' races). Pick a side, or commit to neutrality. Half-measures look weird.

### Q3. What does "winning" look like for this project?

The roadmap stops at "make Phase 3 a SaaS". But:
- Are you trying to support a specific friend's race in 2028?
- Are you trying to build a product that you exit?
- Are you trying to influence specific election outcomes?
- Are you trying to learn engineering?

These have different priority orders. I've written this brainstorm assuming "broad value across all four" but if it's actually narrower, I'd cut a lot.

---

## PRODUCT — affect specific features

### Q4. Talking-point generator + counter-narrative suggester — comfortable building?

These features (Feature #4 + #10) write AI-generated language the campaign could potentially use publicly. Two failure modes:
- **Quote hallucination** — I think the prompt engineering is strong enough to prevent this, but worth a human-in-the-loop requirement
- **Tone/factual mismatch** — LLM might generate something that sounds right but doesn't match Cognetti's actual positions

Both can be mitigated (require human review before any external use, always show sources). But: building these = explicit AI-generated political messaging. Some campaigns/people are squeamish.

**Your call:** Build or skip? If skip, the value of the project drops noticeably (intelligence without ammunition). If build, document the safeguards prominently.

### Q5. "Devil's Advocate" / "Opposition Eyes" features — too cute or genuinely useful?

These are the "Wild Ideas" in the features doc. They're novel and would be a marketing differentiator. But:
- Might be uncomfortable for a candidate to use ("simulating what they're thinking about me")
- Could be misinterpreted if the public hears about it

Your call. They'd be cool. Not safe.

### Q6. Public press-kit URLs (Feature #11) — confident this is OK?

Sharing a public URL of "here's what we tracked about Bresnahan's healthcare record" — is this:
- Smart PR (give journalists a one-stop shop)?
- Risky (giving the opposition a permanent record of your intelligence collection)?
- Legally sensitive (defamation if any AI-generated content is wrong)?

I think it's fine if the URL only shows source articles + your verbatim summary + verified quotes — no AI-generated commentary. But check with the campaign attorney before launching anything public-facing.

### Q7. Auto-suggested counter-narratives — surface them where?

If Feature #10 ships, where in the UI does it appear? Options:
- Inline on each emerging-stage opposing frame card ("Pre-drafted responses ▼")
- A dedicated "Response Queue" page
- Email only (push to comms director, not stored in tool)
- Slack push

Each has different ergonomics + different trust patterns.

---

## TECHNICAL — affect implementation

### Q8. Drop the dead tables or keep them?

`kg_*` (12), `narratives`, `narrative_mentions`, `generated_talking_points`, plus the 5 empty tables (`candidate_narratives`, `candidate_message_libraries`, `canvassing_notes`, `manual_captures`, `manual_source_reminders`) = ~20 tables you could drop.

But: `generated_talking_points` is the schema for a feature you might revive (Feature #4). Same for `kg_entity_aliases` (Feature: alias problem).

**Recommendation:** Drop the 12 `kg_*` (lessons captured in ROADMAP). Keep `generated_talking_points` if you're going to build #4. Drop the 5 empties.

### Q9. Migrate to Postgres before or after the Cognetti race?

ROADMAP suggests doing it in a quiet period. Cognetti's race is presumably ongoing through Nov 2026 (or whenever).

- **Before:** big risk if migration breaks something mid-race
- **During:** zero opportunity, don't try
- **After (post-race, win or lose):** clean, safe, prepares for Phase 2

I'd wait until post-race. SQLite is fine for one race + ~50K rows.

### Q10. Embedding model choice

Currently using Gemini `gemini-embedding-001` (3072 dims) primary + OpenAI `text-embedding-3-large` (3072 dims) fallback.

Alternative: drop to OpenAI `text-embedding-3-small` (1536 dims) everywhere. 5× cheaper. But: re-embed all cached vectors.

**My call:** Stay with what you have. Cost isn't meaningful yet.

### Q11. Real-time vs scheduled refresh

Currently most jobs run on schedule (RSS every 30 min, candidate-promoter daily, etc.). Some campaign moments deserve real-time (a breaking attack ad drops at 2 PM).

Options:
- Webhooks from publishers (most outlets don't offer this)
- Aggressive polling for tier-1 outlets only (every 5 min)
- Push notifications when something does land

Real-time is hard. Scheduled-but-shorter (5-min poll for tier-1) is easier.

### Q12. LLM provider lock-in

`get_provider()` is Groq-first with fallbacks. `get_judge_provider()` is OpenAI-first with Groq fallback.

If Anthropic releases a substantially better Claude that you want to use, swapping is non-trivial because the prompts are tuned to specific model behaviors.

**Recommendation:** Build an integration test that runs the same prompt through all three providers and diffs the output. Helps you switch confidently.

---

## OPERATIONAL

### Q13. Hosting cost when (if) you go to Phase 2

Per-tenant hosting at $10-20/mo + LLM cost passthrough at ~$10-50/mo per race = $20-70/mo per race in operating cost. At $200/mo standard pricing, margin is reasonable.

But: at Phase 2 (no payment yet) you're eating $20-70/mo per friend × 5 friends = $100-350/mo out of pocket. Sustainable?

### Q14. Long-term data retention

Your DB will grow. 13K source_items today, probably 50K by Nov 2026, 200K+ if Phase 2 happens.

- Postgres can handle this easily
- SQLite gets slow at 100K+ rows for FTS
- Decision: what do you retain forever vs archive? My take: keep articles 18 months max (election cycle), archive older.

Storage cost is trivial. Query performance + backup time is what eventually matters.

### Q15. Compliance posture

For US federal races, key things you might care about:
- **FEC reporting** — none of what you're doing requires FEC disclosure (you're not making expenditures)
- **Data privacy** — public data is fine; if you ever ingest voter file or donor data, GDPR-style protections matter (even though US doesn't require)
- **Section 230 / CDA** — not applicable; you're not a platform hosting user-generated content
- **State election laws** — vary by state; some require disclosures for "electioneering communications"

If you stay in narrative-monitoring (no public outputs, no ad-buying), legal exposure is minimal. The moment you generate public content (press-kit URLs, AI talking points distributed externally), get a lawyer.

---

## META — about this brainstorm

### Q16. Did I miss something important?

Things I didn't cover that might matter:
- Mobile app or PWA (does the campaign use mobile primarily?)
- Voice/SMS integration (some directors want SMS alerts)
- Volunteer-facing views (do interns/volunteers get access?)
- Internal annotations / collaboration (multiple campaign staff editing notes)
- Integration with existing campaign comms tools (Mailchimp, etc.)
- Backup / disaster recovery (what if the server dies during October?)

If any of these are critical, let me know and I'll think harder about them.

### Q17. Did I over-engineer any recommendation?

Specific places I'm worried I over-thought:
- The Postgres migration considerations (might be premature)
- The market-strategy doc (might not be your goal at all)
- The competitive landscape (probably stale — landscape moves fast)
- Feature #10 (counter-narrative auto-suggest) — might be too AI-heavy / risky

If you only want me to refine 3-5 things, tell me which ones to focus on.

### Q18. What's missing that would have made this brainstorm better?

I worked from your DB + code only. Things that would have sharpened my recommendations:
- Your actual workflow (what you do each morning)
- Cognetti's campaign's pain points specifically (have you heard "I wish this could X")
- Whether you have a comms director using this — their reactions
- What broke during real use that I haven't seen in logs
- What you've actually tried that I might re-suggest

If you do another overnight, share these and I'll be sharper.
