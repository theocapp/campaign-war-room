"""Deterministic label correction for v15.0 claim_records.

Built after a manual audit of 38 stratified-random records (2026-05-28)
found label accuracy was ~70%, not the ~99% the automated verbatim
check implied. The LLM was over-applying labels based on semantic
similarity rather than schema definitions — e.g. labeling "expresses
strong support for the project" as `endorsement` when the schema
requires endorsement of a candidate or named bill.

DESIGN:
  Two layers, both pure regex (no LLM calls):

  1. POSITIVE RULES — when the quote text contains a high-confidence
     trigger pattern, set the label to the target. Overrides whatever
     the LLM emitted (including null).
       e.g. "voted in favor of H.R. 5688" → label = vote

  2. SANITY DOWNGRADE — when the LLM emitted a label but the quote
     contains zero trigger words for that label, downgrade to null.
       e.g. label = endorsement, but quote has no "endors*"/"backs"/
       "throws support behind" → label = null

  Ambiguous cases (no positive trigger AND LLM's label passes sanity)
  keep the LLM's label unchanged.

USE FROM:
  - persist_claims (in entity_extraction.py) for every new extraction
  - scripts/relabel_claim_records.py for one-shot correction of existing

Adding a new rule? Test it against the 38-record audit sample first.
The temptation is to add more positive triggers; resist unless the
trigger is genuinely unambiguous. Better to leave a label uncertain
than to assert one we can't defend.
"""
from __future__ import annotations

import re
from typing import Optional


# ── Positive rules ───────────────────────────────────────────────────────
# Patterns that, when present in the quote, unambiguously identify the
# label. First match wins. Order matters — most-specific patterns first.

