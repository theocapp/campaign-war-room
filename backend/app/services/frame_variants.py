"""
Frame variant clustering — production.

For each narrative frame with enough mentions, cluster the extracted_text
quotes into variants and assign each NarrativeFrameMention to a variant.

Variant = a specific phrasing/argument of the broader frame. A frame about
"Bresnahan's Healthcare Record" might have variants like:
  - "Bresnahan voted against ACA expansion"
  - "Bresnahan blocked Medicaid for seniors"
  - "Bresnahan killed healthcare"

This is what powers "show me how the messaging is evolving" — variants are
the unit of language change tracking.

Strategy: full re-cluster on each run.
  1. Wipe existing FrameVariant rows for the frame (and NULL NFM.variant_id)
  2. Embed all NFM quotes via Gemini (cache in NFM.quote_embedding)
  3. Cluster by incremental cosine similarity (centroid match → assign or new)
  4. Name each cluster via Groq llama-3.3-70b-versatile
  5. Persist FrameVariant rows + assign NFM.variant_id

Idempotent. Safe to re-run. Future enhancement: incremental re-cluster that
preserves stable variant identity (don't rename a variant if it survives
the new clustering).
"""
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Knobs ─────────────────────────────────────────────────────────────────────
DEFAULT_SIMILARITY_THRESHOLD = 0.88  # cosine sim for "same variant"
MIN_FRAME_QUOTES_TO_CLUSTER = 3      # frames with fewer quotes skipped
EMBED_BATCH_SIZE = 100


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cosine(a, b) -> float:
    """Cosine similarity. Local copy to avoid embeddings module dependency
    when called in tight loops."""
    if not a or not b:
        return 0.0
    import math
    s = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        s += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return s / (math.sqrt(na) * math.sqrt(nb))


def _hdbscan_cluster(
    items: list[dict], min_cluster_size: int = 2,
) -> list[list[dict]]:
    """Density-based clustering with HDBSCAN.

    Why HDBSCAN: no global similarity threshold. Clusters emerge from local
    density, so wire-syndicated near-duplicates cluster tightly (high
    density), genuine variants cluster more loosely, and one-off quotes
    fall out as noise singletons. The only knob (`min_cluster_size`) is a
    domain choice — "a variant must recur in at least N quotes" — not a
    similarity number.

    Implementation note: HDBSCAN's `cosine` metric requires precomputing
    a distance matrix. Faster alternative: L2-normalize embeddings, then
    Euclidean distance ≈ cosine distance up to a monotonic transformation.
    """
    import numpy as np
    from hdbscan import HDBSCAN

    items_with_emb = [it for it in items if it.get("embedding")]
    if len(items_with_emb) < min_cluster_size:
        return [[it] for it in items_with_emb]

    embs = np.array([it["embedding"] for it in items_with_emb], dtype=np.float32)
    # L2-normalize so Euclidean distance is monotonic in cosine distance.
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    clusterer = HDBSCAN(
        min_cluster_size=min_cluster_size,
        # min_samples defaults to min_cluster_size — controls how conservative
        # the noise classification is. Lower means more cluster assignments.
        min_samples=1,
        metric="euclidean",
        cluster_selection_method="leaf",  # 'leaf' produces finer-grained clusters
                                          # vs 'eom' (excess of mass) which over-merges
    )
    labels = clusterer.fit_predict(embs_norm)

    # Group items by cluster label. -1 = noise → each becomes its own singleton.
    by_label: dict = {}
    noise_singletons: list[list[dict]] = []
    for idx, label in enumerate(labels):
        if label == -1:
            noise_singletons.append([items_with_emb[idx]])
        else:
            by_label.setdefault(int(label), []).append(items_with_emb[idx])

    return list(by_label.values()) + noise_singletons


