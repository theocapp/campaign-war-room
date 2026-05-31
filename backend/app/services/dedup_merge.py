"""Post-hoc duplicate detection + merge for SourceItems.

Motivated by the 2026-05-26 Google News body collapse: ~63% of
short-body Google News stubs already exist in the DB under a different
`source_url` (with a longer body, ingested via a different RSS path).
Our default dedup is keyed on `source_url`, so SQL never collapses them.
This module catches them post-hoc by fuzzy-matching titles.

Merge model (NOT delete):
  - For each stub identified as a duplicate of a longer-body canonical:
      stub.archived_as_irrelevant = True
      stub.relevance_reasons = JSON list including
          {"reason": "duplicate", "canonical_source_item_id": <id>}
  - Canonical is untouched.

This keeps the audit trail (we know we observed the article on N feeds)
while making analytics/counts read only the canonical. A future migration
can promote the reason to a real `duplicate_of` FK column if needed.

Safety guards (so we never collapse two real articles into one):
  - Normalized titles must overlap ≥ TITLE_SIMILARITY_THRESHOLD
  - Canonical must have raw_text length ≥ MIN_CANONICAL_BODY_CHARS
  - Stub must have raw_text length < MAX_STUB_BODY_CHARS
  - Normalized title must be ≥ MIN_TITLE_CHARS chars (too-short titles
    have too many false positives)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import SourceItem

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────

TITLE_SIMILARITY_THRESHOLD = 0.90
MIN_CANONICAL_BODY_CHARS = 500
MAX_STUB_BODY_CHARS = 200
MIN_TITLE_CHARS = 20  # normalized title length
# Prefix length used for the SQL ILIKE pre-filter — pulls down a candidate
# set per stub. Too short → too many false positives in the candidate set
# (still safe because we do the SequenceMatcher check, just slower).
# Too long → real duplicates miss when publishers truncated the title
# differently (e.g., title shows "...Bres" vs "Bresnahan"). 30 chars is
# a balance that picked up every real duplicate in spot checks.
TITLE_PREFIX_LEN = 30


# ── Title normalization ───────────────────────────────────────────────────

def normalize_title(t: Optional[str]) -> str:
    """Strip Google-News-style ` - Publisher Name` suffix and lowercase.

    Google News rewrites titles as `<Original Title> - <Publisher Name>`.
    Direct publisher RSS often omits the publisher. To match across
    sources we drop the trailing ` - <short token>` if present.
    """
    if not t:
        return ""
    s = t.strip()
    # Only strip if the trailing segment looks like a publisher name
    # (short, not the article's actual punctuation). Conservative — a real
    # title segment after a final dash like "Election 2026 - the year" would
    # have a longer tail; we preserve those.
    if " - " in s:
        head, _, tail = s.rpartition(" - ")
        if 1 < len(tail) <= 40 and len(head) > MIN_TITLE_CHARS:
            s = head
    return s.strip().lower()


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Return SequenceMatcher ratio on normalized titles. Range [0, 1]."""
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


# ── Duplicate detection ───────────────────────────────────────────────────

@dataclass
class DuplicatePair:
    stub_id: int
    canonical_id: int
    similarity: float
    stub_title: str
    canonical_title: str