# Each entry: (compiled regex, target label, rule name for audit logging)
# ORDER MATTERS: a denial-of-attack quote contains both denial verbs AND
# attack nouns ("Bresnahan denied any insider trading"). Defense must
# fire first; otherwise the attack noun captures it.
POSITIVE_RULES: list[tuple[re.Pattern, str, str]] = [
    # DEFENSE — explicit denial / rebuttal language. RUN FIRST so denials
    # of attacks (which contain both defense verbs AND attack nouns) get
    # classified as defense, not attack.
    (re.compile(r"\b(?:denied|denies|denying)\b.{0,80}\b(?:any|the|claims?|allegations?|insinuation|reports?|charges?|accusations?|wrongdoing|involvement)\b", re.I | re.DOTALL),
     "defense", "denied_claim"),
    (re.compile(r"\b(?:rejected|rejects?|disputed|disputes?|refuted|refutes?|pushed back (?:on|against)|hit back at|fired back)\b", re.I),
     "defense", "rejected_dispute"),
    (re.compile(r"\b(?:fact[- ]check|misleading|inaccurate|not true|simply not true|categorically (?:false|deny|denied))\b", re.I),
     "defense", "fact_check"),

    # VOTE-AS-ATTACK — runs BEFORE the neutral vote rule, because a vote
    # framed as harmful (e.g. "voted for policies that are increasing
    # the cost") is structurally a vote but rhetorically an attack on
    # the voter. Covers two patterns:
    #   (a) "voted/voting to <harm-verb>": voted to gut, voting to repeal, etc.
    #   (b) "voted/voting (to|for) <noun phrase incl. harmful object>":
    #       e.g. "voted for the cuts to SNAP", "voted for policies that hurt",
    #       "voting for a bill that would cut Medicaid"
    #   (c) "voted against <protected interest>": medicaid, working families, etc.
    (re.compile(
        # Branch 1: vote VERB to harm-verb
        r"\b(?:voted|voting) to (?:gut|cut|strip|kill|defund|repeal|"
        r"hurt|harm|take away|raise|increase|jack up|slash|eliminate|end|sunset)\b",
        re.I),
     "attack", "voted_to_harm"),
    (re.compile(
        # Branch 2a: "voted/voting (to|for) [policies/etc] that [harm-verb]"
        r"\b(?:voted|voting) (?:to|for) (?:policies?|measures?|bills?|laws?|legislation|a bill) "
        r"(?:that|to|which) \s*(?:would\s+|will\s+|are\s+|is\s+)?"
        r"(?:hurt(?:s|ing)?|harm(?:s|ing)?|gut(?:s|ting)?|cut(?:s|ting)?|"
        r"slash(?:es|ing)?|strip(?:s|ping)?|defund(?:s|ing)?|"
        r"repeal(?:s|ing)?|take(?:s|n|ing)?\s+away|"
        r"increas(?:e[ds]?|ing)\s+(?:costs?|prices?|taxes|the cost)|"
        r"rais(?:e[ds]?|ing)\s+(?:costs?|taxes|prices?)|"
        r"eliminate|end|sunset|go(?:es|ing)?\s+against)\b",
        re.I),
     "attack", "voted_for_harmful_policies"),
    (re.compile(
        # Branch 2b: "voted/voting (for|in favor of|to pass) <0-60 chars> <harm-noun>"
        # The flexible [^.!?\n]{0,60} captures intervening qualifiers
        # ("the largest", "$300 billion in", "massive", etc.) without
        # crossing sentence boundaries (which would make the connection
        # spurious). Examples this catches:
        #   "voted for the largest cuts to Medicaid"
        #   "voted for $300 billion in cuts to SNAP"
        #   "voted in favor of historic cuts"
        #   "voting for a massive repeal of"
        r"\b(?:voted|voting) (?:for|to (?:pass|approve|advance)|in favor of)\b"
        r"[^.!?\n]{0,60}"
        r"\b(?:cuts? to|in cuts? to|cut (?:in|from)|repeal of|elimination of|"
        r"defunding of|gutting of|stripping of|slashing of)\b",
        re.I),
     "attack", "voted_for_cuts"),
    (re.compile(
        # Branch 3: "voted against <protected interest>"
        r"\b(?:voted|voting) against (?:protections?|the people|working families|"
        r"medicaid|medicare|social security|the affordable care act|veterans|seniors|"
        r"public schools?|food assistance|snap)\b", re.I),
     "attack", "voted_against_protection"),

    # VOTE — must include voted/votes + a vote-action word
    (re.compile(r"\bvoted (?:in favor of|yes on|against|no on|for|to (?:pass|approve|reject|advance|kill|block))\b", re.I),
     "vote", "voted_in_favor_of"),
    (re.compile(r"\b(?:cast|casts|casting) (?:a|her|his|their) (?:'?yes'?|'?no'?|vote)\b", re.I),
     "vote", "cast_vote"),

    # ENDORSEMENT — only for candidate / bill endorsements. Requires the
    # word "endors*" or "backs"/"backing" in proximity to a candidate
    # or office context.
    (re.compile(r"\b(?:endorses?|endorsed|endorsing|endorsement of)\b.{0,80}\b(?:for (?:congress|senate|the house|president|mayor|governor|reelection|the nomination|the \w+ district)|in (?:the \w+ (?:primary|election|race)))\b", re.I | re.DOTALL),
     "endorsement", "endorsed_for_office"),
    (re.compile(r"\b(?:endorsed by|received (?:the|an) endorsement|throw[ns]? (?:his|her|their) (?:full )?(?:support|weight) behind|publicly thrown (?:his|her|their) support behind)\b", re.I),
     "endorsement", "endorsement_received"),
    (re.compile(r"\b(?:officially backed|officially backs|formally endorsed|has the backing of)\b", re.I),
     "endorsement", "officially_backed"),

    # ANNOUNCEMENT — explicit campaign rollout language
    (re.compile(r"\b(?:announce[ds]?|announces|announcing|launch(?:es|ed|ing)?|kick(?:s|ed)? off|debut[s]?(?:ed|ing)?)\b.{0,50}\b(?:campaign|candidacy|bid|run|race|reelection bid)\b", re.I | re.DOTALL),
     "announcement", "announced_campaign"),
    (re.compile(r"\bofficially (?:announce[ds]?|launches?|launched|files? to run|filed for)\b", re.I),
     "announcement", "officially_announced"),

    # ATTACK — explicit attack verbs in the quote
    (re.compile(r"\b(?:slammed?|blasted|condemned?|attacked|accused (?:\w+ )?of|criticiz(?:es|ed))\b", re.I),
     "attack", "attack_verb"),
    # Attack nouns — see `_attack_noun_in_attacking_position` below. We
    # use a function instead of a pure regex because Python's `re` module
    # doesn't support variable-width lookbehind, and we need to exclude
    # cases where the attack noun is the topic ("fights corruption",
    # "anti-corruption bill") rather than an accusation against someone.
    # This is registered as a "callable rule" — the runner detects the
    # `None` regex and dispatches to the function.
    (None, "attack", "attack_noun"),

    # COMMITMENT — first-person / promise language. Strict to first-person
    # to avoid catching reportage of past commitments.
    (re.compile(r"\b(?:I (?:will|won't|wo'?n't|am committed|pledge|promise|commit|vow|refuse to let)\b|I'll \b)", re.I),
     "commitment", "first_person_will"),
    (re.compile(r"\b(?:we (?:will|won't|wo'?n't|are committed|pledge|promise|commit|vow)|we'll)\b", re.I),
     "commitment", "first_person_plural_will"),
    (re.compile(r"\b(?:pledged|promised|vowed|committed) to\b", re.I),
     "commitment", "past_pledge"),
]


