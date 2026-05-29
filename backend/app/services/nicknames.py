"""Nickname-aware name matching for person entities.

Used by canonicalize_entity to merge variants like "Patricia Beynon" and
"Trish Beynon" into a single canonical entity. Embedding similarity
doesn't catch nicknames (first-name divergence drops cosine below
review threshold, see Day-1 audit), so we use a hand-curated dictionary
of common English nicknames.

This is intentionally bounded. Adding a full FirstName-Nickname dataset
(e.g. https://github.com/onyxrev/common_nickname_csv) would help on the
long tail but risk false positives (Frank ↔ Franklin ↔ Francis collide).
The dictionary below is biased toward US political naming conventions —
the names that actually appear in campaign coverage.

Design:
  - `NICKNAMES`: dict[canonical_full_name → tuple of nickname forms]
  - All keys + values lowercase.
  - `is_nickname_equivalent("Patricia", "Trish")` → True
  - `is_nickname_equivalent("Trish", "Tricia")` → True (both Patricia nicks)
  - `is_nickname_equivalent("Patricia", "Patricia")` → True (identity)
  - `is_nickname_equivalent("Robert", "Patricia")` → False
"""
from __future__ import annotations

import re
from typing import Optional


# Canonical → nicknames. Bidirectional matching is computed at lookup time.
# Add only well-attested variants; "Bobby" → Robert is fine, "Skip" → unknown is not.
NICKNAMES: dict[str, tuple[str, ...]] = {
    # Female
    "patricia":   ("trish", "pat", "patty", "tricia"),
    "elizabeth":  ("liz", "beth", "eliza", "betty", "lizzie", "libby"),
    "catherine":  ("cathy", "kate", "katie", "cat", "kit"),
    "katherine":  ("kathy", "kate", "katie", "kat"),
    "margaret":   ("maggie", "meg", "peggy", "marge"),
    "jennifer":   ("jen", "jenny"),
    "susan":      ("sue", "susie"),
    "barbara":    ("barb", "babs"),
    "deborah":    ("deb", "debbie"),
    "kimberly":   ("kim", "kimmy"),
    "rebecca":    ("becky", "becca"),
    "stephanie":  ("steph",),
    "victoria":   ("vicky", "vic", "tori"),
    "abigail":    ("abby", "abi"),
    "harriet":    ("hattie",),
    "alexandra":  ("alex", "sandy", "alexa"),
    "samantha":   ("sam", "sammy"),
    "michelle":   ("shelly", "mickie"),
    "christine":  ("chris", "christy", "tina"),
    "christina":  ("chris", "christy", "tina"),
    "nicole":     ("nicky", "nikki"),
    "rachel":     ("rach",),

    # Male
    "robert":     ("rob", "bob", "bobby", "robbie", "bert"),
    "michael":    ("mike", "mikey", "mick", "mickey"),
    "william":    ("bill", "billy", "will", "willy", "liam"),
    "richard":    ("rick", "dick", "rich", "ricky", "richie"),
    "james":      ("jim", "jimmy", "jamie"),
    "john":       ("jack", "johnny", "jonny"),
    "joseph":     ("joe", "joey"),
    "charles":    ("chuck", "charlie", "chas"),
    "thomas":     ("tom", "tommy"),
    "daniel":     ("dan", "danny"),
    "edward":     ("ed", "eddie", "ted", "ned", "eddy"),
    "anthony":    ("tony", "ant"),
    "christopher": ("chris", "topher"),
    "donald":     ("don", "donny", "donnie"),
    "andrew":     ("andy", "drew"),
    "stephen":    ("steve",),
    "steven":     ("steve",),
    "david":      ("dave", "davey", "davy"),
    "peter":      ("pete",),
    "ronald":     ("ron", "ronnie", "ronny"),
    "kenneth":    ("ken", "kenny"),
    "matthew":    ("matt", "mateo"),
    "henry":      ("hank", "hal", "harry"),
    "joshua":     ("josh",),
    "nathan":     ("nate", "nathaniel"),
    "samuel":     ("sam", "sammy"),
    "benjamin":   ("ben", "benny", "benji"),
    "eugene":     ("gene",),
    "frederick":  ("fred", "freddie", "freddy"),
    "gregory":    ("greg",),
    "russell":    ("russ",),
    "vincent":    ("vince", "vinny", "vinnie"),
    "francis":    ("frank", "fran"),
    "albert":     ("al", "bert", "albie"),
    "alexander":  ("alex", "al", "sandy"),
    "lawrence":   ("larry", "lawry"),
    "geoffrey":   ("geoff", "jeff"),
    "jeffrey":    ("jeff", "jeffry"),
    "leonard":    ("len", "lenny", "leo"),
    "gerald":     ("gerry", "jerry"),
    "douglas":    ("doug", "dougie"),
    "raymond":    ("ray", "raymie"),
    "philip":     ("phil",),
    "phillip":    ("phil",),
    "norman":     ("norm",),
    "timothy":    ("tim", "timmy"),
    "patrick":    ("pat", "paddy", "rick"),
    "calvin":     ("cal",),
    "walter":     ("walt", "wally"),
    "harold":     ("harry", "hal"),
    "ronald":     ("ron", "ronnie"),
    "leslie":     ("les",),
    "terrence":   ("terry",),
    "terence":    ("terry",),
    "courtney":   ("court",),
    "mortimer":   ("mort",),
    "wilfred":    ("wilf", "fred"),
    "wilbur":     ("wilbur", "wil"),
    "augustus":   ("gus",),
    "kenneth":    ("ken", "kenny"),
    "marvin":     ("marv",),

    # Political-name specifics
    "bernard":    ("bernie", "bern"),
    "joseph":     ("joe", "joey"),    # Joe Biden, Joe Manchin
    "hillary":    ("hill",),
    "barack":     (),
    "kamala":     (),
    "donald":     ("don", "donny", "donnie"),  # Donald Trump (he goes by Donald)
    "rutherford": ("ruther",),
}


