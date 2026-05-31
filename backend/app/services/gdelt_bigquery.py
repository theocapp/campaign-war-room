"""Historical backfill via GDELT's public BigQuery dataset.

Why this exists:
The GDELT DOC API caps each request at 250 articles. Over a 365-day backfill
with a few queries that caps the URL ceiling around 9,000 — well below the
true article volume for a competitive race. GDELT publishes its full archive
to BigQuery (`gdelt-bq.gdeltv2.gkg_partitioned`), which has no per-request
cap. We use BigQuery only for URL discovery; the URLs flow through the same
scrape → cluster → score → frame-match pipeline as the DOC API path.

Cost model:
The gdelt-bq dataset is PUBLIC — no storage cost on our side. We only pay
query cost. BigQuery's free tier is 1 TB of data scanned per month, and every
query in this module always filters on _PARTITIONTIME (the table's date
partition column), so a 365-day backfill scans ~100 GB. The free tier easily
covers ~10 backfill runs per month.

One-time setup per environment:
  1. Create a Google Cloud project (free)
  2. Enable the BigQuery API in that project
  3. Create a service account with these roles:
       - BigQuery Data Viewer
       - BigQuery Job User
  4. Download its JSON key file
  5. Set GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/key.json in backend/.env
  6. `pip install google-cloud-bigquery` (already in requirements.txt)
"""
import json as _json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Seconds between article scrape attempts — be polite to target servers.
# Mirrors gdelt_backfill.SCRAPE_DELAY.
SCRAPE_DELAY = 0.3


def _check_credentials() -> None:
    """Raise a clear error if Google Cloud credentials aren't configured."""
    if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS env var not set. "
            "See app/services/gdelt_bigquery.py module docstring for setup steps."
        )


# GDELT V2Themes catalog covers thousands of taxonomy tags. These are the ones
# that reliably indicate political/electoral coverage. Combined with location
# matching, they catch issue/policy articles that don't name candidates.
# Reference: http://data.gdeltproject.org/api/v2/guides/LOOKUP-GKGTHEMES.TXT
_POLITICAL_THEMES = [
    "ELECTION",
    "ELECTION_FRAUD",
    "POL_PARTY",
    "POLITICAL_TURMOIL",
    "DEMOCRACY",
    "CAMPAIGN",
    "LEGISLATION",
    "GOV_INTERIOR",
    "TAX",
    "EPU_POLICY",          # economic policy uncertainty
    "USPEC_POLITICS_GENERAL1",
]


def _filter_location_hints(raw_keywords: list[str]) -> list[str]:
    """Pick only the location strings from CampaignConfig.geography_keywords
    that are safe to use as BigQuery LIKE patterns. We want city/region names,
    not 2-letter state codes (too broad) or text fragments (config junk).
    """
    out = []
    for k in raw_keywords or []:
        if not isinstance(k, str):
            continue
        s = k.strip()
        # 4-30 chars: long enough to not match everything ("PA" too broad),
        # short enough to not be a sentence ("PA-08 federal candidate filings…").
        if not (4 <= len(s) <= 30):
            continue
        # Skip pure-digit / shorthand tokens that look like district codes.
        if s.replace("-", "").isdigit():
            continue
        # Skip district codes themselves — V2Locations doesn't carry them.
        if s.upper().startswith(("PA-", "NY-", "CA-")) and len(s) <= 6:
            continue
        out.append(s)
    return out


