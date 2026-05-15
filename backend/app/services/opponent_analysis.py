"""
Opponent analysis service.

Extracts opponent claims, attacks, and promises at the sentence level.
Deduplicates against existing OpponentActivity rows.
"""
import re
from typing import TYPE_CHECKING
from sqlalchemy.orm import Session
from app.models import CampaignConfig, Opponent, OpponentActivity, SourceItem
from app.services.text_utils import strip_html_to_text

if TYPE_CHECKING:
    from app.services.llm_provider import BaseLLMProvider


def _norm_text(text: str | None, maxlen: int = 500) -> str:
    """Lowercase + collapse whitespace + truncate for fingerprint comparison."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower().strip())[:maxlen]


def _activity_fingerprint(act_data: dict) -> str:
    """Stable dedup key encoding all three activity fields.

    Using a pipe-separated triple rather than exact DB string comparison
    avoids the NULL-equality trap: 'col = NULL' in SQL evaluates to NULL
    (not TRUE), causing false matches when using OR across NULL fields.
    """
    return "|".join([
        _norm_text(act_data.get("attack"), 500),
        _norm_text(act_data.get("claim"), 500),
        _norm_text(act_data.get("promise"), 300),
    ])

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


def _first_mention_pos(lower_text: str, name: str) -> int:
    """Return the position of the first word-boundary match of any meaningful part of name.

    Strips punctuation from name parts so FEC-format names like 'COGNETTI, PAIGE'
    correctly match 'Cognetti' in natural text (the comma in 'COGNETTI,' would
    otherwise prevent any match).
    """
    raw_parts = name.lower().split()
    # Strip non-alpha characters so "cognetti," → "cognetti", "jr." → "jr"
    parts = [re.sub(r'[^a-z]', '', p) for p in raw_parts]
    # Keep only meaningful tokens (skip initials, "jr", "sr", "rep", etc.)
    skip = {'jr', 'sr', 'mr', 'ms', 'dr', 'rep', 'sen', 'hon', 'the', 'and'}
    parts = [p for p in parts if len(p) > 2 and p not in skip]
    positions = []
    for part in parts:
        m = re.search(r'\b' + re.escape(part) + r'\b', lower_text)
        if m:
            positions.append(m.start())
    return min(positions) if positions else -1


def _classify_sentence(sentence: str, opponent_name: str, candidate_name: str = "") -> dict | None:
    """
    If sentence mentions the opponent AS THE SUBJECT, classify it as attack / claim / promise.

    Skips sentences where the candidate appears before the opponent — those are the
    candidate talking *about* the opponent (e.g. "Cognetti criticized Bresnahan for X"),
    not the opponent acting.

    Sets `needs_llm_verify=True` when both names are present and the opponent appears
    first — these may be passive-voice sentences where the opponent is the grammatical
    subject but the candidate is the actual actor (e.g. "Bresnahan was attacked by Cognetti").
    """
    lower = sentence.lower()

    opp_pos = _first_mention_pos(lower, opponent_name)
    if opp_pos == -1:
        return None

    needs_llm_verify = False
    if candidate_name:
        cand_pos = _first_mention_pos(lower, candidate_name)
        if cand_pos != -1:
            if cand_pos < opp_pos:
                # Candidate is clearly the subject — skip entirely.
                return None
            else:
                # Both names present, opponent appears first.
                # Could be passive voice ("Bresnahan was criticized by Cognetti").
                # Flag for LLM verification.
                needs_llm_verify = True

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
        "needs_llm_verify": needs_llm_verify,
    }


def _detect_theme(text: str) -> str | None:
    lower = text.lower()
    for theme, keywords in _THEME_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return theme
    return None


def _extract_activities(
    full_text: str,
    opponent_name: str,
    candidate_name: str = "",
    llm: "BaseLLMProvider | None" = None,
) -> list[dict]:
    """
    Return a list of activity dicts extracted from sentences in full_text.
    Each dict has: claim, attack, promise, contradiction_note, repeated_theme.

    When both names appear in a sentence (ambiguous passive-voice cases), the LLM
    is asked to confirm who the actual actor is. Sentences where the LLM says the
    candidate is the actor are dropped.
    """
    sentences = _split_sentences(full_text)
    results: list[dict] = []

    for sentence in sentences:
        classified = _classify_sentence(sentence, opponent_name, candidate_name)
        if not classified:
            continue

        # LLM verification for ambiguous sentences where both names are present.
        if classified.get("needs_llm_verify") and llm is not None and candidate_name:
            actor = llm.verify_opponent_subject(sentence, opponent_name, candidate_name)
            if actor == "candidate":
                continue

        # Decode entities and strip any residual tags so quotes don't carry
        # `&#x2019;` or `<a href>` markup into storage / UI.
        clean = strip_html_to_text(sentence)

        activity: dict = {
            "claim": None,
            "attack": None,
            "promise": None,
            "contradiction_note": None,
            "repeated_theme": _detect_theme(clean),
        }

        if classified["is_attack"]:
            activity["attack"] = clean[:500]
        if classified["is_claim"]:
            activity["claim"] = clean[:500]
        if classified["is_promise"]:
            activity["promise"] = clean[:300]

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
    from app.services.llm_provider import get_provider
    opponents = db.query(Opponent).all()
    full_text = f"{source_item.title}. {source_item.raw_text or ''}"
    created: list[OpponentActivity] = []

    campaign = db.query(CampaignConfig).first()
    candidate_name = campaign.candidate_name if campaign else ""
    llm = get_provider()

    for opponent in opponents:
        activities = _extract_activities(full_text, opponent.name, candidate_name, llm)
        if not activities:
            continue

        # Load all existing activities for this source+opponent in one query,
        # then compare by normalized fingerprint.  This avoids the NULL-equality
        # trap where (col == None) compiles to "col IS NULL" and matches any row
        # whose column happens to be NULL — causing valid distinct activities to
        # be incorrectly skipped.
        existing_rows = (
            db.query(OpponentActivity)
            .filter(
                OpponentActivity.opponent_id == opponent.id,
                OpponentActivity.source_item_id == source_item.id,
            )
            .all()
        )
        seen_fingerprints: set[str] = {
            _activity_fingerprint({"attack": r.attack, "claim": r.claim, "promise": r.promise})
            for r in existing_rows
        }

        for act_data in activities:
            fp = _activity_fingerprint(act_data)
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)

            activity = OpponentActivity(
                opponent_id=opponent.id,
                source_item_id=source_item.id,
                **act_data,
            )
            db.add(activity)
            created.append(activity)

    db.commit()
    return created
