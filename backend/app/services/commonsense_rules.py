"""Commonsense grounding rules — GKG principle: implicit-knowledge constraints.

Domain/range constraints (in entity_extraction.PREDICATE_DOMAIN_RANGE) catch
TYPE-level violations: a location can't endorse, a bill can't attack a person,
etc. They don't catch ROLE-level violations:

  - The US President doesn't represent a specific House district.
  - A US Senator doesn't represent a House district (senators represent
    whole states; districts elect House Representatives).
  - The Vice President doesn't represent a state.
  - A House member from NY can't represent PA-08.
  - A mayor can't represent a federal district by their mayoral role.

These rules encode that implicit knowledge as composable predicates against
the seeded role / state metadata. Auto-discovered entities (no role tag)
generally pass these rules — there's nothing to check against. Rules are
designed to err on the side of NOT rejecting in the absence of metadata.

Application points:
  - entity_extraction.persist_extraction calls evaluate() per new relation
    and skips writes that get action="reject"
  - scripts/entity_commonsense_cleanup.py runs the rules over existing data
    and applies the chosen action (reject = delete; flag = surface in queue;
    downgrade = lower confidence)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Literal

logger = logging.getLogger(__name__)


# Roles that don't represent specific districts/states by the `represents`
# predicate (their role is national/executive, not district-based).
_NATIONAL_EXECUTIVE_ROLES = {"president", "vice_president"}

# Roles that represent ONLY their whole state — never a House district.
_STATE_LEVEL_ROLES = {"senator", "former_senator", "governor", "former_governor"}

# Roles that represent a specific district or city.
_DISTRICT_LEVEL_ROLES = {"candidate", "incumbent", "former_congressman",
                          "congresswoman", "congressman", "representative"}

# Roles that represent only a specific city (mayor types).
_CITY_LEVEL_ROLES = {"mayor", "former_mayor"}


def _metadata(entity) -> dict:
    """Pull metadata_json off an Entity (or dict-like) into a plain dict.
    Safe against missing/malformed values."""
    if entity is None:
        return {}
    raw = getattr(entity, "metadata_json", None)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def role_of(entity) -> str | None:
    return _metadata(entity).get("role")


def state_of(entity) -> str | None:
    """Subject's home state (for persons) or location's state (for locations)."""
    md = _metadata(entity)
    return md.get("state") or md.get("home_state")


def location_type_of(entity) -> str | None:
    """Distinguishes 'city' / 'county' / 'district' / 'state' / 'region'."""
    return _metadata(entity).get("location_type")


@dataclass
class CommonsenseRule:
    name: str
    description: str
    detect: Callable[[object, str, object], bool]
    action: Literal["reject", "flag_for_review", "downgrade_confidence"]


# ── Rule definitions ───────────────────────────────────────────────────────

RULES: list[CommonsenseRule] = [
    CommonsenseRule(
        name="national-executive-cannot-represent-location",
        description="The US President and Vice President don't represent any specific district or state via the `represents` predicate — their role is national.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _NATIONAL_EXECUTIVE_ROLES
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="senator-cannot-represent-house-district",
        description="US Senators represent entire states, not House districts.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _STATE_LEVEL_ROLES
            and location_type_of(o) in {"district"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="senator-cannot-represent-city",
        description="US Senators represent entire states, not individual cities.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _STATE_LEVEL_ROLES
            and location_type_of(o) in {"city"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="senator-cannot-represent-county",
        description="US Senators represent entire states, not individual counties.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _STATE_LEVEL_ROLES
            and location_type_of(o) in {"county"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="governor-cannot-represent-district",
        description="Governors represent their state, not a House district.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in {"governor", "former_governor"}
            and location_type_of(o) in {"district", "county", "city"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="mayor-cannot-represent-district-or-state",
        description="Mayors represent their city, not a district or state.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _CITY_LEVEL_ROLES
            and location_type_of(o) in {"district", "state", "county"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="house-rep-from-other-district-cannot-represent-this-district",
        description="A House member's role is to represent their own district. Mike Lawler (NY-17) doesn't represent PA-08.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in _DISTRICT_LEVEL_ROLES
            and state_of(s) is not None
            and state_of(o) is not None
            and state_of(s) != state_of(o)
            and location_type_of(o) in {"district"}
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="speaker-doesnt-represent-via-role",
        description="The House Speaker / Minority Leader doesn't `represents` a district through their leadership role — their `represents` is for their home district. Cross-state representation by these leaders is almost always a misclassification.",
        detect=lambda s, p, o: (
            p == "represents"
            and role_of(s) in {"house_speaker", "house_minority_leader"}
            and state_of(s) is not None
            and state_of(o) is not None
            and state_of(s) != state_of(o)
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="locations-dont-have-predecessors",
        description="Predecessor_of requires both subject and object to be persons (already enforced by domain/range, but reinforced here).",
        detect=lambda s, p, o: (
            p == "predecessor_of"
            and getattr(s, "type", None) != "person"
        ),
        action="reject",
    ),
    CommonsenseRule(
        name="entity-cannot-be-predecessor-of-itself",
        description="A person can't be their own predecessor.",
        detect=lambda s, p, o: (
            p == "predecessor_of"
            and getattr(s, "id", None) == getattr(o, "id", None)
            and getattr(s, "id", None) is not None
        ),
        action="reject",
    ),
]


def evaluate(subject, predicate: str, object_) -> tuple[str | None, str | None]:
    """Run all rules. Return (action, rule_name) of the FIRST matching rule,
    or (None, None) if no rule fires. action is 'reject', 'flag_for_review',
    or 'downgrade_confidence'."""
    for rule in RULES:
        try:
            if rule.detect(subject, predicate, object_):
                return rule.action, rule.name
        except Exception as exc:
            # A buggy rule should not break extraction. Log and continue.
            logger.warning("commonsense rule %s raised: %s", rule.name, exc)
            continue
    return None, None
