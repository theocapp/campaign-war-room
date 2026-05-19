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
                            location: str | None, candidate: str | None) -> int:
    """Use an LLM to discover local news outlets for an uncatalogued district.

    Validates each suggested RSS URL before saving.  Idempotent — skips domains
    that already exist as Outlet records.  Returns number of new outlets created.

    Only runs when the district has no outlets in either the hardcoded catalog
    or the DB already — safe to call on every campaign init.
    """
    import json as _json
    from app.models import Outlet
    from app.services.llm_provider import get_provider
    from app.services.source_discovery import get_local_outlets

    # Skip if the catalog already has DISTRICT-SPECIFIC entries (not just state-level ones).
    # State-level entries apply to every race in the state so they don't substitute for
    # local outlets specific to this district.
    dist_key = district.upper().strip()
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


def run_historical_backfill(db: Session) -> dict:
    """One-time 90-day Google News backfill on campaign initialization.

    Breaks 90 days into 3 monthly windows and fetches each key query per window.
    Marks CampaignConfig.historical_backfill_completed = True when done.
    Safe to call multiple times — skips if already completed.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign or campaign.historical_backfill_completed:
        return {"skipped": True}

    opponents = db.query(Opponent).all()
    candidate = campaign.candidate_name

    queries = []
    if candidate:
        cand_last = _candidate_last_name(candidate)
        if cand_last:
            queries.append(cand_last)
    for opp in opponents:
        opp_last = _candidate_last_name(opp.name)
        if opp_last:
            queries.append(opp_last)
    if campaign.district:
        queries.append(campaign.district)

    now = datetime.utcnow()
    windows = []
    for i in range(3):
        before = now - timedelta(days=i * 30)
        after = now - timedelta(days=(i + 1) * 30)
        windows.append((after.strftime("%Y-%m-%d"), before.strftime("%Y-%m-%d")))

    total_added = 0
    for query in queries:
        for after_date, before_date in windows:
            url = _gnews_url_with_dates(query, after_date, before_date)
            try:
                result = ingestion.ingest_rss(db, url, label=f"Backfill: {query} ({after_date})")
                total_added += result.added
            except Exception:
                pass

    campaign.historical_backfill_completed = True
    db.commit()
    return {"added": total_added, "queries": len(queries), "windows": len(windows)}


def _normalize_person_name(raw: str) -> str:
    """Convert FEC-format 'LAST, FIRST MIDDLE' to 'First Last' for LLM prompts."""
    name = raw.strip()
    if "," in name:
        parts = [p.strip().title() for p in name.split(",", 1)]
        # parts[0] = Last, parts[1] = First [Middle]
        return f"{parts[1]} {parts[0]}".strip()
    return name.title()


def _lookup_twitter_handle(name: str, role_hint: str = "") -> str | None:
    """Ask the LLM for a person's Twitter/X handle, then verify it against Nitter.

    Returns the bare handle (no @) if verified, or None if unknown or unverifiable.
    Nitter probe confirms the handle actually exists before we save it.
    """
    from app.services.llm_provider import get_provider, MockLLMProvider
    from app.services.twitter_scraper import extract_twitter_username, resolve_nitter_rss

    try:
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            return None
        context = f" ({role_hint})" if role_hint else ""
        prompt = (
            f"What is the official X/Twitter handle for {name}{context}? "
            f"Reply with ONLY the @ handle if you know it with confidence (e.g. @JohnSmith). "
            f"If you are not sure, reply with exactly: unknown"
        )
        raw = (provider.complete(prompt) or "").strip()
        if not raw or raw.lower() == "unknown":
            return None
        handle = extract_twitter_username(raw)
        if not handle:
            return None
        rss_url = resolve_nitter_rss(handle)
        return handle if rss_url else None
    except Exception as exc:
        logger.warning("_lookup_twitter_handle: failed for %r: %s", name, exc)
        return None


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
            opp = opp_names.get(person_name) or opp_names.get(raw_name)
            role_hint = f"politician, {campaign.district or campaign.location or ''}" if campaign else "politician"
        else:
            role_hint = campaign.district or campaign.location or ""

        logger.info("auto_twitter: looking up handle for %r (role: %s)", person_name, role_hint)
        handle = _lookup_twitter_handle(person_name, role_hint)
        if handle:
            monitor.query = f"@{handle}"
            db.flush()
            ensure_twitter_feed(db, monitor)
            logger.info("auto_twitter: set @%s for %r", handle, person_name)
        else:
            logger.info("auto_twitter: no verified handle found for %r", person_name)
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
    for feed in new_rss_feeds:
        try:
            result = ingestion.ingest_rss(db, feed.url, feed.name)
            sources_ingested += result.added
            feed.last_fetched_at = datetime.utcnow()
            db.commit()
        except Exception:
            pass

    return {
        "generated": len(created),
        "skipped": skipped,
        "search_monitors_ingested": len(search_monitors),
        "sources_ingested": sources_ingested,
        "ingested": sources_ingested,
    }
