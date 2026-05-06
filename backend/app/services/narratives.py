"""First-pass narrative extraction and traction scoring.

This deliberately uses conservative deterministic rules. Narratives should be
fewer and higher-signal than issue tags, so broad issue mentions alone do not
create narratives.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import re

logger = logging.getLogger(__name__)

_REFRESH_COOLDOWN = timedelta(seconds=60)
_last_refresh: datetime | None = None

from sqlalchemy.orm import Session, joinedload

from app.models import CampaignConfig, CandidateNarrative, ManualCapture, Narrative, NarrativeMention, Opponent, OpponentActivity, SourceItem


_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "for", "in", "on",
    "with", "by", "from", "that", "this", "is", "are", "was", "were", "be",
    "has", "have", "had", "says", "said", "claims", "claimed", "argues",
    "announced", "according", "report", "reports", "campaign", "candidate",
}

_NARRATIVE_VERBS = {
    "failed", "failure", "reckless", "dangerous", "corrupt", "dishonest",
    "wrong", "misleading", "lied", "lying", "attack", "accuses", "accused",
    "pledged", "vowed", "promised", "fighting", "protect", "delivering",
    "standing", "supports", "opposes",
}

_ATTACK_ATTRIBUTION_VERBS = {
    "attacked", "criticized", "criticised", "accused", "blamed", "slammed",
    "charged", "hit", "called out", "took aim", "said", "says", "claimed",
    "claims", "argued", "argues", "alleged", "alleges",
}

_OPPONENT_OWNED_TYPES = {"opponent_statement"}

_OPPONENT_OWNED_SOURCE_HINTS = {
    "opponent campaign", "official campaign",
}

_CANDIDATE_OWNED_TYPES = {"campaign_note"}

# Words in a URL *domain* that, combined with a person's name, signal ownership.
_OWNERSHIP_DOMAIN_SIGNALS = {
    "campaign", "forcongress", "forsenate", "forassembly", "forcouncil",
    "formayor", "forgovernor", "elect", "official", "vote",
}
_GOV_DOMAIN_RE = re.compile(r'\.gov(\.|\b|/|$)')

# Attack verbs: used to detect whether opponent-owned content explicitly
# targets the candidate (vs. just mentioning them or praising the opponent).
_EXPLICIT_ATTACK_VERBS = {
    "attacked", "slammed", "blasted", "accused", "blamed", "criticized",
    "criticised", "failed", "failure", "lied", "lying", "dishonest",
    "reckless", "dangerous", "corrupt", "wrong", "misleading", "unfit",
    "defund", "smeared", "distorted", "misrepresented",
}


# ── Domain-based ownership helpers ───────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Return only the hostname from a URL (no path/port/query)."""
    m = re.search(r'https?://([^/:?#]+)', (url or "").lower())
    return m.group(1) if m else ""


def _name_in_domain(domain: str, name: str) -> bool:
    parts = [p.lower() for p in name.split() if len(p) > 3]
    return bool(parts) and any(p in domain for p in parts)


def _domain_implies_opponent_ownership(url: str, opponent_names: list[str]) -> bool:
    """True when the URL domain contains an opponent's name + an ownership signal."""
    domain = _extract_domain(url)
    if not domain:
        return False
    for name in opponent_names:
        if not _name_in_domain(domain, name):
            continue
        if _GOV_DOMAIN_RE.search(domain) or any(s in domain for s in _OWNERSHIP_DOMAIN_SIGNALS):
            return True
    return False


@dataclass
class NarrativeCandidate:
    text: str
    narrative_type: str
    owner_type: str
    direction: str
    source_item: SourceItem | None
    opponent_activity: OpponentActivity | None
    mention_role: str
    confidence_score: int
    owner_confidence: str
    attribution_type: str
    target_confidence: str
    candidate_narrative_id: int | None = None


def _norm(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text.lower())
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _tokens(text: str) -> set[str]:
    return {
        tok for tok in _norm(text).split()
        if len(tok) > 2 and tok not in _STOPWORDS
    }


