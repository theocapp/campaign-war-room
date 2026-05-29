"""Auto-triage for proposed (HDBSCAN-clustered candidate-frame) narratives.

Phase B of the proposed-narrative auto-triage roadmap. The goal: most
clusters should never reach the user. The triage service walks the
current proposed clusters and emits one of four verdicts:

  - auto_reject            Heuristic noise gate (1 outlet, < 3 frames).
                           No LLM call. Confidence 1.0.
  - auto_merge             LLM merge-check judged this cluster IS a tracked
                           narrative already. suggested_merge_frame_id
                           points at it. Confidence from LLM.
  - auto_promote_suggested LLM promote-check judged it's clearly a real,
                           distinct, recurring narrative worth tracking.
                           suggested_name + description + owner pre-fill
                           the Promote modal. Confidence from LLM.
  - human_review           Genuinely ambiguous. Surfaces to the user with
                           no pre-fill.

Pipeline per cluster:
  1. Noise gate. Cheap; kills obvious junk.
  2. Embedding-similarity merge-check. Embed cluster's central claim,
     compute cosine vs all tracked frames. If top match >= MERGE_SIM_GATE
     we run the LLM merge-check on the top-3 candidates.
  3. LLM merge-check (gpt-4o). If "same" with confidence >= MERGE_CONF
     → auto_merge.
  4. LLM promote-check (gpt-4o). If "worth_tracking" with confidence
     >= PROMOTE_CONF → auto_promote_suggested. Else human_review.

Persists verdicts to ProposedClusterTriage keyed by a stable
cluster_fingerprint (sha256 of sorted member candidate_frame_ids) so
verdicts survive HDBSCAN cluster_id reshuffles across recomputes.

Designed to be triggered manually via POST /api/narrative-triage/run.
NOT wired into the scheduler — running it costs LLM money and the user
should decide when (Phase C will calibrate the prompts first).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models import CampaignConfig, CandidateFrame, NarrativeFrame, Opponent, ProposedClusterTriage
from app.services.embeddings import cosine_similarity, embed_one, embed_texts
from app.services.llm_provider import OpenAIProvider, _parse_json_response
from app.services.narrative_landscape import get_landscape

log = logging.getLogger(__name__)

# ── Thresholds — tuned in Phase C; these are the conservative starting set.
# Conservative = "when in doubt, send to human_review." We'd rather make the
# user click Confirm than auto-merge something that's actually different.

# Cosine similarity below which we don't even bother running the LLM
# merge-check. Dropped to 0.30 in V13.10c — gpt-4o was correctly catching
# the FEMA→#60 merge in some runs and missing it in others depending on
# whether #60 fell into the top-N candidates after embedding sort. At
# 0.30 with MERGE_CANDIDATE_COUNT=8, the full set of plausible matches
# enters the LLM's view consistently. False-positives at this lower gate
# get caught by the LLM itself — the gate is just a cost filter.
MERGE_SIM_GATE = 0.30

# How many top-similar tracked frames to send to the LLM merge-check.
# Bumped from 5 → 8. Still a single LLM call per cluster (all candidates
# in one prompt) so this just enlarges that single prompt by ~3 frames.
MERGE_CANDIDATE_COUNT = 8

# Within-batch dedup: cosine similarity above which we accept an LLM-
# proposed grouping as plausible. Acts as a hallucination filter — the LLM
# sometimes wants to group truly unrelated proposals ("Trump submission"
# with "carpetbagger"); requiring SOME minimum pairwise similarity blocks
# those hallucinations.
#
# Lowered from 0.65 → 0.35 in V13.10d after audit showed legitimate
# cross-vocabulary duplicates (e.g. "Bresnahan votes for Medicaid cuts"
# and "Federal budget cuts hurt PA welfare" — same story, similarity
# 0.39) were being filtered out. False positives at this lower gate get
# caught by the LLM's tightened "DO NOT over-merge" prompt + union-find
# over two dedup runs (false positives need to fire on BOTH runs).
DEDUP_SIM_GATE = 0.35

# LLM dedup confidence above which we downgrade the smaller-cluster
# auto_promote_suggested verdict to human_review with a "similar to X"
# reasoning. Same threshold as merge so the behavior feels symmetric.
DEDUP_CONF = 0.80

# Fixed seed for LLM determinism. Combined with temperature=0 (where the
# model supports it), this makes the LLM near-deterministic across calls
# with the same input — eliminates the 35% verdict-wobble observed at
# default temperature with gpt-4o. Picked arbitrarily; any constant works.
DETERMINISM_SEED = 42

# Which OpenAI model to use for triage judgments.
#
# Tested in V13.10:
#   gpt-4o default temp:   35% wobble across 3 runs (BAD)
#   gpt-4o + temp=0 + seed: 5% wobble across 3 runs (BEST)
#   o3-mini + seed:        30% wobble across 3 runs (reasoning model
#                          ignores temperature; seed alone insufficient)
#
# Default to gpt-4o because temperature=0 is the lever that actually kills
# the wobble. Reasoning models (o3-*, gpt-5-*) sound appealing but their
# internal sampling reintroduces stochasticity that we can't control.
TRIAGE_MODEL = "gpt-4o"

# Whether the chosen model supports temperature=0. Reasoning models do not.
_REASONING_MODELS = {"o3-mini", "o3", "o1", "o1-mini", "o1-preview", "gpt-5", "gpt-5-mini"}
SUPPORTS_TEMPERATURE = TRIAGE_MODEL not in _REASONING_MODELS

# LLM merge-check confidence above which we auto-merge (no user confirmation).
# V13.10c tried 0.70 to catch more borderline merges but the LLM was too
# eager — merged "Bresnahan supports farmers" → "Bresnahan delivers district
# funding" and similar across-policy collapses. 0.85 is the right point:
# strong "same" signals trigger auto-merge, weaker ones go to promote-check
# (better to track a duplicate than to merge two distinct narratives).
MERGE_CONF = 0.85

# V13.15 — "uncertain merge" gate. When the LLM merge-check returns a non-
# trivial confidence below MERGE_CONF but above MERGE_UNCERTAIN_GATE, send
# the cluster to human_review with the suggested merge target attached,
# INSTEAD of silently falling through to the promote-check.
#
# Why this exists: prior to this gate, a merge-check returning best_conf=0.7
# ("kinda similar but not sure") was thrown away, the cluster fell through
# to the promote-check, and the promote-check happily said "yes worth
# tracking with conf=0.95" — creating a duplicate frame that the user then
# had to clean up. The dedup signal from the merge-check was being silently
# discarded. Frame 75 ("Bresnahan's Harmful Cuts to Healthcare") was a real
# example: auto-promoted despite frame 65 ("Bresnahan's Record on Medicaid
# Cuts") already existing.
#
# 0.5 is conservative — anything the LLM thinks has at least 50% chance of
# being a duplicate gets a human glance before promotion. Tunable.
MERGE_UNCERTAIN_GATE = 0.5

# LLM promote-check confidence above which the verdict is `auto_promote_suggested`
# (yellow "Suggest promote" badge + pre-filled Promote modal). Below this floor
# the verdict falls to `human_review` (gray "AI uncertain" badge, no pre-fill).
#
# Tightened from 0.75 → 0.90 in V13.10 after the first triage pass showed
# gpt-4o gravitating to 0.90 on confident calls and lower values on borderline
# ones. 0.90 is also the floor we'd use when (or if) we flip auto-promote to
# hands-off: "only the most-certain verdicts skip your review." Lowering it
# again here is a one-line revert.
PROMOTE_CONF = 0.90

# Heuristic noise gate.
NOISE_MAX_SIZE = 2          # < 3 frames in cluster
NOISE_MAX_OUTLETS = 1       # only 1 unique outlet


# ── Prompts ─────────────────────────────────────────────────────────────

def _build_merge_system_prompt(candidate_name: str, opponent_name: str) -> str:
    """Merge-check system prompt, parameterized by who's running.

    Originally a constant; V13.10b makes it candidate-aware so the LLM can
    reason about who's being attacked/praised when judging whether two
    narratives describe the same political claim.
    """
    return f"""You are a political-campaign analyst working for the campaign of {candidate_name}.

