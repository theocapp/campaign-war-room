# Feature Ideas — ranked, with sketches

15 features ranked by impact-to-effort ratio. Each has a UI sketch, what data drives it, effort estimate, and why it matters for Cognetti's race specifically.

Plus 3 "wild ideas" at the bottom — speculative but could be differentiators.

---

## Top 5 — start here

### #1. Head-to-Head Tab ⭐
**Impact: HIGH · Effort: ~2 hrs · Data: 100% existing**

105 articles in your DB mention BOTH candidates. ALL are tagged critical relevance. These are the direct head-to-head coverage — the most strategically important article slice in the entire database. Nowhere in the UI is this filterable.

**Sketch (Dashboard tab):**
```
┌─ HEAD-TO-HEAD COVERAGE ──────────────────────────────────────────┐
│  105 articles · 89 outlets · last update 14 min ago             │
│  ───────────────────────────────────────────────────────────────│
│  [Past 7d ▼] [All outlets ▼] [Sort: Recent ▼]                  │
│                                                                  │
│  🔴 NYT profiled Paige Cognetti, the "Paige Against the Machine"│
│    NYT · 12 min ago · sentiment: positive (Cognetti)            │
│    "Cognetti's blunt-talking style draws contrast with..."      │
│    Attack-on-Cognetti?: No · Attack-on-Bresnahan?: Implicit     │
│                                                                  │
│  🔴 Bresnahan uses unauthorized clips of newscasters in attack ad│
│    Reddit r/scranton · 1h ago · sentiment: negative (Bresnahan) │
│    [Show 3 related articles in this cluster ›]                  │
└──────────────────────────────────────────────────────────────────┘
```

**Why this matters for Cognetti:** Most coverage is one-sided (about one candidate). The 2-sided coverage is where the campaign narrative gets defined. Right now you have to know to search for it.

---

### #2. Daily Narrative Diff ⭐
**Impact: HIGH · Effort: ~6 hrs · Data: 100% existing**

What changed about the narrative landscape since yesterday? A morning email/page with:
- New frames added (from candidate_frames promotion)
- Frames that changed stage (emerging→spreading, etc.) — `frame_stage_history` has this
- Frames whose article count this week is 2× last week (momentum spike)
- New outlets joining a story (was 3 outlets yesterday, now 7)
- Dormant frames that resurrected (last_seen jumped)

**Sketch (email + web view):**
```
NARRATIVE DIFF — May 24 vs May 23

🟢 NEW NARRATIVE EMERGED
   "Bresnahan's Helicopter Ownership" — 3 articles · 3 outlets
   First seen 6h ago. Worth promoting from candidate_frames.

🔥 STAGE PROMOTED
   "Bresnahan's Healthcare Record":  mainstream → resurfacing
   (Trigger: 13 articles this week vs baseline 1/week)

📈 MOMENTUM SPIKE
   "Cognetti's Anti-Corruption": +4 outlets in 24h
   New outlets: Spotlight PA, Pennsylvania Capital-Star, Politico

💤 GOING DORMANT
   "Healthcare Debate" — last article 96 days ago. Auto-archive?

📊 PULSE SUMMARY
   Total relevant articles last 24h:  49 (vs 59 yesterday, -17%)
   Top issue:  Corruption & Ethics (12 mentions)
   Sentiment shift:  Cognetti +0.18, Bresnahan -0.34
```

**Why this matters:** Right now you have to compare yesterday's dashboard mentally. A diff converts data → action. This is the #1 feature that would make this tool indispensable for a daily routine.

---

### #3. Frame Sentiment Trend Chart ⭐
**Impact: MEDIUM-HIGH · Effort: ~3 hrs · Data: gdelt_tone (per article) + frame_cluster_matches**

For each frame, plot the average GDELT tone of its matched articles over the past 30 days. A frame whose tone is trending NEGATIVE → it's landing as an attack. A frame trending POSITIVE → it's working as positive coverage.

**Sketch (frame detail page widget):**
```
TONE TREND — "Bresnahan's Healthcare Record" (last 30 days)

  +2 │                          ╱
   0 │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─╱─ ─ ─ ─ ─ ─ ─ ─ baseline (neutral)
  -2 │   .                    ╱
  -4 │     .   .      .  .  ╱
  -6 │       .         . . ╱
      May 1                  May 24
      
  Article tone got SHARPER over time (steeper decline).
  This narrative is landing hard against Bresnahan.
```

