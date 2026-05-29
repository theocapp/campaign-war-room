"""
Tests for owner_type_correction.correct_owner_inversion().

The heuristic catches the LLM's most common owner_type mistake: tagging
an opponent-attack frame as owner=opponent because Bresnahan-the-named-
subject IS the opponent, even though the rule is "owner = who benefits".

These tests include all 4 confirmed mis-classifications from the live
DB on 2026-05-24, plus general patterns, plus negative tests (cases
where the heuristic should NOT flip).
"""
import pytest

from app.services.owner_type_correction import (
    correct_owner_inversion,
    frame_attacks_subject,
)


CANDIDATE = "Paige Cognetti"
OPPONENTS = ["Rob Bresnahan"]


# ─────────────────────────────────────────────────────────────────────────
# Confirmed real bugs from the DB — these are the actual frame names the
# user reported. They MUST get corrected.
# ─────────────────────────────────────────────────────────────────────────

def test_real_bug_1_bresnahan_cuts_public_broadcasting():
    """The exact frame the user pointed to in conversation."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan cuts public broadcasting funding",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"
    assert reason is not None
    assert "attacks opponent" in reason


def test_real_bug_2_bresnahan_ice_funding_controversy():
    """'X's [topic] Controversy' is a classic possessive-attack pattern."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan's ICE Funding Controversy",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"
    assert reason is not None


def test_real_bug_3_bresnahan_supported_controversial_legislation():
    """'supported controversial' is in the attack-phrases list."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan supported controversial legislation",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"


def test_real_bug_4_duplicate_cuts_public_broadcasting():
    """Frame name with same wording as bug 1 (the source row had 2 candidates)."""
    corrected, _ = correct_owner_inversion(
        suggested_name="Bresnahan cuts public broadcasting funding",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"


# ─────────────────────────────────────────────────────────────────────────
# Reverse direction — attacks on candidate should be tagged opponent.
# Not seen in live data but the heuristic must handle it symmetrically.
# ─────────────────────────────────────────────────────────────────────────

def test_attack_on_candidate_tagged_candidate_gets_flipped():
    """Symmetric case: 'Cognetti's scandal' attacks Cognetti → benefits Bresnahan."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Cognetti's ethics scandal",
        proposed_owner_type="candidate",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "opponent"
    assert reason is not None
    assert "attacks candidate" in reason


