"""Cluster the `candidate_frames` staging table into promotable suggestions.

After clustering, calls GPT-4o (full, not mini) ONCE to deduplicate the
results against existing active NarrativeFrames AND merge near-duplicate
clusters among themselves. Why GPT-4o full: the embeddings-based clustering
catches obvious lexical dups ("Bresnahan's vote against energy tax credits"
vs "Bresnahan's vote against renewable energy tax credits") but misses
semantic dups ("Bresnahan's Broken Promises" vs an existing
"Bresnahan's Tax Cuts Lack Benefits" when both center on the same vote).
gpt-4o-mini is unreliable on this kind of judgment; gpt-4o is roughly 95%
accurate, costs ~$0.05-0.10/daily-run, and runs once per cache refresh.


Background
----------
`CandidateFrame` rows are written during article scoring whenever the LLM
notices a recurring narrative that doesn't match any active frame
(rescore.py:137-144). Each row is a single suggested-name + evidence-quote
captured from one article.

The model docstring on `CandidateFrame` promised a "periodic auto-promotion
job [that] clusters semantically similar candidate frames; clusters with
enough cross-article and cross-outlet support get promoted into real
NarrativeFrames." That promoter never existed. This module is it.

Algorithm
---------
1. Read unresolved CandidateFrame rows from the last `days_back` days.
2. Embed each row's (suggested_name + evidence_quote) via Gemini
   SEMANTIC_SIMILARITY embeddings (already in services/embeddings.py).
3. Greedy single-link clustering on cosine similarity ≥ THRESHOLD.
   Volume here is low (low hundreds of rows), so O(n²) is fine — no need
   for HDBSCAN-style density clustering.
4. For each cluster, compute:
     - Source diversity: count of distinct source_item_ids (≥3 to promote)
     - Outlet diversity: count of distinct outlets across those source_items
     - A representative name (the most-frequent suggested_name)
     - A representative evidence quote (longest, after filtering refusals)
5. Return clusters meeting promotion thresholds. The CALLER decides
   whether to auto-promote or surface for human approval.

This is deliberately non-destructive: it doesn't write anything by itself.
Promotion happens via `promote_cluster(...)` which creates a NarrativeFrame
and marks the contributing CandidateFrame rows as resolved.

Cost
----
Zero LLM calls in the clustering pass (re-uses existing per-row data and
embeddings). Promotion itself doesn't call the LLM either — it just copies
the cluster's representative name/description into a new NarrativeFrame.
"""
from __future__ import annotations
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    CandidateFrame,
    NarrativeFrame,
    Outlet,
    SourceItem,
)
logger = logging.getLogger(__name__)

# embeddings module is optional — depends on `google-generativeai` package.
# If it's not installed (or GEMINI_API_KEY isn't configured), clustering
# can't run; we fall through to returning [] from find_promotable_clusters
# rather than raising ImportError on every endpoint call. This way a
# misconfigured environment doesn't 500 the whole route.
try:
    from app.services.embeddings import embed_texts, get_last_embed_stats
    _EMBEDDINGS_AVAILABLE = True
except Exception as _exc:
    logger.warning(
        "candidate_frame_promoter: embeddings module unavailable (%s) — "
        "clustering will return empty until the env is fixed.", _exc,
    )
    _EMBEDDINGS_AVAILABLE = False
    def embed_texts(texts, **_kwargs):  # type: ignore[no-redef]
        return [None] * len(texts)
    def get_last_embed_stats():  # type: ignore[no-redef]
        # Minimal shim so refresh_cache() can call .n_failed / .summary()
        # without an attribute error when embeddings module is missing.
        class _Stats:
            n_total = 0
            n_cached = 0
            n_gemini_ok = 0
            n_openai_ok = 0
            n_failed = 0
            gemini_quota_exhausted = True
            openai_attempted = False
            openai_available = False
            openai_error = "embeddings_module_unavailable"
            elapsed_seconds = 0.0
            def summary(self): return "embeddings_module_unavailable"
        return _Stats()


