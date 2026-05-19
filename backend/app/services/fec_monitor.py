"""FEC filing monitor — polls api.fec.gov for campaign finance activity.

Two monitor types:
  fec_filings     — independent expenditure notices + fundraising reports
                    for a specific candidate (query = FEC candidate ID).
  fec_ie_district — all IE notices targeting a congressional district
                    (query = "STATE:DISTRICT_NUM", e.g. "PA:8").

Converts FEC API results into SourceItems with source_type="public_record"
so they flow through the standard relevance pipeline.

Requires FEC_API_KEY env var (free registration at https://api.data.gov/signup).
Falls back to DEMO_KEY which works at low request volume.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import SourceItem

logger = logging.getLogger(__name__)

FEC_BASE = "https://api.fec.gov/v1"
_API_KEY = os.environ.get("FEC_API_KEY", "DEMO_KEY")

# Only fetch expenditures filed in the last N days to avoid re-processing old data.
_LOOKBACK_DAYS = 7


def _fec_get(path: str, params: dict) -> dict | None:
    try:
        import requests
        r = requests.get(
            f"{FEC_BASE}{path}",
            params={"api_key": _API_KEY, **params},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("fec_monitor: GET %s failed: %s", path, exc)
        return None


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        return None


def _ie_to_source_item_fields(result: dict, candidate_name: str) -> dict | None:
    """Convert a schedule_e result to SourceItem field dict. Returns None if unusable."""
    spender = result.get("committee_name") or "Unknown committee"
    support_oppose = (result.get("support_oppose_indicator") or "").upper()
    action = "supporting" if support_oppose == "S" else "opposing"
    amount = result.get("expenditure_amount") or 0
    exp_date = result.get("expenditure_date") or result.get("receipt_date") or ""
    description = (result.get("expenditure_description") or "").strip()
    is_notice = bool(result.get("is_notice"))
    form_label = "24/48-hr IE notice" if is_notice else "independent expenditure"
    candidate_id = result.get("candidate_id") or ""

    title = (
        f"FEC {form_label}: {spender} files ${amount:,.0f} {action} {candidate_name}"
    )
    body = "\n".join(filter(None, [
        f"{spender} filed a Federal Election Commission {form_label} reporting "
        f"an independent expenditure of ${amount:,.2f} {action} {candidate_name}.",
        f"Date of expenditure: {exp_date}" if exp_date else None,
        f"Purpose: {description}" if description else None,
        f"Committee: {spender}",
        f"FEC candidate ID: {candidate_id}" if candidate_id else None,
    ]))

    url = (
        f"https://www.fec.gov/data/independent-expenditures/"
        f"?candidate_id={candidate_id}&is_notice={'true' if is_notice else 'false'}"
    )
    return {
        "title": title,
        "raw_text": body,
        "source_name": "FEC Filing",
        "source_url": url,
        "source_type": "public_record",
        "published_at": _parse_date(exp_date),
        "urgency": "high" if is_notice else "medium",
    }


def _filing_to_source_item_fields(result: dict) -> dict | None:
    filer = result.get("committee_name") or "Unknown committee"
    form_type = result.get("form_type") or "filing"
    receipt_date = result.get("receipt_date") or ""
    period_end = result.get("coverage_end_date") or ""
    committee_id = result.get("committee_id") or ""
    file_number = result.get("file_number") or ""

    title = f"FEC {form_type}: {filer} quarterly report (through {period_end})"
    body = "\n".join(filter(None, [
        f"{filer} filed {form_type} with the Federal Election Commission.",
        f"Receipt date: {receipt_date}" if receipt_date else None,
        f"Coverage period end: {period_end}" if period_end else None,
        f"File number: {file_number}" if file_number else None,
    ]))
    url = (
        f"https://www.fec.gov/data/filings/?committee_id={committee_id}"
        if committee_id else "https://www.fec.gov/data/filings/"
    )
    return {
        "title": title,
        "raw_text": body,
        "source_name": "FEC Filing",
        "source_url": url,
        "source_type": "public_record",
        "published_at": _parse_date(receipt_date),
        "urgency": "low",
    }


def _already_ingested(db: Session, title: str, url: str) -> bool:
    return bool(
        db.query(SourceItem).filter(SourceItem.title == title).first()
        or db.query(SourceItem).filter(SourceItem.source_url == url).first()
    )


def _ingest_fields(db: Session, fields: dict) -> bool:
    """Create a SourceItem from a dict of fields and run it through ingestion."""
    from app.services.ingestion import _create_and_analyze
    if _already_ingested(db, fields["title"], fields["source_url"]):
        return False
    item = SourceItem(
        title=fields["title"],
        raw_text=fields["raw_text"],
        source_name=fields["source_name"],
        source_url=fields["source_url"],
        source_type=fields["source_type"],
        published_at=fields.get("published_at"),
        urgency=fields.get("urgency", "medium"),
        ingested_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    try:
        _create_and_analyze(db, item)
        return True
    except Exception as exc:
        logger.warning("fec_monitor: ingest failed for %r: %s", fields["title"], exc)
        return False


def poll_candidate_fec(db: Session, fec_candidate_id: str, candidate_name: str) -> int:
    """Fetch IE notices and F3 fundraising filings for a specific FEC candidate ID.

    Returns the number of new SourceItems created.
    """
    since = (datetime.utcnow() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    created = 0

    # Independent expenditure 24/48-hr notices (most actionable)
    data = _fec_get("/schedules/schedule_e/", {
        "candidate_id": fec_candidate_id,
        "is_notice": "true",
        "min_date": since,
        "sort": "-expenditure_date",
        "per_page": 20,
    })
    for result in (data or {}).get("results", []):
        fields = _ie_to_source_item_fields(result, candidate_name)
        if fields and _ingest_fields(db, fields):
            created += 1

    # Historical IE (non-notice) filings — catches spending that missed the notice window
    data = _fec_get("/schedules/schedule_e/", {
        "candidate_id": fec_candidate_id,
        "is_notice": "false",
        "min_date": since,
        "sort": "-expenditure_date",
        "per_page": 10,
    })
    for result in (data or {}).get("results", []):
        fields = _ie_to_source_item_fields(result, candidate_name)
        if fields and _ingest_fields(db, fields):
            created += 1

    # Opponent's own fundraising reports (F3 quarterly)
    # First resolve candidate_id → principal committee_id
    cand_data = _fec_get(f"/candidates/{fec_candidate_id}/", {})
    committees = (cand_data or {}).get("results", [{}])
    committee_id = None
    if committees:
        # principal_committees is a list; take the first
        pcs = committees[0].get("principal_committees") or []
        if pcs:
            committee_id = pcs[0].get("id")

    if committee_id:
        data = _fec_get("/filings/", {
            "committee_id": committee_id,
            "form_type": "F3",
            "min_receipt_date": since,
            "sort": "-receipt_date",
            "per_page": 5,
        })
        for result in (data or {}).get("results", []):
            fields = _filing_to_source_item_fields(result)
            if fields and _ingest_fields(db, fields):
                created += 1

    logger.info(
        "fec_monitor: candidate=%s name=%r created=%d",
        fec_candidate_id, candidate_name, created,
    )
    return created


def poll_district_ie(db: Session, state_district_query: str) -> int:
    """Fetch all IE notices filed for a congressional district.

    query format: "STATE:DISTRICT_NUM" e.g. "PA:8"
    Returns the number of new SourceItems created.
    """
    parts = state_district_query.split(":")
    if len(parts) != 2:
        logger.warning("fec_monitor: invalid district query %r", state_district_query)
        return 0

    state, district_num = parts[0].upper(), parts[1].lstrip("0") or "0"
    since = (datetime.utcnow() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    data = _fec_get("/schedules/schedule_e/", {
        "candidate_office": "H",
        "candidate_office_state": state,
        "candidate_office_district": district_num.zfill(2),
        "is_notice": "true",
        "min_date": since,
        "sort": "-expenditure_date",
        "per_page": 20,
    })

    created = 0
    for result in (data or {}).get("results", []):
        candidate_name = result.get("candidate_name") or f"{state}-{district_num} candidate"
        fields = _ie_to_source_item_fields(result, candidate_name)
        if fields and _ingest_fields(db, fields):
            created += 1

    logger.info(
        "fec_monitor: district=%s created=%d", state_district_query, created
    )
    return created
