import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig
from app.schemas import CampaignProfileOut, CampaignProfileIn, CampaignInitializeResult
from app.services.monitors import auto_setup_monitors, run_historical_backfill
from app.services.gdelt_backfill import run_gdelt_backfill
from app.services.campaign_setup import infer_election_date, initialize_campaign

router = APIRouter()
logger = logging.getLogger(__name__)


def _config_to_profile(config: CampaignConfig) -> CampaignProfileOut:
    profile = CampaignProfileOut.model_validate(config)
    if not config.election_date:
        return profile
    inferred = infer_election_date(
        config.election_type,
        config.election_date.year,
        _state_from_location(config.location),
    )
    inferred_flag = bool(inferred and inferred.date() == config.election_date.date())
    return profile.model_copy(update={"election_date_inferred": inferred_flag})


@router.post("/campaign/initialize", response_model=CampaignInitializeResult)
def campaign_initialize(
    days_back: int = 365,
    source: str | None = None,
    db: Session = Depends(get_db),
):
    """Run the full initialization sequence for a brand-new campaign.

    Synchronous (returns immediately when done):
      • Create campaign config + opponents from existing setup
      • Auto-generate the initial monitor set (RSS feeds, search queries,
        candidate/opponent Twitter/Bluesky monitors via LLM discovery)

    Background (kicked off as a daemon thread, status polled via
    /api/campaign/pipeline-status):
      • Historical backfill → feed discovery → rescore → rematch

    `source` selects URL discovery for the backfill:
      • None / unset (default) → auto-detect: BigQuery if GOOGLE_APPLICATION_CREDENTIALS
        is configured, otherwise the GDELT DOC API
      • "bigquery"             → force BigQuery (errors if creds missing)
      • "api"                  → force the GDELT DOC API path
    """
    import os
    import threading

    # Step 1 — synchronous: initialize monitors, config, etc.
    result = initialize_campaign(db)

    # Step 2 — background: full backfill chain. Auto-pick BigQuery when
    # credentials exist, since it returns ~3-5× the URL coverage with no
    # 250-per-request cap.
    chosen_source = source or (
        "bigquery" if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") else "api"
    )
    if chosen_source not in ("api", "bigquery"):
        raise HTTPException(status_code=400, detail=f"source must be 'api' or 'bigquery', got {source!r}")

    if _backfill_status["running"]:
        logger.info("campaign_initialize: backfill already running, not starting another")
    else:
        # Force=True so a re-initialize after a previous campaign also fires the chain.
        campaign = db.query(CampaignConfig).first()
        if campaign:
            campaign.extended_backfill_completed = False
            db.commit()

        _backfill_status["running"] = True
        _backfill_status["started_at"] = datetime.utcnow().isoformat()
        _backfill_status["finished_at"] = None
        _backfill_status["result"] = None
        threading.Thread(
            target=_run_backfill_then_rescore_then_rematch,
            args=(days_back, chosen_source),
            daemon=True,
        ).start()
        logger.info(
            "campaign_initialize: launched backfill chain in background (source=%s, days_back=%d)",
            chosen_source, days_back,
        )

    return CampaignInitializeResult(**result)


_backfill_status: dict = {"running": False, "started_at": None, "finished_at": None, "result": None}


def _run_backfill_then_rescore_then_rematch(days_back: int, source: str = "api") -> None:
    """Background worker: backfill → feed discovery → rescore → rematch.

    `source` controls URL discovery — "api" uses the GDELT DOC API (capped at
    250 URLs per request), "bigquery" uses the public gdelt-bq dataset (no
    cap, needs GOOGLE_APPLICATION_CREDENTIALS configured — see
    app/services/gdelt_bigquery.py for setup). Ingestion downstream of
    discovery is identical for both paths.

    Each stage runs only when the previous one completes successfully.
    Rescore uses only_unscored=True so it processes only the newly ingested
    articles, not the full corpus. auto_rematch=True wires rematch to fire
    automatically when rescore finishes.
    """
    from app.db import SessionLocal
    from app.services.feed_discovery import discover_feeds_from_gdelt
    from app.services import rescore as rescore_svc

    db = SessionLocal()
    try:
        if source == "bigquery":
            from app.services.gdelt_bigquery import run_gdelt_bigquery_backfill
            result = run_gdelt_bigquery_backfill(db, force=False, days_back=days_back)
        else:
            result = run_gdelt_backfill(db, force=False, days_back=days_back)
        _backfill_status["result"] = result
        if not result.get("skipped"):
            # Auto-create RSS feeds for domains GDELT discovered
            try:
                disc = discover_feeds_from_gdelt(db, min_articles=2)
                logger.info(
                    "feed_discovery after backfill: created=%d probed=%d",
                    disc["created"], disc["probed"],
                )
            except Exception as exc:
                logger.warning("feed_discovery after backfill failed: %s", exc)

            # Rescore newly ingested articles, then auto-trigger rematch when done
            try:
                r = rescore_svc.start_rescore(
                    db,
                    delay_seconds=2.0,
                    only_unscored=True,
                    auto_rematch=True,
                )
                logger.info(
                    "backfill: rescore queued — total=%d estimated_minutes=%s",
                    r.get("total", 0), r.get("estimated_minutes", "?"),
                )
            except Exception as exc:
                logger.warning("rescore queue after backfill failed: %s", exc)
    except Exception as exc:
        logger.warning("background backfill failed: %s", exc)
        _backfill_status["result"] = {"error": str(exc)}
    finally:
        db.close()
        _backfill_status["running"] = False
        _backfill_status["finished_at"] = datetime.utcnow().isoformat()