# Module-level cache for the latest clustering result. Computing clusters is
# slow (Gemini embeddings, ~5-30s for ~200 rows) and the answer doesn't change
# faster than new candidate_frame rows arrive. The daily scheduler job
# (_run_candidate_frame_promoter in scheduler.py) populates this cache; the
# UI endpoint reads from it. On a fresh process start the cache is empty —
# `get_cached_or_compute()` will do one live compute on first request.
_CACHE: dict = {
    "suggestions": None,   # list[dict] | None
    "computed_at": None,   # datetime | None
    "days_back": None,     # int | None
    "last_error": None,    # str | None — refresh_cache failure surface
}

# Promotion-gate thresholds. These are PRODUCT decisions, not similarity
# numbers — "a narrative needs N sources to be worth surfacing" is the
# same logic in any race. Tunable but not race-dependent.
MIN_CLUSTER_ROWS = 3          # at least N candidate_frame rows
MIN_DISTINCT_ARTICLES = 3     # at least N distinct source articles
MIN_DISTINCT_OUTLETS = 2      # at least N distinct outlets

# Frames with these suggested_name patterns are too generic to promote.
_GENERIC_NAME_PATTERNS = (
    "general election",
    "primary election",
    "campaign overview",
    "political news",
    "election news",
)


def _is_generic_name(name: str) -> bool:
    n = (name or "").lower().strip()
    return any(pat in n for pat in _GENERIC_NAME_PATTERNS)


def _build_clusters(
    candidates: list[CandidateFrame],
    embeddings: list[Optional[list[float]]],
    min_cluster_size: int = 3,
) -> list[list[int]]:
    """Density-based clustering via HDBSCAN. Returns list of clusters, each
    a list of indices into `candidates`.

    Why HDBSCAN (and why not a similarity threshold)
    ------------------------------------------------
    Earlier versions of this function used `cosine_similarity >= 0.85`
    (Gemini-calibrated) or `>= 0.65` (OpenAI-calibrated). Both approaches
    are RACE-DEPENDENT — the right threshold depends on:
      - how varied the LLM's suggested_name vocabulary is for the same
        underlying narrative (race-specific issues + candidate names)
      - how densely the political-content vector space is populated for
        the specific race (Cognetti vs. e.g. a state senate race)
      - which embedding provider is in use (Gemini and OpenAI have
        different cosine similarity distributions on identical text)

    HDBSCAN doesn't have a global similarity threshold. Clusters emerge
    from local density variation:
      - Tightly-clustered topics (wire-syndicated press releases) form
        dense small clusters
      - Loosely-related variants (different outlets' takes on one story)
        form less-dense larger clusters
      - True one-offs fall out as noise (label = -1) which we treat as
        singletons

    The only knob is `min_cluster_size` ("a narrative needs ≥N references
    to count"). That's a product/recall decision, not a similarity number,
    so it's race-agnostic.

    This mirrors the proven pattern in `services/frame_variants.py` for
    within-frame variant clustering. Same library, same parameters, same
    cluster_selection_method='leaf' (which avoids the EOM-method
    over-merging trap).

    Returns: list of cluster indices, ONE list per cluster. Each noise
    singleton becomes its own list-of-one — that way callers get the
    same data structure regardless of whether items clustered.
    """
    import numpy as np
    from hdbscan import HDBSCAN
    from app.services._numba_serialize import numba_lock

    # Filter to embedded items and remember their original indices.
    keep: list[tuple[int, list[float]]] = [
        (i, emb) for i, emb in enumerate(embeddings) if emb is not None
    ]
    if not keep:
        return []
    if len(keep) < min_cluster_size:
        # HDBSCAN refuses fewer than min_cluster_size items. Treat each as
        # a singleton so downstream gate logic gets to see them (it'll
        # reject them via MIN_CLUSTER_ROWS, just like before).
        return [[i] for i, _ in keep]

    indices = [i for i, _ in keep]
    embs = np.array([emb for _, emb in keep], dtype=np.float32)
    # L2-normalize so Euclidean distance is monotonic in cosine distance —
    # same trick frame_variants.py uses. Avoids HDBSCAN's slow precomputed-
    # cosine path while preserving similarity semantics.
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        # min_samples=1 gives the loosest noise-classification (more items
        # land in clusters vs noise). Tighten by raising if false-merges
        # show up in production — but at this scale, 1 keeps recall high
        # without harming precision (HDBSCAN's density model handles the
        # rest).
        min_samples=1,
        metric="euclidean",
        # 'leaf' produces finer-grained clusters — same choice as
        # frame_variants.py. 'eom' (excess-of-mass) over-merges; we
        # observed it producing a 15-row mega-cluster mixing literacy +
        # ethics + downtown + fiscal topics in real data.
        cluster_selection_method="leaf",
    )
    # Serialize against any other UMAP/HDBSCAN call. See _numba_serialize.py.
    with numba_lock:
        labels = clusterer.fit_predict(embs_norm)

    # Group items by cluster label. -1 = noise → each becomes its own
    # singleton so the gate logic (MIN_CLUSTER_ROWS) treats them uniformly.
    by_label: dict[int, list[int]] = {}
    noise_singletons: list[list[int]] = []
    for j, label in enumerate(labels):
        original_idx = indices[j]
        if label == -1:
            noise_singletons.append([original_idx])
        else:
            by_label.setdefault(int(label), []).append(original_idx)

    return list(by_label.values()) + noise_singletons


