# Noctua Knowledge Graph — Architecture Review for Second Opinion

> Self-contained brief for an outside reviewer (ChatGPT). I've built a political-campaign
> intelligence tool with a knowledge-graph layer. Before I commit ~$5–6 of LLM spend on
> a full corpus re-extraction, I want a second opinion on the architecture, the data
> model, and a handful of design decisions where I'm not sure I chose right.

---

## 0. Quick TL;DR

- **System**: News-monitoring + KG for a US House race (PA-08, Cognetti vs Bresnahan), being productized for any campaign (SaaS = "Noctua").
- **Stack**: FastAPI + SQLite (WAL) + SQLAlchemy + React/Vite + d3-force + d3-zoom. LLM = OpenAI `gpt-4o-mini` for extraction, with Groq fallbacks elsewhere. Embeddings via Gemini with OpenAI fallback.
- **Corpus**: 17,520 articles total, **5,857 race-relevant** (critical+high+medium+low buckets). 2,279 articles have been through the extractor already at v14.1/v14.3. Race-relevant articles only get extracted; the other ~11,700 are filtered as "irrelevant" by an earlier scoring pass and skipped.
- **Current KG state**: 2,083 entities (729 person + 728 org + 345 location + 281 bill, **0 events** — see §6), 9,453 mentions, 1,786 relations, 1,786 claims, 3,783 claim-supports.
- **Extractor**: now at **v14.6**. The corpus hasn't been re-extracted at v14.5 or v14.6 yet — that's the run I'm about to commit to.
- **What I want reviewed**: the architecture, the 14-ish principles I tried to implement, my staged-backfill plan, and ~10 specific open questions at the bottom.

---

## 1. What the system actually does

End-to-end pipeline for a single race:

1. **Ingestion** — RSS, GDELT, Reddit, Bluesky firehose, Mastodon, manual outlets. Articles land in a `source_items` table.
2. **Race-relevance scoring** — each article gets a per-race relevance label (`critical | high | medium | low | irrelevant`) plus content category, geo relevance, perspective, etc. Driven by `campaign_analysis.py` — a single LLM call that does relevance + summary + framing + frame matching together.
3. **Narrative frames** — named messages (e.g. "Bresnahan's stock trading", "Cognetti as outsider"). Each frame has `owner_type` ∈ {candidate, opponent, media}, a `stage` (emerging → spreading → mainstream → fading → dormant), and frame **variants** (HDBSCAN-clustered specific claim phrasings, LLM-named).
4. **Story clusters** — SimHash dedup so one wire story across 5 outlets becomes 1 cluster, not 5 mentions.
5. **Knowledge graph** — the topic of this review. Entity extraction over race-relevant articles produces a typed entity/relation graph that's overlaid on top of frames and articles.
6. **Daily briefing** — gpt-4o-mini-driven memo summarizing the day's narrative shifts, with frame momentum signals (`viral | missing_coverage | elite_only | stable`).

Surface: React/Vite SPA with a Dashboard (DDHQ-inspired 3-column layout), Narrative pages, Entity Network visualization (force-directed canvas), Review Queue, Morning Briefing, Geographic overlay, etc.

The KG is the layer this document is about.

---

## 2. KG architecture — the layers

