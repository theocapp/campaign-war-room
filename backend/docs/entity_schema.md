# Entity Schema — Phase 1 of Feature A (real KG pipeline)

This is the contract every downstream feature (Entity Network, Geographic Overlay, Timeline, Search) plugs into. It defines:

1. What kinds of "things" we extract from articles
2. What fields each thing has
3. How those things connect to each other
4. The pre-populated **canonical seed list** the extractor starts with

The cookbook approach (PERSON / ORG / LOCATION / EVENT / ARTIFACT) is the right shape but too generic for political intel. We tighten it to PA-08-style political races while keeping the structure portable.

---

## Entity types (4) — V14.1 after inter-session review

Initial draft proposed 6 types. Inter-session review caught two problems:
1. `issue` contradicted the schema doc's own design notes ("Issues NOT modeled as entities — too fuzzy, LLM hallucinated them constantly in the prior KG attempt"). Narrative frames already handle issue tracking. Removed.
2. `event` is the hardest type to canonicalize — "Cognetti's launch" vs "April 9 kickoff" vs "the Scranton event" → same thing, three surface forms. This was a failure mode of the previous KG. Removed for V1; revisit when Sonnet resolution layer is in place.

| Type | What it is | Examples |
|---|---|---|
| `person` | A human in the race ecosystem — candidates, officeholders, donors, activists, journalists, family members | Paige Cognetti · Rob Bresnahan · Borys Krawczeniuk (reporter) · Trump · Shapiro |
| `organization` | Any group acting as a unit — parties, committees, PACs, unions, news outlets, advocacy groups, government bodies | NRCC · EMILY's List · AFL-CIO · Fox News · House Freedom Caucus |
| `bill` | Specific legislation under consideration or recently voted on | ACA Subsidy Extension · Stock Trading Ban · Trump Tax Cut Extension |
| `location` | A named geographic place tied to the race | Scranton · Wilkes-Barre · Luzerne County · Pocono region |

**Explicit exclusions (sources of the V1 narrowing):**
- **Issues** (healthcare, immigration, ICE, taxes, education, crime) — too fuzzy to extract reliably as standalone entities. The narrative_frames system already handles "what topics are being debated" at a more curated grain. Issues live as narrative-frame metadata, not entity rows.
- **Events** (rallies, debates, fundraisers, votes-as-events) — hardest type to canonicalize. Same event surfaces as "Cognetti's campaign launch" / "the April 9 kickoff" / "the Scranton announcement" — three forms, one event, no clean dedup heuristic. Revisit when Sonnet resolution layer exists.
- **Quotes** — captured as relationship sample text (`sample_quote` field), not first-class objects.
- **Media outlets** — treated as `organization` with `subtype="news"`. Avoids duplicating the entity model (Scranton Times-Tribune, Fox News, etc. are orgs).
- **Generic phrases** in headlines like "Four House Republicans" — these are NOT entities, they're descriptive references the LLM should ignore.

## Entity fields

Every entity has:
```
canonical_id:   stable string ID (e.g. "person:cognetti", "org:nrcc")
type:           one of the 5 above
name:           canonical display name ("Paige Cognetti")
aliases:        [string]  — other names this entity is known by ("Mayor Cognetti", "Paige")
description:    one-sentence summary, LLM-written
affiliation:    "D" | "R" | "I" | null  — party (people, orgs, events; null for bills/locations)
mention_count:  int — auto-updated from extraction
first_seen:     ISO date  — first article that mentions this entity
last_seen:      ISO date  — most recent
source_count:   int — distinct articles mentioning
```

Type-specific extras:

