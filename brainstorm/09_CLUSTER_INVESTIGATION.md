# Rabbit Hole — Why 94% of relevant clusters are size-1

Investigation triggered by the user's earlier (correct) pushback that the size-1 dominance might just be irrelevant noise. Even after filtering for `race_relevance_label='critical'`, 94% of clusters are still size-1 — which means there's a real clustering bug, not just noise.

This doc: what I found, what the root cause is, and how to fix it.

---

## TL;DR

The clusterer uses `simhash_64` + title Jaccard. Both measure **lexical** similarity (specific word overlap). When 5 different outlets cover the same story with different headlines + paraphrased content, lexical similarity is low → they get 5 separate clusters.

Concrete: the "Bresnahan stock trades" story is in **5 different clusters** instead of one. The Medicaid vote story is in **10+ clusters**.

**Fix:** Add a 4th clustering rule using semantic (embedding) similarity. We already compute embeddings for candidate_frames; reuse them here.

---

## What I found (the evidence)

### Story 1: "Bresnahan stock trades" splits into 5 clusters

Pulled from `story_clusters` and `source_items`:

```
cluster_id      simhash_64        title
─────────────────────────────────────────────────────────────────────────
source-396      c2ad9400180232bf  "Cognetti accuses Bresnahan of 'public corruption'..."
source-1307     20e9834128212047  "Scranton mayor launches House campaign, goes after stock trades"
source-2305     68fd869324415b04  "Bresnahan campaigned on congressional stock trade ban..."
source-4044     3741e342afa6ccef  "Cognetti accuses Bresnahan of 'public corruption'..."
source-6907     02818808304a08b3  "Rep. Rob Bresnahan Was a Prolific Stock Trader. Now He's Quit..."
```

5 clusters, 1 article each. They cover the same event from different outlets. The simhashes are completely different (different word shingles). Title Jaccard between any pair is well below 0.65 (different word choices).

But the **frame matcher** correctly puts all 5 into the same `"Bresnahan's Stock Trades"` narrative_frame via FCM. So we know at the SEMANTIC level they're the same story — the lexical clusterer just can't see it.

### Story 2: "Medicaid vote" splits into 10+ clusters

Pairs of critical-relevance articles in different clusters, both about Bresnahan's Medicaid vote:
- "Look who voted for Medicaid cuts when he campaigned on the opposite"
- "Bresnahan DOES have a stance on something, and it's not Medicare/Medicaid..."
- "DCCC: Bresnahan dumped stock before voting for historic Medicaid cuts"
- "Letter: Ads misportray Bresnahan Medicaid vote"
- "New ad calls out Bresnahan's decision to sell Medicaid stocks before gutting..."
- "Our Opinion: Bresnahan's budget vote breaks promises to avoid Medicaid cuts"
- "Rep. Rob Bresnahan sold stock in several Medicaid providers before voting for cuts"

These should be ONE cluster (or 2-3 max, given some are sub-angles like "stock dumping" vs "broken promise"). They're 10+ clusters.

### Story 3: "Maternity leave inconsistency" splits into 4 clusters

- "Democratic candidate in key Pennsylvania House race faces scrutiny over maternity..."
- "Center for Rural Pa. finds state's maternity health desert growing..."
- "Letter: Cognetti backs maternity leave nationally, not locally - Hazleton Standard..."
- "Letter: Cognetti backs maternity leave nationally, not locally - Scranton Times..."
- "Letter: Cognetti backs maternity leave nationally, not locally - Wilkes-Barre Citizens..."

The last 3 are LITERALLY THE SAME LETTER TO THE EDITOR published in 3 different papers. The titles are almost identical (`"Letter: Cognetti backs maternity leave nationally, not locally"`). Title Jaccard is 1.0. Why aren't they clustering?

Because the **suffix** "- Hazleton Standard-Speaker" / "- Scranton Times-Tribune" / "- Wilkes-Barre Citizens" gets stripped by `_SOURCE_SUFFIX = re.compile(r"\s+[-|:]\s+[^-|:]{2,60}$")` — but only ONCE. If the suffix doesn't strip, OR the regex doesn't fire identically across all titles, they slip past Rule 2 (title Jaccard 0.92+).

I'd need to actually run `normalize_title()` on these to confirm. If all 3 produce the exact same normalized text, then Rule 2 SHOULD fire. If they don't (e.g., different unicode characters in the dashes), it doesn't.

---

## Root cause analysis

### How clustering works today

In `services/story_clustering.py:assign_story_cluster_v2`:

```python
# Rule 1: URL canonical match → same cluster
# Rule 2: Title Jaccard ≥ 0.92 → same cluster
# Rule 3: Title Jaccard ≥ 0.65 AND SimHash Hamming ≤ 6 AND within 7 days → same cluster
# Rule 4: Create new cluster (fallback)
```

Defaults: `CLUSTER_TITLE_JACCARD_MIN=0.65`, `CLUSTER_SIMHASH_HAMMING_MAX=6`, `CLUSTER_WINDOW_DAYS=14`.

### Why all 3 rules fail on cross-outlet original journalism

- **Rule 1 (URL match):** Different outlets = different domains = different URLs. Never matches across outlets.
- **Rule 2 (Title Jaccard ≥ 0.92):** Outlets write different headlines. Even on the same wire story, headlines vary ("Cognetti slams..." vs "Mayor attacks..." vs "Democratic challenger calls..."). Almost never matches.
- **Rule 3 (Title Jaccard ≥ 0.65 + SimHash hamming ≤ 6):** Both conditions strict. SimHash hamming ≤ 6 (out of 64) is essentially "near-verbatim text" — fine for AP wire syndication but not for outlets writing original takes.