def test_attack_on_candidate_breaks_promise():
    """'Cognetti broke X' — owner should be opponent."""
    corrected, _ = correct_owner_inversion(
        suggested_name="Cognetti broke her transparency pledge",
        proposed_owner_type="candidate",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "opponent"


def test_attack_on_candidate_voted_against():
    """'Cognetti voted against' is a clear attack phrase on her."""
    corrected, _ = correct_owner_inversion(
        suggested_name="Cognetti voted against transparency",
        proposed_owner_type="candidate",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "opponent"


# ─────────────────────────────────────────────────────────────────────────
# Cases where the heuristic must NOT flip — these are correct already.
# ─────────────────────────────────────────────────────────────────────────

def test_pro_candidate_frame_not_flipped():
    """'Cognetti's Anti-Corruption' is genuinely a pro-Cognetti frame.
    No attack pattern on Cognetti — heuristic must stay silent."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Cognetti's Anti-Corruption",
        proposed_owner_type="candidate",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"
    assert reason is None


def test_pro_opponent_frame_not_flipped():
    """'Bresnahan Delivers District Funding' is genuinely pro-Bresnahan.
    No attack words. Heuristic must not flip."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan Delivers District Funding",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "opponent"
    assert reason is None


def test_neutral_frame_not_flipped():
    """'Healthcare Debate' has no actor — heuristic should stay silent."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Healthcare Debate",
        proposed_owner_type="media",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "media"
    assert reason is None


def test_pa_district_demographics_not_flipped():
    """Real media-tier frame — no attack pattern. No change."""
    corrected, reason = correct_owner_inversion(
        suggested_name="PA District Demographics Shift Further Left",
        proposed_owner_type="media",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "media"
    assert reason is None


# ─────────────────────────────────────────────────────────────────────────
# Edge cases / known-tricky cases that test heuristic robustness.
# ─────────────────────────────────────────────────────────────────────────

def test_dual_subjects_no_flip():
    """When both candidates appear (e.g., comparative coverage), heuristic
    is intentionally silent — too easy to be wrong."""
    corrected, reason = correct_owner_inversion(
        # Hypothetical: a frame that's about both attacking each other
        suggested_name="Cognetti slams Bresnahan as Bresnahan attacks Cognetti",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    # Both subjects attacked — ambiguous, stay silent
    assert corrected == "opponent"  # unchanged from input
    assert reason is None


def test_no_inversion_when_already_correct():
    """If LLM gets it right (attack on opponent → owner=candidate), don't
    re-flip it. The heuristic only flips obvious wrongs."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan cuts public broadcasting funding",
        proposed_owner_type="candidate",  # ← already correct
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"
    assert reason is None  # no change needed


def test_media_owner_type_never_flipped():
    """'media' input doesn't get touched by this heuristic — the existing
    _validate_owner_type handles the media downgrade case separately."""
    corrected, reason = correct_owner_inversion(
        suggested_name="Bresnahan cuts public broadcasting funding",
        proposed_owner_type="media",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "media"
    assert reason is None


def test_short_or_garbage_name_returns_input():
    """Empty / very short subject names should be handled gracefully."""
    # Empty candidate name
    corrected, _ = correct_owner_inversion(
        suggested_name="Bresnahan cuts funding",
        proposed_owner_type="opponent",
        candidate_name="",
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate"  # opponent attack still triggers

    # No opponents listed
    corrected, _ = correct_owner_inversion(
        suggested_name="Bresnahan cuts funding",
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=[],
    )
    assert corrected == "opponent"  # can't check attack-on-opponent, stay silent


def test_fec_format_last_first_handled():
    """Name like 'COGNETTI, PAIGE' should still work — FEC data sometimes
    uses this format in race_candidates."""
    corrected, _ = correct_owner_inversion(
        suggested_name="Cognetti's broken promise",
        proposed_owner_type="candidate",
        candidate_name="COGNETTI, PAIGE",  # FEC format
        opponent_names=["BRESNAHAN, ROB"],
    )
    assert corrected == "opponent"


# ─────────────────────────────────────────────────────────────────────────
# frame_attacks_subject() direct tests — the building block
# ─────────────────────────────────────────────────────────────────────────

def test_attack_subject_simple_cut_pattern():
    assert frame_attacks_subject("Bresnahan cuts Medicaid", "Rob Bresnahan") is True


def test_attack_subject_possessive_scandal():
    assert frame_attacks_subject("Bresnahan's stock scandal", "Rob Bresnahan") is True


def test_attack_subject_voted_against():
    assert frame_attacks_subject(
        "Bresnahan voted against healthcare protections", "Rob Bresnahan"
    ) is True


def test_attack_subject_no_attack_words():
    """Neutral mention should not register as an attack."""
    assert frame_attacks_subject(
        "Bresnahan represents PA-08", "Rob Bresnahan"
    ) is False


def test_attack_subject_no_name():
    assert frame_attacks_subject(
        "Healthcare Debate", "Rob Bresnahan"
    ) is False


def test_attack_subject_name_substring_doesnt_match():
    """'Bres' embedded in another word shouldn't trigger — word-boundary matching."""
    # 'breeze' contains 'bres' as substring — should NOT match Bresnahan
    assert frame_attacks_subject(
        "A pleasant breeze cuts through the valley", "Rob Bresnahan"
    ) is False


# ─────────────────────────────────────────────────────────────────────────
# Coverage: every common attack-keyword type should work
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frame_name", [
    "Bresnahan cuts funding",
    "Bresnahan broke his promise",
    "Bresnahan betrayed working families",
    "Bresnahan harms district",
    "Bresnahan voted to eliminate Medicaid",
    "Bresnahan voted against ACA",
    "Bresnahan's broken promises on Medicare",
    "Bresnahan's stock trading scandal",
    "Bresnahan's healthcare controversy",
    "Bresnahan supported controversial legislation",
    "Bresnahan ignores constituents",
])
def test_various_attack_patterns_all_flip(frame_name):
    """Every common attack pattern should flip from opponent → candidate."""
    corrected, _ = correct_owner_inversion(
        suggested_name=frame_name,
        proposed_owner_type="opponent",
        candidate_name=CANDIDATE,
        opponent_names=OPPONENTS,
    )
    assert corrected == "candidate", (
        f"Frame name {frame_name!r} should be detected as opponent-attack"
    )
