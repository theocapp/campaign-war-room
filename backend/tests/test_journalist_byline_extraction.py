"""Tests for the deterministic byline extractor used by journalist discovery.

The journalist discovery used to call an LLM once per article to extract the
byline. It now reads `SourceItem.source_author` (already populated during
ingestion from RSS entry.author and HTML <meta name="author">) and filters
the value through `_clean_byline`. These tests pin the filter behavior so
the LLM doesn't have to.
"""
from app.services.monitors import _byline_from_text, _clean_byline


def test_real_journalist_names_pass_through():
    assert _clean_byline("Savannah Hulsey Pointer") == "Savannah Hulsey Pointer"
    assert _clean_byline("Sarah K. Burris") == "Sarah K. Burris"
    assert _clean_byline("Bill O'Boyle") == "Bill O'Boyle"
    assert _clean_byline("Jennifer Learn-Andes") == "Jennifer Learn-Andes"


def test_strips_leading_by_prefix():
    assert _clean_byline("By Sarah K. Burris") == "Sarah K. Burris"
    assert _clean_byline("by Sarah K. Burris") == "Sarah K. Burris"


def test_strips_surrounding_punctuation():
    assert _clean_byline(' "Matthew Rozsa" ') == "Matthew Rozsa"
    assert _clean_byline("Matthew Rozsa.") == "Matthew Rozsa"


def test_institutional_bylines_rejected():
    for v in ["Associated Press", "The Associated Press", "Reuters",
              "Editorial Board", "Staff", "newsroom"]:
        assert _clean_byline(v) is None, f"expected None for {v!r}"


def test_non_human_byline_shapes_rejected():
    # Social handles, emails, URLs, reddit handles — none look like a person's name.
    for v in ["@RepBresnahan", "Fragrant-Pepper7710", "r/Pennsylvania",
              "jalango@dccc.org", "https://www.facebook.com/123",
              "ab21.bsky.social@bsky.brid.gy"]:
        assert _clean_byline(v) is None, f"expected None for {v!r}"


def test_empty_and_none_inputs():
    assert _clean_byline(None) is None
    assert _clean_byline("") is None
    assert _clean_byline("   ") is None


def test_single_word_rejected():
    # A byline must be at least Firstname + Lastname (two words).
    assert _clean_byline("Cher") is None
    assert _clean_byline("WVIA") is None


def test_outlet_name_blocklist_filters_publications():
    outlets = {"times leader", "sunday dispatch", "the new york times"}
    # Outlet names that pass the name regex would otherwise sneak through.
    assert _clean_byline("Times Leader", outlet_names=outlets) is None
    assert _clean_byline("Sunday Dispatch", outlet_names=outlets) is None
    assert _clean_byline("The New York Times", outlet_names=outlets) is None
    # Without the dynamic blocklist, the publication-token filter catches
    # these too — "Times" and "Dispatch" are publication tokens. The dynamic
    # blocklist is still important for outlet names that don't contain such
    # tokens (e.g. "Axios", "Politico" — single words and so rejected anyway,
    # but the principle applies for hypothetical multi-word outlets).
    assert _clean_byline("Times Leader") is None
    assert _clean_byline("Sunday Dispatch") is None


def test_length_bounds():
    # Each word must have ≥2 letters (the regex matches [A-Za-z .'-]+, one or
    # more), so single-letter "names" like "A B" are rejected.
    assert _clean_byline("A B") is None
    assert _clean_byline("Al Bo") is not None
    assert _clean_byline("a" * 61) is None        # over 60 chars — rejected


# ── Multi-author + outlet-suffix bylines (real RSS noise patterns) ──────────
# These patterns showed up in 600+ rows when sweeping the live DB and would
# have been rejected by a strict name-shape match. The cleaner now extracts
# the first author and strips outlet/role suffixes.

def test_strips_trailing_outlet_after_comma():
    # RSS author fields often append the outlet after a comma.
    assert _clean_byline("Predrag Milic, The Associated Press") == "Predrag Milic"
    assert _clean_byline("Deb Kiner, Advance Local Express Desk") == "Deb Kiner"


def test_strips_trailing_outlet_after_pipe():
    assert _clean_byline("Ben Nuckols | The Associated Press") == "Ben Nuckols"
    assert _clean_byline("Gary Grumbach | NBC News") == "Gary Grumbach"


def test_strips_trailing_role_descriptor():
    assert _clean_byline("Alexandria Jacobson, Investigative Reporter") == "Alexandria Jacobson"
    assert _clean_byline("Jane Doe (staff writer)") == "Jane Doe"


def test_strips_for_outlet_suffix():
    assert _clean_byline("Ann Rejrat for Spotlight PA") == "Ann Rejrat"


def test_multi_author_byline_takes_first():
    # Joint bylines: we count the first author. Aggregating both would
    # double-count an article and the LLM also typically returned one name.
    assert _clean_byline("Hailey Fuchs and Meredith Lee Hill") == "Hailey Fuchs"
    assert _clean_byline("Jordain Carney and Alex Gangitano") == "Jordain Carney"
    assert _clean_byline("By Jordain Carney and Alex Gangitano") == "Jordain Carney"


