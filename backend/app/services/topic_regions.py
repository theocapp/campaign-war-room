"""
Topic regions for the Landscape page.

Takes the established-frame UMAP positions, clusters them with HDBSCAN,
labels each cluster via LLM, and persists the labels so user edits
survive recomputes.

Why this exists
---------------
The Landscape map already arranges frames by topical similarity. This
layer adds NAMES to the natural topic groupings so you can say "show me
the healthcare narratives" instead of squinting at coordinates. The
user's vision: regions → frames → article extracts as an infinite-zoom
hierarchy.

Architecture overview
---------------------
1. Cluster: HDBSCAN over 2D UMAP positions with min_cluster_size=2 so
   even small topic groupings surface. Singletons stay ungrouped
   (cluster_id = -1) and render outside region hulls in the UI.

2. Label: For each NEW cluster (not matching an existing persisted
   label by Jaccard ≥ 0.5), call gpt-4o-mini with the I_role_few_shot
   prompt determined in scripts/topic_label_bakeoff_v2.py.

3. Persist: User-editable labels live in the topic_region_labels table.
   Identity = sorted set of member frame_ids (JSON). On recompute, fuzzy
   match new clusters against persisted rows. If a user edited a label,
   it sticks forever.

4. Cache: 24h TTL on the full topic-regions response, invalidated when
   any frame mutates. Cold compute is ~3s (HDBSCAN + 1 LLM call per new
   region).

Prompt selection
----------------
The chosen prompt + model was determined in a controlled bake-off over
24 real clusters (4 established regions + 20 proposed clusters):
  - 8 prompt variants tested across 5 models in V1
  - 5 prompt variants × 2 models on the full 24-cluster corpus in V2
  - Final head-to-head: original I_role_few_shot vs with-compound-instruction

Winner: I_role_few_shot + "&" compound notation + gpt-4o-mini. See
scripts/topic_label_final_check.py for the comparison data.
"""
from __future__ import annotations
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import (
    FrameClusterMatch,
    Outlet,
    SourceItem,
    TopicRegionLabel,
)

logger = logging.getLogger(__name__)


# ── Topic-dominance weighting ─────────────────────────────────────────────
#
# Topic ring color comes from a weighted "owner_mix": for each member frame,
# sum the per-outlet article contributions weighted by outlet authority.
#
#   contribution(outlet, frame) = min(articles, ARTICLE_CAP_PER_OUTLET)
#                                  × COALESCE(authority_score, 5)
#
# The cap and the weighting both encode honest choices:
#
# - ARTICLE_CAP_PER_OUTLET = 9 was derived from the empirical 95th
#   percentile of the actual (outlet, frame) article-count distribution at
#   the time this was written. 95% of pairs have ≤9 articles; the top 5%
#   are typically one outlet (e.g. thetimes-tribune) blanketing a single
#   story 20+ times — wire-syndication shape, not editorial diversity. The
#   cap lets such an outlet contribute strong signal (9 × authority) but
#   prevents it from carrying a topic single-handedly.
#
# - We use authority_score (1-10, per-outlet, editorially set) rather than
#   the system's monthly_visitors × 0.003 reach formula. The visitors
#   formula's 0.003 constant pretends to be a scientific "% of monthly
#   audience reads each article" but breaks for any non-newspaper outlet
#   (a Facebook page with 100M monthly visitors does not deliver 300k
#   readers per post). authority_score is openly editorial and tunable
#   per outlet in Setup — same epistemological honesty as a 1-10 quality
#   rating, no fake precision.
#
# Authority_score itself remains a curated editorial value, ultimately a
# human judgment on an ordinal scale. This is the best signal we have
# right now and beats narrative-count or monthly_visitors×0.003 on both
# accuracy and honesty — but it should be revisited when better signals
# exist (e.g. citation-pickup graphs across outlets, agenda-setting
# metrics, district-voter exposure surveys). See task #81.
ARTICLE_CAP_PER_OUTLET = 9
DEFAULT_AUTHORITY_SCORE = 5  # midpoint of the 1-10 scale, used when outlet is unlinked