**person:** `role` (mayor, congressman, journalist, etc.) · `city` · `state`
**organization:** `subtype` (party_committee, pac, union, news, advocacy, government) · `home_state`
**bill:** `status` (pending, passed, failed) · `congress_session` · `summary`
**event:** `event_date` · `event_location_id` (FK to location entity) · `event_type` (rally, debate, vote, fundraiser, town_hall)
**location:** `location_type` (city, county, region, district) · `state` · `parent_id` (FK — Scranton's parent is Lackawanna County)

## Relationship types (9 verbs) — V14.1 after inter-session review

Initial draft proposed 11. Inter-session review dropped two:
1. `attended` — depends on `event` entity targets which we removed.
2. `donated_to` — donation data lives in FEC filings, not news prose. Journalists don't write "X donated to Y". Including the verb just creates hallucination surface. Add back when FEC import lands as a separate data source.

| Predicate | What it means | Direction | Corpus freq |
|---|---|---|---|
| `endorses` | Supports / allies with / praises | source → target | 15.8% |
| `criticizes` | Publicly criticizes (lighter than attack) | source → target | 15.8% |
| `attacks` | Hostile attack / accusation | source → target | 6.2% |
| `voted_for` | Voted yes | person → bill | 1.4% |
| `voted_against` | Voted no | person → bill | 0.6% |
| `co_sponsored` | Co-sponsored bill | person → bill | 1.4% |
| `represents` | Currently holds office for this district | person → location | 2.6% |
| `member_of` | Is part of / leads / works for | person → org | 1.8% |
| `predecessor_of` | Held this office before X | person → person | 1.4% |

Notes:
- `criticizes` and `attacks` are intentionally separated — the difference between "criticized the vote" (normal political discourse) and "attacked her record" (hostile framing) matters for the perspective audit signal.
- `voted_for` and `co_sponsored` are separate — a co-sponsor signed onto the bill text, a yes-voter just supported it on the floor. Different commitment levels.
- `predecessor_of` is a one-time relationship per office, but it's clean and frequently mentioned ("Bresnahan unseated Cartwright in 2024").

Each relationship row has:
```
id:               unique
subject:          entity canonical_id
predicate:        one of the 8 above
object:           entity canonical_id
weight:           int — count of supporting articles (auto-updated)
first_seen:       ISO date — earliest supporting article
last_seen:        ISO date — most recent
sample_quote:     short text excerpt from one supporting article
source_articles:  list of article ids
confidence:       "high" | "medium" | "low"
```

## Canonical seed list

We pre-populate 32 entities the extractor knows about up front. This radically reduces dedup work — when an article mentions "Paige Cognetti" or "the mayor of Scranton", the extractor links to the existing canonical entity instead of creating a duplicate.

The seed list for PA-08 (this race) lives in `backend/data/canonical_entities.PA-08.json` and is named by district code so future races bring their own seed files.

**People (12)**: Cognetti, Bresnahan, Trump, Shapiro, Vance, Cartwright, Mike Johnson, Jeffries, Fetterman, McCormick, Casey, Krawczeniuk
**Organizations (10)**: NRCC, DCCC, EMILY's List, Club for Growth, AFL-CIO, Freedom Caucus, Heritage, Scranton Times-Tribune, Times Leader, PA Capital-Star
**Locations (6)**: Scranton, Wilkes-Barre, Hazleton, Luzerne County, Lackawanna County, PA-08
**Bills (4)**: ACA Subsidy Extension, Federal Medicaid Cuts, Stock Trading Ban, Trump Tax Cut Extension

The auto-discovery layer adds anyone NOT in the seed list as new canonical entities — this gives us the long tail (local council members, smaller PACs, etc.) that a campaign manager won't manually maintain but should know exists.

## Extraction pipeline (what runs over each article)

```
article in
  → Haiku call (cheap, fast) with Pydantic-constrained output
       returns: list of (mentioned_entity_name, type, surface_text)
              + list of (subject_name, predicate, object_name, sample_quote)
  → canonicalize step (per mention):
       (a) check seed-list aliases for exact match
       (b) check existing canonical entities via embedding similarity
       (c) if neither matches, create new entity with auto-generated canonical_id
       (d) flag low-confidence merges for the Review Queue
  → persist as entity_mentions + relationships rows
```

LLM cost estimate: ~$0.0001/article via gpt-4o-mini. We run extraction over the **race-relevant subset** only (articles with race_relevance_score ≥ 50). For PA-08 today that's ~2,367 articles = ~$0.24 one-time. Off-topic articles in the FTS index (~16,305 total) are NOT extracted — they wouldn't yield useful entities anyway.

Resolution layer (Sonnet) runs as a separate batch job nightly:
- Re-examines low-confidence merges
- Splits over-merged entities (Patrick Tate Adamiak shouldn't be merged with Patrick Murphy)
- Suggests aliases the extraction missed

LLM cost estimate: ~$0.001/canonical entity (only ambiguous ones, ~50/race) = ~$0.05.

**Total one-time cost: ~$0.30 for PA-08 backfill. Ongoing: ~$0.0001/new article.**

## What the user reviews before we proceed

1. **This document** — does the entity/relationship model match how you think about the race?
2. **Canonical seed list** (in `backend/data/canonical_entities.PA-08.json`) — does it include the right starting entities? Are any wrong?
3. **Pydantic schema** (in `backend/app/services/entity_extraction.py`) — code-level review of the structured output the LLM will produce.

If all three look right, Phase 2 (the extraction script) becomes a mechanical translation of this spec.

## Known design decisions worth flagging

| Decision | Why | Tradeoff |
|---|---|---|
| 5 entity types, not more | Tighter schema = less LLM confusion = better extraction accuracy | Some things (issues, claims) don't fit cleanly |
| 8 relationship verbs, not 16 | Same reason — less LLM choice paralysis | Loses some nuance ("criticized vs. attacked vs. accused") |
| Canonical seed list per district | Avoids dedup headaches for known players | Needs maintenance per race (but small ~30-entity list) |
| Embeddings + LLM dedup, not pure-LLM | Empirically the safest hybrid (per `project-kg-pivot` notes) | More moving parts than the cookbook recipe |
| Per-article extraction, not corpus-wide | Scales linearly, easy to update on new articles | Misses cross-article inference (handled by resolution step) |
| Issues NOT modeled as entities | Too fuzzy, LLM hallucinated them constantly in the prior KG attempt | Lose the ability to do "show me everything tagged 'healthcare'" until we add issue tags as article metadata |

## How this generalizes to OTHER races

The 4 entity types and 9 verbs are deliberately race-agnostic — they describe any US political contest, not just PA-08. What changes per race is the **seed list**, not the schema.

For a new race we run a **5-minute setup script** (planned, not built yet) that:

1. Reads `campaign.district` (e.g. "CA-12") and basic profile (candidate name, party, opponents)
2. Pulls the top-mentioned entities from that race's article corpus (same analysis as `scripts/entity_schema_analysis.py` but per-race)
3. Cross-references against US Congress public data for officeholders + predecessors
4. Cross-references against FEC committee filings for active PACs
5. Generates `canonical_entities.<DISTRICT>.json` with:
   - The candidate + opponent + their party committees (always present)
   - Top 10-15 entities discovered from the corpus
   - National figures (President / VP / Speaker / Minority Leader — always present)
   - Major bills the candidate has positioned on
   - District-specific locations (auto-derived from the GeoJSON we already cache)
6. Issues are stable across races — the 6 issue entities are the same for any US race

The user then reviews the auto-generated seed list before the extraction backfill runs. Same flow as Phase 1 here, just automated.

**Bottom line:** schema + verbs are universal; seed list is per-race but mostly auto-generated. Manual entry should be rare (a journalist new to the district, an obscure local PAC, etc.).
