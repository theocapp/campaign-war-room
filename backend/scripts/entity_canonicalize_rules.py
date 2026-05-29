"""Rule-based fuzzy canonicalization for the entity graph.

Identifies entities that are clearly the same real-world thing under different
surface forms, and proposes merges to collapse them. Produces a dry-run report
first — the actual merges only happen with --apply on a pre-generated JSON.

Heuristics, by entity type:

  person       Strip honorifics ("Rep. Bresnahan", "Speaker Johnson") and
               suffixes (Jr., Sr., III). After normalization, merge entities
               with identical normalized names.

  location     Strip state suffixes (", PA", "Pennsylvania") and county/city/
               borough qualifiers. Merge identical cores.

  organization Strip "The " prefix and ".com" suffixes. Match acronyms against
               full names when both are present (e.g. "DCCC" ↔ "Democratic
               Congressional Campaign Committee").

  bill         TOPIC-LEVEL merging per user direction. Hardcoded synonym
               groups for the families that fragmented heavily in extraction:
               ACA family (ACA / Affordable Care Act / Obamacare / subsidy
               variants), Medicaid family, Trump tax cut family, STOCK Act
               family. Anything outside these groups is left alone.

Apply phase (--apply):
  - Target entity = the seeded one if any in the group, else the
    highest mention_count
  - entity_mentions are redirected to target (dropping dupes via UNIQUE
    constraint)
  - entity_relations are redirected to target with weight + source_articles
    merged when a duplicate triple already exists; self-relations after
    merge are deleted
  - Source entity rows are deleted
  - Target's mention_count / source_count / first_seen / last_seen are
    recomputed from the surviving mentions

USAGE:
    cd backend && .venv/bin/python scripts/entity_canonicalize_rules.py            # dry-run
    cd backend && .venv/bin/python scripts/entity_canonicalize_rules.py --apply    # apply
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityMention, EntityRelation


PROPOSAL_PATH = Path("/tmp/noctua_canonicalize_proposals.json")
REPORT_PATH = Path("/tmp/noctua_canonicalize_report.md")


# ── Normalization ────────────────────────────────────────────────────────

_PERSON_TITLES = (
    "rep.", "rep", "representative", "sen.", "sen", "senator",
    "gov.", "gov", "governor", "pres.", "pres", "president",
    "vp", "vice president", "speaker", "mayor", "mr.", "mrs.",
    "ms.", "dr.", "congressman", "congresswoman", "former",
    "the", "rev.", "rev",
)

_PERSON_SUFFIXES = (
    " jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v",
)

# US state names — used to avoid merging a state with a same-named county
# (e.g. Wyoming the state ≠ Wyoming County in PA-08).
_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming",
}

# Acronyms whose short form has multiple common meanings — too risky to
# auto-merge with any one full-name match. PPL is the major PA electric
# utility; CHS has dozens of unrelated organizations using it.
_ACRONYM_BLACKLIST = {"ppl", "chs"}


def _normalize_base(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_person(name: str) -> str:
    """Default person normalization: strip titles but **keep** Jr./Sr./III
    suffixes. Jr./Sr. typically distinguishes father from son for political
    families (Tom Kean Sr. ≠ Tom Kean Jr.) — stripping it auto-merges
    unrelated people."""
    s = _normalize_base(name)
    # Repeatedly strip leading titles
    changed = True
    while changed:
        changed = False
        for t in _PERSON_TITLES:
            if s.startswith(t + " "):
                s = s[len(t) + 1:].strip()
                changed = True
    return s


def normalize_person_loose(name: str) -> str:
    """Stricter normalization that DOES strip Jr./Sr./III. Used in a second
    pass to merge auto-discovered "X Jr." into a SEEDED "X" entity — the
    seed list is curated, so we trust it. Without a seeded target, we don't
    merge across the suffix boundary."""
    s = normalize_person(name)
    for suf in _PERSON_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)].strip()
    return s


def normalize_location(name: str) -> str:
    """Strip state suffixes and borough/township qualifiers. Do NOT strip
    `county` / `city` / `district` — those are the level of name collision
    that goes badly (Wyoming state vs Wyoming County PA, New York state vs
    New York City). Reject any strip whose result is a US state name."""
    s = _normalize_base(name)
    # Normalize "pennsylvania" → "pa" in suffix so "Wyoming, Pennsylvania"
    # and "Wyoming, PA" land in the same bucket without losing the suffix.
    s = re.sub(r",\s*pennsylvania\b", ", pa", s)

    # Try stripping the state suffix — only accept if the bare result isn't
    # itself a state. (Prevents "Wyoming, Pennsylvania" → "Wyoming" matching
    # the state entity.)
    candidate = re.sub(r",?\s*(pa|pa-08)\s*$", "", s).strip()
    if candidate and candidate not in _US_STATES:
        s = candidate

    # Strip borough/township only. county/city/district stay attached.
    candidate = re.sub(r"\s+(borough|township)\s*$", "", s).strip()
    if candidate and candidate not in _US_STATES:
        s = candidate

    return s.strip()


def normalize_organization(name: str) -> str:
    s = _normalize_base(name)
    # Strip leading "the"
    if s.startswith("the "):
        s = s[4:].strip()
    # Strip news-site TLDs
    s = re.sub(r"\.(com|net|org)\s*$", "", s)
    return s


# ── Topic families for bills (per user choice: topic-level merging) ─────

_BILL_FAMILIES: dict[str, tuple[str, list[str]]] = {
    # canonical_target_name -> (display, [match_substrings_lowercase])
    "ACA": (
        "Affordable Care Act (family)",
        ["aca", "affordable care", "obamacare"],
    ),
    "MEDICAID": (
        "Medicaid (family)",
        ["medicaid"],
    ),
    "TRUMP_TAX_CUTS": (
        "Trump Tax Cuts (family)",
        ["tcja", "trump tax", "2017 tax cut", "tax cuts and jobs"],
    ),
    "STOCK_ACT": (
        "Stock Trading Ban (family)",
        ["stock act", "stock trading ban", "congressional stock"],
    ),
}


def bill_family_key(name: str) -> str | None:
    """Return the family key if the bill name matches one of the hardcoded
    families, else None (leave the bill alone)."""
    s = _normalize_base(name)
    for key, (_disp, subs) in _BILL_FAMILIES.items():
        for sub in subs:
            if sub in s:
                return key
    return None


# ── Org acronym detection ───────────────────────────────────────────────

def acronym_of(name: str) -> str | None:
    """For an org name like 'Democratic Congressional Campaign Committee',
    return 'DCCC' if and only if it has ≥3 capitalized first letters from
    distinct meaningful words. Filters out trivial cases."""
    # Tokenize on whitespace
    parts = re.findall(r"[A-Z][a-z]*", name)
    if len(parts) < 3:
        return None
    initials = "".join(p[0] for p in parts if p)
    if len(initials) < 3:
        return None
    return initials


# ── Proposal generation ─────────────────────────────────────────────────

def group_entities(entities: list[Entity]) -> dict:
    """Bucket entities by their (type, normalized-name) key. Returns dict of
    {bucket_key: [entity, ...]}.

    Person Jr./Sr. handling: by default we keep the suffix (preserves
    father/son distinction). After bucketing, do a second pass that moves
    an auto-discovered "X Jr." into a seeded "X" bucket if one exists.

    Org acronym handling: a full-name org whose computed acronym matches a
    short-named entity is folded into the same bucket — unless the acronym
    is on the blacklist of multi-meaning short forms.
    """
    buckets: dict[tuple[str, str], list[Entity]] = defaultdict(list)

    # First pass: drop everything into normalized buckets
    org_acronym_index: dict[str, str] = {}
    for e in entities:
        if e.type == "person":
            key = normalize_person(e.name)  # KEEPS Jr./Sr.
        elif e.type == "location":
            key = normalize_location(e.name)
        elif e.type == "organization":
            key = normalize_organization(e.name)
            ac = acronym_of(e.name)
            if ac and ac.lower() not in _ACRONYM_BLACKLIST:
                org_acronym_index[ac.lower()] = key
        elif e.type == "bill":
            fam = bill_family_key(e.name)
            key = fam if fam else _normalize_base(e.name)
        else:
            key = _normalize_base(e.name)
        if not key:
            continue
        buckets[(e.type, key)].append(e)

    # Second pass for persons: try Jr./Sr. matching, but only if the
    # suffix-stripped key matches a SEEDED entity. (Without a seed target,
    # we don't risk merging father with son.)
    seed_loose_index: dict[str, Entity] = {}
    for e in entities:
        if e.type == "person" and e.seeded:
            seed_loose_index[normalize_person_loose(e.name)] = e
    for e in entities:
        if e.type != "person" or e.seeded:
            continue
        strict_key = normalize_person(e.name)
        loose_key = normalize_person_loose(e.name)
        if loose_key == strict_key:
            continue  # No suffix to strip → no Jr./Sr. ambiguity
        seed = seed_loose_index.get(loose_key)
        if not seed:
            continue
        seed_key = normalize_person(seed.name)
        if seed_key == strict_key:
            continue  # Already in same bucket
        # Move e from its strict bucket into the seed's bucket
        src_bucket = buckets[(e.type, strict_key)]
        if e in src_bucket:
            src_bucket.remove(e)
            if not src_bucket:
                del buckets[(e.type, strict_key)]
        buckets[(e.type, seed_key)].append(e)

    # Third pass for orgs: an acronym bucket (e.g. "dccc") gets aliased to
    # the bucket of the full name whose acronym is "DCCC". Blacklisted
    # acronyms are excluded.
    aliases: dict[tuple[str, str], tuple[str, str]] = {}
    for (t, k), _ents in list(buckets.items()):
        if t != "organization":
            continue
        if k in _ACRONYM_BLACKLIST:
            continue
        if k in org_acronym_index and org_acronym_index[k] != k:
            target = ("organization", org_acronym_index[k])
            aliases[(t, k)] = target

    for src, tgt in aliases.items():
        buckets[tgt].extend(buckets[src])
        del buckets[src]

    return buckets


def pick_target(entities: list[Entity]) -> Entity:
    """Pick the canonical entity from a group: prefer seeded, then highest
    mention_count, then lowest id."""
    return sorted(entities, key=lambda e: (not bool(e.seeded), -(e.mention_count or 0), e.id))[0]


def generate_proposals(db) -> list[dict]:
    entities = db.query(Entity).all()
    buckets = group_entities(entities)

    proposals: list[dict] = []
    for (etype, key), group in buckets.items():
        if len(group) < 2:
            continue
        target = pick_target(group)
        sources = [e for e in group if e.id != target.id]
        if not sources:
            continue

        # Build reason
        if etype == "bill" and key in _BILL_FAMILIES:
            reason = f"bill family '{_BILL_FAMILIES[key][0]}'"
            confidence = "high"
        elif etype == "person":
            reason = f"same normalized person name: '{key}'"
            confidence = "high"
        elif etype == "location":
            reason = f"same normalized location: '{key}'"
            confidence = "high"
        elif etype == "organization":
            reason = f"same normalized org name (or acronym match): '{key}'"
            confidence = "high"
        else:
            reason = f"normalized match: '{key}'"
            confidence = "medium"

        proposals.append({
            "type": etype,
            "target": {"id": target.id, "name": target.name, "canonical_id": target.canonical_id,
                       "mentions": target.mention_count or 0, "seeded": bool(target.seeded)},
            "sources": [{"id": e.id, "name": e.name, "canonical_id": e.canonical_id,
                         "mentions": e.mention_count or 0, "seeded": bool(e.seeded)} for e in sources],
            "combined_mentions": sum((e.mention_count or 0) for e in group),
            "reason": reason,
            "confidence": confidence,
        })

    # Sort: biggest impact first (most-mentioned merges at top)
    proposals.sort(key=lambda p: -p["combined_mentions"])
    return proposals


# ── Apply phase ─────────────────────────────────────────────────────────

def merge_sources_into_target(db, source_ids: list[int], target_id: int) -> dict:
    """Merge multiple sources into target in one transaction-aware pass.

    We track the (article_id) and (subject_id, predicate, object_id) sets
    we've already redirected in Python, because SQLAlchemy's pending writes
    aren't visible to subsequent queries within the same session before
    flush — without local dedup, two sources mentioning the same article
    each redirect their mention to target and trip the UNIQUE constraint.
    """
    stats = {"mentions_redirected": 0, "mentions_deduped": 0,
             "relations_redirected": 0, "relations_merged": 0,
             "self_relations_deleted": 0, "entities_removed": 0}

    # Pre-load target's existing mention articles
    target_mention_articles: set[int] = {
        a for (a,) in
        db.query(EntityMention.article_id).filter(EntityMention.entity_id == target_id).all()
    }
    # Pre-load target's existing relation triples (for dedup across sources).
    # Maps (subj, pred, obj) → EntityRelation row.
    target_triples: dict[tuple[int, str, int], EntityRelation] = {}
    for r in db.query(EntityRelation).filter(
        (EntityRelation.subject_id == target_id) | (EntityRelation.object_id == target_id)
    ).all():
        target_triples[(r.subject_id, r.predicate, r.object_id)] = r

    # 1) Mentions across all sources
    source_set = set(source_ids)
    for m in db.query(EntityMention).filter(EntityMention.entity_id.in_(source_set)).all():
        if m.article_id in target_mention_articles:
            db.delete(m)
            stats["mentions_deduped"] += 1
        else:
            m.entity_id = target_id
            target_mention_articles.add(m.article_id)
            stats["mentions_redirected"] += 1

    # 2) Relations: redirect references to source ids → target, dedup on triple key.
    # Fetch every relation that mentions any source as subject or object.
    rels = (
        db.query(EntityRelation)
        .filter(
            (EntityRelation.subject_id.in_(source_set)) | (EntityRelation.object_id.in_(source_set))
        )
        .all()
    )
    for r in rels:
        # After substitution: source ids become target id
        new_s = target_id if r.subject_id in source_set else r.subject_id
        new_o = target_id if r.object_id in source_set else r.object_id
        if new_s == new_o:
            # Self-relation after merge — drop.
            db.delete(r)
            stats["self_relations_deleted"] += 1
            continue
        triple = (new_s, r.predicate, new_o)
        existing = target_triples.get(triple)
        if existing and existing is not r:
            # Combine weight + source_articles into existing, delete this row
            existing.weight = (existing.weight or 0) + (r.weight or 0)
            _merge_source_articles(existing, r)
            # Combine first_seen / last_seen
            if existing.first_seen is None or (r.first_seen and r.first_seen < existing.first_seen):
                existing.first_seen = r.first_seen
            if existing.last_seen is None or (r.last_seen and r.last_seen > existing.last_seen):
                existing.last_seen = r.last_seen
            db.delete(r)
            stats["relations_merged"] += 1
        else:
            # Redirect this row's subject/object to target. Register in the
            # triple map so a sibling source merging the same triple folds in.
            r.subject_id = new_s
            r.object_id = new_o
            target_triples[triple] = r
            stats["relations_redirected"] += 1

    # 3) Add each source's name + existing aliases as aliases on the target.
    # Without this, a future extraction emitting the source surface form
    # (e.g. "Affordable Care Act" after we merged it into the ACA Subsidy
    # Extension target) won't match the target via canonicalize_entity's
    # alias step and will create a fresh duplicate.
    target = db.query(Entity).filter(Entity.id == target_id).one()
    try:
        existing_aliases = set(json.loads(target.aliases or "[]"))
    except Exception:
        existing_aliases = set()
    target_lower = target.name.strip().lower()
    for sid in source_ids:
        src = db.query(Entity).filter(Entity.id == sid).one_or_none()
        if not src:
            continue
        if src.name and src.name.strip().lower() != target_lower:
            existing_aliases.add(src.name.strip())
        try:
            src_aliases = json.loads(src.aliases or "[]")
        except Exception:
            src_aliases = []
        for a in src_aliases:
            if isinstance(a, str) and a.strip() and a.strip().lower() != target_lower:
                existing_aliases.add(a.strip())
    target.aliases = json.dumps(sorted(existing_aliases))

    # 4) Delete the source entity rows
    for sid in source_ids:
        src = db.query(Entity).filter(Entity.id == sid).one_or_none()
        if src:
            db.delete(src)
            stats["entities_removed"] += 1

    return stats


def _merge_source_articles(existing: EntityRelation, source_rel: EntityRelation) -> None:
    try:
        a = json.loads(existing.source_articles or "[]")
        b = json.loads(source_rel.source_articles or "[]")
    except Exception:
        return
    merged = list(dict.fromkeys(a + b))[-50:]
    existing.source_articles = json.dumps(merged)


def recompute_counters(db, target_id: int) -> None:
    target = db.query(Entity).filter(Entity.id == target_id).one()
    mentions = db.query(EntityMention).filter(EntityMention.entity_id == target_id).all()
    target.mention_count = len(mentions)
    target.source_count = len({m.article_id for m in mentions})
    # first_seen / last_seen recompute requires joining to source_items —
    # the existing values across the kept + merged mentions are already
    # the union, so the canonical entity's existing first_seen/last_seen
    # may be stale on the lower bound. Pull from articles to be safe.
    from app.models import SourceItem
    rows = (
        db.query(SourceItem.published_at)
        .join(EntityMention, EntityMention.article_id == SourceItem.id)
        .filter(EntityMention.entity_id == target_id)
        .filter(SourceItem.published_at.isnot(None))
        .all()
    )
    if rows:
        dates = [r[0] for r in rows if r[0]]
        if dates:
            target.first_seen = min(dates)
            target.last_seen = max(dates)


def apply_proposals(db, proposals: list[dict]) -> dict:
    grand = {"merges_applied": 0, "entities_removed": 0, "mentions_redirected": 0,
             "mentions_deduped": 0, "relations_redirected": 0, "relations_merged": 0,
             "self_relations_deleted": 0}

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


# ── Reporting ───────────────────────────────────────────────────────────

def write_report(proposals: list[dict]) -> None:
    lines: list[str] = []
    lines.append(f"# Canonicalization proposals ({len(proposals)} merges)\n")
    if not proposals:
        lines.append("No duplicate entities detected.\n")
    else:
        total_merge_count = sum(len(p["sources"]) for p in proposals)
        total_combined = sum(p["combined_mentions"] for p in proposals)
        lines.append(f"- Total entities to be removed: **{total_merge_count}**")
        lines.append(f"- Combined mention impact: **{total_combined}**")
        lines.append("")
        lines.append("Each entry below shows the canonical target (kept) and the duplicates")
        lines.append("that will be merged into it. Mention counts are pre-merge.")
        lines.append("")

        for i, p in enumerate(proposals, 1):
            tgt = p["target"]
            tgt_tag = "[seed]" if tgt["seeded"] else "[auto]"
            lines.append(f"## {i}. {p['type']} — {p['reason']} ({p['confidence']})")
            lines.append("")
            lines.append(f"**KEEP:** {tgt_tag} `{tgt['canonical_id']}` — **{tgt['name']}** ({tgt['mentions']} mentions)")
            lines.append("")
            lines.append("**Merge into target:**")
            for s in p["sources"]:
                s_tag = "[seed]" if s["seeded"] else "[auto]"
                lines.append(f"- {s_tag} `{s['canonical_id']}` — {s['name']} ({s['mentions']} mentions)")
            lines.append("")

    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")


def write_proposals_json(proposals: list[dict]) -> None:
    PROPOSAL_PATH.write_text(json.dumps(proposals, indent=2))
    print(f"Proposals written: {PROPOSAL_PATH}")


# ── Entry point ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Apply the proposals from /tmp/noctua_canonicalize_proposals.json")
    parser.add_argument("--regenerate", action="store_true",
                        help="Force a fresh proposal pass even in --apply mode")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.apply and not args.regenerate:
            if not PROPOSAL_PATH.exists():
                print(f"No proposals file at {PROPOSAL_PATH} — run without --apply first.")
                return
            proposals = json.loads(PROPOSAL_PATH.read_text())
            print(f"Applying {len(proposals)} merges from {PROPOSAL_PATH}...\n")
            grand = apply_proposals(db, proposals)
            print()
            print("=" * 60)
            for k, v in grand.items():
                print(f"  {k:30s} {v}")
        else:
            print("Generating proposals (dry-run)...\n")
            proposals = generate_proposals(db)
            print(f"Found {len(proposals)} merge groups")
            write_proposals_json(proposals)
            write_report(proposals)
            if proposals[:5]:
                print()
                print("TOP 5 PREVIEW:")
                for p in proposals[:5]:
                    tgt = p["target"]
                    print(f"  KEEP {tgt['canonical_id']} ({tgt['name']}, {tgt['mentions']} mentions)")
                    for s in p["sources"][:3]:
                        print(f"    + merge {s['canonical_id']} ({s['name']}, {s['mentions']} mentions)")
                    if len(p["sources"]) > 3:
                        print(f"    + {len(p['sources']) - 3} more...")
    finally:
        db.close()


if __name__ == "__main__":
    main()
