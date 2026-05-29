import json
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, RssFeed, SourceItem, SourceMonitor
from app.services import ingestion
from app.services.search_provider import get_search_provider
from app.services.source_discovery import generate_monitors_for_campaign, get_local_outlets, _gnews_url_with_dates, _candidate_last_name, _parse_state_code


def _json_list(value: list | None) -> str | None:
    return json.dumps(value) if value is not None else None


def _to_values(suggestion: dict) -> dict:
    values = dict(suggestion)
    values["required_terms"] = _json_list(values.get("required_terms"))
    values["excluded_terms"] = _json_list(values.get("excluded_terms"))
    return values


def _duplicate_query(db: Session, values: dict) -> SourceMonitor | None:
    q = db.query(SourceMonitor).filter(SourceMonitor.monitor_type == values["monitor_type"])
    clauses = []
    if values.get("query"):
        clauses.append(SourceMonitor.query == values["query"])
    if values.get("url"):
        clauses.append(SourceMonitor.url == values["url"])
    clauses.append(SourceMonitor.name == values["name"])
    return q.filter(or_(*clauses)).first()


def _resolve_youtube_channel_id(url: str) -> str | None:
    """Extract or resolve a YouTube channel ID from a channel URL.

    Handles:
      youtube.com/channel/UCxxxxxxxx   → direct ID extraction
      youtube.com/@handle              → fetches page HTML to find channel ID
    Returns None if resolution fails.
    """
    if not url:
        return None
    # Direct channel ID in URL
    m = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    # Handle-based URL — fetch the page and look for channel ID in the HTML
    try:
        import requests as _req
        r = _req.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        m = re.search(r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"', r.text)
        if m:
            return m.group(1)
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{20,})"', r.text)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _ensure_rss_feed(db: Session, monitor: SourceMonitor) -> None:
    """Register an RssFeed row for the monitor so the scheduler picks it up.

    For monitor_type="rss": straightforward — use the URL as-is.
    For monitor_type="youtube": resolve channel ID from the URL and convert
    to the YouTube XML feed format before registering.
    """
    from app.services.source_discovery import _youtube_channel_rss

    if monitor.monitor_type == "twitter_profile":
        if not (monitor.query or monitor.url):
            return  # waiting for user to supply a handle
        from app.services.twitter_scraper import ensure_twitter_feed
        ensure_twitter_feed(db, monitor)
        return

    if monitor.monitor_type == "youtube":
        if not monitor.url:
            return  # waiting for user to supply a channel URL
        channel_id = _resolve_youtube_channel_id(monitor.url)
        if not channel_id:
            logger.warning("monitors: could not resolve YouTube channel ID from %s", monitor.url)
            return
        feed_url = _youtube_channel_rss(channel_id)
        if db.query(RssFeed).filter_by(url=feed_url).first():
            return
        db.add(RssFeed(name=monitor.name, url=feed_url, source_type=monitor.source_type or "social"))
        return

    if monitor.monitor_type != "rss" or not monitor.url:
        return
    if db.query(RssFeed).filter_by(url=monitor.url).first():
        return
    db.add(RssFeed(name=monitor.name, url=monitor.url, source_type=monitor.source_type or "news"))


def _validate_rss_url(url: str, timeout: int = 8) -> bool:
    """Return True if the URL responds with XML-ish content that looks like a feed."""
    if not url:
        return False
    try:
        import requests as _requests
        r = _requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CampaignBot/1.0)"},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return False
        ct = r.headers.get("content-type", "")
        body = r.text[:500].lower()
        return (
            "xml" in ct or "rss" in ct or "atom" in ct
            or "<rss" in body or "<feed" in body or "<?xml" in body
        )
    except Exception:
        return False


