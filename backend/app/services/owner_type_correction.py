"""
Heuristic to catch the LLM's candidate↔opponent owner_type inversion.

The bug pattern
---------------
The campaign_analysis SYSTEM_PROMPT explicitly tells the LLM that
owner_type identifies which SIDE BENEFITS — so an attack on Bresnahan
should be owner=candidate (it helps Cognetti) and an attack on Cognetti
should be owner=opponent.

The LLM still inverts ~periodically. The failure mode: it sees "this
frame mentions Bresnahan" and matches "Bresnahan IS the opponent" →
owner=opponent. The "who benefits" rule gets overridden by the simpler
name-association heuristic.

Example caught in production (frame id 107):
  name:        "Bresnahan cuts public broadcasting funding"
  LLM said:    owner_type=opponent
  Correct:     owner_type=candidate (attack on Bresnahan helps Cognetti)

What this module does
---------------------
A conservative pattern-matcher that flips owner_type ONLY when the
frame name contains an unambiguous attack pattern against a named
subject. False positives are worse than false negatives — if we flip
incorrectly we create a wrong frame; if we miss a real inversion the
worst case is the user sees the same bug they're seeing today.

What this does NOT do
---------------------
- Doesn't look at evidence_quote for attack patterns. Quotes often
  reproduce the opponent's hostile framing of our candidate, which would
  confuse the heuristic. Frame name only.
- Doesn't call an LLM. Heuristic is free, deterministic, testable.
- Doesn't claim to catch every inversion. Only the clear cases.
"""
from __future__ import annotations
import re
from typing import Optional


# Attack VERBS where the named subject is doing the negative action.
# Example: "Bresnahan cuts ..." — Bresnahan is the agent of "cuts".
_ATTACK_VERBS = {
    # Direct negative actions
    "cuts", "cut", "cutting",
    "broke", "breaks", "breaking", "broken",
    "betrays", "betrayed", "betraying", "betrayal",
    "harms", "harmed", "harmful",
    "fails", "failed", "failing", "failure",
    "denies", "denied", "denying",
    "evades", "evaded", "evading", "evasion", "evasions",
    "lies", "lied", "lying",
    "undermines", "undermined", "undermining",
    "guts", "gutting", "gutted",
    "strips", "stripped", "stripping",
    "repeals", "repealed", "repealing",
    "eliminates", "eliminated", "eliminating",
    "reneges", "reneged", "reneging",
    "neglects", "neglected", "neglecting", "neglect",
    "ignores", "ignored", "ignoring",
    # Hyphenated style
    "bashes", "bashed", "trashes", "trashed",
}

# Attack NOUNS appearing in possessive context: "{name}'s {noun}" or
# "{name} {noun}". These directly characterize the subject negatively.
_ATTACK_NOUNS = {
    "scandal", "scandals", "controversy", "controversies",
    "corruption", "corrupt",
    "inconsistency", "inconsistencies",
    "hypocrisy", "hypocrisies", "hypocrite",
    "crisis", "crises",
    "misconduct", "wrongdoing",
    "negligence", "incompetence",
    "shady",
    "betrayal", "betrayals",
    # Specific to broken-promise framing
    "promise", "promises",  # only when paired with "broken"
}

# Phrases that signal voting against good or voting for bad. Multi-word so
# they must be matched as phrases, not bag-of-words.
_ATTACK_PHRASES = (
    "voted against",
    "voted to cut",
    "voted to gut",
    "voted to strip",
    "voted to eliminate",
    "voted to repeal",
    "voted to deny",
    "voted to dismantle",
    "voted to defund",
    "broken promise",
    "broken promises",
    "broke a promise",
    "broke his promise",
    "broke her promise",
    "broke their promise",
    "broke campaign promise",
    "broken campaign promise",
    "supported controversial",
    "backed controversial",
    "endorsed controversial",
)

def _last_name(full_name: str) -> str:
    """Best-effort surname extraction. Handles 'Last, First' (FEC) format
    and 'First Last' (human) format. Returns lowercased token, empty if
    no usable name."""
    if not full_name:
        return ""
    s = full_name.strip()
    if "," in s:
        return s.split(",", 1)[0].strip().lower()
    parts = s.split()
    return parts[-1].lower() if parts else ""


# Regex for "anti-X" / "anti X" / "against X" — these reverse polarity, so
# the X noun shouldn't be counted as an attack signal even though X is in
# the attack-nouns list. Example: "Cognetti's Anti-Corruption" — "corruption"
# is in the noun list but "anti-" makes it pro-Cognetti.
_POLARITY_REVERSERS = re.compile(r"\b(anti[\s\-]|against\s+|fights?\s+|opposes?\s+|"
                                  r"battling\s+|combat(?:ing|s)?\s+)\w*", re.IGNORECASE)


