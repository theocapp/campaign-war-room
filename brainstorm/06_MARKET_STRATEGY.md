# Market Strategy — productization, positioning, moat

You already have a clear deployment trajectory in ROADMAP (own race → friends → SaaS). This doc covers what's NOT in the ROADMAP: positioning, competitive landscape, pricing intuitions, GTM, and ethical considerations.

Confidence tags:
- ✅ Sure
- ⚠ Recall — verify before betting on
- ❓ Speculation

---

## The product, named

You don't have a name yet (project dir is "campaign-war-room"). Some options to brainstorm:

| Name | Vibe | Notes |
|---|---|---|
| **War Room** | direct, military | already what you call it, but generic; trademark check needed |
| **Narrative** | the differentiator | strong but maybe too abstract |
| **Pulse** | the daily-cadence value | crowded namespace (Pulse for X, Pulse for Y) |
| **Signal** | what the tool finds | overloaded in tech world |
| **Frame** | the technical concept | inside-baseball; users don't know "frame" |
| **Throughline** | what narratives become | available, distinctive, captures the abstraction |
| **Frontrun** | win the news cycle | aggressive, memorable |
| **Mainline** | mainstream + line | catchy |
| **Through** | minimal, abstract | available as a domain |
| **Wire** | press wire ethos | crowded but works |

If I had to pick: **Throughline**. It's exactly the abstraction — narratives as continuous storylines through coverage. Easy to explain ("we find the throughlines in your race's coverage"). Probably available as a .com.

---

## What this product actually IS (positioning)

Most political tech serves one of these jobs:
- **Voter file / targeting** — NGP VAN, L2, i360, Aristotle
- **Fundraising / donor management** — ActBlue, WinRed, NGP VAN
- **Campaign org / volunteers** — NationBuilder, MobilizeAmerica
- **Polling** — Civis, Cygnal, Public Policy Polling
- **Media monitoring (enterprise)** — Cision, Meltwater, Critical Mention, Talkwalker (~$5-25K/year)

What you have is a **narrative intelligence tool for campaigns who can't afford enterprise media monitoring.** The pitch:

> "Watch the race the way a $50K/year media monitoring platform would — for $X/month, run by an AI that knows your candidate's story."

**Adjacent in scope:** Cision/Meltwater (way more expensive, broader B2B brands).
**Adjacent in price:** Free + Google Alerts (way less capable, no narrative concept).

Your gap is where most state-level + congressional + statewide non-marquee races live. They have $50K-2M total budgets. They can spare $200-500/mo for tooling but not $5K.

---

## Competitive landscape (with confidence)

### Direct competitors (narrative + media monitoring focused)

| Product | Price | Strength | Weakness |
|---|---|---|---|
| **Cision PR Newswire** ✅ | $5-15K/yr | Established, broad outlet coverage, PR-focused workflows | Generic, not political; can't track narrative arcs |
| **Meltwater** ✅ | $5-25K/yr | Strong global coverage, social listening included | Same as Cision |
| **Critical Mention** ✅ | $5-15K/yr | TV/broadcast monitoring excellence | Print/digital weaker, no AI synthesis |
| **Talkwalker** ✅ | $5-20K/yr | Best-in-class social listening | Generic B2B framing, not narrative-frame oriented |
| **Brandwatch / Sprinklr** ✅ | $10K+/yr | Enterprise social | Same |
| **DDHQ (Decision Desk HQ)** ✅ | Various | Election data + ratings | Election-night focused, not daily narrative monitoring |
| **NetBase Quid** ❓ | enterprise | Narrative + sentiment analytics | Heavy lift to set up, not political-specific |

### Adjacent (different job-to-be-done)

| Product | What it does |
|---|---|
| **NGP VAN** ✅ | Voter file, donor management, fundraising |
| **NationBuilder** ✅ | Volunteer/list management |
| **MobilizeAmerica** ✅ | Volunteer event signup |
| **Aristotle** ⚠ | Campaign software suite (voter file + light media) |
| **L2/i360** ✅ | Voter targeting (R-side i360 + bipartisan L2) |

**The gap:** Affordable real-time narrative intelligence for sub-Senate-level races. No serious incumbent at <$500/mo.

### What you'd compete with at the free tier
- Google Alerts (keyword email digest, no synthesis)
- Manual press monitoring (intern with a spreadsheet)
- Twitter/X following a list (no longer reliable post-API-restrictions)

---

