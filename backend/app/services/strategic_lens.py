"""
Strategic posture derivation from (momentum_signal, owner_type).

The momentum classifier (services/frame_momentum.py) tells us WHAT a frame
is doing: viral, missing_coverage, elite_only, etc. This module takes that
and the frame's owner_type to derive WHAT TO DO ABOUT IT.

Why separate from frame_momentum.py: the classifier is content-agnostic
(pure article × trend math). This module encodes campaign-strategy
interpretation, which is judgment, not math. Keeping it isolated means:
  - The interpretation rules are reviewable as a single small matrix
  - Tests can pin every (signal, owner) combination explicitly
  - Future races can override the matrix (e.g. for advocacy orgs the
    "offensive/defensive" framing doesn't apply the same way) without
    touching the classifier

The interpretation rules
------------------------
There are 6 momentum signals and 3 owner types = 18 combinations. Each maps
to a (posture, action, urgency) triple. See `_MATRIX` below for the full
table with reasoning.

Signal meanings (mirror frame_momentum.py):
  viral            — outlets spike AND voters search. Real momentum.
  amplified        — outlets spike but voters quiet. Wire/PR pickup.
  elite_only       — many angles from few outlets. Beat-reporter obsession.
  missing_coverage — voter search spike, press flat. Unmet demand.
  stable           — no spikes.
  no_trend_signal  — couldn't classify, no matching trend terms.

Field meanings
~~~~~~~~~~~~~~
- posture:   high-level strategic stance — what this is for the campaign
- action:    one-sentence "what to do" recommendation; null when monitoring-only
- urgency:   "high" | "medium" | "low" — how fast this needs attention

These are intentionally short. The UI surfaces them on hover; tooltip length
matters more than precision here.
"""
from __future__ import annotations
from typing import Optional, TypedDict


class StrategicLens(TypedDict):
    posture: str
    action: Optional[str]
    urgency: str