def _strip_known_names(text: str, campaign: CampaignConfig | None, opponent_names: list[str]) -> str:
    working = text
    names = list(opponent_names)
    if campaign:
        names.append(campaign.candidate_name)
    for name in names:
        if not name:
            continue
        working = re.sub(re.escape(name), " ", working, flags=re.IGNORECASE)
        for part in name.split():
            if len(part) > 2:
                working = re.sub(rf"\b{re.escape(part)}\b", " ", working, flags=re.IGNORECASE)
    return working


def _canonical_key(text: str, campaign: CampaignConfig | None, opponent_names: list[str]) -> str:
    stripped = _strip_known_names(text, campaign, opponent_names)
    tokens = sorted(_tokens(stripped))
    if not tokens:
        tokens = sorted(_tokens(text))
    return " ".join(tokens[:14])


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    at = set(a.split())
    bt = set(b.split())
    if not at or not bt:
        return False
    overlap = len(at & bt) / max(len(at | bt), 1)
    return overlap >= 0.58


def _short_label(text: str) -> str:
    label = re.sub(r"\s+", " ", text).strip()
    if len(label) <= 72:
        return label
    return label[:69].rstrip() + "..."


def _source_time(source: SourceItem | None) -> datetime:
    if not source:
        return datetime.utcnow()
    return source.published_at or source.created_at or datetime.utcnow()


def _cluster_key(source: SourceItem | None) -> str:
    if not source:
        return "unsourced"
    return source.story_cluster_id or f"source-{source.id}"


def _contains_name(text: str, name: str | None) -> bool:
    if not text or not name:
        return False
    lower = text.lower()
    if name.lower() in lower:
        return True
    parts = [p.lower() for p in name.split() if len(p) > 2]
    return bool(parts) and any(re.search(rf"\b{re.escape(part)}\b", lower) for part in parts)


def _name_regex(name: str | None) -> str:
    if not name:
        return r".{0,80}"
    options = [re.escape(name.lower())]
    options.extend(re.escape(part.lower()) for part in name.split() if len(part) > 2)
    return r"(?:" + "|".join(dict.fromkeys(options)) + r")"


def _is_direct_quote_attributed_to_opponent(text: str, opponent_name: str) -> bool:
    lower = text.lower()
    if not _contains_name(text, opponent_name):
        return False
    quote_near_name = re.search(
        rf"([\"“][^\"”]{{8,220}}[\"”]\s*,?\s*(said|says|claimed|argued|alleged)\s+{re.escape(opponent_name.lower())})"
        rf"|({re.escape(opponent_name.lower())}\s+(said|says|claimed|argued|alleged)[^\"“]{{0,80}}[\"“][^\"”]{{8,220}}[\"”])",
        lower,
    )
    return bool(quote_near_name)


def _explicitly_attributes_attack(text: str, opponent_name: str, candidate_name: str | None) -> bool:
    lower = text.lower()
    if not _contains_name(text, opponent_name):
        return False
    if candidate_name and not _contains_name(text, candidate_name):
        return False
    opponent_pattern = re.escape(opponent_name.lower())
    candidate_pattern = _name_regex(candidate_name)
    verbs = "|".join(re.escape(v) for v in sorted(_ATTACK_ATTRIBUTION_VERBS, key=len, reverse=True))
    patterns = [
        rf"{opponent_pattern}.{{0,120}}\b({verbs})\b.{{0,160}}{candidate_pattern}",
        rf"{candidate_pattern}.{{0,120}}\b({verbs})\s+by\s+{opponent_pattern}",
        rf"{opponent_pattern}'s campaign.{{0,160}}{candidate_pattern}",
    ]
    return any(re.search(pattern, lower) for pattern in patterns)


def _manual_capture_explicit_opponent(source: SourceItem | None) -> bool:
    if not source:
        return False
    text = " ".join(filter(None, [source.title, source.source_name, source.raw_text, source.summary])).lower()
    return (
        source.source_type == "opponent_statement"
        or source.source_owner_type == "opponent_statement"
        or "opponent messaging" in text
        or "opponent mailer" in text
        or "opponent flyer" in text
        or "opponent campaign" in text
    )


