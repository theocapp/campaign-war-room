"""Contradiction detector for the entity-relation graph.

Surfaces (subject, object) pairs where the subject simultaneously:
  - SUPPORTS the object (endorses / co_sponsored / voted_for / member_of) AND
  - OPPOSES the object (criticizes / attacks / voted_against / predecessor_of)

Real political nuance produces some of these (e.g. a legislator who
procedurally supports a bill but rhetorically criticizes it), so this is
a REVIEW QUEUE, not an auto-fix. The output is a markdown report you can
read article-by-article to decide what's real vs noise.

Why this exists per the GKG framework:
  - Principle #7 (probabilistic truth, not binary)
  - Principle #9 (cross-document reconciliation)
  - Principle #10 (humans for ambiguity)

This is the pragmatic 80/20 version. Doesn't compute Bayesian P(fact=true);
it just flags conflicts and shows the supporting evidence so you can
adjudicate.

USAGE:
    .venv/bin/python scripts/entity_contradiction_detector.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation, SourceItem


REPORT_PATH = Path("/tmp/noctua_contradictions_report.md")

# Predicates that signal support of the object
_SUPPORT = {"endorses", "co_sponsored", "voted_for", "member_of"}
# Predicates that signal opposition
_OPPOSE = {"criticizes", "attacks", "voted_against"}


def find_contradictions(db) -> list[dict]:
    """Find (subject, object) pairs with both a support and opposition relation."""
    # Pull all relations once
    relations = db.query(EntityRelation).all()
    entities = {e.id: e for e in db.query(Entity).all()}

    # Bucket by (subject_id, object_id)
    pairs: dict[tuple[int, int], dict[str, list[EntityRelation]]] = defaultdict(
        lambda: {"support": [], "oppose": []}
    )
    for r in relations:
        key = (r.subject_id, r.object_id)
        if r.predicate in _SUPPORT:
            pairs[key]["support"].append(r)
        elif r.predicate in _OPPOSE:
            pairs[key]["oppose"].append(r)

    contradictions: list[dict] = []
    for (s_id, o_id), rels in pairs.items():
        if not rels["support"] or not rels["oppose"]:
            continue
        subj = entities.get(s_id)
        obj = entities.get(o_id)
        if not subj or not obj:
            continue

        total_support_weight = sum((r.weight or 0) for r in rels["support"])
        total_oppose_weight = sum((r.weight or 0) for r in rels["oppose"])

        # Pull sample article titles for the support side
        support_articles: list[str] = []
        oppose_articles: list[str] = []
        for r in rels["support"]:
            try:
                ids = json.loads(r.source_articles or "[]")[:3]
            except Exception:
                ids = []
            if ids:
                rows = db.query(SourceItem.title).filter(SourceItem.id.in_(ids)).all()
                support_articles.extend(t for (t,) in rows if t)
        for r in rels["oppose"]:
            try:
                ids = json.loads(r.source_articles or "[]")[:3]
            except Exception:
                ids = []
            if ids:
                rows = db.query(SourceItem.title).filter(SourceItem.id.in_(ids)).all()
                oppose_articles.extend(t for (t,) in rows if t)

        contradictions.append({
            "subject": {"name": subj.name, "canonical_id": subj.canonical_id,
                        "affiliation": subj.affiliation, "type": subj.type},
            "object": {"name": obj.name, "canonical_id": obj.canonical_id, "type": obj.type},
            "support_relations": [
                {"predicate": r.predicate, "weight": r.weight,
                 "confidence": r.confidence, "sample_quote": r.sample_quote}
                for r in rels["support"]
            ],
            "oppose_relations": [
                {"predicate": r.predicate, "weight": r.weight,
                 "confidence": r.confidence, "sample_quote": r.sample_quote}
                for r in rels["oppose"]
            ],
            "support_weight_total": total_support_weight,
            "oppose_weight_total": total_oppose_weight,
            "support_articles_sample": support_articles[:5],
            "oppose_articles_sample": oppose_articles[:5],
            "score": min(total_support_weight, total_oppose_weight),  # how balanced
        })

    # Sort by score desc — biggest, most-balanced contradictions first
    contradictions.sort(key=lambda c: -c["score"])
    return contradictions


def write_report(contradictions: list[dict]) -> None:
    lines: list[str] = []
    lines.append("# Contradiction detector — subjects with both support and opposition\n")
    lines.append(f"- Pairs flagged: **{len(contradictions)}**")
    lines.append("")
    lines.append("Each entry shows a subject-object pair where the subject has BOTH")
    lines.append("support-type and opposition-type relations. Some are real political nuance")
    lines.append("(procedural support + rhetorical opposition); others may be extraction")
    lines.append("noise that needs cleanup.")
    lines.append("")
    lines.append("Sorted by **min(support_weight, oppose_weight)** — most balanced contradictions first.")
    lines.append("")
    lines.append("---\n")
    for i, c in enumerate(contradictions, 1):
        subj = c["subject"]
        obj = c["object"]
        aff_tag = f" ({subj['affiliation']})" if subj['affiliation'] else ""
        lines.append(f"## {i}. {subj['name']}{aff_tag} ↔ {obj['name']}")
        lines.append("")
        lines.append(f"`{subj['canonical_id']}` ↔ `{obj['canonical_id']}`  ·  support_total={c['support_weight_total']}, oppose_total={c['oppose_weight_total']}")
        lines.append("")
        lines.append("**Support relations:**")
        for r in c["support_relations"]:
            q = r.get("sample_quote") or ""
            q_part = f' — *"{q[:120]}"*' if q else ""
            lines.append(f"- `{r['predicate']}` weight={r['weight']} confidence={r['confidence']}{q_part}")
        lines.append("")
        lines.append("**Opposition relations:**")
        for r in c["oppose_relations"]:
            q = r.get("sample_quote") or ""
            q_part = f' — *"{q[:120]}"*' if q else ""
            lines.append(f"- `{r['predicate']}` weight={r['weight']} confidence={r['confidence']}{q_part}")
        lines.append("")
        if c["support_articles_sample"] or c["oppose_articles_sample"]:
            lines.append("**Sample article titles:**")
            for t in c["support_articles_sample"][:3]:
                lines.append(f"- [support] {t}")
            for t in c["oppose_articles_sample"][:3]:
                lines.append(f"- [oppose] {t}")
            lines.append("")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report: {REPORT_PATH}")


def main() -> None:
    db = SessionLocal()
    try:
        contradictions = find_contradictions(db)
        print(f"Found {len(contradictions)} support-vs-opposition contradictions")
        write_report(contradictions)
        if contradictions[:5]:
            print()
            print("TOP 5 BY BALANCE SCORE:")
            for c in contradictions[:5]:
                subj = c["subject"]
                obj = c["object"]
                supports = ", ".join(r["predicate"] for r in c["support_relations"])
                opposes = ", ".join(r["predicate"] for r in c["oppose_relations"])
                print(f"  {subj['name']:25s} ↔ {obj['name']:30s} "
                      f"sup=[{supports}]({c['support_weight_total']}) "
                      f"opp=[{opposes}]({c['oppose_weight_total']})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