def _auto_discover_outlets(db: Session, district: str, state_code: str | None,
                            location: str | None, candidate: str | None,
                            force: bool = False) -> int:
    """Use an LLM to discover local news outlets for a campaign district.

    Validates each suggested RSS URL before saving. Idempotent — skips domains
    that already exist as Outlet records. Returns number of new outlets created.

    Normally only runs when the district has no outlets (hardcoded catalog or
    DB). Pass force=True to run discovery even when entries exist — useful
    for augmenting curated districts (PA-08) with additional outlets and for
    periodic re-discovery to catch newly-emerged outlets.
    """
    import json as _json
    from app.models import Outlet
    from app.services.llm_provider import get_provider
    from app.services.source_discovery import get_local_outlets

    dist_key = district.upper().strip()

    if not force:
        # Skip if the catalog already has DISTRICT-SPECIFIC entries (not just state-level ones).
        from app.services.source_discovery import _OUTLET_CATALOG
        has_hardcoded = bool(_OUTLET_CATALOG["district"].get(dist_key))

        # Also check DB for any outlets already tagged with this exact district.
        import json as _json2
        from app.models import Outlet as _OutletCheck
        db_tagged = [
            o for o in db.query(_OutletCheck).filter(
                _OutletCheck.active == True, _OutletCheck.districts.isnot(None)
            ).all()
            if dist_key in (_json2.loads(o.districts or "[]"))
        ]
        if has_hardcoded or db_tagged:
            return 0  # district already covered

        logger.info("outlet_discovery: no catalog entries for %s — running LLM discovery", district)
    else:
        logger.info("outlet_discovery: FORCE mode — running LLM discovery for %s (catalog/DB will be augmented)", district)

    location_hint = location or district
    state_name = state_code or ""

    prompt = f"""You are helping set up a political news monitoring system for a US congressional campaign.

Campaign district: {district}
Location: {location_hint}
State: {state_name}

List the 8-10 most important LOCAL and REGIONAL news outlets that cover this specific congressional district. Focus on:
- Local daily newspapers
- Local TV news stations
- Regional political news sites (like state-focused journalism nonprofits)
- Do NOT include national outlets (NY Times, Washington Post, Politico, etc.)

For each outlet provide:
- name: the outlet's common name
- domain: just the domain (e.g. "akronbeaconjournal.com")
- rss_url: the outlet's main RSS feed URL (use /feed/ for WordPress sites, check for common patterns)
- outlet_type: one of: local_news, regional_news, broadcast
- authority_score: integer 1-10 (10=major metro daily, 8=solid regional paper, 6=small local paper, 5=community site)
- monthly_visitors: your best estimate of monthly unique website visitors as an integer (e.g. 450000 for a mid-size regional daily, 1500000 for a major market TV station website, 80000 for a small community paper). Be conservative — err low rather than high.
- city: primary city served (or null)

Return ONLY a JSON array. No explanation. Example format:
[{{"name":"Akron Beacon Journal","domain":"beaconjournal.com","rss_url":"https://www.beaconjournal.com/arcio/rss/","outlet_type":"local_news","authority_score":8,"monthly_visitors":420000,"city":"Akron"}}]"""

    try:
        provider = get_provider()
        raw = provider.complete(prompt)
    except Exception as e:
        logger.warning("outlet_discovery: LLM call failed for %s: %s", district, e)
        return 0

    # Parse JSON — strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()

    try:
        suggestions = _json.loads(raw)
        if not isinstance(suggestions, list):
            raise ValueError("expected list")
    except Exception as e:
        logger.warning("outlet_discovery: JSON parse failed for %s: %s | raw=%r", district, e, raw[:200])
        return 0

    created = 0
    for s in suggestions:
        try:
            domain = (s.get("domain") or "").lower().strip().lstrip("www.").strip("/")
            rss_url = (s.get("rss_url") or "").strip()
            name = (s.get("name") or domain).strip()
            if not domain or not rss_url:
                continue
            if db.query(Outlet).filter_by(domain=domain).first():
                continue
            if not _validate_rss_url(rss_url):
                logger.info("outlet_discovery: RSS validation failed for %s (%s)", name, rss_url)
                continue
            import json as _json2
            mv_raw = s.get("monthly_visitors")
            monthly_visitors = max(1000, int(mv_raw)) if mv_raw else None
            db.add(Outlet(
                name=name,
                domain=domain,
                rss_url=rss_url,
                outlet_type=s.get("outlet_type", "local_news"),
                authority_score=max(1, min(10, int(s.get("authority_score") or 5))),
                monthly_visitors=monthly_visitors,
                state=state_code,
                city=s.get("city"),
                districts=_json2.dumps([district]),
                active=True,
                notes="Auto-discovered by LLM on campaign setup",
            ))
            created += 1
            logger.info("outlet_discovery: added %s (%s)", name, domain)
        except Exception as e:
            logger.warning("outlet_discovery: error processing suggestion %r: %s", s, e)
            continue

    if created:
        db.commit()
        logger.info("outlet_discovery: created %d outlets for %s", created, district)

    return created


def _estimate_outlet_traffic(db: Session) -> int:
    """Ask the LLM to estimate monthly_visitors for any outlets that don't have it yet.

    Batches all missing outlets into a single LLM call to stay cheap.
    Idempotent — only processes outlets where monthly_visitors IS NULL.
    Returns the number of outlets updated.
    """
    import json as _json
    from app.models import Outlet
    from app.services.llm_provider import get_provider

    missing = db.query(Outlet).filter(
        Outlet.monthly_visitors.is_(None),
        Outlet.active == True,
    ).all()
    if not missing:
        return 0

    outlet_list = "\n".join(
        f'- {o.name} ({o.domain}) [{o.outlet_type}, {o.state or "US"}]'
        for o in missing
    )
    prompt = f"""Estimate the monthly unique website visitors for each of these US news outlets.
Use your knowledge of their audience size. Be conservative — err low rather than high.
Return ONLY a JSON object mapping domain → integer visitor count. No explanation.

Outlets:
{outlet_list}

Example format: {{"beaconjournal.com": 420000, "wkbn.com": 850000}}"""

    try:
        provider = get_provider()
        raw = provider.complete(prompt)
    except Exception as e:
        logger.warning("traffic_estimate: LLM call failed: %s", e)
        return 0

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()

    try:
        estimates = _json.loads(raw)
        if not isinstance(estimates, dict):
            raise ValueError("expected object")
    except Exception as e:
        logger.warning("traffic_estimate: JSON parse failed: %s | raw=%r", e, raw[:300])
        return 0

    updated = 0
    domain_map = {o.domain.lower(): o for o in missing}
    for domain_raw, visitors in estimates.items():
        domain = domain_raw.lower().strip()
        outlet = domain_map.get(domain)
        if not outlet:
            continue
        try:
            outlet.monthly_visitors = max(1000, int(visitors))
            updated += 1
        except Exception:
            continue

    if updated:
        db.commit()
        logger.info("traffic_estimate: estimated monthly visitors for %d outlets", updated)
    return updated


def _seed_campaign_outlets(db: Session, campaign) -> int:
    """Create Outlet records for all local outlets in the district catalog.

    For districts in the hardcoded catalog: seeds from catalog entries.
    For uncatalogued districts: triggers LLM auto-discovery.

    Idempotent — skips outlets whose domain already exists.  Returns total
    new Outlet records created.
    """
    from app.models import Outlet
    state_code = _parse_state_code(campaign.district, campaign.location)

    # Auto-discover outlets for districts not in the hardcoded catalog.
    # This runs first so discovered outlets are available to seed below.
    if campaign.district:
        _auto_discover_outlets(
            db,
            district=campaign.district,
            state_code=state_code,
            location=campaign.location,
            candidate=campaign.candidate_name,
        )

    outlets = get_local_outlets(campaign.district, state_code, db=db)
    created = 0
    for o in outlets:
        domain = o["domain"].lower()
        existing = db.query(Outlet).filter_by(domain=domain).first()
        if existing:
            # Backfill rss_url onto pre-existing Outlet records that predate the column
            if not existing.rss_url and o.get("rss_url"):
                existing.rss_url = o["rss_url"]
                created += 1  # counts as an update worth committing
            continue
        db.add(Outlet(
            name=o["name"],
            domain=domain,
            rss_url=o.get("rss_url"),
            outlet_type=o.get("outlet_type", "local_news"),
            authority_score=o.get("authority_score", 5),
            state=o.get("state"),
            city=o.get("city"),
            active=True,
        ))
        created += 1
    if created:
        db.commit()

    # Estimate monthly visitors for any outlets still missing it (one LLM call, batched).
    _estimate_outlet_traffic(db)

    return created


