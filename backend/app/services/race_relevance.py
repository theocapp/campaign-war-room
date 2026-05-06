"""Deterministic campaign relevance scoring for source items."""
import json
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Issue, Opponent, SourceItem


SPORTS_TERMS = {
    "phillies", "eagles", "flyers", "76ers", "mlb", "nba", "nfl", "nhl",
    "coach", "manager", "playoffs", "game", "season",
}
DEFAULT_EXCLUDED_TERMS = {
    "sports", "phillies", "eagles", "flyers", "76ers", "mlb", "nba", "nfl", "nhl",
    "playoffs", "game", "manager", "coach", "celebrity", "restaurant", "recipe",
    "weather", "lottery",
}
ENTERTAINMENT_TERMS = {"movie", "concert", "celebrity", "actor", "music", "festival", "streaming"}
WEATHER_TERMS = {"weather", "forecast", "rain", "snow", "storm", "temperature", "heat wave"}
FOOD_TERMS = {"restaurant", "dining", "chef", "menu", "food", "bar", "brewery"}
CRIME_TERMS = {"arrest", "shooting", "robbery", "burglary", "assault", "police blotter"}
ELECTION_TERMS = {
    "campaign", "candidate", "election", "vote", "voter", "ballot", "poll",
    "endorsement", "debate", "primary", "general election", "yard sign",
}
PUBLIC_RECORD_TERMS = {
    "filing", "finance", "campaign finance", "donation", "pac", "committee",
    "ballot", "election", "ethics", "public record",
}
OPPONENT_ATTACK_TERMS = {
    "false", "lied", "lying", "misleading", "failed", "failure", "attack",
    "accused", "reckless", "dangerous", "corrupt", "flip-flop",
}
CLAIM_TERMS = {"says", "said", "claims", "claimed", "announced", "stated", "pledged", "promised"}


@dataclass
class RelevanceResult:
    race_relevance_score: int
    race_relevance_label: str
    relevance_reasons: list[str]
    actionability_score: int
    actionability_label: str
    content_category: str
    geo_relevance: str
    candidate_mentioned: bool
    opponent_mentioned: bool
    district_mentioned: bool
    priority_issue_mentioned: bool
    archived_as_irrelevant: bool
    recommended_disposition: str


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def _contains_phrase(text: str, phrase: str | None) -> bool:
    phrase = _norm(phrase)
    if not phrase or len(phrase) < 3:
        return False
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(re.search(rf"\b{re.escape(term.lower())}\b", text) for term in terms)


