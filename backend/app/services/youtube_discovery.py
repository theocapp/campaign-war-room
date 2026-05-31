"""Automated YouTube channel discovery for news outlets and candidates.

The goal is end-to-end automation: at campaign onboarding, the system
finds every plausible YouTube channel (local outlets + the candidate +
opponents) without the user typing anything. New outlets discovered
later get probed too.

Two discovery strategies, picked per subject type:

  - **Outlets (institutional)** → scrape the publisher's homepage for a
    YouTube link in the footer/about. ~95% hit rate on US local news
    sites in spot checks; the ones that fail are usually paywalled or
    JS-rendered, where the link still exists but a different probe path
    would be needed.

  - **Candidates (individuals)** → ask the `judge_provider` LLM
    (gpt-4o-mini) for the handle, then verify by fetching the channel's
    RSS and confirming recent video titles match the candidate's name
    or district keywords. Verification is the key safety property —
    the LLM will confidently hallucinate handles otherwise.

Verification levels:

  - **strict** (candidates): require ≥2 of the last 10 video titles to
    contain at least one expected keyword. False positives here pollute
    the campaign with someone else's channel, so we're conservative.
  - **loose** (outlets): also accept matches in the channel's RSS
    `<title>` / `<author>` metadata, because outlet channels often have
    generic video titles ("Friday weather forecast") while still being
    the right entity.

Adds are idempotent (RssFeed dedup on URL containing the channel ID).
When a candidate's direct channel is added successfully, we deactivate
the matching `YouTube: {Surname}` Google News search feed (which today
ingests title-only stubs + Italian-author noise — see INTER_SESSION
notes from 2026-05-30).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_TIMEOUT = 12.0

# Direct UCxxx channel-id pattern (24-char canonical YouTube channel ID).
_CHANNEL_ID_RE = re.compile(r"youtube\.com/(?:channel/)?(UC[\w-]{22})")
# Modern @handle pattern (lowercase letters, digits, dots, dashes, underscores).
_HANDLE_RE = re.compile(r"youtube\.com/(@[\w.-]+)")
# canonical / itemprop extraction on resolved handle pages
_CANONICAL_CHANNEL_ID_RE = re.compile(
    r'(?:canonical[^>]+href|itemprop="channelId"[^>]+content)="[^"]*?(UC[\w-]{22})'
)

# YouTube channel RSS endpoint — works for any channel by ID.
_CHANNEL_RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


@dataclass
class DiscoveryResult:
    """One discovery attempt's outcome — surfaces to the bulk caller."""
    subject: str
    subject_type: str  # "outlet" | "candidate" | "opponent"
    channel_id: Optional[str]
    verified: bool
    reason: str  # short tag: "ok" | "no_link_on_homepage" | "verify_failed" | etc.


# ── Outlet discovery (scrape publisher homepage) ──────────────────────────