## Moat analysis — what's hard to copy

### Real moats (defensible over 1-2 years)

1. **The narrative-frame schema + prompts.** The v2 scoring prompt with anti-hallucination, owner_type semantics, and per-claim extraction is genuinely good prompt engineering. Anyone could TRY to copy this, but it took real iteration. **Defensibility: 6-12 months for a competitor to catch up.**

2. **The data-source integration breadth.** RSS + GDELT + GDELT BigQuery + Reddit (Tavily) + Bluesky firehose + Mastodon + Google News + manual capture. That's 8 ingestion paths. Building each takes 4-20 hours. A competitor needs ~3 months to match.

3. **Race-specific tuning.** Each race has its own keywords, outlets, frames, opponents, issues. Once a campaign sets this up, switching cost is real (re-doing 4 hours of setup). **Lock-in is moderate.**

### Weak moats (easy to copy)

- The UI (anyone can copy a dashboard in 2 weeks with React + Tailwind)
- The OpenAI/Gemini scoring (depends on LLM provider, not on you)
- The basic clustering (HDBSCAN + simhash is published technique)

### Things competitors WILL try to do
- Bigger enterprise vendors will eventually have a "campaign" tier — wait for it
- Free copycats from AI hobbyists — won't have the data pipeline depth
- Polling/voter-file vendors will bolt on narrative tracking as an upsell

### Defensibility play
If you take this seriously, the real moat is **proprietary historical data + benchmarks**. After tracking 50 races, you can say "this attack narrative typically dies in 11 days" or "polls move 0.7% per outlet-tier-2 negative story." That's not in any public dataset. Each race you track adds to it.

---

## Pricing intuition

Three tiers I'd test:

### DIY / Open Source — $0
- Self-hosted, single-tenant
- Setup cost is the user's time
- LLM API keys are theirs
- **Who uses this:** technical campaign staffers, party orgs that pay devs
- **Strategic value:** distribution + brand-building. Open source the core. Reserve advanced features for paid.

### Standard — $199/month per race
- Hosted, you handle deployment
- 1 campaign, unlimited users
- LLM costs included up to ~$50/mo retail
- Sometimes called "Indie tier" — small races, primary candidates, single-issue advocacy
- **Margin:** $80-130/mo (after compute + LLM costs)

### Pro — $599/month per race
- Standard plus:
- Priority data freshness (5-min refresh vs 30-min)
- Custom outlet additions
- Slack/Discord integrations
- Talking-point generation (LLM-cost-heavier feature)
- Public press-kit URLs
- **Margin:** $300-400/mo

### Enterprise — Custom
- State party orgs, leadership PACs running 10+ races
- White-label, custom branding
- Cross-race rollups
- Probably $2K-10K/mo
- **Margin:** depends heavily on volume

Note: Most campaigns are sensitive to cost-per-month numbers in March 2024 budget season then forget by July when they need it. Annual prepay discount works.

---

## Go-to-market

### Year 1 (your reality)
You're running it for one race. The right play is to:
1. **Make it indispensable for Cognetti's campaign first.** All other GTM is downstream of this. The product becomes the testimonial.
2. **Document everything** as you go — what worked, what didn't, what the campaign did differently because of the tool. This is your case study.
3. **Talk to 5-10 other campaigns/comms directors** in parallel. Understand their workflow. Confirm the problem is real.

### Year 2 — friends' campaigns
The ROADMAP Phase 2 plan is exactly right. Spin up self-hosted instances for 3-10 friend campaigns. Per-instance, not multi-tenant. Postgres migration before this.

**GTM tactic:** "Hey, want me to set up the tool I built for Paige's race? It takes 2 hours of your time and I'll cover hosting until it's worth charging."

This is when:
- You learn what to charge (when friends start saying "this is so valuable I'd pay you for it")
- You harden the deployment pipeline
- You build a real testimonial library

### Year 3 — productize

If Years 1-2 went well, you have:
- 5-10 friend campaigns paying $0-200/mo each
- A wait-list
- A case study from Cognetti's race (W or L doesn't matter as much as the story)
- A clear sense of what to build for the paid tier

Distribution channels for paid product:
1. **Direct outreach to campaign managers** — LinkedIn/Twitter DMs, intro through party orgs
2. **State party preferred-vendor programs** — DLCC, RSLC, DSCC, DCCC, NRCC all have these. Months of relationship-building.
3. **Conference presence** — Politicon, Campaign & Elections Tech Summit, party-specific conferences
4. **Content** — A weekly newsletter analyzing political narratives using your own data is great inbound marketing. "Throughline Weekly: what we saw in 50 races this week."

