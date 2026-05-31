"""Unit tests for app.services.youtube_discovery.

We can't hit live YouTube from tests, so HTTP and the LLM provider are
mocked. Tests cover:

  - Outlet homepage parsing (direct channel-ID vs @handle vs nothing)
  - Handle → channel-ID resolution from a channel page
  - LLM response parsing (clean JSON, fenced JSON, garbage, null)
  - Verification (strict vs loose) against canned RSS payloads
  - add_youtube_feed idempotency
  - Redundant-search-feed deactivation
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Outlet, RssFeed, CampaignConfig, Opponent
from app.services import youtube_discovery as yd


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _mk_response(status: int, text: str, content_type: str = "text/html"):
    req = httpx.Request("GET", "https://example.test/")
    return httpx.Response(
        status_code=status, text=text, request=req,
        headers={"content-type": content_type},
    )


# ── discover_outlet_channel ──────────────────────────────────────────────

def test_outlet_discovery_finds_direct_channel_id_in_html():
    html = '''
    <html><body>
        <footer>
            <a href="https://www.youtube.com/channel/UCMXHwsyBEY2-cL5rMVUuKXA">YouTube</a>
        </footer>
    </body></html>
    '''
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)):
        result = yd.discover_outlet_channel("wvia.org")
    assert result == "UCMXHwsyBEY2-cL5rMVUuKXA"


def test_outlet_discovery_resolves_handle_when_no_direct_id():
    """Homepage has @handle, no direct channel ID. We resolve the
    handle via a second fetch."""
    homepage = '<a href="https://www.youtube.com/@RepBresnahan">Watch</a>'
    handle_page = '''
    <html><head>
        <link rel="canonical" href="https://www.youtube.com/channel/UCABCDEFG1234567890QRSTU">
    </head></html>
    '''
    responses = iter([
        _mk_response(200, homepage),  # homepage
        _mk_response(200, handle_page),  # handle resolution
    ])
    with patch("app.services.youtube_discovery.httpx.get",
               side_effect=lambda *a, **kw: next(responses)):
        result = yd.discover_outlet_channel("timesleader.com")
    assert result == "UCABCDEFG1234567890QRSTU"


def test_outlet_discovery_returns_none_when_no_youtube_link():
    html = '<html><body>No social links here.</body></html>'
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)):
        result = yd.discover_outlet_channel("noyoutube.example")
    assert result is None


def test_outlet_discovery_returns_none_on_http_error():
    def boom(*a, **kw):
        raise httpx.TimeoutException("simulated")
    with patch("app.services.youtube_discovery.httpx.get", side_effect=boom):
        result = yd.discover_outlet_channel("broken.example")
    assert result is None


def test_outlet_discovery_returns_none_for_empty_domain():
    assert yd.discover_outlet_channel("") is None
    assert yd.discover_outlet_channel(None) is None  # type: ignore


# ── resolve_handle_to_channel_id ─────────────────────────────────────────

def test_handle_resolution_extracts_canonical_id():
    html = '<link rel="canonical" href="https://www.youtube.com/channel/UCXXXXXXXXXXXXXXXXXXXXXX">'
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)):
        result = yd.resolve_handle_to_channel_id("@RepBresnahan")
    assert result == "UCXXXXXXXXXXXXXXXXXXXXXX"


def test_handle_resolution_extracts_itemprop_form():
    html = '<meta itemprop="channelId" content="UCYYYYYYYYYYYYYYYYYYYYYY">'
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)):
        result = yd.resolve_handle_to_channel_id("@Cognetti")
    assert result == "UCYYYYYYYYYYYYYYYYYYYYYY"


def test_handle_resolution_falls_back_to_generic_uc_match():
    """Some pages embed the channel ID only in JSON data blobs."""
    html = '<script>window.__data = {"channelId":"UCZZZZZZZZZZZZZZZZZZZZZZ"};</script>'
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)):
        result = yd.resolve_handle_to_channel_id("@SomeOne")
    assert result == "UCZZZZZZZZZZZZZZZZZZZZZZ"


def test_handle_resolution_adds_at_prefix_if_missing():
    html = '<link rel="canonical" href="https://www.youtube.com/channel/UCAAAAAAAAAAAAAAAAAAAAAA">'
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, html)) as get:
        result = yd.resolve_handle_to_channel_id("BareName")  # no @ prefix
    # Should still fetch @BareName
    assert "@BareName" in get.call_args.args[0]
    assert result == "UCAAAAAAAAAAAAAAAAAAAAAA"


def test_handle_resolution_returns_none_on_404():
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(404, "Not Found")):
        result = yd.resolve_handle_to_channel_id("@NonExistent")
    assert result is None


# ── LLM response parsing ────────────────────────────────────────────────

def test_parse_clean_json_response():
    assert yd._parse_handle_from_llm_response('{"handle": "@PaigeForPA"}') == "@PaigeForPA"


def test_parse_response_adds_missing_at_prefix():
    assert yd._parse_handle_from_llm_response('{"handle": "PaigeForPA"}') == "@PaigeForPA"


def test_parse_response_with_markdown_fence():
    raw = '```json\n{"handle": "@RepBresnahan"}\n```'
    assert yd._parse_handle_from_llm_response(raw) == "@RepBresnahan"


def test_parse_response_with_chatter_around_json():
    raw = 'I think the handle is {"handle": "@CandX"}. Let me know if you need more.'
    assert yd._parse_handle_from_llm_response(raw) == "@CandX"


def test_parse_response_returns_none_when_handle_is_null():
    assert yd._parse_handle_from_llm_response('{"handle": null}') is None


def test_parse_response_returns_none_when_handle_is_missing():
    assert yd._parse_handle_from_llm_response('{"something_else": "x"}') is None


def test_parse_response_returns_none_on_garbage():
    assert yd._parse_handle_from_llm_response("totally not json") is None
    assert yd._parse_handle_from_llm_response("") is None
    assert yd._parse_handle_from_llm_response(None) is None  # type: ignore


def test_parse_response_rejects_handle_with_spaces_or_slashes():
    """Sanitization — a handle should be a single token."""
    assert yd._parse_handle_from_llm_response('{"handle": "@bad handle"}') is None
    assert yd._parse_handle_from_llm_response('{"handle": "@bad/slash"}') is None


# ── discover_candidate_channel (end-to-end with mocks) ──────────────────

def test_candidate_discovery_returns_channel_id_on_success():
    """LLM returns a handle, handle resolves, channel ID returned."""
    fake_provider = MagicMock()
    fake_provider.complete.return_value = '{"handle": "@PaigeForPA"}'
    handle_page = '<link rel="canonical" href="https://www.youtube.com/channel/UCHHHHHHHHHHHHHHHHHHHHHH">'
    with patch("app.services.llm_provider.get_judge_provider",
               return_value=fake_provider):
        with patch("app.services.youtube_discovery.httpx.get",
                   return_value=_mk_response(200, handle_page)):
            result = yd.discover_candidate_channel(
                "Paige Cognetti", state="PA", district="08", office="House",
            )
    assert result == "UCHHHHHHHHHHHHHHHHHHHHHH"


def test_candidate_discovery_returns_none_when_llm_says_null():
    fake_provider = MagicMock()
    fake_provider.complete.return_value = '{"handle": null}'
    with patch("app.services.llm_provider.get_judge_provider",
               return_value=fake_provider):
        result = yd.discover_candidate_channel("Obscure Candidate")
    assert result is None


def test_candidate_discovery_returns_none_when_llm_raises():
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = RuntimeError("LLM down")
    with patch("app.services.llm_provider.get_judge_provider",
               return_value=fake_provider):
        result = yd.discover_candidate_channel("Someone")
    assert result is None


# ── verify_channel_subject ───────────────────────────────────────────────

def _rss_with_titles(channel_title: str, video_titles: list[str]) -> str:
    """Build a minimal YouTube channel RSS body."""
    entries = "\n".join(f"<entry><title>{t}</title></entry>" for t in video_titles)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{channel_title}</title>
  <author><name>{channel_title}</name></author>
  {entries}
</feed>"""