def _compute_frame_weighted_contributions(
    db: Session, frame_ids: list[int],
) -> dict[int, float]:
    """Return {frame_id: total_weighted_contribution} for the given frames.

    Each (outlet, frame) pair contributes min(article_count, cap) × authority.
    Articles with no outlet linkage fall back to DEFAULT_AUTHORITY_SCORE.

    One batched query — no N+1.
    """
    if not frame_ids:
        return {}

    # Step 1: count articles per (frame, outlet) pair.
    per_outlet = (
        db.query(
            FrameClusterMatch.frame_id.label("frame_id"),
            SourceItem.outlet_id.label("outlet_id"),
            func.count(SourceItem.id).label("article_count"),
        )
        .join(SourceItem, SourceItem.story_cluster_id == FrameClusterMatch.story_cluster_id)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            FrameClusterMatch.frame_id.in_(frame_ids),
        )
        .group_by(FrameClusterMatch.frame_id, SourceItem.outlet_id)
        .subquery()
    )

    # Step 2: cap article_count at ARTICLE_CAP_PER_OUTLET, multiply by the
    # outlet's authority_score (or default midpoint when outlet missing),
    # sum per frame.
    capped = case(
        (per_outlet.c.article_count > ARTICLE_CAP_PER_OUTLET, ARTICLE_CAP_PER_OUTLET),
        else_=per_outlet.c.article_count,
    )
    weighted_expr = capped * func.coalesce(Outlet.authority_score, DEFAULT_AUTHORITY_SCORE)

    rows = (
        db.query(
            per_outlet.c.frame_id,
            func.sum(weighted_expr).label("weighted_total"),
        )
        .outerjoin(Outlet, Outlet.id == per_outlet.c.outlet_id)
        .group_by(per_outlet.c.frame_id)
        .all()
    )

    return {r.frame_id: float(r.weighted_total or 0) for r in rows}


# ── Prompt (winner from scripts/topic_label_final_check.py) ───────────────

LABELING_PROMPT = """You are a political analyst building a narrative topic map for the {race} race.

Give each cluster a precise 1-3 word topic label. Title Case. No punctuation. No quotes.

If the cluster genuinely combines two equal themes, use "X & Y".

Examples:
Cluster: "Medicaid Cuts in Pennsylvania", "ACA Subsidy Expiration", "Hospital Closures Surge"
Label: Medicaid Cuts

Cluster: "Voter ID Restrictions", "Mail Ballot Drop-Boxes Removed", "Polling Place Closures"
Label: Voting Access

Cluster: "Insider Trading Allegations", "Stock Disclosure Failures", "Ethics Committee Probe"
Label: Insider Trading

Cluster: "Federal Bridge Funding", "Amtrak Expansion Proposal", "Highway Maintenance Backlog"
Label: Infrastructure Investment

Cluster: "Healthcare Subsidies Debate", "Census Migration Patterns", "Suburban Demographic Shift"
Label: Healthcare & Demographics

Now label this cluster:
{narratives}

Label:"""


# ── Types ──────────────────────────────────────────────────────────────────

class TopicRegion(TypedDict):
    """One named region returned to the frontend."""
    region_id: int                      # transient ID for this compute (NOT the DB row id)
    label: str
    member_frame_ids: list[int]
    edited_by_user: bool                # so UI can show a "user-edited" indicator if desired
    # Owner-color mix — frontend uses dominant color for hull tinting.
    # Authority-weighted sum (not raw count) — see ARTICLE_CAP_PER_OUTLET
    # and _compute_frame_weighted_contributions above. Floats since the
    # multiplication can produce non-integer totals; the frontend
    # topicColorWithDominance() takes `number` and handles either case.
    owner_mix: dict[str, float]         # {"candidate": 87.5, "opponent": 32.0, "media": 5.0}
    # V13.19 — 4-quadrant breakdown (owner × subject). Keys are the
    # QUADRANT_* constants from subject_classifier.py. Same authority-
    # weighted contribution unit as owner_mix; the two are different
    # AGGREGATIONS of the same per-frame contribution values.
    # owner_mix sums by beneficiary; quadrant_mix sums by quadrant.
    quadrant_mix: dict[str, float]      # {"our_defense": 50.0, "our_offense": 30.0, ...}


