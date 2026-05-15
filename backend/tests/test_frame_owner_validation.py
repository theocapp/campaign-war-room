"""Phase 0 bugs 6 + 7: frame suggestion mislabels owner_type and descriptions.

Bug 6: auto-suggested frames with generic news got `owner_type="opponent"`
even though no opponent was named anywhere in the frame.

The fix runs every LLM-returned frame through `_validate_owner_type`,
which downgrades `candidate`- or `opponent`-typed frames to `media` when
the relevant person isn't actually named in the frame text.
"""
from app.services.narrative_frames import _validate_owner_type


def test_opponent_label_downgraded_when_no_opponent_named():
    """Generic news labeled as 'opponent' — should drop to 'media'."""
    result = _validate_owner_type(
        owner_type="opponent",
        frame_text="Local infrastructure repairs and pothole coverage in Scranton",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "media"


def test_opponent_label_kept_when_opponent_named():
    result = _validate_owner_type(
        owner_type="opponent",
        frame_text="Bresnahan attacks on healthcare votes by the incumbent",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "opponent"


def test_opponent_label_matches_either_name_token():
    # "Rob" alone is enough — many news stories use first names.
    result = _validate_owner_type(
        owner_type="opponent",
        frame_text="Rob's record on small business taxes",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "opponent"


def test_opponent_label_handles_last_comma_first_format():
    """Opponents seeded from FEC have names like 'BRESNAHAN, ROB' —
    name tokenisation must still find the surname in the frame text.
    """
    result = _validate_owner_type(
        owner_type="opponent",
        frame_text="Bresnahan attacks on healthcare votes",
        candidate="Paige Cognetti",
        opponents=["BRESNAHAN, ROB"],
    )
    assert result == "opponent"


def test_candidate_label_downgraded_when_no_candidate_named():
    result = _validate_owner_type(
        owner_type="candidate",
        frame_text="Local economy and small-business climate",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "media"


def test_candidate_label_kept_when_candidate_named():
    result = _validate_owner_type(
        owner_type="candidate",
        frame_text="Cognetti's housing reform platform and endorsements",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "candidate"


def test_media_label_passes_through_unchanged():
    result = _validate_owner_type(
        owner_type="media",
        frame_text="Anything at all",
        candidate="Paige Cognetti",
        opponents=["Rob Bresnahan"],
    )
    assert result == "media"


def test_no_opponents_configured_drops_opponent_label():
    result = _validate_owner_type(
        owner_type="opponent",
        frame_text="Bresnahan attacks",  # text mentions the name
        candidate="Paige Cognetti",
        opponents=[],  # but no opponents configured — can't validate the link
    )
    assert result == "media"
