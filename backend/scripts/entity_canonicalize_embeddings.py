"""Embedding-similarity canonicalization (Phase 3 of Feature A).

Catches semantic duplicates the rule-based pass missed:
  - Different first-name forms (Bill / William, Mike / Michael)
  - Reworded org names without shared acronyms
  - Bills referred to by topic rather than canonical name

Approach:
  1. Embed every entity name + description with `embed_texts()`.
  2. Within each type, compute cosine similarity for every pair.
  3. Cluster connected components of high-similarity pairs.
  4. Output proposals to /tmp/noctua_canonicalize_embed_proposals.json
     and a markdown report.
  5. --apply executes them through the same merge function as the rule-based
     pass.

Thresholds:
  AUTO_THRESHOLD  ≥ 0.92  → include in proposals (high confidence)
  REVIEW_THRESHOLD ≥ 0.85 → include with confidence='medium'

The script never auto-applies — even AUTO-threshold matches go through the
dry-run-then-apply workflow so you can edit the JSON if anything looks off.

USAGE:
    .venv/bin/python scripts/entity_canonicalize_embeddings.py            # dry-run
    .venv/bin/python scripts/entity_canonicalize_embeddings.py --apply    # apply
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity
from app.services.embeddings import embed_texts, cosine_similarity
from scripts.entity_canonicalize_rules import (
    merge_sources_into_target,
    recompute_counters,
)


PROPOSAL_PATH = Path("/tmp/noctua_canonicalize_embed_proposals.json")
REPORT_PATH = Path("/tmp/noctua_canonicalize_embed_report.md")

AUTO_THRESHOLD = 0.92
REVIEW_THRESHOLD = 0.85

# Hard cap on how many entities per type we'll embed. Keeps the pairwise
# loop bounded — at 1500 entities it's 1.1M comparisons (fast) but the
# embedding API cost scales linearly. Pick the top-N by mention_count.
MAX_ENTITIES_PER_TYPE = 800

# Don't even bother embedding entities with very low mention count — they're
# noise. A duplicate at mention=1 has marginal impact even if merged.
MIN_MENTIONS_FOR_EMBED = 2


def build_embed_text(e: Entity) -> str:
    """Text used to embed an entity. Combines name and description for richer
    semantic signal than name alone."""
    parts = [e.name or ""]
    if e.description:
        parts.append(e.description)
    return " — ".join(parts)


def find_pairs_for_type(
    entities: list[Entity],
    embeddings: dict[int, list[float]],
) -> list[tuple[Entity, Entity, float]]:
    """Pairwise similarity for entities of one type. Returns list of
    (a, b, sim) tuples above REVIEW_THRESHOLD, with a.id < b.id."""
    pairs: list[tuple[Entity, Entity, float]] = []
    eids = [e.id for e in entities if e.id in embeddings]
    id_to_ent = {e.id: e for e in entities}
    for i, a_id in enumerate(eids):
        a_emb = embeddings[a_id]
        for b_id in eids[i + 1:]:
            sim = cosine_similarity(a_emb, embeddings[b_id])
            if sim >= REVIEW_THRESHOLD:
                pairs.append((id_to_ent[a_id], id_to_ent[b_id], sim))
    return pairs


def cluster_pairs(pairs: list[tuple[Entity, Entity, float]]) -> list[list[Entity]]:
    """Build connected-components clusters from the pair list."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    ent_lookup: dict[int, Entity] = {}
    for a, b, _ in pairs:
        for e in (a, b):
            if e.id not in parent:
                parent[e.id] = e.id
                ent_lookup[e.id] = e
        union(a.id, b.id)

    clusters: dict[int, list[Entity]] = defaultdict(list)
    for eid in parent:
        clusters[find(eid)].append(ent_lookup[eid])

    return [c for c in clusters.values() if len(c) > 1]


def pick_target(group: list[Entity]) -> Entity:
    """Same selection rule as the rule-based pass: seeded preferred, then
    highest mention_count, then lowest id."""
    return sorted(group, key=lambda e: (not bool(e.seeded), -(e.mention_count or 0), e.id))[0]