### What DOES cluster correctly

When clusters have size 5-15, they're typically:
- AP wire stories republished verbatim (same text, different URLs)
- Press releases distributed to multiple outlets
- Single-source GDELT BigQuery backfills with same publisher_domain

So lexical similarity catches "the same wire story across outlets" but misses "the same EVENT covered by different outlets."

---

## The fix

### Option A: Add a 4th rule using embedding similarity

Insert before the "create new" fallback:

```python
# Rule 4: Embedding cosine similarity ≥ 0.85 + published within 14 days
# (catches semantic dups when lexical rules fail — e.g., different outlets
#  paraphrasing the same story)
if not short_text:
    item_emb = embed_one(f"{item.title}\n\n{item.raw_text[:1500]}")
    if item_emb is not None:
        for cluster, rep in candidates:
            if not _published_close(item.published_at, rep.published_at, days=14):
                continue
            rep_emb = embed_one(f"{rep.title}\n\n{rep.raw_text[:1500]}")  # cached
            if rep_emb is not None and cosine_similarity(item_emb, rep_emb) >= 0.85:
                matched = cluster
                break
```

**Cost:** Adds an embedding call per ingested item (caches subsequent calls per cluster representative). At ~50 articles/day → 50 embedding calls/day. Trivial cost ($0.001/day even on OpenAI text-3-large).

**Risk:** Embedding similarity isn't perfectly calibrated for "same story." Could over-merge if threshold is too loose. The 0.85 threshold is conservative — calibrate by running on the 5 known-mis-clustered groups above.

**Effort:** ~3-4 hours implementation + 1-2 hours calibration.

### Option B: Cluster MERGE as a separate pass

Don't change ingestion-time clustering at all. Add a nightly job that:
1. Finds clusters where multiple cluster_ids link to the same `frame_cluster_match.frame_id`
2. For each frame, identifies clusters that should probably be merged (low simhash similarity but same frame + similar timeline)
3. Surfaces these to the user as "possible duplicate clusters" for manual confirmation
4. Optionally: auto-merges when confidence is very high

**Pros:** Doesn't touch the hot ingestion path. Easy to disable. Manual review keeps user in control.

**Cons:** Lags by 24h. Doesn't help with real-time analysis.

### Option C: Two-tier cluster model

Keep `story_clusters` as the lexical-dedup layer (current behavior). Add a `story_arcs` table that groups clusters by FRAME + timeline. UI shows arcs, not clusters.

**Pros:** Most architecturally clean. Doesn't change existing clustering semantics.

**Cons:** Bigger change — every UI query that aggregates by cluster would need to aggregate by arc instead. ~1-2 weeks of work.

### My recommendation

**Start with Option A.** Lowest risk, immediate impact. Calibrate the threshold against the known mis-clusterings.

If A works well, consider whether B (manual merge UI) is needed for the cases A misses.

C is over-engineering until A and B prove insufficient.

---

## Calibration plan for Option A

Test threshold against these 3 known mis-clustered groups (from above):

**Group 1: Stock trades** — should merge into 1 cluster
- source-396, 1307, 2305, 4044, 6907

**Group 2: Medicaid vote** — should merge into 1-3 clusters (some are sub-angles)
- source-1591, 1596, 2224, 3312, 4612, 3548, 3257, 2410, 4662, 2985, 3164

**Group 3: Maternity leave (letters)** — should merge into 1 cluster
- source-1350, 6461, 5382, 6459 + state context article (separate)

Pull embeddings for each article, compute all-pairs cosine similarity. Find the threshold that:
- Merges all 5 stock-trade articles together
- Merges the 3 identical "Letter" articles together
- Does NOT merge the maternity-leave-state-context article into the candidate-scrutiny cluster
- Does NOT merge unrelated articles in the corpus

Likely threshold lands at 0.82-0.88. Validate on 10 randomly-sampled non-mis-clusterings to confirm no false merges.

---

## Why this matters for the product

Two clear value-adds from fixing cluster size:

### 1. UI accuracy
The Dashboard says "Bresnahan's Stock Trades — 174 articles, 58 variants." That's via frame matching, which works. But if you drill into the cluster timeline, you see 174 individual clusters instead of ~20 meaningful story arcs. Hard to scan.

After fix: "Bresnahan's Stock Trades" expands to ~20 actual story moments (initial scoop, follow-up, NYT picks up, etc.) instead of 174 unrelated cluster blobs.

### 2. Better "outlets reporting" counts
Currently `outlet_count` is per-cluster. Each cluster has 1-2 outlets. A frame appears to span "many outlets" only because it has many clusters. The actual outlet-diversity-per-story-event is undercounted.

After fix: a single story event correctly shows "covered by 7 outlets" instead of "in 7 different clusters each with 1 outlet."

### 3. Analytical depth
Several brainstormed features (#3 Tone Trend, #5 Outlet Authority Lens, #11 Press-Kit View) presume that "one cluster = one story." Today they'd produce noisy outputs because the assumption is wrong.

---

## What I deliberately did NOT do

- Run the embedding-based cluster merge on the live DB (would have been a write, out of scope)
- Modify any clustering code (out of scope)
- Test calibration thresholds against real data (would need to make API calls)

If you want a follow-up session focused on this:
1. I'd run the cosine-similarity analysis on the 3 known mis-clusterings using the cache I built in Session A
2. Calibrate a threshold
3. Write a one-off "candidate merges" script that surfaces likely merges WITHOUT modifying the DB
4. You review the candidate merges, approve which to actually merge

This would be a ~3-hour focused session and produces a real product improvement (better cluster topology).