**Why this matters:** "30 articles mentioned this frame" is one signal. "30 articles increasingly negative-toned" is a much sharper signal. You currently have the data; just not the chart.

---

### #4. Talking Point Generator (revive existing schema) ⭐
**Impact: HIGH · Effort: ~1-2 days · Data: critical frames + structured_extraction + an LLM call**

The `generated_talking_points` table already exists (1 row from May 8 — was prototyped, never finished). Schema includes: short_answer, long_answer, debate_answer, social_post, risk_warning, evidence_notes, source_urls_used.

For each critical-relevance frame, generate (and cache) the campaign's response language in multiple tones/lengths. Click a button on the frame card → get 4 framings to choose from.

**Sketch (frame card → modal):**
```
GENERATE RESPONSE — "Bresnahan's Healthcare Record"

  [Aggressive] [Defensive] [Pivot to economy] [Empathy-first]

  ───────────────────────────────────────────────────────────
  Aggressive (chosen):

  📱 Social post (280 chars):
    "Rob Bresnahan promised to protect Medicaid. He broke that
    promise. Northeastern Pennsylvanians deserve a representative
    who actually keeps her word. — Paige"
  
  📝 Long answer (paragraph):
    "Congressman Bresnahan looked his constituents in the eye and
    promised them he wouldn't cut Medicaid. He then voted to cut
    Medicaid by $1 trillion. This is the definition of broken
    political trust..."
  
  🎤 Debate-ready answer (50s spoken):
    "Let's be clear about what happened. My opponent..."
  
  ⚠ Risk warning:
    "Aggressive framing risks looking partisan if used in front
    of moderate audiences. Pair with empathy framing for swing
    voters."
  
  Sources used:
    1. [Times-Tribune Apr 18 — Bresnahan's vote]
    2. [Citizens' Voice May 2 — patient impact]
    3. [Bresnahan's own campaign Medicaid promise — Feb 2024]
  
  [Copy] [Save to library] [Regenerate]
```

**Why this matters:** This converts intelligence → ammunition. The current product helps you SEE what's happening. This makes it help you ACT.

---

### #5. Outlet Authority Lens
**Impact: HIGH · Effort: ~2-3 hrs · Data: outlets.authority_score + outlets.outlet_type + frame_cluster_matches**

For each frame, show the article count split by outlet tier. A story with 30 articles all from Free Republic + Breitbart (blog/social) is very different from 30 articles all from Spotlight PA + Times-Tribune + WNEP (regional/local high-authority).

**Sketch (frame card subtitle):**
```
🟦 "Bresnahan's Healthcare Record"           mainstream · viral
   227 articles · 31 variants · 9-month run
   ╔══════════╦═════════╦═════════╦══════╦═══════╗
   ║ NATIONAL ║ REGIONAL│ LOCAL   ║ BLOG │ SOCIAL║
   ║    4     ║   18    │   89    ║  64  │  52   ║
   ╚══════════╩═════════╩═════════╩══════╩═══════╝
   ↑ Mostly local + blog. Hasn't broken nationally yet.
```

**Why this matters:** Authority composition tells you whether to spend energy amplifying (it's not breaking out) or defending (it's getting elite-tier traction).

---

## Top 10 — solid wins after the first 5

### #6. Per-Issue Dashboard
**Impact: MEDIUM · Effort: ~3 hrs**

You have 13 priority issues and 250 issue_mentions. A donut chart on the Dashboard shows "what issues are dominating coverage this month." Click a slice → drill into that issue's frames + recent articles.

```
ISSUE COVERAGE THIS MONTH (250 mentions across 13 issues)

  Corruption & Ethics ████████████████ 43
  Economy & Jobs      ███████████████  40
  Taxes & Budget      █████████████    36
  Healthcare          ██████████        27
  Education           ███████           20
  Infrastructure      █████             16
  Public Safety       █████             16
  [collapsed: 6 more issues, 52 mentions]
```

---

