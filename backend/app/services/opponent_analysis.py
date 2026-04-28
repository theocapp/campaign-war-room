"""
Opponent analysis service.

Extracts opponent claims, attacks, and promises at the sentence level.
Deduplicates against existing OpponentActivity rows.
"""
import re
from sqlalchemy.orm import Session
from app.models import Opponent, OpponentActivity, SourceItem

# ── Sentence-level classifiers ────────────────────────────────────────────────

_ATTACK_MARKERS = {
    "false", "lie", "lied", "lying", "wrong", "mislead", "misleading",
    "distort", "fabricat", "misrepresent", "defund", "accused", "accuses",
    "dangerous", "reckless", "failed", "failure", "attack", "smear",
    "dishonest", "corrupt", "flip-flop",
}
_CLAIM_MARKERS = {
    "says", "said", "claims", "claimed", "according to", "stated",
    "announced", "argues", "argued", "declared", "insisted", "maintains",
    "contends", "alleges", "asserts", "told reporters",
}
_PROMISE_MARKERS = {
    "promises", "promised", "pledged", "pledge", "committed to", "vows",
    "vowed", "will ensure", "will fight", "will deliver", "plans to",
    "proposes", "proposed",
}

_THEME_KEYWORDS: dict[str, list[str]] = {
    "public safety / law and order": ["crime", "police", "safety", "defund", "officer", "patrol"],
    "housing": ["rent", "housing", "afford", "tenant", "landlord", "evict"],
    "taxes / fiscal responsibility": ["tax", "budget", "fiscal", "spending", "deficit", "cost"],
    "education": ["school", "education", "classroom", "student", "teacher"],
    "infrastructure": ["road", "pothole", "infrastructure", "repair", "sidewalk"],
    "ethics / corruption": ["corruption", "conflict of interest", "pac", "donation", "scandal"],
    "economy / jobs": ["job", "employment", "business", "economy", "wage"],
    "environment": ["climate", "environment", "pollution", "clean"],
    "immigration": ["immigration", "border", "immigrant", "undocumented"],
}


def _split_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if s.strip()]


def _classify_sentence(sentence: str, opponent_name: str) -> dict | None:
    """
    If sentence mentions the opponent, classify it as attack / claim / promise.
    Returns a classification dict or None if no opponent mention.
    """
    lower = sentence.lower()
    if opponent_name.lower() not in lower and not any(
        part.lower() in lower for part in opponent_name.split()
    ):
        return None

    is_attack = any(m in lower for m in _ATTACK_MARKERS)
    is_claim = any(m in lower for m in _CLAIM_MARKERS)
    is_promise = any(m in lower for m in _PROMISE_MARKERS)

    if not (is_attack or is_claim or is_promise):
        return None

    return {
        "is_attack": is_attack,
        "is_claim": is_claim,
        "is_promise": is_promise,
        "sentence": sentence,
    }


def _detect_theme(text: str) -> str | None:
    lower = text.lower()
    for theme, keywords in _THEME_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return theme
    return None


def _extract_activities(full_text: str, opponent_name: str) -> list[dict]:
    """
    Return a list of activity dicts extracted from sentences in full_text.
    Each dict has: claim, attack, promise, contradiction_note, repeated_theme.
    """
    sentences = _split_sentences(full_text)
    results: list[dict] = []

    for sentence in sentences:
        classified = _classify_sentence(sentence, opponent_name)
        if not classified:
            continue

        activity: dict = {
            "claim": None,
            "attack": None,
            "promise": None,
            "contradiction_note": None,
            "repeated_theme": _detect_theme(sentence),
        }

        if classified["is_attack"]:
            activity["attack"] = sentence[:500]
        if classified["is_claim"]:
            activity["claim"] = sentence[:500]
        if classified["is_promise"]:
            activity["promise"] = sentence[:300]

        results.append(activity)

    # Deduplicate identical sentences
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        key = (r["claim"] or "") + (r["attack"] or "") + (r["promise"] or "")
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


def analyze_source_for_opponents(db: Session, source_item: SourceItem) -> list[OpponentActivity]:
    opponents = db.query(Opponent).all()
    full_text = f"{source_item.title}. {source_item.raw_text or ''}"
    created: list[OpponentActivity] = []

    for opponent in opponents:
        activities = _extract_activities(full_text, opponent.name)

        for act_data in activities:
            # Dedup: skip if we already have an activity for this source + opponent + same attack/claim text
            existing = (
                db.query(OpponentActivity)
                .filter(
                    OpponentActivity.opponent_id == opponent.id,
                    OpponentActivity.source_item_id == source_item.id,
                )
                .filter(
                    (OpponentActivity.attack == act_data["attack"])
                    | (OpponentActivity.claim == act_data["claim"])
                )
                .first()
            )
            if existing:
                continue

            activity = OpponentActivity(
                opponent_id=opponent.id,
                source_item_id=source_item.id,
                **act_data,
            )
            db.add(activity)
            created.append(activity)

    db.commit()
    return created