@router.post("/campaign/backfill-historical")
def campaign_backfill_historical(
    days_back: int = 180,
    force: bool = False,
    source: str = "bigquery",
    db: Session = Depends(get_db),
):
    """Ensure the extended historical backfill has been done. Idempotent unless force=true.

    `source` selects URL discovery:
      - "bigquery" (default) — public gdelt-bq dataset; no per-request cap, much
                                higher coverage; requires GOOGLE_APPLICATION_CREDENTIALS
                                (see SETUP_BIGQUERY.md)
      - "api"                 — GDELT DOC API; fallback when BigQuery is
                                unavailable. Capped at ~9k URLs by the API's
                                250-per-request limit. The realtime poll
                                (every 15 min) always uses this DOC API path —
                                only the one-time backfill defaults to BigQuery.
    """
    if source not in ("api", "bigquery"):
        raise HTTPException(status_code=400, detail=f"source must be 'api' or 'bigquery', got {source!r}")
    campaign = db.query(CampaignConfig).first()
    if force and campaign:
        campaign.extended_backfill_completed = False
        db.commit()
    if not force and campaign and getattr(campaign, "extended_backfill_completed", False):
        return {"status": "already_done"}
    if _backfill_status["running"]:
        return {"status": "running", "started_at": _backfill_status["started_at"]}

    import threading
    _backfill_status["running"] = True
    _backfill_status["started_at"] = datetime.utcnow().isoformat()
    _backfill_status["finished_at"] = None
    _backfill_status["result"] = None
    threading.Thread(
        target=_run_backfill_then_rescore_then_rematch,
        args=(days_back, source),
        daemon=True,
    ).start()
    return {"status": "started", "source": source}


@router.get("/campaign/backfill-status")
def campaign_backfill_status(db: Session = Depends(get_db)):
    """Poll-friendly status for the extended backfill."""
    from app.services.gdelt_backfill import backfill_progress
    campaign = db.query(CampaignConfig).first()
    done = bool(campaign and getattr(campaign, "extended_backfill_completed", False))
    return {
        "done": done,
        "running": _backfill_status["running"] or backfill_progress["running"],
        "started_at": backfill_progress["started_at"] or _backfill_status["started_at"],
        "finished_at": _backfill_status["finished_at"],
        "result": _backfill_status["result"],
        "progress_done": backfill_progress["done"],
        "progress_total": backfill_progress["total"],
        "progress_added": backfill_progress["added"],
        "progress_wayback": backfill_progress["wayback_hits"],
    }


@router.get("/campaign/pipeline-status")
def campaign_pipeline_status(db: Session = Depends(get_db)):
    """Single endpoint exposing the state of all four backfill pipeline stages.

    Each stage has: running (bool), done (bool), and optional progress fields.
    The frontend uses this to render a checklist banner.
    """
    from app.services.gdelt_backfill import backfill_progress
    from app.services.feed_discovery import feed_discovery_progress
    from app.services.rescore import get_status as rescore_status
    from app.services.narrative_frames import get_rematch_progress

    campaign = db.query(CampaignConfig).first()
    backfill_done = bool(campaign and getattr(campaign, "extended_backfill_completed", False))
    rs = rescore_status()
    rm = get_rematch_progress()
    fd = feed_discovery_progress

    return {
        "backfill": {
            "running": _backfill_status["running"] or backfill_progress["running"],
            "done": backfill_done,
            "progress_done": backfill_progress["done"],
            "progress_total": backfill_progress["total"],
            "progress_added": backfill_progress["added"],
        },
        "feed_discovery": {
            "running": fd["running"],
            "done": fd["done"],
            "probed": fd["probed"],
            "created": fd["created"],
        },
        "rescore": {
            "running": rs["running"],
            "done": bool(rs.get("finished_at")),
            "processed": rs["processed"],
            "total": rs["total"],
            "fallbacks": rs.get("fallbacks", 0),
            "started_at": rs.get("started_at"),
        },
        "rematch": {
            "running": rm["running"],
            "done": not rm["running"] and rm["done"] > 0,
            "done_count": rm["done"],
            "total": rm["total"],
        },
    }