### #7. Why-Was-This-Flagged Explainability Panel
**Impact: MEDIUM · Effort: ~3 hrs · Data: source_items.structured_extraction**

1,354 articles have rich JSON with `framing, sentiment, relevance_score, opponent_attacks[], reason`. Currently invisible to user. Add an expandable "Why?" link on every article card that opens this JSON in human-readable form.

```
[▼ Why was this flagged critical?]

  Reasoning: "Article directly attacks Bresnahan's voting record
  on Medicaid expansion, quoting both Cognetti and a constituent.
  Three named opponent attacks: pension cuts, ACA repeal vote, 
  helicopter purchase scandal. Geographic relevance: explicitly
  mentions Lackawanna County."

  Framing: opponent_attack
  Per-claim extraction: 3 attacks identified
  Sentiment: negative (toward Bresnahan)
  Relevance score: 95/100
```

This dramatically increases trust in the AI scoring — you can see the LLM's reasoning, not just the verdict.

---

### #8. Promise Tracker
**Impact: HIGH (political) · Effort: ~6-8 hrs**

You have 30 promises in `cluster_opponent_activities.promise`. Build a dedicated view showing each Bresnahan promise with:
- The original promise quote + source
- A "fulfillment status" tag (manual): kept / broken / pending / no evidence
- Articles related to it (linked via the cluster)

This is **gold for accountability messaging**. Every campaign tries to track this manually in a spreadsheet. You'd have it auto-populated from articles.

```
PROMISES TRACKER — Rob Bresnahan
─────────────────────────────────────────────
🔴 BROKEN: "Won't vote to cut Medicaid"
   Made: Feb 2024 · Cognetti Healthcare town hall coverage
   Status: Broken May 2026 — voted YES on reconciliation
   ⚠ 13 articles linking promise to broken vote

🟡 PENDING: "Hold weekly office hours in district"
   Made: Campaign launch
   Status: 3 office hours held in 18 months
   ⚠ 6 articles complaining about availability

⚪ NO EVIDENCE: "Lower taxes for working families"
   Made: Sep 2024 stump speech
   Status: No verifiable action yet
   → Should be probed
```

---

### #9. Local-Only Filter Chip
**Impact: MEDIUM · Effort: ~30 min**

140 articles tagged district_mentioned=1, 1,046 with geo_relevance=local. Add filter chip "PA-08 only" on Review Queue + Dashboard.

Currently you can't distinguish "Pennsylvania news" from "actually about my district." Adding this chip surfaces the most-relevant slice.

---

### #10. Counter-Narrative Suggestions
**Impact: HIGH · Effort: ~2 days · Data: emerging frames + opposition_attacks + LLM**

For each emerging-stage frame in opponent_type='opponent' (= "attack against Cognetti"), generate 3 response options:
1. Direct rebuttal (with evidence)
2. Pivot (acknowledge + reframe to your message)
3. Ignore + amplify positive

Similar to #4 (talking points) but specifically defensive and pre-emptive.

```
NEW ATTACK EMERGING — "Cognetti's Maternity Leave Inconsistency"
3 outlets · 5 articles · just emerged

RESPONSE OPTIONS:
  ❶ DIRECT REBUTTAL:
    "The Scranton city policy was set by the previous administration
    and is being actively reviewed. As Mayor, I've expanded paid
    leave benefits where I had authority..."

  ❷ PIVOT:
    "While Bresnahan attacks me over local policy nuance, his vote
    cost Northeastern PA families paid leave protection at the federal
    level. Let's talk about which of us actually..."

  ❸ IGNORE + AMPLIFY:
    Pair with our maternal health plan rollout this week.
    Don't engage the framing.
```

---

### #11. Public Press-Kit View
**Impact: HIGH (PR/marketing) · Effort: ~1-2 days**

Read-only sharable URL for each frame with: narrative summary, key evidence quotes, source articles. No login required.

When a reporter calls asking about an attack, you DM them a link instead of explaining over the phone. This becomes a multiplier for press relations.

```
URL pattern:  warroom.cognetti.io/public/frames/healthcare-record
              warroom.cognetti.io/public/promises/medicaid-broken
              warroom.cognetti.io/public/timeline (full race timeline)
```

The same data structure powers internal AND external view. Toggle "share" on each frame.

