"""Honorific + title prefix/suffix stripping for person names.

Catches fragmentation patterns like:
  "Dr. Mehmet Oz"      ↔ "Mehmet Oz"
  "Rep. Bresnahan"     ↔ "Rob Bresnahan"  (combined with nickname matcher)
  "Sen. Bob Casey Jr." ↔ "Bob Casey"      (prefix + suffix combined)
  "Mayor Cognetti"     ↔ "Paige Cognetti"

Generic across any campaign — political honorifics (Rep., Sen., Gov.,
Mayor, President) plus civilian titles (Dr., Mr., Ms., Rev., Prof.) plus
military ranks (Gen., Col., Capt., Lt., Sgt.) plus name suffixes (Jr.,
Sr., II, III, IV, V, Esq., MD, PhD).

Used in canonicalize_entity as a normalize step before the exact + alias
match attempts. When a stripped form matches an existing entity, the
ORIGINAL form is auto-added as an alias so the cheap path catches it
next time.
"""
from __future__ import annotations

import re


# Honorific + role prefixes. Lowercase. Dots optional in matching.
# Multi-word phrases are explicitly listed; the matcher tries longest first.
PREFIX_TITLES: tuple[str, ...] = (
    # Civilian
    "dr", "mr", "ms", "mrs", "mx", "miss",
    "rev", "reverend", "fr", "father", "sister", "sr",
    "prof", "professor",
    # Political
    "rep", "representative",
    "sen", "senator",
    "gov", "governor",
    "mayor",
    "pres", "president",
    "vp", "vice president",
    "congressman", "congresswoman",
    "councilman", "councilwoman",
    "councilmember", "councilor",
    "secretary", "ambassador",
    "attorney general", "ag",
    # Legal
    "judge", "justice", "chief justice",
    # Military
    "gen", "general",
    "lt gen", "lieutenant general",
    "maj gen", "major general",
    "brig gen", "brigadier general",
    "col", "colonel",
    "lt col", "lieutenant colonel",
    "maj", "major",
    "capt", "captain",
    "lt", "lieutenant",
    "sgt", "sergeant",
    "cpl", "corporal",
    "pvt", "private",
    "adm", "admiral",
    "cmdr", "commander",
    "officer", "detective",
)

# Suffixes after the last name. Lowercase. Dots optional in matching.
SUFFIX_TITLES: tuple[str, ...] = (
    "jr", "sr", "ii", "iii", "iv", "v",
    "esq", "esquire",
    "md", "phd", "jd", "mba", "dds", "rn", "cpa",
    "ret", "retired",
)

# GENERATIONAL suffixes — they distinguish two PEOPLE (father vs son).
# Stripping "Jr." can collapse a real person into a different one. Audit
# code must NOT auto-propose merges based only on a generational-suffix
# strip; require human review.
GENERATIONAL_SUFFIXES: frozenset[str] = frozenset({
    "jr", "jr.", "sr", "sr.",
    "ii", "iii", "iv", "v",
})


def removed_only_generational(removed: list[str]) -> bool:
    """True if `removed` (output of strip_honorifics) contains ONLY
    generational suffixes. Used to gate audit auto-merge proposals —
    a Jr.→non-Jr. merge is ambiguous and needs human review."""
    if not removed:
        return False
    for r in removed:
        norm = r.strip().lower().rstrip(".")
        if norm not in {s.rstrip(".") for s in GENERATIONAL_SUFFIXES}:
            return False
    return True

