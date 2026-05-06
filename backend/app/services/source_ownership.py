"""Heuristics for classifying who owns a source page or statement."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, SourceItem


_COMMITTEE_KEYWORDS = {
    "nrcc", "dccc", "rnc", "dnc", "nrsc", "dscc", "committee", "pac",
    "political action committee", "party committee", "campaign committee",
}
_OUTSIDE_GROUP_KEYWORDS = {
    "votevets", "moveon", "common cause", "club for growth", "sierra club",
    "end citizens united", "emily's list", "emilys list", "afl-cio",
    "teachers union", "laborers", "outside group", "super pac",
}
_COMMUNITY_HINTS = {
    "community", "forum", "notes", "manual", "pasted", "flyer", "endorsement",
    "newsletter", "debate", "social", "door", "canvass",
}


@dataclass
class OwnershipResult:
    source_owner_type: str
    source_owner_confidence: str
    source_owner_reasons: list[str]


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _source_text(item: SourceItem) -> str:
    return _norm(" ".join(filter(None, [item.title, item.source_name, item.source_url, item.raw_text[:1200] if item.raw_text else None])))


def classify_source_owner(db: Session, item: SourceItem) -> OwnershipResult:
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()
    text = _source_text(item)
    reasons: list[str] = []

    is_committee = _contains_any(text, _COMMITTEE_KEYWORDS)
    is_outside_group = _contains_any(text, _OUTSIDE_GROUP_KEYWORDS)
    is_community = any(hint in text for hint in _COMMUNITY_HINTS)

    candidate_name = _norm(campaign.candidate_name if campaign else None)
    opponent_names = [_norm(opponent.name) for opponent in opponents if opponent.name]

    candidate_hit = bool(candidate_name and candidate_name in text)
    opponent_hit = any(name and name in text for name in opponent_names)

    if item.source_type == "campaign_note":
        if candidate_hit:
            return OwnershipResult("candidate_statement", "high", ["Campaign note tied to candidate"])
        if opponent_hit:
            return OwnershipResult("opponent_statement", "medium", ["Campaign note tied to opponent"])
        return OwnershipResult("community/manual", "low", ["Manual campaign note"])

    if item.source_type == "opponent_statement" and not (is_committee or is_outside_group):
        if opponent_hit:
            return OwnershipResult("opponent_statement", "high", ["Opponent source matched by name"])
        return OwnershipResult("opponent_statement", "medium", ["Marked as opponent statement by source type"])

    if is_committee:
        return OwnershipResult("party_committee_statement", "high", ["Committee or PAC keywords detected"])

    if is_outside_group:
        return OwnershipResult("outside_group_statement", "high", ["Outside-group keywords detected"])

    if candidate_hit and ("campaign" in text or "official" in text or "for" in text or item.source_type in {"social", "campaign_note"}):
        return OwnershipResult("candidate_statement", "medium", ["Candidate name matched in campaign-like source"])

    if opponent_hit and ("campaign" in text or "official" in text or "for" in text or item.source_type == "opponent_statement"):
        return OwnershipResult("opponent_statement", "medium", ["Opponent name matched in campaign-like source"])

    if item.source_type in {"social", "news", "public_record"}:
        if "campaign" in text or "statement" in text or "press release" in text:
            return OwnershipResult("media", "medium", ["General media or public source"])

    if is_community or item.source_type in {"manual", "campaign_note"}:
        return OwnershipResult("community/manual", "medium", ["Community or manually captured source"])

    return OwnershipResult("unclear", "low", ["No clear owner signals"])