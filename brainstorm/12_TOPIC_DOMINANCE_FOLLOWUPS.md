# Topic Dominance — follow-ups to revisit

Context: in V13.x we replaced the narrative-count `owner_mix` (which
colored topic rings purely by how many tracked frames each side had
in the region) with an authority-weighted article volume model:

```
contribution(outlet, frame) = min(article_count, 9) × COALESCE(authority_score, 5)
owner_mix[side] = sum over member frames of (contribution per frame)
```

Constants live in `backend/app/services/topic_regions.py`:
- `ARTICLE_CAP_PER_OUTLET = 9`  — p95 of the empirical per-outlet,
  per-frame article-count distribution at the time of writing.
- `DEFAULT_AUTHORITY_SCORE = 5` — midpoint of the 1-10 scale, used
  for outlet-less SourceItems and as the floor for frames with zero
  matched articles.

This is a meaningful improvement over both the old narrative-count
formula AND the system-wide `monthly_visitors × 0.003` reach formula
(whose 0.003 constant is wrong for any non-traditional-newsroom
outlet — the Facebook case where 100M monthly visitors does NOT
mean 300k readers per post).

But the new model is still ultimately built on editorial judgment
(`authority_score` is a 1-10 number set per outlet by a human or
an LLM at ingestion time). The remainder of this doc tracks the
ideas we want to revisit when we're ready to tighten the model.

---

## Tier 1 — easy wins (low effort, real signal improvement)

### Make the cap data-driven at compute time
Replace the hardcoded `ARTICLE_CAP_PER_OUTLET = 9` with a function
that recomputes p95 from the current corpus each time topic regions
are computed:

```python
def _current_p95_cap(db: Session, default: int = 9) -> int:
    rows = (
        db.query(func.count(SourceItem.id).label("n"))
        .join(FrameClusterMatch, FrameClusterMatch.story_cluster_id == SourceItem.story_cluster_id)
        .filter(SourceItem.archived_as_irrelevant == False)
        .group_by(FrameClusterMatch.frame_id, SourceItem.outlet_id)
        .all()
    )
    if not rows:
        return default
    counts = sorted(r.n for r in rows)
    idx = int(len(counts) * 0.95)
    return max(default, counts[min(idx, len(counts) - 1)])
```

Cost: ~30 min. Removes "today's snapshot" calcification — the cap
stays at the 95th percentile as the corpus grows. The `max(...)`
guard prevents it from ever going BELOW the original empirical cap
of 9, so we don't accidentally over-suppress in a sparse-data state.

### Surface outlet authority in Setup with a sanity-check
Show all outlets sorted by `authority_score`. If something looks
mis-rated (e.g. a major paper at 2, or an obscure blog at 8), the
user can fix it inline. Probably already exists — worth confirming
on the Setup page.

---

## Tier 2 — mid-effort, better signal

### District-voter exposure proxy
For a PA-08 congressional race, an outlet's value isn't its raw
`authority_score` — it's "what fraction of its readers are in
PA-08." A Scranton paper at authority 6 reaching mostly PA-08
voters arguably matters MORE than NYT at authority 9 reaching
mostly New Yorkers.

Implementation:
- `Outlet.state` and `Outlet.districts` columns already exist.
- Boost in-district outlets by, say, ×1.5 to ×2.
- Penalize national outlets to ~0.7× when the race is local.

Tradeoff: introduces another tunable constant. Need to A/B against
the unweighted version to know if it helps the user's perceived
"which side is winning here" signal.

### Citation / repost graph
When local outlets republish AP/Reuters content, the original
outlet's score should propagate to all carriers. Build a per-story
citation graph (URL similarity, headline similarity, time-window
heuristics) and use PageRank-style influence propagation to assign
authority dynamically rather than relying on per-outlet editorial
ratings.

Major lift. But it's the only way to eliminate the "someone gave
this outlet a number" problem entirely — authority becomes derived
from observed behavior, not asserted.

---

## Tier 3 — hard, true signal (only if Tier 1+2 still feel arbitrary)

### Empirical influence / agenda-setting scoring
Track when an outlet's story gets picked up by other outlets within
N days. Outlets whose framings spread = high agenda-setting authority.
Replaces ordinal `authority_score` with a continuous, ground-truth
signal derived from actual media behavior.

This is the gold standard. Requires:
- Cross-outlet content matching (title/lede similarity).
- A time-decayed propagation model.
- Re-scoring all outlets periodically (weekly?).
- Some way to handle cold-start outlets (no propagation history yet).

Plausible but a multi-week build. Defer until the current model
clearly hits a ceiling that we can articulate concretely.

### Cap by recency
Wire syndication tends to happen in bursts. Cap could be tighter
for articles within a 24h window from the same outlet — filters
the AP→100-local-papers echo without suppressing genuine sustained
coverage.

Implementation: per-outlet article count for a topic could be
"distinct calendar days with coverage" instead of "raw article
count," with the cap applied at the day level. Cheap modification
once the basic model is stable.

---

## Tier 4 — probably overkill, but on the table

### Bayesian smoothing toward outlet-type average
Newer outlets with unstable counts (a blog that posted 30 articles
last week but nothing for 6 months) get pulled toward their tier
average until they have enough data. Limits the impact of an outlet's
sudden flood of coverage. Honestly, the existing per-outlet article
cap already does most of this work — Bayesian smoothing would only
matter if we removed the cap.

### Article-level reach (vs outlet-level reach)
Some articles obviously go viral (front-page treatment, social-media
amplification) while others are buried. If we ever ingest engagement
data (shares, comments, etc.), we could weight per-article reach
independently of outlet authority. Currently we don't ingest any
engagement signal, so this is moot.

---

## Broader follow-ups exposed by this work

### Reach formula app-wide
The `monthly_visitors × 0.003` formula has the same flaws everywhere
it's used (narrative pulse, spike detection, weekly weighted counts).
Topic dominance now uses a different, better formula. There's a
question of whether to swap the system-wide formula too, or keep
two different reach concepts in the system.

If we go to "swap everywhere," the change ripples through:
- `narrative_frames.py:1500+` (get_frames_with_counts)
- spike detection in analytics
- any other place using `reach_weight`

Best done as a deliberate "reach formula audit" task — propose the
new formula, list every call site, A/B the outputs against the
existing user-visible numbers, decide together what changes.

### Topic edge cases worth watching
- **Single-narrative topics**: now color by the dominant outlet
  rather than the single narrative's owner_type. If a candidate-
  owned narrative is mostly covered by media-tier outlets (e.g.
  AP), the topic color might land on "media" even though the
  underlying narrative is candidate-owned. Watch for this; might
  want to floor the narrative's owner_type contribution at a
  minimum.
- **Zero-article topics**: baseline of 5 per frame keeps them from
  vanishing. But if EVERY frame in a topic has zero articles, the
  topic colors uniformly by whatever owner_type has the most
  frames (back to narrative count by accident). Probably fine
  since this state is rare and short-lived.

---

## When to revisit

Trigger candidates:
- User looks at the chart and says "this topic should clearly be
  X but it's Y." (Current signal threshold.)
- A topic flips color rapidly between recomputes (cap is too low
  or too high).
- We add a major new ingestion source (e.g. TV broadcast monitors)
  with very different audience scale than newspapers — the existing
  authority scores may not generalize.
- The corpus grows past ~5000 articles per active race and the p95
  cap drifts substantially (>50% change from current 9).