def discover_urls_via_bigquery(
    surnames: list[str],
    *,
    days_back: int = 365,
    max_urls: int = 50000,
    location_hints: list[str] | None = None,
) -> list[dict]:
    """Query GDELT's public BigQuery dataset for article URLs.

    Two parallel match paths (combined with OR):
      Path A — any article whose V2Persons NER includes one of `surnames`
      Path B — any article whose V2Locations includes one of `location_hints`
               AND whose V2Themes includes a political/electoral tag
               (only enabled when location_hints is non-empty)

    Path B catches issue articles where the candidates aren't named directly —
    e.g. a healthcare-policy piece set in Scranton that mentions "the
    Republican incumbent" but not "Bresnahan".

    Returns a list of dicts: {url, source_name, first_seen, avg_tone}.

    Implementation notes:
      - V2Persons / V2Locations / V2Themes are semicolon-separated NER outputs.
      - We GROUP BY DocumentIdentifier to dedupe articles GDELT indexed twice.
      - Always filtered on _PARTITIONTIME — bounds scan cost.
    """
    _check_credentials()
    from google.cloud import bigquery

    if not surnames and not location_hints:
        return []

    client = bigquery.Client()
    end = datetime.utcnow()
    start = end - timedelta(days=days_back)

    # Path A: surname matching (always enabled when we have surnames)
    name_clauses_sql = ""
    if surnames:
        name_clauses_sql = " OR ".join(
            f"V2Persons LIKE @name{i}" for i in range(len(surnames))
        )

    # Path B: location + political theme combination
    loc_clauses_sql = ""
    cleaned_locations = _filter_location_hints(location_hints or [])
    if cleaned_locations:
        loc_or = " OR ".join(
            f"V2Locations LIKE @loc{i}" for i in range(len(cleaned_locations))
        )
        theme_or = " OR ".join(
            f"V2Themes LIKE @theme{i}" for i in range(len(_POLITICAL_THEMES))
        )
        loc_clauses_sql = f"(({loc_or}) AND ({theme_or}))"

    # Combine paths with OR
    where_parts = [c for c in (name_clauses_sql, loc_clauses_sql) if c]
    combined_where = " OR ".join(f"({p})" for p in where_parts)

    # V2Tone is "avg_tone, positive, negative, polarity, activity_density,
    # group_density, word_count" — we capture all 7 components so the
    # ingestion layer can store richer tone data per article.
    query = f"""
    SELECT
      DocumentIdentifier AS url,
      ANY_VALUE(SourceCommonName) AS source_name,
      MIN(DATE) AS first_seen,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[OFFSET(0)] AS FLOAT64)) AS avg_tone,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(1)] AS FLOAT64)) AS positive_score,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(2)] AS FLOAT64)) AS negative_score,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(3)] AS FLOAT64)) AS polarity,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(4)] AS FLOAT64)) AS activity_density,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(5)] AS FLOAT64)) AS group_density,
      AVG(SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(6)] AS FLOAT64)) AS word_count
    FROM `gdelt-bq.gdeltv2.gkg_partitioned`
    WHERE _PARTITIONTIME BETWEEN @start AND @end
      AND ({combined_where})
      AND DocumentIdentifier LIKE 'http%'
    GROUP BY DocumentIdentifier
    ORDER BY MIN(DATE) DESC
    LIMIT @max_urls
    """

    params = [
        bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
        bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
        bigquery.ScalarQueryParameter("max_urls", "INT64", max_urls),
    ]
    for i, name in enumerate(surnames):
        params.append(bigquery.ScalarQueryParameter(f"name{i}", "STRING", f"%{name}%"))
    for i, loc in enumerate(cleaned_locations):
        params.append(bigquery.ScalarQueryParameter(f"loc{i}", "STRING", f"%{loc}%"))
    for i, theme in enumerate(_POLITICAL_THEMES):
        params.append(bigquery.ScalarQueryParameter(f"theme{i}", "STRING", f"%{theme}%"))

    job_config = bigquery.QueryJobConfig(query_parameters=params)
    logger.info(
        "gdelt_bigquery: surnames=%s locations=%s themes=%d over %d days (max_urls=%d)",
        surnames, cleaned_locations, len(_POLITICAL_THEMES) if cleaned_locations else 0,
        days_back, max_urls,
    )

    job = client.query(query, job_config=job_config)
    rows = list(job.result())

    bytes_scanned = job.total_bytes_processed or 0
    logger.info(
        "gdelt_bigquery: returned %d URLs, scanned %.2f GB (~$%.4f beyond 1 TB free tier)",
        len(rows), bytes_scanned / 1e9, max(0, bytes_scanned - 1e12) / 1e12 * 5,
    )

    def _f(v):
        return float(v) if v is not None else None
    return [
        {
            "url": r.url,
            "source_name": r.source_name,
            "first_seen": _gdelt_int_date_to_iso(r.first_seen),
            "avg_tone": _f(r.avg_tone),
            # Full V2Tone field bundle for richer per-article tone storage.
            "tone": {
                "avg_tone": _f(r.avg_tone),
                "positive": _f(r.positive_score),
                "negative": _f(r.negative_score),
                "polarity": _f(r.polarity),
                "activity_density": _f(r.activity_density),
                "group_density": _f(r.group_density),
                "word_count": _f(r.word_count),
            },
        }
        for r in rows
    ]


def _gdelt_int_date_to_iso(n) -> Optional[str]:
    """GDELT GKG's DATE column is an INT64 in YYYYMMDDHHMMSS format
    (e.g. 20260523120000). Convert to a standard ISO datetime string, or None
    if the value can't be parsed."""
    if n is None:
        return None
    try:
        s = str(int(n)).zfill(14)
        if len(s) < 8:
            return None
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}"
    except (ValueError, TypeError):
        return None


