"""
Tests for app.services.strategic_lens.

These tests pin every (momentum_signal, owner_type) → output combination
explicitly. Any change to the strategic matrix needs to update the
expected values here AND have a one-sentence justification in the test
docstring — that forces deliberate review of campaign-strategy changes.

These tests are pure-function (no DB, no LLM, no I/O) so they run fast
and can never flake.
"""
from app.services.strategic_lens import (
    strategic_lens,
    supported_signals,
    supported_owner_types,
)


# ─────────────────────────────────────────────────────────────────────────
# Coverage sanity — make sure the matrix has every signal × every owner.
# If frame_momentum.py adds a new signal value or someone introduces a new
# owner_type, this test will catch it immediately.
# ─────────────────────────────────────────────────────────────────────────

def test_matrix_covers_every_signal_owner_combination():
    """The matrix should have an entry for every (signal, owner) pair so
    no combination silently falls through to None at production."""
    expected_signals = {
        "viral", "amplified", "missing_coverage", "elite_only",
        "stable", "no_trend_signal",
    }
    expected_owners = {"candidate", "opponent", "media"}

    assert supported_signals() == expected_signals, (
        f"Matrix missing signal types: {expected_signals - supported_signals()}; "
        f"matrix has unknown signals: {supported_signals() - expected_signals}"
    )
    assert supported_owner_types() == expected_owners

    # Every cross-product should yield a non-None lens.
    for sig in expected_signals:
        for owner in expected_owners:
            assert strategic_lens(sig, owner) is not None, \
                f"Matrix missing entry for ({sig}, {owner})"


# ─────────────────────────────────────────────────────────────────────────
# None-input handling — protective behavior for missing data.
# ─────────────────────────────────────────────────────────────────────────

def test_returns_none_when_signal_missing():
    """Frames below MIN_ACTIVE_ARTICLES have momentum_signal=None.
    These should never get a strategic recommendation — there's no signal."""
    assert strategic_lens(None, "candidate") is None
    assert strategic_lens(None, "opponent") is None
    assert strategic_lens(None, "media") is None


def test_returns_none_when_owner_type_missing():
    """A frame without owner_type is malformed but shouldn't crash the API."""
    assert strategic_lens("viral", None) is None
    assert strategic_lens("missing_coverage", "") is None


def test_returns_none_for_unknown_signal():
    """Future-compat: if frame_momentum.py adds a new signal we haven't
    taught the matrix, the UI should fail gracefully (skip the chip)
    rather than crash or guess."""
    assert strategic_lens("some_future_signal", "candidate") is None


# ─────────────────────────────────────────────────────────────────────────
# VIRAL signal — both article volume AND search interest spiking.
# ─────────────────────────────────────────────────────────────────────────

def test_viral_candidate_is_amplify():
    """When our own narrative is both pressed and voter-searched, we should
    amplify — both supply and demand are aligned. Highest urgency: the
    window for compounding is short."""
    lens = strategic_lens("viral", "candidate")
    assert lens["posture"] == "amplify"
    assert lens["urgency"] == "high"
    assert lens["action"] is not None
    assert "push" in lens["action"].lower() or "amplif" in lens["action"].lower()


def test_viral_opponent_is_defensive():
    """An opposition attack that's BOTH in press AND drawing voter searches
    is an active landing strike. Silence implies acceptance — needs response."""
    lens = strategic_lens("viral", "opponent")
    assert lens["posture"] == "defensive"
    assert lens["urgency"] == "high"
    assert lens["action"] is not None
    assert "respon" in lens["action"].lower() or "landing" in lens["action"].lower()


def test_viral_media_is_monitor():
    """Neutral framing going viral could help either side. Without lens we
    can only watch and reassess."""
    lens = strategic_lens("viral", "media")
    assert lens["posture"] == "monitor"
    assert lens["urgency"] == "medium"


# ─────────────────────────────────────────────────────────────────────────
# AMPLIFIED — outlets spike, voter search flat. Wire / PR pickup landing
# across the press but voters haven't engaged yet. Distinct from viral
# (which has voter alignment) and from elite_only (narrow outlets, many
# angles). The classic case is a press release republished by 10 outlets.
# ─────────────────────────────────────────────────────────────────────────