def frame_attacks_subject(text: str, subject_name: str) -> bool:
    """Return True if `text` appears to be an attack on `subject_name`.

    Three patterns trigger this — all require the SUBJECT NAME to be
    structurally connected to the attack signal (not just nearby):

      A. "[Name] [attack verb]"   — subject is the AGENT of negative action
         e.g. "Bresnahan cuts funding", "Cognetti broke promise"
      B. "[Name]'s [attack noun]" — possessive characterization (allow up
         to 3 intermediate words for cases like "Bresnahan's ICE Funding
         Controversy")
         Polarity reversers ("anti-X", "against X", "fights X") suppress
         the noun match — they invert meaning.
      C. "[Name] [attack phrase]" — multi-word direct attack
         e.g. "Bresnahan voted to eliminate", "Cognetti supported controversial"

    Conservative by design — false positives invent fake attack frames
    and confuse the user; false negatives just preserve the LLM's call.
    """
    if not text or not subject_name:
        return False
    text_lower = text.lower()
    last = _last_name(subject_name)
    if not last or len(last) < 3:
        return False

    name_re = re.escape(last)

    # Pattern A — "Name verb" (subject is the agent of negative action).
    # Verbs are matched as alternation, not from a Python set, so we get
    # proper regex word boundaries.
    verb_alt = "|".join(re.escape(v) for v in _ATTACK_VERBS)
    pat_a = re.compile(rf"\b{name_re}\s+(?:{verb_alt})\b")
    if pat_a.search(text_lower):
        return True

    # Pattern C — "Name [attack phrase]" — multi-word direct attack.
    # Done BEFORE pattern B because patterns like "Bresnahan voted to
    # cut Medicaid" should match the phrase, not the verb pattern.
    for phrase in _ATTACK_PHRASES:
        pat_c = re.compile(rf"\b{name_re}\s+{re.escape(phrase)}\b")
        if pat_c.search(text_lower):
            return True

    # Pattern B — "Name's [up to 3 words] [attack noun]" (possessive).
    # Skip the match if a polarity reverser appears between the name and
    # the noun ("Cognetti's anti-corruption").
    noun_alt = "|".join(re.escape(n) for n in _ATTACK_NOUNS)
    pat_b = re.compile(rf"\b{name_re}'s\s+((?:\w+\s+){{0,3}})(?:{noun_alt})\b")
    for m in pat_b.finditer(text_lower):
        intermediate = m.group(1) or ""
        if _POLARITY_REVERSERS.search(intermediate):
            continue  # "Cognetti's anti-corruption" — reverser cancels the noun match
        # Also check the noun isn't itself the start of a polarity reverser
        # ("anti-corruption" as one word)
        match_text = m.group(0)
        if re.search(r"\banti[\s\-]", match_text):
            continue
        return True

    return False


def correct_owner_inversion(
    suggested_name: str,
    proposed_owner_type: str,
    candidate_name: str,
    opponent_names: list[str],
) -> tuple[str, Optional[str]]:
    """Return (corrected_owner_type, reason_or_None).

    `reason` is non-None when the heuristic flipped the input — caller
    should log it so corrections are observable in production. None
    when the input passed unchanged (heuristic stayed silent).

    The flip rules
    ~~~~~~~~~~~~~~
    A. Frame name attacks the OPPONENT and the input said owner=opponent
       → flip to owner=candidate
       (because an attack on opponent benefits the candidate side)

    B. Frame name attacks the CANDIDATE and the input said owner=candidate
       → flip to owner=opponent
       (because an attack on candidate benefits the opponent side)

    Conservative ambiguity rule
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~
    If frame attacks BOTH sides (rare — usually a comparative piece) or
    NEITHER side, don't touch. Better to defer to the LLM than guess.
    """
    if proposed_owner_type not in ("candidate", "opponent"):
        # 'media' or unknown — heuristic doesn't apply
        return proposed_owner_type, None

    attacks_candidate = (
        frame_attacks_subject(suggested_name, candidate_name)
        if candidate_name else False
    )
    attacks_any_opponent = any(
        frame_attacks_subject(suggested_name, opp) for opp in opponent_names if opp
    )

    # Both — don't touch (ambiguous)
    if attacks_candidate and attacks_any_opponent:
        return proposed_owner_type, None
    # Neither — don't touch (frame may name nobody, may be theme-level)
    if not attacks_candidate and not attacks_any_opponent:
        return proposed_owner_type, None

    # Frame attacks opponent → should be owner=candidate
    if attacks_any_opponent and proposed_owner_type == "opponent":
        return "candidate", (
            f"frame name attacks opponent — benefits candidate side "
            f"(was owner=opponent, corrected to candidate)"
        )
    # Frame attacks candidate → should be owner=opponent
    if attacks_candidate and proposed_owner_type == "candidate":
        return "opponent", (
            f"frame name attacks candidate — benefits opponent side "
            f"(was owner=candidate, corrected to opponent)"
        )

    # Heuristic agrees with the LLM — no change needed.
    return proposed_owner_type, None
