"""Unit tests for the Phase 2 third-party-account discovery service:
query builder, per-platform display names, and role inference. These
together are the surface area that most often produces user-visible
output, so locking their behavior with tests now keeps later refactors
honest.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.search_provider import SearchResponse, SearchResult
from app.services.third_party_account_discovery import (
    _build_queries,
    _infer_role,
    _platform_display_name,
    discover_third_party_accounts,
)


# ── _build_queries ─────────────────────────────────────────────────────────────

def test_build_queries_includes_both_candidate_and_opponent():
    """One anchor each for candidate + every opponent (up to the cap)."""
    queries = _build_queries(
        candidate_name="Paige Cognetti",
        opponent_names=["Rob Bresnahan"],
        location="Scranton, PA",
        district="PA-08",
    )
    # 5 hosts × 2 anchors = 10 queries
    assert len(queries) == 10
    # Every host appears twice — once per anchor
    cognetti = [q for _, q in queries if "Cognetti" in q]
    bresnahan = [q for _, q in queries if "Bresnahan" in q]
    assert len(cognetti) == 5
    assert len(bresnahan) == 5


def test_build_queries_multi_opponent_no_silent_truncation():
    """The earlier `[:2]` cap silently dropped opponents past index 1
    on primary-election races. Confirm the new explicit cap covers all
    realistic cases.
    """
    queries = _build_queries(
        candidate_name="Cand A",
        opponent_names=["Opp B", "Opp C", "Opp D"],
        location="Anytown, ST",
        district=None,
    )
    # 5 hosts × 4 anchors (1 candidate + 3 opponents) = 20 queries
    assert len(queries) == 20


def test_build_queries_respects_anchor_cap():
    """With more opponents than the cap, only the first N total anchors
    fire — prevents query-budget runaway on big primary fields.
    """
    queries = _build_queries(
        candidate_name="Cand A",
        opponent_names=["Opp B", "Opp C", "Opp D", "Opp E", "Opp F", "Opp G"],
        location="Anytown, ST",
        district=None,
    )
    # Cap is 5 anchors; 5 hosts × 5 anchors = 25 queries
    assert len(queries) == 25


def test_build_queries_excludes_district_as_anchor():
    """A bare district like "PA-08" tagged onto site:facebook.com returns
    too much noise; district is appended via `location` only, not used
    as its own anchor.
    """
    queries = _build_queries(
        candidate_name="X",
        opponent_names=[],
        location=None,
        district="PA-08",
    )
    # 5 hosts × 1 anchor (just candidate) = 5 queries
    assert len(queries) == 5
    # The district doesn't get its own anchor query
    assert not any('"PA-08"' in q for _, q in queries)


def test_build_queries_appends_location_to_every_query():
    queries = _build_queries(
        candidate_name="Cand",
        opponent_names=[],
        location="Scranton, PA",
        district=None,
    )
    assert all("Scranton, PA" in q for _, q in queries)


def test_build_queries_handles_empty_inputs():
    assert _build_queries("", [], None, None) == []
    assert _build_queries(None, None, None, None) == []
    assert _build_queries("Cand", [None, ""], "Loc", None) != []


# ── _platform_display_name ─────────────────────────────────────────────────────

@pytest.mark.parametrize("platform,identifier,title,expected", [
    # Reddit / Bluesky / YouTube use identifier-based labels
    ("reddit_subreddit", "Scranton",     "Some article title", "r/Scranton"),
    ("reddit_user",      "SomeUser",     "irrelevant",         "u/SomeUser"),
    ("bluesky",          "x.bsky.social", None,                "@x.bsky.social"),
    ("youtube",          "@RepBresnahan", "something",         "@RepBresnahan"),
    # FB/IG take a title-derived name when it's short and clean
    ("facebook",  "2822news",     "WBRE/WYOU 28/22 News - Facebook", "WBRE/WYOU 28/22 News"),
    ("instagram", "spotlightpa",  "Spotlight PA",                    "Spotlight PA"),
    # FB/IG fall back to identifier when title looks like quoted post content
    # (too long, or ends with !/?/:)
    ("instagram", "dccc", '"MEET PAIGE! Paige Cognetti is a reformer who...', "dccc"),
    ("facebook",  "page", "Paige Cognetti for Congress!",                    "page"),
    # FB/IG fall back to identifier when no title is available
    ("facebook",  "2822news", None,                                          "2822news"),
])
def test_platform_display_name(platform, identifier, title, expected):
    assert _platform_display_name(platform, identifier, title) == expected


# ── _infer_role ────────────────────────────────────────────────────────────────

def test_infer_role_news_wins_priority_order():
    """News must be checked before committee — was previously a real bug
    where Washington Examiner classified as 'committee' just because the
    word appeared in the snippet."""
    assert _infer_role(
        name="Washington Examiner",
        snippet="committee mentioned in passing",
        identifier="WashingtonExaminer",
    ) == "news"


def test_infer_role_dccc_nrcc_are_committee_not_union():
    """DCCC / NRCC are party campaign committees, not unions."""
    assert _infer_role(None, None, "dccc") == "committee"
    assert _infer_role(None, None, "nrcc") == "committee"


def test_infer_role_strong_only_roles_require_identifier_match():
    """Opposition keywords in a quoted article title shouldn't classify
    a community subreddit as opposition — was a real bug with
    r/Pennsylvania."""
    role = _infer_role(
        name="Paige Against the Machine",  # NYT article title leaked in
        snippet='NYT: "Paige Against the Machine"',
        identifier="Pennsylvania",
    )
    assert role != "opposition"
    assert role == "unknown"  # no strong signal


def test_infer_role_opposition_fires_on_identifier_match():
    """Strong-only roles DO fire when the keyword is in the identifier."""
    assert _infer_role("Stop Bresnahan", "campaign", "StopBresnahan") == "opposition"


def test_infer_role_loose_roles_fire_on_snippet():
    """News can be detected from the snippet alone — its keywords are
    distinctive enough."""
    assert _infer_role(None, "local news report on the race", "TheKeystoneNews") == "news"


def test_infer_role_unknown_when_no_signal():
    assert _infer_role("Some Random Page", "random content here", "randomhandle") == "unknown"


# ── discover_third_party_accounts (integration with stub provider) ────────────

class _StubProvider:
    """Returns canned results based on which anchor and which host appear
    in the query string."""
    name = "stub"

    def __init__(self, responses):
        self._responses = responses

    def search(self, query, limit=12):
        for predicate, results in self._responses:
            if predicate(query):
                return SearchResponse(provider=self.name, message=None, results=results)
        return SearchResponse(provider=self.name, message=None, results=[])


def test_discover_third_party_matched_anchors_per_account():
    """An account that surfaces from both anchors gets both names in
    matched_anchors; an account that surfaces from only one anchor gets
    only that one. This is the visibility-gap fix from the session."""
    responses = [
        (
            lambda q: "Cognetti" in q and "reddit.com" in q,
            [
                SearchResult(title="r/Scranton", url="https://www.reddit.com/r/Scranton/", snippet="Cognetti is mayor"),
                SearchResult(title="r/Pennsylvania", url="https://www.reddit.com/r/Pennsylvania/", snippet="Cognetti coverage"),
            ],
        ),
        (
            lambda q: "Bresnahan" in q and "reddit.com" in q,
            [
                SearchResult(title="r/Scranton", url="https://www.reddit.com/r/Scranton/", snippet="Bresnahan stock trades"),
                SearchResult(title="r/Politics", url="https://www.reddit.com/r/politics/", snippet="Bresnahan-only signal"),
            ],
        ),
    ]
    with patch(
        "app.services.third_party_account_discovery.get_search_provider",
        return_value=_StubProvider(responses),
    ):
        result = discover_third_party_accounts(
            candidate_name="Paige Cognetti",
            opponent_names=["Rob Bresnahan"],
            location="Scranton, PA",
            district="PA-08",
        )

    subs = {a.identifier: a for a in result["reddit_subreddit"]}
    assert sorted(subs["Scranton"].matched_anchors) == ["Paige Cognetti", "Rob Bresnahan"]
    assert subs["Pennsylvania"].matched_anchors == ["Paige Cognetti"]
    assert subs["politics"].matched_anchors == ["Rob Bresnahan"]


def test_discover_third_party_excludes_own_handles():
    """The candidate's confirmed handles should NOT show up as third-party."""
    responses = [
        (lambda q: True, [
            SearchResult(title="@candidate", url="https://www.instagram.com/mayorcand/", snippet=""),
            SearchResult(title="@committee", url="https://www.instagram.com/dccc/", snippet=""),
        ]),
    ]
    with patch(
        "app.services.third_party_account_discovery.get_search_provider",
        return_value=_StubProvider(responses),
    ):
        result = discover_third_party_accounts(
            candidate_name="Paige Cognetti",
            opponent_names=[],
            exclude={"instagram": {"mayorcand"}},
        )
    handles = {a.identifier for a in result["instagram"]}
    assert "mayorcand" not in handles
    assert "dccc" in handles