```
┌───────────────────────────────────────────────────────────────────────┐
│ EXTRACTION (entity_extraction.py)                                      │
│   gpt-4o-mini, single call per article, Pydantic-validated JSON out    │
│   • Strict ontology: 5 entity types × 10 predicates × domain/range     │
│   • Stance: supporting / contesting                                    │
│   • Event-specific dedup gate (date OR location required)              │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ CANONICALIZATION (entity_extraction.py:canonicalize_entity)            │
│   1. Direct name match on existing canonical entity of same type       │
│   2. Alias match (entity.aliases JSON array)                           │
│   3. Embedding-similarity match (Phase 3 — NOT YET WIRED, see §11)     │
│   4. Create new entity with auto-generated canonical_id                │
│   Special: event entities require (name + date) OR (name + location)   │
│   to match, else fresh; also accumulate date_observations on match.    │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE (entity_extraction.py:persist_extraction)                  │
│   Writes to FIVE tables in one transaction:                            │
│   • entities                — canonical inventory + metadata_json      │
│   • entity_mentions         — (article × entity) provenance + UNIQUE   │
│   • entity_relations        — denormalized triple store, weight bump,  │
│                               evidence_json array of per-article rows  │
│   • claims                  — NEW: source-of-truth triple, dimensional │
│                               stance, status (active/contested/retract)│
│   • claim_supports          — NEW: per-article support with stance     │
│                               supporting|contesting, UNIQUE per pair   │
│   Auto-flip claim.status → 'contested' when both stances are present.  │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ POST-PROCESSING (scripts/)                                             │
│   • entity_partisan_guard.py        — cross-party endorses → co_spons. │
│   • entity_contradiction_detector.py — dimensional-stance conflict scan│
│   • entity_domain_range_cleanup.py  — retroactive constraint sweep    │
│   • entity_commonsense_cleanup.py   — POTUS-represents-district etc.  │
│   • entity_canonicalize_rules.py / _embeddings.py — dedup passes       │
│   • entity_apply_temporal_validity.py — role_transitions.PA-08.json   │
│   • outlet_reliability_apply.py     — outlets.bias_label + score      │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ API (routes/)                                                          │
│   /api/entity-network              — full graph (entities + relations) │
│   /api/entity-network/neighbors    — N-hop ego network                 │
│   /api/entity-network/path         — entity-to-entity path finder      │
│   /api/claims/{id}                 — claim inspector + retract/reactiv.│
│   /api/entity-review-queue/items   — contradiction queue               │
│   /api/extractor-drift/summary     — version registry diff + stale     │
│   /api/narrative-frames/{id}/graph — frame ↔ entity overlay            │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────┐
│ UI (frontend-v2/src/pages/)                                            │
│   EntityNetwork.tsx   — force-directed canvas, pan/zoom, side panel,   │
│                         path-finder popover, claim-inspector modal,    │
│                         event date-disagreement badge, N-hop button    │
│   EntityReview.tsx    — contradiction queue with approve/reject/skip   │
│   NarrativeDetail.tsx — entities/relations propagating each frame      │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 3. Schema (current)

Key KG tables (full column list in `backend/app/models.py`):

### `entities`
```
id, canonical_id (unique), type, name, aliases (JSON array),
description, affiliation (D|R|I|null), metadata_json,
mention_count, source_count, first_seen, last_seen,
seeded (bool), created_at, updated_at
```
- `canonical_id` is a stable string like `"person:cognetti"` or auto-generated `"event:auto:wilkes-barre-rally-2026-04-09"`.
- `metadata_json` carries type-specific data: persons have `role`/`state`, events have `event_date`/`event_location`/`event_type`/`date_observations`/`date_disagreement`. Avoids per-type tables.

### `entity_mentions`
```
id, article_id, entity_id, surface_text, confidence,
extraction_method, created_at
```
- One row per `(article, entity)` pair, UNIQUE constraint via in-Python check (not a DB constraint — bit fragile, see §11).
- `extraction_method` ∈ `seed_name | seed_alias | embedding | fresh`.

### `entity_relations` (denormalized triple store)
```
id, subject_id, predicate, object_id, weight,
first_seen, last_seen, sample_quote, source_articles (JSON list),
confidence, valid_from, valid_to, evidence_json (JSON array),
created_at, updated_at
```
- Triple `(subject_id, predicate, object_id)` is treated as the natural key.
- `weight` increments when more articles produce the same triple.
- `evidence_json` is an array of `{article_id, sample_quote, confidence, extracted_at, extractor_version}` — replaces the flat `(source_articles, sample_quote, confidence)` for new persists. Legacy data has lossy migration (only the first article carries the quote).
- `valid_from`/`valid_to` populated only for role-type predicates from seed file `role_transitions.PA-08.json` (e.g. Cartwright represents PA-08 2013-01-03 → 2025-01-03).
- 1,786 rows in DB.

### `claims` (new source-of-truth triple — v14.6)
```
id, subject_id, predicate, object_id (UNIQUE triple),
procedural, rhetorical, ideological,  -- dimensional stance vector
status ∈ {active, contested, retracted},
retracted_at, retracted_by, retracted_reason,
first_seen, last_seen, sample_quote, confidence,
extractor_version, created_at, updated_at
```
- Same natural key as `entity_relations`. **Yes — currently dual-written.** See §11 for the question of whether this should collapse.
- `procedural/rhetorical/ideological` are the dimensional stance values (one of `aligned | mixed | opposed | neutral | unknown`), set on creation from the predicate's default stance vector (see §5.4).
- 1,786 rows in DB (claim_layer_backfill.py created these from existing entity_relations).

### `claim_supports`
```
id, claim_id, article_id (UNIQUE pair), stance,
sample_quote, confidence, extractor_version, extracted_at
```
- `stance` ∈ `supporting | contesting`.
- Append-only per claim — every article extraction adds one row.
- When a `(claim_id, article_id)` row already exists and the new stance differs, the newer stance wins (only happens on rewrite re-extraction).
- 3,783 rows in DB. **But all from claim_layer_backfill — no real "contesting" rows yet.**

### `entity_review_decisions`
```
id, item_type, item_key, decision, notes, decided_at, decided_by
```
- For the review queue UI. `item_type` ∈ `contradiction | partisan_violation | commonsense_violation`. 0 rows currently — queue surfaces 53 contradictions waiting for human decision.

---

## 4. The extractor (v14.6, gpt-4o-mini)

### 4.1 LLM call shape

One synchronous call per article. ~3K input tokens / ~1K output tokens. JSON-mode forced (`response_format={type: "json_object"}`). Temperature 0, seed 42.

Inputs concatenated into the user message:
- Title (240 chars)
- Summary (1200 chars)
- Excerpt (**8000 chars** of raw_text — V14.4 bumped from 1500)

System prompt is ~3K chars defining: 5 entity types, 10 predicates, domain/range table, stance rules, event-specific extraction requirements, forbidden patterns.

### 4.2 Schema validation

`ExtractedEntity` and `ExtractedRelation` Pydantic models with `Literal` typed enums for type / predicate / affiliation / stance / confidence. **Bad entities/relations get dropped individually** rather than failing the whole article — original V14.1 lost 275/2400 articles to one-bad-entity failures.

### 4.3 Per-call work after the LLM responds

1. Lenient JSON parse → `(ExtractionResult, dropped_counts)`
2. For each entity:
   - Canonicalize against existing seeded + auto-discovered inventory
   - If event without date/location → reject (`entity_id = -1`)
   - If event matches existing event → record date_observation
3. Build `name → entity_id` index from the just-canonicalized entities
4. For each relation:
   - Look up subject + object in the index — drop if unresolved
   - Domain/range check (`relation_type_allowed(subj_type, pred, obj_type)`)
   - Commonsense check (`commonsense_rules.evaluate`) — `reject | downgrade_confidence | flag_for_review | accept`
   - UPSERT `entity_relations` (increment weight on existing triple)
   - UPSERT `claims` (dual-write, same triple)
   - INSERT `claim_supports` (one per article, stance from the LLM)
   - Auto-flip `claim.status = 'contested'` if both supporting + contesting now exist

All this in one transaction. Failures roll back the whole article.

---

## 5. The principles I tried to implement (and how)

This was driven by a previous ChatGPT critique that handed me ~12 GKG (graph-of-knowledge-graphs) principles. Here's what I did for each:

### 5.1 Provenance (every fact must have a source)
- `entity_mentions.article_id` — link from mention to article.
- `entity_relations.source_articles` (legacy list) + `evidence_json` (per-article structured array with quote, confidence, version).
- `claim_supports.article_id` — every claim has explicit per-article support.
- **Outlet reliability**: `outlets.bias_label` (left | center-left | center | center-right | right) and `reliability_score` (0–100, Ad-Fontes-style). 76 outlets tagged via `outlet_reliability.json`. Used in `/api/entity-network` to compute `avg_source_reliability` per relation.

### 5.2 Ontology constraints (closed vocabulary, typed edges)
- 5 entity types, 10 predicates — `Literal[...]` in Pydantic, **never invent new ones** in the prompt.
- `PREDICATE_DOMAIN_RANGE` table in code — `endorses` subject ∈ {person, organization}, object ∈ {person, organization, bill, event}; etc.
- Enforced at extraction time (LLM is told the table) AND at write time (`relation_type_allowed()` drops violators silently).
- Retroactive sweep (`entity_domain_range_cleanup.py`) deleted 593 pre-V14.3 violations.

### 5.3 Entity resolution (canonicalization)
- 4-stage resolution: name → alias → embedding (unwired) → fresh.
- Seed file `canonical_entities.PA-08.json` with 32 hand-curated entities (12 person, 10 org, 6 loc, 4 bill — **0 events**).
- Auto-discovered entities get a slug-based canonical_id (`person:auto:john-smith`).
- Retroactive canonicalization passes: rule-based fuzzy match (`entity_canonicalize_rules.py`) and embedding-similarity (`entity_canonicalize_embeddings.py`). The merge function consolidates mentions, re-points relations, sums weights, dedup'd in-Python to work around SQLAlchemy session-pending visibility.

### 5.4 Dimensional stance (not binary support/oppose)
- `StanceVector` dataclass: `procedural | rhetorical | ideological + intensity`, each ∈ `aligned | mixed | opposed | neutral | unknown`.
- `PREDICATE_STANCE` table maps every predicate to a default vector:
  - `endorses`: procedural=aligned, rhetorical=aligned, ideological=aligned
  - `co_sponsored`: procedural=aligned, rhetorical=neutral, ideological=mixed (key for cross-party crossovers — discharge petitions look like endorsements but aren't ideologically)
  - `voted_for`: procedural=aligned, rhetorical=neutral, ideological=**mixed** (was `aligned` — bug fix, see §6)
  - `attacks`/`criticizes`: opposed across all 3
- `vectors_conflict(a, b)` returns True only when two vectors disagree **on the same dimension** (not just different vectors). Reduced false-positive contradictions from 86 → 53.

### 5.5 Temporal validity (facts decay)
- `valid_from` / `valid_to` columns on `entity_relations`.
- Populated only for role-type predicates (`represents`, `member_of`, `predecessor_of`) — event predicates (`voted_for`, `endorses`, `attended`) get a point-in-time observation.
- Seeded from `role_transitions.PA-08.json` — e.g. Cartwright represents PA-08 from 2013-01-03 to 2025-01-03, then Bresnahan represents PA-08 from 2025-01-03.
- UI dims expired edges (dashed line + lower opacity) and shows them in the side panel with `expired` pill.

### 5.6 Contradiction detection
- After extraction, `entity_contradiction_detector.py` scans relations sharing the same `(subject, object)` pair and finds where stance vectors conflict on at least one dimension.
- Outputs `(relation_a_id, relation_b_id, conflicting_dimensions)` rows that surface in the EntityReview queue.
- 53 surfaced; human approves/rejects/skips; decisions persist in `entity_review_decisions`.

### 5.7 Claim layer (separate from denormalized edges)
- The pattern: edges are query-optimized, claims are the philosophical source-of-truth.
- Same triple natural key. A claim has supporting + contesting article sets, a status, and a retract trail. Edges are derived.
- **Currently dual-written** at extraction time, plus `claim_layer_backfill.py` populated the existing 1,786 entity_relations into matching claims.
- The claim inspector modal (`/api/claims/{id}`) shows the full claim with both supporting and contesting articles, retract/reactivate buttons, sample quotes.

### 5.8 Ontology drift versioning
- `EXTRACTOR_VERSION = "v14.6"` constant — stamped onto every claim_support and evidence row at write time.
- `extractor_versions.py` is an append-only `VERSIONS` list with a capability snapshot for each version (`endorses_strict`, `domain_range_enforced`, `commonsense_enforced`, `excerpt_chars`, `events_first_class`, `stance_aware`, `claim_layer_writes`).
- `/api/extractor-drift/summary` shows: relations with all-stale evidence (= 1786 today), per-version evidence counts, and human-readable `diff_summary(old, new)` explaining what each version change rejects.

### 5.9 Source reliability tagging
- See §5.1. Each outlet has bias + reliability. Surface in graph: `relation.avg_source_reliability` (0–100, mean across supporting outlets).
- **Not yet used in any auto-decision** — purely advisory in the UI. Possible future use: dim/hide claims with avg < 40.

### 5.10 Commonsense grounding
- 10 hand-written rules in `commonsense_rules.py`:
  1. POTUS / VP can't `represents` any location
  2. Senators can't `represents` a district or county or city
  3. Governors can't `represents` a district or county
  4. House reps from state X can't `represents` districts in state Y
  5. Mayors can't `represents` a state
  6. People can't `predecessor_of` themselves
  7. Bills can't act (already covered by domain/range, but explicit)
  8. Locations can't endorse / attack / vote
  9. Events can't act (subject of any non-attended relation)
  10. Person with role="dead" can't have new role-predicate relations dated after death (not yet enforced — see §11)
- Reads role/state from entity `metadata_json`. Returns `(reject | downgrade_confidence | flag_for_review | accept, rule_name)`.
- 20 retroactive violations cleaned via `entity_commonsense_cleanup.py`.

### 5.11 Semantic chunking (8K excerpt window)
- V14.4 bumped excerpt window from 1500 → 8000 chars.
- No actual chunking yet — articles longer than 8K just get truncated. The principle says we should sliding-window, but I held off because gpt-4o-mini handles 8K reliably and most race-relevant articles are under that.

### 5.12 Events as first-class entities
- V14.5 added `event` as 5th type with strict dedup contract.
- Cross-document timeline reconciliation: when multiple articles describe the same event with different dates, `metadata.date_observations` accumulates all of them; the primary `event_date` is the most-attested; `date_disagreement` flips true if there's any disagreement.
- UI badge: "⚠ DATES CONTESTED — 5 articles say 2026-04-09 · 1 article says 2026-04-10".

### 5.13 Multi-hop traversal
- `/api/entity-network/neighbors` — N-hop ego network with weight cutoff.
- `/api/entity-network/path` — entity-to-entity path enumeration (DFS with cycle detection, max 30 paths returned, max 4 hops default).
- UI: side-panel buttons for 1/2/3-hop neighborhood filter, popover for path finder with autocomplete + max-hops picker + clickable result rows that highlight the path on the canvas.

### 5.14 Contested-claim emission in the LLM prompt
- V14.6 added explicit instructions: when an article DENIES, REFUTES, DISPUTES, or CORRECTS a claim, emit `stance="contesting"` instead of dropping it.
- Net effect: fact-check articles produce contesting evidence on the original claim instead of being absent, so the system can surface `claim.status = "contested"`.

---

## 6. Past mistakes and how I fixed them

Worth showing because reviewers learn more from where I was wrong than where I was right.

| Symptom | Cause | Fix |
|---|---|---|
| 4 articles said Bresnahan endorsed ACA | Discharge petitions look like endorsements in language ("signed on to") | Reclassify those as `co_sponsored` instead of dropping. New prompt explicitly says "discharge petition = co_sponsored, not endorses" |
| 86 contradictions, mostly garbage | Binary support/oppose — voting for a bipartisan bill = "aligned" = "wins endorsement contradiction" | Decomposed into dimensional stance. `voted_for` ideological=mixed (not aligned). Conflict only if same dimension differs. → 53 real contradictions. |
| 275 articles failed extraction | One bad entity or relation in the LLM output failed the entire `ExtractionResult(**parsed)` | `_parse_result_lenient` — drop individual items, keep the rest |
| Mention counts inflated on re-run | `ent.mention_count += 1` ran BEFORE the "already exists" check | Just fixed this hour — moved counter bump inside the `if not existing:` block. |
| LLM hallucinated relations between persons (`person → represents → person`) | Prompt was vague | Domain/range table both in prompt AND enforced at write time |
| Canonicalization merge failed with UNIQUE violation | SQLAlchemy session-pending writes invisible to subsequent queries in same session | In-Python dedup via `target_mention_articles` set + `target_triples` dict |
| Events were originally dropped entirely | V14.1 dropped them because dedup was too hard | V14.5 strict dedup contract: events need name + (date OR location), else rejected |
| Drift API reported v14.6 evidence as "Unrecognized version" | Registry stopped at v14.4 | Just fixed — appended v14.5 + v14.6 entries with full capability snapshot |

---

## 7. The UI (just the KG-relevant parts)

### EntityNetwork.tsx
- Force-directed canvas (`d3-force` with charge/center/collide/link forces, settled synchronously over 220 ticks).
- d3-zoom for pan/zoom (scale extent 0.05–8, click-on-node skips zoom). Initial fit-to-view computed from positions bounding box, scale-floored at 0.35 so labels stay readable.
- Side panel: type icon, name, affiliation badge, description, mention count + first/last seen, neighborhood-depth buttons (1/2/3-hop), Connections list, Featured-in-narratives list, Recent mentions.
- For **events**: extra metadata card with event_date / event_location / "⚠ DATES CONTESTED" pill + aggregated observation counts.
- Connection rows: cursor=pointer, hover-fill, status pills (`contested` / `retracted`), click opens claim inspector modal (entity name inside the row navigates instead, via `stopPropagation`).
- Claim inspector modal: triple display with affiliation colors, dimensional-stance breakdown, retract/reactivate buttons, supporting + contesting article lists with quotes.
- Path-finder popover: From/To autocomplete + max-hops picker, results render as clickable rows; clicking highlights the path entities + edges on canvas, dims everything else.
- Saved-query chips: hand-curated shortcuts ("Who endorsed Cognetti?", "NRCC attack vectors", etc.).

### EntityReview.tsx
- Drift banner showing stale-evidence count from `/api/extractor-drift/summary`.
- Contradiction queue (53 items): for each, shows both relations + conflicting dimensions + sample quotes + approve/reject/skip buttons.

### NarrativeDetail.tsx
- For each frame: top entities and relations that propagate that frame, with in-frame / overall mention counts.

---

## 8. Current data state (as of this review)

```
source_items                   17,520
  race-relevant (any label):    5,857
  with entity_mentions:         2,279   ← only this many have been extracted so far
