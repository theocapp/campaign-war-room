import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CampaignConfig,
    NarrativeFrame,
    Opponent,
    SourceItem,
    TrackedThirdPartyAccount,
)
from app.schemas import SetupStatusOut, SetupChecklistItem
from app.services.social_handle_discovery import (
    HandleCandidate,
    discover_social_handles,
)
from app.services.third_party_account_discovery import (
    DiscoveredAccount,
    discover_third_party_accounts,
)

router = APIRouter()


class DiscoveredHandle(BaseModel):
    handle: str
    url: str
    snippet: Optional[str] = None
    confidence: str
    score: float


class HandleDiscoveryOut(BaseModel):
    name: str
    location: Optional[str] = None
    instagram: list[DiscoveredHandle]
    facebook: list[DiscoveredHandle]


class HandleSaveIn(BaseModel):
    target: Literal["candidate", "opponent"]
    opponent_id: Optional[int] = None
    # Full replacement semantics — the list sent here BECOMES the stored
    # list. Pass [] to clear all handles for that platform. Omit (null)
    # to leave the stored list unchanged so the caller can update just
    # one platform without touching the other.
    instagram_handles: Optional[list[str]] = None
    facebook_pages: Optional[list[str]] = None


def _to_payload(c: HandleCandidate) -> DiscoveredHandle:
    return DiscoveredHandle(
        handle=c.handle, url=c.url, snippet=c.snippet,
        confidence=c.confidence, score=c.score,
    )


@router.get("/setup/discover-handles", response_model=HandleDiscoveryOut)
def discover_handles(
    name: str,
    location: Optional[str] = None,
    limit: int = 4,
):
    """Auto-discover candidate Instagram and Facebook handles for a named
    person via web search. Results are ranked, with `confidence` labels
    the UI shows next to each. Callers (the Setup wizard) typically
    let the user multi-select which handles to track — politicians
    routinely run multiple parallel accounts (campaign / office /
    personal) and signal lives on all of them.

    Empty lists are returned when no search provider is configured (the
    mock provider returns no results) — UI should explain that case
    rather than surface as an error.
    """
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    result = discover_social_handles(name=name, location=location, limit_per_platform=limit)
    return HandleDiscoveryOut(
        name=name,
        location=location,
        instagram=[_to_payload(c) for c in result.get("instagram", [])],
        facebook=[_to_payload(c) for c in result.get("facebook", [])],
    )


def _clean_handle_list(values: Optional[list[str]]) -> Optional[list[str]]:
    """Strip whitespace, drop empties, de-dup while preserving order.

    Returns None when the input is None (= "don't touch this field").
    Returns [] for an explicit empty list (= "clear all handles").
    """
    if values is None:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip().lstrip("@")
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    return cleaned