def _soft_duplicate(db: Session, title: str | None, source_name: str | None) -> SourceItem | None:
    title_key = re.sub(r"\s+", " ", (title or "").strip()).lower()
    source_key = re.sub(r"\s+", " ", (source_name or "").strip()).lower()
    if not title_key or not source_key:
        return None
    for item in db.query(SourceItem).filter(SourceItem.source_name.isnot(None)).limit(500).all():
        if (
            re.sub(r"\s+", " ", (item.title or "").strip()).lower() == title_key
            and re.sub(r"\s+", " ", (item.source_name or "").strip()).lower() == source_key
        ):
            return item
    return None


def run_fec_monitors(db: Session) -> dict:
    """Poll all active FEC monitors and ingest any new filings.

    Called by the scheduler on a daily cadence — FEC notices are filed
    within 24-48 hours, so daily polling is sufficient.

    Returns a summary dict with counts per monitor type.
    """
    from app.services import fec_monitor

    fec_monitors = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type.in_(["fec_filings", "fec_ie_district"]),
            SourceMonitor.active == True,  # noqa: E712
        )
        .all()
    )

    results = {"fec_filings": 0, "fec_ie_district": 0, "monitors_run": 0}

    for monitor in fec_monitors:
        if not monitor.query:
            continue
        try:
            if monitor.monitor_type == "fec_filings":
                # query = FEC candidate ID; name contains the candidate name
                candidate_name = monitor.name.replace("FEC: ", "").replace(" filings", "").strip()
                n = fec_monitor.poll_candidate_fec(db, monitor.query, candidate_name)
                results["fec_filings"] += n
            elif monitor.monitor_type == "fec_ie_district":
                n = fec_monitor.poll_district_ie(db, monitor.query)
                results["fec_ie_district"] += n
            results["monitors_run"] += 1
            monitor.last_checked_at = datetime.utcnow()
            monitor.updated_at = datetime.utcnow()
            db.commit()
        except Exception as exc:
            logger.warning("run_fec_monitors: monitor %d failed: %s", monitor.id, exc)

    return results


def _run_search_monitor(db: Session, monitor: SourceMonitor) -> int:
    if not monitor.query:
        return 0
    provider = get_search_provider()
    try:
        response = provider.search(monitor.query, limit=10)
    except Exception:
        monitor.last_checked_at = datetime.utcnow()
        monitor.updated_at = datetime.utcnow()
        db.commit()
        return 0
    added = 0
    for result in response.results[:10]:
        if not result.url:
            continue
        if db.query(SourceItem).filter_by(source_url=result.url).first():
            continue
        if _soft_duplicate(db, result.title, result.source_name):
            continue
        if ingestion.ingest_url(db, result.url, monitor.source_type or "news"):
            added += 1
    monitor.last_checked_at = datetime.utcnow()
    monitor.updated_at = datetime.utcnow()
    db.commit()
    return added


def _full_name(raw: str | None) -> str | None:
    """Convert FEC-format 'LAST, FIRST MIDDLE' to 'First Last' (quoted for search)."""
    if not raw:
        return None
    name = raw.strip()
    if "," in name:
        parts = [p.strip().title() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[1]} {parts[0]}".strip()
    return name.title()


def _load_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
        return [str(x).strip() for x in loaded if str(x).strip()] if isinstance(loaded, list) else []
    except Exception:
        return []


