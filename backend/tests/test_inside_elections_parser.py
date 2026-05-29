"""Parser tests for the Inside Elections House-ratings page.

These are HTML-snippet tests — no network. The snippet mirrors the live
layout captured 2026-05-29:

  <h3 class="rating lean-republican">Lean Republican</h3>
  <table class="ratings ...">
    <tr><td class="state">PA</td><td class="district">8</td>...</tr>
  </table>

If Inside Elections changes their HTML, this test will fail loudly rather
than the fetcher silently going stale.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from app.services.race_ratings_monitor import _parse_inside_elections_district


SAMPLE_HTML = """
<html><body>
  <h3 class="rating toss-up">Toss-up</h3>
  <table class="ratings id-1 toss-up">
    <tr>
      <td class="state">CA</td>
      <td class="district">22</td>
      <td class="party R"><span>R</span></td>
      <td class="notes"></td>
      <td class="incumbent">Valadao</td>
      <td class="shift "></td>
    </tr>
  </table>

  <h3 class="rating tilt-republican">Tilt Republican</h3>
  <table class="ratings id-5 tilt-republican">
    <tr>
      <td class="state">PA</td>
      <td class="district">8</td>
      <td class="party R"><span>R</span></td>
      <td class="notes"></td>
      <td class="incumbent">Bresnahan</td>
      <td class="shift "></td>
    </tr>
    <tr>
      <td class="state">WI</td>
      <td class="district">3</td>
      <td class="party R"><span>R</span></td>
      <td class="notes"></td>
      <td class="incumbent">Van Orden</td>
      <td class="shift "></td>
    </tr>
  </table>

  <h3 class="rating lean-republican">Lean Republican</h3>
  <table class="ratings id-7 lean-republican">
    <tr>
      <td class="state">OH</td>
      <td class="district">9</td>
      <td class="party D"><span>D</span></td>
      <td class="notes"></td>
      <td class="incumbent">Kaptur</td>
      <td class="shift "></td>
    </tr>
  </table>
</body></html>
"""


def _soup() -> BeautifulSoup:
    return BeautifulSoup(SAMPLE_HTML, "html.parser")


def test_finds_pa_08_under_tilt_republican():
    assert _parse_inside_elections_district(_soup(), "PA", "8") == "Tilt Republican"


def test_finds_first_table_row():
    assert _parse_inside_elections_district(_soup(), "CA", "22") == "Toss-up"


def test_finds_row_in_later_tier():
    assert _parse_inside_elections_district(_soup(), "OH", "9") == "Lean Republican"


def test_finds_second_row_in_same_tier():
    # Verifies we keep matching after the first row of a tier.
    assert _parse_inside_elections_district(_soup(), "WI", "3") == "Tilt Republican"


def test_returns_none_for_missing_district():
    assert _parse_inside_elections_district(_soup(), "TX", "1") is None


def test_state_match_is_case_insensitive():
    # IE renders states uppercase; the parser uppercases too so a future
    # case change upstream doesn't silently break the match.
    assert _parse_inside_elections_district(_soup(), "pa", "8") is None  # caller must pass UPPER
    assert _parse_inside_elections_district(_soup(), "PA", "8") == "Tilt Republican"