def _source_owner_class(source: SourceItem | None) -> str:
    if not source:
        return "unclear"
    return source.source_owner_type or "unclear"


def _source_is_opponent_owned(source: SourceItem | None, opponent_names: list[str] | None = None) -> bool:
    """True when the source is clearly produced/owned by the opponent.

    Checks (in order of confidence):
    1. source_owner_type already set to opponent_statement
    2. source_type is opponent_statement
    3. Source name contains a known opponent-owned hint phrase
    4. URL domain implies opponent ownership (name + .gov or campaign signal)

    *Not* triggered by the opponent's name appearing inside a news article.
    """
    if not source:
        return False
    owner_type = _source_owner_class(source)
    source_name = (source.source_name or "").lower()
    if (
        owner_type == "opponent_statement"
        or source.source_type in _OPPONENT_OWNED_TYPES
        or any(hint in source_name for hint in _OPPONENT_OWNED_SOURCE_HINTS)
    ):
        return True
    # Domain-based detection (e.g. bresnahan.house.gov)
    if opponent_names and source.source_url:
        return _domain_implies_opponent_ownership(source.source_url, opponent_names)
    return False


def _candidate_owned_source(source: SourceItem | None, campaign: CampaignConfig | None, candidate_capture_ids: set[int]) -> bool:
    if not source:
        return False
    owner_type = _source_owner_class(source)
    source_name = (source.source_name or "").lower()
    title = (source.title or "").lower()
    url = (source.source_url or "").lower()
    candidate_name = (campaign.candidate_name if campaign else "") or ""
    candidate_hit = _contains_name(source_name, candidate_name) or _contains_name(title, candidate_name)
    return (
        source.id in candidate_capture_ids
        or owner_type == "candidate_statement"
        or source.source_type in _CANDIDATE_OWNED_TYPES
        or "candidate campaign" in source_name
        or "campaign statement" in source_name
        or (candidate_hit and ("campaign" in source_name or "for" in source_name or "campaign" in url))
    )


def _candidate_attributed_source(source: SourceItem | None, text: str, campaign: CampaignConfig | None) -> bool:
    if not source or not campaign:
        return False
    if _candidate_owned_source(source, campaign, set()):
        return True
    candidate_pattern = _name_regex(campaign.candidate_name)
    verbs = r"said|says|announced|pledged|argued|called for|released|proposed|vowed|promised"
    return bool(re.search(rf"{candidate_pattern}.{{0,90}}\b({verbs})\b", text.lower()))


