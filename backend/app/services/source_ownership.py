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
# Word fragments in a URL *domain* that, combined with a person's name in the
# domain, strongly indicate that person owns the site.
_OWNERSHIP_DOMAIN_SIGNALS = {
    "campaign", "forcongress", "forsenate", "forassembly", "forcouncil",
    "formayor", "forgovernor", "elect", "official", "vote",
}
# Government TLD pattern — e.g. bresnahan.house.gov, smith.senate.gov
_GOV_DOMAIN_RE = re.compile(r'\.gov(\.|\b|/|$)')


@dataclass
class OwnershipResult:
    source_owner_type: str
    source_owner_confidence: str
    source_owner_reasons: list[str]


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_domain(url: str) -> str:
    """Return just the hostname (no path, port, or query) from a URL."""
    m = re.search(r'https?://([^/:?#]+)', (url or "").lower())
    return m.group(1) if m else ""


def _name_in_domain(domain: str, name: str) -> bool:
    """True when any long-enough part of *name* appears in *domain*.

    A "part" must be >3 chars to avoid false matches on short words like
    "lee" hitting "policy" or "ji" hitting anything.
    """
    parts = [p.lower() for p in name.split() if len(p) > 3]
    return bool(parts) and any(p in domain for p in parts)


def _domain_implies_ownership(domain: str, name: str) -> bool:
    """True when the URL domain strongly implies ownership by *name*.

    Requires the person's name in the domain *plus* either a .gov TLD or a
    recognised ownership-signal word.  A news domain that includes a
    politician's name in an article path does not qualify — we only check the
    hostname, not the path.

    Examples that return True:
        bresnahan.house.gov   (name: "Matt Bresnahan")
        jordanleecampaign.com (name: "Jordan Lee")
        alexriveraforcouncil.com (name: "Alex Rivera")

    Examples that return False:
        queensdaily.com/alex-rivera-housing  (name in path, not domain)
        nytimes.com                          (name absent)
    """
    if not _name_in_domain(domain, name):
        return False
    return (
        _GOV_DOMAIN_RE.search(domain) is not None
        or any(s in domain for s in _OWNERSHIP_DOMAIN_SIGNALS)
    )


def _source_meta_text(item: SourceItem) -> str:
    """Source name + URL domain only — used for name-based ownership checks.

    Deliberately excludes title and raw_text so that a candidate's or
    opponent's name appearing *inside* a third-party article does not
    trigger a false candidate_statement / opponent_statement classification.
    """
    domain = _extract_domain(item.source_url or "")
    return _norm(" ".join(filter(None, [item.source_name, domain])))


def _source_full_text(item: SourceItem) -> str:
    """Full concatenated text — used only for broad keyword detection."""
    return _norm(" ".join(filter(None, [
        item.title,
        item.source_name,
        item.source_url,
        item.raw_text[:1200] if item.raw_text else None,
    ])))


def classify_source_owner(db: Session, item: SourceItem) -> OwnershipResult:
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()

    full = _source_full_text(item)      # for committee/outside-group keyword sweep
    meta = _source_meta_text(item)      # source_name + domain — for name-based ownership
    domain = _extract_domain(item.source_url or "")

    is_committee = _contains_any(full, _COMMITTEE_KEYWORDS)
    is_outside_group = _contains_any(full, _OUTSIDE_GROUP_KEYWORDS)
    is_community = any(hint in full for hint in _COMMUNITY_HINTS)

    candidate_name = _norm(campaign.candidate_name if campaign else None)
    opponent_names = [_norm(o.name) for o in opponents if o.name]

    # --- Ownership signals must come from source *metadata* (name, domain),
    #     not from a name appearing inside the article body.
    candidate_in_meta = bool(candidate_name and candidate_name in meta)
    candidate_domain_owned = _domain_implies_ownership(domain, candidate_name) if candidate_name else False
    opponent_in_meta = any(n and n in meta for n in opponent_names)
    opponent_domain_owned = any(_domain_implies_ownership(domain, n) for n in opponent_names)

    # --- Explicit source_type overrides first ---

    if item.source_type == "campaign_note":
        # Internally produced notes: content is relevant for attribution
        content_has_candidate = bool(candidate_name and candidate_name in full)
        content_has_opponent = any(n and n in full for n in opponent_names)
        if candidate_in_meta or candidate_domain_owned or content_has_candidate:
            return OwnershipResult("candidate_statement", "high", ["Campaign note tied to candidate"])
        if opponent_in_meta or opponent_domain_owned or content_has_opponent:
            return OwnershipResult("opponent_statement", "medium", ["Campaign note tied to opponent"])
        return OwnershipResult("community/manual", "low", ["Manual campaign note"])

    if item.source_type == "opponent_statement" and not (is_committee or is_outside_group):
        if opponent_in_meta or opponent_domain_owned:
            return OwnershipResult("opponent_statement", "high", ["Opponent source matched by name"])
        return OwnershipResult("opponent_statement", "medium", ["Marked as opponent statement by source type"])

    # --- Broad keyword detection (committee / outside group) ---

    if is_committee:
        return OwnershipResult("party_committee_statement", "high", ["Committee or PAC keywords detected"])

    if is_outside_group:
        return OwnershipResult("outside_group_statement", "high", ["Outside-group keywords detected"])

    # --- Domain-based detection (strongest signal for web sources) ---

    if candidate_domain_owned:
        return OwnershipResult("candidate_statement", "high", ["Candidate domain detected in URL"])

    if opponent_domain_owned:
        return OwnershipResult("opponent_statement", "high", ["Opponent domain detected in URL"])

    # --- Source-name-based detection (name + ownership signal in source_name) ---
    # Ownership signal must appear in the *metadata* (source_name / domain),
    # not anywhere in the article body, to prevent news coverage from being
    # misclassified as campaign-owned content.

    _OWNERSHIP_META_SIGNALS = {"campaign", "official", "elect", "press release"}

    if candidate_in_meta and any(s in meta for s in _OWNERSHIP_META_SIGNALS):
        return OwnershipResult("candidate_statement", "medium",
                               ["Candidate name in campaign-like source name"])

    if opponent_in_meta and any(s in meta for s in _OWNERSHIP_META_SIGNALS):
        return OwnershipResult("opponent_statement", "medium",
                               ["Opponent name in campaign-like source name"])

    # --- General media / community fallbacks ---

    if item.source_type in {"social", "news", "public_record"}:
        if "campaign" in meta or "statement" in meta or "press release" in meta:
            return OwnershipResult("media", "medium", ["General media or public source"])

    if is_community or item.source_type in {"manual", "campaign_note"}:
        return OwnershipResult("community/manual", "medium", ["Community or manually captured source"])

    return OwnershipResult("unclear", "low", ["No clear owner signals"])
