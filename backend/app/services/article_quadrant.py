"""
Resolve (owner_type, subject_type) for individual articles.

Most of the campaign UI colors articles by the 4-quadrant scheme (see
`subject_classifier.quadrant_key` + the frontend's `quadrantColor.ts`).
That scheme expects two axes per item:

  owner_type   — whose side benefits from / authored this piece
  subject_type — who the piece is ABOUT

`NarrativeFrame` rows carry both axes, but raw `SourceItem` rows do not.
For dots/pins that surface in places like the Timeline's "top moments by
market impact" list, we need to derive the pair per-article.

Cascade (highest signal → lowest)
---------------------------------
1. **Highest-confidence narrative-frame match.** If the article was matched
   to one or more frames, take the frame with the highest `confidence` and
   adopt its `owner_type`/`subject_type`. NULL `subject_type` is filled by
   the existing name-based heuristic in `subject_classifier`. When the
   classifier returns "media" but the frame's owner is partisan, default
   subject to owner (self-promotion — defensible for ambiguously-named
   frames like "NEPA Support" that are clearly campaign-owned).

2. **`source_owner_type`.** Ingestion sometimes tags posts with an explicit
   owner label (`candidate_statement`, `opponent_statement`, `media`).
   When set, that's authoritative. Subject defaults to owner.

3. **`perspective` (V13.21).** Per-article tone classification: pro_candidate
   / pro_opponent / neutral. Maps to owner=candidate/opponent/media with
   subject = owner.

4. **Default.** Both owner and subject = "media" (neutral gray).

WHY this lives here, not in `article_perspective.py`
----------------------------------------------------
`article_perspective.py` populates a SINGLE-axis label (lean) and runs at
ingestion. This helper composes the lean with frame matches + ingestion
labels to produce the TWO-axis pair, at READ time. It's a presentation
concern, not a data-extraction concern, so it sits alongside other
read-time services (source_display.py, etc.).
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.subject_classifier import get_subject_classifier


# Type alias for clarity: ("candidate" | "opponent" | "media", same).
QuadrantPair = tuple[str, str]

_DEFAULT: QuadrantPair = ("media", "media")


def _from_source_owner_type(sot: str | None) -> str | None:
    """Map ingestion's `source_owner_type` to a 3-value owner. None if the
    label is uninformative (unclear, community/manual, party/outside-group
    statements that need extra resolution we don't do here)."""
    if not sot:
        return None
    s = sot.strip().lower()
    if s == "candidate_statement":
        return "candidate"
    if s == "opponent_statement":
        return "opponent"
    if s == "media":
        return "media"
    return None


def _from_perspective(persp: str | None) -> str | None:
    """Map `perspective` to a 3-value owner (lean → owner approximation)."""
    if not persp:
        return None
    p = persp.strip().lower()
    if p == "pro_candidate":
        return "candidate"
    if p == "pro_opponent":
        return "opponent"
    if p == "neutral":
        return "media"
    return None


def _fallback_pair(item: SourceItem) -> QuadrantPair:
    """No-frame-match path: source_owner_type → perspective → media."""
    owner = _from_source_owner_type(item.source_owner_type)
    if owner is None:
        owner = _from_perspective(item.perspective)
    if owner is None:
        return _DEFAULT
    # Without a frame to anchor subject, default to owner (self-referential).
    # A pro-candidate article with no frame match is more likely to be
    # self-promotion than an attack — and "Pro-Cognetti" is still a far
    # better label than "Neutral" for the user.
    return (owner, owner)


def quadrants_for_articles(
    items: Iterable[SourceItem],
    db: Session,
) -> dict[int, QuadrantPair]:
    """Resolve (owner_type, subject_type) for every article in `items`.

    Returns a dict keyed by `SourceItem.id`. Articles with no signal at
    all land on `("media", "media")` so callers can blindly look up every
    id and get a defensible default.

    Batched: ONE query for frame matches across all article ids, then
    in-memory winner selection per article.
    """
    items_by_id: dict[int, SourceItem] = {a.id: a for a in items if a.id is not None}
    if not items_by_id:
        return {}

    classify_subject = get_subject_classifier(db)

    # Single query: all (article_id, frame.owner_type, frame.subject_type,
    # frame.name, mention.confidence) rows for the requested articles.
    # Ordered so the first row per article_id is the highest-confidence match.
    rows = (
        db.query(
            NarrativeFrameMention.source_item_id,
            NarrativeFrame.owner_type,
            NarrativeFrame.subject_type,
            NarrativeFrame.name,
            NarrativeFrameMention.confidence,
        )
        .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
        .filter(NarrativeFrameMention.source_item_id.in_(items_by_id.keys()))
        .order_by(
            NarrativeFrameMention.source_item_id.asc(),
            NarrativeFrameMention.confidence.desc().nullslast(),
        )
        .all()
    )

    # Group rows by article (rows are already sorted by confidence DESC).
    # We need ALL matches per article (not just the top one) because the
    # subject-axis tiebreaker below looks across the full set.
    from collections import defaultdict
    per_article: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for aid, owner, subject, name, _conf in rows:
        eff_owner = owner or "media"
        if not subject:
            try:
                subject = classify_subject(name or "")
            except Exception:
                subject = "media"
        # If the classifier couldn't identify a subject but the owner is
        # partisan, treat the frame as self-referential (subject = owner).
        # Without this, frames like "NEPA Support" (owner=candidate, name
        # mentions neither side) would never count as self-axis evidence.
        if subject == "media" and eff_owner in ("candidate", "opponent"):
            subject = eff_owner
        per_article[aid].append((eff_owner, subject))

    winners: dict[int, QuadrantPair] = {}
    for aid, matches in per_article.items():
        # Top match (highest-confidence) anchors the OWNER axis. For most
        # articles every match is candidate-owned (the campaign owns nearly
        # all frames), so this is uncontroversial.
        top_owner, top_subject = matches[0]

        if top_owner in ("candidate", "opponent"):
            # SELF-AXIS WINS for partisan-owned articles. A mixed tweet that
            # leads with "Proud to have @PennaNurses support" but also
            # mentions "Bresnahan voted to gut Medicaid" matches BOTH a
            # self-promo frame AND an attack frame. The article's primary
            # purpose is self-promotion (it's a campaign post on the
            # candidate's own account); the attack is a contrast beat, not
            # the headline. So: if ANY frame match treats the article as
            # self-referential (subject == owner), bucket it as
            # self-promotion. Only flip to subject=opponent when EVERY
            # match is other-axis — i.e., it's a pure attack post.
            has_self_match = any(o == top_owner and s == top_owner for o, s in matches)
            subject = top_owner if has_self_match else top_subject
        else:
            # Owner=media: no self/other distinction, take top match's subject.
            subject = top_subject

        winners[aid] = (top_owner, subject)

    # Fill in articles with no frame match using the fallback cascade.
    out: dict[int, QuadrantPair] = {}
    for aid, item in items_by_id.items():
        out[aid] = winners.get(aid) or _fallback_pair(item)
    return out


def quadrant_for_article(item: SourceItem, db: Session) -> QuadrantPair:
    """Single-article convenience wrapper around `quadrants_for_articles`.

    Prefer the batch version when you have more than one article — it runs
    one SQL query instead of N.
    """
    return quadrants_for_articles([item], db).get(item.id, _DEFAULT)