def _parse_stored_handles(raw: Optional[str]) -> list[str]:
    """Read the TEXT column back as a list. Empty/null/malformed → []."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


@router.post("/setup/save-handles")
def save_handles(body: HandleSaveIn, db: Session = Depends(get_db)):
    """Persist the confirmed IG/FB handle lists on either the candidate's
    CampaignConfig row or a specific Opponent row.

    Semantics:
      - Pass a list (incl. empty) to fully replace stored handles for that
        platform.
      - Pass null (omit the field) to leave that platform's stored handles
        untouched. This lets the wizard update just IG without overwriting
        FB.
    """
    if body.target == "candidate":
        config = db.query(CampaignConfig).first()
        if not config:
            raise HTTPException(
                status_code=404,
                detail="No campaign profile exists — set up the candidate first.",
            )
        target_row = config
    else:
        if not body.opponent_id:
            raise HTTPException(
                status_code=400,
                detail="opponent_id is required when target=opponent",
            )
        opp = db.query(Opponent).filter(Opponent.id == body.opponent_id).first()
        if not opp:
            raise HTTPException(status_code=404, detail=f"Opponent {body.opponent_id} not found")
        target_row = opp

    ig = _clean_handle_list(body.instagram_handles)
    fb = _clean_handle_list(body.facebook_pages)
    if ig is not None:
        target_row.instagram_handles = json.dumps(ig) if ig else None
    if fb is not None:
        target_row.facebook_pages = json.dumps(fb) if fb else None
    db.commit()
    db.refresh(target_row)
    return {
        "target": body.target,
        "opponent_id": body.opponent_id,
        "instagram_handles": _parse_stored_handles(target_row.instagram_handles),
        "facebook_pages": _parse_stored_handles(target_row.facebook_pages),
    }


@router.get("/setup/status", response_model=SetupStatusOut)
def get_setup_status(db: Session = Depends(get_db)):
    campaign = db.query(CampaignConfig).first()
    opponent_count = db.query(Opponent).count()
    source_count = db.query(SourceItem).count()
    frame_count = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).count()  # noqa: E712

    profile_complete = bool(
        campaign
        and campaign.candidate_name
        and campaign.office
        and campaign.campaign_message
        and campaign.election_date
    )

    items = [
        SetupChecklistItem(
            id="campaign_profile",
            label="Campaign profile completed",
            complete=profile_complete,
            helper_text="Add your candidate name, office, district, and campaign message.",
            action_path="/campaign",
        ),
        SetupChecklistItem(
            id="opponent_added",
            label="At least one opponent added",
            complete=opponent_count > 0,
            helper_text="Add your opponent(s) so attacks and claims can be tracked automatically.",
            action_path="/opponents",
        ),
        SetupChecklistItem(
            id="source_added",
            label="At least one source added",
            complete=source_count > 0,
            helper_text="Add a news source, paste text, fetch a URL, or configure an RSS feed.",
            action_path="/sources",
        ),
        SetupChecklistItem(
            id="narrative_frame_added",
            label="At least one narrative frame defined",
            complete=frame_count > 0,
            helper_text="Define the narrative frames your campaign cares about — your message and the opponent's attacks.",
            action_path="/narratives",
        ),
    ]

    return SetupStatusOut(
        complete=all(item.complete for item in items),
        items=items,
    )


# ── Phase 2: third-party account tracking ─────────────────────────────────────
#
# Different from the per-actor IG/FB handles in save-handles above. Those
# are the candidate's and opponents' OWN accounts. These are anyone ELSE
# the user wants to track — local news outlets, county committees, PACs,
# statewide subreddits, journalists. The discovery + save flow uses the
# same web-search engine but searches for accounts that MENTION the race,
# not accounts owned by a named person.


class DiscoveredThirdPartyAccount(BaseModel):
    platform: str
    identifier: str
    display_name: Optional[str] = None
    url: str
    snippet: Optional[str] = None
    score: float
    confidence: str
    inferred_role: str
    rss_url: Optional[str] = None
    matched_queries: list[str] = []
    # Bare anchor names (e.g. ["Paige Cognetti", "Rob Bresnahan"]) — used
    # by the UI to render "via X" pills so the user sees which side's
    # search surfaced each result.
    matched_anchors: list[str] = []


class ThirdPartyDiscoveryOut(BaseModel):
    candidate_name: str
    location: Optional[str] = None
    accounts_by_platform: dict[str, list[DiscoveredThirdPartyAccount]]
    # Identifiers of accounts already saved as third-party (so the UI can
    # mark them "already tracked" rather than re-offer them).
    already_tracked: dict[str, list[str]]


class TrackedAccountOut(BaseModel):
    id: int
    platform: str
    identifier: str
    display_name: Optional[str]
    url: str
    inferred_role: Optional[str]
    snippet: Optional[str]
    rss_url: Optional[str]
    notes: Optional[str]
    added_at: datetime

    model_config = {"from_attributes": True}


class TrackedAccountIn(BaseModel):
    platform: str
    identifier: str
    display_name: Optional[str] = None
    url: str
    inferred_role: Optional[str] = None
    snippet: Optional[str] = None
    rss_url: Optional[str] = None
    notes: Optional[str] = None


class TrackedAccountSaveBatchIn(BaseModel):
    accounts: list[TrackedAccountIn]


def _discovered_to_payload(a: DiscoveredAccount) -> DiscoveredThirdPartyAccount:
    return DiscoveredThirdPartyAccount(
        platform=a.platform,
        identifier=a.identifier,
        display_name=a.display_name,
        url=a.url,
        snippet=a.snippet,
        score=a.score,
        confidence=a.confidence,
        inferred_role=a.inferred_role,
        rss_url=a.rss_url,
        matched_queries=a.matched_queries,
        matched_anchors=a.matched_anchors,
    )


def _collect_own_handles(db: Session) -> dict[str, set[str]]:
    """Build the exclusion map: known IG/FB handles for the candidate AND
    all opponents. The discovery service skips any URL that resolves to
    one of these so the candidate's own accounts don't show up as "third
    party."
    """
    exclude: dict[str, set[str]] = {"instagram": set(), "facebook": set()}
    config = db.query(CampaignConfig).first()
    if config:
        for handle in json.loads(config.instagram_handles or "[]"):
            exclude["instagram"].add(handle)
        for page in json.loads(config.facebook_pages or "[]"):
            exclude["facebook"].add(page)
    for opp in db.query(Opponent).all():
        for handle in json.loads(opp.instagram_handles or "[]"):
            exclude["instagram"].add(handle)
        for page in json.loads(opp.facebook_pages or "[]"):
            exclude["facebook"].add(page)
    return exclude


@router.get("/setup/discover-third-party", response_model=ThirdPartyDiscoveryOut)
def discover_third_party(db: Session = Depends(get_db)):
    """Run third-party account discovery anchored to the current campaign.

    Uses the saved CampaignConfig (candidate name, location, district) and
    the list of Opponents — no params needed from the client. Returns
    ranked candidates per platform with confidence + role labels, and a
    parallel `already_tracked` map so the UI can hide accounts the user
    has previously confirmed.

    No persistence here — the UI shows results and a separate save call
    commits the user's picks.
    """
    config = db.query(CampaignConfig).first()
    if not config:
        raise HTTPException(
            status_code=404,
            detail="No campaign profile exists — set up the candidate first.",
        )
    opponents = db.query(Opponent).all()
    exclude = _collect_own_handles(db)

    result = discover_third_party_accounts(
        candidate_name=config.candidate_name,
        opponent_names=[o.name for o in opponents if o.name],
        location=config.location or config.district,
        district=config.district,
        exclude=exclude,
        limit_per_platform=8,
    )

    payload = {
        platform: [_discovered_to_payload(a) for a in accts]
        for platform, accts in result.items()
    }

    # Build the already-tracked map: any (platform, identifier) the user
    # has previously confirmed shows up here so the UI can hide it or
    # mark it as already-on.
    tracked = db.query(TrackedThirdPartyAccount).all()
    already: dict[str, list[str]] = {}
    for row in tracked:
        already.setdefault(row.platform, []).append(row.identifier)

    return ThirdPartyDiscoveryOut(
        candidate_name=config.candidate_name,
        location=config.location or config.district,
        accounts_by_platform=payload,
        already_tracked=already,
    )


@router.get("/setup/tracked-accounts", response_model=list[TrackedAccountOut])
def list_tracked_accounts(db: Session = Depends(get_db)):
    """Return every confirmed third-party account, newest first."""
    return (
        db.query(TrackedThirdPartyAccount)
        .order_by(TrackedThirdPartyAccount.added_at.desc())
        .all()
    )


@router.post("/setup/tracked-accounts", response_model=list[TrackedAccountOut])
def save_tracked_accounts(body: TrackedAccountSaveBatchIn, db: Session = Depends(get_db)):
    """Batch-insert confirmed third-party accounts. Idempotent on
    (platform, identifier) — re-saving an existing pair updates its
    display_name / snippet / inferred_role / rss_url if the new payload
    has values, otherwise leaves the existing row alone.

    Returns the full set of newly-saved or updated rows (NOT the entire
    tracked-accounts list — the UI maintains its own local copy and
    appends).
    """
    saved: list[TrackedThirdPartyAccount] = []
    for payload in body.accounts:
        platform = (payload.platform or "").strip()
        identifier = (payload.identifier or "").strip().lstrip("@")
        if not platform or not identifier:
            continue
        existing = (
            db.query(TrackedThirdPartyAccount)
            .filter_by(platform=platform, identifier=identifier)
            .first()
        )
        if existing:
            # Only overwrite fields when the new payload has a value —
            # lets the UI send minimal "re-confirm" payloads without
            # clobbering user-edited notes or display_name.
            if payload.display_name:
                existing.display_name = payload.display_name
            if payload.url:
                existing.url = payload.url
            if payload.inferred_role:
                existing.inferred_role = payload.inferred_role
            if payload.snippet:
                existing.snippet = payload.snippet
            if payload.rss_url:
                existing.rss_url = payload.rss_url
            if payload.notes is not None:
                existing.notes = payload.notes
            saved.append(existing)
        else:
            row = TrackedThirdPartyAccount(
                platform=platform,
                identifier=identifier,
                display_name=payload.display_name,
                url=payload.url,
                inferred_role=payload.inferred_role,
                snippet=payload.snippet,
                rss_url=payload.rss_url,
                notes=payload.notes,
            )
            db.add(row)
            saved.append(row)
    db.commit()
    for row in saved:
        db.refresh(row)
    return saved


@router.delete("/setup/tracked-accounts/{account_id}", status_code=204)
def delete_tracked_account(account_id: int, db: Session = Depends(get_db)):
    """Stop tracking a third-party account. The corresponding RSS feed
    (if generated) goes away on the next monitor reconciliation pass —
    nothing to delete here besides the row itself.
    """
    row = db.query(TrackedThirdPartyAccount).filter_by(id=account_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Tracked account {account_id} not found")
    db.delete(row)
    db.commit()