The opposing candidate is {opponent_name}.

Your job: decide whether a proposed new narrative is really the same as a \
narrative the campaign already tracks. We track narratives at the level of \
recurring claims/messages, not individual news events.

Treat two narratives as "same" when they describe:
  - The same specific vote, scandal, or policy position. Two articles \
about Bresnahan's Medicaid-cuts vote = same. Two articles about \
"Bresnahan delivers federal money" with one specifically saying FEMA = \
same (FEMA is an instance of district funding).
  - The same recurring attack frame (e.g. "Cognetti is a carpetbagger" + \
"Cognetti doesn't belong here" → same)

Treat them as "related_but_distinct" when:
  - Different votes (Medicaid cuts vs. public broadcasting cuts vs. \
energy tax credits — each is its own narrative)
  - Different policy areas (Medicaid vs. SNAP, agriculture vs. federal \
funding writ large)
  - One is a SPECIFIC scandal/policy and the other is a GENERAL \
posture (e.g. "Cognetti's Fidelity Bank deal" ≠ "Cognetti's general \
transparency concerns" — first is a specific deal, second is broader)
  - Different politicians (NEVER merge attacks/praise of different people)

DO NOT over-merge. If you collapse two distinct narratives into one, we \
lose tracking granularity that matters for campaign messaging. Default to \
"related_but_distinct" when you can't point to a SPECIFIC shared fact.

Output strictly valid JSON with this exact shape:
{{
  "verdict": "same" | "related_but_distinct" | "unrelated",
  "confidence": 0.0-1.0,
  "reasoning": "<one sentence>"
}}

- "same" = same recurring claim, regardless of framing.
- "related_but_distinct" = related topic but different narrative.
- "unrelated" = different topic entirely.
"""

def _build_promote_system_prompt(
    candidate_name: str,
    opponent_name: str,
    tracked_examples: Optional[list[NarrativeFrame]] = None,
) -> str:
    """System prompt for the promote-check, parameterized by who's running
    AND by the campaign's existing tracked narratives.

    V13.10d learning loop: the campaign's existing tracked narratives are
    the strongest signal of "what this user thinks is worth tracking."
    Injecting them as in-context examples lets the LLM calibrate against
    actual user taste — without any past triage decisions, without any
    fine-tuning, and without added LLM calls.

    candidate_name + opponent_name still injected so the LLM can correctly
    classify owner_type ("criticism of Bresnahan" → candidate-favoring).
    """
    examples_block = ""
    if tracked_examples:
        lines = []
        for f in tracked_examples:
            desc = (f.description or "").strip()
            if desc:
                lines.append(f"  - \"{f.name}\" ({f.owner_type}): {desc[:160]}")
            else:
                lines.append(f"  - \"{f.name}\" ({f.owner_type})")
        examples_block = (
            "\nREFERENCE — narratives this campaign is already tracking "
            "(these are good examples of the granularity, specificity, and "
            "strategic value worth tracking):\n"
            + "\n".join(lines)
            + "\n\nWhen judging worth_tracking, calibrate against these. "
            "A proposed narrative similar in specificity + strategic value "
            "= worth tracking. Vague or one-off framings that don't match "
            "this pattern = not worth tracking.\n"
        )
    return f"""You are a political-campaign analyst working for the campaign of {candidate_name}.

The opposing candidate is {opponent_name}.

Your job: decide if a proposed narrative is worth tracking as a standalone \
recurring narrative in our campaign-intelligence system.

A narrative is WORTH TRACKING if it:
  - Is a coherent recurring claim/message (NOT a one-off news event)
  - Has strategic relevance to the campaign (could matter in voter \
messaging, opposition attacks, or media framing)
  - Could plausibly appear in multiple outlets / over multiple weeks

A narrative is NOT WORTH TRACKING if it:
  - Is a single news event with no recurring claim ("X happened on date Y")
  - Is too generic to be useful as a tracking unit
  - Is procedural / administrative (e.g. "primary election filing deadline")

OWNER TYPE — VERY IMPORTANT:
Set improved_owner_type based on WHO BENEFITS from the narrative being true,
NOT who's mentioned in it. The cluster's owner hint is often wrong; OVERRIDE
it freely.

  - improved_owner_type = "candidate"  (favors {candidate_name}) when:
      * Positive framing of {candidate_name}, OR
      * CRITICISM or ATTACK on {opponent_name} (their votes, scandals, etc.)
      * Example: "{opponent_name} voted to cut Medicaid" → candidate \