### Distribution warning
Political tech sales cycles are LONG (3-6 months from intro to contract). Decisions are made by committees. Most campaigns are reluctant to try new tools mid-cycle. **Sell in the off-cycle (Nov-Feb of off years) for the next cycle.** Don't expect to sell something in October 2026 — they're already locked in.

---

## Ethical & legal considerations

This is sensitive software. Some things to think about:

### Data sourcing
- ✅ GDELT, Census, FEC, BLS, FRED — all public data, no issue
- ⚠ Reddit, Bluesky, Mastodon — public posts, but be careful about user-specific aggregation (looks like targeting)
- ⚠ Google News — terms of service allow personal use; commercial use is murky
- ✅ RSS feeds — legitimate as long as you respect attribution + don't republish

### Outputs
- ❌ NEVER generate fake quotes / images / content that could be passed as real reporting
- ⚠ Talking-point generation (Feature #4) — make it clear these are drafts, human review required
- ⚠ Counter-narrative suggestions (Feature #10) — same
- ✅ Read-only intelligence (current product) — no ethical issue beyond data sourcing

### Multi-side neutrality
You're building a tool for ONE side of a race today. The same code could serve ANY race. Decisions:
- **Single-side?** Pick a partisan side and only serve them. Easier marketing (D campaigns trust each other; R campaigns trust each other). Smaller market. Avoids "you helped the other guy" awkwardness.
- **Bipartisan?** Serve both sides, never both in the same race. Larger market. Requires real org neutrality.
- **Issue-only / advocacy?** Sell to NGOs / advocacy orgs as well as campaigns. Larger market. Less politically risky.

**My take:** Pick a side for the first 2 years (where you have relationships). Expand later.

### Disclosure
If you market this as "AI-powered intelligence," there's regulatory risk for hallucinated content. Build in:
- Always show source links for any claim
- Distinguish human-edited from AI-generated in any output
- Clear product disclaimers in TOS

---

## What I'd build differently if going full SaaS

(For context, when you're at Phase 3.)

1. **Multi-tenant from day 1 of SaaS code.** Don't half-multi-tenant — every query gets a campaign_id filter, enforced at the ORM layer.

2. **Provider-agnostic LLM at the storage layer.** Don't bake OpenAI/Gemini into business logic. Make it pluggable so a customer can use Anthropic if they prefer.

3. **Per-tenant LLM-key BYOK option.** Lets cost-sensitive customers bring their own provider keys. You only charge for platform.

4. **Audit logs by default.** Every score, every promotion, every dismiss — logged with timestamp + user. Compliance + debugging.

5. **Webhook-first integrations.** Don't try to be the user's CRM. Push narrative events to Slack, Mailchimp, ActBlue, etc.

6. **Public API.** Sell to power users + integrators. Documented, rate-limited, OAuth.

7. **Privacy-by-default for opponent data.** Sensitive material (research) needs tighter access controls than "everyone in the campaign sees everything."

---

## What I'd NOT do

- Don't build a polling product (Civis owns it)
- Don't build voter-file products (NGP VAN/i360 own it)
- Don't build voter targeting (too sensitive, requires regulatory expertise)
- Don't build fundraising tools (ActBlue/WinRed solved it)
- Don't build a journalism product (different buyer, different needs)
- Don't expand to corporate PR (Cision/Meltwater own it; commoditization)

**Stay in your lane: narrative intelligence for political campaigns at sub-enterprise prices.**

---

## A bigger thought — what's the 10-year version?

A trade-off worth naming:

**Version A — Stay small + premium.** Serve 50-200 campaigns/cycle. $200-2K/mo each. Annual revenue $1-3M. Bootstrap-friendly, owner-controlled.

**Version B — Go big + commoditize.** Try to be the default. 5,000+ campaigns/cycle at $50/mo. VC-backed. Probably $10-30M annual at scale. High execution risk.

**Version C — Become a layer.** White-label/API to the NGP VANs and Aristotles of the world. They embed your narrative intelligence into their existing campaign software. You get smaller deal sizes but enterprise stability + distribution. Probably $5-15M ARR potential.

**My take:** Version A is the only one you can credibly start with from your current position. Versions B and C become available once Version A is working. Don't try to do A and B at once — they need different products + GTM + capital.
