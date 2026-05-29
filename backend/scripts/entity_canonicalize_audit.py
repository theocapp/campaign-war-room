"""Generic entity-canonicalization audit.

Finds existing entity fragmentation in the DB and proposes merges.
Works for ANY campaign — no race-specific code. The matchers it uses:

  1. Nickname-equivalence (Patricia ↔ Trish, Robert ↔ Bob, etc.)
     via app/services/nicknames.py
  2. Honorific-stripped matches (Dr. X / X, Rep. X / X, etc.)
     via app/services/honorifics.py
  3. District surface forms (8th Congressional District / PA-08 / etc.)
     via app/services/canonicalize_district.py, scoped to the campaign's
     configured district from CampaignConfig.

Cleanup pattern is the same as scripts/entity_canonicalize_rules.py and
scripts/entity_canonicalize_embeddings.py: dry-run writes proposals JSON
+ markdown report, --apply executes via the merge_sources_into_target
helper. Reuses that function so all bookkeeping (mentions redirect,
relation weight merge, alias preservation, counter recompute) is shared.

USAGE:
    python scripts/entity_canonicalize_audit.py              # dry-run
    python scripts/entity_canonicalize_audit.py --apply      # apply

WHEN TO RUN:
  - Once per campaign during onboarding (before the first extraction
    backfill), so the seeded canonicals have surface-form aliases.
  - Periodically as the corpus grows and the LLM finds new variants.
  - After any change to the nickname / honorific / district modules.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Entity
from app.services.canonicalize_district import (
    district_surface_forms, is_district_surface_form,
)
from app.services.honorifics import removed_only_generational, strip_honorifics
from app.services.nicknames import person_names_match
from scripts.entity_canonicalize_rules import (
    merge_sources_into_target, recompute_counters,
)


PROPOSALS_PATH = Path("/tmp/noctua_audit_proposals.json")
REPORT_PATH = Path("/tmp/noctua_audit_report.md")


# ── matcher 1: nicknames + quoted-nickname variants ──────────────────────

def find_nickname_fragments(db) -> list[dict]:
    """For each pair of person entities with nickname-equivalent names,
    propose merging the smaller-mention one into the larger.

    Skips identical pairs (those are already merged by definition).
    """
    persons = db.query(Entity).filter(Entity.type == "person").all()
    proposals: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for i, a in enumerate(persons):
        for b in persons[i + 1:]:
            if a.id == b.id:
                continue
            key = frozenset({a.id, b.id})
            if key in seen_pairs:
                continue
            if not person_names_match(a.name, b.name):
                continue
            # Same exact name? Nothing to do, already deduped.
            if (a.name or "").strip().lower() == (b.name or "").strip().lower():
                continue
            seen_pairs.add(key)
            # Target = more mentions; seeded > auto when tied
            def rank(e):
                return (0 if e.seeded else 1, -(e.mention_count or 0), e.id)
            ranked = sorted([a, b], key=rank)
            target, source = ranked[0], ranked[1]
            proposals.append({
                "matcher": "nickname",
                "type": "person",
                "target": {
                    "id": target.id, "name": target.name,
                    "canonical_id": target.canonical_id,
                    "mentions": target.mention_count or 0,
                    "seeded": bool(target.seeded),
                },
                "sources": [{
                    "id": source.id, "name": source.name,
                    "canonical_id": source.canonical_id,
                    "mentions": source.mention_count or 0,
                    "seeded": bool(source.seeded),
                }],
                "reason": f"nickname-equivalent: {a.name!r} ↔ {b.name!r}",
            })
    return proposals


# ── matcher 2: honorifics ────────────────────────────────────────────────

def find_honorific_fragments(db) -> list[dict]:
    """For each person entity whose name has an honorific (Dr., Rep., Jr.),
    look for other persons matching the stripped form."""
    persons = db.query(Entity).filter(Entity.type == "person").all()
    # Build a quick lookup by lowercase name
    by_name = defaultdict(list)
    for p in persons:
        if p.name:
            by_name[p.name.strip().lower()].append(p)

    proposals: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for p in persons:
        stripped, removed = strip_honorifics(p.name or "")
        if not removed or not stripped:
            continue
        if stripped.strip().lower() == (p.name or "").strip().lower():
            continue
        # Skip merges where the only stripped tokens are generational
        # suffixes (Jr./Sr./II/III). "Rob Bresnahan Jr." vs "Rob Bresnahan"
        # is ambiguous — could be the same person reported with the
        # suffix in one article and without in another, OR a father-son
        # pair. Require human review for these, don't auto-propose.
        if removed_only_generational(removed):
            continue
        candidates = by_name.get(stripped.strip().lower(), [])
        for c in candidates:
            if c.id == p.id:
                continue
            key = frozenset({c.id, p.id})
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            def rank(e):
                return (0 if e.seeded else 1, -(e.mention_count or 0), e.id)
            ranked = sorted([p, c], key=rank)
            target, source = ranked[0], ranked[1]
            proposals.append({
                "matcher": "honorific",
                "type": "person",
                "target": {
                    "id": target.id, "name": target.name,
                    "canonical_id": target.canonical_id,
                    "mentions": target.mention_count or 0,
                    "seeded": bool(target.seeded),
                },
                "sources": [{
                    "id": source.id, "name": source.name,
                    "canonical_id": source.canonical_id,
                    "mentions": source.mention_count or 0,
                    "seeded": bool(source.seeded),
                }],
                "reason": f"honorific-strip: {p.name!r} → {stripped!r}",
            })
    return proposals


# ── matcher 3: campaign district surface forms ───────────────────────────

def find_district_fragments(db, district_code: str) -> list[dict]:
    """Find all location entities whose name matches any surface form of
    the campaign's district, and propose merging them into the seeded
    loc:{state}-{NN} canonical.

    Skips the seeded entity itself.
    """
    if not district_code:
        return []
    seeded_id = f"loc:{district_code.lower()}"
    seeded = (
        db.query(Entity)
        .filter(Entity.canonical_id == seeded_id)
        .first()
    )
    if not seeded:
        # No seeded canonical to merge INTO — skip rather than picking
        # arbitrarily. Caller may want to flag this upstream.
        return []

    locations = (
        db.query(Entity)
        .filter(Entity.type == "location")
        .filter(Entity.id != seeded.id)
        .all()
    )
    sources: list[Entity] = []
    for loc in locations:
        if is_district_surface_form(loc.name or "", district_code):
            sources.append(loc)

    if not sources:
        return []
    return [{
        "matcher": "district",
        "type": "location",
        "target": {
            "id": seeded.id, "name": seeded.name,
            "canonical_id": seeded.canonical_id,
            "mentions": seeded.mention_count or 0,
            "seeded": True,
        },
        "sources": [{
            "id": s.id, "name": s.name,
            "canonical_id": s.canonical_id,
            "mentions": s.mention_count or 0,
            "seeded": bool(s.seeded),
        } for s in sources],
        "reason": f"district surface forms collapse to {seeded.canonical_id!r}",
    }]


# ── report + apply ───────────────────────────────────────────────────────

def write_report(proposals: list[dict], district_code: str) -> None:
    lines: list[str] = []
    lines.append("# Entity canonicalization audit\n")
    lines.append(f"Campaign district: `{district_code or '(unset)'}`\n")
    by_matcher: dict[str, list[dict]] = defaultdict(list)
    for p in proposals:
        by_matcher[p["matcher"]].append(p)
    for matcher in ("nickname", "honorific", "district"):
        items = by_matcher.get(matcher, [])
        lines.append(f"## {matcher} ({len(items)})\n")
        for i, p in enumerate(items, 1):
            t = p["target"]
            seeded_badge = " (seeded)" if t.get("seeded") else ""
            lines.append(f"### {i}. → {t['name']!r}{seeded_badge}  [m={t['mentions']}]")
            lines.append(f"   reason: {p['reason']}")
            for s in p["sources"]:
                lines.append(f"   - merge {s['name']!r} ({s['canonical_id']}, m={s['mentions']}) → target")
            lines.append("")
    REPORT_PATH.write_text("\n".join(lines))


def apply_proposals(db, proposals: list[dict]) -> dict:
    """Apply all proposals via the existing merge helper. Mutates DB.

    The session is configured with autoflush=False, so we explicitly flush
    after each merge before recomputing counters — otherwise the freshly
    redirected mention rows aren't visible to recompute_counters' query
    and the mention_count stays at the old value (we saw PA-08 stuck at
    159 when its actual entity_mentions count was 278).
    """
    overall = defaultdict(int)
    for p in proposals:
        target_id = p["target"]["id"]
        source_ids = [s["id"] for s in p["sources"]]
        stats = merge_sources_into_target(db, source_ids, target_id)
        for k, v in stats.items():
            overall[k] += v
        # Add source names as aliases on target
        target = db.query(Entity).filter(Entity.id == target_id).first()
        if target:
            try:
                cur = json.loads(target.aliases) if target.aliases else []
            except Exception:
                cur = []
            changed = False
            for s in p["sources"]:
                if s["name"] not in cur and s["name"] != target.name:
                    cur.append(s["name"])
                    changed = True
            if changed:
                target.aliases = json.dumps(cur)
            db.flush()  # ← critical: make redirected mentions visible to recompute
            recompute_counters(db, target.id)
    db.commit()
    return dict(overall)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Apply proposals to DB (default: dry-run only).")
    args = parser.parse_args()

    with SessionLocal() as db:
        config = db.query(CampaignConfig).first()
        district_code = (config.district or "").strip() if config else ""

        print(f"Campaign district: {district_code or '(unset)'}")
        print("Scanning entity inventory for fragmentation...")

        proposals: list[dict] = []
        proposals.extend(find_nickname_fragments(db))
        proposals.extend(find_honorific_fragments(db))
        proposals.extend(find_district_fragments(db, district_code))

        # De-dup: if the same (target, source) pair appears via multiple
        # matchers, keep only the first. The matchers run cheap → expensive
        # so the cheaper reason wins on ties (informational only).
        seen_pairs: set[tuple[int, int]] = set()
        deduped: list[dict] = []
        for p in proposals:
            keep = True
            for s in p["sources"]:
                key = (p["target"]["id"], s["id"])
                if key in seen_pairs:
                    keep = False
                    break
            if keep:
                for s in p["sources"]:
                    seen_pairs.add((p["target"]["id"], s["id"]))
                deduped.append(p)
        proposals = deduped

        print(f"\nFound {len(proposals)} merge proposals:")
        for matcher in ("nickname", "honorific", "district"):
            n = sum(1 for p in proposals if p["matcher"] == matcher)
            print(f"  {matcher:12s} {n}")

        PROPOSALS_PATH.write_text(json.dumps(proposals, indent=2))
        write_report(proposals, district_code)
        print(f"\nProposals JSON: {PROPOSALS_PATH}")
        print(f"Report:        {REPORT_PATH}")

        if not args.apply:
            print("\n(dry-run; re-run with --apply to merge)")
            return 0

        print("\nApplying merges...")
        stats = apply_proposals(db, proposals)
        print(f"Merge stats: {dict(stats)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