# ── Sanity downgrades ────────────────────────────────────────────────────
# For each label, what trigger words MUST be present in the quote for the
# label to be defensible? If LLM emits the label but none of these are
# present, downgrade to null.
#
# NOT exhaustive — these are minimal patterns. Quotes containing rich
# alternative phrasing still pass (because they'll match positive rules
# above first, OR because we deliberately accept some LLM judgment for
# fuzzy categories like statement / policy_position).

SANITY_PATTERNS: dict[str, re.Pattern] = {
    "endorsement": re.compile(
        r"\b(?:endors|back(s|ed|ing)?\b.{0,30}\b(?:for|in)\b|"
        r"support(?:s|ed)?\b.{0,40}\b(?:for (?:congress|senate|house|president|mayor|governor)|"
        r"(?:'?s)? candidacy|(?:'?s)? campaign|in the \w+ (?:primary|race|election))|"
        r"throws? .*? support behind)",
        re.I | re.DOTALL,
    ),
    "vote": re.compile(r"\b(?:vote[ds]?|voting|in favor of|against)\b", re.I),
    "announcement": re.compile(
        r"\b(?:announce|launch|debut|kick(?:s|ed) off|files? to run|filed for|officially)\b",
        re.I,
    ),
    "attack": re.compile(
        r"\b(?:criticiz|attack|slam|blast|accus|condemn|deni(?:es|ed)|corrupt|"
        r"scandal|crooked|hypocris|gut|cuts?\b|strip|misleading|broken promises?|liar?|"
        r"insider trading|pay[- ]to[- ]play|reject(?:s|ed) (?:by|her|his)|"
        r"abandon|sold out|betray|failed)\b",
        re.I,
    ),
    "defense": re.compile(
        r"\b(?:deni|reject|dispute|refute|push(?:ed)? back|fact[- ]check|defend|"
        r"misleading|not true|inaccurate|wrong about|hit back|fired back)\b",
        re.I,
    ),
    "commitment": re.compile(
        r"\b(?:I (?:will|won't|wo'?n't|am committed|pledge|promise|commit|vow|refuse to let)|"
        r"I'll|we (?:will|won't|wo'?n't|are committed|pledge|promise|commit|vow)|we'll|"
        r"pledged|promised|vowed|committed to|won't (?:let|allow))\b",
        re.I,
    ),
    # policy_position and statement are deliberately omitted —
    # those labels accept very broad quote shapes; trying to enforce
    # patterns produces too many false-positive downgrades.
}