@router.get("/campaign/trends-keywords")
def get_trends_keywords(db: Session = Depends(get_db)):
    """Return the current list of Google Trends keywords (auto-core + user-custom)."""
    from app.services.google_trends import _get_terms
    campaign = db.query(CampaignConfig).first()
    custom: list[str] = []
    if campaign and campaign.trends_keywords:
        try:
            custom = json.loads(campaign.trends_keywords) or []
        except Exception:
            pass
    all_terms = _get_terms(db)
    return {"terms": all_terms, "custom_terms": custom}


@router.post("/campaign/discover-journalists")
def trigger_journalist_discovery(
    days_back: int = 30,
    min_articles: int = 2,
    max_journalists: int = 15,
    db: Session = Depends(get_db),
):
    """Run journalist auto-discovery now (also runs daily via the scheduler).

    Extracts bylines from recent race-relevant articles and auto-creates
    twitter_profile monitors for journalists who appear in `>= min_articles`
    articles within the last `days_back` days.
    """
    from app.services.monitors import auto_discover_journalists
    result = auto_discover_journalists(
        db,
        days_back=days_back,
        min_articles=min_articles,
        max_journalists=max_journalists,
    )
    return result


@router.post("/campaign/prune-monitors")
def trigger_monitor_prune(
    dry_run: bool = True,
    min_age_days: int = 30,
    min_posts: int = 15,
    relevance_threshold: int = 40,
    db: Session = Depends(get_db),
):
    """Soft-deactivate social monitors that consistently produced irrelevant
    content. Counterpart to /campaign/discover-journalists — together they
    form a self-tuning loop that converges on the truly relevant accounts.

    Defaults to `dry_run=True` (preview only). Set `dry_run=false` to actually
    apply the deactivations. The weekly scheduler job runs with dry_run=False.
    """
    from app.services.monitors import prune_unproductive_monitors
    return prune_unproductive_monitors(
        db,
        min_age_days=min_age_days,
        min_posts=min_posts,
        relevance_threshold=relevance_threshold,
        dry_run=dry_run,
    )


@router.post("/campaign/trends-collect")
def trigger_trends_collect(db: Session = Depends(get_db)):
    """Manually trigger a Google Trends data collection run."""
    import threading
    from app.services.google_trends import collect_trends

    def _run():
        from app.db import SessionLocal
        with SessionLocal() as _db:
            collect_trends(_db)

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}


@router.get("/campaign", response_model=CampaignProfileOut)
def get_campaign(db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    if not config:
        config = CampaignConfig(candidate_name="My Campaign")
        db.add(config)
        db.commit()
        db.refresh(config)
    return _config_to_profile(config)


def _election_year(election_date, location: str | None) -> int | None:
    """Best-effort election year from supplied date or current year."""
    if election_date:
        try:
            return election_date.year
        except AttributeError:
            pass
    return datetime.utcnow().year


def _state_from_location(location: str | None) -> str | None:
    """Extract a two-letter state code from a location string, e.g. 'Riverton, PA' → 'PA'."""
    if not location:
        return None
    for part in reversed(location.replace(",", " ").split()):
        if len(part) == 2 and part.isalpha():
            return part.upper()
    return None


@router.put("/campaign", response_model=CampaignProfileOut)
def update_campaign(body: CampaignProfileIn, db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    if not config:
        config = CampaignConfig()
        db.add(config)

    config.candidate_name = body.candidate_name
    config.party = body.party
    config.race = body.race
    config.district = body.district
    config.office = body.office
    config.location = body.location
    config.race_level = body.race_level
    config.election_type = body.election_type
    config.district_number = body.district_number
    config.neighborhood_keywords = json.dumps(body.neighborhood_keywords) if body.neighborhood_keywords is not None else None
    config.sparse_race_mode = body.sparse_race_mode
    if body.election_date is not None:
        config.election_date = body.election_date
    elif not config.election_date:
        year = _election_year(body.election_date, body.location)
        config.election_date = infer_election_date(body.election_type, year, _state_from_location(body.location))
    config.campaign_message = body.campaign_message
    config.key_priorities = json.dumps(body.key_priorities) if body.key_priorities is not None else None
    config.relevance_keywords = json.dumps(body.relevance_keywords) if body.relevance_keywords is not None else None
    config.excluded_keywords = json.dumps(body.excluded_keywords) if body.excluded_keywords is not None else None
    config.geography_keywords = json.dumps(body.geography_keywords) if body.geography_keywords is not None else None
    if body.trends_keywords is not None:
        config.trends_keywords = json.dumps(body.trends_keywords)
    config.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(config)

    try:
        auto_setup_monitors(db)
    except Exception as exc:
        logger.warning("auto_setup_monitors failed during campaign update: %s", exc, exc_info=True)

    return _config_to_profile(config)