def find_promotable_clusters(
    db: Session,
    days_back: int = 21,
) -> list[dict]:
    """Identify clusters of candidate_frames that meet promotion thresholds.

    Returns a list of suggestion dicts:
        {
          "suggested_name": str,
          "suggested_description": str,
          "owner_type_hint": "candidate" | "opponent" | "media",
          "n_rows": int,           # raw candidate_frames count
          "n_articles": int,        # distinct source_item ids
          "n_outlets": int,         # distinct outlets
          "evidence_quotes": [...], # up to 5 verbatim quotes
          "candidate_frame_ids": [...],
          "first_seen": iso,
          "last_seen": iso,
        }

    No writes. Caller decides whether to promote.
    """
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    rows: list[CandidateFrame] = (
        db.query(CandidateFrame)
        .filter(
            CandidateFrame.resolved_to_frame_id.is_(None),
            CandidateFrame.created_at >= cutoff,
        )
        .all()
    )
    if not rows:
        logger.info("candidate_frame_promoter: no unresolved rows in last %dd", days_back)
        return []

    # Drop rows whose suggested_name is too generic to be a tracked frame.
    rows = [r for r in rows if not _is_generic_name(r.suggested_name)]
    if not rows:
        return []

    # Embed the (name + evidence) text for clustering.
    texts = [
        f"{r.suggested_name}. {(r.evidence_quote or '')[:300]}"
        for r in rows
    ]
    embeddings = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")

    # If embeddings broadly failed (quota exhaustion etc.), surface that to
    # the caller as an exception. Otherwise the empty cluster list looks
    # identical to "no narratives detected" — the silent-failure pattern
    # this whole rework was meant to kill.
    stats = get_last_embed_stats()
    if stats.n_total > 0:
        failure_rate = stats.n_failed / stats.n_total
        if failure_rate >= 0.5:
            # Build a user-readable diagnosis, not a stack trace.
            parts = []
            if stats.gemini_quota_exhausted:
                parts.append("Gemini quota exhausted")
            if stats.openai_error:
                parts.append(f"OpenAI fallback failed ({stats.openai_error[:80]})")
            elif not stats.openai_available:
                parts.append("OpenAI fallback unavailable (no OPENAI_API_KEY)")
            if not parts:
                parts.append("embedding providers returned no vectors")
            raise RuntimeError(
                f"embeddings unavailable: {stats.n_failed}/{stats.n_total} failed — "
                + "; ".join(parts)
            )

    clusters = _build_clusters(rows, embeddings)

    # Hydrate source_item / outlet info for diversity counts (one batch query).
    sid_to_outlet: dict[int, Optional[int]] = {}
    if rows:
        sids = [r.source_item_id for r in rows if r.source_item_id]
        if sids:
            for sid, oid in db.query(SourceItem.id, SourceItem.outlet_id).filter(
                SourceItem.id.in_(sids)
            ).all():
                sid_to_outlet[sid] = oid

    outlet_names: dict[int, str] = {}
    oids = {o for o in sid_to_outlet.values() if o}
    if oids:
        for oid, name in db.query(Outlet.id, Outlet.name).filter(
            Outlet.id.in_(oids)
        ).all():
            outlet_names[oid] = name

    # Subject classifier bound to current campaign — used to label each
    # suggestion with a quadrant on the frontend (owner × subject).
    from app.services.subject_classifier import get_subject_classifier
    _classify_subject = get_subject_classifier(db)

    suggestions: list[dict] = []
    for cluster_indices in clusters:
        members = [rows[i] for i in cluster_indices]
        if len(members) < MIN_CLUSTER_ROWS:
            continue

        article_ids = {m.source_item_id for m in members if m.source_item_id}
        outlets = {sid_to_outlet.get(sid) for sid in article_ids
                   if sid_to_outlet.get(sid)}
        if len(article_ids) < MIN_DISTINCT_ARTICLES:
            continue
        if len(outlets) < MIN_DISTINCT_OUTLETS:
            continue

        # Representative name: most-frequent suggested_name in the cluster,
        # title-cased for display. Ties broken by length (longer = more
        # specific).
        name_counter = Counter(m.suggested_name for m in members)
        max_count = max(name_counter.values())
        top_names = [n for n, c in name_counter.items() if c == max_count]
        rep_name = max(top_names, key=len)

        # Owner-type hint: the modal owner_type_hint across cluster members.
        owner_counter = Counter(m.owner_type_hint or "media" for m in members)
        owner_hint = owner_counter.most_common(1)[0][0]

        # Up to 5 evidence quotes, longest first (= most specific).
        quotes = sorted(
            {(m.evidence_quote or "").strip() for m in members if m.evidence_quote},
            key=len,
            reverse=True,
        )[:5]

        # Reasoning samples for a description seed — pick the first non-empty.
        reasoning = next(
            (m.reasoning for m in members if m.reasoning), ""
        )
        description = reasoning or (quotes[0] if quotes else "")

        first_seen = min((m.created_at for m in members if m.created_at),
                         default=None)
        last_seen = max((m.created_at for m in members if m.created_at),
                        default=None)

        suggestions.append({
            "suggested_name": rep_name,
            "suggested_description": description[:280],
            "owner_type_hint": owner_hint,
            "subject_type_hint": _classify_subject(rep_name),
            "n_rows": len(members),
            "n_articles": len(article_ids),
            "n_outlets": len(outlets),
            "outlet_names": sorted(outlet_names.get(o, "") for o in outlets if outlet_names.get(o)),
            "evidence_quotes": quotes,
            "candidate_frame_ids": [m.id for m in members],
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
        })

    # Sort by signal strength: distinct articles first, then row count.
    suggestions.sort(key=lambda s: (s["n_articles"], s["n_rows"]), reverse=True)
    logger.info(
        "candidate_frame_promoter: %d clusters meet promotion thresholds "
        "(MIN_ROWS=%d, MIN_ARTICLES=%d, MIN_OUTLETS=%d, HDBSCAN min_cluster_size=3)",
        len(suggestions), MIN_CLUSTER_ROWS, MIN_DISTINCT_ARTICLES,
        MIN_DISTINCT_OUTLETS,
    )
    return suggestions