# Build matching-friendly variants (dot / no-dot) so "Dr." and "Dr" both match.
def _expand_variants(items: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for s in items:
        s = s.strip()
        if not s:
            continue
        # Bare form, with dot, with single trailing period
        out.add(s)
        out.add(s + ".")
        # Multi-word forms — also accept "lt.gen." and "lt gen." patterns
        if " " in s:
            parts = s.split()
            out.add(" ".join(p + "." for p in parts))   # "lt. gen."
            out.add(".".join(parts) + ".")              # "lt.gen."
    return out


_PREFIX_FORMS: set[str] = _expand_variants(PREFIX_TITLES)
_SUFFIX_FORMS: set[str] = _expand_variants(SUFFIX_TITLES)

# Sort by length desc so multi-word matches consume before single-word
_PREFIX_FORMS_SORTED: list[str] = sorted(_PREFIX_FORMS, key=lambda s: (-len(s), s))
_SUFFIX_FORMS_SORTED: list[str] = sorted(_SUFFIX_FORMS, key=lambda s: (-len(s), s))


def _starts_with_word(text_lc: str, candidate: str) -> bool:
    """True if `text_lc` starts with `candidate` followed by a word boundary."""
    if not text_lc.startswith(candidate):
        return False
    nxt = text_lc[len(candidate):len(candidate) + 1]
    return nxt == "" or nxt == " " or nxt == "\t"


def _ends_with_word(text_lc: str, candidate: str) -> bool:
    """True if `text_lc` ends with `candidate` preceded by a word boundary
    (or is exactly the candidate)."""
    if not text_lc.endswith(candidate):
        return False
    prev_idx = len(text_lc) - len(candidate) - 1
    if prev_idx < 0:
        return True
    prev = text_lc[prev_idx]
    return prev == " " or prev == "\t" or prev == ","


def strip_honorifics(name: str) -> tuple[str, list[str]]:
    """Return `(stripped_name, removed_parts)`.

    Removes any leading honorific (Dr., Rep., Gov., etc.) and trailing
    suffix (Jr., II, MD, etc.) from a person's name. Greedy: handles
    chained titles like "Rep. Dr. Smith" or "Bob Casey Jr. Esq."

    Returns the cleaned name (with single-space whitespace) and a list
    of the removed tokens for logging/aliasing.

    Examples:
      strip_honorifics("Dr. Mehmet Oz")           → ("Mehmet Oz", ["Dr."])
      strip_honorifics("Rep. Rob Bresnahan")      → ("Rob Bresnahan", ["Rep."])
      strip_honorifics("Sen. Bob Casey Jr.")      → ("Bob Casey", ["Sen.", "Jr."])
      strip_honorifics("Paige Cognetti")          → ("Paige Cognetti", [])
      strip_honorifics("Lt. Gen. Michael Flynn")  → ("Michael Flynn", ["Lt. Gen."])
    """
    if not name:
        return ("", [])
    text = name.strip()
    removed: list[str] = []

    # Strip leading prefixes (greedy, longest-first to consume multi-word forms)
    changed = True
    while changed:
        changed = False
        text_lc = text.lower()
        for cand in _PREFIX_FORMS_SORTED:
            if _starts_with_word(text_lc, cand):
                # Remove from original (preserve case for the removed token log)
                removed.append(text[:len(cand)].strip())
                text = text[len(cand):].lstrip(" ,\t")
                changed = True
                break

    # Strip trailing suffixes (greedy, longest-first)
    changed = True
    while changed:
        changed = False
        text_lc = text.lower().rstrip(" ,\t.")
        for cand in _SUFFIX_FORMS_SORTED:
            if _ends_with_word(text_lc, cand):
                # Compute the start position in the (possibly trimmed) original
                # by re-locating the end of the original text minus the suffix.
                # Easier: rebuild from tokens.
                # Tokenize on whitespace + commas
                tokens = re.split(r"([ ,]+)", text)
                # Find the trailing non-whitespace tokens that, lowercased + joined, equal cand
                tokens_filtered = [t for t in tokens if t.strip(" ,") and t.strip(" ,") != ""]
                # Build candidates from end
                hit_idx = None
                for take in range(1, min(4, len(tokens_filtered)) + 1):
                    tail = " ".join(t.strip(" ,").lower().rstrip(".") for t in tokens_filtered[-take:])
                    if tail == cand.rstrip(".") or tail + "." == cand:
                        hit_idx = take
                        break
                if hit_idx:
                    removed_str = " ".join(tokens_filtered[-hit_idx:])
                    removed.append(removed_str.strip())
                    tokens_filtered = tokens_filtered[:-hit_idx]
                    text = " ".join(tokens_filtered).rstrip(" ,\t.")
                    changed = True
                    break

    # Collapse internal whitespace and strip orphan punctuation
    text = re.sub(r"\s+", " ", text).strip(" ,\t.")
    return (text, removed)


def has_honorific(name: str) -> bool:
    """Quick check: would strip_honorifics change this name?"""
    if not name:
        return False
    cleaned, removed = strip_honorifics(name)
    return bool(removed) and cleaned != name.strip()
