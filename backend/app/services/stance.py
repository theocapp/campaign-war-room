"""Stance decomposition — dimensional view of relation semantics.

Background: our 9 predicates aren't orthogonal. `endorses` collapses
procedural support (signing/voting/joining) with rhetorical support
(public praise) with ideological alignment. `co_sponsored` is purely
procedural. `attacks` is rhetorical + ideological but not procedural.
A binary "support vs oppose" classification produces false contradictions
when a legislator procedurally supports a bill (discharge petition) AND
rhetorically criticizes it (objects to framing).

This module exposes three orthogonal stance dimensions per predicate:

  procedural   — what the actor DID in formal/procedural terms.
                 (advance | oppose | neutral)
  rhetorical   — what the actor SAID about the target publicly.
                 (supportive | neutral | critical | hostile)
  ideological  — coarse alignment between actor's general position and
                 the target's general position.
                 (aligned | mixed | opposed | unknown)

  intensity    — 0..1 confidence/strength of the categorical assignment.

These are DERIVED from the predicate (and optionally the article context),
not stored on the row. The contradiction detector now asks "do these
stance vectors disagree on the same dimension?" instead of "does one say
support and the other say oppose?"
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Procedural = Literal["advance", "oppose", "neutral"]
Rhetorical = Literal["supportive", "neutral", "critical", "hostile"]
Ideological = Literal["aligned", "mixed", "opposed", "unknown"]


@dataclass(frozen=True)
class StanceVector:
    procedural: Procedural
    rhetorical: Rhetorical
    ideological: Ideological
    intensity: float  # 0..1

    def to_dict(self) -> dict:
        return {
            "procedural": self.procedural,
            "rhetorical": self.rhetorical,
            "ideological": self.ideological,
            "intensity": self.intensity,
        }


# Default stance vector per predicate. Derived once and cached — this is
# the predicate → stance "decoder" called per relation at API time.
#
# Notes:
#  - `voted_for` defaults to ideological=aligned because a yes vote typically
#    implies the legislator aligns with the bill's intent. Could be a
#    cross-party crossover vote with low alignment, but at population level
#    the default is reasonable.
#  - `co_sponsored` is procedural=advance + ideological=mixed because the
#    classic case (signing a discharge petition) is procedural support
#    without ideological endorsement. v14.3+ partisan guard reclassifies
#    cross-party `endorses` into `co_sponsored` exactly for this reason.
#  - `represents`, `member_of`, `predecessor_of` are structural rather than
#    stance — all dimensions default to neutral / unknown.
PREDICATE_STANCE: dict[str, StanceVector] = {
    # Strong-signal predicates: explicit endorsement (`endorses`) and hostile
    # attack (`attacks`) carry both rhetorical AND ideological weight.
    "endorses":       StanceVector("advance",  "supportive", "aligned", 0.95),
    "attacks":        StanceVector("neutral",  "hostile",    "opposed", 0.95),

    # Criticism is rhetorical only. The legislator may still ideologically
    # align in other contexts (criticize a specific provision while
    # supporting the bill's general direction). Use ideological=mixed.
    "criticizes":     StanceVector("neutral",  "critical",   "mixed",   0.70),

    # Votes are procedural actions. The vote ITSELF doesn't determine
    # ideological alignment — a Republican voting yes on a Democratic bill
    # is a cross-party crossover, not an ideological signal. Default
    # ideological=mixed; the partisan-guard layer is what catches genuine
    # cross-party patterns.
    "voted_for":      StanceVector("advance",  "neutral",    "mixed",   0.85),
    "voted_against":  StanceVector("oppose",   "neutral",    "mixed",   0.85),
    "co_sponsored":   StanceVector("advance",  "neutral",    "mixed",   0.80),

    # Structural / temporal — no inherent stance.
    "represents":     StanceVector("neutral",  "neutral",    "unknown", 0.50),
    "member_of":      StanceVector("neutral",  "neutral",    "unknown", 0.60),
    "predecessor_of": StanceVector("neutral",  "neutral",    "unknown", 0.50),

    # Event participation. Attending an event isn't a stance per se —
    # someone can attend a debate without endorsing the host's positions.
    # Weak ideological signal (you go to events that align with you);
    # neutral on procedural and rhetorical dimensions.
    "attended":       StanceVector("neutral",  "neutral",    "mixed",   0.55),
}


def stance_for(predicate: str) -> Optional[StanceVector]:
    """Return the stance vector for a predicate, or None if unknown."""
    return PREDICATE_STANCE.get(predicate)


# ── Conflict logic ────────────────────────────────────────────────────────
#
# Two stance vectors conflict if they disagree on the SAME dimension.
# Crucially, "neutral" never conflicts — it's the absence of a stance on
# that dimension, not an opposite stance. So `co_sponsored` (procedural=
# advance, rhetorical=neutral) and `criticizes` (procedural=neutral,
# rhetorical=critical) do NOT conflict: procedural is advance-vs-neutral
# (no conflict — one side has no procedural stance) and rhetorical is
# neutral-vs-critical (same — no conflict). The legislator supported
# procedurally without disagreeing rhetorically; the rhetorical criticism
# is on a separate axis the procedural action didn't touch.

# Pairs that DO constitute a same-dimension conflict.
_PROCEDURAL_CONFLICT: set[frozenset[str]] = {
    frozenset({"advance", "oppose"}),
}
_RHETORICAL_CONFLICT: set[frozenset[str]] = {
    frozenset({"supportive", "critical"}),
    frozenset({"supportive", "hostile"}),
}
_IDEOLOGICAL_CONFLICT: set[frozenset[str]] = {
    frozenset({"aligned", "opposed"}),
}


def vectors_conflict(a: StanceVector, b: StanceVector) -> tuple[bool, list[str]]:
    """Return (is_conflict, list_of_conflicting_dimensions).

    Two vectors conflict only when they disagree on the SAME dimension —
    not just when one says "support" and the other says "oppose" across
    different dimensions.
    """
    conflicts: list[str] = []
    if frozenset({a.procedural, b.procedural}) in _PROCEDURAL_CONFLICT:
        conflicts.append("procedural")
    if frozenset({a.rhetorical, b.rhetorical}) in _RHETORICAL_CONFLICT:
        conflicts.append("rhetorical")
    if frozenset({a.ideological, b.ideological}) in _IDEOLOGICAL_CONFLICT:
        conflicts.append("ideological")
    return (bool(conflicts), conflicts)


def predicates_conflict(p_a: str, p_b: str) -> tuple[bool, list[str]]:
    """Convenience wrapper for the common case of comparing two predicates."""
    a = stance_for(p_a)
    b = stance_for(p_b)
    if not a or not b:
        return (False, [])
    return vectors_conflict(a, b)


# ── Aggregation across multiple relations ─────────────────────────────────


def aggregate_stance(
    relations: list[tuple[str, int]],
) -> dict:
    """Given a list of (predicate, weight) pairs between the SAME subject
    and object, compute an aggregate stance across dimensions.

    For each dimension, the aggregate is the weighted-majority categorical
    value, with a separate `dimension_conflict` flag if there's significant
    weight on opposing values.
    """
    if not relations:
        return {
            "procedural": "neutral", "rhetorical": "neutral",
            "ideological": "unknown", "intensity": 0.0,
            "dimension_conflicts": [],
        }

    proc_counts: dict[str, int] = {}
    rhet_counts: dict[str, int] = {}
    ideo_counts: dict[str, int] = {}
    total_weight = 0
    total_intensity_weight = 0.0
    for pred, weight in relations:
        sv = stance_for(pred)
        if not sv:
            continue
        proc_counts[sv.procedural] = proc_counts.get(sv.procedural, 0) + weight
        rhet_counts[sv.rhetorical] = rhet_counts.get(sv.rhetorical, 0) + weight
        ideo_counts[sv.ideological] = ideo_counts.get(sv.ideological, 0) + weight
        total_weight += weight
        total_intensity_weight += sv.intensity * weight

    def majority(counts: dict[str, int], default: str) -> str:
        if not counts:
            return default
        return max(counts.items(), key=lambda kv: kv[1])[0]

    # Detect dimension conflicts: opposing values both with significant weight
    # ("significant" = at least 25% of the total).
    THRESHOLD = 0.25
    dim_conflicts: list[str] = []
    if total_weight > 0:
        if (proc_counts.get("advance", 0) / total_weight >= THRESHOLD and
                proc_counts.get("oppose", 0) / total_weight >= THRESHOLD):
            dim_conflicts.append("procedural")
        rhet_support = rhet_counts.get("supportive", 0) / total_weight
        rhet_against = (rhet_counts.get("critical", 0) + rhet_counts.get("hostile", 0)) / total_weight
        if rhet_support >= THRESHOLD and rhet_against >= THRESHOLD:
            dim_conflicts.append("rhetorical")
        if (ideo_counts.get("aligned", 0) / total_weight >= THRESHOLD and
                ideo_counts.get("opposed", 0) / total_weight >= THRESHOLD):
            dim_conflicts.append("ideological")

    return {
        "procedural": majority(proc_counts, "neutral"),
        "rhetorical": majority(rhet_counts, "neutral"),
        "ideological": majority(ideo_counts, "unknown"),
        "intensity": round(total_intensity_weight / max(total_weight, 1), 2),
        "dimension_conflicts": dim_conflicts,
    }