def run_historical_backfill(db: Session, *, force: bool = False, days_back: int = 180) -> dict:
    """Google News backfill on a sliding window of date-ranged queries.

    Idempotent: runs once per campaign (gated by extended_backfill_completed).
    With force=True, ignores the flag and re-runs. Safe to re-run anyway because
    ingest_rss dedupes on source_url and relevance is enforced downstream by the
    LLM scoring pipeline (so broader queries don't degrade signal; they just
    give the pipeline more raw input).

    Quality safeguards:
      • Quoted full-name queries reduce false positives vs. bare last names.
      • Each broad term is joined with the campaign location/district to scope
        results to the race (e.g. `healthcare "PA-08"` rather than bare `healthcare`).
      • Capped at ~120 query×window combinations to bound runtime.
      • URL dedup is enforced by ingest_rss → no duplicate SourceItems.
      • Existing relevance/frame-matching pipeline runs over backfilled articles
        unchanged, so off-topic results are filtered out at scoring time.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign:
        return {"skipped": True, "reason": "no campaign"}
    if getattr(campaign, "extended_backfill_completed", False) and not force:
        return {"skipped": True, "reason": "already completed"}

    opponents = db.query(Opponent).all()

    # Build queries. Each entry is the raw query string passed to Google News.
    # Prefer quoted full names for precision; fall back to last names where useful.
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str | None) -> None:
        if not q:
            return
        q = q.strip()
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        queries.append(q)

    # Candidate variants
    cand_full = _full_name(campaign.candidate_name)
    cand_last = _candidate_last_name(campaign.candidate_name) if campaign.candidate_name else None
    if cand_full:
        add(f'"{cand_full}"')
    if cand_last and cand_full and cand_last.lower() != cand_full.lower():
        add(cand_last)

    # Opponent variants
    for opp in opponents:
        opp_full = _full_name(opp.name)
        opp_last = _candidate_last_name(opp.name) if opp.name else None
        if opp_full:
            add(f'"{opp_full}"')
        if opp_last and opp_full and opp_last.lower() != opp_full.lower():
            add(opp_last)

    # District / geography
    if campaign.district:
        add(campaign.district)
        # Scoped issue/topic queries: `<issue> <district>` — keeps results race-scoped
        # so we don't pull in generic national stories about the issue.
        for issue in _load_json_list(campaign.key_priorities)[:5]:
            add(f'{issue} {campaign.district}')

    # Relevance keywords scoped to candidate (e.g. `"Paige Cognetti" healthcare`)
    if cand_full:
        for kw in _load_json_list(campaign.relevance_keywords)[:8]:
            add(f'"{cand_full}" {kw}')

    # Bound total queries to avoid runaway API calls
    queries = queries[:20]

    # Build sliding 30-day windows back to `days_back` days
    now = datetime.utcnow()
    window_count = max(1, days_back // 30)
    windows: list[tuple[str, str]] = []
    for i in range(window_count):
        before = now - timedelta(days=i * 30)
        after = now - timedelta(days=(i + 1) * 30)
        windows.append((after.strftime("%Y-%m-%d"), before.strftime("%Y-%m-%d")))

    total_added = 0
    total_attempts = 0
    failures = 0
    for query in queries:
        for after_date, before_date in windows:
            total_attempts += 1
            url = _gnews_url_with_dates(query, after_date, before_date)
            try:
                result = ingestion.ingest_rss(db, url, label=f"Backfill: {query} ({after_date})")
                total_added += result.added
            except Exception as exc:
                failures += 1
                logger.warning("backfill query failed: %s (%s)", query, exc)

    campaign.historical_backfill_completed = True
    campaign.extended_backfill_completed = True
    db.commit()
    return {
        "added": total_added,
        "queries": len(queries),
        "windows": len(windows),
        "attempts": total_attempts,
        "failures": failures,
        "days_back": days_back,
        "forced": force,
    }


def _normalize_person_name(raw: str) -> str:
    """Convert FEC-format 'LAST, FIRST MIDDLE' to 'First Last' for LLM prompts."""
    name = raw.strip()
    if "," in name:
        parts = [p.strip().title() for p in name.split(",", 1)]
        # parts[0] = Last, parts[1] = First [Middle]
        return f"{parts[1]} {parts[0]}".strip()
    return name.title()


def _lookup_twitter_handles(name: str, role_hint: str = "") -> list[str]:
    """Find all verified Twitter/X handles for a person.

    Strategy (in order):
      1. Web search — most reliable for current handles, especially local politicians.
         Searches "{name} twitter site:twitter.com OR site:x.com" and extracts
         handles from result URLs. No-op with MockSearchProvider.
      2. LLM fallback — used when web search finds nothing. Good for well-known
         figures whose handles are in training data, but prone to hallucination
         for local candidates. Every candidate is verified on Nitter before saving.

    Returns a list of verified bare handles (no @). Empty list if none found.
    """
    # Try web search first — more reliable than LLM for current social handles
    verified = _search_for_twitter_handles(name, role_hint)
    if verified:
        return verified

    # LLM fallback
    from app.services.llm_provider import get_provider, MockLLMProvider
    from app.services.twitter_scraper import extract_twitter_username, resolve_nitter_rss
    import json as _json

    try:
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            return []
        context = f" ({role_hint})" if role_hint else ""
        prompt = (
            f"List ALL known X/Twitter accounts for {name}{context}. "
            f"Include personal, official, campaign, and prior-role accounts — even if unverified. "
            f"Reply with ONLY a JSON array of @ handles, e.g. [\"@JohnSmith\", \"@RepJohnSmith\"]. "
            f"If you know of none, reply with exactly: []"
        )
        raw = (provider.complete(prompt) or "").strip()
        if not raw or raw == "[]":
            return []

        try:
            parsed = _json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError
            candidates = [extract_twitter_username(h) for h in parsed if isinstance(h, str)]
        except Exception:
            import re
            candidates = re.findall(r'@([A-Za-z0-9_]{1,50})', raw)
        candidates = [h for h in candidates if h]

        verified = []
        for handle in candidates:
            if resolve_nitter_rss(handle):
                verified.append(handle)
                logger.info("_lookup_twitter_handles: verified @%s for %r via LLM", handle, name)
            else:
                logger.debug("_lookup_twitter_handles: @%s not found on Nitter for %r", handle, name)
        return verified

    except Exception as exc:
        logger.warning("_lookup_twitter_handles: LLM fallback failed for %r: %s", name, exc)
        return []


def _search_for_twitter_handles(name: str, role_hint: str = "") -> list[str]:
    """Web search fallback: search for a person's Twitter handles when the LLM doesn't know them.

    Uses the configured search provider (no-op with MockSearchProvider).
    Extracts @handles from result URLs and snippets, then verifies each on Nitter.
    """
    import re
    from app.services.search_provider import get_search_provider
    from app.services.twitter_scraper import extract_twitter_username, resolve_nitter_rss

    try:
        provider = get_search_provider()
        # MockSearchProvider returns no results — skip gracefully
        if type(provider).__name__ == "MockSearchProvider":
            return []

        context = f" {role_hint}" if role_hint else ""
        query = f"{name}{context} twitter OR \"x.com\""
        response = provider.search(query, limit=10)

        candidates: list[str] = []
        for result in response.results:
            for text in [result.url or "", getattr(result, "snippet", "") or "", result.title or ""]:
                handle = extract_twitter_username(text)
                if handle and handle not in candidates:
                    candidates.append(handle)

        verified = []
        for handle in candidates:
            if resolve_nitter_rss(handle):
                verified.append(handle)
                logger.info("_search_for_twitter_handles: verified @%s for %r", handle, name)

        return verified

    except Exception as exc:
        logger.warning("_search_for_twitter_handles: failed for %r: %s", name, exc)
        return []


def _auto_populate_twitter_handles(db: Session, monitors: list[SourceMonitor]) -> None:
    """For newly created twitter_profile monitors with no handle, ask the LLM.

    Skips the journalists placeholder — those require manual identification.
    """
    from app.models import CampaignConfig, Opponent
    from app.services.twitter_scraper import ensure_twitter_feed

    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()
    opp_names = {_normalize_person_name(o.name): o for o in opponents}

    twitter_monitors = [
        m for m in monitors
        if m.monitor_type == "twitter_profile" and not m.query and not m.url
        and "journalists" not in m.name.lower()
    ]
    for monitor in twitter_monitors:
        raw_name = (
            monitor.name
            .replace(" X/Twitter profile", "")
            .replace(" Twitter profile", "")
            .strip()
        )
        person_name = _normalize_person_name(raw_name)

        # Build a role hint so the LLM has enough context for local candidates
        if campaign and person_name.split()[-1].lower() in (campaign.candidate_name or "").lower():
            role_hint = f"candidate for {campaign.office or 'office'}, {campaign.district or campaign.location or ''}"
        elif person_name in opp_names or raw_name in opp_names:
            role_hint = f"politician, {campaign.district or campaign.location or ''}" if campaign else "politician"
        else:
            role_hint = campaign.district or campaign.location or ""

        logger.info("auto_twitter: looking up handles for %r (role: %s)", person_name, role_hint)
        handles = _lookup_twitter_handles(person_name, role_hint)

        if not handles:
            logger.info("auto_twitter: no verified handles found for %r", person_name)
            continue

        # First handle goes on the original monitor; additional handles get new monitors
        for i, handle in enumerate(handles):
            if i == 0:
                target = monitor
            else:
                target = SourceMonitor(
                    name=f"{person_name} X/Twitter (@{handle})",
                    monitor_type="twitter_profile",
                    source_type=monitor.source_type,
                    category=monitor.category,
                    active=True,
                )
                db.add(target)
                db.flush()

            target.query = f"@{handle}"
            db.flush()
            ensure_twitter_feed(db, target)
            logger.info("auto_twitter: registered @%s for %r", handle, person_name)

    if twitter_monitors:
        db.commit()


def auto_setup_monitors(db: Session) -> dict:
    """Generate monitors for the current campaign and ingest new search monitors.

    Idempotent: duplicate monitors are skipped. Safe to call on every campaign save.
    Returns counts for generated/skipped monitors and ingested source items.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign:
        return {
            "generated": 0,
            "skipped": 0,
            "search_monitors_ingested": 0,
            "sources_ingested": 0,
            "ingested": 0,
        }

    # Seed Outlet records for the district before creating monitors so that
    # articles ingested immediately below can be authority-weighted correctly.
    _seed_campaign_outlets(db, campaign)

    suggestions = generate_monitors_for_campaign(campaign, db.query(Opponent).all(), db=db)

    created: list[SourceMonitor] = []
    skipped = 0
    for suggestion in suggestions:
        values = _to_values(suggestion)
        if _duplicate_query(db, values):
            skipped += 1
            continue
        monitor = SourceMonitor(**values)
        db.add(monitor)
        db.flush()
        _ensure_rss_feed(db, monitor)
        created.append(monitor)
    db.commit()

    # Auto-populate Twitter handles for newly created twitter_profile monitors.
    # Runs in the background of setup — a failed LLM/Nitter call just leaves
    # the monitor empty for manual entry, which is fine.
    _auto_populate_twitter_handles(db, created)

    search_monitors = [m for m in created if m.monitor_type == "search_query" and m.active]
    sources_ingested = 0
    for monitor in search_monitors:
        try:
            sources_ingested += _run_search_monitor(db, monitor)
        except Exception:
            pass

    # Immediately ingest any newly created RSS feeds so content appears without
    # waiting for the next scheduler tick.
    new_rss_feeds = [
        db.query(RssFeed).filter_by(url=m.url).first()
        for m in created
        if m.monitor_type == "rss" and m.url
    ]
    new_rss_feeds = [f for f in new_rss_feeds if f]
    from app.services.rss_ingestion import mark_rss_feed_fetched
    for feed in new_rss_feeds:
        try:
            result = ingestion.ingest_rss(db, feed.url, feed.name)
            sources_ingested += result.added
            feed.last_fetched_at = mark_rss_feed_fetched(db, feed.url)
            db.commit()
        except Exception:
            pass

    # Phase 2: try to auto-discover URLs for any remaining "X campaign website
    # check" manual placeholders and convert them to webpage monitors.  Failures
    # are non-fatal — they leave the manual placeholder intact with a stamped
    # last_checked_at acting as a 24-hour retry cooldown.
    websites_discovered = {"converted": 0, "failed": 0, "skipped_cooldown": 0}
    try:
        from app.services.monitor_url_discovery import convert_website_manuals_to_webpages
        websites_discovered = convert_website_manuals_to_webpages(db)
    except Exception as exc:
        logger.warning("auto_setup_monitors: website URL discovery failed: %s", exc)

    return {
        "generated": len(created),
        "skipped": skipped,
        "search_monitors_ingested": len(search_monitors),
        "sources_ingested": sources_ingested,
        "ingested": sources_ingested,
        "websites_discovered": websites_discovered.get("converted", 0),
        "websites_failed": websites_discovered.get("failed", 0),
    }