_DEDUP_MODEL = "gpt-4o"  # NOT gpt-4o-mini — judgment task, accuracy > cost.


def _gpt4o_dedup(
    suggestions: list[dict],
    existing_frames: list[NarrativeFrame],
) -> list[dict]:
    """One GPT-4o call to (a) drop suggestions that duplicate existing
    frames, and (b) merge near-duplicate suggestions into each other.

    Returns the cleaned suggestion list. On any LLM error, returns the
    input unchanged — dedup is a nice-to-have, not a correctness gate.
    """
    import os
    import json
    import re

    if not suggestions or len(suggestions) < 2 and not existing_frames:
        return suggestions

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("_gpt4o_dedup: OPENAI_API_KEY not set, skipping dedup")
        return suggestions

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("_gpt4o_dedup: openai package not installed, skipping")
        return suggestions

    frames_block = "\n".join(
        f"  F{f.id}: \"{f.name}\" — {(f.description or '')[:120]}"
        for f in existing_frames
    ) or "  (none)"

    clusters_block = "\n".join(
        f"  C{i}: \"{s['suggested_name']}\" — quote: \"{(s['evidence_quotes'][0] if s['evidence_quotes'] else '')[:160]}\""
        for i, s in enumerate(suggestions)
    )

    prompt = f"""You are de-duplicating proposed campaign narrative frames before they are surfaced to a human reviewer.

EXISTING TRACKED FRAMES:
{frames_block}

PROPOSED NEW CLUSTERS:
{clusters_block}

For each proposed cluster, decide:
  - duplicate_of_existing: the cluster covers the SAME specific claim as an existing frame above (return the F-id of that frame)
  - duplicate_of_cluster: the cluster covers the SAME specific claim as ANOTHER cluster in the proposed list (return the lower-index C-id of the canonical cluster)
  - genuinely_new: the cluster is a meaningfully distinct narrative not already tracked or duplicated

Two narratives are duplicates ONLY when an article that supports one would always support the other. "Bresnahan voted against ACA" and "Bresnahan's healthcare record" are DIFFERENT (one is a specific vote, one is a broader pattern). "Bresnahan's vote against energy tax credits" and "Bresnahan's vote against renewable energy tax credits" ARE the same.

Return ONLY a JSON array, one entry per cluster IN ORDER (so the array length equals the number of proposed clusters). Each entry: {{"cluster_id": <C-index>, "decision": "duplicate_of_existing"|"duplicate_of_cluster"|"genuinely_new", "target": <F-id or C-index or null>}}"""

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=_DEDUP_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=800,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as exc:
        logger.warning("_gpt4o_dedup: LLM call failed: %s", exc)
        return suggestions

    # Strip code fences if present, then locate the JSON array.
    text = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        logger.warning("_gpt4o_dedup: no JSON array in response, skipping")
        return suggestions
    try:
        decisions = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("_gpt4o_dedup: JSON parse failed: %s", exc)
        return suggestions

    if not isinstance(decisions, list):
        return suggestions

    # Apply decisions. We DROP duplicates_of_existing entirely. For
    # duplicates_of_cluster, we merge member candidate_frame_ids into the
    # target's list and drop the merged-from. Anything genuinely_new
    # passes through.
    drop_indices: set[int] = set()
    merge_targets: dict[int, list[int]] = {}  # target -> [merged-from indices]

    for d in decisions:
        if not isinstance(d, dict):
            continue
        cid = d.get("cluster_id")
        dec = d.get("decision")
        tgt = d.get("target")
        if not isinstance(cid, int) or cid < 0 or cid >= len(suggestions):
            continue
        if dec == "duplicate_of_existing":
            drop_indices.add(cid)
        elif dec == "duplicate_of_cluster" and isinstance(tgt, int):
            if 0 <= tgt < len(suggestions) and tgt != cid:
                merge_targets.setdefault(tgt, []).append(cid)
                drop_indices.add(cid)

    # Perform merges: combine candidate_frame_ids + evidence_quotes into target.
    for target, sources in merge_targets.items():
        for src in sources:
            suggestions[target]["candidate_frame_ids"] = list(set(
                suggestions[target]["candidate_frame_ids"]
                + suggestions[src]["candidate_frame_ids"]
            ))
            # Take a few extra evidence quotes from the merged-in cluster
            extra = suggestions[src]["evidence_quotes"][:2]
            existing_q = set(suggestions[target]["evidence_quotes"])
            for q in extra:
                if q not in existing_q:
                    suggestions[target]["evidence_quotes"].append(q)
                    existing_q.add(q)
        # Refresh stats after the merge.
        suggestions[target]["n_rows"] = len(suggestions[target]["candidate_frame_ids"])

    cleaned = [s for i, s in enumerate(suggestions) if i not in drop_indices]
    logger.info(
        "_gpt4o_dedup: %d in → %d out (%d dup_existing, %d merged_into_other)",
        len(suggestions), len(cleaned),
        sum(1 for d in decisions if isinstance(d, dict) and d.get("decision") == "duplicate_of_existing"),
        sum(1 for d in decisions if isinstance(d, dict) and d.get("decision") == "duplicate_of_cluster"),
    )
    return cleaned


