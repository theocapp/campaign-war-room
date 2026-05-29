# Free Data Sources — APIs you're not using

Ranked by impact/effort. Each has: what it gives you, what feature it unlocks, integration complexity, and a confidence tag for my own claims:
- ✅ I'm sure this exists and is free at the relevant tier
- ⚠ I recall this; verify before integrating
- ❓ Need to check

---

## TIER 1 — Should integrate ASAP

### 1. ProPublica Congress API ✅
**URL:** `https://projects.propublica.org/api-docs/congress-api/`
**Cost:** Free, just need API key

**What it gives you:**
- Every Bresnahan vote (Yea/Nay/Present/Not Voting) with bill metadata
- Bresnahan's bill sponsorships + co-sponsorships
- Floor statements
- Committee assignments
- Roll call records

**Features it enables:**
- **Promise Tracker (#8)** — auto-detect "promise X" vs "voted Y" contradictions
- **Bresnahan's voting record** as a dedicated tab
- Auto-classify articles by which Bresnahan vote they reference
- Pre-populate frame: "Voted YES on $X — articles about it: 12"

**Integration:** ~4 hrs. Simple REST, well-documented, response format stable. Add a scheduled job to nightly-sync Bresnahan's recent votes into a `congress_votes` table.

**One catch:** API has a 5,000 req/day limit. For one congressperson you'll use <50/day. Fine.

---

### 2. FEC API ✅
**URL:** `https://api.open.fec.gov/developers/`
**Cost:** Free (use data.gov key — instant signup)

**What it gives you:**
- Quarterly fundraising totals for Bresnahan vs Cognetti
- Top contributors (individual + PACs)
- Independent expenditures (PAC money spent attacking either candidate)
- Geographic donor distribution (which zip codes are donating)
- Comparison to historical PA-08 races

**Features it enables:**
- **Money + Narrative dashboard** — overlay quarterly fundraising on top of narrative momentum (does an emerging attack correlate with a fundraising spike for the attacker?)
- "Independent expenditures alert" — when a Super PAC drops $500K on Bresnahan attack ads, you know within 24h
- Donor geography map (mostly local? mostly out-of-state?)
- For productization: any race that's federal has FEC data

**Integration:** ~6 hrs. REST API, pagination is the only annoyance. Nightly job pulls recent filings.

---

### 3. Congressional bulk data (govinfo.gov) ✅
**URL:** `https://www.govinfo.gov/bulkdata`
**Cost:** Free

**What it gives you:**
- Full text of every bill Bresnahan votes on
- Committee reports
- Floor proceedings (Congressional Record)

**Features it enables:**
- Article-to-bill linking — when an article mentions "the One Big Beautiful Bill", auto-link to the bill text
- Generate "what the bill actually says" panel from your own RAG over bills
- Counter-narrative source material — "the bill text actually states..."

**Integration:** ~6 hrs for basic use. Bulk XML downloads, parse what you need.

---

### 4. Census ACS API (American Community Survey) ✅
**URL:** `https://api.census.gov/data.html`
**Cost:** Free

**What it gives you (at PA-08 district level):**
- Demographics: age, race, income, education
- Industry breakdowns (% manufacturing, % healthcare, % retail)
- Veterans count
- Healthcare coverage rates
- Housing tenure (own/rent), vacancy
- Commute patterns, broadband adoption
- Disability rates
- Language spoken at home

**Features it enables:**
- **Demographics card** on the Race tab — "PA-08 is 89% white, 18% over 65, median income $58K, 12% veterans"
- Context for narrative resonance — "Healthcare polls high here because 18% are 65+"
- Voter targeting hints
- For productization: works for ANY congressional district / state / county

**Integration:** ~4 hrs. ACS variables are arcane but Wikipedia + AI can map them. Cache aggressively (data updates yearly).

---

### 5. FRED — Federal Reserve Economic Data ✅
**URL:** `https://fred.stlouisfed.org/docs/api/`
**Cost:** Free with API key

**What it gives you:**
- Local unemployment rates per metro area (Scranton-Wilkes-Barre MSA)
- Local labor force statistics
- State-level GDP, inflation, housing prices
- Industry-specific employment trends

**Features it enables:**
- **Economic context widget** — "Scranton unemployment 4.2%, up 0.3 since last quarter"
- Articles about economy auto-overlay with actual numbers ("Bresnahan claims jobs are up — FRED says local unemployment rose")
- Trend lines for kitchen-table issues

**Integration:** ~3 hrs. Clean REST API, very stable.

---

## TIER 2 — High value, more setup

### 6. BLS Local Area Unemployment Statistics ✅
**URL:** `https://www.bls.gov/lau/`
**Cost:** Free

**What it gives you:** County-level unemployment, by month. Lackawanna County, Luzerne County, etc.

**Features it enables:** Geographic economic context. "Lackawanna unemployment 4.5%, Luzerne 3.9%." Useful for issue-specific microtargeting.

**Integration:** ~3 hrs. Similar to FRED.

---

### 7. LegiScan ⚠ (verify pricing)
**URL:** `https://legiscan.com/legiscan`
**Cost:** Free tier exists; verify limits

**What it gives you:** State legislative data — bills, votes, sponsors at the PA state level. Useful if Cognetti's race highlights state-level policy contrasts.

**Features it enables:** Track PA state legislature votes on issues that affect PA-08. Cross-reference with congressional activity.

**Integration:** ~4 hrs. REST API.

---

### 8. Substack RSS (newsletters) ✅
**URL:** Any Substack newsletter has `/feed` available

**What it gives you:** Independent political commentary, often the first to break local stories. Spotlight PA, Sister District, regional political blogs.

**Features it enables:** Catches narratives forming in expert commentary before they hit major outlets. Add a list of relevant Substacks as RSS feeds — no special integration.

**Integration:** 30 min — just add URLs to the existing RSS feeds table.

**Suggested Substacks to add:**
- PoliticsPA (already in outlets, check if they have a feed)
- Lackawanna Today
- The Bulwark (national political analysis)
- Slow Boring (Matt Yglesias — moderate political analysis)
- TPM Newsletter

---

### 9. YouTube channel RSS ✅
**URL pattern:** `https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}`
**Cost:** Free, no API key

**What it gives you:** Direct video uploads from specific channels. No YouTube API quota burn.

**Channels to add for PA-08:**
- Cognetti's official channel
- Bresnahan's official channel
- WNEP-TV
- WBRE
- WVIA
- ABC27
- Local debates / town halls archives

**Features it enables:** Auto-ingest video uploads. Already have YouTube as 2nd-highest-volume relevant source — adding direct channels improves signal-to-noise vs current keyword search.

**Integration:** 1 hr — add as rss_feeds rows with source_type='video' (or new type).

---

### 10. House.gov + Bresnahan Press Office RSS ⚠
**URL:** `https://bresnahan.house.gov/feed` or similar — verify
**Cost:** Free

**What it gives you:** Direct from-the-source Bresnahan statements, in real time. Currently you ingest his press releases via Google News + manual capture — slower + lossy.

**Features it enables:** Detect his framing/positioning the moment he publishes it. Beat the news cycle.

**Integration:** 30 min if the feed exists at a known URL. Otherwise need to scrape his press release page (~2 hrs).

---

## TIER 3 — Niche but interesting

### 11. Race-rating agencies (Cook Political Report, Sabato, Inside Elections) ⚠
**Cost:** Free (RSS or public ratings pages)

**What it gives you:** Lean R / Lean D / Tossup / Likely R ratings. Changes to ratings are real events ("Cook moved PA-08 from Lean R to Tossup").

**Features it enables:** Auto-alert on rating changes. Add to morning briefing. Currently you have one frame ("Shapiro backs Cognetti; PA-08 rated Toss-Up as polls tighten") that captures one such change — automate detection of these.

**Integration:** ~2 hrs. Probably need to scrape; check for RSS first.

---

### 12. Federal Election Commission Independent Expenditure feed ⚠
**Cost:** Free

**What it gives you:** Real-time alerts when a Super PAC files an independent expenditure (IE) — when they buy attack ads.

**Features it enables:** "Action alert — Americans for Prosperity just dropped $250K on TV ads attacking Cognetti" the same day it files. Currently you find out when ads start airing.

**Integration:** ~3 hrs. FEC has IE-specific endpoints; need to filter for PA-08 spending.

---

### 13. Wikipedia API ✅
**URL:** `https://www.mediawiki.org/wiki/API:Main_page`
**Cost:** Free

**What it gives you:** Article revision history. When Bresnahan's Wikipedia page gets edited (especially aggressively), that's a signal.

**Features it enables:** "Bresnahan's Wikipedia page got 14 edits in the past 7 days, mostly anonymous IPs editing the same section about his stock trades" — early warning of organized narrative pressure.

**Integration:** ~2 hrs. Reasonably stable API. Set up a watcher for both candidates' pages.

---

### 14. Reddit JSON endpoints ⚠ (changed in 2024)
**URL pattern:** `https://www.reddit.com/r/{sub}/.json` or specific post `.json`
**Cost:** Free but rate-limited

**What it gives you:** Subreddit feeds, post details, comment threads.

**Features it enables:** Deeper than your current Tavily Reddit scraping. Track r/scranton, r/Pennsylvania, r/NEPA day-by-day.

**Caveat:** Reddit cracked down on free API access in 2023. Direct `.json` endpoints still work in 2025 but with stricter rate limits + user-agent requirements.

**Integration:** ~4 hrs. Need to handle 429s carefully + spoof a believable user-agent.

---

### 15. ArXiv / SSRN academic papers ❓
**Cost:** Free

**What it gives you:** Recent academic papers on PA politics, healthcare policy, etc.

**Features it enables:** Strategic depth — when a paper drops that contradicts opposition's framing, you can use it.

**Integration:** ~3 hrs.

**Honest assessment:** Lower priority. Academic papers move too slowly to affect campaign cycle.

---

### 16. National Weather Service / NOAA ✅
**URL:** `https://www.weather.gov/documentation/services-web-api`
**Cost:** Free, no API key

**What it gives you:** Weather events in PA-08.

**Features it enables:** Detect when extreme weather hits the district (heat wave, flood, etc.). These often trigger constituent service moments — campaign can capture them.

**Integration:** ~2 hrs.

---

### 17. Internet Archive Wayback Machine ✅
Already used. Worth noting as a fallback — can reach historical versions of Bresnahan's website to track promise/position changes over time.

---

## What I deliberately did NOT recommend

| Source | Why skip |
|---|---|
| Twitter/X API | $200/mo minimum, frequently breaks, hostile to scrapers. Bluesky + Mastodon + Reddit cover social well enough. |
| LinkedIn | API extremely restricted. Manual capture only. |
| Polling aggregators (538, RCP) | Polling data lags news by 2-4 weeks. Useful for trend confirmation but not action. |
| Vote Smart | Site has been declining in maintenance since 2023; data may be stale. |
| Snopes/PolitiFact APIs | Mostly geared for fact-check claim verification, slow update cadence. Manual link is fine. |
| Google Civic Information API | Was deprecated in 2024. Use Census ACS instead for demographics. |
| Twilio for SMS alerts | Not free, and SMS notification is overkill for daily-cycle work. Email is fine. |
| Stripe/Plaid | Not relevant to political intelligence. |
| AI training data licensors | Hugging Face datasets are interesting but unrelated to your domain. |

---

## Integration sequence I'd recommend

If you're going to do 3, do them in this order for maximum value-per-hour:

**Week 1: ProPublica Congress + FEC + YouTube channel RSS** (~12 hrs total)
- ProPublica unlocks promise tracking
- FEC unlocks fundraising context
- YouTube RSS dramatically improves coverage signal-to-noise

**Week 2: Census ACS + FRED + BLS** (~10 hrs)
- Demographic + economic context for the district
- Makes the product MUCH more useful for productization (works for any district)

**Week 3: Race-rating agencies + IE feed + Substack RSS** (~7 hrs)
- Strategic / political-insider signal
- Catches narrative-shaping moments earlier

After this, you've roughly tripled the data your system reasons over, without paying for any APIs.

---

## Estimated ongoing costs

| Item | Cost |
|---|---|
| All Tier 1-3 APIs (rate-limit friendly) | $0 |
| Server to host scheduled fetches | Already paying for backend hosting |
| Storage growth (~5-10 MB/month per data source) | Negligible |
| LLM cost for enrichment of new data | $5-20/mo at current volumes |

Total marginal cost of adding all 15+ new sources: ~$25/mo.