entities                        2,083
  seeded:                          32
  type=person:                    729
  type=organization:              728
  type=location:                  345
  type=bill:                      281
  type=event:                       0   ← 0! v14.5 hasn't been run on real corpus yet
entity_mentions                 9,453
entity_relations                1,786
claims                          1,786   ← 1:1 with relations after backfill
claim_supports                  3,783
  with stance="contesting":         0   ← 0! comes from real v14.6 extractions
entity_review_decisions             0   ← user hasn't reviewed any contradictions yet
narrative_frames                varies (active campaign frames)
```

Per the drift API:
- `current_version`: v14.6 (after the fix this hour)
- `relations_fresh`: 0 — every relation has at least one stale evidence row
- `relations_with_all_stale_evidence`: 1,786

So the entire 1,786-relation graph is at the equivalent of v14.1/v14.3, predating the events-first-class + stance-aware + claim-layer-write capabilities.

---

## 9. The backfill plan

### 9.1 What "backfill" means here

Re-running the gpt-4o-mini extractor over race-relevant articles at v14.6, so we get:
- Event entities (currently 0) — probably ~200–500 auto-discovered (rallies, debates, town halls, votes, fundraisers)
- `attended` relations linking persons/orgs to events
- Fresh v14.6 evidence on all existing relations (drift counters drop to 0)
- Real `stance="contesting"` rows on fact-check articles
- Auto-`status="contested"` claims wherever LLM flips stances
- Event date observations accumulating for the date-disagreement badge

### 9.2 Cost / runtime estimate

- gpt-4o-mini pricing: $0.15 / M input tokens, $0.60 / M output tokens
- Per article: ~3K input + ~1K output ≈ $0.001
- 5,857 race-relevant articles ≈ **~$5.86**
- Runtime: ~0.3s rate-limited per call → ~30 min sequentially
- The original backfill comment says "$0.30 / 15 min" — that's wrong (probably accurate for the V14.1 1500-char-window era).

### 9.3 Idempotency / re-extraction strategy

Two modes:
- `rewrite=False` (default): re-running on an article skips the duplicate mention (idempotent after this hour's fix), but the relation gets a new evidence entry and the claim gets a new ClaimSupport row. So **mention_count stays stable** but **claim_support rows accumulate**. Existing v14.1 evidence is preserved.
- `rewrite=True`: first calls `_rewrite_remove_article_contribution(article_id)` which decrements relation weights and removes mentions, THEN writes the v14.6 extraction. Clean cutover but destroys the old extraction signature.

Question for review: which mode for the backfill? See §11.

### 9.4 Staged plan

1. **Stage 1** — `--limit 50` on the highest-relevance articles (the script orders by `race_relevance_score desc`). Spend ~$0.05. Inspect:
   - First real event entities — are they sensible? Do dedup gates fire correctly?
   - Are contradictions surfacing? Is the dimensional stance picking up real ones?
   - Are commonsense / domain-range rules over-rejecting?
2. **Stage 2** — `--limit 0` (all 5,857) if Stage 1 looks good.

---

## 10. The two fixes I just applied (this hour)

For completeness — these are NEW since the last ChatGPT review.

### Fix #1: `extractor_versions.py` registry append
Added v14.5 and v14.6 entries with full capability snapshots and added `events_first_class` / `stance_aware` / `claim_layer_writes` boolean fields. Updated `diff_summary()` to explain the v14.4 → v14.5 → v14.6 transitions in plain English. Drift API now correctly reports `current_version: v14.6`.

### Fix #2: Mention-count idempotency
Moved `ent.mention_count += 1` and `first_seen` / `last_seen` updates inside the `if not existing:` block in `persist_extraction`. Re-running the backfill on already-extracted articles will no longer inflate counts.

---

## 11. Open questions I want a second opinion on

These are the actual decision points where I'm not sure I chose right. Hit me with disagreement.

### Q1 — Is the claim layer the right pattern, or am I overengineering?

The dual-write is: every relation extraction writes BOTH an `entity_relations` row AND a `claims`+`claim_supports` row. They have the same natural key `(subject_id, predicate, object_id)`. The `claims` table adds dimensional stance, retract trail, and per-article stance via `claim_supports`. The `entity_relations` table stays as a denormalized query-optimized edge list.

**Concern**: I could collapse them into one — `entity_relations` already has `evidence_json`, and I could add `status` + `dimensional_stance` columns there. That would remove the dual-write entirely. The argument for keeping them separate is that "edge" and "claim" are conceptually different (edges are query-time, claims are knowledge-base assertions). But I'm not sure that distinction is worth the schema duplication.

**What I want to know**: would you keep them separate or collapse?

### Q2 — Strict event dedup: is `name + (date OR location)` too tight or too loose?

Right now an event extraction without `event_date` OR `event_location` is dropped at canonicalization. The LLM is told this explicitly.

**Concern A — too tight**: real articles often say "the rally" or "Cognetti's announcement event" without a date and without naming a location. We lose these.
**Concern B — too loose**: `(name + date)` alone could collapse two distinct events if they have similar names on the same date. `(name + location)` alone could collapse a 2024 and a 2026 rally at the same venue.

**What I want to know**: should I require BOTH date AND location? Should I add `event_type` as a third dedup signal?

### Q3 — Stance dimensions: 3 (proc/rhet/ideo) — overengineered?

The reason I went dimensional was that voting for a bipartisan bill made the binary system say "Republican aligned with Democrat → contradiction with attacks". Three dimensions fixed that.

But now I have to teach the LLM to emit dimensional stance, OR I infer it from the predicate (which is what I currently do — every relation gets its predicate's default vector, regardless of article context). Inferring loses signal: a Republican who voted_for ACA-subsidies because of constituency pressure has different rhetorical stance than one who voted_for because they support the policy.

**What I want to know**: should the LLM be emitting per-relation stance vectors (more tokens, more complexity)? Or is the predicate-derived approach good enough as a first cut?

### Q4 — Should `claim.status = "contested"` auto-flip be reversible?

Currently: when `claim_supports` contains both `supporting` and `contesting`, `claim.status` flips to `contested`. If contesting goes back to 0 (e.g. all contesting articles get retracted), I auto-flip back to `active`.

**Concern**: this means a single contesting article can flip a 50-supporter claim into "contested", which the UI shows as a yellow warning pill. That seems over-sensitive. Should I require a ratio threshold (≥10% contesting evidence)? Reliability-weighted (need contesting articles from outlets with reliability_score > 60)?

### Q5 — Re-extraction mode for the backfill: rewrite=True or False?

For the 2,279 articles already extracted at v14.1/v14.3:
- `rewrite=False`: keeps old extractions, adds v14.6 evidence on top. Drift counters won't fully drop because old evidence stays present alongside new.
- `rewrite=True`: clean cut. Old extraction (and its constraint violations) disappear, replaced by v14.6. But "evidence_json" loses the historical record of when each fact was first attested.

My current lean: `rewrite=True` for already-extracted articles, plain extraction for never-extracted ones. The script doesn't currently distinguish — need to add a filter.

**What I want to know**: am I right that rewrite=True is the right choice? Or is preserving historical extraction data more valuable?

### Q6 — Phase 3 canonicalization (embedding similarity) is unwired. Should I land it before the backfill?

Right now canonicalization is name-exact + alias-exact. Embedding similarity (suggested ≥0.85 cosine, gemini-embedding-001) would catch:
- "Paige Gebhardt Cognetti" → cognetti (alias miss because middle name)
- "U.S. House Representative Bresnahan" → bresnahan (modifier prefix)
- "Local 0123, UFCW" → ufcw (organizational subset)

I have `entity_canonicalize_embeddings.py` as a post-extraction sweep, but ideally it should be inline at canonicalization time so the first extraction gets the right entity.

**What I want to know**: is this worth doing before the backfill? Or run backfill first, sweep after?

### Q7 — Source reliability is computed but not USED. Wasted work?

Every relation has `avg_source_reliability`. The UI shows it advisory but no automation acts on it. Should I:
- Drop relations whose avg < 30 (clear conspiracy-tier sources)?
- Discount weight by reliability ratio?
- Hide low-reliability claims unless a high-reliability one exists?

Or is keeping reliability advisory-only the right call for a human-in-the-loop product?

### Q8 — Commonsense rules: 10 hand-coded rules. Will I write 100?

Each rule is bespoke Python like `if subject_role == "POTUS" and predicate == "represents": return reject`. It's working for the current race but won't scale to "any campaign" if every race needs a different rule set.

**What I want to know**: is this a case where an LLM-as-judge call at extraction time would be better (one extra ~$0.0001/article to ask "does this make commonsense sense?")? Or do I keep going with rules and accept the per-race customization burden?

### Q9 — Frame-graph integration: should KG entities seed frame promotion?

Right now narrative frames and KG entities are connected only by the `/api/narrative-frames/{id}/graph` view (which entities propagate which frames). Frames are created by a separate scoring pipeline.

**Question**: should an emerging high-mention entity (e.g. a new opponent who appeared in 30 articles in 3 days) auto-create a candidate frame? Or is frame-creation a human decision the system shouldn't automate?

### Q10 — Am I missing any principle entirely?

Things I'm aware I haven't built:
- **Confidence propagation** through multi-hop paths (a 4-hop path has compounding uncertainty)
- **Negation handling beyond stance** — "X did NOT meet with Y" is currently extracted as `met → contesting`, but "X was NOT at the rally" is structurally different from "X disputes attending the rally"
- **Temporal contradiction** — same entity, same predicate, different valid_from windows that overlap incorrectly
- **Counterfactuals** — "if Bresnahan loses, his role expires Jan 3 2027" → not represented

Of these, which would you prioritize? Any I'm not listing?

---

## 12. Code pointers (for reading)

If you want to dig into specifics, these are the load-bearing files:

```
backend/app/services/entity_extraction.py     # extractor + persist_extraction
backend/app/services/extractor_versions.py    # version registry
backend/app/services/stance.py                # dimensional stance + conflict logic
backend/app/services/commonsense_rules.py     # 10 role-aware rules
backend/app/models.py                         # SQLAlchemy schema (Claim, ClaimSupport @ ~line 880+)
backend/app/routes/entity_network.py          # graph API + neighbors + path
backend/app/routes/claims.py                  # claim inspector + retract
backend/app/routes/entity_review.py           # contradiction queue
backend/app/routes/extractor_drift.py         # drift summary
backend/app/routes/narrative_frames.py        # frame ↔ entity overlay
backend/scripts/entity_extraction_backfill.py # the script we're about to run
backend/scripts/entity_drift_reextract.py     # targeted re-extract at new version
backend/scripts/claim_layer_backfill.py       # one-shot: existing rels → claims
backend/data/canonical_entities.PA-08.json    # 32 seeded entities
backend/data/role_transitions.PA-08.json      # temporal validity seed
backend/data/outlet_reliability.json          # 76 outlets w/ bias + reliability

frontend-v2/src/pages/EntityNetwork.tsx       # canvas + side panel + inspector + path finder
frontend-v2/src/pages/EntityReview.tsx        # review queue
frontend-v2/src/api/client.ts                 # API methods
```

---

## 13. What I'd most like to hear from you

In order of how useful it'd be:

1. **Architecture-level**: any of the 10 questions in §11 where you disagree with my current direction, with reasoning.
2. **Schema**: anywhere the dual claims/relations setup is wrong or where I'm missing a column I'll regret not having.
3. **Backfill plan**: is the staged approach right? Is rewrite=True for already-extracted articles correct? Anything I should do FIRST before running the backfill?
4. **Missed principles**: anything in the GKG literature I should be implementing that I'm not.
5. **Cost / model choice**: should I be using a stronger model than gpt-4o-mini for extraction given the dimensional-stance and event complexity? (Cost trade-off: 4o-mini = $5.86 / 4o = ~$30 / o1-mini = ~$10.)
6. **UI**: anything in the EntityNetwork visualization that's a known antipattern for graph review tools.

Thanks. The goal is to ship a defensible KG-backed narrative-intelligence tool to a real campaign, then productize it for any campaign.