def test_verify_strict_accepts_two_title_matches():
    rss = _rss_with_titles("Some Channel", [
        "Bresnahan visits Scranton",
        "Local news roundup",
        "Bresnahan on healthcare",
        "Random thing",
    ])
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, rss, "application/xml")):
        assert yd.verify_channel_subject("UCAAAAAAAAAAAAAAAAAAAAAA", ["Bresnahan"]) is True


def test_verify_strict_rejects_one_match():
    rss = _rss_with_titles("Some Channel", [
        "Bresnahan visits Scranton",
        "Cooking with grandma",
        "Random thing",
    ])
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, rss, "application/xml")):
        assert yd.verify_channel_subject("UCAAAAAAAAAAAAAAAAAAAAAA", ["Bresnahan"]) is False


def test_verify_loose_accepts_channel_metadata_match():
    """Loose mode: outlet channels often have generic video titles but
    the channel name itself identifies the outlet."""
    rss = _rss_with_titles("Times Leader Wilkes-Barre", [
        "Today's weather forecast",
        "Friday roundup",
    ])
    with patch("app.services.youtube_discovery.httpx.get",
               return_value=_mk_response(200, rss, "application/xml")):
        # Strict: no Times Leader in titles → False
        assert yd.verify_channel_subject("UC0", ["Times Leader"], strict=True) is False
        # Loose: Times Leader is the channel name → True
        assert yd.verify_channel_subject("UC0", ["Times Leader"], strict=False) is True