def run_gdelt_bigquery_backfill(db, *, force: bool = False, days_back: int = 365) -> dict:
    """Historical backfill via BigQuery.

    Discovery: one BigQuery query against the public gdelt-bq dataset returns
    every URL with a candidate-surname person mention in the date range, no
    250-per-request cap.
    Ingestion: identical to the DOC API path — scrape each URL, cluster, link
    to outlet, commit. We share gdelt_backfill.backfill_progress so the UI's
    pipeline-status endpoint works for either backfill source.

    Idempotent: gated by extended_backfill_completed unless force=True. The
    URL-dedup in the ingest loop also means re-running is safe — already-
    ingested URLs are skipped fast.
    """
    from app.models import CampaignConfig, Opponent, SourceItem
    from app.services import gdelt_backfill as bf  # for shared backfill_progress
    from app.services.ingestion import _clean_html_with_quality, _parse_html_published_date
    from app.services.source_discovery import _candidate_last_name

    campaign = db.query(CampaignConfig).first()
    if not campaign:
        return {"skipped": True, "reason": "no campaign"}
    if getattr(campaign, "extended_backfill_completed", False) and not force:
        return {"skipped": True, "reason": "already completed"}

    # Build the surname set — same as the DOC API path.
    surnames: list[str] = []
    cand_last = _candidate_last_name(campaign.candidate_name or "")
    if cand_last:
        surnames.append(cand_last)
    for opp in db.query(Opponent).all():
        opp_last = _candidate_last_name(opp.name or "")
        if opp_last and opp_last not in surnames:
            surnames.append(opp_last)

    if not surnames:
        return {"skipped": True, "reason": "no surnames"}

    # Pull location hints from the campaign config (geography_keywords is a
    # JSON list of city/region strings). The function filters out short/junk
    # entries before sending to BigQuery.
    import json as _json
    location_hints: list[str] = []
    if campaign.geography_keywords:
        try:
            raw_locs = _json.loads(campaign.geography_keywords)
            if isinstance(raw_locs, list):
                location_hints = [s for s in raw_locs if isinstance(s, str)]
        except Exception:
            pass

    # Discovery — single BigQuery query (surname path + location/theme path).
    try:
        url_rows = discover_urls_via_bigquery(
            surnames, days_back=days_back, location_hints=location_hints,
        )
    except Exception as exc:
        logger.exception("gdelt_bigquery: discovery failed")
        return {"error": str(exc)}

    if not url_rows:
        logger.info("gdelt_bigquery: no URLs returned")
        campaign.extended_backfill_completed = True
        db.commit()
        return {"added": 0, "skipped": 0, "failed": 0, "total_urls": 0}

    # Ingestion — mirrors the per-URL loop in gdelt_backfill.run_gdelt_backfill.
    added = skipped = failed = 0

    bf.backfill_progress["running"] = True
    bf.backfill_progress["done"] = 0
    bf.backfill_progress["total"] = len(url_rows)
    bf.backfill_progress["started_at"] = datetime.utcnow().isoformat()
    bf.backfill_progress["added"] = 0
    bf.backfill_progress["wayback_hits"] = 0

    for row in url_rows:
        url = (row.get("url") or "").strip()
        if not url:
            continue

        try:
            # Fast dedup — skip URLs already ingested.
            if db.query(SourceItem.id).filter_by(source_url=url).first():
                skipped += 1
                bf.backfill_progress["done"] += 1
                continue

            try:
                resp = httpx.get(url, timeout=8, follow_redirects=True, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)",
                })
                resp.raise_for_status()
            except Exception:
                failed += 1
                bf.backfill_progress["done"] += 1
                time.sleep(SCRAPE_DELAY)
                continue

            title, body_text, quality_score, quality_label, quality_reasons = \
                _clean_html_with_quality(resp.text)
            published_date = _parse_html_published_date(resp.text)

            # Fall back to BigQuery's first_seen date when HTML has none.
            if not published_date and row.get("first_seen"):
                try:
                    published_date = datetime.fromisoformat(row["first_seen"])
                except Exception:
                    pass

            if not title:
                title = row.get("source_name") or url.rstrip("/").split("/")[-1].replace("-", " ").title() or url

            # Per-article GDELT tone — store the full V2Tone bundle for later
            # frame-level tone analysis. Cheap (~100 bytes JSON per row).
            tone_blob = row.get("tone")
            tone_json = _json.dumps(tone_blob) if tone_blob else None

            from app.services.ingestion import clean_title as _clean_title
            title = _clean_title(title) or ""
            item = SourceItem(
                title=title[:200],
                raw_text=body_text,
                source_url=url,
                source_name=row.get("source_name") or (url.split("/")[2] if "://" in url else url[:50]),
                source_type="gdelt_bigquery_backfill",
                published_at=published_date,
                extraction_quality_score=quality_score,
                extraction_quality_label=quality_label,
                extraction_quality_reasons=_json.dumps(quality_reasons),
                gdelt_tone=tone_json,
            )
            db.add(item)
            db.flush()

            from app.services import story_clustering
            story_clustering.assign_story_cluster_v2(db, item)

            from app.services.outlet_linking import build_outlet_index, link_outlet_to_item
            link_outlet_to_item(item, build_outlet_index(db))

            db.commit()
            added += 1
            bf.backfill_progress["added"] = added

        except Exception as exc:
            logger.warning("gdelt_bigquery: failed to ingest %s: %s", url, exc)
            try:
                db.rollback()
            except Exception:
                pass
            failed += 1

        bf.backfill_progress["done"] += 1
        time.sleep(SCRAPE_DELAY)

    bf.backfill_progress["running"] = False
    campaign.extended_backfill_completed = True
    db.commit()

    logger.info(
        "gdelt_bigquery: done — added=%d skipped=%d failed=%d total=%d",
        added, skipped, failed, len(url_rows),
    )
    return {
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "total_urls": len(url_rows),
        "source": "bigquery",
    }
