from typing import Optional
from app.services.llm_provider import get_provider


def summarize_source(raw_text: str) -> str:
    return get_provider().summarize(raw_text)


def classify_urgency(raw_text: str) -> str:
    return get_provider().classify_urgency(raw_text)


def extract_issues(raw_text: str) -> list[str]:
    return get_provider().extract_issues(raw_text)


def detect_opponent_activity(raw_text: str, opponent_name: str) -> dict:
    return get_provider().detect_opponent_activity(raw_text, opponent_name)


def generate_talking_points(
    issue: str,
    tone: str,
    context: str = "",
    campaign_profile: Optional[dict] = None,
    sources: Optional[list[dict]] = None,
    opponent_activities: Optional[list[dict]] = None,
) -> dict:
    return get_provider().generate_talking_points(
        issue, tone, context, campaign_profile, sources, opponent_activities
    )


def generate_risk_warning(text: str, credibility_note: str) -> str | None:
    return get_provider().generate_risk_warning(text, credibility_note)