def _label(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "irrelevant"


def _priorities(campaign: CampaignConfig | None) -> list[str]:
    if not campaign or not campaign.key_priorities:
        return []
    if isinstance(campaign.key_priorities, str):
        try:
            parsed = json.loads(campaign.key_priorities)
            return [str(p) for p in parsed if str(p).strip()]
        except Exception:
            return [p.strip() for p in campaign.key_priorities.split(",") if p.strip()]
    return [str(p) for p in campaign.key_priorities if str(p).strip()]


def _json_terms(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(p) for p in parsed if str(p).strip()]
    except Exception:
        return [p.strip() for p in value.split(",") if p.strip()]


def _matches_issue_name(text: str, issue_name: str) -> bool:
    if _contains_phrase(text, issue_name):
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", _norm(issue_name)) if len(t) >= 4]
    return bool(tokens) and any(re.search(rf"\b{re.escape(token)}\b", text) for token in tokens)


def _content_category(text: str, has_race_connection: bool, priority_issue: bool) -> tuple[str, int, str | None]:
    if _contains_any(text, SPORTS_TERMS):
        return ("campaign" if has_race_connection else "sports", 0 if has_race_connection else -50,
                None if has_race_connection else "Sports story with no campaign connection")
    if _contains_any(text, ENTERTAINMENT_TERMS):
        return ("campaign" if has_race_connection else "entertainment", 0 if has_race_connection else -50,
                None if has_race_connection else "Entertainment story with no campaign connection")
    if _contains_any(text, WEATHER_TERMS) and not has_race_connection and not priority_issue:
        return "weather", -50, "Weather-only item with no campaign connection"
    if _contains_any(text, FOOD_TERMS) and not has_race_connection and not priority_issue:
        return "food", -50, "Food or restaurant item with no campaign connection"
    if _contains_any(text, CRIME_TERMS) and not has_race_connection and not priority_issue:
        return "generic_crime", -50, "Generic crime blotter with no race, policy, or district connection"
    if priority_issue:
        return "priority_issue", 0, None
    if has_race_connection:
        return "campaign", 0, None
    return "irrelevant", 0, None


def analyze_source_item(db: Session, item: SourceItem) -> RelevanceResult:
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()
    known_issues = db.query(Issue).all()
    text = _norm(" ".join([
        item.title or "",
        item.raw_text or "",
        item.summary or "",
        item.source_type or "",
        item.source_name or "",
        item.source_url or "",
    ]))

    score = 0
    reasons: list[str] = []

    candidate_mentioned = _contains_phrase(text, campaign.candidate_name if campaign else None)
    if candidate_mentioned:
        score += 35
        reasons.append("Candidate mentioned")

    opponent_mentioned = any(_contains_phrase(text, o.name) for o in opponents)
    if opponent_mentioned:
        score += 35
        reasons.append("Opponent mentioned")

    district_terms = [
        campaign.district if campaign else None,
        campaign.location if campaign else None,
        campaign.district_number if campaign else None,
        *_json_terms(campaign.neighborhood_keywords if campaign else None),
    ]
    district_mentioned = any(_contains_phrase(text, term) for term in district_terms)
    if district_mentioned:
        score += 30
        reasons.append("District or campaign location mentioned")

    office_mentioned = _contains_phrase(text, campaign.office if campaign else None) or _contains_phrase(text, campaign.race if campaign else None)
    if office_mentioned:
        score += 30 if campaign and campaign.sparse_race_mode else 20
        reasons.append("Office or race mentioned")

    geography_keyword_mentioned = any(_contains_phrase(text, term) for term in _json_terms(campaign.geography_keywords if campaign else None))
    local_geo = district_mentioned or _contains_phrase(text, campaign.location if campaign else None) or geography_keyword_mentioned
    if local_geo:
        score += 25 if campaign and campaign.sparse_race_mode else 15
        reasons.append("Local geography match")

    election_term = _contains_any(text, ELECTION_TERMS)
    if election_term:
        score += 20
        reasons.append("Election or campaign term found")

    priority_issue_mentioned = any(_contains_phrase(text, p) for p in _priorities(campaign))
    if priority_issue_mentioned:
        score += 20
        reasons.append("Campaign priority issue mentioned")

    known_issue_mentioned = any(_matches_issue_name(text, issue.name) for issue in known_issues)
    if known_issue_mentioned:
        score += 10
        reasons.append("Known issue mentioned")

    relevance_keyword_mentioned = any(_contains_phrase(text, term) for term in _json_terms(campaign.relevance_keywords if campaign else None))
    if relevance_keyword_mentioned:
        score += 20
        reasons.append("Custom relevance keyword matched")

    if item.source_type == "public_record" and _contains_any(text, PUBLIC_RECORD_TERMS):
        score += 20
        reasons.append("Public record tied to campaign, filing, finance, or ballot")

    if item.source_type in {"opponent_statement", "campaign_note"}:
        score += 35 if campaign and campaign.sparse_race_mode else 30
        reasons.append("Campaign or opponent statement source")
    if campaign and campaign.sparse_race_mode and item.source_type in {"public_record", "social"}:
        score += 15
        reasons.append("Sparse-race source type boost")

    if candidate_mentioned or opponent_mentioned or district_mentioned:
        score = max(score, 60)

    has_local_or_race_connection = any([
        candidate_mentioned,
        opponent_mentioned,
        district_mentioned,
        office_mentioned,
        local_geo,
        relevance_keyword_mentioned,
        item.source_type in {"opponent_statement", "campaign_note"},
    ])
    category, penalty, penalty_reason = _content_category(text, has_local_or_race_connection, priority_issue_mentioned)
    if penalty:
        score += penalty
        if penalty_reason:
            reasons.append(penalty_reason)

    excluded_terms = set(DEFAULT_EXCLUDED_TERMS) | {t.lower() for t in _json_terms(campaign.excluded_keywords if campaign else None)}
    excluded_keyword_mentioned = _contains_any(text, excluded_terms)
    if excluded_keyword_mentioned and not (candidate_mentioned or opponent_mentioned or district_mentioned):
        score -= 50
        reasons.append("Excluded noise keyword matched without candidate, opponent, or district connection")

    priority_without_context = priority_issue_mentioned and not (local_geo or has_local_or_race_connection)
    cap = 40 if campaign and campaign.sparse_race_mode else 30
    if not has_local_or_race_connection and not (item.source_type == "public_record" and election_term):
        score = min(score, cap)
        if score > 0:
            reasons.append("No clear local or race connection; relevance capped")
    if priority_without_context:
        score = min(score, 30)

    score = max(0, min(100, score))
    label = _label(score)

    opponent_attack_or_claim = opponent_mentioned and (_contains_any(text, OPPONENT_ATTACK_TERMS) or _contains_any(text, CLAIM_TERMS))
    candidate_or_opponent_claim = (candidate_mentioned or opponent_mentioned) and _contains_any(text, CLAIM_TERMS | OPPONENT_ATTACK_TERMS)

    if score >= 60 and (opponent_attack_or_claim or candidate_or_opponent_claim):
        action_label = "respond"
        action_score = min(100, score + 15)
    elif score >= 60:
        action_label = "review"
        action_score = score
    elif score >= (30 if campaign and campaign.sparse_race_mode else 40):
        action_label = "monitor"
        action_score = min(score, 59)
    else:
        action_label = "ignore"
        action_score = min(score, 39)

    if item.extraction_quality_label == "poor" and item.source_type == "news":
        if action_label == "respond":
            action_label = "review"
            action_score = min(action_score, 69)
            reasons.append("Extraction quality is poor; response recommendation downgraded pending source verification")
        score = min(score, 70)
        label = _label(score)

    if campaign and campaign.sparse_race_mode and score >= 30 and category != "irrelevant":
        archived = False
    else:
        archived = label == "irrelevant" or action_label == "ignore" or category == "irrelevant"
    if archived and not reasons:
        reasons.append("No candidate, opponent, district, race, local, or priority issue connection detected")

    geo_relevance = "district" if district_mentioned else ("local" if local_geo else "none")
    if item.extraction_quality_label == "poor" and geo_relevance == "none":
        reasons.append("Geography unclear because article extraction quality is poor")
    return RelevanceResult(
        race_relevance_score=score,
        race_relevance_label=label,
        relevance_reasons=reasons,
        actionability_score=action_score,
        actionability_label=action_label,
        content_category=category,
        geo_relevance=geo_relevance,
        candidate_mentioned=candidate_mentioned,
        opponent_mentioned=opponent_mentioned,
        district_mentioned=district_mentioned,
        priority_issue_mentioned=priority_issue_mentioned,
        archived_as_irrelevant=archived,
        recommended_disposition=action_label,
    )


def apply_relevance(db: Session, item: SourceItem) -> RelevanceResult:
    result = analyze_source_item(db, item)
    item.race_relevance_score = result.race_relevance_score
    item.race_relevance_label = result.race_relevance_label
    item.relevance_reasons = json.dumps(result.relevance_reasons)
    item.actionability_score = result.actionability_score
    item.actionability_label = result.actionability_label
    item.content_category = result.content_category
    item.geo_relevance = result.geo_relevance
    item.candidate_mentioned = result.candidate_mentioned
    item.opponent_mentioned = result.opponent_mentioned
    item.district_mentioned = result.district_mentioned
    item.priority_issue_mentioned = result.priority_issue_mentioned
    item.archived_as_irrelevant = result.archived_as_irrelevant
    return result