def test_amplified_candidate_is_offensive():
    """Our message is broadcasting across outlets but voters aren't searching.
    Don't waste the press cycle — push voter-facing content to convert
    press exposure into voter attention while the wire is hot."""
    lens = strategic_lens("amplified", "candidate")
    assert lens["posture"] == "offensive"
    assert lens["urgency"] == "medium"
    assert lens["action"] is not None
    assert any(w in lens["action"].lower() for w in ["push", "capitaliz", "content", "cycle"])


def test_amplified_opponent_is_defensive():
    """Opposition wire/PR is broadcasting broadly but voters haven't noticed yet.
    Pre-empt with counter-messaging before voter attention catches up to the
    press surface area. Medium urgency — broader than elite_only, narrower
    than viral (no voter signal yet)."""
    lens = strategic_lens("amplified", "opponent")
    assert lens["posture"] == "defensive"
    assert lens["urgency"] == "medium"
    assert lens["action"] is not None
    assert any(w in lens["action"].lower() for w in ["pre-empt", "counter", "before"])


def test_amplified_media_is_monitor():
    """Neutral framing being broadly amplified. No owner lens, no clear action."""
    lens = strategic_lens("amplified", "media")
    assert lens["posture"] == "monitor"
    assert lens["urgency"] == "low"


# ─────────────────────────────────────────────────────────────────────────
# MISSING_COVERAGE — voter search demand without article supply.
# This is the most actionable signal for asymmetric content opportunity.
# ─────────────────────────────────────────────────────────────────────────

def test_missing_coverage_candidate_is_offensive():
    """Voters are searching for our angle but we're not publishing — gap WE
    control. Pure content-side fix. Offensive opportunity."""
    lens = strategic_lens("missing_coverage", "candidate")
    assert lens["posture"] == "offensive"
    assert lens["urgency"] == "medium"
    assert lens["action"] is not None
    # Should suggest content/publishing, not response
    assert any(w in lens["action"].lower() for w in ["content", "publish", "ramp", "demand"])


def test_missing_coverage_opponent_is_defensive():
    """Voters are searching for opposition's angle but opposition hasn't
    capitalized yet. Pre-empt them: shape the framing before they figure
    out the opening."""
    lens = strategic_lens("missing_coverage", "opponent")
    assert lens["posture"] == "defensive"
    assert lens["urgency"] == "medium"
    assert lens["action"] is not None
    # Should suggest pre-emptive counter-narrative, not active response
    assert any(w in lens["action"].lower() for w in ["counter", "pre-empt", "prepare", "before"])


def test_missing_coverage_media_is_monitor():
    """Voter demand for neutral framing without press coverage. Useful to
    know but not directly actionable without an owner lens."""
    lens = strategic_lens("missing_coverage", "media")
    assert lens["posture"] == "monitor"
    assert lens["urgency"] == "low"


# ─────────────────────────────────────────────────────────────────────────
# ELITE_ONLY — press covering but voters not searching.
# Key subtle case: responding to an elite_only opponent attack RISKS
# AMPLIFYING it. Default is don't-engage.
# ─────────────────────────────────────────────────────────────────────────

def test_elite_only_candidate_is_offensive_reframe():
    """Press is covering our message but voters aren't searching. The story
    isn't translating to voter attention. Don't push harder in the same
    framing — reframe for voter relevance (kitchen-table, local)."""
    lens = strategic_lens("elite_only", "candidate")
    assert lens["posture"] == "offensive"
    assert lens["urgency"] == "low"
    assert lens["action"] is not None
    assert "reframe" in lens["action"].lower() or "voter" in lens["action"].lower()


def test_elite_only_opponent_is_monitor_NOT_defensive():
    """Critical anti-pattern check: when press covers an opposition attack
    but voters aren't searching, responding to the attack RISKS amplifying
    the story to voters who hadn't noticed it. The right posture is monitor,
    not defensive. (This is the rule most likely to feel wrong intuitively.)"""
    lens = strategic_lens("elite_only", "opponent")
    assert lens["posture"] == "monitor", (
        "elite_only + opponent should be MONITOR not defensive. "
        "Responding amplifies — see _MATRIX comment."
    )
    assert lens["urgency"] == "low"
    assert lens["action"] is not None
    assert "amplif" in lens["action"].lower() or "engage" in lens["action"].lower()


