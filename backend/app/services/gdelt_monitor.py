"""GDELT real-time monitoring and tone tracking.

Two jobs run on a schedule:
  1. poll_gdelt_realtime  — every 15 min, queries GDELT artlist for the last
     30 minutes of coverage and feeds new article URLs into the normal ingest
     pipeline. Complements RSS by catching articles from outlets we don't have
     feeds for.

  2. collect_tone_snapshots — daily, queries GDELT timelinetone for the past
     7 days per query term (candidate + opponents) and upserts daily tone rows
     into GdeltToneSnapshot.  Frontend can show a "media tone" sparkline.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# How far back (minutes) the real-time poll looks. 30 min gives a generous
# overlap window so we don't miss articles in case a poll fires late.
REALTIME_LOOKBACK_MINUTES = 30

# Pause between article ingest calls — be polite to article origin servers.
INGEST_DELAY = 0.5


def _gdelt_artlist(
    query: str, start: datetime, end: datetime, *, max_retries: int = 3,
) -> tuple[list[dict], Optional[str]]:
    """Fetch articles from GDELT DOC artlist mode for the given window.

    Returns (articles, error_reason). On success error_reason is None;
    on failure (after all retries) articles is [] and error_reason is the
    last exception string. The caller can count throttled calls separately
    from "0 articles in window" so the scheduler-health view doesn't
    silently report success when GDELT is rejecting us.

    Retries 429s with exponential backoff (10s, 20s, 40s) since GDELT
    throttle windows are typically ~minute-long. Other errors (network,
    parse, 5xx) also retry the same way — cheap insurance.
    """
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": "250",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "format": "json",
        "sourcelang": "english",
        "sourcecountry": "US",
    }
    last_exc: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(GDELT_DOC_API, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json().get("articles") or [], None
        except Exception as exc:
            last_exc = str(exc)
            if attempt == max_retries - 1:
                logger.warning(
                    "gdelt_artlist failed for '%s' after %d attempts: %s",
                    query, max_retries, exc,
                )
                return [], last_exc
            backoff = 10 * (2 ** attempt)  # 10s, 20s, 40s
            logger.info(
                "gdelt_artlist for '%s' failed (%s) — retry %d/%d in %ds",
                query, exc, attempt + 1, max_retries - 1, backoff,
            )
            time.sleep(backoff)
    return [], last_exc


def _gdelt_timelinetone(query: str, days_back: int = 7, *, max_retries: int = 4) -> list[dict]:
    """Fetch daily tone timeline from GDELT for the past N days.

    GDELT throttles by IP, so 429s are common while other GDELT jobs (the
    15-minute realtime poll, the historical backfill) are active. We retry with
    exponential backoff — the tone job runs only once a day, so it can afford
    to wait out a throttle rather than give up immediately.

    Returns a list of dicts with keys: date (YYYYMMDDHHMMSS), value (avg tone).
    """
    end = datetime.utcnow()
    start = end - timedelta(days=days_back)
    params = {
        "query": query,
        "mode": "timelinetone",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "format": "json",
        "sourcelang": "english",
        "sourcecountry": "US",
        "timezoom": "yes",  # collapse to daily buckets
    }
    for attempt in range(max_retries):
        try:
            resp = httpx.get(GDELT_DOC_API, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            # timelinetone returns {"timeline": [{"series": "Average Tone",
            #   "data": [{"date": "20260423T000000Z", "value": -2.5}, ...]}]}
            # — "series" is the label string; the points live under "data".
            timeline = data.get("timeline") or []
            if not timeline:
                return []
            return timeline[0].get("data") or []
        except Exception as exc:
            if attempt == max_retries - 1:
                logger.warning("gdelt_timelinetone failed for '%s' after %d attempts: %s",
                               query, max_retries, exc)
                return []
            # Exponential backoff: 15s, 30s, 60s — GDELT throttles clear slowly.
            backoff = 15 * (2 ** attempt)
            logger.info("gdelt_timelinetone for '%s' failed (%s) — retry %d/%d in %ds",
                        query, exc, attempt + 1, max_retries - 1, backoff)
            time.sleep(backoff)
    return []


def poll_gdelt_realtime(db) -> dict:
    """Query GDELT for articles from the past 30 minutes and ingest new ones.

    Called by the scheduler every 15 minutes. Ingests only URLs not already
    in the database — the normal ingest_url dedup handles that.
    """
    from app.models import CampaignConfig, Opponent
    from app.services.ingestion import ingest_url

    campaign = db.query(CampaignConfig).first()
    if not campaign or not campaign.candidate_name:
        return {"skipped": True, "reason": "no campaign"}

    opponents = db.query(Opponent).all()

    def _build_query(name: str) -> str:
        """Surname-only GDELT query — broader recall than an exact full-name
        phrase (catches headlines and second references) for the same cost."""
        raw = (name or "").strip()
        if not raw:
            return ""
        # Surname: first token of FEC "LAST, FIRST", else the last token.
        surname = (raw.split(",")[0] if "," in raw else raw.split()[-1]).strip()
        surname = surname.title()
        return f'"{surname}"' if surname else ""

    queries: list[tuple[str, str]] = []  # (query_string, label)
    cand_q = _build_query(campaign.candidate_name)
    if cand_q:
        queries.append((cand_q, campaign.candidate_name))
    for opp in opponents:
        opp_q = _build_query(opp.name or "")
        if opp_q:
            queries.append((opp_q, opp.name))

    if not queries:
        return {"skipped": True, "reason": "no queries"}

    end = datetime.utcnow()
    start = end - timedelta(minutes=REALTIME_LOOKBACK_MINUTES)

    seen_urls: set[str] = set()
    added = skipped = errors = 0
    throttled_queries = 0  # count of GDELT API calls that failed all retries
    last_throttle_reason: Optional[str] = None

    for query_str, label in queries:
        articles, fetch_err = _gdelt_artlist(query_str, start, end)
        if fetch_err is not None:
            # The fetch itself failed (e.g. 429 after retries). This is an
            # observability signal, not a "0 articles in window" success.
            throttled_queries += 1
            last_throttle_reason = fetch_err
        for art in articles:
            url = art.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                item = ingest_url(db, url, source_type="gdelt_realtime")
                if item is None:
                    skipped += 1
                else:
                    added += 1
            except Exception as exc:
                logger.debug("gdelt_realtime: ingest failed for %s: %s", url, exc)
                errors += 1
            time.sleep(INGEST_DELAY)
        time.sleep(0.5)  # be polite to GDELT between queries

    logger.info(
        "gdelt_realtime: added=%d skipped=%d errors=%d throttled=%d (window=%s→%s)",
        added, skipped, errors, throttled_queries,
        start.strftime("%H:%M"), end.strftime("%H:%M"),
    )
    return {
        "added": added,
        "skipped": skipped,
        "errors": errors,
        "throttled_queries": throttled_queries,
        "last_throttle_reason": last_throttle_reason,
    }


def collect_tone_snapshots(db, days_back: int = 7) -> dict:
    """Fetch daily tone data from GDELT and store in GdeltToneSnapshot.

    Called daily by the scheduler. Queries the past `days_back` days for the
    candidate and each opponent, then upserts one row per (label, date).
    """
    from app.models import CampaignConfig, Opponent, GdeltToneSnapshot
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    campaign = db.query(CampaignConfig).first()
    if not campaign or not campaign.candidate_name:
        return {"skipped": True, "reason": "no campaign"}

    opponents = db.query(Opponent).all()

    def _full_name(raw: str) -> str:
        """Normalize a name to natural 'First Last' order, title-cased.

        Campaign/FEC data stores names as 'LAST, FIRST' (e.g. 'COGNETTI, PAIGE');
        a quoted GDELT phrase search needs the natural order articles actually use.
        """
        raw = (raw or "").strip()
        if not raw:
            return ""
        if "," in raw:
            last, _, first = raw.partition(",")
            raw = f"{first.strip()} {last.strip()}"
        return " ".join(p.title() for p in raw.split() if p)

    entities: list[tuple[str, str, str]] = []  # (label, query, entity_type)
    cand_name = _full_name(campaign.candidate_name)
    if cand_name:
        entities.append((cand_name, f'"{cand_name}"', "candidate"))
    for opp in opponents:
        opp_name = _full_name(opp.name or "")
        if opp_name:
            entities.append((opp_name, f'"{opp_name}"', "opponent"))

    if not entities:
        return {"skipped": True, "reason": "no entities"}

    total_upserted = 0

    for label, query_str, entity_type in entities:
        series = _gdelt_timelinetone(query_str, days_back=days_back)

        # Dedupe GDELT points by date. The timelinetone response can include
        # multiple sub-day points for the same calendar day; we want one row
        # per day, taking the latest value (the most recent within the day).
        # Without this dedupe we'd hit UNIQUE(label, date) on the second insert.
        per_day: dict[datetime, float] = {}
        for point in series:
            date_str = point.get("date", "")
            tone_val = point.get("value")
            if not date_str or tone_val is None:
                continue
            try:
                snap_dt = datetime.strptime(date_str[:8], "%Y%m%d")
            except ValueError:
                continue
            per_day[snap_dt] = round(float(tone_val), 2)

        for snap_dt, tone_val in per_day.items():
            existing = (
                db.query(GdeltToneSnapshot)
                .filter(
                    GdeltToneSnapshot.query_label == label,
                    GdeltToneSnapshot.snapshot_date == snap_dt,
                )
                .first()
            )
            if existing:
                existing.avg_tone = tone_val
            else:
                db.add(GdeltToneSnapshot(
                    query_label=label,
                    entity_type=entity_type,
                    snapshot_date=snap_dt,
                    avg_tone=tone_val,
                ))
            total_upserted += 1

        try:
            db.commit()
        except Exception as exc:
            # Defensive: if the unique constraint still fires (concurrent
            # writer, schema drift, etc.), roll back and continue with the
            # next entity rather than aborting the whole job.
            logger.warning("gdelt_tone: commit failed for %s: %s", label, exc)
            db.rollback()
        time.sleep(0.5)  # be polite to GDELT between queries

    logger.info(
        "gdelt_tone: upserted %d snapshots for %d entities",
        total_upserted, len(entities),
    )
    return {"upserted": total_upserted, "entities": len(entities)}