def refresh_cache(db: Session, days_back: int = 21) -> int:
    """Compute clusters, dedup against existing frames via GPT-4o, and store
    the cleaned result in the module-level cache. Returns final count.

    Exception-protected at the top level. If any step raises (Gemini
    embedding quota, GPT-4o auth blip, DB hiccup), we PRESERVE the prior
    cached suggestions and update `computed_at` to NOW. Otherwise a single
    failure would mark the cache as fresh-but-empty, hiding stale-but-
    useful data, OR (worse) get_cached_suggestions's staleness check would
    re-fire the live compute on every UI hit, hammering the failing path
    in a loop. `last_error` lets the observability endpoint surface the
    failure mode to the user.
    """
    try:
        raw = find_promotable_clusters(db, days_back=days_back)
        if raw:
            existing = (
                db.query(NarrativeFrame)
                .filter(NarrativeFrame.active == True)  # noqa: E712
                .all()
            )
            cleaned = _gpt4o_dedup(raw, existing)
        else:
            cleaned = []
        _CACHE["suggestions"] = cleaned
        _CACHE["computed_at"] = datetime.utcnow()
        _CACHE["days_back"] = days_back
        _CACHE["last_error"] = None
        return len(cleaned)
    except Exception as exc:
        # Preserve the prior `suggestions` but mark computed_at NOW so the
        # staleness check doesn't immediately re-fire on every UI hit.
        # Surface the error for diagnostics.
        logger.exception("refresh_cache: failed (preserving stale cache)")
        _CACHE["computed_at"] = datetime.utcnow()
        _CACHE["last_error"] = f"{type(exc).__name__}: {exc}"
        prior = _CACHE.get("suggestions") or []
        return len(prior)


