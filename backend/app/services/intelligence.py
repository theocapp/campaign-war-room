"""Thin wrappers around the configured LLM provider.

Only the helpers actively used by ingestion remain after the Phase 0 cleanup.
talking-point / risk-warning / per-opponent extraction wrappers were dropped
along with their callers.
"""
from app.services.llm_provider import get_ingestion_provider


def summarize_source(raw_text: str) -> str:
    return get_ingestion_provider().summarize(raw_text)


def classify_urgency(raw_text: str) -> str:
    return get_ingestion_provider().classify_urgency(raw_text)


def extract_issues(raw_text: str) -> list[str]:
    return get_ingestion_provider().extract_issues(raw_text)