def test_elite_only_media_is_ignore():
    """Inside-baseball / beltway story with no voter resonance — lowest
    priority. No action recommended."""
    lens = strategic_lens("elite_only", "media")
    assert lens["posture"] == "ignore"
    assert lens["action"] is None
    assert lens["urgency"] == "low"


# ─────────────────────────────────────────────────────────────────────────
# STABLE — neither metric is spiking. Status quo.
# ─────────────────────────────────────────────────────────────────────────

def test_stable_any_owner_is_monitor_or_ignore():
    """Stable means no spike on either dimension. No action recommended
    for any owner type."""
    for owner in ("candidate", "opponent", "media"):
        lens = strategic_lens("stable", owner)
        assert lens is not None, f"stable + {owner} missing from matrix"
        assert lens["posture"] in ("monitor", "ignore")
        assert lens["urgency"] == "low"
        assert lens["action"] is None


# ─────────────────────────────────────────────────────────────────────────
# NO_TREND_SIGNAL — couldn't classify, no matching trend terms.
# ─────────────────────────────────────────────────────────────────────────

def test_no_trend_signal_suggests_adding_trend_terms():
    """For candidate/opponent owner types, the actionable thing isn't
    strategy — it's data hygiene. Suggest expanding the trend term list
    so this frame can be classified next run."""
    for owner in ("candidate", "opponent"):
        lens = strategic_lens("no_trend_signal", owner)
        assert lens is not None
        assert lens["posture"] == "monitor"
        assert lens["action"] is not None
        assert "trend" in lens["action"].lower()


def test_no_trend_signal_media_is_ignore():
    """Media frames with no trend signal are lowest priority — no actor to
    defend or attack, no signal to classify."""
    lens = strategic_lens("no_trend_signal", "media")
    assert lens["posture"] == "ignore"
    assert lens["action"] is None


# ─────────────────────────────────────────────────────────────────────────
# Property tests — invariants that should hold for every entry.
# ─────────────────────────────────────────────────────────────────────────

def test_all_postures_are_in_known_vocabulary():
    """The 'posture' field has a small fixed vocabulary. Any new value
    needs UI styling, so the matrix shouldn't invent new ones silently."""
    allowed = {"amplify", "offensive", "defensive", "monitor", "ignore"}
    for sig in supported_signals():
        for owner in supported_owner_types():
            lens = strategic_lens(sig, owner)
            assert lens["posture"] in allowed, (
                f"Unknown posture {lens['posture']!r} at ({sig}, {owner}). "
                f"Allowed: {sorted(allowed)}"
            )


def test_all_urgencies_are_in_known_vocabulary():
    """Same for urgency."""
    allowed = {"high", "medium", "low"}
    for sig in supported_signals():
        for owner in supported_owner_types():
            lens = strategic_lens(sig, owner)
            assert lens["urgency"] in allowed


def test_ignore_posture_never_has_action():
    """If posture is 'ignore', there's nothing to do — action must be None.
    Otherwise the UI shows a contradictory 'don't act + here's the action'."""
    for sig in supported_signals():
        for owner in supported_owner_types():
            lens = strategic_lens(sig, owner)
            if lens["posture"] == "ignore":
                assert lens["action"] is None, (
                    f"({sig}, {owner}) has posture=ignore but action={lens['action']!r}"
                )


def test_high_urgency_only_for_viral_signals():
    """high urgency is reserved for active situations (viral). Anything
    else is medium or low — accidental 'high' urgency would burn out the user."""
    for sig in supported_signals():
        for owner in supported_owner_types():
            lens = strategic_lens(sig, owner)
            if lens["urgency"] == "high":
                assert sig == "viral", (
                    f"({sig}, {owner}) is urgency=high but signal isn't viral"
                )


def test_action_is_short_one_sentence():
    """UI surfaces action in a tooltip. Keep it short."""
    MAX_CHARS = 90
    for sig in supported_signals():
        for owner in supported_owner_types():
            lens = strategic_lens(sig, owner)
            if lens["action"] is not None:
                assert len(lens["action"]) <= MAX_CHARS, (
                    f"({sig}, {owner}) action is {len(lens['action'])} chars, "
                    f"limit is {MAX_CHARS}: {lens['action']!r}"
                )