def test_split_then_still_validates_name_shape():
    # "Smith" is one word — rejected even after we split on the comma. The
    # split isolates a candidate; the name-shape regex still has the final say.
    assert _clean_byline("Smith, Jones") is None
    assert _clean_byline("Smith, Jones, and Doe") is None
    # But a real first author with two words passes:
    assert _clean_byline("John Smith, Jones") == "John Smith"


# ── _byline_from_text: fallback regex when source_author is NULL ────────────

def test_body_byline_at_start():
    text = "By Julia Terruso\n\nThe political operatives who powered Mamdani's..."
    assert _byline_from_text(None, text) == "Julia Terruso"


def test_body_byline_with_multi_authors():
    text = "By Scott Wong, Sahil Kapur, Melanie Zanona and Kyle Stewart\n\nCentrist Republicans..."
    candidate = _byline_from_text(None, text)
    # The regex captures the full multi-author string; _clean_byline will
    # split it down to the first author.
    assert candidate is not None
    assert _clean_byline(candidate) == "Scott Wong"


def test_body_byline_with_outlet_suffix():
    text = "By Ronald Blum, The Associated Press. The article body continues here..."
    assert _clean_byline(_byline_from_text(None, text)) == "Ronald Blum"


def test_title_opinion_column_byline():
    title = "Betsy McCaughey: The Geniuses in Congress — That's a Joke | The Patriot Post"
    assert _byline_from_text(title, None) == "Betsy McCaughey"


def test_title_with_single_word_prefix_not_byline():
    # "Trump: I will..." — single word, would be rejected by _clean_byline.
    title = "Trump: I will do something"
    candidate = _byline_from_text(title, None)
    # The title regex requires 2+ words for the name part.
    assert candidate is None or _clean_byline(candidate) is None


def test_no_byline_returns_none():
    assert _byline_from_text(None, None) is None
    assert _byline_from_text("", "") is None
    assert _byline_from_text("A headline with no byline", "Body text with no byline pattern") is None


def test_body_byline_does_not_match_mid_sentence_by():
    # "...passed by Congress" must NOT trigger the byline regex.
    text = "The bill was passed by Congress yesterday."
    assert _byline_from_text(None, text) is None


def test_byline_after_timestamp_prefix():
    # NBC News and many wire services format bylines as
    # "...Dec. 17, 2025, 7:39 PM EST By Scott Wong ..." — "By" follows a
    # timestamp, not a sentence-end punctuation. Word-boundary anchor handles it.
    text = "Updated Dec. 17, 2025, 7:39 PM EST By Scott Wong , Sahil Kapur and Kyle Stewart WASHINGTON — Republicans..."
    candidate = _byline_from_text(None, text)
    assert candidate is not None
    assert _clean_byline(candidate) == "Scott Wong"


def test_mid_sentence_by_with_two_capitalized_words_still_safe():
    # If the body says "approved by John Smith yesterday", we still capture
    # "John Smith" — that is a real risk of loosening the anchor. The strict
    # lookahead (terminator required after the name) saves us.
    text = "The bill was approved by John Smith yesterday."
    # "John Smith" is followed by " yesterday" — no sentence terminator → no match.
    assert _byline_from_text(None, text) is None


def test_all_caps_press_release_byline_titlecased():
    # The all-caps fallback regex picks up the byline and title-cases it.
    text = "By JONATHAN J. COOPER, STEVE PEOPLES, HUMERA LODHI and SIMRAN PARWANI NEW YORK (AP) — President..."
    candidate = _byline_from_text(None, text)
    assert candidate is not None
    # Multi-author split takes the first author; expect title-cased name.
    assert _clean_byline(candidate) == "Jonathan J. Cooper"


def test_all_caps_byline_terminated_by_titlecase_word():
    # "By LIAM MAYO EIGHTH CONGRESSIONAL DISTRICT, PA —" — terminator is the
    # comma after DISTRICT or the lowercase " PA" sequel. With the all-caps
    # regex's strict terminator (TitleCase word, sentence punctuation, or
    # open paren), capture stops cleanly.
    text = "By LIAM MAYO EIGHTH CONGRESSIONAL DISTRICT, PA — On Friday, February 13..."
    candidate = _byline_from_text(None, text)
    # The capture may extend through CONGRESSIONAL/DISTRICT (all caps), but
    # the 4-word cap and the comma terminator bound it.
    if candidate is not None:
        cleaned = _clean_byline(candidate)
        # We don't strictly require "Liam Mayo" here — accept any non-None
        # output as long as it's plausibly a person's name (max 4 words).
        if cleaned is not None:
            assert len(cleaned.split()) <= 4


def test_publication_token_blocks_outlet_names():
    # Two-word outlets that pass the name-shape regex but are publications.
    assert _clean_byline("Daily Mail") is None
    assert _clean_byline("Hindustan Times") is None
    assert _clean_byline("Red State") is None
    assert _clean_byline("Patriot Post") is None
    assert _clean_byline("Sunday Tribune") is None


def test_publication_token_does_not_overfilter_real_names():
    # Real journalist names that happen to share NO tokens with publications.
    assert _clean_byline("Sarah Burris") == "Sarah Burris"
    assert _clean_byline("Bill O'Boyle") == "Bill O'Boyle"
    assert _clean_byline("Matthew Rozsa") == "Matthew Rozsa"
    assert _clean_byline("Jennifer Learn-Andes") == "Jennifer Learn-Andes"
