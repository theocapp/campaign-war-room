"""
Extract-to-frame match verifier.

Runs after the LLM matching step (in narrative_frames.match_article or
rescore.py) to catch the failure mode where the matcher assigns an
extract to a topically-adjacent-but-wrong frame.

This is the production version of the V5 prompt that won the bake-off
in scripts/rescore_eval_prompt.py. Same prompt that purged 552 bad
assignments during the V12 cleanup pass.

When this is enabled, the runtime cost per match is ~$0.002 (gpt-4o)
or ~$0.0001 (gpt-4o-mini). At the historical scoring rate of ~30
articles/day with ~3 matches/article, that's about $0.18/day on
gpt-4o or $0.01/day on gpt-4o-mini.

Toggle via env:
  EXTRACT_VERIFIER_ENABLED       - "true"/"false" (default "true")
  EXTRACT_VERIFIER_MODEL         - default "gpt-4o-mini" for cost; flip
                                   to "gpt-4o" if you want max precision
                                   (~5pp accuracy improvement, 20× cost).
  EXTRACT_VERIFIER_FAIL_OPEN     - "true"/"false" (default "true").
                                   If true, LLM failures KEEP the match
                                   (don't lose data when the verifier
                                   itself errors). If false, fail-closed
                                   = drop the match on any error.

Logging
-------
Every REJECT decision is logged at INFO with frame_id, source_item_id,
extract preview, and the verifier's reason. You can grep recent logs to
see what the verifier is purging in production.
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# Same prompt as scripts/rescore_eval_prompt.py V5 ("v5_refined") that
# won the bake-off with 93-94% agreement against gpt-4o ground truth.
# DO NOT modify without re-running the bake-off — small wording changes
# move precision/recall by several percentage points.
_VERIFIER_PROMPT = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT is appropriately matched to the FRAME.

APPROPRIATE if BOTH:
  (A) The frame's subject is substantively involved — as actor, target,
      OR critic. If Frame is "Bresnahan's Healthcare Record" and the
      extract is Cognetti CRITICIZING Bresnahan's healthcare record,
      that is APPROPRIATE — Cognetti is the critic, Bresnahan's record
      is the topic.
  (B) The extract is specifically about the frame's TOPIC area — not
      generic political statements that could fit any frame.

The frame's NAME may have stance-loaded words like "Criticized",
"Lack Benefits", "Concerns" — these describe the frame's overall stance,
but individual extracts don't need to match the stance. An extract that
talks about the topic from a supportive angle still appropriately
matches a critical frame (and vice versa), as long as it's the same topic.

INAPPROPRIATE if ANY:
  - Extract is about a different person/topic and the frame's subject
    isn't relevantly involved.
  - Extract is generic political content with no specific tie to the
    frame's topic (e.g. "we will win in November" matches no specific
    topic frame).
  - Extract is a fragment, list item, or pure throat-clearing.

EXAMPLES (covering tricky cases):

Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's past Medicaid votes" → KEEP

Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a $27M downtown traffic plan" → REJECT

Frame "Cognetti's Anti-Corruption" + "I've shown in Scranton we can build government for people and be honest with people" → KEEP

Frame "Cognetti Flips NEPA Seat" + "NEPA is fired up and ready to win in November" → KEEP

Frame "Cognetti Flips NEPA Seat" + "Democrats hope to win back the House in 2026" → REJECT

Frame "Bresnahan's Healthcare Record" + "DCCC says Bresnahan is poster child of corruption" → REJECT

Frame "Bresnahan's Tax Cuts Lack Benefits" + "ATR notes Bresnahan signed Taxpayer Protection Pledge" → KEEP

Frame "NEPA Support" + "Cognetti will be a voice in Congress that puts NEPA first" → KEEP

Frame "Healthcare Debate" + "Public healthcare premiums will skyrocket after Jan 2026 due to expiring subsidies" → REJECT

Frame "Bresnahan Delivers District Funding" + "Bresnahan worked to ensure PA-08 hospitals received funding" → KEEP

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


@dataclass
class VerifierResult:
    """Verdict + reason. `keep` is the only attribute callers need 99% of the time."""
    keep: bool
    reason: str
    error: Optional[str] = None


def _is_enabled() -> bool:
    """Default-on. Set EXTRACT_VERIFIER_ENABLED=false to bypass."""
    return os.environ.get("EXTRACT_VERIFIER_ENABLED", "true").lower() != "false"


def _fail_open() -> bool:
    """Default: LLM error → KEEP the match (don't lose data on transient errors)."""
    return os.environ.get("EXTRACT_VERIFIER_FAIL_OPEN", "true").lower() != "false"


def _model() -> str:
    return os.environ.get("EXTRACT_VERIFIER_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def verify_match(
    extract: str, frame_id: int, frame_name: str,
    frame_description: Optional[str] = None,
    source_item_id: Optional[int] = None,
) -> VerifierResult:
    """Ask the LLM whether (extract → frame) is an appropriate match.

    Returns a VerifierResult. Callers should check `.keep` and either
    write the match (if True) or skip it (if False).

    `source_item_id` is optional — used only for log breadcrumbs so
    rejections can be traced back to a specific article.
    """
    if not _is_enabled():
        return VerifierResult(keep=True, reason="verifier disabled by env")

    # Defensive: empty extract = nothing to verify, refuse the match so
    # nothing weird like a NULL string sneaks past.
    if not extract or len(extract.strip()) < 4:
        return VerifierResult(keep=False, reason="extract empty or too short")

    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            # No API key → can't verify. Fail-open by default.
            return VerifierResult(
                keep=_fail_open(),
                reason="no OPENAI_API_KEY",
                error="missing_key",
            )
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=_model(),
            messages=[{
                "role": "user",
                "content": _VERIFIER_PROMPT.format(
                    frame_name=frame_name,
                    frame_description=(frame_description or "(no description)")[:500],
                    extract=(extract or "").replace('"', "'")[:600],
                ),
            }],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=120,
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        verdict = (data.get("verdict") or "").upper()
        reason = data.get("reason") or ""
        keep = verdict == "KEEP"

        # Always log REJECTs at INFO so production behavior is auditable
        # without flipping log levels. KEEPs go to DEBUG (high volume).
        if not keep:
            logger.info(
                "extract_verifier: REJECT item=%s frame=%d '%s' — %s | extract=%r",
                source_item_id if source_item_id is not None else "?",
                frame_id, frame_name, reason, (extract or "")[:120],
            )
        else:
            logger.debug(
                "extract_verifier: KEEP item=%s frame=%d '%s'",
                source_item_id if source_item_id is not None else "?",
                frame_id, frame_name,
            )

        return VerifierResult(keep=keep, reason=reason or verdict)

    except Exception as exc:
        # LLM error — apply the fail-open / fail-closed policy.
        keep = _fail_open()
        logger.warning(
            "extract_verifier: error (%s) item=%s frame=%d — fail_open=%s, returning keep=%s",
            exc, source_item_id, frame_id, _fail_open(), keep,
        )
        return VerifierResult(keep=keep, reason=f"verifier error: {exc}", error=str(exc))