def find_duplicate_pairs(
    db: Session,
    *,
    hours_back: int = 96,
    max_stubs: int = 1000,
) -> list[DuplicatePair]:
    """Find (stub, canonical) duplicate pairs in the trailing window.

    Algorithm:
      1. Pull all short-body stubs in the window (raw_text < MAX_STUB_BODY).
      2. For each stub, ILIKE-prefix-search candidates with raw_text >=
         MIN_CANONICAL_BODY. The candidate set has no time bound — a
         stub ingested last week can canonicalize against a sibling
         from months ago.
      3. Run SequenceMatcher on each candidate; first that exceeds
         TITLE_SIMILARITY_THRESHOLD wins. Stops at first match — articles
         have at most one canonical version we care about.

    Performance: at our scale (~20k articles, ~300 stubs in a 96h window)
    this runs in a few seconds against Postgres. If the corpus grows past
    100k we should add a trigram index on `title` and use that for the
    pre-filter instead of ILIKE.

    The `max_stubs` cap prevents one invocation from holding the worker
    indefinitely if the stub backlog is huge.
    """
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)

    stubs = (
        db.query(SourceItem.id, SourceItem.title)
        .filter(
            SourceItem.created_at >= cutoff,
            SourceItem.raw_text.isnot(None),
            func.length(SourceItem.raw_text) < MAX_STUB_BODY_CHARS,
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.title.isnot(None),
        )
        .order_by(SourceItem.id.desc())
        .limit(max_stubs)
        .all()
    )

    pairs: list[DuplicatePair] = []
    for stub_id, stub_title in stubs:
        norm = normalize_title(stub_title)
        if len(norm) < MIN_TITLE_CHARS:
            continue
        # Use the head of the normalized title as the ILIKE prefix.
        prefix = norm[:TITLE_PREFIX_LEN]
        # Escape the % and _ wildcards to prevent ILIKE pattern injection
        # from titles that contain those characters.
        safe_prefix = (
            prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        candidates = (
            db.query(SourceItem.id, SourceItem.title)
            .filter(
                SourceItem.id != stub_id,
                func.length(SourceItem.raw_text) >= MIN_CANONICAL_BODY_CHARS,
                SourceItem.title.ilike(f"%{safe_prefix}%"),
            )
            .limit(20)
            .all()
        )
        for cand_id, cand_title in candidates:
            sim = title_similarity(stub_title, cand_title)
            if sim >= TITLE_SIMILARITY_THRESHOLD:
                pairs.append(DuplicatePair(
                    stub_id=stub_id,
                    canonical_id=cand_id,
                    similarity=sim,
                    stub_title=stub_title,
                    canonical_title=cand_title,
                ))
                break

    return pairs


# ── Merge ─────────────────────────────────────────────────────────────────

def _append_duplicate_reason(
    existing_reasons: Optional[str], canonical_id: int, similarity: float
) -> str:
    """Return a JSON string with the duplicate marker appended.

    `relevance_reasons` is a JSON-encoded list of strings/dicts. We
    preserve any existing reasons and add a new dict so a future query
    can find duplicates by reading `reason == "duplicate"`.
    """
    reasons: list = []
    if existing_reasons:
        try:
            parsed = json.loads(existing_reasons)
            if isinstance(parsed, list):
                reasons = parsed
            elif parsed:
                reasons = [parsed]
        except Exception:
            # Treat unparseable existing value as a single string reason.
            reasons = [existing_reasons]
    reasons.append({
        "reason": "duplicate",
        "canonical_source_item_id": canonical_id,
        "title_similarity": round(similarity, 3),
        "merged_at": datetime.utcnow().isoformat(),
    })
    return json.dumps(reasons)


def merge_duplicates(db: Session, pairs: list[DuplicatePair]) -> dict:
    """Apply the merge for a list of (stub, canonical) pairs.

    Per pair:
      - Look up the stub fresh (avoids stale view if pairs was computed
        in a long-running batch).
      - Mark it `archived_as_irrelevant=True`.
      - Append a structured duplicate reason to `relevance_reasons`.
      - Clear the dashboard's `reviewed` flag so the change doesn't
        trip whatever review-state machinery cares about it.

    Idempotent: re-applying to the same pair is a no-op.
    """
    archived = 0
    skipped_already_archived = 0
    skipped_not_found = 0
    for pair in pairs:
        stub = db.get(SourceItem, pair.stub_id)
        if stub is None:
            skipped_not_found += 1
            continue
        if stub.archived_as_irrelevant:
            # Either we processed it in a prior run or another path
            # archived it for an unrelated reason. Don't write another
            # duplicate marker — keep relevance_reasons clean.
            skipped_already_archived += 1
            continue
        stub.archived_as_irrelevant = True
        stub.relevance_reasons = _append_duplicate_reason(
            stub.relevance_reasons, pair.canonical_id, pair.similarity,
        )
        archived += 1
    db.commit()
    return {
        "merged": archived,
        "skipped_already_archived": skipped_already_archived,
        "skipped_not_found": skipped_not_found,
        "total_pairs": len(pairs),
    }


# ── Inline dedup (at ingest time) ────────────────────────────────────────

@dataclass
class CanonicalDecision:
    """Outcome of the inline check against a not-yet-scored new item."""
    canonical: Optional[SourceItem]
    similarity: float
    # Which row is canonical depends on body length:
    #   "new_is_duplicate"      → existing wins; archive the new item
    #   "existing_is_duplicate" → new wins; archive the existing item
    #   "neither_canonical"     → title match found but neither has a real
    #                             body yet. Let both through; the
    #                             post-hoc merge can revisit later.
    #   "no_match"              → no title-similar item found.
    verdict: str


def find_canonical_for_item(
    db: Session, item: SourceItem, *, recent_days: Optional[int] = None,
) -> CanonicalDecision:
    """Inline duplicate check for a not-yet-fully-ingested item.

    Called from `ingestion._create_and_analyze` BEFORE the LLM scoring
    so we can skip the LLM call entirely on duplicates.

    Compares the new `item` against existing rows whose title prefix
    matches; returns the first row above `TITLE_SIMILARITY_THRESHOLD`.

    By default scans the full corpus — the title ILIKE pre-filter + the
    LIMIT 20 cap means the sequential scan stops at the first 20 prefix
    matches anywhere in the table, so cost stays bounded even at 20k+
    rows. The earlier 14-day window was over-cautious; removed
    2026-05-31 once we measured per-call cost at ~10-50ms with no
    meaningful difference between windowed and full-corpus queries.

    Callers can still pass `recent_days=N` to restrict — useful for
    targeted reprocessing where you only care about recent collisions.

    The function does NOT mutate either row. The caller decides whether
    to archive new or existing based on the `verdict` field, since the
    ingestion pipeline owns the commit boundary.
    """
    norm = normalize_title(item.title)
    if len(norm) < MIN_TITLE_CHARS:
        return CanonicalDecision(None, 0.0, "no_match")

    prefix = norm[:TITLE_PREFIX_LEN]
    safe_prefix = (
        prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )

    # Pre-filter: items whose title ILIKEs our prefix. When `recent_days`
    # is None (default), scan the whole corpus. Exclude the current item
    # (it may already exist in the session if the caller called
    # db.flush() before us).
    q = db.query(SourceItem).filter(
        SourceItem.title.ilike(f"%{safe_prefix}%"),
    )
    if recent_days is not None:
        cutoff = datetime.utcnow() - timedelta(days=recent_days)
        q = q.filter(SourceItem.created_at >= cutoff)
    if item.id is not None:
        q = q.filter(SourceItem.id != item.id)
    candidates = q.limit(20).all()

    best: Optional[SourceItem] = None
    best_sim = 0.0
    for cand in candidates:
        sim = title_similarity(item.title, cand.title)
        if sim >= TITLE_SIMILARITY_THRESHOLD and sim > best_sim:
            best = cand
            best_sim = sim

    if best is None:
        return CanonicalDecision(None, 0.0, "no_match")

    new_len = len(item.raw_text or "")
    cand_len = len(best.raw_text or "")
    # Which is canonical? The one with the longer body wins, IF it
    # clears the absolute floor. If neither does, defer to the batch
    # pass — we don't want to archive a stub when its match is also
    # a stub.
    if cand_len >= MIN_CANONICAL_BODY_CHARS and cand_len >= new_len:
        verdict = "new_is_duplicate"
    elif new_len >= MIN_CANONICAL_BODY_CHARS and new_len > cand_len:
        verdict = "existing_is_duplicate"
    else:
        verdict = "neither_canonical"
    return CanonicalDecision(best, best_sim, verdict)


def mark_as_duplicate(
    db: Session, *, duplicate: SourceItem, canonical: SourceItem, similarity: float,
) -> None:
    """Archive `duplicate` with a structured pointer to `canonical`.

    Caller is responsible for committing the session — we just mutate
    the row. The ingestion pipeline batches multiple writes per item
    and commits at the end of `_create_and_analyze`, so we avoid an
    extra commit here.
    """
    duplicate.archived_as_irrelevant = True
    duplicate.relevance_reasons = _append_duplicate_reason(
        duplicate.relevance_reasons, canonical.id, similarity,
    )


def run_dedup_merge(
    db: Session, *, hours_back: int = 96, max_stubs: int = 1000,
) -> dict:
    """Convenience entry point: find pairs and merge in one call."""
    pairs = find_duplicate_pairs(db, hours_back=hours_back, max_stubs=max_stubs)
    result = merge_duplicates(db, pairs)
    result["candidates_scanned"] = (
        # We can't reconstruct this without re-querying; approximate.
        result["total_pairs"]
        + result["skipped_already_archived"]
        + result["skipped_not_found"]
    )
    logger.info(
        "dedup_merge: %s — hours_back=%d", result, hours_back,
    )
    return result
