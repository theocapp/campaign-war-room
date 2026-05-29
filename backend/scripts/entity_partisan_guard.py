"""Partisan domain/range guard — reclassify cross-party endorsements.

Problem: gpt-4o-mini emits `endorses` too eagerly. When a Republican signs a
discharge petition for a Democratic bill, the LLM picks up the favorable
language ("crossing party lines to support") and emits `endorses`. That
captures something real — a cross-party procedural action — but
`endorses` is a misleadingly strong predicate for what's actually closer
to `co_sponsored` (procedural support without ideological alignment).

This script:
  1) Loads a partisan coding map for known bills (seed list + heuristic patterns).
  2) Finds `endorses` rows where subject affiliation conflicts with object coding.
  3) Reports them with full sample-quote + article evidence.
  4) --apply: reclassifies the predicate from `endorses` to `co_sponsored`,
     merging weight/source_articles into an existing co_sponsored row if one
     already exists between the same subject and object.

NOT flagged (could be legitimate intra-/cross-party patterns):
  voted_for / voted_against — bipartisan votes are real
  attacks / criticizes — intra-party fights happen
  member_of / represents — geographic/structural, not partisan

USAGE:
    .venv/bin/python scripts/entity_partisan_guard.py            # dry-run report
    .venv/bin/python scripts/entity_partisan_guard.py --apply    # reclassify endorses → co_sponsored
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation, SourceItem


REPORT_PATH = Path("/tmp/noctua_partisan_guard_report.md")


# Known bills with explicit partisan coding (canonical_id → 'D' or 'R').
# These cover the seed bills + the most common auto-discovered families
# that survived the canonicalization pass.
_BILL_CODING_BY_ID: dict[str, str] = {
    "bill:aca-subsidies": "D",        # Affordable Care Act, Dem priority to extend
    "bill:medicaid-cuts": "R",        # Federal Medicaid Cuts, R-led reduction
    "bill:tax-cuts": "R",             # Trump Tax Cut Extension, R priority
    # bill:stock-act is genuinely bipartisan — leave unflagged
}

# Pattern-based fallback for auto-discovered bills not in the explicit map.
# Order matters — first match wins.
_BILL_CODING_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\baffordable care act\b", re.I), "D"),
    (re.compile(r"\bobamacare\b", re.I), "D"),
    (re.compile(r"\baca\b", re.I), "D"),
    (re.compile(r"\btrump.*tax\b", re.I), "R"),
    (re.compile(r"\btax cuts and jobs act\b", re.I), "R"),
    (re.compile(r"\bmedicaid.*cut", re.I), "R"),
    (re.compile(r"\bmedicare for all\b", re.I), "D"),
    (re.compile(r"\bgreen new deal\b", re.I), "D"),
    (re.compile(r"\bbuild back better\b", re.I), "D"),
    (re.compile(r"\binflation reduction act\b", re.I), "D"),
    (re.compile(r"\bchips act\b", re.I), "D"),
    (re.compile(r"\bproject 2025\b", re.I), "R"),
    (re.compile(r"\bborder wall|secure the border\b", re.I), "R"),
]


def bill_coding(entity: Entity) -> str | None:
    """Return 'D', 'R', or None for the bill entity. None = unknown / bipartisan."""
    if entity.type != "bill":
        return None
    coding = _BILL_CODING_BY_ID.get(entity.canonical_id)
    if coding:
        return coding
    for pattern, code in _BILL_CODING_PATTERNS:
        if pattern.search(entity.name or ""):
            return code
    return None


def find_suspicious(db) -> list[dict]:
    """Return suspicious relations as a list of dict reports."""
    # Pull everything once
    entities = {e.id: e for e in db.query(Entity).all()}
    relations = db.query(EntityRelation).filter(EntityRelation.predicate == "endorses").all()

    suspect: list[dict] = []
    for r in relations:
        subj = entities.get(r.subject_id)
        obj = entities.get(r.object_id)
        if not subj or not obj:
            continue
        if subj.type != "person":
            continue
        if not subj.affiliation or subj.affiliation not in ("D", "R"):
            continue
        obj_coding = bill_coding(obj)
        if not obj_coding:
            continue
        if subj.affiliation == obj_coding:
            continue  # same-party — fine

        # Pull article ids for evidence
        try:
            article_ids = json.loads(r.source_articles or "[]")
        except Exception:
            article_ids = []
        article_titles: list[str] = []
        if article_ids:
            rows = (
                db.query(SourceItem.title)
                .filter(SourceItem.id.in_(article_ids[:5]))
                .all()
            )
            article_titles = [t for (t,) in rows if t]

        suspect.append({
            "relation_id": r.id,
            "subject": {"id": subj.id, "name": subj.name, "canonical_id": subj.canonical_id,
                        "affiliation": subj.affiliation},
            "predicate": r.predicate,
            "object": {"id": obj.id, "name": obj.name, "canonical_id": obj.canonical_id,
                       "coding": obj_coding},
            "weight": r.weight or 0,
            "sample_quote": r.sample_quote,
            "article_count": len(article_ids),
            "article_titles_sample": article_titles,
        })

    # Sort by weight desc so the biggest false positives come first
    suspect.sort(key=lambda x: -x["weight"])
    return suspect


def write_report(suspect: list[dict]) -> None:
    lines: list[str] = []
    lines.append("# Partisan domain/range guard — suspicious endorsements\n")
    total_weight = sum(s["weight"] for s in suspect)
    lines.append(f"- Relations flagged: **{len(suspect)}**")
    lines.append(f"- Combined weight (article-evidence count): **{total_weight}**")
    lines.append("")
    lines.append("These are `endorses` relations from a person to a bill where the person's")
    lines.append("party affiliation does not match the bill's partisan coding. The LLM almost")
    lines.append("certainly misread \"discussed favorably\" or \"acknowledged\" as endorsement.")
    lines.append("")
    lines.append("---\n")
    for i, s in enumerate(suspect, 1):
        subj = s["subject"]
        obj = s["object"]
        lines.append(f"## {i}. {subj['name']} ({subj['affiliation']}) → endorses → {obj['name']} ({obj['coding']}-coded) — weight {s['weight']}")
        lines.append("")
        lines.append(f"- Subject: `{subj['canonical_id']}`")
        lines.append(f"- Object: `{obj['canonical_id']}`")
        if s["sample_quote"]:
            lines.append(f"- Sample quote: *\"{s['sample_quote']}\"*")
        lines.append(f"- Backed by {s['article_count']} article(s)")
        if s["article_titles_sample"]:
            lines.append(f"- Top article titles:")
            for t in s["article_titles_sample"]:
                lines.append(f"  - {t}")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report: {REPORT_PATH}")


def apply_reclassifications(db, suspect: list[dict]) -> dict:
    """Change predicate from `endorses` to `co_sponsored` for cross-party rows.

    If a (subject, co_sponsored, object) row already exists, fold the
    endorses row's weight + source_articles into it and delete the endorses
    row (preserves the unique triple constraint).
    """
    stats = {"reclassified": 0, "merged_into_existing": 0, "weight_moved": 0}
    for s in suspect:
        rel = db.query(EntityRelation).filter(EntityRelation.id == s["relation_id"]).one_or_none()
        if not rel:
            continue
        target = (
            db.query(EntityRelation)
            .filter(EntityRelation.subject_id == rel.subject_id,
                    EntityRelation.predicate == "co_sponsored",
                    EntityRelation.object_id == rel.object_id)
            .first()
        )
        moved = rel.weight or 0
        if target and target.id != rel.id:
            # Fold endorses into existing co_sponsored row
            target.weight = (target.weight or 0) + (rel.weight or 0)
            # Merge source_articles, dedupe, cap at 50
            try:
                a = json.loads(target.source_articles or "[]")
                b = json.loads(rel.source_articles or "[]")
            except Exception:
                a, b = [], []
            merged_sources = list(dict.fromkeys(a + b))[-50:]
            target.source_articles = json.dumps(merged_sources)
            # Keep the earliest first_seen / latest last_seen
            if rel.first_seen and (target.first_seen is None or rel.first_seen < target.first_seen):
                target.first_seen = rel.first_seen
            if rel.last_seen and (target.last_seen is None or rel.last_seen > target.last_seen):
                target.last_seen = rel.last_seen
            # If endorses sample_quote is non-empty and target's is empty, copy over
            if (not target.sample_quote) and rel.sample_quote:
                target.sample_quote = rel.sample_quote
            db.delete(rel)
            stats["merged_into_existing"] += 1
        else:
            # No existing co_sponsored row — just relabel
            rel.predicate = "co_sponsored"
            # Lower confidence: this was inferred from imprecise language,
            # not a literal "X co-sponsored Y" statement.
            rel.confidence = "low"
            stats["reclassified"] += 1
        stats["weight_moved"] += moved
    db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Reclassify endorses → co_sponsored (confidence=low)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        suspect = find_suspicious(db)
        print(f"Found {len(suspect)} cross-party endorsement relations")
        write_report(suspect)
        if suspect[:5]:
            print()
            print("TOP 5 BY WEIGHT:")
            for s in suspect[:5]:
                print(f"  {s['subject']['name']} ({s['subject']['affiliation']}) → endorses → {s['object']['name']} ({s['object']['coding']}) — weight {s['weight']}")
        if args.apply:
            print()
            print("Reclassifying endorses → co_sponsored (confidence=low)...")
            stats = apply_reclassifications(db, suspect)
            print(f"  Reclassified (no merge needed):   {stats['reclassified']}")
            print(f"  Merged into existing co_sponsored: {stats['merged_into_existing']}")
            print(f"  Combined evidence weight moved:    {stats['weight_moved']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