def is_nickname_equivalent(a: str, b: str) -> bool:
    """True if first-names `a` and `b` refer to the same canonical name.

    Cases handled:
      - Identity ("Patricia" / "Patricia")
      - Canonical ↔ nickname ("Patricia" / "Trish")
      - Nickname ↔ nickname under same canonical ("Trish" / "Tricia")
      - Whitespace + case normalization

    Returns False on empty input.
    """
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    # a is canonical, b is one of its nicknames (or vice versa)
    if b in NICKNAMES.get(a, ()):
        return True
    if a in NICKNAMES.get(b, ()):
        return True
    # Both are nicknames of the same canonical
    for _full, nicks in NICKNAMES.items():
        if a in nicks and b in nicks:
            return True
    return False


# Detect a quoted-nickname-in-middle pattern.
# Matches: Patricia "Trish" Beynon  /  Patricia 'Trish' Beynon  /  Patricia (Trish) Beynon
# Uses [^\s] for the captured nickname so we don't grab whitespace or punctuation.
_QUOTED_NICKNAME_RE = re.compile(
    r'\s+["“\'\(]([A-Za-z][A-Za-z\-]*)["”\'\)]\s+'
)


def strip_quoted_nickname(name: str) -> tuple[str, Optional[str]]:
    """If `name` contains an inline quoted nickname like
    `Patricia "Trish" Beynon` / `Robert 'Bob' Casey` / `Daniel (Dan) Meuser`,
    return `(name_without_nickname, nickname)`. Otherwise `(name, None)`.

    Example:
      strip_quoted_nickname('Patricia "Trish" Beynon')
        → ('Patricia Beynon', 'Trish')
      strip_quoted_nickname('Paige Cognetti')
        → ('Paige Cognetti', None)
    """
    if not name:
        return ("", None)
    m = _QUOTED_NICKNAME_RE.search(name)
    if not m:
        return (name, None)
    nickname = m.group(1)
    # Replace the matched span with a single space (preserves word boundaries)
    stripped = name[:m.start()] + " " + name[m.end():]
    # Collapse extra whitespace
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return (stripped, nickname)


# Generational suffixes — these are part of identity. A "Jr." vs "Sr."
# vs no-suffix usually distinguishes father / son / grandfather (think
# Tom Kean Sr. vs Tom Kean Jr., or Robert Kennedy Sr. vs Jr.). We strip
# them when isolating the surname (so the surname comparison works) but
# we PRESERVE them separately and require them to match for two names
# to be considered the same person.
_GENERATIONAL_SUFFIXES: set[str] = {
    "jr", "jr.", "sr", "sr.",
    "ii", "iii", "iv", "v",
}