# ──────────────────────────────────────────────────────────────────────────
# The matrix. Each entry is (posture, action, urgency).
#
# Posture vocabulary (intentionally small — five concepts cover everything):
#   "amplify"    — push it harder; both press and voters are receptive
#   "offensive"  — there's a content opportunity we're not filling
#   "defensive"  — opposition narrative; needs response or pre-emption
#   "monitor"    — watch but don't engage; engagement risks amplifying
#   "ignore"     — insufficient data or stable; not worth attention
#
# Reasoning is in the comment beside each entry. If you disagree with any
# of these, this is the file to argue with. The MATRIX is the contract.
# ──────────────────────────────────────────────────────────────────────────
_MATRIX: dict[tuple[str, str], StrategicLens] = {

    # ── VIRAL (article volume AND search interest both spiking) ────────
    ("viral", "candidate"): {
        # Pro-candidate narrative is firing in press AND voters are searching for it.
        # This is the rare moment when amplification compounds — both supply (press)
        # and demand (voter attention) are aligned.
        "posture": "amplify",
        "action": "Push this further — press and voters are aligned",
        "urgency": "high",
    },
    ("viral", "opponent"): {
        # Opposition narrative is landing in BOTH press and voter searches.
        # This is the classic "active attack" — needs immediate response, not just
        # monitoring. Silence here implies acceptance.
        "posture": "defensive",
        "action": "Active response needed — opposition attack is landing",
        "urgency": "high",
    },
    ("viral", "media"): {
        # Neutral framing (neither side's owned narrative) heating up. Without
        # owner_type signal we can't say defend or offend; the action is to figure
        # out which side this NET helps and reclassify.
        "posture": "monitor",
        "action": "Heat is real but neutral — assess which side benefits",
        "urgency": "medium",
    },

    # ── AMPLIFIED (outlets spike, voter search flat) ──────────────────
    # Broad press pickup — wire syndication, press release amplification, or
    # multiple independent outlets picking up the same news. Voters aren't
    # searching for it yet. This is "supply spiking, demand unaligned."
    # Different from viral (which has voter alignment) and from elite_only
    # (which has narrow outlets writing many angles).
    ("amplified", "candidate"): {
        # Our story is broadcasting across outlets but voters haven't engaged.
        # Don't waste the cycle — push complementary content to convert press
        # exposure into voter attention while the wire is hot.
        "posture": "offensive",
        "action": "Capitalize on press pickup — push voter-facing content while the cycle is hot",
        "urgency": "medium",
    },
    ("amplified", "opponent"): {
        # Opposition wire / press-release is broadcasting across outlets.
        # Voters haven't noticed yet, but the press surface area is real.
        # Pre-empt with counter-message before voter attention catches up.
        "posture": "defensive",
        "action": "Pre-empt with counter-message — opposition pickup is broad, voter attention next",
        "urgency": "medium",
    },
    ("amplified", "media"): {
        # Neutral framing being amplified. No owner lens to act through.
        "posture": "monitor",
        "action": "Broad press pickup of neutral framing — watch for owner-side angles",
        "urgency": "low",
    },

    # ── MISSING_COVERAGE (search demand high, article volume flat) ─────
    ("missing_coverage", "candidate"): {
        # Voters are searching for something connected to our angle, but the
        # campaign's not publishing on it. This is a content gap WE control — fill it.
        "posture": "offensive",
        "action": "Ramp up content — voter demand unmet on your narrative",
        "urgency": "medium",
    },
    ("missing_coverage", "opponent"): {
        # Voters are searching for an attack-angle theme, but the opposition hasn't
        # built the narrative around it yet. Pre-empt them: shape the framing
        # before they figure out the opportunity.
        "posture": "defensive",
        "action": "Prepare counter-narrative before opposition capitalizes",
        "urgency": "medium",
    },
    ("missing_coverage", "media"): {
        # Voter demand without press coverage and no clear side it favors.
        # Likely a niche topic; useful to know but rarely actionable.
        "posture": "monitor",
        "action": "Content gap exists but lens is unclear",
        "urgency": "low",
    },

    # ── ELITE_ONLY (articles spiking, voter search flat) ───────────────
    ("elite_only", "candidate"): {
        # Press is covering our message but voters aren't searching for it.
        # The story isn't translating to voter attention. The fix is to reframe
        # the content for voter-relevant terms (kitchen-table, local).
        "posture": "offensive",
        "action": "Reframe for voter relevance — message isn't landing",
        "urgency": "low",
    },
    ("elite_only", "opponent"): {
        # Press is running attack stories but voters aren't paying attention.
        # Responding RISKS AMPLIFYING — turning a journalist-only story into a
        # voter-aware one. Default is "don't engage" unless your team has reason
        # to think the story is about to break out.
        "posture": "monitor",
        "action": "Don't engage — responding may amplify a story voters haven't noticed",
        "urgency": "low",
    },
    ("elite_only", "media"): {
        # Beltway / inside-baseball story with no voter resonance. Lowest priority.
        "posture": "ignore",
        "action": None,
        "urgency": "low",
    },

    # ── STABLE (neither article nor search velocity is spiking) ────────
    ("stable", "candidate"): {
        "posture": "monitor",
        "action": None,
        "urgency": "low",
    },
    ("stable", "opponent"): {
        "posture": "monitor",
        "action": None,
        "urgency": "low",
    },
    ("stable", "media"): {
        "posture": "ignore",
        "action": None,
        "urgency": "low",
    },

    # ── NO_TREND_SIGNAL (we couldn't classify — no matching trend terms) ─
    # Same posture regardless of owner: we don't have enough data to act.
    ("no_trend_signal", "candidate"): {
        "posture": "monitor",
        "action": "Add trend terms (issue keywords, frame name words) so this can be classified",
        "urgency": "low",
    },
    ("no_trend_signal", "opponent"): {
        "posture": "monitor",
        "action": "Add trend terms (issue keywords, frame name words) so this can be classified",
        "urgency": "low",
    },
    ("no_trend_signal", "media"): {
        "posture": "ignore",
        "action": None,
        "urgency": "low",
    },
}


def strategic_lens(
    momentum_signal: Optional[str],
    owner_type: Optional[str],
) -> Optional[StrategicLens]:
    """Return strategic interpretation for a (signal, owner) pair.

    Returns None when either input is missing OR the combination isn't in
    the matrix. The UI should treat None as "no recommendation, render nothing."

    None-case explicitly catches:
      - Frame is below MIN_ACTIVE_ARTICLES (momentum_signal is None)
      - Frame just got created and momentum hasn't run yet
      - A future signal value we haven't taught the matrix yet

    Forward-compatibility: adding a new signal to frame_momentum.py without
    updating this matrix is harmless — the UI just skips the chip.
    """
    if not momentum_signal or not owner_type:
        return None
    return _MATRIX.get((momentum_signal, owner_type))


def supported_signals() -> set[str]:
    """For tests + tooling: every signal value the matrix understands."""
    return {sig for sig, _owner in _MATRIX}


def supported_owner_types() -> set[str]:
    """For tests + tooling: every owner_type the matrix understands."""
    return {owner for _sig, owner in _MATRIX}
