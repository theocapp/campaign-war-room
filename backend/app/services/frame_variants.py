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
  2. Embed all NFM quotes (cache in NFM.quote_embedding)
  3. Cluster via agglomerative complete linkage on cosine distance.
     Threshold is calibrated per-campaign — see scripts/calibrate_variant_threshold.py.
  4. Name each cluster via the judge LLM provider (gpt-4o-mini / Groq fallback)
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
# CLUSTER_DISTANCE_THRESHOLD: cosine distance threshold for agglomerative
# clustering with complete linkage. Calibrated via SimHash-supervised
# threshold search — see app/scripts/calibrate_variant_threshold.py and
# verify_calibrated_threshold.py. The calibration:
#   1. Treats within-frame, same story_cluster_id pairs as positive labels
#   2. Treats cross-frame pairs as truly-negative labels
#   3. Sweeps thresholds; picks the loosest where cross-frame FPR ≤ 1%
#   4. Grid-searches linkage × threshold combos; complete linkage at 0.42
#      maximized wire-sync purity (64%) without producing mega-clusters
#
# This is a starting default. Each campaign should recalibrate against its
# own data once ≥200 NFMs have accumulated — re-run the calibration script.
CLUSTER_DISTANCE_THRESHOLD = 0.42
CLUSTER_LINKAGE = "complete"
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


def _agglomerative_cluster(
    items: list[dict],
    distance_threshold: float = CLUSTER_DISTANCE_THRESHOLD,
    linkage: str = CLUSTER_LINKAGE,
) -> list[list[dict]]:
    """Agglomerative clustering with cosine distance.

    Why agglomerative complete linkage (not HDBSCAN): on this corpus, HDBSCAN
    produced mixed clusters in dense frames — it would chain through density
    regions that share vocabulary but contain distinct claims. Complete
    linkage requires every pair within a merged cluster to be within the
    distance threshold, which prevents that chaining.

    The threshold is calibrated against SimHash-supervised wire-sync pairs;
    see app/scripts/calibrate_variant_threshold.py for the calibration
    procedure. Each campaign should recalibrate against its own data once
    ≥200 NFMs accumulate.
    """
    from sklearn.cluster import AgglomerativeClustering
    import numpy as np

    items_with_emb = [it for it in items if it.get("embedding")]
    if len(items_with_emb) < 2:
        return [[it] for it in items_with_emb]

    X = np.array([it["embedding"] for it in items_with_emb], dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    Xn = X / norms

    model = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage=linkage,
    )
    labels = model.fit_predict(Xn)

    groups: dict = {}
    for it, lbl in zip(items_with_emb, labels):
        groups.setdefault(int(lbl), []).append(it)
    return list(groups.values())


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

        # Cluster via agglomerative complete linkage on cosine distance.
        # Threshold (CLUSTER_DISTANCE_THRESHOLD) is calibrated against
        # SimHash-supervised wire-sync pairs — see calibrate_variant_threshold.py.
        raw_clusters = _agglomerative_cluster(items)
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
