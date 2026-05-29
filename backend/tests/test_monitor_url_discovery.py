"""Tests for monitor_url_discovery service.

Covers:
  • Happy path: search → LLM pick → HTTP check → monitor flipped to webpage
  • Domain blocklist filters obvious junk before LLM
  • LLM rejection (judge returns 0)
  • HTTP check failure (non-2xx, non-HTML)
  • RETRY_COOLDOWN_HOURS cooldown skips fresh attempts
  • Mock search provider short-circuits to no-op
  • Idempotency: webpage monitors are not re-discovered
  • Already-set URL on a manual monitor is treated as eligible (we still try)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceMonitor


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def campaign(db):
    cfg = CampaignConfig(
        candidate_name="Paige Cognetti",
        office="US House",
        district="PA-08",
        location="Scranton/Wilkes-Barre, PA-08",
    )
    db.add(cfg)
    db.add(Opponent(name="Rob Bresnahan"))
    db.commit()
    return cfg


def _make_monitor(db, name: str, category: str = "candidate") -> SourceMonitor:
    m = SourceMonitor(
        name=name,
        monitor_type="manual",
        category=category,
        source_type="news" if category == "candidate" else "opponent_statement",
        active=True,
    )
    db.add(m)
    db.commit()
    return m


def _sresult(url: str, title: str = "", snippet: str = ""):
    """Build a SearchResult-shaped object (duck-typed for the filter code)."""
    return SimpleNamespace(url=url, title=title, snippet=snippet)


def _stub_search(results: list):
    """Build a stub search provider returning the supplied results."""
    class _Provider:
        name = "stub"
        def search(self, query, limit=10):
            return SimpleNamespace(results=results, provider="stub", message=None)
    return _Provider()


def _stub_judge(reply: str):
    """Build a stub LLM judge that always returns `reply`."""
    class _Provider:
        def complete(self, prompt: str) -> str:
            return reply
    return _Provider()


# ── _filter_candidates / blocklist ───────────────────────────────────────────

def _campaign_affinity(person: str):
    """Affinity fn closure matching the campaign-website discover function."""
    from app.services.monitor_url_discovery import _affinity_campaign_website
    return lambda host: _affinity_campaign_website(host, person)


def test_filter_drops_blocked_hosts():
    from app.services.monitor_url_discovery import _filter_candidates
    results = [
        _sresult("https://en.wikipedia.org/wiki/Paige_Cognetti", "wiki"),
        _sresult("https://ballotpedia.org/Paige_Cognetti"),
        _sresult("https://www.facebook.com/cognetti"),
        _sresult("https://twitter.com/cognetti"),
        _sresult("https://www.fec.gov/data/candidate/H4PA08000/"),
        _sresult("https://www.cognettiforcongress.com/", "Paige Cognetti for Congress"),
    ]
    out = _filter_candidates(results, _campaign_affinity("Paige Cognetti"))
    assert len(out) == 1
    assert out[0].host == "cognettiforcongress.com"


def test_filter_dedupes_by_host():
    from app.services.monitor_url_discovery import _filter_candidates
    results = [
        _sresult("https://cognettiforcongress.com/issues"),
        _sresult("https://cognettiforcongress.com/about"),
        _sresult("https://www.cognettiforcongress.com/donate"),
        _sresult("https://news.example.com/article-about-cognetti"),
    ]
    out = _filter_candidates(results, _campaign_affinity("Paige Cognetti"))
    hosts = [c.host for c in out]
    assert hosts.count("cognettiforcongress.com") == 1
    assert "news.example.com" in hosts


def test_filter_ranks_by_affinity():
    from app.services.monitor_url_discovery import _filter_candidates
    results = [
        _sresult("https://example.org/news"),
        _sresult("https://cognetti.com/"),
        _sresult("https://cognettiforcongress.com/"),
    ]
    out = _filter_candidates(results, _campaign_affinity("Paige Cognetti"))
    assert out[0].host == "cognettiforcongress.com"
    assert out[1].host == "cognetti.com"


# ── _is_blocked_host ─────────────────────────────────────────────────────────

def test_blocked_host_matches_parent_suffix():
    from app.services.monitor_url_discovery import _is_blocked_host
    assert _is_blocked_host("en.wikipedia.org")
    assert _is_blocked_host("m.facebook.com")
    assert _is_blocked_host("mobile.twitter.com")
    # Custom campaign domains are NOT blocked
    assert not _is_blocked_host("cognettiforcongress.com")
    assert not _is_blocked_host("bresnahanforcongress.com")


# ── _person_from_website_monitor ─────────────────────────────────────────────

def test_person_extraction_from_monitor_name():
    from app.services.monitor_url_discovery import _person_from_website_monitor
    assert _person_from_website_monitor("Paige Cognetti campaign website check") == "Paige Cognetti"
    assert _person_from_website_monitor("Rob Bresnahan campaign website check") == "Rob Bresnahan"
    assert _person_from_website_monitor("Some other monitor") is None


# ── convert_manuals_to_webpages: happy path ──────────────────────────────────

def test_happy_path_converts_to_webpage(db, campaign, monkeypatch):
    """Search finds the campaign site, LLM picks it, HTTP check passes,
    monitor flips from manual to webpage."""
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://www.cognettiforcongress.com/",
                     title="Paige Cognetti for Congress",
                     snippet="Official campaign website"),
            _sresult("https://en.wikipedia.org/wiki/Paige_Cognetti"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)

    assert result["converted"] == 1
    assert result["failed"] == 0

    db.refresh(m)
    assert m.monitor_type == "webpage"
    assert m.url == "https://www.cognettiforcongress.com/"
    assert m.last_checked_at is not None


# ── LLM rejects ──────────────────────────────────────────────────────────────

def test_llm_rejection_keeps_manual(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://news.example.com/cognetti-runs-for-office"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("0"),  # judge rejects all
    )

    result = mod.convert_website_manuals_to_webpages(db)

    assert result["converted"] == 0
    assert result["failed"] == 1

    db.refresh(m)
    assert m.monitor_type == "manual"
    assert m.url is None
    # last_checked_at IS stamped (so cooldown kicks in)
    assert m.last_checked_at is not None


# ── HTTP check fails ─────────────────────────────────────────────────────────

def test_http_check_failure_keeps_manual(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://cognettiforcongress.com/",
                     title="Paige Cognetti for Congress"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: False)

    result = mod.convert_website_manuals_to_webpages(db)

    assert result["converted"] == 0
    assert result["failed"] == 1
    db.refresh(m)
    assert m.monitor_type == "manual"
    assert m.url is None


# ── Cooldown ─────────────────────────────────────────────────────────────────

def test_cooldown_skips_fresh_attempts(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")
    # Pretend we tried 1 hour ago.
    m.last_checked_at = datetime.utcnow() - timedelta(hours=1)
    db.commit()

    # Even if search would succeed, we shouldn't even call it.
    call_count = {"n": 0}
    def _err_search():
        call_count["n"] += 1
        raise AssertionError("should not call search during cooldown")
    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider", _err_search,
    )

    result = mod.convert_website_manuals_to_webpages(db)

    assert result["skipped_cooldown"] == 1
    assert result["converted"] == 0
    assert call_count["n"] == 0

    db.refresh(m)
    assert m.monitor_type == "manual"


def test_cooldown_expires_after_threshold(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")
    # Pretend we tried 25 hours ago — past the 24h cooldown.
    m.last_checked_at = datetime.utcnow() - timedelta(hours=25)
    db.commit()

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([_sresult("https://cognettiforcongress.com/")]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_website_manuals_to_webpages(db)
    assert result["converted"] == 1
    assert result["skipped_cooldown"] == 0


# ── Mock search provider short-circuits ──────────────────────────────────────

def test_mock_search_provider_short_circuits(db, campaign, monkeypatch):
    """When no real search provider is configured, the discovery service
    should fail gracefully without attempting to make any LLM calls."""
    from app.services import monitor_url_discovery as mod
    from app.services.search_provider import MockSearchProvider

    m = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: MockSearchProvider(),
    )
    # Judge should never be called.
    def _err_judge():
        raise AssertionError("judge should not be called when search is mock")
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider", _err_judge,
    )

    result = mod.convert_website_manuals_to_webpages(db)
    assert result["converted"] == 0
    assert result["failed"] == 1
    assert "mock" in result["details"]["failed"][0]["reason"].lower()


# ── Idempotency: already-webpage monitor not re-discovered ───────────────────

def test_webpage_monitor_not_eligible(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    # A monitor that already became a webpage on a previous run.
    m = SourceMonitor(
        name="Paige Cognetti campaign website check",
        monitor_type="webpage",
        url="https://cognettiforcongress.com/",
        category="candidate",
        active=True,
    )
    db.add(m)
    db.commit()

    # Even if search would succeed, the eligibility filter should skip this.
    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([_sresult("https://somethingelse.com/")]),
    )

    result = mod.convert_website_manuals_to_webpages(db)
    assert result["eligible"] == 0
    assert result["converted"] == 0
    db.refresh(m)
    assert m.url == "https://cognettiforcongress.com/"


# ── Multiple monitors processed in single run ────────────────────────────────

def test_processes_both_candidate_and_opponent(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    c = _make_monitor(db, "Paige Cognetti campaign website check", "candidate")
    o = _make_monitor(db, "Rob Bresnahan campaign website check", "opponent")

    # The search stub returns a person-specific result per query.
    def _search_factory():
        class _P:
            name = "stub"
            def search(self, query, limit=10):
                if "Cognetti" in query:
                    return SimpleNamespace(
                        results=[_sresult("https://cognettiforcongress.com/")],
                        provider="stub", message=None,
                    )
                return SimpleNamespace(
                    results=[_sresult("https://bresnahanforcongress.com/")],
                    provider="stub", message=None,
                )
        return _P()

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider", _search_factory,
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)
    assert result["converted"] == 2

    db.refresh(c)
    db.refresh(o)
    assert c.monitor_type == "webpage" and "cognetti" in c.url
    assert o.monitor_type == "webpage" and "bresnahan" in o.url


# ── State / location parsing helpers ────────────────────────────────────────

def test_state_name_map():
    from app.services.monitor_url_discovery import _state_name
    assert _state_name("PA") == "Pennsylvania"
    assert _state_name("pa") == "Pennsylvania"
    assert _state_name("OH") == "Ohio"
    # Unknown code → returned as-is (graceful)
    assert _state_name("XX") == "XX"


def test_primary_city_extraction():
    from app.services.monitor_url_discovery import _primary_city
    assert _primary_city("Scranton/Wilkes-Barre, PA-08") == "Scranton"
    assert _primary_city("Akron, OH-13") == "Akron"
    # District-only locations have no city anchor
    assert _primary_city("PA-08") == ""
    assert _primary_city("") == ""


def test_state_code_from_campaign(db, campaign):
    from app.services.monitor_url_discovery import _state_code_from_campaign
    assert _state_code_from_campaign(campaign) == "PA"


# ── Government affinity scorers ─────────────────────────────────────────────

def test_state_election_board_affinity_prefers_gov_and_state():
    from app.services.monitor_url_discovery import _affinity_state_election_board
    assert _affinity_state_election_board("vote.pa.gov", "PA", "Pennsylvania") > \
        _affinity_state_election_board("news.example.com", "PA", "Pennsylvania")
    assert _affinity_state_election_board("elections.gov", "PA", "Pennsylvania") > \
        _affinity_state_election_board("electionsinfo.com", "PA", "Pennsylvania")


def test_council_agenda_affinity_boosts_civic_platforms():
    from app.services.monitor_url_discovery import _affinity_council_agenda
    granicus_score = _affinity_council_agenda("scranton.granicus.com", "Scranton", "PA")
    random_score = _affinity_council_agenda("random.com", "Scranton", "PA")
    assert granicus_score > random_score
    # .gov + city name in host beats civic platform
    assert _affinity_council_agenda("council.scranton.gov", "Scranton", "PA") > granicus_score


# ── Government URL discovery: each kind happy path ──────────────────────────

def test_state_election_board_happy_path(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "State election board check", "public_record")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://www.vote.pa.gov/",
                     title="Pennsylvania Department of State - Voting & Elections",
                     snippet="Official PA elections information"),
            _sresult("https://news.example.com/pa-election-2026"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)
    assert result["converted"] == 1
    db.refresh(m)
    assert m.monitor_type == "webpage"
    assert "vote.pa.gov" in m.url


def test_county_election_board_happy_path(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "County election board check", "public_record")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://www.lackawannacounty.org/departments/elections",
                     title="Lackawanna County Bureau of Elections",
                     snippet="Voter information for Lackawanna County, PA"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)
    assert result["converted"] == 1
    db.refresh(m)
    assert m.monitor_type == "webpage"


def test_city_council_agenda_happy_path(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "City council agenda check", "public_record")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://scranton.legistar.com/Calendar.aspx",
                     title="Scranton City Council - Legistar",
                     snippet="Upcoming meetings and agendas"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)
    assert result["converted"] == 1
    db.refresh(m)
    assert m.monitor_type == "webpage"
    assert "scranton.legistar.com" in m.url


def test_county_commission_agenda_happy_path(db, campaign, monkeypatch):
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "County commission agenda check", "public_record")

    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider",
        lambda: _stub_search([
            _sresult("https://www.lackawannacounty.org/commissioners-meetings",
                     title="Lackawanna County Commissioners - Meetings",
                     snippet="Agendas and minutes"),
        ]),
    )
    monkeypatch.setattr(
        "app.services.llm_provider.get_judge_provider",
        lambda: _stub_judge("1"),
    )
    monkeypatch.setattr(mod, "_http_check", lambda url, timeout=8: True)

    result = mod.convert_manuals_to_webpages(db)
    assert result["converted"] == 1
    db.refresh(m)
    assert m.monitor_type == "webpage"


# ── Classifier rejects unrecognized monitor names ────────────────────────────

def test_classify_returns_kind_or_none():
    from app.services.monitor_url_discovery import _classify_manual_monitor
    assert _classify_manual_monitor("Paige Cognetti campaign website check") == "campaign_website"
    assert _classify_manual_monitor("State election board check") == "state_election_board"
    assert _classify_manual_monitor("County election board check") == "county_election_board"
    assert _classify_manual_monitor("City council agenda check") == "city_council_agenda"
    assert _classify_manual_monitor("County commission agenda check") == "county_commission_agenda"
    # Unrecognized names should not trigger discovery
    assert _classify_manual_monitor("Some random monitor") is None
    assert _classify_manual_monitor("Paige Cognetti social check") is None


def test_unrecognized_monitor_left_alone(db, campaign, monkeypatch):
    """A manual monitor whose name doesn't match any known kind is not
    processed by the orchestrator — last_checked_at is not touched, the
    monitor stays as-is."""
    from app.services import monitor_url_discovery as mod

    m = _make_monitor(db, "Some weird monitor", "public_record")

    def _err_search():
        raise AssertionError("search should not be called for unrecognized name")
    monkeypatch.setattr(
        "app.services.search_provider.get_search_provider", _err_search,
    )

    result = mod.convert_manuals_to_webpages(db)
    assert result["eligible"] == 0
    assert result["converted"] == 0

    db.refresh(m)
    assert m.monitor_type == "manual"
    assert m.last_checked_at is None


# ── Backward-compat alias still importable ───────────────────────────────────

def test_backward_compat_alias_exists():
    from app.services.monitor_url_discovery import (
        convert_website_manuals_to_webpages,
        convert_manuals_to_webpages,
    )
    assert convert_website_manuals_to_webpages is convert_manuals_to_webpages