class TopicRegionsResult(TypedDict):
    regions: list[TopicRegion]
    ungrouped_frame_ids: list[int]      # HDBSCAN noise points
    computed_at: str
    error: Optional[str]


@dataclass
class _Frame:
    """Internal frame representation for clustering + labeling."""
    id: int
    name: str
    description: str
    owner_type: str
    x: float
    y: float


# ── Cache ─────────────────────────────────────────────────────────────────

_CACHE: dict = {
    "data": None,            # TopicRegionsResult | None
    "computed_at": None,     # datetime
}
_lock = threading.Lock()


def invalidate_cache() -> None:
    """Drop cached regions. Called after any frame add/edit/delete/promote."""
    with _lock:
        _CACHE["data"] = None
        _CACHE["computed_at"] = None


def get_topic_regions(
    db: Session, frames: list[dict], max_age_hours: int = 24,
) -> TopicRegionsResult:
    """Return cached regions or compute fresh.

    `frames` is the already-projected list from get_established_landscape:
    each dict has frame_id, name, description, owner_type, x, y.
    """
    cached = _CACHE.get("data")
    computed_at = _CACHE.get("computed_at")
    if (
        cached is not None
        and computed_at is not None
        and (datetime.utcnow() - computed_at).total_seconds() <= max_age_hours * 3600
    ):
        return cached
    return _compute(db, frames)


