"""
DRY-RUN frame variant clustering.

Reads NarrativeFrameMention rows with extracted_text, groups by frame, embeds
each quote, clusters by cosine similarity, and asks Groq 70B to name each
cluster. Writes results to /tmp/frame_variants_dry_run.json + prints summary.

Does NOT write to the DB — purely for inspection. Once the user validates
quality, a follow-up commits schema + writes results live.

Usage:
    cd backend && .venv/bin/python3 -m app.scripts.cluster_frame_variants_dry_run
"""
import json
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Optional

# Standard module setup so this runs as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── Knobs ─────────────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.88  # cosine; >= this means "same variant"
MIN_CLUSTER_SIZE = 1         # for dry-run, show all clusters including singletons
MIN_FRAME_QUOTES = 2         # skip frames with fewer than this many quotes


# ── Clustering: simple incremental algorithm ──────────────────────────────────

def incremental_cluster(
    items: list[dict], emb_key: str = "embedding",
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[list[dict]]:
    """Group items by cosine similarity. For each item, find the existing
    cluster whose centroid has the highest similarity. If above threshold,
    add to that cluster. Otherwise start a new one.

    Cluster centroid = element-wise mean of member embeddings.
    Order-dependent (could refine with k-means later) but good enough for
    a first pass on small per-frame quote sets.
    """
    from app.services.embeddings import cosine_similarity

    clusters: list[dict] = []  # {centroid: list[float], members: list[dict]}

    for it in items:
        emb = it.get(emb_key)
        if emb is None:
            continue
        best_idx = -1
        best_sim = -1.0
        for ci, c in enumerate(clusters):
            sim = cosine_similarity(emb, c["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_idx = ci
        if best_sim >= threshold and best_idx >= 0:
            c = clusters[best_idx]
            n = len(c["members"])
            # Update centroid as running mean.
            c["centroid"] = [
                (cv * n + ev) / (n + 1) for cv, ev in zip(c["centroid"], emb)
            ]
            c["members"].append(it)
        else:
            clusters.append({"centroid": list(emb), "members": [it]})

    return [c["members"] for c in clusters]


# ── LLM cluster naming via Groq 70B ───────────────────────────────────────────

def name_cluster_with_llm(quotes: list[str], frame_name: str) -> str:
    """Ask Groq llama-3.3-70b-versatile to summarize the common claim across
    a set of quotes in 5-12 words. Falls back to first quote if LLM unavailable.
    """
    if not quotes:
        return "(empty)"
    if len(quotes) == 1:
        # Singleton — name it after itself, truncated.
        return quotes[0][:80].rstrip(".,;:!?") + ("…" if len(quotes[0]) > 80 else "")

    try:
        from openai import OpenAI
        api_key = os.environ.get("GROQ_API_KEY", "").strip()
        if not api_key:
            return quotes[0][:80]
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

        quote_block = "\n".join(f"  - \"{q[:200]}\"" for q in quotes[:6])
        prompt = f"""These quotes were extracted from political campaign news articles. They all relate to the narrative frame "{frame_name}".

Quotes:
{quote_block}

Summarize the COMMON claim across these quotes in 5-12 words. The summary should describe the SPECIFIC claim being made, not the topic in general. Use the most concrete language from the quotes.

Examples of good names:
  "Bresnahan voted against ACA expansion"
  "Cognetti's secret second home at ski resort"
  "Bresnahan donates after voting to cut SNAP"

Examples of BAD names (too generic):
  "Healthcare issues"
  "Political controversy"
  "Cognetti's record"

Return ONLY the name, no quotes or explanation."""
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=40,
        )
        name = (resp.choices[0].message.content or "").strip().strip('"').strip("'")
        return name[:100] if name else quotes[0][:80]
    except Exception as exc:
        logger.warning("cluster naming failed: %s", exc)
        return quotes[0][:80]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    from app.db import SessionLocal
    from app.models import NarrativeFrame, NarrativeFrameMention
    from app.services.embeddings import embed_texts

    with SessionLocal() as db:
        # Pull frames + their quotes
        frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
        frame_quotes: dict[int, list[dict]] = defaultdict(list)
        for nfm in db.query(NarrativeFrameMention).filter(
            NarrativeFrameMention.extracted_text.isnot(None),
        ).all():
            if not (nfm.extracted_text or "").strip():
                continue
            frame_quotes[nfm.frame_id].append({
                "nfm_id": nfm.id,
                "frame_id": nfm.frame_id,
                "source_item_id": nfm.source_item_id,
                "quote": nfm.extracted_text,
                "claim_meta": nfm.claim_meta,
                "confidence": nfm.confidence,
            })

    frame_by_id = {f.id: f for f in frames}
    eligible = {
        fid: qs for fid, qs in frame_quotes.items()
        if fid in frame_by_id and len(qs) >= MIN_FRAME_QUOTES
    }
    logger.info(
        "Frames with quotes: %d. Eligible (>=%d quotes): %d.",
        len(frame_quotes), MIN_FRAME_QUOTES, len(eligible),
    )

    report: dict = {
        "summary": {
            "total_frames_with_quotes": len(frame_quotes),
            "eligible_frames": len(eligible),
            "similarity_threshold": SIMILARITY_THRESHOLD,
        },
        "frames": [],
    }

    for fid, quotes_list in sorted(eligible.items()):
        frame = frame_by_id[fid]
        quote_texts = [q["quote"] for q in quotes_list]
        logger.info("→ Frame #%d '%s' — %d quotes", fid, frame.name, len(quote_texts))

        t0 = time.time()
        embs = embed_texts(quote_texts, task_type="SEMANTIC_SIMILARITY")
        for i, e in enumerate(embs):
            quotes_list[i]["embedding"] = e
        logger.info("  embedded in %.1fs", time.time() - t0)

        clusters = incremental_cluster(quotes_list)
        logger.info("  → %d clusters", len(clusters))

        cluster_records = []
        for ci, members in enumerate(clusters):
            member_quotes = [m["quote"] for m in members]
            name = name_cluster_with_llm(member_quotes, frame.name)
            logger.info("    cluster %d (%d members): %r", ci, len(members), name)
            cluster_records.append({
                "cluster_index": ci,
                "size": len(members),
                "name": name,
                "sample_quotes": member_quotes[:3],
                "member_nfm_ids": [m["nfm_id"] for m in members],
            })

        report["frames"].append({
            "frame_id": fid,
            "frame_name": frame.name,
            "owner_type": frame.owner_type,
            "quote_count": len(quote_texts),
            "cluster_count": len(clusters),
            "clusters": cluster_records,
        })

    out_path = "/tmp/frame_variants_dry_run.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Wrote report to %s", out_path)

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for f_record in report["frames"]:
        print(f"\nFrame #{f_record['frame_id']}: \"{f_record['frame_name']}\"  [{f_record['owner_type']}]")
        print(f"  {f_record['quote_count']} quotes → {f_record['cluster_count']} variants")
        for c in f_record["clusters"]:
            print(f"    [{c['size']}x] {c['name']}")
            if c["size"] > 1:
                for q in c["sample_quotes"][:2]:
                    print(f"          ↳ \"{q[:120]}\"")

    return 0


if __name__ == "__main__":
    sys.exit(main())