def _json_terms(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        import json
        parsed = json.loads(value)
        return [str(x) for x in parsed if str(x).strip()]
    except Exception:
        return [part.strip() for part in value.split(",") if part.strip()]


def _candidate_terms(candidate_narrative: CandidateNarrative) -> list[str]:
    terms = [candidate_narrative.short_label, candidate_narrative.canonical_text]
    terms.extend(_json_terms(candidate_narrative.preferred_phrases))
    terms.extend(_json_terms(candidate_narrative.must_mention_points))
    return [term.strip() for term in terms if term and len(term.strip()) >= 4]


def _matches_candidate_narrative(text: str, candidate_narrative: CandidateNarrative) -> tuple[bool, int, str | None]:
    lower = text.lower()
    terms = _candidate_terms(candidate_narrative)
    for term in terms:
        normalized = re.sub(r"\s+", " ", term.lower()).strip()
        if len(normalized) >= 8 and normalized in lower:
            return True, 85, term
    canonical_tokens = _tokens(candidate_narrative.canonical_text)
    preferred_tokens = set()
    for term in terms:
        preferred_tokens.update(_tokens(term))
    source_tokens = _tokens(text)
    signal_tokens = canonical_tokens | preferred_tokens
    if len(signal_tokens) < 4:
        return False, 0, None
    overlap = len(signal_tokens & source_tokens) / max(len(signal_tokens), 1)
    if overlap >= 0.48 and len(signal_tokens & source_tokens) >= 4:
        return True, int(60 + min(25, overlap * 30)), candidate_narrative.short_label
    return False, 0, None


def _candidate_narrative_type(candidate_narrative: CandidateNarrative, source: SourceItem) -> tuple[str, str]:
    if candidate_narrative.narrative_kind == "contrast":
        return "candidate_attack", "against_opponent" if source.opponent_mentioned else "for_candidate"
    if candidate_narrative.narrative_kind == "rebuttal":
        return "policy_frame", "against_opponent" if source.opponent_mentioned else "for_candidate"
    if candidate_narrative.narrative_kind == "issue_frame":
        return "policy_frame", "for_candidate"
    return "candidate_self_definition", "for_candidate"


def _has_explicit_attack_on_candidate(text: str, candidate_name: str | None, opponent_name: str | None) -> bool:
    """True when text from an opponent-owned source explicitly attacks the candidate.

    Requires both:
    - the candidate's name present in the text, AND
    - an attack verb/adjective either attributed to the opponent toward the
      candidate (via _explicitly_attributes_attack) or appearing alongside
      the candidate's name in the text.

    A press release that merely mentions the candidate's district, praises the
    opponent's own record, or uses the candidate's name in passing does NOT
    qualify as an explicit attack.
    """
    if not candidate_name or not _contains_name(text, candidate_name):
        return False
    lower = text.lower()
    # Prefer the structured attribution check (opponent said X about candidate)
    if _explicitly_attributes_attack(text, opponent_name or "", candidate_name):
        return True
    # Fallback: attack verb appears near the candidate name in the same
    # sentence-length window (≤120 chars either side).
    candidate_pattern = _name_regex(candidate_name)
    attack_pattern = "|".join(re.escape(v) for v in sorted(_EXPLICIT_ATTACK_VERBS, key=len, reverse=True))
    return bool(re.search(
        rf"({attack_pattern}).{{0,120}}{candidate_pattern}"
        rf"|{candidate_pattern}.{{0,120}}({attack_pattern})",
        lower,
    ))


def _opponent_attribution(
    source: SourceItem | None,
    text: str,
    opponent_name: str | None,
    candidate_name: str | None,
) -> tuple[bool, str, str, str, str]:
    """Classify whether content is an attack by the opponent on the candidate.

    Returns (is_attack, attribution_type, owner_confidence, target_confidence, owner_type).

    Key fix: opponent-owned sources (press releases, official sites) are NOT
    automatically treated as attacks.  Only when explicit attack language
    targeting the candidate is present do we return is_attack=True.  A press
    release praising the opponent's own record gets is_attack=False with
    attribution_type="opponent_self_promotion".
    """
    if not opponent_name:
        return False, "unclear", "low", "low", "unknown"
    owner_class = _source_owner_class(source)
    owner_type_map = {
        "candidate_statement": "candidate",
        "opponent_statement": "opponent",
        "outside_group_statement": "outside_group",
        "party_committee_statement": "party_committee",
        "media": "media",
        "community/manual": "community_manual",
    }
    source_owner_type = owner_type_map.get(owner_class, "unknown")

    # Pass opponent_name as a list for domain-ownership check
    if _source_is_opponent_owned(source, [opponent_name]):
        if _has_explicit_attack_on_candidate(text, candidate_name, opponent_name):
            target = "high" if candidate_name and _contains_name(text, candidate_name) else "medium"
            return True, "opponent_owned_source", "high", target, "opponent"
        # Opponent-owned but self-promotional (praising themselves, policy
        # announcement, etc.) — not an attack even if candidate is mentioned.
        target = "medium" if candidate_name and _contains_name(text, candidate_name) else "low"
        return False, "opponent_self_promotion", "medium", target, "opponent"

    if _manual_capture_explicit_opponent(source):
        target = "high" if candidate_name and _contains_name(text, candidate_name) else "medium"
        return True, "opponent_owned_source", "high", target, source_owner_type if source_owner_type != "unknown" else "opponent"
    if _is_direct_quote_attributed_to_opponent(text, opponent_name):
        target = "high" if candidate_name and _contains_name(text, candidate_name) else "medium"
        return True, "direct_quote", "high", target, source_owner_type if source_owner_type != "unknown" else "opponent"
    if _explicitly_attributes_attack(text, opponent_name, candidate_name):
        target_confidence = "high" if candidate_name else "medium"
        owner_type = source_owner_type if source_owner_type != "unknown" else "opponent"
        return True, "explicit_reported_attack", "high", target_confidence, owner_type
    if _contains_name(text, opponent_name):
        target = "medium" if candidate_name and _contains_name(text, candidate_name) else "low"
        return False, "unclear", "low", target, source_owner_type
    return False, "unclear", "low", "low", source_owner_type


def _candidate_from_opponent_activity(activity: OpponentActivity, campaign: CampaignConfig | None) -> NarrativeCandidate | None:
    text = activity.attack or activity.claim
    if not text:
        return None
    source = activity.source_item
    if source and (source.archived_as_irrelevant or source.content_category == "irrelevant"):
        return None
    if source and source.extraction_quality_label == "poor" and source.source_type == "news":
        return None
    full_text = " ".join(filter(None, [source.title if source else None, source.raw_text if source else None, text]))
    opponent_name = activity.opponent.name if activity.opponent else None
    candidate_name = campaign.candidate_name if campaign else None
    is_attack, attribution, owner_confidence, target_confidence, owner_type = _opponent_attribution(
        source,
        full_text,
        opponent_name,
        candidate_name,
    )
    if activity.attack and not is_attack:
        return NarrativeCandidate(
            text=text[:500],
            narrative_type="possible_attack",
            owner_type=owner_type,
            direction="against_candidate",
            source_item=source,
            opponent_activity=activity,
            mention_role="seed",
            confidence_score=55,
            owner_confidence=owner_confidence,
            attribution_type=attribution,
            target_confidence=target_confidence,
        )
    return NarrativeCandidate(
        text=text[:500],
        narrative_type="opponent_attack" if activity.attack else "policy_frame",
        owner_type=owner_type if activity.attack else owner_type,
        direction="against_candidate" if activity.attack else "neutral",
        source_item=source,
        opponent_activity=activity,
        mention_role="seed",
        confidence_score=85 if activity.attack else 70,
        owner_confidence=owner_confidence if activity.attack else "medium",
        attribution_type=attribution if activity.attack else "inferred",
        target_confidence=target_confidence if activity.attack else "low",
    )


def _candidate_from_source(source: SourceItem, campaign: CampaignConfig | None, opponents: list[Opponent]) -> NarrativeCandidate | None:
    if source.archived_as_irrelevant or source.content_category == "irrelevant":
        return None
    if source.extraction_quality_label == "poor" and source.source_type == "news":
        return None
    if source.opponent_activities:
        return None
    if (source.race_relevance_score or 0) < 55 and source.actionability_label != "respond":
        return None
    text = source.summary or source.title
    if not text:
        return None
    lowered = text.lower()
    has_narrative_verb = any(marker in lowered for marker in _NARRATIVE_VERBS)
    if source.actionability_label == "respond" or source.opponent_mentioned:
        full_text = " ".join(filter(None, [source.title, source.raw_text, source.summary]))
        attributed = None
        for opponent in opponents:
            is_attack, attribution, owner_confidence, target_confidence, owner_type = _opponent_attribution(
                source,
                full_text,
                opponent.name,
                campaign.candidate_name if campaign else None,
            )
            if is_attack:
                attributed = (attribution, owner_confidence, target_confidence, owner_type)
                break
        if attributed:
            attribution, owner_confidence, target_confidence, owner_type = attributed
            return NarrativeCandidate(
                text=text[:500],
                narrative_type="opponent_attack",
                owner_type=owner_type,
                direction="against_candidate",
                source_item=source,
                opponent_activity=None,
                mention_role="seed",
                confidence_score=78,
                owner_confidence=owner_confidence,
                attribution_type=attribution,
                target_confidence=target_confidence,
            )
        owner_class = _source_owner_class(source)
        if owner_class in {"party_committee_statement", "outside_group_statement"}:
            narrative_type = "possible_attack"
            attribution_type = "inferred_owned_source"
            owner_type = "party_committee" if owner_class == "party_committee_statement" else "outside_group"
            direction = "against_candidate"
        elif owner_class == "opponent_statement":
            # Opponent-owned source that passed through _opponent_attribution
            # without triggering is_attack — i.e. it's self-promotional content
            # (policy announcement, record-touting) rather than an explicit attack.
            narrative_type = "policy_frame"
            attribution_type = "opponent_self_promotion"
            owner_type = "opponent"
            direction = "neutral"
        elif source.actionability_label == "respond":
            narrative_type = "possible_attack"
            attribution_type = "unclear"
            owner_type = owner_class if owner_class != "unclear" else "unknown"
            direction = "against_candidate"
        else:
            narrative_type = "media_frame"
            attribution_type = "media_frame"
            owner_type = "media"
            direction = "neutral"
        return NarrativeCandidate(
            text=text[:500],
            narrative_type=narrative_type,
            owner_type=owner_type,
            direction=direction,
            source_item=source,
            opponent_activity=None,
            mention_role="repeat",
            confidence_score=50,
            owner_confidence="low",
            attribution_type=attribution_type,
            target_confidence="medium" if source.candidate_mentioned else "low",
        )
    if source.source_type in {"campaign_note", "social", "manual", "webpage"} and source.candidate_mentioned and has_narrative_verb:
        return NarrativeCandidate(
            text=text[:500],
            narrative_type="candidate_self_definition",
            owner_type="candidate",
            direction="for_candidate",
            source_item=source,
            opponent_activity=None,
            mention_role="seed",
            confidence_score=60,
            owner_confidence="medium",
            attribution_type="inferred",
            target_confidence="medium",
        )
    return None


def _candidate_from_message_library(
    source: SourceItem,
    campaign: CampaignConfig | None,
    candidate_narratives: list[CandidateNarrative],
    candidate_capture_ids: set[int],
) -> NarrativeCandidate | None:
    if source.archived_as_irrelevant or source.content_category == "irrelevant" or not campaign:
        return None
    if source.extraction_quality_label == "poor" and source.source_type == "news":
        return None
    full_text = " ".join(filter(None, [source.title, source.raw_text, source.summary]))
    if not full_text:
        return None
    if not (_candidate_owned_source(source, campaign, candidate_capture_ids) or _candidate_attributed_source(source, full_text, campaign)):
        return None
    best: tuple[CandidateNarrative, int, str | None] | None = None
    for candidate_narrative in candidate_narratives:
        matched, confidence, matched_text = _matches_candidate_narrative(full_text, candidate_narrative)
        if matched and (best is None or confidence > best[1]):
            best = (candidate_narrative, confidence, matched_text)
    if not best or best[1] < 70:
        return None
    candidate_narrative, confidence, matched_text = best
    narrative_type, direction = _candidate_narrative_type(candidate_narrative, source)
    return NarrativeCandidate(
        text=candidate_narrative.canonical_text[:500],
        narrative_type=narrative_type,
        owner_type="candidate",
        direction=direction,
        source_item=source,
        opponent_activity=None,
        mention_role="seed" if _candidate_owned_source(source, campaign, candidate_capture_ids) else "amplification",
        confidence_score=confidence,
        owner_confidence="high" if _candidate_owned_source(source, campaign, candidate_capture_ids) else "medium",
        attribution_type="candidate_owned_source" if _candidate_owned_source(source, campaign, candidate_capture_ids) else "explicit_reported_attack",
        target_confidence="high",
        candidate_narrative_id=candidate_narrative.id,
    )


def _group_candidates(candidates: list[NarrativeCandidate], campaign: CampaignConfig | None, opponent_names: list[str]) -> list[list[NarrativeCandidate]]:
    grouped: list[tuple[str, list[NarrativeCandidate]]] = []
    for candidate in candidates:
        key = _canonical_key(candidate.text, campaign, opponent_names)
        if len(key.split()) < 2:
            continue
        match = None
        for existing_key, items in grouped:
            same_family = items[0].narrative_type == candidate.narrative_type and items[0].owner_type == candidate.owner_type
            if same_family and _similar(key, existing_key):
                match = items
                break
        if match is not None:
            match.append(candidate)
        else:
            grouped.append((key, [candidate]))
    return [items for _key, items in grouped]


def _score_group(items: list[NarrativeCandidate]) -> dict:
    sources = [item.source_item for item in items if item.source_item]
    source_ids = {s.id for s in sources}
    clusters = {_cluster_key(s) for s in sources}
    messengers = {s.source_name or s.source_type for s in sources}
    geographies = {s.geo_relevance for s in sources if s.geo_relevance and s.geo_relevance != "none"}
    now = datetime.utcnow()
    last_seen = max((_source_time(s) for s in sources), default=now)
    first_seen = min((_source_time(s) for s in sources), default=now)
    recent_sources = sum(1 for s in sources if _source_time(s) >= now - timedelta(days=7))
    escaped_owned = any(s.source_type not in {"campaign_note", "opponent_statement"} for s in sources)

    traction = 0
    traction += min(len(clusters), 4) * 18
    traction += min(len(messengers), 4) * 9
    traction += min(len(geographies), 3) * 7
    traction += min(recent_sources, 3) * 8
    if any(item.owner_type == "opponent" for item in items):
        traction += 10
    if escaped_owned and len(clusters) > 1:
        traction += 10
    traction = min(100, traction)

    if len(clusters) >= 3 and len(messengers) >= 2:
        evidence = "strong"
    elif len(clusters) >= 2 or len(source_ids) >= 3:
        evidence = "moderate"
    else:
        evidence = "weak"

    if last_seen < now - timedelta(days=14):
        status = "fading"
    elif len(clusters) == 1:
        status = "emerging"
    elif recent_sources >= 2 or traction >= 65:
        status = "rising"
    else:
        status = "stable"

    response_status = "no_response"
    if any(s.actionability_label == "respond" for s in sources):
        response_status = "response_ready"
    if any(item.mention_role == "response" for item in items):
        response_status = "response_active"

    notes = []
    if len(clusters) == 1:
        notes.append("Confined to one distinct source cluster.")
    if len(messengers) <= 1:
        notes.append("Messenger diversity is limited.")
    if escaped_owned:
        notes.append("Appears outside campaign-owned material.")
    else:
        notes.append("Still confined to campaign/opponent-controlled material.")

    return {
        "source_cluster_count": len(clusters),
        "source_count": len(source_ids),
        "messenger_diversity_count": len(messengers),
        "geography_count": len(geographies),
        "first_seen_at": first_seen,
        "last_seen_at": last_seen,
        "traction_score": traction,
        "evidence_strength": evidence,
        "status": status,
        "response_status": response_status,
        "owner_confidence": "high" if any(i.owner_confidence == "high" for i in items) else ("medium" if any(i.owner_confidence == "medium" for i in items) else "low"),
        "attribution_type": next((i.attribution_type for i in items if i.attribution_type in {"direct_quote", "explicit_reported_attack", "opponent_owned_source"}), items[0].attribution_type),
        "target_confidence": "high" if any(i.target_confidence == "high" for i in items) else ("medium" if any(i.target_confidence == "medium" for i in items) else "low"),
        "notes": " ".join(notes),
    }


def refresh_narratives(db: Session, force: bool = False) -> list[Narrative]:
    """Rebuild MVP narratives from current evidence.

    This is intentionally rebuild-based for the MVP so changes in extraction
    rules can be reflected without a separate migration/backfill workflow.

    Rebuilds are throttled to at most once per 60 seconds. Pass force=True
    (e.g. from an explicit UI action) to bypass the cooldown.
    """
    global _last_refresh
    now = datetime.utcnow()
    if not force and _last_refresh is not None and (now - _last_refresh) < _REFRESH_COOLDOWN:
        logger.debug("Narrative refresh skipped (last ran %ss ago)", (now - _last_refresh).seconds)
        return db.query(Narrative).order_by(Narrative.traction_score.desc()).all()
    _last_refresh = now

    campaign = db.query(CampaignConfig).first()
    opponent_names = [o.name for o in db.query(Opponent).all()]

    candidates: list[NarrativeCandidate] = []
    activities = (
        db.query(OpponentActivity)
        .options(joinedload(OpponentActivity.source_item))
        .order_by(OpponentActivity.created_at.desc())
        .limit(250)
        .all()
    )
    for activity in activities:
        candidate = _candidate_from_opponent_activity(activity, campaign)
        if candidate:
            candidates.append(candidate)

    opponents = db.query(Opponent).all()
    candidate_capture_ids = {
        row[0]
        for row in db.query(ManualCapture.source_item_id).filter(ManualCapture.candidate_related == True).all()  # noqa: E712
    }
    candidate_library_narratives = (
        db.query(CandidateNarrative)
        .filter(CandidateNarrative.active == True)  # noqa: E712
        .order_by(CandidateNarrative.priority.desc(), CandidateNarrative.created_at.desc())
        .all()
    )
    sources = (
        db.query(SourceItem)
        .order_by(SourceItem.created_at.desc())
        .limit(300)
        .all()
    )
    for source in sources:
        candidate = _candidate_from_message_library(source, campaign, candidate_library_narratives, candidate_capture_ids)
        if candidate:
            candidates.append(candidate)
            continue
        candidate = _candidate_from_source(source, campaign, opponents)
        if candidate:
            candidates.append(candidate)

    groups = _group_candidates(candidates, campaign, opponent_names)

    db.query(NarrativeMention).delete()
    db.query(Narrative).delete()
    db.flush()

    narratives: list[Narrative] = []
    for items in groups:
        score = _score_group(items)
        seed = max(items, key=lambda item: item.confidence_score)
        narrative = Narrative(
            canonical_text=seed.text,
            short_label=_short_label(seed.text),
            narrative_type=seed.narrative_type,
            owner_type=seed.owner_type,
            direction=seed.direction,
            status=score["status"],
            first_seen_at=score["first_seen_at"],
            last_seen_at=score["last_seen_at"],
            source_cluster_count=score["source_cluster_count"],
            source_count=score["source_count"],
            messenger_diversity_count=score["messenger_diversity_count"],
            geography_count=score["geography_count"],
            traction_score=score["traction_score"],
            evidence_strength=score["evidence_strength"],
            response_status=score["response_status"],
            owner_confidence=score["owner_confidence"],
            attribution_type=score["attribution_type"],
            target_confidence=score["target_confidence"],
            candidate_narrative_id=seed.candidate_narrative_id,
            notes=score["notes"],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(narrative)
        db.flush()
        # Dedup by source_item_id: the same URL must not appear more than once
        # as evidence for a single narrative.  Sort highest-confidence first so
        # the best match wins when two candidates share the same source.
        # NULL source_ids (activity-only mentions) are never suppressed.
        seen_source_ids: set[int] = set()
        for item in sorted(items, key=lambda i: i.confidence_score, reverse=True):
            source = item.source_item
            source_id = source.id if source else None
            if source_id is not None:
                if source_id in seen_source_ids:
                    continue
                seen_source_ids.add(source_id)
            db.add(NarrativeMention(
                narrative_id=narrative.id,
                source_item_id=source.id if source else None,
                opponent_activity_id=item.opponent_activity.id if item.opponent_activity else None,
                source_cluster_id=_cluster_key(source) if source else None,
                matched_text=item.text[:500],
                mention_role=item.mention_role,
                confidence_score=item.confidence_score,
                owner_confidence=item.owner_confidence,
                attribution_type=item.attribution_type,
                target_confidence=item.target_confidence,
                candidate_narrative_id=item.candidate_narrative_id,
            ))
        narratives.append(narrative)

    db.commit()
    return narratives


def top_narratives(db: Session, limit: int = 5) -> list[Narrative]:
    if db.query(Narrative).count() == 0:
        refresh_narratives(db)
    return (
        db.query(Narrative)
        .options(joinedload(Narrative.mentions).joinedload(NarrativeMention.source_item))
        .order_by(Narrative.traction_score.desc(), Narrative.last_seen_at.desc())
        .limit(limit)
        .all()
    )
