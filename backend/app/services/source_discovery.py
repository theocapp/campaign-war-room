"""Generate campaign-specific source monitor suggestions."""
import json
from typing import Any

from app.models import CampaignConfig, Opponent


def _terms(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    try:
        parsed = json.loads(value)
        return [str(v).strip() for v in parsed if str(v).strip()]
    except Exception:
        return [v.strip() for v in value.split(",") if v.strip()]


def _q(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


def _quoted(value: str | None) -> str | None:
    value = (value or "").strip()
    return f'"{value}"' if value else None


def _add(monitors: list[dict[str, Any]], seen: set[tuple], **data: Any) -> None:
    key = (
        data.get("monitor_type"),
        (data.get("query") or "").lower().strip(),
        (data.get("url") or "").lower().strip(),
        data.get("name", "").lower().strip(),
    )
    if key in seen:
        return
    seen.add(key)
    monitors.append({
        "source_type": "news",
        "active": True,
        "required_terms": [],
        "excluded_terms": [],
        **data,
    })


def generate_monitors_for_campaign(campaign_profile: CampaignConfig, opponents: list[Opponent]) -> list[dict[str, Any]]:
    candidate = campaign_profile.candidate_name
    office = campaign_profile.office or campaign_profile.race
    district = campaign_profile.district
    location = campaign_profile.location
    district_number = campaign_profile.district_number
    election_type = (campaign_profile.election_type or "").lower()
    race_level = (campaign_profile.race_level or "").lower()
    neighborhoods = _terms(campaign_profile.neighborhood_keywords)
    priorities = _terms(campaign_profile.key_priorities)
    required = [x for x in [candidate, district, location, office] if x]
    excluded = _terms(campaign_profile.excluded_keywords)
    small_race = bool(campaign_profile.sparse_race_mode or election_type in {"primary", "special"} or race_level in {"city", "local", "state"})
    geo_terms = [x for x in [district, district_number, location, *neighborhoods] if x]

    monitors: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    if candidate:
        _add(monitors, seen, name=f"{candidate} news search", monitor_type="search_query",
             query=_q(_quoted(candidate), _quoted(district) or _quoted(location)),
             category="candidate", required_terms=[candidate], excluded_terms=excluded,
             relevance_hint="Find reporting that mentions the candidate in the campaign geography.")
        if small_race:
            if office:
                _add(monitors, seen, name=f"{candidate} {office} search", monitor_type="search_query",
                     query=_q(_quoted(candidate), _quoted(office)), category="candidate",
                     required_terms=[candidate, office], excluded_terms=excluded,
                     relevance_hint="Small-race query anchored to candidate and office.")
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{candidate} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(candidate), _quoted(geo)), category="candidate",
                     required_terms=[candidate, geo], excluded_terms=excluded,
                     relevance_hint="Small-race query anchored to candidate and local geography.")
        _add(monitors, seen, name=f"{candidate} campaign website check", monitor_type="manual",
             category="candidate", required_terms=[candidate],
             relevance_hint="Add the candidate campaign website URL if known; do not assume an official URL.")
        _add(monitors, seen, name=f"{candidate} social check", monitor_type="manual",
             category="candidate", required_terms=[candidate],
             relevance_hint="Add official social profile URLs after verifying them manually.")
        if small_race:
            for label in ["Facebook", "Instagram", "X/Twitter", "Threads", "LinkedIn"]:
                _add(monitors, seen, name=f"{candidate} {label} check", monitor_type="manual",
                     category="candidate", required_terms=[candidate],
                     relevance_hint=f"Verify the candidate's official {label} page before adding a URL.")

    for opponent in opponents:
        if not opponent.name:
            continue
        _add(monitors, seen, name=f"{opponent.name} news search", monitor_type="search_query",
             query=_q(_quoted(opponent.name), _quoted(district) or _quoted(location)),
             category="opponent", source_type="opponent_statement",
             required_terms=[opponent.name], excluded_terms=excluded,
             relevance_hint="Track opponent mentions tied to the race geography.")
        if small_race and office:
            _add(monitors, seen, name=f"{opponent.name} {office} search", monitor_type="search_query",
                 query=_q(_quoted(opponent.name), _quoted(office)), category="opponent",
                 source_type="opponent_statement", required_terms=[opponent.name, office],
                 excluded_terms=excluded, relevance_hint="Small-race query anchored to opponent and office.")
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{opponent.name} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(opponent.name), _quoted(geo)), category="opponent",
                     source_type="opponent_statement", required_terms=[opponent.name, geo],
                     excluded_terms=excluded, relevance_hint="Small-race query anchored to opponent and local geography.")
        _add(monitors, seen, name=f"{opponent.name} campaign website check", monitor_type="manual",
             category="opponent", source_type="opponent_statement", required_terms=[opponent.name],
             relevance_hint="Add the opponent campaign website URL only after verifying it.")
        _add(monitors, seen, name=f"{opponent.name} social check", monitor_type="manual",
             category="opponent", source_type="opponent_statement", required_terms=[opponent.name],
             relevance_hint="Add verified opponent social profile URLs manually.")
        if candidate:
            _add(monitors, seen, name=f"{candidate} vs {opponent.name}", monitor_type="search_query",
                 query=_q(_quoted(candidate), _quoted(opponent.name)),
                 category="race", required_terms=[candidate, opponent.name], excluded_terms=excluded,
                 relevance_hint="Find coverage that compares the candidate and opponent directly.")

    if district:
        _add(monitors, seen, name=f"{district} election search", monitor_type="search_query",
             query=_q(_quoted(district), "election"), category="race",
             required_terms=[district], excluded_terms=excluded,
             relevance_hint="Track election coverage using the district name.")
    if office and district:
        _add(monitors, seen, name=f"{office} {district} race search", monitor_type="search_query",
             query=_q(_quoted(district), office), category="race",
             required_terms=[district, office], excluded_terms=excluded,
             relevance_hint="Track coverage of the office and district.")
    if location and office:
        _add(monitors, seen, name=f"{location} {office} election search", monitor_type="search_query",
             query=_q(_quoted(location), office, "election"), category="race",
             required_terms=[location], excluded_terms=excluded,
             relevance_hint="Track local election coverage.")
    if small_race:
        election_word = election_type or "primary"
        for geo in geo_terms[:6]:
            if office:
                _add(monitors, seen, name=f"{geo} {office} {election_word} search", monitor_type="search_query",
                     query=_q(_quoted(geo), _quoted(office), election_word),
                     category="race", required_terms=[geo, office], excluded_terms=excluded,
                     relevance_hint="Sparse-race query combining local geography, office, and election type.")
            if candidate:
                _add(monitors, seen, name=f"{geo} {candidate} search", monitor_type="search_query",
                     query=_q(_quoted(geo), _quoted(candidate)),
                     category="race", required_terms=[geo, candidate], excluded_terms=excluded,
                     relevance_hint="Sparse-race neighborhood query for candidate mentions.")

    for priority in priorities:
        if district:
            _add(monitors, seen, name=f"{priority} in {district}", monitor_type="search_query",
                 query=_q(_quoted(district), priority), category="issue",
                 required_terms=[priority, district], excluded_terms=excluded,
                 relevance_hint="Find district-specific coverage of a campaign priority.")
        if location:
            _add(monitors, seen, name=f"{priority} in {location}", monitor_type="search_query",
                 query=_q(_quoted(location), priority), category="issue",
                 required_terms=[priority, location], excluded_terms=excluded,
                 relevance_hint="Find local coverage of a campaign priority.")
        if candidate:
            _add(monitors, seen, name=f"{candidate} on {priority}", monitor_type="search_query",
                 query=_q(_quoted(candidate), priority), category="issue",
                 required_terms=[candidate, priority], excluded_terms=excluded,
                 relevance_hint="Find candidate statements or coverage tied to a priority.")
        for opponent in opponents[:3]:
            _add(monitors, seen, name=f"{opponent.name} on {priority}", monitor_type="search_query",
                 query=_q(_quoted(opponent.name), priority), category="issue",
                 source_type="opponent_statement", required_terms=[opponent.name, priority],
                 excluded_terms=excluded, relevance_hint="Find opponent claims tied to a campaign priority.")
        if small_race:
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{priority} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(geo), priority), category="issue",
                     required_terms=[priority, geo], excluded_terms=excluded,
                     relevance_hint="Sparse-race issue query anchored to local geography.")

    public_record_names = [
        "FEC candidate or committee check",
        "State election board check",
        "County election board check",
        "Campaign finance filing check",
        "Ballot access and deadline check",
    ]
    for name in public_record_names:
        _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
             category="public_record", required_terms=required,
             relevance_hint="Configure the verified public-record page for this race; no official URL is assumed.")

    if small_race:
        for name in [
            "NYC Board of Elections candidate list check",
            "Local party endorsement page check",
            "Community newspaper candidate guide check",
            "Tenant union endorsement check",
            "Labor union endorsement check",
            "Good-government group questionnaire check",
            "Local civic association forum check",
        ]:
            _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
                 category="endorsement_or_election_board", required_terms=required,
                 relevance_hint="Sparse races often surface through election boards, endorsement groups, questionnaires, and forums; add verified URLs manually.")

    for name in ["City council agenda check", "County commission agenda check"]:
        _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
             category="local_government", required_terms=[x for x in [location, district] if x],
             relevance_hint="Add verified local agenda URLs for recurring manual review.")
    if any("school" in p.lower() or "education" in p.lower() for p in priorities):
        _add(monitors, seen, name="School board agenda check", monitor_type="manual",
             source_type="public_record", category="local_government",
             relevance_hint="Add the verified school board agenda page if education is a campaign priority.")
    if any("job" in p.lower() or "economy" in p.lower() or "economic" in p.lower() for p in priorities):
        _add(monitors, seen, name="Economic development and layoffs check", monitor_type="manual",
             source_type="public_record", category="local_government",
             relevance_hint="Track local employer layoffs, WARN notices, and economic development agendas.")

    return monitors
