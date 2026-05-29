"""Generic congressional-district name normalization.

Problem this solves: the LLM emits many surface forms for the same
district. For PA-08, we observed in production:

  - "PA-08"  (seeded canonical)
  - "8th Congressional District"
  - "Pennsylvania's 8th Congressional District"
  - "Eighth Congressional District"
  - "Pennsylvania's eighth congressional district"
  - "PA 8th Congressional District"
  - "PA's 8th Congressional District"

Each got a separate auto-discovered entity, fragmenting the location
graph (129 mentions split across 6 entities, plus the seeded one).

Fix: generate the SET of plausible surface forms for the campaign's
district at startup, attach them as aliases on the seeded canonical
entity, and let the existing alias-match step in canonicalize_entity
absorb them automatically.

This is generic — it works for any US House district (any state,
any number 1–53), because it derives forms programmatically from the
district code (e.g. "PA-08" → state="PA"/"Pennsylvania", number=8).

Future extension: state-level offices (governor, senator races) follow
similar patterns. A campaign config of "PA-Governor" would be normalized
the same way for "Pennsylvania Governor" / "PA Gov" / "Governor of PA".
Out of scope for the v15.0 cleanup pass; revisit when we onboard a
non-House race.
"""
from __future__ import annotations

from typing import Optional

# ── US state lookups ────────────────────────────────────────────────────

# Abbreviation → full name. Limited to actual states + DC + territories
# (Puerto Rico, USVI, Guam, American Samoa, Northern Mariana Islands)
# that elect non-voting House delegates. The right set for "any US House
# race" — non-US races aren't in scope.
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands",
    "GU": "Guam", "AS": "American Samoa", "MP": "Northern Mariana Islands",
}


# Number → English word (1 through 53, covering all current US House
# districts — California has 52, that's the upper bound). Extend if a
# state ever gains more.
_NUMBER_WORDS: dict[int, str] = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
    11: "eleventh", 12: "twelfth", 13: "thirteenth", 14: "fourteenth",
    15: "fifteenth", 16: "sixteenth", 17: "seventeenth", 18: "eighteenth",
    19: "nineteenth", 20: "twentieth", 21: "twenty-first", 22: "twenty-second",
    23: "twenty-third", 24: "twenty-fourth", 25: "twenty-fifth",
    26: "twenty-sixth", 27: "twenty-seventh", 28: "twenty-eighth",
    29: "twenty-ninth", 30: "thirtieth", 31: "thirty-first",
    32: "thirty-second", 33: "thirty-third", 34: "thirty-fourth",
    35: "thirty-fifth", 36: "thirty-sixth", 37: "thirty-seventh",
    38: "thirty-eighth", 39: "thirty-ninth", 40: "fortieth",
    41: "forty-first", 42: "forty-second", 43: "forty-third",
    44: "forty-fourth", 45: "forty-fifth", 46: "forty-sixth",
    47: "forty-seventh", 48: "forty-eighth", 49: "forty-ninth",
    50: "fiftieth", 51: "fifty-first", 52: "fifty-second", 53: "fifty-third",
}


def _ordinal_digit_suffix(n: int) -> str:
    """Return the English ordinal suffix for `n` (1→st, 2→nd, 3→rd, etc.)."""
    if 10 <= (n % 100) <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def parse_district_code(code: str) -> Optional[tuple[str, str, int]]:
    """Parse a config district code like 'PA-08' into (state_abbr, state_full, number).

    Accepts forms: 'PA-08', 'PA-8', 'pa-08', 'pa-8'. Trims whitespace.
    Returns None if the code is malformed or the state isn't recognized.
    """
    if not code:
        return None
    norm = code.strip().upper().replace(" ", "")
    if "-" not in norm:
        return None
    abbr, _, num = norm.partition("-")
    if abbr not in US_STATES:
        return None
    try:
        number = int(num)
    except ValueError:
        return None
    if number < 1 or number > 53:
        return None
    return (abbr, US_STATES[abbr], number)


def district_surface_forms(district_code: str) -> list[str]:
    """Generate all plausible surface forms an LLM might emit for the
    campaign's congressional district. Result is sorted + de-duped.

    Returns canonical-cased forms (e.g. "Pennsylvania's 8th Congressional
    District"). Callers should match against these case-insensitively.

    Example:
      district_surface_forms("PA-08") → [
        "8th Congressional District",
        "8th congressional district of Pennsylvania",
        "Eighth Congressional District",
        "PA 08",
        "PA 8",
        "PA-08",
        "PA-8",
        "PA's 8th Congressional District",
        "PA's Eighth Congressional District",
        "Pennsylvania 8th Congressional District",
        "Pennsylvania's 8th Congressional District",
        "Pennsylvania's Eighth Congressional District",
        ...
      ]
    """
    parsed = parse_district_code(district_code)
    if not parsed:
        return []
    abbr, state, number = parsed
    word = _NUMBER_WORDS.get(number, "").capitalize()
    digit_suffix = _ordinal_digit_suffix(number)
    digit_ord = f"{number}{digit_suffix}"   # "8th"
    pad_two = f"{number:02d}"               # "08"

    forms: set[str] = set()

    # Compact forms ----------------------------------------------------------
    forms.add(f"{abbr}-{pad_two}")          # PA-08
    forms.add(f"{abbr}-{number}")           # PA-8
    forms.add(f"{abbr} {pad_two}")          # PA 08
    forms.add(f"{abbr} {number}")           # PA 8

    # "Nth Congressional District" with various state prefixes ----------------
    state_prefixes = [
        "",                       # no prefix: "8th Congressional District"
        f"{state} ",              # "Pennsylvania 8th Congressional District"
        f"{state}'s ",            # "Pennsylvania's 8th Congressional District"
        f"{state}’s ",       # curly apostrophe variant
        f"{abbr} ",               # "PA 8th Congressional District"
        f"{abbr}'s ",             # "PA's 8th Congressional District"
        f"{abbr}’s ",        # curly apostrophe variant
    ]

    ordinal_forms = [digit_ord]
    if word:
        ordinal_forms.append(word)            # "Eighth"
        ordinal_forms.append(word.lower())    # "eighth" (lowercase variant)

    suffix_variants = [
        " Congressional District",
        " congressional district",
        "th Congressional District" if not word else None,  # avoid double-suffix
    ]
    suffix_variants = [s for s in suffix_variants if s is not None]

    for prefix in state_prefixes:
        for ordinal in ordinal_forms:
            forms.add(f"{prefix}{ordinal} Congressional District")
            forms.add(f"{prefix}{ordinal} congressional district")

    # "Nth district of {State}" / "Nth Congressional District of {State}"
    forms.add(f"{digit_ord} Congressional District of {state}")
    forms.add(f"{digit_ord} district of {state}")
    if word:
        forms.add(f"{word} Congressional District of {state}")

    # Drop empty / whitespace-only results
    return sorted(f.strip() for f in forms if f and f.strip())


def is_district_surface_form(name: str, district_code: str) -> bool:
    """Case-insensitive check: does `name` look like a surface form of
    the campaign's district?"""
    if not name or not district_code:
        return False
    name_norm = " ".join(name.strip().lower().split())
    for form in district_surface_forms(district_code):
        if " ".join(form.strip().lower().split()) == name_norm:
            return True
    return False
