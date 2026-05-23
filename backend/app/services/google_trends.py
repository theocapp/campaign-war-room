"""Google Trends data collection via pytrends.

Fetches daily search interest (0–100, Pennsylvania geo) for campaign-relevant
terms: candidate/opponent surnames, a race-level term, and any custom terms the
user configured. Stores results in GoogleTrendSnapshot for 90-day sparkline
display.

Google Trends is rate-limited (~5 requests/minute unauthenticated). We batch
terms in groups of 5 (Trends API max) and sleep between batches.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Geographies collected for every term, so the Analytics page can toggle views.
#   US-PA      — statewide Pennsylvania
#   US-PA-577  — Wilkes Barre-Scranton DMA (the NEPA media market, ~PA-08)
_GEOS = {
    "state": "US-PA",
    "local": "US-PA-577",
}
_DEFAULT_GEO = "US-PA"
_TIMEFRAME = "today 3-m"  # 90 days of daily data
_BATCH_SIZE = 5            # Google allows max 5 terms per request
_BATCH_DELAY = 8           # seconds between batches to avoid rate limits


def _last_name(raw: str) -> str:
    """Extract just the surname from a name in either 'First Last' or 'Last, First' form."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    last = raw.split(",")[0] if "," in raw else raw.split()[-1]
    return last.strip().title()


def _get_terms(db) -> list[str]:
    """Build the full list of terms to track.

    For the candidate and each opponent we track the surname alone. Google
    Trends uses broad match, so the surname ('Cognetti') already counts every
    search containing it — including the full name ('Paige Cognetti') — making
    a separate full-name term a redundant duplicate. Any user-configured
    trends_keywords are also included.
    Deduped, max 20 total to keep poll time reasonable.
    """
    from app.models import CampaignConfig, Opponent
    campaign = db.query(CampaignConfig).first()
    terms: list[str] = []

    def _add(t: str) -> None:
        t = (t or "").strip()
        if t and t not in terms:
            terms.append(t)

    # Candidate + opponents: surname only (broad match subsumes the full name)
    if campaign and campaign.candidate_name:
        _add(_last_name(campaign.candidate_name))

    for opp in db.query(Opponent).all():
        if opp.name:
            _add(_last_name(opp.name))

    # User-configured extras
    if campaign and campaign.trends_keywords:
        try:
            for t in json.loads(campaign.trends_keywords):
                _add(t)
        except (json.JSONDecodeError, TypeError):
            pass

    return terms[:20]


def _fetch_interest(terms: list[str], geo: str = _DEFAULT_GEO, timeframe: str = _TIMEFRAME) -> dict[str, list[dict]]:
    """Fetch interest-over-time for up to 5 terms. Returns {term: [{date, interest}]}."""
    from pytrends.request import TrendReq
    try:
        pt = TrendReq(hl="en-US", tz=300, timeout=(10, 30), retries=2, backoff_factor=0.5)
        pt.build_payload(terms, cat=0, timeframe=timeframe, geo=geo, gprop="")
        df = pt.interest_over_time()
        if df is None or df.empty:
            return {}
        result: dict[str, list[dict]] = {}
        for term in terms:
            if term not in df.columns:
                continue
            series = []
            for ts, row in df[term].items():
                series.append({"date": ts.strftime("%Y-%m-%d"), "interest": int(row)})
            result[term] = series
        return result
    except Exception as exc:
        logger.warning("google_trends: fetch failed for %s: %s", terms, exc)
        return {}


def collect_trends(db) -> dict:
    """Fetch and store Google Trends data for all configured terms.

    Collects each term for every geography in _GEOS (statewide + DMA).
    Idempotent — skips (term, date, geo) tuples already stored. Safe to run daily.
    Returns summary dict.
    """
    from app.models import GoogleTrendSnapshot

    terms = _get_terms(db)
    if not terms:
        logger.info("google_trends: no terms configured, skipping")
        return {"terms": 0, "geos": 0, "rows_added": 0}

    logger.info("google_trends: fetching %d terms across %d geos", len(terms), len(_GEOS))

    # Existing snapshots — keyed (term, date_str, geo) for dedup
    existing = {
        (row.term, row.snapshot_date.strftime("%Y-%m-%d"), row.geo)
        for row in db.query(
            GoogleTrendSnapshot.term,
            GoogleTrendSnapshot.snapshot_date,
            GoogleTrendSnapshot.geo,
        ).all()
    }

    rows_added = 0
    batches = [terms[i:i + _BATCH_SIZE] for i in range(0, len(terms), _BATCH_SIZE)]
    first_request = True

    for geo in _GEOS.values():
        for batch in batches:
            if not first_request:
                time.sleep(_BATCH_DELAY)
            first_request = False

            data = _fetch_interest(batch, geo=geo)
            for term, series in data.items():
                for point in series:
                    key = (term, point["date"], geo)
                    if key in existing:
                        continue
                    snap = GoogleTrendSnapshot(
                        term=term,
                        snapshot_date=datetime.strptime(point["date"], "%Y-%m-%d"),
                        interest=point["interest"],
                        geo=geo,
                    )
                    db.add(snap)
                    existing.add(key)
                    rows_added += 1

            try:
                db.commit()
            except Exception as exc:
                logger.warning("google_trends: commit failed: %s", exc)
                db.rollback()

    logger.info("google_trends: done — terms=%d geos=%d rows_added=%d",
                len(terms), len(_GEOS), rows_added)
    return {"terms": len(terms), "geos": len(_GEOS), "rows_added": rows_added}


def get_trends_series(db, days: int = 90, geo: str = _DEFAULT_GEO) -> list[dict]:
    """Return sparkline data for all tracked terms in the given geography.

    Returns: [{ term, series: [{date, interest}] }]
    """
    from app.models import GoogleTrendSnapshot

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(GoogleTrendSnapshot)
        .filter(
            GoogleTrendSnapshot.snapshot_date >= cutoff,
            GoogleTrendSnapshot.geo == geo,
        )
        .order_by(GoogleTrendSnapshot.term, GoogleTrendSnapshot.snapshot_date)
        .all()
    )

    by_term: dict[str, list[dict]] = {}
    for row in rows:
        by_term.setdefault(row.term, []).append({
            "date": row.snapshot_date.strftime("%Y-%m-%d"),
            "interest": row.interest,
        })

    return [{"term": term, "series": series} for term, series in by_term.items()]