def _compute(db: Session, frame_dicts: list[dict]) -> TopicRegionsResult:
    """Fresh HDBSCAN + LLM labeling. Writes cache on success."""
    if len(frame_dicts) < 2:
        return _empty(None)

    # ── 1. Internal frame objects ─────────────────────────────────────────
    frames = [
        _Frame(
            id=int(f["frame_id"]),
            name=f["name"],
            description=f.get("description") or "",
            owner_type=f.get("owner_type") or "media",
            x=float(f["x"]),
            y=float(f["y"]),
        )
        for f in frame_dicts
    ]

    # ── 2. HDBSCAN over UMAP positions ────────────────────────────────────
    try:
        import numpy as np
        from hdbscan import HDBSCAN
        from app.services._numba_serialize import numba_lock
    except Exception as exc:
        return _empty(f"hdbscan unavailable: {exc}")

    coords = np.array([[f.x, f.y] for f in frames])
    # min_cluster_size=2 so even small topic groupings get a region.
    # min_samples=1 = leaf clustering (more granular than the default
    # eom). Matches the bake-off configuration.
    # numba_lock serializes against any other UMAP/HDBSCAN call in the
    # process — Numba's workqueue threading layer isn't thread-safe, and
    # without this the backend dies when the scheduler's clustering
    # refresh fires while a user is hitting the landscape endpoint.
    with numba_lock:
        labels = HDBSCAN(
            min_cluster_size=2, min_samples=1,
            metric="euclidean", cluster_selection_method="leaf",
        ).fit_predict(coords)

    # Group frames by cluster label.
    groups: dict[int, list[_Frame]] = {}
    for f, lbl in zip(frames, labels):
        groups.setdefault(int(lbl), []).append(f)

    # V13.18 — POST-PROCESS NOISE: HDBSCAN with min_cluster_size=2 tends
    # to leave singletons as noise (cluster_id = -1) even when they sit
    # right next to a real cluster. With the V13.18 role-placeholder
    # embeddings, several Bresnahan-related frames ended up as noise
    # (Stock Trades, District Funding, etc.) because there weren't enough
    # other Bresnahan-tagged frames nearby to form a 2-member cluster.
    # Visually those points still belong with their nearest cluster.
    #
    # Algorithm: compute each existing cluster's centroid and the
    # 90th-percentile within-cluster member→centroid distance — that's
    # our "this point belongs here" threshold (data-driven, no magic
    # numbers). For each noise frame, find nearest cluster centroid;
    # if within the absorption threshold, assign it to that cluster.
    # If still too far from any cluster, leave it as noise.
    noise_frames = list(groups.get(-1, []))
    if noise_frames and len(groups) > 1:  # at least one non-noise cluster exists
        non_noise_cids = [cid for cid in groups if cid != -1]
        # Centroids of the real clusters (UMAP space).
        cluster_centroids: dict[int, tuple[float, float]] = {}
        within_dists: list[float] = []
        for cid in non_noise_cids:
            members = groups[cid]
            cx = sum(m.x for m in members) / len(members)
            cy = sum(m.y for m in members) / len(members)
            cluster_centroids[cid] = (cx, cy)
            # Collect within-cluster member→centroid distances for the
            # global p90 absorption threshold.
            for m in members:
                within_dists.append(((m.x - cx) ** 2 + (m.y - cy) ** 2) ** 0.5)

        if within_dists:
            within_dists.sort()
            # p90: most members fall within this distance of their centroid.
            # Noise frames live BY DEFINITION outside the tight-cluster
            # density region, so the absorption threshold needs to be a
            # MULTIPLE of p90 to catch them. With our current PA-08
            # corpus (32 frames, ~3 per topic), p90 ≈ 0.57 in UMAP space
            # while typical noise→nearest-centroid distances are 0.7-1.1.
            # Multiplier 2.0 → threshold ≈ 1.14, which catches noise
            # frames sitting just outside their nearest cluster's tight
            # core while still leaving truly isolated frames as noise
            # (distance > 2× p90 = "clearly its own thing").
            absorption_threshold = within_dists[
                min(int(len(within_dists) * 0.90), len(within_dists) - 1)
            ]
            absorption_threshold *= 2.0
        else:
            # Single-member clusters everywhere — fall back to a wide
            # threshold so absorption still happens.
            absorption_threshold = 3.0

        still_noise: list[_Frame] = []
        for nf in noise_frames:
            # Nearest non-noise centroid.
            best_cid = None
            best_dist = float("inf")
            for cid, (cx, cy) in cluster_centroids.items():
                d = ((nf.x - cx) ** 2 + (nf.y - cy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_cid = cid
            if best_cid is not None and best_dist <= absorption_threshold:
                groups[best_cid].append(nf)
            else:
                still_noise.append(nf)
        groups[-1] = still_noise

    ungrouped: list[int] = sorted(f.id for f in groups.get(-1, []))
    real_clusters: list[list[_Frame]] = [
        sorted(members, key=lambda f: f.id)
        for cluster_id, members in groups.items()
        if cluster_id != -1
    ]

    if not real_clusters:
        return _empty(None)

    # ── 3. Resolve labels (cached + LLM for new ones) ─────────────────────
    race_descriptor = _race_descriptor(db)
    persisted = _load_persisted_labels(db)

    # Per-frame weighted contributions for the topic-dominance owner_mix.
    # Single batched query for all frames in scope. Frames with no articles
    # don't appear in this dict and fall through to a baseline below.
    all_frame_ids = [f.id for f in frames]
    weighted_by_frame = _compute_frame_weighted_contributions(db, all_frame_ids)

    # V13.19 — subject classifier for the quadrant_mix calculation below.
    # Computed once per compute (one DB hit for campaign config) and reused
    # across all frames in the inner loop.
    from app.services.subject_classifier import get_subject_classifier
    _subject_classify = get_subject_classifier(db)

    regions: list[TopicRegion] = []
    # V13.15 — track labels already chosen in THIS recompute so we can
    # prevent two clusters from ending up with the same name. Two failure
    # modes were both observed in production:
    #   1. The LLM independently named two distinct clusters "Cognetti's
    #      Record" because each label call sees only its own cluster.
    #   2. The fuzzy-match returned the same persisted label for two
    #      different clusters when both happened to overlap with it.
    # Both produce duplicate sidebar entries that confuse the user.
    used_labels_in_batch: set[str] = set()

    for i, members in enumerate(real_clusters):
        member_ids = [f.id for f in members]
        matched = _fuzzy_match(member_ids, persisted)
        label: Optional[str] = None
        edited_by_user = False

        if matched and matched.label not in used_labels_in_batch:
            # Reuse persisted label. If user-edited it's permanent;
            # otherwise we still reuse — the LLM label is stable and the
            # 24h cache will only refresh occasionally anyway.
            label = matched.label
            edited_by_user = matched.edited_by_user
            # If membership shifted slightly, update the persisted row's
            # member set so next match works against the latest set.
            # NOTE: we must `db.add` the underlying ORM row, not the
            # _PersistedLabel wrapper — the wrapper isn't a mapped class.
            # Bug found when the post-cleanup frame delete shifted region
            # memberships and the next compute crashed with "Class
            # _PersistedLabel is not mapped".
            if matched.member_frame_ids != member_ids:
                matched.member_frame_ids_json = json.dumps(member_ids)
                db.add(matched.row)
        else:
            # No persisted match OR persisted match would collide with a
            # label already used by an earlier cluster in this batch. In
            # either case, call the LLM and tell it which labels are off
            # limits. If user-edited the matched persisted row, we still
            # honor the edit, but we trigger a fresh label so neither
            # cluster loses its name silently (rare — only if the user
            # edited a row whose Jaccard overlaps two new clusters).
            label = _llm_label(members, race_descriptor, avoid_labels=used_labels_in_batch)
            # As a last guard against the LLM ignoring the avoid_labels
            # instruction, append a numeric disambiguator. We bias toward
            # readable output; a "(2)" suffix is uglier than the cluster
            # would otherwise look but is better than two identical sidebar
            # rows the user can't tell apart.
            if label in used_labels_in_batch:
                base = label
                n = 2
                while f"{base} ({n})" in used_labels_in_batch:
                    n += 1
                label = f"{base} ({n})"
            row = TopicRegionLabel(
                member_frame_ids_json=json.dumps(member_ids),
                label=label,
                edited_by_user=False,
            )
            db.add(row)

        used_labels_in_batch.add(label)

        # Authority-weighted owner_mix. Each frame contributes its weighted
        # total (= sum over outlets of min(article_count, cap) × authority)
        # to its owner side. Frames with no articles still count as the
        # baseline (1 × DEFAULT_AUTHORITY_SCORE = 5) so a tracked-but-
        # unmatched narrative isn't silently dropped from dominance.
        #
        # V13.19 — also compute quadrant_mix, summing the SAME per-frame
        # weighted contributions by (owner × subject) quadrant. Same
        # underlying numbers as owner_mix, just bucketed two different
        # ways for two different chart visualizations.
        from app.services.subject_classifier import quadrant_key
        owner_mix: dict[str, float] = {"candidate": 0.0, "opponent": 0.0, "media": 0.0}
        quadrant_mix: dict[str, float] = {
            "our_defense": 0.0, "our_offense": 0.0,
            "their_defense": 0.0, "their_offense": 0.0,
            "media": 0.0,
        }
        for f in members:
            contribution = weighted_by_frame.get(f.id, 0.0)
            if contribution <= 0:
                contribution = float(DEFAULT_AUTHORITY_SCORE)
            owner_mix[f.owner_type] = owner_mix.get(f.owner_type, 0.0) + contribution
            # The _Frame dataclass doesn't carry subject_type, so derive it
            # here. Each compute call should construct its own classifier
            # — cheap (one DB hit for campaign config).
            subj = _subject_classify(f.name)
            qk = quadrant_key(f.owner_type, subj)
            quadrant_mix[qk] = quadrant_mix.get(qk, 0.0) + contribution

        regions.append({
            "region_id": i,
            "label": label,
            "member_frame_ids": member_ids,
            "edited_by_user": edited_by_user,
            "owner_mix": owner_mix,
            "quadrant_mix": quadrant_mix,
        })

    db.commit()

    result: TopicRegionsResult = {
        "regions": regions,
        "ungrouped_frame_ids": ungrouped,
        "computed_at": datetime.utcnow().isoformat(),
        "error": None,
    }

    with _lock:
        _CACHE["data"] = result
        _CACHE["computed_at"] = datetime.utcnow()

    logger.info(
        "topic_regions: %d regions, %d ungrouped frames",
        len(regions), len(ungrouped),
    )
    return result


# ── Persisted label match ─────────────────────────────────────────────────

@dataclass
class _PersistedLabel:
    """In-memory copy of TopicRegionLabel rows for fast Jaccard lookup."""
    row: TopicRegionLabel
    member_frame_ids: list[int]

    @property
    def label(self) -> str:
        return self.row.label

    @property
    def edited_by_user(self) -> bool:
        return self.row.edited_by_user

    @property
    def member_frame_ids_json(self) -> str:
        return self.row.member_frame_ids_json

    @member_frame_ids_json.setter
    def member_frame_ids_json(self, value: str) -> None:
        self.row.member_frame_ids_json = value


def _load_persisted_labels(db: Session) -> list[_PersistedLabel]:
    out: list[_PersistedLabel] = []
    for row in db.query(TopicRegionLabel).all():
        try:
            ids = sorted(int(x) for x in json.loads(row.member_frame_ids_json))
        except Exception:
            continue
        out.append(_PersistedLabel(row=row, member_frame_ids=ids))
    return out


def _fuzzy_match(
    new_member_ids: list[int], persisted: list[_PersistedLabel],
    threshold: float = 0.5,
) -> Optional[_PersistedLabel]:
    """Return the best Jaccard-overlap match if it clears the threshold.

    Jaccard = |A ∩ B| / |A ∪ B|. Threshold 0.5 means the new region
    shares more than half its members with an existing labeled region.
    Lower than that and the clusters are too different to count as the
    same region.
    """
    new_set = set(new_member_ids)
    if not new_set:
        return None
    best: Optional[_PersistedLabel] = None
    best_jac: float = 0.0
    for p in persisted:
        old_set = set(p.member_frame_ids)
        if not old_set:
            continue
        inter = len(new_set & old_set)
        union = len(new_set | old_set)
        jac = inter / union
        # Always prefer user-edited matches: if a user-edited row clears
        # threshold, take it even if a non-edited row has higher overlap.
        if jac >= threshold and (
            best is None
            or (p.edited_by_user and not best.edited_by_user)
            or (p.edited_by_user == best.edited_by_user and jac > best_jac)
        ):
            best = p
            best_jac = jac
    return best


# ── LLM labeling ──────────────────────────────────────────────────────────

def _race_descriptor(db: Session) -> str:
    """Human-readable race description for the prompt (e.g. 'PA-08 U.S. House 2026').

    Pulled from CampaignConfig. Falls back to a generic descriptor if the
    campaign isn't fully configured.
    """
    from app.models import CampaignConfig
    cfg = db.query(CampaignConfig).first()
    if not cfg:
        return "U.S. political campaign"
    parts = []
    if cfg.district:
        parts.append(cfg.district)
    elif cfg.state:
        parts.append(cfg.state)
    if cfg.office:
        parts.append(cfg.office)
    if cfg.election_date:
        parts.append(str(cfg.election_date.year))
    return " ".join(parts) if parts else "U.S. political campaign"


def _llm_label(
    members: list[_Frame],
    race_descriptor: str,
    avoid_labels: Optional[set[str]] = None,
) -> str:
    """Call the LLM to generate a label for a new region.

    Falls back to a string-mash of the dominant frame names if the LLM
    is unreachable. Better to have an OK label than a blank one.

    avoid_labels: labels already used by OTHER clusters in this batch.
    Passed to the LLM so it can pick a distinct name; also enforced
    post-hoc by the caller with a numeric disambiguator suffix.
    """
    narratives = "\n".join(
        f"- {f.name}: {f.description[:200]}" if f.description else f"- {f.name}"
        for f in members
    )
    prompt = LABELING_PROMPT.format(race=race_descriptor, narratives=narratives)
    if avoid_labels:
        # SAFETY NET only — only fires when a previous cluster in this
        # same batch landed on one of these labels. The calibrated prompt
        # above (kept unchanged from the V2 bake-off) handles the common
        # case; this addendum exists for the edge case where two clusters
        # would otherwise collide on the same generic label.
        #
        # When the LLM hits this list it's a signal the two clusters'
        # core themes are similar — the right answer is a MORE SPECIFIC
        # angle (e.g. "Cognetti's Mayoral Record" beats "Cognetti's
        # Record"), not a synonym. The phrasing nudges in that direction
        # without restructuring the proven prompt.
        forbidden = sorted(avoid_labels)
        prompt += (
            "\n\nAdditional constraint: do not use any of these labels "
            f"(already taken by other clusters in this batch): {', '.join(forbidden)}. "
            "If the cluster genuinely overlaps with one of those topics, "
            "use a MORE SPECIFIC angle (e.g. 'Cognetti's Mayoral Record' "
            "instead of 'Cognetti's Record')."
        )
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return _fallback_label(members)
        model = os.environ.get("OPENAI_TOPIC_LABEL_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=30,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _clean_label(raw) or _fallback_label(members)
    except Exception as exc:
        logger.warning("topic_regions: LLM labeling failed: %s", exc)
        return _fallback_label(members)


def _clean_label(raw: str) -> str:
    """Strip wrappers the LLM might add. Also enforce 1-3 word limit
    (matches the calibrated bake-off prompt — see PROMPT_FINAL in
    scripts/topic_label_final_check.py).

    If the model occasionally returns "Label: X" or quoted output despite
    the prompt, we normalize here so the bad output never reaches the UI.
    """
    text = (raw or "").strip()
    if "LABEL:" in text:
        text = text.split("LABEL:", 1)[1].strip()
    if "\n" in text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = lines[-1] if lines else text
    text = text.strip().strip('"').strip("'").rstrip(".").strip()
    # Cap at 3 words (4 if the 3rd or 4th is a single-char "&" for the
    # compound notation, e.g. "Healthcare & Demographics").
    words = text.split()
    if len(words) > 4:
        words = words[:3]
    return " ".join(words)


def _fallback_label(members: list[_Frame]) -> str:
    """Last-resort label when LLM is unavailable.

    Use the shortest member name as a proxy. Imperfect but at least
    relates to the actual cluster content.
    """
    if not members:
        return "Topic"
    return min(members, key=lambda f: len(f.name)).name.split(":")[0].strip()[:30]


def _empty(error: Optional[str]) -> TopicRegionsResult:
    return {
        "regions": [],
        "ungrouped_frame_ids": [],
        "computed_at": datetime.utcnow().isoformat(),
        "error": error,
    }


# ── User-edit support ─────────────────────────────────────────────────────

def update_label(db: Session, persisted_row_id: int, new_label: str) -> Optional[TopicRegionLabel]:
    """Update a label and mark it as user-edited.

    Called from the PUT /api/topic-regions/{id}/label endpoint. After
    update, invalidates BOTH the topic-regions cache and the established-
    landscape cache (which composes the labels into its response and
    caches the whole thing).
    """
    row = db.get(TopicRegionLabel, persisted_row_id)
    if not row:
        return None
    row.label = _clean_label(new_label) or row.label
    row.edited_by_user = True
    db.commit()
    invalidate_cache()
    # Also bust the upstream cache so the next GET /landscape-established
    # rebuilds with the new label.
    try:
        from app.services.narrative_landscape_established import invalidate_cache as _inv_landscape
        _inv_landscape()
    except Exception:
        pass
    return row