def get_cached_suggestions(
    db: Session,
    days_back: int = 21,
    max_age_hours: int = 25,
) -> tuple[list[dict], Optional[datetime], bool]:
    """Return cached suggestions if fresh, else compute + cache + return.

    Returns (suggestions, computed_at, was_live_computed).
    - If cache is None: compute live (and cache).
    - If cache is stale (> max_age_hours): compute live (and cache).
    - Otherwise return cached.

    The UI endpoint should call this. The scheduled job calls `refresh_cache`
    directly to avoid the staleness check.
    """
    cached = _CACHE["suggestions"]
    computed_at = _CACHE["computed_at"]
    cached_days = _CACHE["days_back"]

    if (
        cached is None
        or computed_at is None
        or cached_days != days_back
        or (datetime.utcnow() - computed_at).total_seconds() > max_age_hours * 3600
    ):
        refresh_cache(db, days_back=days_back)
        return _CACHE["suggestions"] or [], _CACHE["computed_at"], True
    return cached, computed_at, False


def _backfill_evidence_for_promoted_frame(
    db: Session,
    *,
    frame_id: int,
    candidate_frame_ids: list[int],
    ts: datetime,
) -> dict[str, int]:
    """Write FrameClusterMatch + NarrativeFrameMention rows so the just-
    promoted frame has visible activity immediately.

    For each candidate_frame, we follow source_item_id → source_item to
    pull (story_cluster_id, published_at, evidence_quote). We then write:
      - FCM row keyed (frame_id, story_cluster_id) — drives the dashboard
        card metrics
      - NFM row keyed (frame_id, source_item_id) — drives variant
        clustering + detail-page quotes

    Confidence is set to 85 ("high"). The promoter only fires when an
    LLM-judged dedup pass has accepted these suggestions as a real frame
    — they're not borderline matches. We intentionally do NOT mark them
    confidence=95 ("very high") which is reserved for the runtime matcher's
    direct quote-verified matches.

    `matched_by="promoted_from_candidate"` makes the provenance traceable
    in case we ever need to roll these back.

    Both writes are best-effort: a missing source_item or duplicate-key
    error is logged and skipped, not raised — promotion must not fail
    because one candidate_frame had a deleted article.

    Returns counts {"fcm_rows": N, "nfm_rows": M} for logging.
    """
    from app.models import SourceItem
    from app.services.cluster_writes import upsert_frame_match

    # Pull source_item + candidate_frame info in one query each.
    candidates = (
        db.query(CandidateFrame)
        .filter(CandidateFrame.id.in_(candidate_frame_ids))
        .all()
    )
    source_item_ids = [c.source_item_id for c in candidates if c.source_item_id]
    items_by_id: dict[int, SourceItem] = {}
    if source_item_ids:
        for si in db.query(SourceItem).filter(SourceItem.id.in_(source_item_ids)).all():
            items_by_id[si.id] = si

    # First the FCM rows (one per distinct story_cluster_id).
    fcm_clusters_done: set[str] = set()
    fcm_rows = 0
    for c in candidates:
        if not c.source_item_id:
            continue
        item = items_by_id.get(c.source_item_id)
        if not item or not item.story_cluster_id:
            logger.debug(
                "_backfill: cf%d → no source_item or no cluster_id, skipping FCM",
                c.id,
            )
            continue
        if item.story_cluster_id in fcm_clusters_done:
            continue  # already wrote this cluster's FCM
        try:
            upsert_frame_match(
                db,
                frame_id=frame_id,
                cluster_id=item.story_cluster_id,
                confidence=85,
                source_type="promoted_from_candidate",
                matched_by="promoted_from_candidate",
                representative_snapshot_ts=ts,
                article_date=item.published_at,
            )
            fcm_clusters_done.add(item.story_cluster_id)
            fcm_rows += 1
        except Exception as exc:
            logger.warning(
                "_backfill: FCM write failed for cf%d (cluster %s): %s",
                c.id, item.story_cluster_id, exc,
            )

    # Now NFM rows (one per distinct source_item_id). NFM has a unique
    # constraint on (frame_id, source_item_id) — use ON CONFLICT DO NOTHING
    # so a duplicate doesn't bring down the whole transaction (which would
    # roll back the newly-flushed NarrativeFrame too — the frame would
    # disappear from the session and the caller's commit would no-op).
    #
    # Most common dup cause: the runtime matcher already linked this
    # article to this frame in the seconds-to-hours between AI suggestion
    # and human promotion.
    from sqlalchemy import text as _sql_text
    nfm_rows = 0
    nfm_source_ids_done: set[int] = set()
    for c in candidates:
        if not c.source_item_id or c.source_item_id in nfm_source_ids_done:
            continue
        item = items_by_id.get(c.source_item_id)
        if not item:
            continue
        try:
            result = db.execute(
                _sql_text(
                    """
                    INSERT INTO narrative_frame_mentions
                      (frame_id, source_item_id, confidence, matched_by,
                       created_at, extracted_text)
                    VALUES
                      (:frame_id, :source_item_id, :confidence, :matched_by,
                       :created_at, :extracted_text)
                    ON CONFLICT (frame_id, source_item_id) DO NOTHING
                    """
                ),
                {
                    "frame_id": frame_id,
                    "source_item_id": c.source_item_id,
                    "confidence": 85,
                    "matched_by": "promoted_from_candidate",
                    "created_at": ts.isoformat(sep=" "),
                    "extracted_text": (c.evidence_quote or None),
                },
            )
            if result.rowcount and result.rowcount > 0:
                nfm_rows += 1
            nfm_source_ids_done.add(c.source_item_id)
        except Exception as exc:
            logger.warning(
                "_backfill: NFM write failed for cf%d (frame=%d, source=%d): %s",
                c.id, frame_id, c.source_item_id, exc,
            )

    return {"fcm_rows": fcm_rows, "nfm_rows": nfm_rows}


