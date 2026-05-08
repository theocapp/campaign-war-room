"""
Election date inference for campaign setup.

General elections: derived from 2 U.S.C. § 7 (House) / § 1 (Senate) —
"first Tuesday after the first Monday in November."  This applies to all
federal races and to most state-level races that align with federal Election
Day.

Primary elections: state law sets the date each cycle.  The PRIMARY_CONFIG
table encodes each state's rule as either a fixed month/day or an nth-weekday
formula, sourced from NCSL primary date calendars and state election codes.
All entries are *rules*, not literal calendar dates; the year is always
computed from the formula.

Special / off-cycle elections: no predictable schedule → returns None so the
user can fill the date manually.
"""

import calendar
from datetime import date, datetime, timezone, timedelta
from typing import Optional


# Weekday constant matching Python's calendar / date.weekday() (0 = Mon, 1 = Tue …)
_TUE = calendar.TUESDAY  # 1


# ── Primary election rule table ───────────────────────────────────────────────
# Source: NCSL 2026 primary election calendar and individual state election codes.
# Format per entry:
#   {"rule": "nth_weekday", "month": M, "n": N, "weekday": W}
#   {"rule": "first_tue_after_first_mon", "month": M}   (mirrors the federal general rule)
# Keys are ISO 3166-2 state abbreviations (upper-case).
PRIMARY_CONFIG: dict[int, dict[str, dict]] = {
    2026: {
        "AL": {"rule": "nth_weekday",               "month": 6,  "n": 1, "weekday": _TUE},
        "AK": {"rule": "nth_weekday",               "month": 8,  "n": 3, "weekday": _TUE},
        "AZ": {"rule": "nth_weekday",               "month": 8,  "n": 4, "weekday": _TUE},
        "AR": {"rule": "nth_weekday",               "month": 5,  "n": 3, "weekday": _TUE},
        "CA": {"rule": "first_tue_after_first_mon", "month": 6},
        "CO": {"rule": "nth_weekday",               "month": 6,  "n": 4, "weekday": _TUE},
        "CT": {"rule": "nth_weekday",               "month": 8,  "n": 2, "weekday": _TUE},
        "DE": {"rule": "nth_weekday",               "month": 9,  "n": 2, "weekday": _TUE},
        "FL": {"rule": "nth_weekday",               "month": 8,  "n": 3, "weekday": _TUE},
        "GA": {"rule": "nth_weekday",               "month": 5,  "n": 3, "weekday": _TUE},
        "HI": {"rule": "nth_weekday",               "month": 8,  "n": 3, "weekday": _TUE},
        "ID": {"rule": "nth_weekday",               "month": 5,  "n": 3, "weekday": _TUE},
        "IL": {"rule": "nth_weekday",               "month": 3,  "n": 3, "weekday": _TUE},
        "IN": {"rule": "first_tue_after_first_mon", "month": 5},
        "IA": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "KS": {"rule": "first_tue_after_first_mon", "month": 8},
        "KY": {"rule": "nth_weekday",               "month": 5,  "n": 3, "weekday": _TUE},
        "LA": {"rule": "first_tue_after_first_mon", "month": 11},  # jungle primary
        "ME": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "MD": {"rule": "nth_weekday",               "month": 7,  "n": 2, "weekday": _TUE},
        "MA": {"rule": "nth_weekday",               "month": 9,  "n": 2, "weekday": _TUE},
        "MI": {"rule": "first_tue_after_first_mon", "month": 8},
        "MN": {"rule": "nth_weekday",               "month": 8,  "n": 2, "weekday": _TUE},
        "MS": {"rule": "first_tue_after_first_mon", "month": 8},
        "MO": {"rule": "first_tue_after_first_mon", "month": 8},
        "MT": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "NE": {"rule": "nth_weekday",               "month": 5,  "n": 2, "weekday": _TUE},
        "NV": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "NH": {"rule": "nth_weekday",               "month": 9,  "n": 2, "weekday": _TUE},
        "NJ": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "NM": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "NY": {"rule": "nth_weekday",               "month": 6,  "n": 4, "weekday": _TUE},
        "NC": {"rule": "first_tue_after_first_mon", "month": 5},
        "ND": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "OH": {"rule": "first_tue_after_first_mon", "month": 5},
        "OK": {"rule": "nth_weekday",               "month": 6,  "n": 3, "weekday": _TUE},
        "OR": {"rule": "nth_weekday",               "month": 5,  "n": 2, "weekday": _TUE},
        "PA": {"rule": "nth_weekday",               "month": 5,  "n": 3, "weekday": _TUE},
        "RI": {"rule": "nth_weekday",               "month": 9,  "n": 2, "weekday": _TUE},
        "SC": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "SD": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "TN": {"rule": "first_tue_after_first_mon", "month": 8},
        "TX": {"rule": "first_tue_after_first_mon", "month": 3},
        "UT": {"rule": "nth_weekday",               "month": 6,  "n": 4, "weekday": _TUE},
        "VT": {"rule": "nth_weekday",               "month": 8,  "n": 2, "weekday": _TUE},
        "VA": {"rule": "nth_weekday",               "month": 6,  "n": 2, "weekday": _TUE},
        "WA": {"rule": "first_tue_after_first_mon", "month": 8},
        "WV": {"rule": "nth_weekday",               "month": 5,  "n": 2, "weekday": _TUE},
        "WI": {"rule": "nth_weekday",               "month": 8,  "n": 2, "weekday": _TUE},
        "WY": {"rule": "nth_weekday",               "month": 8,  "n": 3, "weekday": _TUE},
    }
}


# ── Date arithmetic helpers ───────────────────────────────────────────────────

