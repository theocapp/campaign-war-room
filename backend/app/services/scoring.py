"""Heuristic evidence and credibility scoring for source items."""
import re
from app.models import SourceItem

_RISK_WORDS = re.compile(
    r'\b(unverified|alleged|rumor|rumour|disputed|questionable|satire|parody|fabricated)\b',
    re.IGNORECASE,
)
_WEAK_WORDS = re.compile(
    r'\b(weak|limited|unclear|uncertain|unconfirmed|anonymous)\b',
    re.IGNORECASE,
)
_NUMBERS = re.compile(r'\b\d+[\.,]\d+|\b\d{4}\b|\b\d+%')
_DATES = re.compile(
    r'\b(January|February|March|April|May|June|July|August|September|'
    r'October|November|December)\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b',
    re.IGNORECASE,
)

_EVIDENCE_TYPE_BONUS = {
    'public_record': 15,
    'news': 5,
    'canvassing': 5,
    'opponent_statement': 10,
    'campaign_note': 0,
    'social': -5,
}

_CRED_TYPE_BONUS = {
    'public_record': 30,
    'news': 25,
    'canvassing': 10,
    'campaign_note': 0,
    'opponent_statement': -10,
    'social': -15,
}


def compute_evidence_score(item: SourceItem) -> int:
    score = 30
    if item.source_url:
        score += 15
    if item.source_name:
        score += 10
    text = f"{item.title or ''} {item.raw_text or ''}"
    if len(text) > 500:
        score += 10
    if len(text) > 1500:
        score += 5
    if _NUMBERS.search(text):
        score += 15
    if _DATES.search(text):
        score += 10
    score += _EVIDENCE_TYPE_BONUS.get(item.source_type or '', 0)
    return min(100, max(0, score))


def compute_credibility_score(item: SourceItem) -> int:
    score = 40
    if item.source_url:
        score += 15
    if item.source_name:
        score += 10
    score += _CRED_TYPE_BONUS.get(item.source_type or '', 0)
    note_text = f"{item.credibility_note or ''} {item.title or ''}"
    if _RISK_WORDS.search(note_text):
        score -= 20
    if _WEAK_WORDS.search(note_text):
        score -= 10
    return min(100, max(0, score))