# ── Journalist auto-discovery ─────────────────────────────────────────────────
# Identify journalists who actively cover this race by extracting bylines from
# already-ingested articles, then look up their social handles via the same
# LLM + web-search + Nitter-verify path used for candidates/opponents.
#
# Key design choice: we discover journalists from the data we ALREADY have,
# not by asking an LLM "who covers PA-08". That yields:
#   • Proven relevance — they wrote about the race
#   • Frequency-weighted — daily reporters surface over one-off authors
#   • Self-updating — new bylines get captured as articles flow in
#   • Generalizable — no race-specific knowledge required, works for any campaign

_INSTITUTIONAL_BYLINES = {
    "associated press", "reuters", "ap", "afp", "agence france-presse",
    "bloomberg news", "the associated press", "staff report", "staff",
    "editorial board", "the editorial board", "wire report",
    "ap staff", "reuters staff", "newsroom", "the editors",
}

# Matches "Firstname Lastname"-style human names (two or more words, letters
# plus punctuation only). Rejects handles like "@RepBresnahan", URLs, emails,
# reddit names ("Fragrant-Pepper7710"), and Bluesky handles.
_BYLINE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]+(?:\s+[A-Za-z][A-Za-z .'\-]+)+$")

# Multi-author bylines like "Hailey Fuchs and Meredith Lee Hill" or
# "Predrag Milic, Reuters" — split on these and take the first author.
_AUTHOR_SPLIT_RE = re.compile(r"\s+(?:and|&)\s+|\s*[,;|/]\s*", re.IGNORECASE)
# Suffixes the byline often carries after the name: " for Spotlight PA",
# " | NBC News", "(staff writer)", etc. Strip them before the name-shape check.
_BYLINE_SUFFIX_RE = re.compile(
    r"\s+(?:for|of|with|at|—|-|–)\s+.+$|"   # " for Spotlight PA", " of Reuters"
    r"\s*\([^)]*\)$",                        # "(staff writer)"
    re.IGNORECASE,
)


# "By Author Name" anywhere in the first portion of body text. Anchored on
# a word-boundary "By"; the strict lookaheads prevent mid-sentence "passed
# by Congress" matches. Title Case required (uppercase initial + lowercase
# letter) so all-caps spans don't over-capture. Captures at most 4 name words.
_BODY_BYLINE_RE = re.compile(
    r"\b[Bb][Yy]\s+(?=[A-Z][a-z])"
    r"([A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})"
    r"(?=\s*(?:[.,\n|;:]|$|\s+(?:for|of|with|at|—|-|–)\s+))"
)
# Same idea for ALL-CAPS press-release bylines ("By JONATHAN J. COOPER ...",
# "By LIAM MAYO ..."). Captures exactly first + (optional middle initial) +
# last so a trailing all-caps dateline doesn't bleed into the capture. The
# downstream multi-author split + title-casing handles the rest.
_BODY_BYLINE_ALL_CAPS_RE = re.compile(
    r"\b[Bb][Yy]\s+"
    r"([A-Z][A-Z'\-]+"           # first name (2+ caps)
    r"(?:\s+[A-Z]\.)?"            # optional middle initial
    r"\s+[A-Z][A-Z'\-]+)"         # last name (2+ caps)
)
# Tokens that strongly suggest "this is a publication, not a person".
# Filters out two-word values like "Daily Mail", "Hindustan Times", "Red State"
# (passes name-shape but is an outlet).
_PUBLICATION_TOKENS = {
    "times", "news", "post", "daily", "tribune", "herald", "journal",
    "magazine", "press", "media", "report", "today", "weekly", "monthly",
    "gazette", "express", "review", "observer", "chronicle", "examiner",
    "wire", "service", "broadcasting", "network", "channel", "state",
    "republic", "nation", "dispatch", "sentinel", "ledger", "globe",
    "standard", "bulletin", "digest", "online", "newsroom",
}
# "Betsy McCaughey: The Geniuses in Congress" — opinion-column title format
# where the author's name leads the title separated by a colon.
_TITLE_BYLINE_RE = re.compile(
    r"^\s*([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})\s*:\s+\S"
)


def _byline_from_text(title: str | None, raw_text: str | None) -> str | None:
    """Extract a byline candidate from article title or body.

    Used as a fallback when SourceItem.source_author is NULL (RSS feed didn't
    carry a byline and HTML had no <meta name="author">). Returns a candidate
    string that the caller should still pass through _clean_byline for
    validation.
    """
    if title:
        m = _TITLE_BYLINE_RE.match(title)
        if m:
            return m.group(1)
    if raw_text:
        snippet = raw_text[:1500]
        m = _BODY_BYLINE_RE.search(snippet)
        if m:
            return m.group(1).strip()
        # Fallback for all-caps press-release bylines. Title-case the result
        # before returning so _clean_byline accepts it.
        m = _BODY_BYLINE_ALL_CAPS_RE.search(snippet)
        if m:
            captured = m.group(1).strip()
            return _titlecase_name(captured)
    return None


def _titlecase_name(s: str) -> str:
    """Convert "JONATHAN J. COOPER" → "Jonathan J. Cooper". Leaves Mc/Mac/O'
    prefixes alone (we can't reliably recover those from all-caps text).
    """
    parts = []
    for word in s.split():
        if word.endswith("."):
            parts.append(word.capitalize())
        elif "'" in word:
            # "O'BRIEN" → "O'Brien"
            head, _, tail = word.partition("'")
            parts.append(head.capitalize() + "'" + tail.capitalize())
        elif "-" in word:
            parts.append("-".join(p.capitalize() for p in word.split("-")))
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


def _clean_byline(raw: str | None, outlet_names: "set[str] | None" = None) -> str | None:
    """Normalize a raw source_author value into a journalist name, or return None.

    Handles:
      • "By Firstname Lastname" → "Firstname Lastname"
      • "Predrag Milic, The Associated Press" → "Predrag Milic"
      • "Hailey Fuchs and Meredith Lee Hill" → "Hailey Fuchs" (first author)
      • "Ann Rejrat for Spotlight PA" → "Ann Rejrat"
    Rejects institutional bylines, outlet names, handles, emails, URLs.
    Pure function — safe to unit-test without DB or LLM.
    """
    if not raw:
        return None
    name = raw.strip().removeprefix("By ").removeprefix("by ").strip(" \"'.,:")
    if not name:
        return None

    # Multi-author byline → take just the first author. Each segment is then
    # validated independently below.
    first = _AUTHOR_SPLIT_RE.split(name, maxsplit=1)[0].strip(" \"'.,:")
    if not first:
        return None
    name = first

    # Strip trailing descriptors like " for Spotlight PA" / " | NBC News" /
    # "(staff writer)" so the name-shape regex sees just the person's name.
    name = _BYLINE_SUFFIX_RE.sub("", name).strip(" \"'.,:")
    if not name:
        return None

    if name.lower() in _INSTITUTIONAL_BYLINES:
        return None
    if outlet_names and name.lower() in outlet_names:
        return None
    if not (2 <= len(name) <= 60 and _BYLINE_NAME_RE.match(name)):
        return None
    # Publication-shape detector: any word in the candidate hits the
    # publication-token blocklist. Catches "Red State", "Hindustan Times",
    # "Daily Mail", "Patriot Post", etc. that pass the name regex but are
    # actually outlets we haven't seeded into the Outlet table.
    tokens = {w.lower().strip(".") for w in name.split()}
    if tokens & _PUBLICATION_TOKENS:
        return None
    return name


def auto_discover_journalists(
    db: "Session",
    *,
    days_back: int = 30,
    min_articles: int = 2,
    max_journalists: int = 15,
    article_cap: int = 300,
) -> dict:
    """Auto-create twitter_profile monitors for journalists covering this race.

    Process recent race-relevant articles, LLM-extract the byline from each,
    rank authors by article count, and look up Twitter handles for the most
    frequent ones (using the existing LLM + Nitter-verify path).

    `min_articles` = how many race-relevant articles an author needs before
    they're worth a monitor. `max_journalists` caps the number we look up
    handles for per run (since handle lookup costs LLM calls + Nitter probes).

    Idempotent: skips authors who already have a twitter_profile monitor.
    """
    from collections import Counter
    from datetime import datetime as _dt, timedelta as _td
    from app.models import Outlet, SourceItem, SourceMonitor
    from app.services.twitter_scraper import ensure_twitter_feed

    cutoff = _dt.utcnow() - _td(days=days_back)
    articles = (
        db.query(SourceItem)
        .filter(
            SourceItem.created_at >= cutoff,
            SourceItem.race_relevance_score >= 50,
            # Need at least one source of byline data: RSS author field or
            # article body to regex-scan.
            (SourceItem.source_author.isnot(None) | SourceItem.raw_text.isnot(None)),
        )
        .order_by(SourceItem.created_at.desc())
        .limit(article_cap)
        .all()
    )

    if not articles:
        return {"processed": 0, "candidates_found": 0, "monitors_created": 0,
                "reason": "no recent race-relevant articles"}

    # Outlet names are deterministic byline rejects — "Times Leader" is the
    # publication, not a journalist. Pull them from the Outlet table so the
    # blocklist updates itself as new outlets are added.
    outlet_names = {
        (n or "").lower()
        for (n,) in db.query(Outlet.name).filter(Outlet.name.isnot(None)).all()
    }

    logger.info("journalist_discovery: reading bylines from %d articles", len(articles))

    # Pass 1 — read author from SourceItem.source_author (populated during
    # ingestion from RSS entry.author and HTML <meta name="author">). Fall
    # back to a regex on title + raw_text when the structured field is empty.
    # No LLM call needed.
    author_counts: Counter = Counter()
    author_outlets: dict = {}        # author -> set of outlets

    for art in articles:
        name = _clean_byline(art.source_author, outlet_names=outlet_names)
        if not name:
            candidate = _byline_from_text(art.title, art.raw_text)
            name = _clean_byline(candidate, outlet_names=outlet_names)
        if not name:
            continue
        author_counts[name] += 1
        author_outlets.setdefault(name, set()).add(art.source_name or "?")

    # Filter to journalists frequent enough to be worth monitoring.
    candidates = [
        (name, count) for name, count in author_counts.most_common(max_journalists)
        if count >= min_articles
    ]
    logger.info(
        "journalist_discovery: found %d candidates (≥%d articles in %d days) out of %d distinct bylines",
        len(candidates), min_articles, days_back, len(author_counts),
    )

    # Pass 2 — look up Twitter AND Bluesky handles for each candidate, skip
    # those already being monitored on either platform.
    from app.services.bluesky_scraper import lookup_bluesky_handles, ensure_bluesky_monitor

    twitter_created = 0
    bluesky_created = 0
    skipped_existing = 0
    skipped_no_social = 0

    reactivated = 0

    for name, count in candidates:
        existing_rows = (
            db.query(SourceMonitor)
            .filter(
                SourceMonitor.monitor_type.in_(("twitter_profile", "bluesky_profile")),
                SourceMonitor.name.ilike(f"%{name}%"),
            )
            .all()
        )
        if existing_rows:
            # If any are active, fine — already monitored.
            if any(m.active for m in existing_rows):
                skipped_existing += 1
                continue
            # All deactivated (auto-pruned earlier). Reactivate them —
            # the journalist is producing race-relevant content again.
            for m in existing_rows:
                m.active = True
                reactivated += 1
                logger.info(
                    "journalist_discovery: reactivated monitor id=%d %r (now producing race content)",
                    m.id, m.name,
                )
            db.flush()
            continue

        outlets = sorted(author_outlets.get(name, set()))[:2]
        role_hint = f"journalist at {', '.join(outlets)}" if outlets else "journalist"

        found_any = False

        # Twitter / X
        for handle in _lookup_twitter_handles(name, role_hint):
            monitor = SourceMonitor(
                name=f"{name} X/Twitter (@{handle})",
                monitor_type="twitter_profile",
                query=f"@{handle}",
                category="social",
                source_type="news",
                active=True,
            )
            db.add(monitor)
            db.flush()
            ensure_twitter_feed(db, monitor)
            twitter_created += 1
            found_any = True
            logger.info(
                "journalist_discovery: twitter monitor for %s @%s (%d articles, outlets: %s)",
                name, handle, count, ", ".join(outlets),
            )

        # Bluesky
        for handle in lookup_bluesky_handles(name, role_hint):
            new_id = ensure_bluesky_monitor(db, name=name, handle=handle)
            if new_id is not None:
                bluesky_created += 1
                found_any = True
                logger.info(
                    "journalist_discovery: bluesky monitor for %s @%s (%d articles)",
                    name, handle, count,
                )

        if not found_any:
            skipped_no_social += 1
            logger.info(
                "journalist_discovery: no verified social handle for %r (%d articles)",
                name, count,
            )

    db.commit()
    return {
        "processed": len(articles),
        "distinct_bylines": len(author_counts),
        "candidates_found": len(candidates),
        "twitter_monitors_created": twitter_created,
        "bluesky_monitors_created": bluesky_created,
        "reactivated": reactivated,
        "skipped_existing": skipped_existing,
        "skipped_no_social": skipped_no_social,
    }


# ── Auto-prune: deactivate monitors that never produced relevant content ──────
# Paired with auto-discovery, this completes the self-tuning loop:
#   auto-discovery adds journalists → auto-prune removes the ones who never
#   produced race-relevant posts → re-discovery can reactivate them later if
#   their writing trends race-relevant again.

def prune_unproductive_monitors(
    db: "Session",
    *,
    min_age_days: int = 30,
    min_posts: int = 15,
    relevance_threshold: int = 40,
    dry_run: bool = True,
) -> dict:
    """Soft-deactivate twitter/bluesky monitors that consistently produce
    irrelevant content. Conservative by design.

    A monitor is eligible for pruning only when ALL of:
      • monitor_type is twitter_profile or bluesky_profile (RSS not touched)
      • monitor was created ≥ `min_age_days` ago (give it grace to prove itself)
      • monitor has produced ≥ `min_posts` SourceItems
      • ZERO of those posts hit race_relevance_score ≥ `relevance_threshold`
      • monitor is NOT name-matched to the candidate or any opponent (protected)

    `dry_run=True` (default) logs what WOULD be pruned without applying.
    `dry_run=False` flips `active=False` on each pruned monitor. Already-
    deactivated monitors can be reactivated by the journalist auto-discovery
    if the same handle later starts producing race-relevant content.
    """
    from datetime import datetime as _dt, timedelta as _td
    from app.models import CampaignConfig, Opponent, SourceItem, SourceMonitor

    cutoff_created = _dt.utcnow() - _td(days=min_age_days)

    # Build the set of name terms that mark a monitor as protected. Includes
    # the candidate, every opponent, and their surnames in lowercase.
    protected_terms: set[str] = set()
    campaign = db.query(CampaignConfig).first()
    raw_names = []
    if campaign and campaign.candidate_name:
        raw_names.append(campaign.candidate_name)
    for opp in db.query(Opponent).all():
        if opp.name:
            raw_names.append(opp.name)
    for raw in raw_names:
        n = raw.strip().lower()
        if n:
            protected_terms.add(n)
        last = n.split(",")[0] if "," in n else n.split()[-1]
        last = last.strip()
        if last and len(last) >= 3:
            protected_terms.add(last)

    def _is_protected(monitor_name: str | None) -> bool:
        n = (monitor_name or "").lower()
        return any(term in n for term in protected_terms)

    def _strip_handle(query: str | None) -> str | None:
        if not query:
            return None
        h = query.strip().lstrip("@").strip()
        return h or None

    monitors = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type.in_(("twitter_profile", "bluesky_profile")),
            SourceMonitor.active == True,  # noqa: E712
            SourceMonitor.created_at < cutoff_created,
        )
        .all()
    )

    actions: list[dict] = []
    skipped_protected = 0
    skipped_no_handle = 0
    skipped_too_few_posts = 0
    skipped_has_relevant_post = 0

    for m in monitors:
        if _is_protected(m.name):
            skipped_protected += 1
            continue
        handle = _strip_handle(m.query)
        if not handle:
            skipped_no_handle += 1
            continue

        # Both Nitter (twitter_profile) and Bluesky (bluesky_profile) URLs
        # have the handle in the path, so a single LIKE pattern suffices.
        post_count = (
            db.query(SourceItem.id)
            .filter(SourceItem.source_url.like(f"%/{handle}/%"))
            .count()
        )
        if post_count < min_posts:
            skipped_too_few_posts += 1
            continue

        relevant_count = (
            db.query(SourceItem.id)
            .filter(
                SourceItem.source_url.like(f"%/{handle}/%"),
                SourceItem.race_relevance_score >= relevance_threshold,
            )
            .count()
        )
        if relevant_count > 0:
            skipped_has_relevant_post += 1
            continue

        # Eligible.
        reason = (
            f"auto-prune: 0/{post_count} posts cleared relevance ≥ "
            f"{relevance_threshold} after ≥{min_age_days}d"
        )
        actions.append({
            "monitor_id": m.id,
            "name": m.name,
            "handle": handle,
            "monitor_type": m.monitor_type,
            "post_count": post_count,
            "reason": reason,
        })
        if dry_run:
            logger.info("prune_monitors: WOULD deactivate id=%d (%s) — %s",
                        m.id, m.name, reason)
        else:
            m.active = False
            logger.info("prune_monitors: deactivated id=%d (%s) — %s",
                        m.id, m.name, reason)

    if not dry_run and actions:
        db.commit()

    return {
        "dry_run": dry_run,
        "eligible_for_review": len(monitors),
        "pruned": len(actions),
        "actions": actions,
        "skipped_protected": skipped_protected,
        "skipped_no_handle": skipped_no_handle,
        "skipped_too_few_posts": skipped_too_few_posts,
        "skipped_has_relevant_post": skipped_has_relevant_post,
    }