def generate_proposals(db) -> list[dict]:
    all_entities = db.query(Entity).all()
    by_type: dict[str, list[Entity]] = defaultdict(list)
    for e in all_entities:
        if (e.mention_count or 0) >= MIN_MENTIONS_FOR_EMBED:
            by_type[e.type].append(e)

    # Cap per-type to MAX_ENTITIES_PER_TYPE — top-N by mention_count.
    for t, ents in list(by_type.items()):
        ents.sort(key=lambda e: -(e.mention_count or 0))
        if len(ents) > MAX_ENTITIES_PER_TYPE:
            by_type[t] = ents[:MAX_ENTITIES_PER_TYPE]

    # Flatten and embed
    all_to_embed: list[Entity] = [e for ents in by_type.values() for e in ents]
    texts = [build_embed_text(e) for e in all_to_embed]
    print(f"Embedding {len(texts)} entity texts...", flush=True)
    vectors = embed_texts(texts)
    print(f"  failures: {sum(1 for v in vectors if v is None)}", flush=True)

    embeddings: dict[int, list[float]] = {}
    for e, v in zip(all_to_embed, vectors):
        if v is not None:
            embeddings[e.id] = v

    # Pairwise within each type
    proposals: list[dict] = []
    for etype, ents in by_type.items():
        pairs = find_pairs_for_type(ents, embeddings)
        if not pairs:
            continue
        # Keep only high-confidence pairs (≥ AUTO_THRESHOLD) for the actual
        # cluster build — but record review-tier ones in the report.
        auto_pairs = [p for p in pairs if p[2] >= AUTO_THRESHOLD]
        clusters = cluster_pairs(auto_pairs)
        for cluster in clusters:
            target = pick_target(cluster)
            sources = [e for e in cluster if e.id != target.id]
            if not sources:
                continue
            min_sim = min(s for a, b, s in auto_pairs
                          if a.id in {e.id for e in cluster} or b.id in {e.id for e in cluster})
            proposals.append({
                "type": etype,
                "target": {"id": target.id, "name": target.name, "canonical_id": target.canonical_id,
                           "mentions": target.mention_count or 0, "seeded": bool(target.seeded)},
                "sources": [{"id": e.id, "name": e.name, "canonical_id": e.canonical_id,
                             "mentions": e.mention_count or 0, "seeded": bool(e.seeded)} for e in sources],
                "combined_mentions": sum((e.mention_count or 0) for e in cluster),
                "min_cluster_similarity": round(min_sim, 4),
                "reason": f"embedding similarity ≥ {AUTO_THRESHOLD}",
                "confidence": "high",
            })

        # Also collect REVIEW-tier pairs (informational, not auto-included)
        review_pairs = [(a, b, s) for a, b, s in pairs
                        if REVIEW_THRESHOLD <= s < AUTO_THRESHOLD]
        for a, b, sim in review_pairs:
            target = pick_target([a, b])
            source = b if target.id == a.id else a
            proposals.append({
                "type": etype,
                "target": {"id": target.id, "name": target.name, "canonical_id": target.canonical_id,
                           "mentions": target.mention_count or 0, "seeded": bool(target.seeded)},
                "sources": [{"id": source.id, "name": source.name, "canonical_id": source.canonical_id,
                             "mentions": source.mention_count or 0, "seeded": bool(source.seeded)}],
                "combined_mentions": (a.mention_count or 0) + (b.mention_count or 0),
                "min_cluster_similarity": round(sim, 4),
                "reason": f"embedding similarity ≥ {REVIEW_THRESHOLD} (review)",
                "confidence": "medium",
            })

    proposals.sort(key=lambda p: (-p["combined_mentions"], -p["min_cluster_similarity"]))
    return proposals