# Professional / non-identity tail tokens — same person regardless of
# whether the article includes them. "Anthony Fauci" vs "Anthony Fauci MD"
# is the same person.
_PROFESSIONAL_TRAILING_TOKENS: set[str] = {
    "esq", "esq.", "esquire",
    "md", "phd", "jd", "mba", "dds", "rn", "cpa",
    "ret", "ret.", "retired",
}


def split_first_last(name: str) -> tuple[Optional[str], Optional[str]]:
    """Return `(first_name, last_name)` from a person name.

    Strips trailing tokens (both generational AND professional) before
    picking the last token as the last name. Generational status is
    NOT preserved here — use `split_first_last_with_generation` when
    you need to compare identity-distinguishing suffixes.

    Returns (None, None) for empty.
    """
    first, last, _gen = split_first_last_with_generation(name)
    return (first, last)


def split_first_last_with_generation(
    name: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return `(first_name, last_name, generational_suffix)`.

    `generational_suffix` is the normalized form (`"jr"`, `"sr"`, `"iii"`)
    if present, else None. Professional suffixes (MD, Esq.) are stripped
    entirely; they don't distinguish identity.

    Examples:
      "Robert F. Kennedy Jr."  → ("Robert", "Kennedy", "jr")
      "Tom Kean Sr."           → ("Tom",    "Kean",    "sr")
      "Tom Kean"               → ("Tom",    "Kean",    None)
      "Dr. Anthony Fauci MD"   → ("Dr.",    "Fauci",   None)  (caller should strip Dr.)
    """
    if not name:
        return (None, None, None)
    parts = [p.strip(",") for p in name.strip().split() if p.strip(",")]
    if not parts:
        return (None, None, None)

    generation: Optional[str] = None
    # Walk from the end, stripping suffixes one at a time. Generational is
    # captured the first time we see it; professional tokens just get stripped.
    while len(parts) > 1:
        tail = parts[-1].lower()
        if tail in _GENERATIONAL_SUFFIXES:
            if generation is None:
                generation = tail.rstrip(".")
            parts = parts[:-1]
        elif tail in _PROFESSIONAL_TRAILING_TOKENS:
            parts = parts[:-1]
        else:
            break

    if len(parts) == 1:
        return (parts[0], None, generation)
    return (parts[0], parts[-1], generation)


def person_names_match(a: str, b: str) -> bool:
    """True if `a` and `b` look like the same person, allowing for
    nickname variants and middle-initial/nickname insertions.

    Generational suffixes (Jr., Sr., II, III) are treated as part of
    identity — a "Jr." matches a "Jr." but not a no-suffix or "Sr.".
    Tom Kean Sr. and Tom Kean Jr. are different people; we must not
    collapse them.

    Examples:
      person_names_match("Patricia Beynon", "Trish Beynon")        → True
      person_names_match("Patricia Beynon", "Patricia A. Beynon")  → True
      person_names_match("Robert Casey", "Bob Casey")              → True
      person_names_match("Robert Casey", "Robert Smith")           → False (different last)
      person_names_match("Patricia Beynon", "Trish Smith")         → False
      person_names_match("Tom Kean Jr.", "Tom Kean")               → False (different generation)
      person_names_match("Tom Kean Jr.", "Tom Kean, Jr.")          → True  (same generation)
      person_names_match("Thomas Kean Jr.", "Tom Kean Jr.")        → True  (nickname + same gen)
    """
    if not a or not b:
        return False
    a_clean, _ = strip_quoted_nickname(a)
    b_clean, _ = strip_quoted_nickname(b)

    first_a, last_a, gen_a = split_first_last_with_generation(a_clean)
    first_b, last_b, gen_b = split_first_last_with_generation(b_clean)

    if not first_a or not first_b:
        return False
    # Generational status must agree. Both None, or both same suffix.
    if gen_a != gen_b:
        return False
    # Last names must match (case-insensitive). Single-name people: require
    # exact match on the only token they have.
    if last_a is None and last_b is None:
        return first_a.lower() == first_b.lower()
    if last_a is None or last_b is None:
        return False
    if last_a.lower() != last_b.lower():
        return False
    # First names must be nickname-equivalent.
    return is_nickname_equivalent(first_a, first_b)