def test_verify_returns_false_on_empty_inputs():
    assert yd.verify_channel_subject("", ["foo"]) is False
    assert yd.verify_channel_subject("UCABC", []) is False
    assert yd.verify_channel_subject("UCABC", [""]) is False


def test_verify_returns_false_on_http_failure():
    def boom(*a, **kw):
        raise httpx.HTTPError("dead")
    with patch("app.services.youtube_discovery.httpx.get", side_effect=boom):
        assert yd.verify_channel_subject("UCXYZ", ["thing"]) is False


# ── add_youtube_feed ────────────────────────────────────────────────────

def test_add_youtube_feed_inserts_new_row(db):
    feed = yd.add_youtube_feed(db, name="Test Channel", channel_id="UCTESTTESTTESTTESTTESTTT")
    assert feed is not None
    assert feed.url.endswith("channel_id=UCTESTTESTTESTTESTTESTTT")
    assert feed.source_type == "youtube"
    assert feed.active is True


def test_add_youtube_feed_idempotent_on_duplicate_channel(db):
    yd.add_youtube_feed(db, name="First Name", channel_id="UCDUPDUPDUPDUPDUPDUPDUPP")
    second = yd.add_youtube_feed(db, name="Different Name", channel_id="UCDUPDUPDUPDUPDUPDUPDUPP")
    # Returns None to signal no-op
    assert second is None
    # Only one feed row exists
    assert db.query(RssFeed).filter(
        RssFeed.url.like("%channel_id=UCDUPDUPDUPDUPDUPDUPDUPP%"),
    ).count() == 1


def test_add_youtube_feed_reactivates_previously_deactivated(db):
    feed = yd.add_youtube_feed(db, name="React", channel_id="UCREACTREACTREACTREACT01")
    feed.active = False
    db.commit()
    # Re-discovering it should flip active back on
    yd.add_youtube_feed(db, name="React again", channel_id="UCREACTREACTREACTREACT01")
    db.refresh(feed)
    assert feed.active is True


# ── _deactivate_redundant_youtube_search_feeds ──────────────────────────

def test_deactivate_redundant_searches_only_gnews(db):
    """Only Google News-routed YouTube searches should be deactivated.
    A user-added direct channel feed with similar name stays alone."""
    # GNews search feed (broken)
    gnews = RssFeed(
        name="YouTube: Cognetti",
        url="https://news.google.com/rss/search?q=youtube...",
        source_type="rss", active=True,
    )
    # Direct channel feed for a different "Cognetti" (e.g. Italian author)
    # — shouldn't be touched
    direct = RssFeed(
        name="YouTube: Cognetti (direct)",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UCSOMEONE000000000000001",
        source_type="youtube", active=True,
    )
    db.add(gnews); db.add(direct)
    db.commit()

    n = yd._deactivate_redundant_youtube_search_feeds(db, "Cognetti")
    assert n == 1
    db.refresh(gnews); db.refresh(direct)
    assert gnews.active is False
    assert direct.active is True


def test_deactivate_redundant_searches_handles_empty_surname(db):
    assert yd._deactivate_redundant_youtube_search_feeds(db, "") == 0


# ── _person_surname ──────────────────────────────────────────────────────

def test_surname_extracts_from_first_last():
    assert yd._person_surname("Paige Cognetti") == "Cognetti"
    assert yd._person_surname("Robert P. Bresnahan") == "Bresnahan"


def test_surname_extracts_from_fec_format():
    assert yd._person_surname("COGNETTI, PAIGE") == "COGNETTI"
    assert yd._person_surname("BRESNAHAN, ROBERT P.") == "BRESNAHAN"


def test_surname_handles_edge_cases():
    assert yd._person_surname("") == ""
    assert yd._person_surname("Cher") == "Cher"  # mononym
