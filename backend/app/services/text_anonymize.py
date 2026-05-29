"""
Anonymize candidate / opponent names in text before embedding.

Why
---
The narrative landscape (both proposed and established views) projects
short text into 2D via UMAP. If the text includes candidate names
prominently ("Bresnahan's healthcare cuts" / "Cognetti's healthcare
expansion"), the embedding picks up the SUBJECT (which person) more
strongly than the TOPIC (healthcare). So two narratives about the same
topic but different candidates end up on opposite sides of the map.

V13.18 — REPLACE names with role placeholders ("the candidate", "the
opponent") instead of stripping them entirely. The old strip-only
behavior left grammatically-broken text ("Stock Trades. is being accused
of...") which degraded embedding quality measurably. Role placeholders:
  - Preserve sentence grammar so embeddings get clean signal
  - Keep the actor's ROLE (candidate vs opponent) so same-side
    narratives still cluster appropriately (e.g. "Cognetti's Anti-
    Corruption" and "Cognetti's Maternity Leave Inconsistency" become
    "the candidate's anti-corruption" and "the candidate's maternity
    leave inconsistency" — both mention "the candidate" so they sit
    closer in embedding space than the old strip-only would produce)
  - Remove the SPECIFIC name so unrelated narratives about the same
    person don't artificially cluster

A/B test results (Gemini embeddings on 32 active PA-08 frames,
9 should-be-close pairs + 3 should-be-far pairs):

    Strategy              close-avg  far-avg  discrimination
    strip (old)           0.457      0.253    0.204
    role placeholders     0.558      0.319    0.239  ← chosen
    generic placeholder   0.488      0.237    0.251
    keep names            0.610      0.457    0.153

Role placeholders give the best signal on the specific pairs that
matter for this domain (e.g. Anti-Corruption ↔ Maternity Leave
went from 0.217 → 0.448, a 2× improvement). The generic placeholder
has slightly better aggregate discrimination but loses the same-
side clustering signal that role placeholders preserve.

Caveats
-------
This is a pre-embedding text transform only. The names stay intact
everywhere else (display, search, exports).

Single-word last names ("Cognetti", "Bresnahan") rarely appear as common
words, so casual word-boundary replacement is safe. First names alone
("Paige", "Rob") are riskier — common enough as nouns in other contexts
— but in this domain (PA-08 campaign articles) they almost always refer
to the candidates. We replace them anyway and accept the small false-
positive rate.
"""
from __future__ import annotations
import re
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent


# Role tokens used as drop-in replacements for actual names. Chosen
# specifically: "the candidate" / "the opponent" read as natural English
# noun phrases so the embedding model treats them as ordinary subject
# tokens rather than as unusual stop-words.
CANDIDATE_TOKEN = "the candidate"
OPPONENT_TOKEN = "the opponent"


def _name_tokens(full_name: str) -> list[str]:
    """Break 'Paige Cognetti' into ['Paige Cognetti', 'Cognetti', 'Paige'].

    Order matters: try the FULL name first (longest match) before falling
    back to individual tokens, otherwise 'Paige Cognetti' gets replaced
    as 'Paige' + 'Cognetti' separately and the regex sees overlapping
    matches.
    """
    parts = full_name.strip().split()
    if not parts:
        return []
    out = [full_name.strip()]
    # Last name first (more discriminating than first name).
    if len(parts) > 1:
        out.append(parts[-1])      # surname
        out.append(parts[0])       # given name
    return out


def _name_replacements(db: Session) -> list[tuple[str, str]]:
    """All (name → role_token) replacements for the current campaign.

    Returns a list of (pattern_text, replacement_text) tuples sorted by
    pattern length DESC so 'Paige Cognetti' is matched before 'Cognetti'
    alone (avoids leaving 'Paige ' as a stray token).

    Empty list if the campaign isn't configured (text passes through).
    """
    out: list[tuple[str, str]] = []
    cfg = db.query(CampaignConfig).first()
    if cfg and cfg.candidate_name:
        for tok in _name_tokens(cfg.candidate_name):
            out.append((tok, CANDIDATE_TOKEN))
    for opp in db.query(Opponent).all():
        if opp.name:
            for tok in _name_tokens(opp.name):
                out.append((tok, OPPONENT_TOKEN))
    # Dedupe by pattern (lowercase), preserving first-seen order.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for pat, repl in out:
        key = pat.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((pat, repl))
    # Sort by pattern length DESC so longer names match before shorter ones.
    deduped.sort(key=lambda kv: len(kv[0]), reverse=True)
    return deduped


def anonymize_text(text: str, replacements: Iterable[tuple[str, str]]) -> str:
    """Replace each name with its role-token, preserving possessives + grammar.

    Examples:
      anonymize_text("Bresnahan's healthcare record",
                     [("Bresnahan", "the opponent")])
        → "the opponent's healthcare record"
      anonymize_text("Paige Cognetti supports Geisinger",
                     [("Paige Cognetti", "the candidate"), ("Cognetti", "the candidate")])
        → "the candidate supports Geisinger"
    """
    if not text:
        return text
    out = text
    for pattern, replacement in replacements:
        if not pattern:
            continue
        # Two patterns: possessive (Name's → role-token's) and bare (Name → role-token).
        # Possessive must be replaced first so the apostrophe isn't orphaned.
        out = re.sub(
            rf"\b{re.escape(pattern)}(['’]s)\b",
            lambda m: f"{replacement}{m.group(1)}",
            out, flags=re.IGNORECASE,
        )
        out = re.sub(
            rf"\b{re.escape(pattern)}\b",
            replacement, out, flags=re.IGNORECASE,
        )
    # Squash repeated whitespace from any successive replacements.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def get_anonymizer(db: Session):
    """Return a (text -> text) callable for the current campaign.

    Cached at the closure level so callers can run it in a tight loop
    without re-querying the DB.
    """
    replacements = _name_replacements(db)
    if not replacements:
        return lambda t: t
    return lambda t: anonymize_text(t, replacements)