---

### #12. Race Setup Wizard (productization)
**Impact: VERY HIGH (productization) · Effort: ~1 day**

Given state + office level, pre-populate:
- Campaign config skeleton
- Race directory entry
- Default issue list (per office level — congressional vs state senate vs local)
- Recommended RSS feeds (per state)
- Recommended Bluesky/Mastodon keywords (per state + race)
- Recommended Google Trends queries (per candidate names entered)

Currently each new race requires manual setup of all of this. A wizard means a new race can be operational in 15 minutes instead of 4 hours.

---

### #13. Hide-Noise Filter Persistence
**Impact: LOW but daily win · Effort: ~1 hr**

Let user permanently dismiss categories: sports, entertainment, food, weather, generic_crime. 1,022 articles tagged these would disappear from review queue. Currently you re-filter every visit.

Implement as user-preferences stored locally OR (better) in a `user_settings` table for the day there are multiple users.

---

### #14. Surge Alert Push
**Impact: HIGH (defensive) · Effort: ~2 days infra-heavy**

When a frame's article count this hour > 2× hourly baseline, push notification:
- Email
- Slack webhook (campaign Slack)
- Optional SMS

Critical for breaking-attack detection. Right now you see it next morning. With surge alerts you see it in 15-60 min.

---

### #15. Outlet Bias Index
**Impact: MEDIUM · Effort: ~3 hrs · Data: outlet × frame.owner_type × sentiment**

For each outlet, compute the % of its coverage that's positive vs negative toward each candidate. Surfaces which outlets are reliably hostile vs friendly to you (vs opponent), and which are balanced.

```
OUTLET BIAS INDEX
─────────────────────────────────────────────────────────────
                       Cognetti  Bresnahan  Articles · Bias
The Times-Tribune       +0.4       -0.1       138    [balanced]
PA Capital-Star         +0.5       -0.4        23    [pro-Cognetti]
Free Republic           -0.7       +0.6        45    [pro-Bresnahan]
Spotlight PA            +0.1       +0.1        23    [neutral]
NYT (1 article)         +0.3        0.0         1    [too few]
```

Useful for press strategy ("who's likely to print this favorably") and watch-list prioritization.

---

## Wild ideas (speculative differentiators)

### W1. "Opposition Eyes" Simulator
What if there was a "view as opposition" toggle that swapped owner_types — showing the campaign what THEIR dashboard would look like to Bresnahan's team? Helps the campaign understand the asymmetric information environment + spot what they'd be worried about if roles reversed.

### W2. Devil's Advocate Daily Probe
A daily LLM prompt that takes the day's articles and asks: "If you were running Bresnahan's campaign, what 3 things would you exploit from these articles?" Gives the campaign foresight on opposition strategy.

### W3. Narrative Genome — Topic Clustering Across Frames
Use frame embeddings (we now have them in cache) to draw a 2D scatterplot of all narrative frames clustered by topic similarity. Visually shows "we have 5 frames in the corruption space, 3 in healthcare, 1 in education — coverage is unbalanced." Helps spot gaps in narrative coverage.

---

## Effort summary

| Tier | Features | Total effort |
|---|---|---|
| **Quick wins (<1 day)** | 1, 2, 5, 6, 9, 13 | ~16 hours |
| **Solid mid-effort (1-3 days)** | 3, 4, 7, 8, 10, 11, 12, 15 | ~10 days |
| **Heavier lifts (3+ days)** | 14, W1-W3 | ~7-10 days |

If you only built the 6 quick wins, you'd visibly transform the product in 2 days of focused work.

---

## Features I'd actively NOT build (or be careful with)

- **Polling integration** — public polling data lags reality by weeks. Don't waste energy unless integrating internal polling.
- **Twitter/X integration** — API costs $200/mo minimum and frequently breaks. Bluesky+Mastodon+Reddit already gives social coverage.
- **Voter file integration** — privacy/legal landmine without proper data agreements. Defer until needed.
- **Auto-response posting** — too risky. Always human-in-the-loop for actual posting. Talking-point generator (#4) is fine because it's draft-only.
- **Donor activity correlation** — FEC data is great but combining with messaging looks shady to anyone reviewing. Keep separate.
