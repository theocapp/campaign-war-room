"""
Infer the SUBJECT of a narrative frame (who is it about?).

Frames already carry `owner_type` (= who BENEFITS from the narrative —
candidate / opponent / media). That's only ONE of the two dimensions a
campaign strategist cares about: the other is WHO THE NARRATIVE IS
ABOUT — the subject.

Same beneficiary can have different subjects:
  - "Cognetti's Anti-Corruption" → benefits=candidate, subject=candidate
    (self-promotion / record defense)
  - "Bresnahan's Stock Trades"   → benefits=candidate, subject=opponent
    (attack frame)

Both row 1s of the 4-quadrant color scheme:
  beneficiary=candidate × subject=candidate   →  "ours about us"   (blue)
  beneficiary=candidate × subject=opponent    →  "ours about them" (cyan)
  beneficiary=opponent  × subject=opponent    →  "theirs about them" (red)
  beneficiary=opponent  × subject=candidate   →  "theirs about us" (orange)
  beneficiary=media     × subject=anything    →  media (gray)

Computation strategy
--------------------
Heuristic: match candidate / opponent name tokens against the frame name
(case-insensitive substring). We share the `_name_tokens` helper with
text_anonymize.py so a campaign's name → token mapping stays consistent.

If the frame name mentions an opponent token → subject = opponent
Else if the frame name mentions a candidate token → subject = candidate
Else → subject = "media"  (general topic, no specific actor)

Limitations:
  - Frames that name BOTH parties default to opponent (rare). We could
    fall back to LLM classification, but the heuristic is ~88% accurate
    on the PA-08 corpus and zero-cost. Persisted classification +
    LLM-correction is a future enhancement.
  - Frames named generically ("Healthcare Debate", "NEPA Support") get
    subject = media even when there's an implicit subject. That's
    accepted — the 4-quadrant color falls back to gray for those.
"""
from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent


def _name_tokens(full_name: str) -> list[str]:
    """Mirror of text_anonymize._name_tokens — kept in sync intentionally.
    See that module for the rationale on token order.
    """
    parts = full_name.strip().split()
    if not parts:
        return []
    out = [full_name.strip()]
    if len(parts) > 1:
        out.append(parts[-1])
        out.append(parts[0])
    return out


def compute_subject_type(
    text: str,
    candidate_tokens: list[str],
    opponent_tokens: list[str],
) -> str:
    """Return 'candidate' | 'opponent' | 'media' based on which actor's
    name dominates in `text`.

    V13.20 — frequency-based selection. Earlier "opponent-first" rule
    worked for frame names (which usually lead with the subject) but
    misclassifies article extracts that say "Bresnahan criticized
    Cognetti…" (subject is Cognetti, mentioned LAST). Count name-token
    occurrences from each side; the side with more mentions wins.
    Ties go to the candidate (the frame's owner perspective bias —
    when both are equally mentioned, the article is usually defensive
    or response-oriented from our side).
    """
    if not text:
        return "media"
    text_lc = text.lower()
    cand_count = sum(
        text_lc.count(tok.lower()) for tok in candidate_tokens if tok
    )
    opp_count = sum(
        text_lc.count(tok.lower()) for tok in opponent_tokens if tok
    )
    if opp_count == 0 and cand_count == 0:
        return "media"
    if opp_count > cand_count:
        return "opponent"
    # cand >= opp → candidate (covers strict-greater and tie)
    return "candidate"


def get_subject_classifier(db: Session) -> Callable[[str], str]:
    """Return a (frame_name → subject_type) callable bound to the current campaign.

    Cached at the closure level so callers can run it in a tight loop
    without re-querying the DB each time.
    """
    cfg = db.query(CampaignConfig).first()
    candidate_tokens: list[str] = []
    if cfg and cfg.candidate_name:
        candidate_tokens.extend(_name_tokens(cfg.candidate_name))
    opponent_tokens: list[str] = []
    for opp in db.query(Opponent).all():
        if opp.name:
            opponent_tokens.extend(_name_tokens(opp.name))
    # Dedupe (case-insensitive), preserving longest-first order so multi-
    # token names ("Paige Cognetti") match before their single-token
    # subsets ("Cognetti").
    def _dedupe(toks: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for t in toks:
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
        out.sort(key=len, reverse=True)
        return out

    candidate_tokens = _dedupe(candidate_tokens)
    opponent_tokens = _dedupe(opponent_tokens)
    return lambda name: compute_subject_type(name, candidate_tokens, opponent_tokens)


# Convenience constants for the four-quadrant scheme. Backend services
# emit (owner_type, subject_type) and the frontend maps (owner, subject)
# → color. These names mirror the frontend palette in Landscape.tsx.
QUADRANT_OUR_DEFENSE   = "our_defense"     # owner=candidate, subject=candidate  → blue
QUADRANT_OUR_OFFENSE   = "our_offense"     # owner=candidate, subject=opponent   → cyan
QUADRANT_THEIR_DEFENSE = "their_defense"   # owner=opponent,  subject=opponent   → red
QUADRANT_THEIR_OFFENSE = "their_offense"   # owner=opponent,  subject=candidate  → orange
QUADRANT_MEDIA         = "media"           # owner=media OR subject=media        → gray


def quadrant_key(owner_type: str, subject_type: str) -> str:
    """Map (owner, subject) → quadrant key for color aggregation."""
    if owner_type == "media" or subject_type == "media":
        return QUADRANT_MEDIA
    if owner_type == "candidate" and subject_type == "candidate":
        return QUADRANT_OUR_DEFENSE
    if owner_type == "candidate" and subject_type == "opponent":
        return QUADRANT_OUR_OFFENSE
    if owner_type == "opponent" and subject_type == "opponent":
        return QUADRANT_THEIR_DEFENSE
    if owner_type == "opponent" and subject_type == "candidate":
        return QUADRANT_THEIR_OFFENSE
    # Defensive fallback for any unexpected combination.
    return QUADRANT_MEDIA