def _first_tue_after_first_mon(year: int, month: int) -> date:
    """Federal Election Day formula (2 U.S.C. § 7): first Tuesday after the
    first Monday of the given month."""
    first = date(year, month, 1)
    days_until_monday = (0 - first.weekday()) % 7  # 0 = Monday
    first_monday = first + timedelta(days=days_until_monday)
    return first_monday + timedelta(days=1)


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """Return the nth occurrence of *weekday* (0=Mon … 6=Sun) in month."""
    first = date(year, month, 1)
    days_ahead = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=days_ahead)
    return first_occurrence + timedelta(weeks=n - 1)


def _resolve_rule(rule: dict, year: int) -> date | None:
    kind = rule.get("rule")
    month = rule.get("month")
    if not kind or not month:
        return None
    if kind == "first_tue_after_first_mon":
        return _first_tue_after_first_mon(year, month)
    if kind == "nth_weekday":
        n = rule.get("n")
        weekday = rule.get("weekday")
        if n is None or weekday is None:
            return None
        return _nth_weekday(year, month, n, weekday)
    return None


def _to_utc_datetime(d: date) -> datetime:
    """Convert a date to midnight UTC datetime for consistent DB storage."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).replace(tzinfo=None)


# ── Public API ────────────────────────────────────────────────────────────────

def infer_election_date(
    election_type: Optional[str],
    year: Optional[int],
    state: Optional[str] = None,
) -> Optional[datetime]:
    """Return a UTC midnight datetime for the inferred election date, or None.

    Args:
        election_type: "general", "primary", or anything else (returns None).
        year:          The election year.
        state:         ISO 3166-2 two-letter state code (upper-case).  Used
                       only for primary inference.
    """
    if not year:
        return None

    election_type = (election_type or "").strip().lower()

    if election_type == "general":
        d = _first_tue_after_first_mon(year, 11)
        return _to_utc_datetime(d)

    if election_type == "primary":
        year_config = PRIMARY_CONFIG.get(year, {})
        state_key = (state or "").strip().upper()
        rule = year_config.get(state_key)
        if not rule:
            return None
        d = _resolve_rule(rule, year)
        return _to_utc_datetime(d) if d else None

    # special / runoff / other → no predictable schedule
    return None


# ── Campaign initialization ────────────────────────────────────────────────────

def initialize_campaign(db) -> dict:
    """Run the full initialization sequence for a newly configured campaign.

    Steps (run in order, each recorded individually):
      1. Validate — campaign profile exists and has a candidate name.
      2. Monitors — generate and apply monitor suggestions (idempotent).
      3. Ingestion — ingest content from new search monitors.
      4. Narratives — trigger a narrative refresh against collected sources.

    Returns a dict consumed by CampaignInitializeResult.  Each step records
    its own status so a partial failure doesn't hide what succeeded.
    """
    from app.models import CampaignConfig
    from app.services.monitors import auto_setup_monitors
    from app.services.narratives import refresh_narratives

    steps: list[dict] = []
    monitors_created = monitors_skipped = sources_ingested = narratives_refreshed = 0

    # Step 1 — Validate campaign
    campaign = db.query(CampaignConfig).first()
    if not campaign or not campaign.candidate_name:
        steps.append({
            "step": 1, "label": "Validate campaign",
            "status": "error", "detail": "No campaign profile found. Save your campaign first.",
        })
        return {
            "steps": steps,
            "monitors_created": 0, "monitors_skipped": 0,
            "sources_ingested": 0, "narratives_refreshed": 0,
            "message": "Campaign profile is required before initializing.",
            "initialized_at": datetime.utcnow(),
        }
    steps.append({
        "step": 1, "label": "Validate campaign",
        "status": "ok", "detail": f"Profile ready for {campaign.candidate_name}.",
    })

    # Step 2 — Generate and apply monitors
    try:
        monitor_result = auto_setup_monitors(db)
        monitors_created = monitor_result["generated"]
        monitors_skipped = monitor_result["skipped"]
        sources_ingested = monitor_result["ingested"]
        steps.append({
            "step": 2, "label": "Monitors created",
            "status": "ok" if monitors_created > 0 else "skipped",
            "detail": (
                f"{monitors_created} monitors created, {monitors_skipped} already existed."
            ),
        })
    except Exception as exc:
        steps.append({
            "step": 2, "label": "Monitors created",
            "status": "error", "detail": str(exc),
        })

    # Step 3 — Ingest search monitors (auto_setup_monitors already ran ingestion
    #           for brand-new monitors; record as its own step for UI clarity)
    steps.append({
        "step": 3, "label": "Ingest coverage",
        "status": "ok",
        "detail": f"{sources_ingested} sources ingested from search monitors.",
    })

    # Step 4 — Narrative refresh
    try:
        narratives = refresh_narratives(db)
        narratives_refreshed = len(narratives)
        steps.append({
            "step": 4, "label": "Narrative refresh",
            "status": "ok",
            "detail": f"{narratives_refreshed} narrative(s) tracked.",
        })
    except Exception as exc:
        steps.append({
            "step": 4, "label": "Narrative refresh",
            "status": "error", "detail": str(exc),
        })

    errors = [s for s in steps if s["status"] == "error"]
    message = (
        f"Campaign initialized for {campaign.candidate_name}. "
        f"{monitors_created} monitors, {sources_ingested} sources, "
        f"{narratives_refreshed} narratives."
        if not errors
        else f"Initialization completed with {len(errors)} error(s). Check steps for details."
    )

    return {
        "steps": steps,
        "monitors_created": monitors_created,
        "monitors_skipped": monitors_skipped,
        "sources_ingested": sources_ingested,
        "narratives_refreshed": narratives_refreshed,
        "message": message,
        "initialized_at": datetime.utcnow(),
    }
