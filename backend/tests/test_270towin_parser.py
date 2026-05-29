"""Parser tests for the 270toWin Cook/Sabato mirror pages.

These are HTML-snippet tests — no network. Snippets mirror the live
2026 page format captured 2026-05-29: each district is encoded as a
JSON object inside a large blob in the page, with state_abbr +
district_number + a one-character map_code that maps to the rating
tier via the page's color-palette legend.

If 270toWin changes their JSON field names or restructures the per-
district records, these tests will fail loudly rather than the
fetchers silently going stale.
"""
from __future__ import annotations

from app.services.race_ratings_monitor import (
    _270TOWIN_COLOR_TO_RATING,
    _parse_270towin_map_code,
    _270towin_fetch,
)


# Minimal but realistic page body. Three districts encoded so we can
# verify the parser actually scopes to the requested (state, district)
# and doesn't accidentally match an adjacent row.
SAMPLE_PA08_TOSSUP = (
    '{...other page chrome and JS...}'
    # CA-22 (Republican, Toss-up)
    '"4222":[{"district_id_combo":"4222","district_number":22,"state_fips_code":"06",'
    '"state_abbr":"CA","state_name":"California","seat_party":"R","seat_status":"T",'
    '"map_code":"0","candidates":[]}],'
    # PA-08 (target)
    '"4208":[{"district_id_combo":"4208","district_number":8,"state_fips_code":"42",'
    '"state_abbr":"PA","state_name":"Pennsylvania","seat_rep_name":"Rob Bresnahan",'
    '"seat_party":"R","seat_status":"T","map_code":"0",'
    '"candidates":[{"id":19270,"full_name":"Rob Bresnahan","party":"R"}]}],'
    # WI-3
    '"5503":[{"district_id_combo":"5503","district_number":3,"state_fips_code":"55",'
    '"state_abbr":"WI","state_name":"Wisconsin","seat_party":"R","seat_status":"D1",'
    '"map_code":"a","candidates":[]}]'
    '{...more page chrome...}'
)

SAMPLE_PA08_LEAN_R = SAMPLE_PA08_TOSSUP.replace(
    '"state_abbr":"PA","state_name":"Pennsylvania","seat_rep_name":"Rob Bresnahan",'
    '"seat_party":"R","seat_status":"T","map_code":"0",',
    '"state_abbr":"PA","state_name":"Pennsylvania","seat_rep_name":"Rob Bresnahan",'
    '"seat_party":"R","seat_status":"R2","map_code":"6",',
)


# ─────────────────────────────────────────────────────────────────────────────
# _parse_270towin_map_code
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_finds_pa08_when_tossup():
    assert _parse_270towin_map_code(SAMPLE_PA08_TOSSUP, "PA", "8") == "0"


def test_parse_finds_pa08_when_lean_r():
    assert _parse_270towin_map_code(SAMPLE_PA08_LEAN_R, "PA", "8") == "6"


def test_parse_does_not_confuse_adjacent_districts():
    # CA-22 and WI-3 have different map_codes ("0" vs "a"); make sure
    # neither leaks into a PA query.
    assert _parse_270towin_map_code(SAMPLE_PA08_LEAN_R, "CA", "22") == "0"
    assert _parse_270towin_map_code(SAMPLE_PA08_LEAN_R, "WI", "3") == "a"


def test_parse_returns_none_for_missing_district():
    assert _parse_270towin_map_code(SAMPLE_PA08_TOSSUP, "TX", "1") is None


# ─────────────────────────────────────────────────────────────────────────────
# _270TOWIN_COLOR_TO_RATING (legend completeness)
# ─────────────────────────────────────────────────────────────────────────────

def test_color_map_covers_all_nine_codes():
    expected_codes = {"1", "3", "5", "a", "0", "2", "4", "6", "b"}
    assert set(_270TOWIN_COLOR_TO_RATING.keys()) == expected_codes


def test_color_map_assigns_favors_correctly():
    # D-side codes favor 'candidate' (this codebase is configured for a
    # Democratic campaign — see RATING_BANDS docstring and existing
    # _favors_from_label in the same module).
    for code in ("1", "3", "5", "a"):
        assert _270TOWIN_COLOR_TO_RATING[code][1] == "candidate"
    # R-side codes favor 'opponent'.
    for code in ("2", "4", "6", "b"):
        assert _270TOWIN_COLOR_TO_RATING[code][1] == "opponent"
    # Toss-up is its own favors bucket.
    assert _270TOWIN_COLOR_TO_RATING["0"][1] == "tossup"


# ─────────────────────────────────────────────────────────────────────────────
# _270towin_fetch end-to-end (parser path only — no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

def test_fetch_returns_none_for_unparseable_district(monkeypatch):
    # Bypass _get_html so the test stays offline.
    from app.services import race_ratings_monitor as mod
    monkeypatch.setattr(mod, "_get_html", lambda url: SAMPLE_PA08_TOSSUP)
    out = mod._270towin_fetch("http://example", {"district_label": "BAD"}, "cook")
    assert out is None


def test_fetch_returns_tossup_for_pa08(monkeypatch):
    from app.services import race_ratings_monitor as mod
    monkeypatch.setattr(mod, "_get_html", lambda url: SAMPLE_PA08_TOSSUP)
    out = mod._270towin_fetch("http://example", {"district_label": "PA-08"}, "cook")
    assert out is not None
    assert out.rating_label == "Toss-up"
    assert out.rating_min_pct == 45
    assert out.rating_max_pct == 55
    assert out.favors == "tossup"


def test_fetch_returns_lean_r_for_pa08_when_blob_says_so(monkeypatch):
    from app.services import race_ratings_monitor as mod
    monkeypatch.setattr(mod, "_get_html", lambda url: SAMPLE_PA08_LEAN_R)
    out = mod._270towin_fetch("http://example", {"district_label": "PA-08"}, "sabato")
    assert out is not None
    assert out.rating_label == "Lean R"
    assert out.rating_min_pct == 60
    assert out.rating_max_pct == 75
    assert out.favors == "opponent"