# Regex matching any "topic-mode" prefix verb anywhere in the preceding
# ~40 chars of an attack noun. Flips attack-mode ("X is corrupt") to
# topic-mode ("Cognetti fights political corruption"). Adjective-laden
# variants like "fights POLITICAL corruption" are caught because we
# scan the full window, not just the immediately-adjacent token.
_ATTACK_NOUN_TOPIC_PREFIX_RE = re.compile(
    r"\b(?:fights?|fighting|fought|"
    r"anti[- ]|"
    r"against|"
    r"combat(?:s|ed|ing)?|"
    r"tackl(?:es?|ing|ed)|"
    r"bans?|banning|banned|"
    r"address(?:es|ing|ed)?|"
    r"ends?|ending|ended|"
    r"prevent(?:s|ing|ed)?|"
    r"oppos(?:es?|ing|ed)|"
    r"stops?|stopping|stopped|"
    r"calls? for|calling for|"
    r"clean(?:s|ing|ed)?\s+up|"  # "clean up corruption" = topic mode
    r"root(?:s|ing|ed)?\s+out|"  # "root out corruption"
    r"weed(?:s|ing|ed)?\s+out|"  # "weed out corruption"
    r"work(?:s|ing|ed)? to (?:end|stop|prevent|combat|address|clean|root|weed)|"
    r"pillar of|focus on|focused on|focusing on)\b",
    re.I,
)

_ATTACK_NOUN_RE = re.compile(
    r"\b(scandal|corrupt(?:ion|s)?|crooked|insider trading|pay[- ]to[- ]play|"
    r"hypocrisy|hypocrite|broken promises?|sold out|"
    r"abandon(?:ed|ing)?|betray(?:ed|ing)?|failed (?:to|us|her|his))\b",
    re.I,
)


def _attack_noun_in_attacking_position(evidence_span: str) -> bool:
    """True if the quote contains an attack noun that's actually accusing
    someone (not the topic of someone's anti-X campaign).

    For each attack-noun match in the span, look at the preceding window
    PLUS the attack noun itself for a "topic-mode" verb (fights / anti- /
    tackle / combat / etc.). Including the noun in the window matters for
    cases like "Anti-corruption town hall" where the topic prefix ("anti-")
    is immediately adjacent to the noun and needs a word boundary on the
    other side to match. If any such verb is found, that occurrence is
    topical, not accusatory — keep scanning. If no occurrence is
    accusatory, return False.
    """
    for m in _ATTACK_NOUN_RE.finditer(evidence_span):
        start = m.start()
        end = m.end()
        window = evidence_span[max(0, start - 40):end]
        if _ATTACK_NOUN_TOPIC_PREFIX_RE.search(window):
            continue  # topic context, skip this hit
        return True   # this occurrence is accusatory
    return False


def correct_label(
    evidence_span: str,
    llm_label: Optional[str],
) -> tuple[Optional[str], str]:
    """Apply deterministic label correction to a claim record.

    Returns (corrected_label, rule_name). The rule_name tells the caller
    which rule fired (for audit logging). When no change is made, returns
    (llm_label, "kept_llm_label").

    The rules are tuned to the v15.0 prompt's label vocabulary:
    statement, attack, defense, endorsement, policy_position, vote,
    announcement, commitment. Labels outside that set are returned
    unchanged (caller logs an anomaly).
    """
    if not evidence_span:
        return (llm_label, "kept_llm_label")

    # Layer 1 — positive rules (override LLM's choice including null).
    # Rules with pattern=None are callable rules dispatched via name.
    for pat, target_label, rule_name in POSITIVE_RULES:
        if pat is None:
            # Callable rule — dispatch on name
            fired = False
            if rule_name == "attack_noun":
                fired = _attack_noun_in_attacking_position(evidence_span)
            if not fired:
                continue
        elif not pat.search(evidence_span):
            continue
        # Rule fired
        if target_label != llm_label:
            return (target_label, f"positive:{rule_name}")
        return (target_label, f"confirmed:{rule_name}")

    # Layer 2 — sanity check (downgrade label → null when triggers absent)
    if llm_label and llm_label in SANITY_PATTERNS:
        if not SANITY_PATTERNS[llm_label].search(evidence_span):
            return (None, f"sanity_downgrade_from:{llm_label}")

    # No change
    return (llm_label, "kept_llm_label")