(it's an attack on the opponent → helps our candidate)

  - improved_owner_type = "opponent"  (favors {opponent_name}) when:
      * Positive framing of {opponent_name}, OR
      * CRITICISM or ATTACK on {candidate_name}
      * Example: "{candidate_name} is a carpetbagger" → opponent \
(attack on our candidate → helps the opponent)

  - improved_owner_type = "media"  for neutral/observational coverage that \
doesn't clearly favor either side.

Output strictly valid JSON with this exact shape:
{{
  "worth_tracking": true | false,
  "confidence": 0.0-1.0,
  "improved_name": "<short noun phrase, <= 70 chars>",
  "improved_description": "<one sentence describing the recurring narrative>",
  "improved_owner_type": "candidate" | "opponent" | "media",
  "reasoning": "<one sentence>"
}}

improved_name + improved_description will pre-fill our campaign tracking \
form. Write them as if writing a brand-new tracked narrative.
{examples_block}"""


_DEDUP_GROUP_SYSTEM_PROMPT = """You are a political-campaign analyst.

You're given a list of PROPOSED narratives that another AI step thought \
were each worth tracking. Your job: find groups of proposals that \
describe the SAME underlying recurring narrative, so the campaign \
doesn't end up tracking three near-duplicates.

GROUPING RULE — be CONSERVATIVE. Only group proposals when they are \
literally about the SAME specific fact:

  - SAME SPECIFIC VOTE (e.g. three proposals all about Bresnahan's vote \
on Medicaid cuts → ONE; but Medicaid cuts ≠ public broadcasting cuts \
≠ energy tax credits cuts → THREE separate, even though all are \
"Bresnahan voting to cut funding")
  - SAME SPECIFIC POLICY POSITION (e.g. two proposals about supporting \
ACA subsidy extension → ONE; but supporting ACA ≠ supporting farmer aid)
  - SAME SPECIFIC SCANDAL/EVENT (e.g. two proposals about Cognetti's \
Fidelity Bank deal → ONE; but Fidelity Bank ≠ general transparency \
concerns → TWO separate)

DO NOT GROUP across these — these are different narratives even if \
they share a theme:
  - Different VOTES (Medicaid cuts vs. public broadcasting cuts vs. \
energy tax credits — all "Bresnahan voted to cut X" but each X is a \
different recurring narrative)
  - Different SCANDALS (Trump alignment vs. carpetbagger label — both \
are attacks but on different angles)
  - Different POLITICIANS (an attack on Bresnahan and an attack on \
Cognetti are NEVER the same narrative)
  - General "is critical of X" vs. specific "X's vote on Y" — too \
different in specificity

A proposal stands alone if you can't point to a SPECIFIC shared fact \
between it and another. When in doubt, DON'T group — the user can \
manually merge later. Spurious groupings destroy useful narratives; \
missed groupings just leave extra rows in the queue.

Output strictly valid JSON with this exact shape:
{
  "groups": [
    {
      "indexes": [1, 5, 8],      // 1-indexed positions of grouped proposals
      "shared_fact": "<the SPECIFIC vote/scandal/event/position these share>",
      "reasoning": "<one sentence why these are the same narrative>"
    },
    ...
  ]
}

Only include groups of size >= 2. Empty list ({"groups": []}) is the \
correct output when nothing actually duplicates.
"""


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_fingerprint(member_candidate_frame_ids: list[int]) -> str:
    """Stable identity for a cluster across landscape recomputes.

    Uses sorted candidate_frame_ids so a cluster keeps its triage row
    even when HDBSCAN reshuffles cluster_ids on the next compute.
    """
    sorted_ids = sorted(set(member_candidate_frame_ids))
    payload = "|".join(str(i) for i in sorted_ids)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cluster_summary_text(
    cluster_name: str,
    member_quotes: list[str],
    owner_hint: str,
    max_quotes: int = 10,
    outlet_names: Optional[list[str]] = None,
    member_count: Optional[int] = None,
    outlet_count: Optional[int] = None,
) -> str:
    """Text representation we embed + show to the LLM.

    V13.10d — richer context for the LLM:
      - Up to 10 quotes (was 5) so the LLM sees more variety in framing
      - Outlet diversity (list of distinct outlet names) so it can see
        whether the cluster is one-outlet noise or multi-outlet narrative
      - Size + outlet count headline so the LLM can weigh credibility

    Same number of LLM calls; more signal per call.
    """
    parts = [f"Name: {cluster_name}", f"Owner hint: {owner_hint}"]
    if member_count is not None:
        outlets_part = f" across {outlet_count} outlet{'s' if (outlet_count or 0) != 1 else ''}" if outlet_count is not None else ""
        parts.append(f"Cluster size: {member_count} candidate frame{'s' if member_count != 1 else ''}{outlets_part}")
    if outlet_names:
        # Dedupe + cap at 12 outlets so the prompt doesn't balloon if a
        # cluster has 30+ outlets (rare but possible for big stories).
        unique_outlets = list(dict.fromkeys(o for o in outlet_names if o))
        if unique_outlets:
            parts.append(f"Outlets: {', '.join(unique_outlets[:12])}")
    if member_quotes:
        parts.append("Evidence quotes:")
        # Deduplicate near-identical quotes (wire syndication produces
        # multiple copies of the same quote). Cheap: lowercase + strip
        # punctuation, only keep first occurrence.
        seen = set()
        deduped: list[str] = []
        for q in member_quotes:
            if not q or not q.strip():
                continue
            key = "".join(c for c in q.lower() if c.isalnum())[:150]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(q.strip()[:280])
            if len(deduped) >= max_quotes:
                break
        for q in deduped:
            parts.append(f"- {q}")
    return "\n".join(parts)


def _tracked_frame_summary_text(frame: NarrativeFrame) -> str:
    """Text representation of a tracked narrative for embedding + LLM."""
    return f"Name: {frame.name}\nOwner: {frame.owner_type}\nDescription: {frame.description or ''}"


def _format_frames_for_merge_prompt(
    cluster_summary: str,
    candidates: list[tuple[NarrativeFrame, float]],
) -> str:
    """Build the user prompt for a single merge-check call."""
    lines = ["PROPOSED NEW NARRATIVE:", cluster_summary, ""]
    lines.append("CANDIDATE EXISTING NARRATIVES (most similar first):")
    for i, (frame, sim) in enumerate(candidates, 1):
        lines.append(f"\n[{i}] (cosine={sim:.2f})")
        lines.append(_tracked_frame_summary_text(frame))
    lines.append("\n---")
    lines.append(
        "For EACH candidate, output a JSON object with the schema "
        "above. Wrap them in a top-level array: "
        '{"checks": [{"candidate_index": 1, ...}, ...]}'
    )
    return "\n".join(lines)


def _format_past_decisions_block(
    db: Session,
    cluster_summary_emb: Optional[list[float]],
    max_examples: int = 4,
) -> str:
    """Pull similar past user decisions and format them as in-context examples.

    V13.10d learning loop. ProposedClusterTriage tracks applied_at (user
    confirmed an AI suggestion) and dismissed_at (user rejected it).
    Returning the top-K most-similar past decisions teaches the LLM
    "for clusters like this one, the user did X" — without any fine-tuning.

    Returns empty string if there's no usable history (system just started,
    or no decisions yet on similar clusters). The cost is one embedding
    call per past triage row, but those embeddings are cached in-process
    so subsequent triage passes are cheap.
    """
    rows = db.query(ProposedClusterTriage).filter(
        (ProposedClusterTriage.applied_at.isnot(None)) |
        (ProposedClusterTriage.dismissed_at.isnot(None)),
    ).all()
    if not rows or cluster_summary_emb is None:
        return ""

    # Embed each past decision's name+description and rank by cosine.
    texts = [
        f"{r.suggested_name or ''}. {r.suggested_description or ''}".strip()
        for r in rows
    ]
    embs = embed_texts(texts)
    scored: list[tuple[ProposedClusterTriage, float]] = []
    for row, emb in zip(rows, embs):
        if emb is None:
            continue
        sim = cosine_similarity(cluster_summary_emb, emb)
        scored.append((row, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return ""
    top = scored[:max_examples]

    lines = ["RELEVANT PAST DECISIONS by this user (high signal — these reflect their actual taste):"]
    for row, sim in top:
        if row.applied_at:
            if row.verdict == "auto_merge":
                # User confirmed a merge — strong signal that this kind of
                # cluster should NOT become its own tracked narrative.
                lines.append(
                    f"  • \"{row.suggested_name or 'cluster'}\" → user ACCEPTED merging "
                    f"into an existing tracked narrative (frame {row.suggested_merge_frame_id})"
                )
            else:
                lines.append(
                    f"  • \"{row.suggested_name or 'cluster'}\" → user ACCEPTED as a new tracked narrative"
                )
        elif row.dismissed_at:
            lines.append(
                f"  • \"{row.suggested_name or 'cluster'}\" → user DISMISSED (not worth tracking)"
            )
    return "\n".join(lines) + "\n\n"


def _make_openai_provider() -> Optional[OpenAIProvider]:
    """Instantiate the configured triage model.

    See TRIAGE_MODEL at top of file. Reasoning models (o3-*, gpt-5-*) are
    used for higher-quality judgments; chat models (gpt-4o) for cheaper
    deterministic-via-temperature runs.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        log.warning("narrative_triage: OPENAI_API_KEY not set, can't run LLM checks")
        return None
    try:
        return OpenAIProvider(api_key=key, model=TRIAGE_MODEL)
    except Exception as e:
        log.warning("narrative_triage: failed to instantiate OpenAIProvider: %s", e)
        return None


# ── Main entry points ──────────────────────────────────────────────────


def run_triage_pass(
    db: Session,
    *,
    days_back: int = 21,
    force_refresh: bool = False,
    dry_run: bool = False,
    hands_off: bool = True,
) -> dict:
    """Walk current proposed clusters and emit/refresh triage verdicts.

    Args:
      days_back: passed to get_landscape (same window the frontend uses).
      force_refresh: if True, re-evaluate clusters even if a triage row
        for their fingerprint already exists. Default False so repeat
        calls are cheap.
      dry_run: compute verdicts but don't write to DB. Useful for the
        Phase C A/B test where we score against manual labels.

    Returns: {
      "evaluated": N,
      "skipped_cached": N,
      "auto_reject": N,
      "auto_merge": N,
      "auto_promote_suggested": N,
      "human_review": N,
      "errors": N,
      "elapsed_seconds": float,
    }
    """
    started = datetime.utcnow()
    counts = {
        "evaluated": 0, "skipped_cached": 0,
        "auto_reject": 0, "auto_merge": 0,
        "auto_promote_suggested": 0, "human_review": 0,
        "errors": 0,
    }

    landscape = get_landscape(db, days_back=days_back)
    if landscape.get("error"):
        log.warning("narrative_triage: landscape error: %s", landscape["error"])
        # Still try — sometimes partial data is usable.

    # Group landscape points by cluster_id.
    points_by_cluster: dict[int, list] = {}
    for p in landscape.get("points", []):
        cid = p["cluster_id"]
        if cid < 0:  # HDBSCAN noise = singleton, skip
            continue
        points_by_cluster.setdefault(cid, []).append(p)

    clusters = landscape.get("clusters", [])
    if not clusters:
        counts["elapsed_seconds"] = (datetime.utcnow() - started).total_seconds()
        return counts

    # Pre-load tracked frames + their embeddings. Embedding cache makes
    # this cheap on repeat runs within the same process.
    tracked_frames = db.query(NarrativeFrame).filter(NarrativeFrame.active.is_(True)).all()
    tracked_texts = [_tracked_frame_summary_text(f) for f in tracked_frames]
    if tracked_frames:
        tracked_embeddings = embed_texts(tracked_texts)
    else:
        tracked_embeddings = []

    # V13.10b — inject candidate + opponent names into the promote-check
    # prompt so the LLM can correctly override owner_type. Without these
    # the LLM doesn't know which side is "ours" and propagates the
    # cluster's owner hint blindly (which is often wrong).
    campaign = db.query(CampaignConfig).first()
    opponent = db.query(Opponent).first()
    candidate_name = (campaign.candidate_name if campaign else None) or "our candidate"
    opponent_name = (opponent.name if opponent else None) or "the opponent"
    # V13.10d learning loop — use the campaign's existing tracked narratives
    # as in-context examples of "what's worth tracking." The LLM calibrates
    # against actual user taste without any past triage history needed.
    promote_system_prompt = _build_promote_system_prompt(
        candidate_name, opponent_name, tracked_examples=tracked_frames,
    )
    merge_system_prompt = _build_merge_system_prompt(candidate_name, opponent_name)

    # OpenAI provider for the LLM calls. Skipped clusters fall through to
    # human_review if LLM isn't available.
    provider = _make_openai_provider()
    judged_by = "gpt-4o" if provider else "no-llm"

    # V13.10b — two-pass: compute per-cluster verdicts first (in memory),
    # then run a within-batch dedup pass over the auto_promote_suggested
    # verdicts to catch duplicates among proposed clusters (the per-cluster
    # merge-check only compares against EXISTING tracked narratives, so it
    # misses cases where two proposed clusters describe the same thing).
    # Finally write everything.
    pending: list[dict] = []  # one entry per cluster being evaluated this pass

    for cluster in clusters:
        cid = cluster["cluster_id"]
        members = points_by_cluster.get(cid, [])
        member_ids = [int(m["candidate_frame_id"]) for m in members]
        if not member_ids:
            continue
        fingerprint = _make_fingerprint(member_ids)

        existing = db.query(ProposedClusterTriage).filter(
            ProposedClusterTriage.cluster_fingerprint == fingerprint,
        ).first()
        if existing and not force_refresh:
            counts["skipped_cached"] += 1
            continue

        try:
            verdict_payload = _triage_one_cluster(
                db=db,
                cluster=cluster,
                members=members,
                tracked_frames=tracked_frames,
                tracked_embeddings=tracked_embeddings,
                provider=provider,
                promote_system_prompt=promote_system_prompt,
                merge_system_prompt=merge_system_prompt,
            )
        except Exception as e:
            log.exception("narrative_triage: cluster %s failed: %s", cid, e)
            counts["errors"] += 1
            continue

        counts["evaluated"] += 1
        pending.append({
            "fingerprint": fingerprint,
            "member_ids": member_ids,
            "existing": existing,
            "payload": verdict_payload,
        })

    # ── Within-batch dedup pass (V13.10b) ──────────────────────────────
    if provider is not None and not dry_run:
        _within_batch_dedup_pass(pending, provider)
    elif provider is not None and dry_run:
        # Dry-run still runs the dedup pass so counts reflect the final state.
        _within_batch_dedup_pass(pending, provider)

    # ── Final counts + write ───────────────────────────────────────────
    for entry in pending:
        v = entry["payload"]["verdict"]
        counts[v] = counts.get(v, 0) + 1

    if dry_run:
        counts["elapsed_seconds"] = (datetime.utcnow() - started).total_seconds()
        return counts

    for entry in pending:
        existing = entry["existing"]
        payload = entry["payload"]
        member_ids = entry["member_ids"]
        if existing:
            existing.verdict = payload["verdict"]
            existing.confidence = payload["confidence"]
            existing.reasoning = payload.get("reasoning")
            existing.suggested_merge_frame_id = payload.get("suggested_merge_frame_id")
            existing.suggested_name = payload.get("suggested_name")
            existing.suggested_description = payload.get("suggested_description")
            existing.suggested_owner_type = payload.get("suggested_owner_type")
            existing.judged_by_model = judged_by
            existing.member_candidate_frame_ids_json = json.dumps(sorted(set(member_ids)))
        else:
            db.add(ProposedClusterTriage(
                cluster_fingerprint=entry["fingerprint"],
                member_candidate_frame_ids_json=json.dumps(sorted(set(member_ids))),
                verdict=payload["verdict"],
                confidence=payload["confidence"],
                reasoning=payload.get("reasoning"),
                suggested_merge_frame_id=payload.get("suggested_merge_frame_id"),
                suggested_name=payload.get("suggested_name"),
                suggested_description=payload.get("suggested_description"),
                suggested_owner_type=payload.get("suggested_owner_type"),
                judged_by_model=judged_by,
            ))

    db.commit()

    # V13.10e — hands-off auto-execution. For every triage row we just
    # wrote, if the verdict is auto_merge or auto_promote_suggested AND
    # the confidence clears the threshold, actually perform the action:
    #   - auto_merge → mark candidate frames resolved into the suggested target
    #   - auto_promote_suggested → create the new tracked NarrativeFrame
    #
    # Stamps applied_at on each row so the UI shows them as "recently
    # auto-applied" rather than pending. Skipped if hands_off=False (the
    # legacy click-to-confirm behavior is still available by setting that).
    counts["auto_executed"] = []
    if hands_off:
        counts["auto_executed"] = _auto_execute_verdicts(db, pending)
        db.commit()

    counts["elapsed_seconds"] = (datetime.utcnow() - started).total_seconds()
    return counts


def _auto_execute_verdicts(db: Session, pending: list[dict]) -> list[dict]:
    """Execute the high-confidence verdicts. Stamps applied_at + returns log.

    Called from run_triage_pass when hands_off=True. Iterates EVERY
    unapplied + non-dismissed triage row — not just newly-pending ones.
    Without this, repeat triage runs against cached verdicts silently
    skip auto-execution (which was the V13.10e shipping bug).
    """
    from app.services.candidate_frame_promoter import promote_cluster
    from app.routes.narrative_frames import _invalidate_established_landscape

    executed: list[dict] = []
    invalidate = False

    # Build the full queue: every triage row that hasn't been applied
    # or dismissed yet. Member IDs come from the pending entry (cheap)
    # when available, otherwise from the persisted JSON column.
    pending_by_fp = {e["fingerprint"]: e for e in pending}
    all_unapplied = db.query(ProposedClusterTriage).filter(
        ProposedClusterTriage.applied_at.is_(None),
        ProposedClusterTriage.dismissed_at.is_(None),
    ).all()

    for row in all_unapplied:
        # Recover the cluster's member candidate-frame ids: from the
        # in-memory pending entry if we just computed it, otherwise from
        # the persisted JSON column.
        entry = pending_by_fp.get(row.cluster_fingerprint)
        if entry is not None:
            member_ids = entry["member_ids"]
        else:
            try:
                member_ids = json.loads(row.member_candidate_frame_ids_json)
            except Exception:
                continue
        if not member_ids:
            continue

        verdict = row.verdict
        conf = float(row.confidence or 0.0)

        # Build a payload-shaped dict from the persisted row so the
        # rest of the executor logic doesn't need to know whether the
        # row came from `pending` or the cache.
        payload = {
            "verdict": verdict,
            "confidence": conf,
            "suggested_merge_frame_id": row.suggested_merge_frame_id,
            "suggested_name": row.suggested_name,
            "suggested_description": row.suggested_description,
            "suggested_owner_type": row.suggested_owner_type,
        }

        if verdict == "auto_merge" and conf >= MERGE_CONF:
            target_id = payload.get("suggested_merge_frame_id")
            if not target_id:
                continue
            target = db.query(NarrativeFrame).filter(
                NarrativeFrame.id == target_id,
            ).first()
            if not target:
                # Target frame no longer exists — leave for human review.
                continue
            now = datetime.utcnow()
            updated = (
                db.query(CandidateFrame)
                .filter(CandidateFrame.id.in_(member_ids))
                .filter(CandidateFrame.resolved_to_frame_id.is_(None))
                .update(
                    {"resolved_to_frame_id": target.id, "resolved_at": now},
                    synchronize_session=False,
                )
            )
            row.applied_at = now
            executed.append({
                "triage_id": row.id,
                "action": "auto_merge",
                "frame_id": target.id,
                "frame_name": target.name,
                "candidate_frames_attached": updated,
            })
            invalidate = True

        elif verdict == "auto_promote_suggested" and conf >= PROMOTE_CONF:
            name = (payload.get("suggested_name") or "").strip()
            description = (payload.get("suggested_description") or "").strip()
            owner = payload.get("suggested_owner_type") or "media"
            if not name or owner not in ("candidate", "opponent", "media"):
                continue
            try:
                new_frame = promote_cluster(
                    db,
                    suggested_name=name,
                    suggested_description=description,
                    owner_type=owner,
                    candidate_frame_ids=member_ids,
                )
                row.applied_at = datetime.utcnow()
                executed.append({
                    "triage_id": row.id,
                    "action": "auto_promote",
                    "frame_id": new_frame.id,
                    "frame_name": new_frame.name,
                })
                invalidate = True
            except Exception as e:
                log.warning(
                    "narrative_triage: auto_promote failed for triage %s (%s): %s",
                    row.id, name, e,
                )

    if invalidate:
        try:
            _invalidate_established_landscape()
        except Exception:
            pass  # cache invalidation failure isn't fatal
        # Also drop the candidate-landscape cache so the Review Queue's
        # "Proposed narratives" list reflects the just-resolved candidates
        # on its next fetch (otherwise the auto-promoted/merged clusters
        # stay visible for up to 25 hours).
        try:
            from app.services.narrative_landscape import invalidate_cache as _inv_candidate
            _inv_candidate()
        except Exception:
            pass

    # V13.10g — if we created any new tracked narratives, re-score the
    # last 7 days of articles against the latest active-frame set. This
    # populates "this week" mention counts on the new frames so they
    # don't look dormant immediately after creation. Only fires when we
    # actually created frames (auto-merges don't need this — they just
    # attach candidates to existing frames that already have mentions).
    promoted_frames = [x for x in executed if x["action"] == "auto_promote"]
    if promoted_frames:
        try:
            from app.services.narrative_frames import rematch_recent
            n = rematch_recent(db, days_back=7)
            log.info(
                "narrative_triage: post-promote rematch created %d new "
                "frame matches across the last 7 days for %d new frame(s)",
                n, len(promoted_frames),
            )
        except Exception:
            log.exception("narrative_triage: post-promote rematch failed")

    return executed


def _within_batch_dedup_pass(pending: list[dict], provider: OpenAIProvider) -> None:
    """Catch duplicates among `auto_promote_suggested` verdicts in the same batch.

    Per-cluster merge-check only sees EXISTING tracked narratives. If two
    proposed clusters in the same triage pass describe the same recurring
    narrative (e.g. three Medicaid-cuts clusters from different angles),
    they all sail through as auto_promote_suggested and we'd end up with
    duplicate tracked narratives.

    V13.10b approach: ONE LLM call that sees the full list of promotes
    and returns groups of indexes the LLM thinks describe the same
    narrative. Replaced the original pairwise approach because:
      - pairwise missed transitive dups (a-b similar, b-c similar, but a-c
        below the embedding gate)
      - pairwise was conservative on "same vs related" and missed
        same-policy/different-angle cases (e.g. Medicaid cuts via three
        framings)
      - single-call is also cheaper: one call per pass, ~$0.03,
        vs N*(N-1)/2 pairwise calls

    For each group of size >= 2, keep the largest-cluster entry and
    demote the rest to human_review with reasoning citing the keeper.
    Mutates `pending` in place.
    """
    promotes = [
        (i, entry) for i, entry in enumerate(pending)
        if entry["payload"]["verdict"] == "auto_promote_suggested"
    ]
    if len(promotes) < 2:
        return

    # V13.10c — run the group-dedup TWICE and union the results. gpt-4o
    # is stochastic on the "is this the same narrative?" call; one run
    # might catch a Medicaid trio, the next might miss one of them.
    # Two calls union'd massively reduces the false-negative rate at the
    # cost of two LLM calls (~$0.06 total) per pass.
    entries_for_dedup = [entry for _, entry in promotes]
    pass1 = _llm_group_dedup(provider, entries_for_dedup)
    pass2 = _llm_group_dedup(provider, entries_for_dedup)
    raw_groups: list[dict] = []
    for r in (pass1, pass2):
        if r and isinstance(r.get("groups"), list):
            raw_groups.extend(r["groups"])

    if not raw_groups:
        return

    # Merge overlapping groups across the two passes via union-find. If
    # pass1 says [1,5] same and pass2 says [5,8] same, the union is
    # [1,5,8] — they're all the same narrative.
    parent = list(range(len(entries_for_dedup)))
    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    group_meta: dict[int, dict] = {}  # representative → {shared_fact, reasoning}
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        idxs = group.get("indexes", [])
        if not isinstance(idxs, list) or len(idxs) < 2:
            continue
        zero = [i - 1 for i in idxs if isinstance(i, int) and 1 <= i <= len(entries_for_dedup)]
        if len(zero) < 2:
            continue
        for k in range(1, len(zero)):
            union(zero[0], zero[k])
        # Remember reasoning by the smallest-index member of the union
        rep = find(zero[0])
        if rep not in group_meta:
            group_meta[rep] = {
                "shared_fact": group.get("shared_fact", ""),
                "reasoning": group.get("reasoning", ""),
            }

    # Collect connected components.
    components: dict[int, list[int]] = {}
    for i in range(len(entries_for_dedup)):
        r = find(i)
        components.setdefault(r, []).append(i)
    # Convert to "groups" structure for downstream processing.
    groups = []
    for rep, members_list in components.items():
        if len(members_list) < 2:
            continue
        meta = group_meta.get(rep, {})
        groups.append({
            "indexes": [m + 1 for m in members_list],  # back to 1-indexed
            "shared_fact": meta.get("shared_fact", ""),
            "reasoning": meta.get("reasoning", ""),
        })

    # V13.10b — pre-embed all promotes so we can validate proposed groupings
    # by pairwise cosine similarity. The LLM occasionally hallucinates
    # connections across genuinely different topics (e.g. "submission to
    # Trump" + "carpetbagger") and the embedding distance is a cheap
    # ground-truth check.
    embed_texts_list = [
        f"{entry['payload'].get('suggested_name','')}. {entry['payload'].get('suggested_description','')}"
        for _, entry in promotes
    ]
    promote_embs = embed_texts(embed_texts_list)

    for group in groups:
        if not isinstance(group, dict):
            continue
        idxs = group.get("indexes", [])
        if not isinstance(idxs, list) or len(idxs) < 2:
            continue
        # Convert 1-indexed → 0-indexed and bound-check.
        zero_idxs = [i - 1 for i in idxs if isinstance(i, int) and 1 <= i <= len(promotes)]
        if len(zero_idxs) < 2:
            continue

        # Sanity-check: every PAIR in this LLM-proposed group must have
        # cosine similarity >= DEDUP_SIM_GATE. If even one pair is too
        # dissimilar, reject the whole group as an LLM hallucination.
        rejected = False
        for ai in range(len(zero_idxs)):
            for bi in range(ai + 1, len(zero_idxs)):
                ea = promote_embs[zero_idxs[ai]]
                eb = promote_embs[zero_idxs[bi]]
                if ea is None or eb is None:
                    continue
                sim = cosine_similarity(ea, eb)
                if sim < DEDUP_SIM_GATE:
                    log.info(
                        "narrative_triage: rejecting hallucinated group %s — "
                        "pair (idx %d, %d) cosine=%.2f below gate %.2f",
                        idxs, zero_idxs[ai] + 1, zero_idxs[bi] + 1, sim, DEDUP_SIM_GATE,
                    )
                    rejected = True
                    break
            if rejected:
                break
        if rejected:
            continue

        # Pick keeper: largest cluster (most member_ids). Tie-break by
        # index to make it deterministic.
        zero_idxs.sort(key=lambda k: (-len(promotes[k][1]["member_ids"]), k))
        keeper_pidx = zero_idxs[0]
        keeper_entry = promotes[keeper_pidx][1]
        keeper_name = keeper_entry["payload"].get("suggested_name", "another cluster")
        reasoning_text = group.get("reasoning", "")
        shared = group.get("shared_fact", "")

        for demote_pidx in zero_idxs[1:]:
            demoted_entry = promotes[demote_pidx][1]
            reason = (
                f"Within-batch duplicate of '{keeper_name}'. "
                f"Shared fact: {shared}. {reasoning_text}".strip()
            )
            demoted_entry["payload"]["verdict"] = "human_review"
            demoted_entry["payload"]["reasoning"] = reason


def _llm_group_dedup(
    provider: OpenAIProvider, entries: list[dict],
) -> Optional[dict]:
    """Single LLM call: identify groups of duplicate proposals.

    Returns the parsed JSON dict {"groups": [{"indexes": [...], "reasoning": "..."}]}
    or None on parse failure.
    """
    lines = ["PROPOSED NARRATIVES TO DEDUP:\n"]
    for i, entry in enumerate(entries, 1):
        payload = entry["payload"]
        lines.append(
            f"[{i}] {payload.get('suggested_name','')}\n"
            f"    Owner: {payload.get('suggested_owner_type','')}\n"
            f"    {payload.get('suggested_description','')}\n"
        )
    lines.append(
        "\nIdentify groups of proposals (by 1-indexed position) that describe "
        "the same underlying recurring narrative. Output JSON per the schema."
    )
    user_prompt = "\n".join(lines)
    try:
        raw = provider._chat(
            user_prompt=user_prompt,
            system_prompt=_DEDUP_GROUP_SYSTEM_PROMPT,
            json_mode=True,
        )
    except Exception as e:
        log.warning("narrative_triage: group-dedup call failed: %s", e)
        return None
    parsed = _parse_json_response(raw)
    if not parsed or "groups" not in parsed:
        log.warning("narrative_triage: group-dedup returned unexpected shape: %r", raw[:200])
        return None
    return parsed


def _triage_one_cluster(
    *,
    db: Session,
    cluster: dict,
    members: list,
    tracked_frames: list[NarrativeFrame],
    tracked_embeddings: list,
    provider: Optional[OpenAIProvider],
    promote_system_prompt: str,
    merge_system_prompt: str,
) -> dict:
    """Run one cluster through the triage pipeline. Returns verdict payload.

    Payload keys: verdict, confidence, reasoning, optional
    suggested_merge_frame_id / suggested_name / suggested_description /
    suggested_owner_type.
    """
    cluster_name = cluster.get("representative_name", "")
    owner_hint = cluster.get("owner_type_hint", "media")
    size = cluster.get("size", len(members))
    outlet_count = cluster.get("outlet_count", 0)
    is_noise_candidate = size <= NOISE_MAX_SIZE and outlet_count <= NOISE_MAX_OUTLETS

    # Build cluster summary up front (used by both merge-check and the
    # eventual promote-check). Cheap; just text formatting + embedding.
    member_quotes = [(m.get("evidence_quote") or "") for m in members]
    cluster_outlets = cluster.get("outlet_names") or []
    if not cluster_outlets:
        cluster_outlets = [m.get("outlet_name") or m.get("source_name") or "" for m in members]
    cluster_summary = _cluster_summary_text(
        cluster_name=cluster_name,
        member_quotes=member_quotes,
        owner_hint=owner_hint,
        outlet_names=cluster_outlets,
        member_count=size,
        outlet_count=outlet_count,
    )

    # V13.10d — embed the cluster summary ONCE; used for merge candidates
    # AND for the learning-loop past-decisions lookup further down.
    cluster_emb = embed_one(cluster_summary)

    # ── 1. Embedding-similarity merge-check
    # V13.10e — runs BEFORE the noise heuristic so that small clusters
    # that ARE actually instances of an existing tracked narrative get
    # rescued via merge instead of dropped as noise. Held-out test on
    # historical promote-decisions showed the old order auto_rejected
    # the 1-frame "Cognetti Anti-Corruption" cluster — but that cluster
    # really WAS the existing tracked narrative. Merge first, reject after.
    merge_candidates: list[tuple[NarrativeFrame, float]] = []
    if tracked_frames and tracked_embeddings and cluster_emb:
        sims = []
        for frame, emb in zip(tracked_frames, tracked_embeddings):
            if emb is None:
                continue
            sim = cosine_similarity(cluster_emb, emb)
            sims.append((frame, sim))
        sims.sort(key=lambda x: x[1], reverse=True)
        merge_candidates = [
            (f, s) for f, s in sims[:MERGE_CANDIDATE_COUNT] if s >= MERGE_SIM_GATE
        ]

    # ── 2. LLM merge-check (only if there's a plausible candidate AND LLM avail)
    if merge_candidates and provider is not None:
        merge_result = _llm_merge_check(provider, cluster_summary, merge_candidates, merge_system_prompt)
        if merge_result is not None:
            best_idx = merge_result.get("best_match_index")
            best_conf = merge_result.get("best_confidence", 0.0)
            best_reason = merge_result.get("best_reasoning", "")
            if best_idx is not None and best_conf >= MERGE_CONF:
                matched_frame = merge_candidates[best_idx][0]
                return {
                    "verdict": "auto_merge",
                    "confidence": best_conf,
                    "reasoning": best_reason,
                    "suggested_merge_frame_id": matched_frame.id,
                }
            # V13.15 — uncertain-merge guard. If the LLM flagged a plausible
            # duplicate (best_conf in [0.5, 0.85)), DON'T fall through to
            # the promote-check — surface to the user with the suggested
            # target attached. This prevents the "silently discarded
            # merge signal → spurious auto-promote" bug that created
            # frame 75 as a duplicate of frame 65.
            if best_idx is not None and best_conf >= MERGE_UNCERTAIN_GATE:
                matched_frame = merge_candidates[best_idx][0]
                return {
                    "verdict": "human_review",
                    "confidence": best_conf,
                    "reasoning": (
                        f"Possible duplicate of '{matched_frame.name}' "
                        f"(frame {matched_frame.id}, merge confidence {best_conf:.2f}): "
                        f"{best_reason}"
                    ),
                    "suggested_merge_frame_id": matched_frame.id,
                }
            # else: weak merge signal, fall through to the promote-check
            # (cluster might still be promote-worthy OR fall through to
            # noise gate if it's tiny)

    # ── 3. Noise gate (heuristic, no LLM)
    # Now AFTER the merge-check: small/single-outlet clusters that don't
    # merge into an existing frame are very likely actual noise.
    if is_noise_candidate:
        return {
            "verdict": "auto_reject",
            "confidence": 1.0,
            "reasoning": f"noise heuristic: size={size}, outlets={outlet_count}",
        }

    # ── 4. LLM promote-check
    if provider is None:
        # No LLM available — punt to human review.
        return {
            "verdict": "human_review",
            "confidence": 0.0,
            "reasoning": "LLM unavailable; defaulted to human review",
        }

    # V13.10d learning loop: pull similar past user decisions (applied
    # accepts + dismissed rejects) as in-context examples for the LLM.
    # On a fresh system this returns empty string (no history yet);
    # populates as the user actually uses the triage UI.
    past_decisions_block = _format_past_decisions_block(db, cluster_emb)

    promote_result = _llm_promote_check(
        provider, cluster_summary, cluster_name, owner_hint,
        promote_system_prompt, past_decisions_block,
    )
    if promote_result is None:
        return {
            "verdict": "human_review",
            "confidence": 0.0,
            "reasoning": "promote-check LLM call failed; defaulted to human review",
        }

    worth = promote_result.get("worth_tracking", False)
    conf = float(promote_result.get("confidence", 0.0))
    reasoning = promote_result.get("reasoning", "")

    # V13.10b — LLM may override the cluster's owner hint (cluster hint can
    # be wrong, e.g. an attack on opponent gets classified as `opponent`
    # when it actually favors our candidate). Accept the override only if
    # it's a valid value; otherwise fall back to the cluster hint.
    llm_owner = promote_result.get("improved_owner_type")
    resolved_owner = llm_owner if llm_owner in ("candidate", "opponent", "media") else owner_hint

    if worth and conf >= PROMOTE_CONF:
        return {
            "verdict": "auto_promote_suggested",
            "confidence": conf,
            "reasoning": reasoning,
            "suggested_name": (promote_result.get("improved_name") or cluster_name)[:200],
            "suggested_description": promote_result.get("improved_description") or "",
            "suggested_owner_type": resolved_owner,
        }

    # Either the LLM said not worth tracking with low confidence, OR worth
    # tracking but low confidence — both go to human review (we never
    # auto-reject a non-noise cluster without explicit user input).
    return {
        "verdict": "human_review",
        "confidence": conf,
        "reasoning": reasoning,
        # Still pre-fill the suggested name/description as a head-start for
        # the user even when confidence wasn't high enough to auto-suggest.
        "suggested_name": (promote_result.get("improved_name") or cluster_name)[:200],
        "suggested_description": promote_result.get("improved_description") or "",
        "suggested_owner_type": resolved_owner,
    }


def _llm_merge_check(
    provider: OpenAIProvider,
    cluster_summary: str,
    candidates: list[tuple[NarrativeFrame, float]],
    system_prompt: str,
) -> Optional[dict]:
    """Ask gpt-4o whether the cluster matches any of the top-N tracked frames.

    Returns {"best_match_index": int|None, "best_confidence": float,
    "best_reasoning": str} or None on parse failure.

    Single LLM call per cluster (asks about all candidates at once) so
    we don't spend N× the tokens.
    """
    user_prompt = _format_frames_for_merge_prompt(cluster_summary, candidates)
    try:
        raw = provider._chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            json_mode=True,
            temperature=0 if SUPPORTS_TEMPERATURE else None,
            seed=DETERMINISM_SEED,
        )
    except Exception as e:
        log.warning("narrative_triage: merge-check call failed: %s", e)
        return None

    parsed = _parse_json_response(raw)
    if not parsed or not isinstance(parsed.get("checks"), list):
        log.warning("narrative_triage: merge-check returned unexpected shape: %r", raw[:200])
        return None

    # Find the highest-confidence "same" verdict among the checks.
    best_idx = None
    best_conf = 0.0
    best_reason = ""
    for check in parsed["checks"]:
        verdict = check.get("verdict")
        conf = float(check.get("confidence", 0.0))
        idx = check.get("candidate_index")
        if verdict == "same" and isinstance(idx, int) and 1 <= idx <= len(candidates):
            if conf > best_conf:
                best_conf = conf
                best_idx = idx - 1  # convert to 0-indexed
                best_reason = str(check.get("reasoning", ""))

    return {
        "best_match_index": best_idx,
        "best_confidence": best_conf,
        "best_reasoning": best_reason,
    }


def _llm_promote_check(
    provider: OpenAIProvider,
    cluster_summary: str,
    cluster_name: str,
    owner_hint: str,
    system_prompt: str,
    past_decisions_block: str = "",
) -> Optional[dict]:
    """Ask gpt-4o whether the cluster is worth tracking as a standalone narrative.

    V13.10d — past_decisions_block, if provided, injects similar past
    user decisions (applied accepts / dismissed rejects) as in-context
    examples. Empty string when there's no relevant history yet — the
    learning loop just bootstraps as the user uses the system.
    """
    user_prompt = (
        "Evaluate this proposed narrative for whether the campaign should "
        "start tracking it as a recurring narrative.\n\n"
        f"{cluster_summary}\n\n"
        f"{past_decisions_block}"
        "Output the JSON object exactly as specified in the system prompt."
    )
    try:
        raw = provider._chat(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            json_mode=True,
            temperature=0 if SUPPORTS_TEMPERATURE else None,
            seed=DETERMINISM_SEED,
        )
    except Exception as e:
        log.warning("narrative_triage: promote-check call failed: %s", e)
        return None

    parsed = _parse_json_response(raw)
    if not parsed or "worth_tracking" not in parsed:
        log.warning(
            "narrative_triage: promote-check returned unexpected shape "
            "(cluster=%r owner=%s): %r",
            cluster_name, owner_hint, raw[:200],
        )
        return None
    return parsed


# ── Read API used by routes/frontend ────────────────────────────────────


def list_triage_verdicts(
    db: Session,
    *,
    include_dismissed: bool = False,
) -> list[dict]:
    """Return all triage rows as serializable dicts.

    Used by GET /api/narrative-triage so the frontend can decorate each
    proposed cluster row with its verdict (and pre-fill the Promote
    modal for high-confidence promote suggestions).
    """
    q = db.query(ProposedClusterTriage)
    if not include_dismissed:
        q = q.filter(ProposedClusterTriage.dismissed_at.is_(None))
    rows = q.all()
    out = []
    for r in rows:
        try:
            members = json.loads(r.member_candidate_frame_ids_json)
        except Exception:
            members = []
        out.append({
            "id": r.id,
            "cluster_fingerprint": r.cluster_fingerprint,
            "member_candidate_frame_ids": members,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
            "suggested_merge_frame_id": r.suggested_merge_frame_id,
            "suggested_name": r.suggested_name,
            "suggested_description": r.suggested_description,
            "suggested_owner_type": r.suggested_owner_type,
            "dismissed_at": r.dismissed_at.isoformat() if r.dismissed_at else None,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            "judged_by_model": r.judged_by_model,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })
    return out