def write_report(proposals: list[dict]) -> None:
    high = [p for p in proposals if p["confidence"] == "high"]
    medium = [p for p in proposals if p["confidence"] == "medium"]
    lines: list[str] = []
    lines.append(f"# Embedding-similarity canonicalization proposals\n")
    lines.append(f"- High-confidence merges (sim ≥ {AUTO_THRESHOLD}): **{len(high)}**")
    lines.append(f"- Review-tier (sim ≥ {REVIEW_THRESHOLD}): **{len(medium)}**")
    lines.append("")
    lines.append("Only the HIGH-confidence merges are applied by `--apply`. Review-tier")
    lines.append("entries are informational and you can promote them by moving them into")
    lines.append("the proposals JSON manually if you agree they're matches.")
    lines.append("")
    lines.append("---\n")
    lines.append("## High-confidence merges (will be applied)\n")
    for i, p in enumerate(high, 1):
        tgt = p["target"]
        tgt_tag = "[seed]" if tgt["seeded"] else "[auto]"
        lines.append(f"### {i}. {p['type']} — similarity {p['min_cluster_similarity']}")
        lines.append("")
        lines.append(f"**KEEP:** {tgt_tag} `{tgt['canonical_id']}` — **{tgt['name']}** ({tgt['mentions']} mentions)")
        lines.append("")
        for s in p["sources"]:
            s_tag = "[seed]" if s["seeded"] else "[auto]"
            lines.append(f"- {s_tag} `{s['canonical_id']}` — {s['name']} ({s['mentions']} mentions)")
        lines.append("")
    lines.append("---")
    lines.append("## Review-tier (informational only)\n")
    for i, p in enumerate(medium, 1):
        tgt = p["target"]
        s = p["sources"][0]
        lines.append(f"{i}. {p['type']} {tgt['name']!r} ↔ {s['name']!r} (sim={p['min_cluster_similarity']})")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report: {REPORT_PATH}")


def write_proposals_json(proposals: list[dict]) -> None:
    # Only persist the HIGH-confidence ones (these are what --apply will run).
    high = [p for p in proposals if p["confidence"] == "high"]
    PROPOSAL_PATH.write_text(json.dumps(high, indent=2))
    print(f"Apply-list: {PROPOSAL_PATH} ({len(high)} high-confidence proposals)")


def apply_proposals(db, proposals: list[dict]) -> dict:
    grand: dict[str, int] = {"merges_applied": 0}
    for prop in proposals:
        target_id = prop["target"]["id"]
        source_ids = [s["id"] for s in prop["sources"]]
        stats = merge_sources_into_target(db, source_ids, target_id)
        for k, v in stats.items():
            grand[k] = grand.get(k, 0) + v
        recompute_counters(db, target_id)
        grand["merges_applied"] += 1
        db.commit()
    return grand


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Apply HIGH-confidence proposals from the JSON")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.apply:
            if not PROPOSAL_PATH.exists():
                print(f"No proposals at {PROPOSAL_PATH} — run without --apply first.")
                return
            proposals = json.loads(PROPOSAL_PATH.read_text())
            print(f"Applying {len(proposals)} embedding-based merges...\n", flush=True)
            grand = apply_proposals(db, proposals)
            print()
            print("=" * 60)
            for k, v in grand.items():
                print(f"  {k:30s} {v}")
        else:
            print("Generating embedding-based proposals (dry-run)...\n", flush=True)
            proposals = generate_proposals(db)
            high = [p for p in proposals if p["confidence"] == "high"]
            medium = [p for p in proposals if p["confidence"] == "medium"]
            print(f"  {len(high)} high-confidence groups, {len(medium)} review-tier pairs")
            write_proposals_json(proposals)
            write_report(proposals)
            if high[:5]:
                print()
                print("TOP 5 PREVIEW:")
                for p in high[:5]:
                    tgt = p["target"]
                    print(f"  KEEP {tgt['canonical_id']} ({tgt['name']}, {tgt['mentions']} mentions) sim={p['min_cluster_similarity']}")
                    for s in p["sources"][:3]:
                        print(f"    + merge {s['canonical_id']} ({s['name']}, {s['mentions']} mentions)")
                    if len(p["sources"]) > 3:
                        print(f"    + {len(p['sources']) - 3} more...")
    finally:
        db.close()


if __name__ == "__main__":
    main()
