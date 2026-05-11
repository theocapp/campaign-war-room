"""
Semantic normalization for political claims (v3 — minimal semantic_id).

Produces a canonical text form (normalized_text) and a stable hash
(semantic_id) that enables near-duplicate deduplication without relying
on exact string matching.

v3 algorithm
────────────
1. Remove hedging phrases with word-boundary-safe regex.
2. Apply multi-word phrase substitutions with word-boundary-safe regex
   (``re.sub(r'\\b<phrase>\\b', canonical, text)``).  Prevents partial-word
   corruption: "voted for their" no longer matches "voted for the".
3. Single-word token synonym normalization (alphabetic tokens only).
4. Stop-word removal.
5. Wrapper-word removal.
6. Light suffix stripping.
7. Build a minimal canonical key:
       action:<token>  — sorted normalized action tokens
       stance:<value>
       ent:<name>      — sorted title-stripped canonical entity names
   semantic_id = SHA-256[:16] of "|".join(parts).

Correctness properties
──────────────────────
• Entity drift: "Rep. Jane Smith" and "Jane Smith" normalize to the same
  entity key via _normalize_entity_name().
• Numeric transparency: dollar amounts and percentages are preserved in
  normalized_text but do NOT influence semantic_id — different amounts for
  the same action/entity/stance are treated as the same assertion for dedup
  purposes.
• Issue slug transparency: issue_slugs are accepted by normalize_claim for
  caller convenience but are NOT included in the hash.  Issue tagging must
  not influence dedup identity.
• Phrase boundary safety: all substitutions use \\b anchors — no
  partial-word corruption.

Provenance guarantee (unchanged)
────────────────────────────────
semantic_id is used only to skip a second KGClaim write when the SAME
SOURCE produces the same assertion in different words.  Cross-source rows
are never merged.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional


# ── Numeric pattern — used only to blank numerics before tokenization ──────────
# This prevents suffix words like "million" in "$1.2 million" from leaking
# into the action token bag and causing "$1.2M" vs "$1.2 million" to produce
# different token sets.  Numeric content is preserved in normalized_text.

_NUMERIC_RE = re.compile(
    r'(?:'
    # Dollar amount with word suffix: $1.2 million, $500 thousand, $2 billion
    r'\$[\d,]+(?:\.\d+)?\s*(?:billion|million|thousand|[bBmMkK])\b'
    r'|'
    # Plain dollar amount: $500,000 / $3.50
    r'\$[\d,]+(?:\.\d+)?'
    r'|'
    # Percentage: 42%, 3.5%
    r'\d+(?:\.\d+)?\s*%'
    r'|'
    # Vote / score ratio: 73-27, 213-198
    r'\b\d+\s*-\s*\d+\b'
    r'|'
    # Large integers with commas (budget lines, vote counts): 1,234,567
    r'\b\d{1,3}(?:,\d{3})+\b'
    r')',
    re.IGNORECASE,
)


# ── Entity name normalization ──────────────────────────────────────────────────
# Strips political title prefixes so "Rep. Jane Smith" and "Jane Smith" resolve
# to the same key.  Does NOT attempt fuzzy matching or nickname resolution.

_ENTITY_TITLE_RE = re.compile(
    r"^(?:"
    r"the\s+hon(?:orable)?\.?\s+"
    r"|rep(?:resentative)?\.?\s+"
    r"|sen(?:ator)?\.?\s+"
    r"|congressman\.?\s+"
    r"|congresswoman\.?\s+"
    r"|gov(?:ernor)?\.?\s+"
    r"|lt\.?\s+gov\.?\s+"
    r"|dr\.?\s+"
    r"|mr\.?\s+"
    r"|mrs\.?\s+"
    r"|ms\.?\s+"
    r"|hon\.?\s+"
    r"|col\.?\s+"
    r"|maj\.?\s+"
    r")",
    re.IGNORECASE,
)


def _normalize_entity_name(name: str) -> str:
    """
    Deterministic normalization for entity names.  Strips title prefixes and
    lowercases.  Iterates to handle stacked titles ("The Hon. Dr. Smith").
    """
    s = name.strip()
    while True:
        stripped = _ENTITY_TITLE_RE.sub("", s).strip()
        if stripped == s:
            break
        s = stripped
    return re.sub(r"\s+", " ", s).lower().strip()


# ── Phrase substitutions ───────────────────────────────────────────────────────

_RAW_PHRASE_MAP: list[tuple[str, str]] = [
    # ── Vote / position actions ────────────────────────────────────────────
    ("voted against the",           "opposed"),
    ("voted against",               "opposed"),
    ("voted no on the",             "opposed"),
    ("voted no on",                 "opposed"),
    ("cast a vote against the",     "opposed"),
    ("cast a vote against",         "opposed"),
    ("came out against the",        "opposed"),
    ("came out against",            "opposed"),
    ("spoke out against the",       "opposed"),
    ("spoke out against",           "opposed"),
    ("stands opposed to the",       "opposed"),
    ("stands opposed to",           "opposed"),
    ("is opposed to the",           "opposed"),
    ("is opposed to",               "opposed"),
    ("voted in favor of the",       "supported"),
    ("voted in favor of",           "supported"),
    ("voted yes on the",            "supported"),
    ("voted yes on",                "supported"),
    ("voted for the",               "supported"),
    ("voted for",                   "supported"),
    ("voted to pass the",           "supported"),
    ("voted to pass",               "supported"),
    ("cast a vote for the",         "supported"),
    ("cast a vote for",             "supported"),
    ("came out in favor of the",    "supported"),
    ("came out in favor of",        "supported"),
    ("spoke in favor of the",       "supported"),
    ("spoke in favor of",           "supported"),
    ("stands in support of the",    "supported"),
    ("stands in support of",        "supported"),
    ("is in support of the",        "supported"),
    ("is in support of",            "supported"),
    # ── Funding receipt ────────────────────────────────────────────────────
    ("accepted donations from",     "received contributions from"),
    ("accepted contributions from", "received contributions from"),
    ("accepted money from",         "received contributions from"),
    ("raised money from",           "received contributions from"),
    ("received donations from",     "received contributions from"),
    ("received money from",         "received contributions from"),
    ("got contributions from",      "received contributions from"),
    ("got money from",              "received contributions from"),
    # ── Legislative object normalization ───────────────────────────────────
    ("infrastructure funding",      "infrastructure legislation"),
    ("infrastructure bill",         "infrastructure legislation"),
    ("infrastructure act",          "infrastructure legislation"),
    ("infrastructure package",      "infrastructure legislation"),
    ("infrastructure plan",         "infrastructure legislation"),
    ("infrastructure proposal",     "infrastructure legislation"),
    ("healthcare funding",          "healthcare legislation"),
    ("healthcare bill",             "healthcare legislation"),
    ("healthcare act",              "healthcare legislation"),
    ("education funding",           "education legislation"),
    ("education bill",              "education legislation"),
    ("climate funding",             "climate legislation"),
    ("climate bill",                "climate legislation"),
    ("climate act",                 "climate legislation"),
    # ── Organisation normalization ─────────────────────────────────────────
    ("political action committees", "pac"),
    ("political action committee",  "pac"),
]

_PHRASE_MAP: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE),
        canonical,
    )
    for phrase, canonical in sorted(_RAW_PHRASE_MAP, key=lambda p: -len(p[0]))
]


# ── Hedging phrases ────────────────────────────────────────────────────────────

_HEDGING_RES: list[re.Pattern[str]] = [
    re.compile(r"\b" + re.escape(h) + r"\b", re.IGNORECASE)
    for h in sorted(
        [
            "according to reports",
            "it has been reported that",
            "it is reported that",
            "it has been alleged that",
            "sources close to",
            "reportedly",
            "allegedly",
            "apparently",
            "purportedly",
            "supposedly",
            "claims that",
            "claim that",
            "said to have",
            "is said to",
            "according to",
            "sources say",
        ],
        key=len,
        reverse=True,
    )
]


# ── Single-word synonym table ──────────────────────────────────────────────────

_TOKEN_SYNONYMS: dict[str, str] = {
    # Opposition
    "opposes":       "opposed",
    "opposing":      "opposed",
    "rejected":      "opposed",
    "rejects":       "opposed",
    "rejecting":     "opposed",
    "blocked":       "opposed",
    "blocks":        "opposed",
    "blocking":      "opposed",
    "fought":        "opposed",
    "denounced":     "opposed",
    "denounces":     "opposed",
    "criticized":    "opposed",
    "criticizes":    "opposed",
    "objected":      "opposed",
    "resisted":      "opposed",
    # Support
    "supports":      "supported",
    "supporting":    "supported",
    "endorsed":      "supported",
    "endorses":      "supported",
    "endorsing":     "supported",
    "backed":        "supported",
    "backs":         "supported",
    "backing":       "supported",
    "championed":    "supported",
    "champions":     "supported",
    "promoted":      "supported",
    "promotes":      "supported",
    "advocated":     "supported",
    "advocates":     "supported",
    "co-sponsored":  "supported",
    "cosponsored":   "supported",
    "favored":       "supported",
    "favors":        "supported",
    # Financial
    "donations":     "contributions",
    "donation":      "contributions",
    "contribution":  "contributions",
    "disbursement":  "contributions",
    "disbursements": "contributions",
    "pacs":          "pac",
    # Legislative authorship
    "introduced":    "proposed",
    "introduces":    "proposed",
    "introducing":   "proposed",
    "authored":      "proposed",
    "filed":         "proposed",
    "files":         "proposed",
    "filing":        "proposed",
    # Receipt
    "received":      "received",
    "accepted":      "received",
    "got":           "received",
    "accepts":       "received",
    "receives":      "received",
}


# ── Stop words ─────────────────────────────────────────────────────────────────

_STOP_WORDS: frozenset[str] = frozenset(
    [
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "that", "this", "these", "those",
        "it", "its", "he", "she", "they", "them", "his", "her", "their",
        "who", "which", "what", "when", "where", "how", "not", "no", "also",
        "both", "about", "any", "more", "than", "as", "so", "if", "then",
        "there", "here", "all", "each", "while", "after", "before",
        "during", "over", "under", "against",
    ]
)


# ── Wrapper words ──────────────────────────────────────────────────────────────

_WRAPPER_WORDS: frozenset[str] = frozenset(
    [
        "bill", "act", "law", "measure", "legislation", "resolution",
        "amendment", "proposal", "package", "plan", "program", "initiative",
        "funding", "funds", "fund", "grant", "grants", "investment", "investments",
        "budget", "spending", "appropriation", "appropriations",
        "deal", "reform", "policy", "regulation", "regulations", "rules",
        "requirements", "coverage", "provision", "provisions",
    ]
)


# ── Suffix stripping ───────────────────────────────────────────────────────────

def _strip_suffix(token: str) -> str:
    if len(token) > 6 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 5 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 6 and token.endswith("tion"):
        return token[:-4]
    if len(token) > 6 and token.endswith("sion"):
        return token[:-4]
    if len(token) > 5 and token.endswith("ers"):
        return token[:-1]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    return token


# ── Public API ─────────────────────────────────────────────────────────────────

def normalize_claim(
    text: str,
    stance: str = "unknown",
    entity_names: Optional[list[str]] = None,
    issue_slugs: Optional[list[str]] = None,  # accepted but NOT used in hash
) -> tuple[str, str]:
    """
    Return ``(normalized_text, semantic_id)`` for a raw claim string.

    Parameters
    ──────────
    entity_names
        Surface or canonical entity names.  Title prefixes (Rep., Sen., Dr.,
        etc.) are stripped deterministically before hashing.
    issue_slugs
        Accepted for caller convenience but NOT included in the semantic_id
        hash.  Issue tagging must not influence dedup identity.

    Returns
    ───────
    normalized_text
        Human-readable canonical form: phrase-substituted, numerics preserved.
    semantic_id
        SHA-256[:16] of a minimal structured field composition:
          ``action:<token>``  sorted normalized action tokens
          ``stance:<value>``
          ``ent:<name>``      sorted title-stripped canonical entity names
        Numeric facts and issue slugs are intentionally excluded so that
        different amounts or issue tags for the same assertion do not produce
        separate dedup buckets.
    """
    # 1. Lowercase
    working = text.lower().strip()

    # 2. Remove hedging phrases (word-boundary safe)
    for hedge_re in _HEDGING_RES:
        working = hedge_re.sub(" ", working)

    # 3. Multi-word phrase substitution (word-boundary safe, longest first)
    for pattern, canonical in _PHRASE_MAP:
        working = pattern.sub(canonical, working)

    # 4. Collapse whitespace
    working = re.sub(r"\s+", " ", working).strip()

    # Human-readable form — numerics are still present in the text
    normalized_text = working

    # 5. Tokenize — alphabetic action tokens only.
    # Blank out numeric matches first so that suffix words like "million" or
    # "billion" in "$1.2 million" never enter the action token bag and cause
    # "$1.2M" and "$1.2 million" to produce different action token sets.
    working_for_tokens = _NUMERIC_RE.sub(" ", working)
    tokens = re.findall(r"\b[a-z][a-z0-9\-]*\b", working_for_tokens)

    # 6. Single-word synonym substitution
    tokens = [_TOKEN_SYNONYMS.get(t, t) for t in tokens]

    # 7. Remove stop words
    tokens = [t for t in tokens if t not in _STOP_WORDS]

    # 8. Remove wrapper words
    tokens = [t for t in tokens if t not in _WRAPPER_WORDS]

    # 9. Suffix stripping
    tokens = [_strip_suffix(t) for t in tokens]

    # 10. Deduplicate and filter very short residuals
    seen: set[str] = set()
    clean: list[str] = []
    for t in tokens:
        if len(t) >= 3 and t not in seen:
            seen.add(t)
            clean.append(t)

    # 11. Build entity keys using title-stripped canonical names
    entity_key_parts = sorted(
        f"ent:{_normalize_entity_name(name)}"
        for name in (entity_names or [])
        if name
    )

    stance_key = (stance or "unknown").strip().lower()

    # Minimal canonical key: action tokens + stance + entity names only.
    # Numeric facts and issue slugs are excluded by design.
    parts: list[str] = (
        [f"action:{t}" for t in sorted(clean)]
        + [f"stance:{stance_key}"]
        + entity_key_parts
    )
    canonical_str = "|".join(parts)
    semantic_id = hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()[:16]

    return normalized_text, semantic_id