def discover_outlet_channel(publisher_domain: str) -> Optional[str]:
    """Find a YouTube channel by scraping the publisher's homepage.

    Looks for a link to either `youtube.com/channel/UC...` (direct ID) or
    `youtube.com/@handle` (needs one extra fetch to resolve). Returns the
    UC channel ID, or None if nothing was found / resolvable.

    Verification is the caller's responsibility — typically via
    `verify_channel_subject` after this returns.
    """
    if not publisher_domain:
        return None
    homepage = f"https://{publisher_domain}/"
    try:
        r = httpx.get(homepage, headers=_BROWSER_HEADERS, timeout=_TIMEOUT,
                      follow_redirects=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        html = r.text
    except Exception as exc:
        logger.debug("youtube discovery: homepage fetch failed for %s: %s",
                     publisher_domain, exc)
        return None

    # Prefer the direct channel-ID form when present — no extra fetch
    # needed and no handle-resolution failure mode.
    m = _CHANNEL_ID_RE.search(html)
    if m:
        return m.group(1)

    # Fall back to handle, resolve.
    m = _HANDLE_RE.search(html)
    if m:
        handle = m.group(1)
        return resolve_handle_to_channel_id(handle)

    return None


def resolve_handle_to_channel_id(handle: str) -> Optional[str]:
    """Fetch `youtube.com/<@handle>` and extract the canonical UC ID.

    Modern YouTube channel pages expose the channel ID via:
      - `<link rel="canonical" href=".../channel/UC...">`
      - `<meta itemprop="channelId" content="UC...">`
      - several JS data blobs

    We grep for the first match — all formats land at the same ID. If the
    page fails to load or the ID can't be extracted, returns None.
    """
    if not handle:
        return None
    if not handle.startswith("@"):
        handle = f"@{handle}"
    url = f"https://www.youtube.com/{handle}"
    try:
        r = httpx.get(url, headers=_BROWSER_HEADERS, timeout=_TIMEOUT,
                      follow_redirects=True)
        if r.status_code != 200:
            return None
        m = _CANONICAL_CHANNEL_ID_RE.search(r.text)
        if m:
            return m.group(1)
        # Some pages bury the ID inside JSON data structures instead of
        # exposing it via meta/link tags. Generic UC pattern catches those.
        m = re.search(r'"(UC[\w-]{22})"', r.text)
        if m:
            return m.group(1)
    except Exception as exc:
        logger.debug("youtube discovery: handle resolve failed for %s: %s",
                     handle, exc)
    return None


# ── Candidate discovery (LLM + verification) ──────────────────────────────

def discover_candidate_channel(
    candidate_name: str,
    *,
    state: Optional[str] = None,
    district: Optional[str] = None,
    office: Optional[str] = None,
) -> Optional[str]:
    """LLM lookup for a candidate's YouTube channel handle, then resolve
    to a channel ID. Returns the UC ID or None.

    Verification (does this channel actually belong to this person?) is
    NOT done here — the caller invokes `verify_channel_subject` against
    the returned ID with the candidate's distinctive keywords.

    Prompted conservatively: the LLM is told to return null when it
    isn't sure, because a confident wrong handle is more damaging than
    no handle.
    """
    if not candidate_name:
        return None

    from app.services.llm_provider import get_judge_provider

    parts = [f"Name: {candidate_name}"]
    if office:
        parts.append(f"Office: {office}")
    if state:
        parts.append(f"State: {state}")
    if district:
        parts.append(f"District: {district}")
    context = "\n".join(parts)

    prompt = f"""Find the official YouTube channel handle for this political candidate or officeholder.

{context}

Return STRICT JSON in this exact shape, with no other text before or after:
{{"handle": "@HandleHere"}}  ← if you are confident
{{"handle": null}}            ← if you are not confident

Be conservative. If you're unsure whether this is the official campaign
or office channel (versus a fan account, news outlet coverage of them,
or someone with a similar name), return null. We will independently
verify any handle you give us, but a wrong handle wastes verification
budget and risks polluting the dataset."""

    try:
        provider = get_judge_provider()
        raw = provider.complete(prompt)
    except Exception as exc:
        logger.warning(
            "youtube discovery: LLM lookup failed for %s: %s",
            candidate_name, exc,
        )
        return None

    handle = _parse_handle_from_llm_response(raw)
    if not handle:
        return None

    return resolve_handle_to_channel_id(handle)


def _parse_handle_from_llm_response(raw: str) -> Optional[str]:
    """Extract `@handle` from the LLM's JSON-ish response.

    Tolerant of:
      - Markdown code fences ("```json\n{...}\n```")
      - Leading/trailing chatter
      - Missing or null "handle" field
    """
    if not raw:
        return None
    # Strip markdown fences if present.
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    # Pull the first JSON object out of the response.
    m = re.search(r"\{[^{}]*\}", stripped, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    handle = obj.get("handle")
    if not handle or not isinstance(handle, str):
        return None
    handle = handle.strip()
    # Normalize: must start with @, no spaces, no slashes.
    if not handle.startswith("@"):
        handle = f"@{handle}"
    if " " in handle or "/" in handle:
        return None
    return handle


# ── Verification ──────────────────────────────────────────────────────────

def verify_channel_subject(
    channel_id: str,
    expected_keywords: list[str],
    *,
    strict: bool = True,
    min_title_matches: int = 2,
) -> bool:
    """Fetch a channel's RSS feed and verify the channel is plausibly
    about the expected subject.

    Strict mode (candidates): require ≥`min_title_matches` of the recent
    video titles to contain at least one expected keyword. A wrong-channel
    hit on a candidate is worse than no channel, so we err on rejecting.

    Loose mode (outlets): also accept matches in the channel's RSS
    `<title>` / `<author>` metadata. Outlet channels sometimes post
    generic-title videos ("Friday weather") where only the channel name
    identifies the owner.

    Returns True if verified, False otherwise (including fetch failure
    and empty feed).
    """
    if not channel_id or not expected_keywords:
        return False

    url = _CHANNEL_RSS_URL.format(cid=channel_id)
    try:
        r = httpx.get(url, headers=_BROWSER_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return False
        xml = r.text
    except Exception as exc:
        logger.debug("youtube discovery: verify fetch failed for %s: %s",
                     channel_id, exc)
        return False

    # Lower-case keywords for case-insensitive substring match.
    needles = [k.lower() for k in expected_keywords if k]
    if not needles:
        return False

    # Extract recent video titles (entries are inside <entry>...<title>...).
    # The top-level <title> AND <author><name> elements describe the
    # channel itself — that's the loose-mode signal.
    entry_titles = re.findall(r"<entry>.*?<title>(.*?)</title>", xml, flags=re.DOTALL)
    title_matches = sum(
        1 for t in entry_titles[:10]
        if any(n in t.lower() for n in needles)
    )
    if title_matches >= min_title_matches:
        return True

    if strict:
        return False

    # Loose mode — check channel-level metadata.
    chan_title_m = re.search(r"<title>(.*?)</title>", xml, flags=re.DOTALL)
    chan_author_m = re.search(r"<author>.*?<name>(.*?)</name>", xml, flags=re.DOTALL)
    chan_meta = " ".join(
        m.group(1).lower() for m in (chan_title_m, chan_author_m) if m
    )
    return any(n in chan_meta for n in needles)


# ── RssFeed integration ──────────────────────────────────────────────────

def add_youtube_feed(
    db: Session, *, name: str, channel_id: str,
) -> Optional["RssFeed"]:  # noqa: F821
    """Insert an RssFeed row for the channel's videos.xml feed.

    Idempotent: if any active RssFeed already targets this channel_id
    (regardless of `name`), this is a no-op and returns None. The check
    is on the URL substring `channel_id={cid}` so renames or duplicate-
    name attempts won't create dupes.
    """
    from app.models import RssFeed

    url = _CHANNEL_RSS_URL.format(cid=channel_id)
    existing = db.query(RssFeed).filter(
        RssFeed.url.like(f"%channel_id={channel_id}%"),
    ).first()
    if existing:
        # Re-activate if previously deactivated — discovery should
        # un-archive feeds the user already saw value in but disabled.
        if not existing.active:
            existing.active = True
            db.commit()
        return None

    feed = RssFeed(
        name=name,
        url=url,
        source_type="youtube",
        active=True,
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)
    return feed


def _deactivate_redundant_youtube_search_feeds(
    db: Session, surname: str,
) -> int:
    """When we've added a direct channel for a person, deactivate any
    Google News searches for `YouTube: {Surname}` — those return
    title-only stubs that pollute the corpus.

    Surname match is case-insensitive substring inside the feed `name`.
    Returns the count deactivated.
    """
    from app.models import RssFeed

    if not surname:
        return 0
    feeds = db.query(RssFeed).filter(
        RssFeed.active == True,  # noqa: E712
        RssFeed.name.ilike(f"YouTube:%{surname}%"),
    ).all()
    n = 0
    for f in feeds:
        # Only deactivate if it's a Google News-routed search (URL has
        # news.google.com). A user-added direct YouTube feed with that
        # name would be left alone.
        if "news.google.com" in (f.url or ""):
            f.active = False
            n += 1
    if n:
        db.commit()
    return n


# ── Bulk orchestration ───────────────────────────────────────────────────

def run_youtube_discovery(db: Session) -> dict:
    """Discover and add YouTube channels for all known outlets + the
    campaign's candidate + opponents that don't have one yet.

    Iterates each subject, attempts discovery, verifies (strict for
    people, loose for outlets), persists on success. Returns a summary
    dict with per-subject outcomes for visibility.

    Idempotent — `add_youtube_feed` dedups on channel_id.
    """
    from app.models import CampaignConfig, Opponent, Outlet, RssFeed

    results: list[DiscoveryResult] = []

    # ── Outlets ──
    outlets = db.query(Outlet).all()
    existing_yt_feeds = {
        m.group(1) for f in db.query(RssFeed).filter(
            RssFeed.url.like("%youtube.com/feeds/videos.xml%"),
        ).all()
        for m in [re.search(r"channel_id=(UC[\w-]{22})", f.url or "")]
        if m
    }

    for outlet in outlets:
        if not outlet.domain:
            continue
        # Skip if any existing YouTube feed name already references this outlet.
        # Coarse but cheap — keeps repeat runs from rediscovering everything.
        if db.query(RssFeed).filter(
            RssFeed.url.like("%youtube.com/feeds/videos.xml%"),
            RssFeed.name.ilike(f"%{outlet.name}%"),
        ).first():
            continue
        channel_id = discover_outlet_channel(outlet.domain)
        if not channel_id:
            results.append(DiscoveryResult(
                subject=outlet.name, subject_type="outlet",
                channel_id=None, verified=False,
                reason="no_link_on_homepage",
            ))
            continue
        if channel_id in existing_yt_feeds:
            # Already covered under a different name — skip.
            results.append(DiscoveryResult(
                subject=outlet.name, subject_type="outlet",
                channel_id=channel_id, verified=False,
                reason="already_covered",
            ))
            continue
        # Loose verification — outlet names sometimes don't appear in
        # video titles (e.g. "Today's weather" from an NBC affiliate).
        keywords = [outlet.name]
        if outlet.city:
            keywords.append(outlet.city)
        verified = verify_channel_subject(
            channel_id, keywords, strict=False,
        )
        if not verified:
            results.append(DiscoveryResult(
                subject=outlet.name, subject_type="outlet",
                channel_id=channel_id, verified=False,
                reason="verify_failed",
            ))
            continue
        feed = add_youtube_feed(
            db, name=f"{outlet.name} — YouTube", channel_id=channel_id,
        )
        existing_yt_feeds.add(channel_id)
        results.append(DiscoveryResult(
            subject=outlet.name, subject_type="outlet",
            channel_id=channel_id, verified=True,
            reason="ok" if feed else "ok_already_present",
        ))

    # ── Candidates + opponents ──
    campaign = db.query(CampaignConfig).first()
    people: list[tuple[str, str, str | None, str | None]] = []
    # (subject_label, subject_type, state, district)
    if campaign and campaign.candidate_name:
        people.append((
            campaign.candidate_name, "candidate",
            (campaign.location or "").split(",")[-1].strip() if campaign.location else None,
            campaign.district,
        ))
    for opp in db.query(Opponent).all():
        people.append((
            opp.name, "opponent",
            campaign.location.split(",")[-1].strip() if (campaign and campaign.location) else None,
            campaign.district if campaign else None,
        ))

    for name, kind, state, district in people:
        if not name:
            continue
        # Skip if we already have a YouTube feed whose name references
        # this person.
        surname = _person_surname(name)
        if db.query(RssFeed).filter(
            RssFeed.url.like("%youtube.com/feeds/videos.xml%"),
            RssFeed.name.ilike(f"%{name}%"),
        ).first():
            continue
        channel_id = discover_candidate_channel(
            name, state=state, district=district,
            office=campaign.office if campaign else None,
        )
        if not channel_id:
            results.append(DiscoveryResult(
                subject=name, subject_type=kind,
                channel_id=None, verified=False, reason="llm_no_handle",
            ))
            continue
        if channel_id in existing_yt_feeds:
            results.append(DiscoveryResult(
                subject=name, subject_type=kind,
                channel_id=channel_id, verified=False,
                reason="already_covered",
            ))
            continue
        # Strict verification for people — wrong-channel hits here are
        # worse than no-channel.
        keywords: list[str] = []
        if surname:
            keywords.append(surname)
        # Also accept matches on first name as a fallback signal.
        first = (name.split()[0] if name.split() else "").strip()
        if first and first != surname:
            keywords.append(first)
        verified = verify_channel_subject(channel_id, keywords, strict=True)
        if not verified:
            results.append(DiscoveryResult(
                subject=name, subject_type=kind,
                channel_id=channel_id, verified=False, reason="verify_failed",
            ))
            continue
        feed = add_youtube_feed(
            db, name=f"YouTube — {name}", channel_id=channel_id,
        )
        existing_yt_feeds.add(channel_id)
        # Deactivate the redundant Google News YouTube search feeds for
        # this person — they only produce title-only stubs now.
        deactivated = _deactivate_redundant_youtube_search_feeds(db, surname)
        results.append(DiscoveryResult(
            subject=name, subject_type=kind,
            channel_id=channel_id, verified=True,
            reason=f"ok (deactivated {deactivated} redundant search feed{'s' if deactivated != 1 else ''})",
        ))

    summary = {
        "checked": len(results),
        "added": sum(1 for r in results if r.verified and r.reason.startswith("ok")),
        "verify_failed": sum(1 for r in results if r.reason == "verify_failed"),
        "no_link_or_handle": sum(
            1 for r in results
            if r.reason in ("no_link_on_homepage", "llm_no_handle")
        ),
        "already_covered": sum(1 for r in results if r.reason == "already_covered"),
        "details": [
            {
                "subject": r.subject,
                "type": r.subject_type,
                "channel_id": r.channel_id,
                "verified": r.verified,
                "reason": r.reason,
            }
            for r in results
        ],
    }
    logger.info(
        "youtube_discovery: checked=%d added=%d verify_failed=%d no_handle=%d already_covered=%d",
        summary["checked"], summary["added"], summary["verify_failed"],
        summary["no_link_or_handle"], summary["already_covered"],
    )
    return summary


def _person_surname(name: str) -> str:
    """Extract the surname from "FIRST LAST" or FEC-style "LAST, FIRST"."""
    if not name:
        return ""
    raw = name.strip()
    if "," in raw:
        return raw.split(",", 1)[0].strip()
    parts = raw.split()
    return parts[-1] if parts else ""