def _llm_group_quotes(
    items: list[dict], frame_name: str, batch_size: int = 40,
) -> list[list[dict]]:
    """LLM-as-judge clustering. Asks Groq 70B to group quotes by underlying
    claim. No similarity threshold — the LLM makes the call.

    For frames with > batch_size quotes, splits into batches by embedding
    similarity (so related quotes go in the same batch), then runs the LLM
    on each batch, then does a final cross-batch merge pass.

    Returns list of clusters, each being a list of items.
    """
    import os, json as _json
    from openai import OpenAI

    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        logger.warning("variant clustering: no GROQ_API_KEY; each quote becomes a singleton")
        return [[it] for it in items]
    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

    # Step 1: chunk items into batches the LLM can handle in one call.
    # For small frames, one batch. For large frames, group by embedding
    # similarity so related quotes batch together (better LLM grouping context).
    if len(items) <= batch_size:
        batches = [items]
    else:
        # Order by embedding similarity to first item (cheap proxy for "related").
        # Within a batch, the LLM sees similar-ish quotes and can group accurately.
        # Cross-batch merging handles cases where a variant spans batches.
        sorted_items = sorted(
            items,
            key=lambda it: -_cosine(it.get("embedding") or [], items[0].get("embedding") or []),
        )
        batches = [
            sorted_items[i:i + batch_size]
            for i in range(0, len(sorted_items), batch_size)
        ]

    # Step 2: ask the LLM to group each batch.
    def _group_batch(batch: list[dict]) -> list[list[dict]]:
        quotes_block = "\n".join(
            f"{i + 1}. \"{(it['nfm'].extracted_text or '').strip()[:300]}\""
            for i, it in enumerate(batch)
        )
        prompt = f"""You are analyzing quotes extracted from political news articles about a campaign in PA-08 (Cognetti vs Bresnahan). All quotes below relate to the narrative frame: "{frame_name}".

A VARIANT is a specific PHRASING or argument within a frame. Different ways of making the SAME specific claim should be grouped together. Different specific claims (even within the same frame) should be kept separate.

Examples:
  Group together (same variant — same specific claim, different wording):
    - "Bresnahan voted against ACA expansion"
    - "Bresnahan opposed expanding the Affordable Care Act"
    - "Bresnahan blocked the ACA bill"

  Keep SEPARATE (different specific claims):
    Variant A: "Bresnahan voted against ACA expansion"  (about a specific vote)
    Variant B: "Bresnahan accepted pharma donations"   (about funding)
    Variant C: "Bresnahan killed Medicaid"              (about a different policy)

QUOTES TO GROUP:
{quotes_block}

Return a JSON object with the groupings. Quote numbers are 1-indexed.

{{
  "groups": [
    {{"quote_indices": [1, 5, 12]}},
    {{"quote_indices": [2]}},
    {{"quote_indices": [3, 8]}}
  ]
}}

Rules:
- Be conservative — only group quotes making the SAME SPECIFIC claim. When unsure, keep separate.
- Singleton groups (size 1) are fine and expected. Many quotes will be their own variant.
- Every quote number must appear in exactly one group.
- Order doesn't matter."""

        try:
            r = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=2000,
            )
            data = _json.loads(r.choices[0].message.content)
            groups_raw = data.get("groups", [])
            # Validate + build clusters
            seen = set()
            clusters: list[list[dict]] = []
            for g in groups_raw:
                indices = g.get("quote_indices") or []
                cluster = []
                for idx in indices:
                    if not isinstance(idx, int):
                        continue
                    if idx < 1 or idx > len(batch):
                        continue
                    if idx in seen:
                        continue
                    seen.add(idx)
                    cluster.append(batch[idx - 1])
                if cluster:
                    clusters.append(cluster)
            # Any quotes the LLM didn't assign get individual singletons
            for i, it in enumerate(batch, start=1):
                if i not in seen:
                    clusters.append([it])
            return clusters
        except Exception as exc:
            logger.warning("LLM group batch failed: %s — using singletons", exc)
            return [[it] for it in batch]

    batch_clusters: list[list[dict]] = []
    for batch in batches:
        batch_clusters.extend(_group_batch(batch))
    logger.info(
        "  LLM grouped %d quotes into %d initial clusters (across %d batches)",
        len(items), len(batch_clusters), len(batches),
    )

    # Step 3: cross-batch merge — for pairs of clusters with highly similar
    # centroids, ask the LLM if they're really the same variant.
    if len(batches) <= 1 or len(batch_clusters) <= 1:
        return batch_clusters

    # Compute centroids
    def _centroid(cluster: list[dict]) -> list[float]:
        embs = [it.get("embedding") for it in cluster if it.get("embedding")]
        if not embs:
            return []
        n = len(embs)
        return [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]

    cluster_centroids = [_centroid(c) for c in batch_clusters]

    # For each pair with centroid similarity > 0.85 (loose retrieval threshold),
    # ask the LLM if they should be merged.
    MERGE_RETRIEVAL = 0.85
    merge_pairs: list[tuple[int, int]] = []
    for i in range(len(batch_clusters)):
        for j in range(i + 1, len(batch_clusters)):
            if not cluster_centroids[i] or not cluster_centroids[j]:
                continue
            sim = _cosine(cluster_centroids[i], cluster_centroids[j])
            if sim >= MERGE_RETRIEVAL:
                merge_pairs.append((i, j))

    if not merge_pairs:
        return batch_clusters

    # Union-Find for merge decisions
    parent = list(range(len(batch_clusters)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # LLM verify each candidate merge
    for i, j in merge_pairs:
        if find(i) == find(j):
            continue  # already merged transitively
        rep_i = batch_clusters[i][0]["nfm"].extracted_text[:250]
        rep_j = batch_clusters[j][0]["nfm"].extracted_text[:250]
        prompt = (
            f"These two quotes are from articles about the campaign frame "
            f"'{frame_name}'. Do they express the SAME SPECIFIC CLAIM (just "
            f"worded differently), or are they about DIFFERENT specific claims?\n\n"
            f"Quote A: \"{rep_i}\"\nQuote B: \"{rep_j}\"\n\n"
            "Return JSON: {\"same_claim\": true} or {\"same_claim\": false}"
        )
        try:
            r = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=50,
            )
            data = _json.loads(r.choices[0].message.content)
            if data.get("same_claim") is True:
                union(i, j)
        except Exception as exc:
            logger.debug("merge verify failed for %d,%d: %s", i, j, exc)

    # Apply merges
    final_groups: dict[int, list[dict]] = {}
    for i, cluster in enumerate(batch_clusters):
        root = find(i)
        final_groups.setdefault(root, []).extend(cluster)
    final = list(final_groups.values())
    if len(final) != len(batch_clusters):
        logger.info(
            "  cross-batch merge: %d clusters → %d (merged %d)",
            len(batch_clusters), len(final), len(batch_clusters) - len(final),
        )
    return final


def _name_cluster(quotes: list[str], frame_name: str) -> str:
    """LLM-name a cluster of quotes. Uses the centralized judge provider
    (OpenAI gpt-4o-mini primary, Groq 70B fallback) — see
    llm_provider.get_judge_provider.

    Returns a short, durable name (5-12 words) describing the common claim.
    """
    if not quotes:
        return "(empty)"
    if len(quotes) == 1:
        q = quotes[0]
        return (q[:80].rstrip(".,;:!?") + "…") if len(q) > 80 else q

    from app.services.llm_provider import get_judge_provider, MockLLMProvider

    provider = get_judge_provider()
    if isinstance(provider, MockLLMProvider):
        return quotes[0][:80]

    block = "\n".join(f'  - "{q[:200]}"' for q in quotes[:6])
    prompt = (
        f'These quotes were extracted from political news articles. They all '
        f'relate to the narrative frame "{frame_name}".\n\n'
        f"Quotes:\n{block}\n\n"
        "Summarize the COMMON claim across these quotes in 5-12 words. The "
        "summary should describe the SPECIFIC claim being made, not the topic "
        "in general. Use the most concrete language from the quotes.\n\n"
        "Examples of good names:\n"
        '  "Bresnahan voted against ACA expansion"\n'
        '  "Cognetti\'s secret second home at ski resort"\n'
        '  "Bresnahan donates after voting to cut SNAP"\n\n'
        "Examples of BAD names (too generic):\n"
        '  "Healthcare issues"\n'
        '  "Political controversy"\n'
        '  "Cognetti\'s record"\n\n'
        "Return ONLY the name, no quotes or explanation."
    )
    try:
        raw = provider.complete(prompt) or ""
        name = raw.strip().strip('"').strip("'")
        # The provider chain may return JSON if the underlying LLM gets confused;
        # strip any "summary":"..." pattern as a safety net.
        if name.startswith("{"):
            import json as _json
            try:
                d = _json.loads(name)
                name = (d.get("name") or d.get("summary") or d.get("title") or "").strip()
            except Exception:
                pass
        return name[:100] if name else quotes[0][:80]
    except Exception as exc:
        logger.warning("variant naming failed: %s", exc)
        return quotes[0][:80]


# ── Main entry ────────────────────────────────────────────────────────────────

def cluster_all_frames(
    db: Session,
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_quotes: int = MIN_FRAME_QUOTES_TO_CLUSTER,
    only_frame_ids: Optional[list[int]] = None,
) -> dict:
    """Cluster all eligible frames. Wipes existing variants for each frame
    before rebuilding (full re-cluster strategy).

    Returns a summary dict. Safe to call multiple times.
    """
    from app.models import NarrativeFrame, NarrativeFrameMention, FrameVariant
    from app.services.embeddings import embed_texts

    now = datetime.utcnow()
    frames_q = db.query(NarrativeFrame).filter(NarrativeFrame.active == True)
    if only_frame_ids:
        frames_q = frames_q.filter(NarrativeFrame.id.in_(only_frame_ids))
    frames = frames_q.all()

    summary = {
        "frames_examined": len(frames),
        "frames_processed": 0,
        "frames_skipped_low_quotes": 0,
        "total_quotes_embedded": 0,
        "total_variants_created": 0,
        "per_frame": [],
    }

    for frame in frames:
        # Pull NFMs with extracted_text for this frame
        nfms = (
            db.query(NarrativeFrameMention)
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                NarrativeFrameMention.extracted_text.isnot(None),
            )
            .all()
        )
        quotes = [
            n for n in nfms
            if (n.extracted_text or "").strip()
        ]
        if len(quotes) < min_quotes:
            summary["frames_skipped_low_quotes"] += 1
            continue

        logger.info(
            "frame_variants: frame %d '%s' — %d quotes",
            frame.id, frame.name, len(quotes),
        )

        # Embed quotes (cache in NFM.quote_embedding)
        texts_to_embed: list[str] = []
        nfms_needing_embed: list = []
        items: list[dict] = []
        for nfm in quotes:
            cached = nfm.quote_embedding
            emb = None
            if cached:
                try:
                    emb = json.loads(cached)
                except Exception:
                    emb = None
            if emb is None:
                texts_to_embed.append(nfm.extracted_text)
                nfms_needing_embed.append(nfm)
            items.append({"nfm": nfm, "embedding": emb})

        if texts_to_embed:
            t0 = time.time()
            new_embs = embed_texts(texts_to_embed, task_type="SEMANTIC_SIMILARITY")
            elapsed = time.time() - t0
            logger.info(
                "  embedded %d new quotes in %.1fs",
                sum(1 for e in new_embs if e), elapsed,
            )
            # Backfill cache + items list
            ne_iter = iter(new_embs)
            for nfm in nfms_needing_embed:
                e = next(ne_iter, None)
                if e is not None:
                    nfm.quote_embedding = json.dumps(e)
                    # Find item placeholder and fill embedding
                    for it in items:
                        if it["nfm"] is nfm:
                            it["embedding"] = e
                            break
            db.commit()

        # Drop NFMs that still have no embedding (embedding failed)
        items = [it for it in items if it["embedding"] is not None]
        if len(items) < min_quotes:
            summary["frames_skipped_low_quotes"] += 1
            continue
        summary["total_quotes_embedded"] += len(items)

        # Cluster via HDBSCAN — density-based, no global similarity threshold.
        # Wire-syndicated near-duplicates form tight high-density clusters;
        # genuine variants form looser clusters; unique quotes fall out as
        # noise singletons. Only knob is min_cluster_size (default 2).
        raw_clusters = _hdbscan_cluster(items, min_cluster_size=2)
        # Wrap raw clusters in the expected {centroid, members} format for downstream code.
        clusters = []
        for raw in raw_clusters:
            embs = [m.get("embedding") for m in raw if m.get("embedding")]
            if embs:
                n = len(embs)
                centroid = [sum(e[i] for e in embs) / n for i in range(len(embs[0]))]
            else:
                centroid = []
            clusters.append({"centroid": centroid, "members": raw})
        logger.info("  → %d clusters via HDBSCAN", len(clusters))

        # Wipe existing variants for this frame, NULL out NFM.variant_id refs
        # (need to NULL refs first because of FK)
        db.query(NarrativeFrameMention).filter(
            NarrativeFrameMention.frame_id == frame.id,
            NarrativeFrameMention.variant_id.isnot(None),
        ).update({"variant_id": None}, synchronize_session=False)
        db.query(FrameVariant).filter(
            FrameVariant.frame_id == frame.id,
        ).delete(synchronize_session=False)
        db.flush()

        # Create new FrameVariant rows + assign NFMs
        per_frame_record = {
            "frame_id": frame.id,
            "frame_name": frame.name,
            "variant_count": 0,
            "variants": [],
        }
        for ci, cluster in enumerate(clusters):
            member_quotes = [m["nfm"].extracted_text for m in cluster["members"]]
            variant_name = _name_cluster(member_quotes, frame.name)

            # first/last_seen reflect when the underlying article was PUBLISHED,
            # not when the NFM row was created. That's the date a user cares
            # about for "narrative emergence" analysis. Falls back to NFM
            # created_at if published_at is missing.
            from app.models import SourceItem as _SourceItem
            si_ids = [m["nfm"].source_item_id for m in cluster["members"]]
            pub_dates: list = []
            if si_ids:
                pub_dates = [
                    r[0] for r in db.query(_SourceItem.published_at)
                    .filter(_SourceItem.id.in_(si_ids))
                    .filter(_SourceItem.published_at.isnot(None))
                    .all()
                    if r[0] is not None
                ]
            if not pub_dates:
                pub_dates = [
                    m["nfm"].created_at for m in cluster["members"]
                    if m["nfm"].created_at
                ]
            first_seen = min(pub_dates) if pub_dates else now
            last_seen = max(pub_dates) if pub_dates else now

            variant = FrameVariant(
                frame_id=frame.id,
                name=variant_name,
                centroid_embedding=json.dumps(cluster["centroid"]),
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                mention_count=len(cluster["members"]),
                generation=1,  # bump on incremental re-clustering later
            )
            db.add(variant)
            db.flush()  # get variant.id

            for member in cluster["members"]:
                member["nfm"].variant_id = variant.id

            per_frame_record["variants"].append({
                "id": variant.id,
                "name": variant_name,
                "size": len(cluster["members"]),
            })
            per_frame_record["variant_count"] += 1
            summary["total_variants_created"] += 1

        db.commit()
        summary["per_frame"].append(per_frame_record)
        summary["frames_processed"] += 1

    logger.info(
        "frame_variants: done — %d frames processed, %d variants created",
        summary["frames_processed"], summary["total_variants_created"],
    )
    return summary