def promote_cluster(
    db: Session,
    *,
    suggested_name: str,
    suggested_description: str,
    owner_type: str,
    candidate_frame_ids: list[int],
    subject_type: str | None = None,
) -> NarrativeFrame:
    """Create a new NarrativeFrame and mark contributing candidate_frames
    as resolved. Returns the created frame.

    Raises ValueError if owner_type isn't one of the valid values.
    """
    if owner_type not in ("candidate", "opponent", "media"):
        raise ValueError(f"owner_type must be candidate/opponent/media, got {owner_type!r}")
    if subject_type is not None and subject_type not in ("candidate", "opponent", "media"):
        raise ValueError(f"subject_type must be candidate/opponent/media or None, got {subject_type!r}")

    # Defense-in-depth: even if a wrong owner_type slipped past rescore.py's
    # heuristic AND past the user's edit-before-promote step, catch it here
    # before we commit it to NarrativeFrame. The heuristic only flips
    # unambiguous attack patterns, so this won't second-guess legitimate
    # user choices.
    try:
        from app.services.owner_type_correction import correct_owner_inversion
        from app.services.narrative_frames import _campaign_context as _ctx_fn
        _ctx = _ctx_fn(db)
        corrected, reason = correct_owner_inversion(
            suggested_name=suggested_name,
            proposed_owner_type=owner_type,
            candidate_name=_ctx.get("candidate") or "",
            opponent_names=_ctx.get("opponents") or [],
        )
        if reason:
            logger.info(
                "promote_cluster: corrected owner_type for %r: %s → %s — %s",
                suggested_name, owner_type, corrected, reason,
            )
            owner_type = corrected
    except Exception as _exc:
        logger.debug("promote_cluster: owner-correction skipped: %s", _exc)

    now = datetime.utcnow()
    frame = NarrativeFrame(
        name=suggested_name.strip(),
        description=suggested_description.strip() or None,
        owner_type=owner_type,
        subject_type=subject_type,
        active=True,
        source="llm",  # auto-discovered, not human-entered
        created_at=now,
        updated_at=now,
    )
    db.add(frame)
    db.flush()  # need frame.id

    # Materialize the AI's evidence as real FCM + NFM rows so the new frame
    # has activity from minute one.
    #
    # WHY: each candidate_frame row points at the source_item that triggered
    # the LLM's suggestion. Before this step, promote_cluster() created the
    # NarrativeFrame but left those connections implicit — the new frame
    # showed up on the dashboard with 0 articles / 0 outlets / 0 reach until
    # the periodic rematch job (~hours later) discovered the connection.
    # The user-reported symptom: "I just promoted this frame but the card
    # shows no activity — but the AI proposed it, so there must be activity?"
    # Yes — and now we wire that activity through immediately.
    if candidate_frame_ids:
        backfill_stats = _backfill_evidence_for_promoted_frame(
            db, frame_id=frame.id, candidate_frame_ids=candidate_frame_ids, ts=now,
        )
        logger.info(
            "candidate_frame_promoter: backfilled %d FCM, %d NFM rows for frame %d",
            backfill_stats["fcm_rows"], backfill_stats["nfm_rows"], frame.id,
        )

    # Mark contributing candidate_frames as resolved (after backfill so the
    # source_item_id is still trivially reachable through the candidate row).
    if candidate_frame_ids:
        (
            db.query(CandidateFrame)
            .filter(CandidateFrame.id.in_(candidate_frame_ids))
            .update(
                {
                    CandidateFrame.resolved_to_frame_id: frame.id,
                    CandidateFrame.resolved_at: now,
                },
                synchronize_session=False,
            )
        )

    db.commit()
    logger.info(
        "candidate_frame_promoter: promoted '%s' (frame_id=%d) from "
        "%d candidate_frames",
        frame.name, frame.id, len(candidate_frame_ids),
    )

    # Surgical cache update: remove the just-promoted cluster from the
    # cached suggestions so it doesn't reappear on page reload. Previously
    # the cache held the cluster for up to 25h post-promote — user would
    # see the same suggestion bounce back after a refresh, with no way to
    # know it had already been promoted.
    # Match the cluster by overlapping candidate_frame_ids (each cluster
    # has a unique set; any overlap is the same cluster).
    cached = _CACHE.get("suggestions") or []
    promoted_id_set = set(candidate_frame_ids)
    new_cached = [
        c for c in cached
        if not (set(c.get("candidate_frame_ids", [])) & promoted_id_set)
    ]
    if len(new_cached) != len(cached):
        _CACHE["suggestions"] = new_cached
        logger.info(
            "candidate_frame_promoter: removed promoted cluster from cache "
            "(was %d, now %d)", len(cached), len(new_cached),
        )

    return frame
