import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, func, not_, or_
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import (
    CampaignConfig,
    FeaturedAppearance,
    Issue,
    IssueMention,
    NarrativeFrame,
    NarrativeFrameMention,
    Opponent,
    OpponentActivity,
    Outlet,
    SourceItem,
)
from app.services import briefing_summary as briefing_svc
from app.services.briefing_retrieval import (
    overnight_changes,
    top_claims_for_briefing,
    top_entities_for_briefing,
)
from app.services.source_category import categorize as categorize_source
from app.services.source_display import display_source_name, preload_outlets


def _safe_json_list(raw):
    """Parse a JSON-array column; return [] for null/empty/malformed values."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _safe_json_obj(raw):
    """Parse a JSON-object column; return None for null/empty/malformed values."""
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _compute_spikes(db: Session) -> list[dict]:
    """Delegates to the canonical cluster-based / reach-weighted spike detector.

    The previous implementation here used the legacy NarrativeFrameMention
    table and raw mention counts, which silently returned stale data once the
    pipeline moved to FrameClusterMatch + weighted reach. Now shares the same
    logic as /analytics/spikes.
    """
    from app.routes.analytics import detect_spike_alerts
    return detect_spike_alerts(db)

router = APIRouter()


def _item_dict(item: SourceItem, outlet: Outlet | None = None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "summary": item.summary,
        "source_name": display_source_name(item, outlet),
        "source_url": item.source_url,
        "source_type": item.source_type,
        "platform": item.platform,
        "source_category": categorize_source(item, outlet),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "race_relevance_score": item.race_relevance_score,
        "race_relevance_label": item.race_relevance_label,
        "actionability_label": item.actionability_label,
        "sentiment": item.sentiment,
        "perspective": item.perspective,
        "framing": getattr(item, "framing", None),
    }


def _duplicate_dict(item: SourceItem, outlet: Outlet | None = None) -> dict:
    return {
        "id": item.id,
        "source_name": display_source_name(item, outlet),
        "source_url": item.source_url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _group_by_normalized_title(
    items: list[SourceItem],
    window_hours: int = 24,
) -> list[tuple[SourceItem, list[SourceItem]]]:
    """Group items whose normalized titles match exactly AND that publish within
    `window_hours` of each other. Returns [(representative, [other_versions])].

    "Normalized title" reuses the same cleanup the story-clustering pipeline
    uses: trailing outlet suffix stripped, lowercased, punctuation removed,
    stopwords dropped. So "Trump signs bill — AP" and "Trump signs bill |
    Reuters" land in the same bucket but "Trump signs bill" and "Biden signs
    bill" do not.

    Representative is the highest race_relevance_score in the group, breaking
    ties by longer body, then earliest published_at — so the row the user sees
    is the strongest version of the story.

    Items with empty normalized titles (junk like "Instagram", emoji-only,
    placeholder rows) are each kept as their own group rather than collapsed
    together — those are bugs to fix elsewhere, not wire-syndication.
    """
    from app.services.story_clustering import normalize_title

    buckets: dict[str, list[SourceItem]] = {}
    for it in items:
        key = normalize_title(it.title)
        if not key:
            buckets[f"_unique_{it.id}"] = [it]
            continue
        buckets.setdefault(key, []).append(it)

    result: list[tuple[SourceItem, list[SourceItem]]] = []
    window_seconds = window_hours * 3600
    for bucket in buckets.values():
        if len(bucket) == 1:
            result.append((bucket[0], []))
            continue

        bucket.sort(key=lambda i: i.published_at or i.created_at or datetime.min)
        windows: list[list[SourceItem]] = []
        current: list[SourceItem] = []
        window_start: datetime | None = None
        for it in bucket:
            ts = it.published_at or it.created_at
            if not current:
                current = [it]
                window_start = ts
                continue
            if ts and window_start and (ts - window_start).total_seconds() <= window_seconds:
                current.append(it)
            else:
                windows.append(current)
                current = [it]
                window_start = ts
        if current:
            windows.append(current)

        for window in windows:
            window.sort(key=lambda i: (
                -(i.race_relevance_score or 0),
                -(len(i.raw_text or "")),
                (i.published_at or i.created_at or datetime.max),
            ))
            result.append((window[0], window[1:]))

    return result


def _is_llm_scored(item: SourceItem) -> bool:
    """Return False for articles whose summary looks like a raw RSS excerpt (not LLM-generated)."""
    s = item.summary or ""
    if "<" in s:
        return False
    if "[...]" in s:
        return False
    if " —" in s and ("(WB" in s or "(WN" in s or "(WY" in s or "(AP)" in s):
        return False
    return True


def _cited_frames_from_memo(db: Session, memo) -> list[dict]:
    """Frames whose articles are cited in the grounded race memo.

    For each cited article, returns the top-confidence frame match(es);
    ties at the max confidence are all included (so an article matching
    two frames at 90 confidence pins both). A frame cited via multiple
    articles appears once with all cited_article_ids attached.

    Returns [] when the memo has no citations or none of its articles
    have active frame matches.
    """
    if not isinstance(memo, dict):
        return []
    citations = memo.get("citations") or []
    article_ids = list({c["article_id"] for c in citations if c.get("article_id")})
    if not article_ids:
        return []
    rows = (
        db.query(
            NarrativeFrameMention.source_item_id,
            NarrativeFrameMention.frame_id,
            NarrativeFrameMention.confidence,
            NarrativeFrame.name,
        )
        .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
        .filter(NarrativeFrameMention.source_item_id.in_(article_ids))
        .filter(NarrativeFrame.active == True)  # noqa: E712
        .all()
    )
    # Group by article, then keep only rows at the article's max confidence.
    per_article: dict[int, list[tuple[int, int, str]]] = {}
    for source_item_id, frame_id, confidence, frame_name in rows:
        per_article.setdefault(source_item_id, []).append(
            (frame_id, confidence, frame_name)
        )
    pinned: dict[int, dict] = {}
    for source_item_id, entries in per_article.items():
        max_conf = max(e[1] for e in entries)
        for frame_id, confidence, frame_name in entries:
            if confidence != max_conf:
                continue
            slot = pinned.setdefault(frame_id, {
                "frame_id": frame_id,
                "frame_name": frame_name,
                "confidence": confidence,
                "cited_article_ids": [],
            })
            slot["cited_article_ids"].append(source_item_id)
    return list(pinned.values())


@router.get("/campaign/names")
def get_campaign_names(db: Session = Depends(get_db)):
    """Return the configured candidate + opponent display names.

    Used by the Articles-page sentiment filter to label its dropdown
    buckets dynamically ("pro-{candidate first name}", etc.) instead of
    hardcoding names — keeps the UI SaaS-ready across campaigns.

    Names come from CampaignConfig (set in /setup) and the first Opponent
    row. Falls back to None when nothing is configured yet so the
    frontend can degrade to generic "pro-candidate / pro-opponent" labels.
    """
    config = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    return {
        "candidate_name": config.candidate_name if config else None,
        "opponent_name": opp.name if opp else None,
    }


@router.get("/briefing/morning")
def get_morning_briefing(
    db: Session = Depends(get_db),
    v: int = 1,
):
    """
    Single-page briefing: new articles, narrative pulse, needs-response, LLM race-situation memo.

    Query params:
      v=1 (default) — legacy prose memo (`race_memo` is a string).
      v=2           — grounded memo (`race_memo` is an object with text +
                      citations + sources_used) + `top_entities` activity card.
    """
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    cutoff_48h = datetime.utcnow() - timedelta(hours=48)
    cutoff_7d = datetime.utcnow() - timedelta(days=7)
    cutoff_14d = datetime.utcnow() - timedelta(days=14)

    # Section 1 — Needs a response right now (published in last 48h).
    #
    # `actionability_label == 'respond'` is set by an LLM `framing` classifier
    # (campaign_analysis.framing_to_action) — it's over-inclusive. False
    # positives we've observed:
    #   1. The candidate's OWN social posts (her tweet restating her message —
    #      she doesn't need to "respond" to her own message)
    #   2. Friendly local news (e.g. "Construction begins on D&L Trail" — gets
    #      tagged respond because it mentions the district and is positive)
    #   3. National-trend pieces about other races that happen to mention
    #      a tracked entity (filtered by content_category != 'irrelevant')
    #
    # Layered filters below address all three:
    #   - content_category != 'irrelevant' kills (3)
    #   - source_owner_type NOT IN (candidate_statement, community/manual)
    #     kills (1) — the candidate's own statements and her social channels
    #   - exclude (party_committee_statement AND content_category=campaign)
    #     kills (2) — friendly party-tagged + campaign-content combo (e.g.
    #     a local outlet quoting a campaign press release)
    #
    # Note we deliberately keep party_committee_statement when content_category
    # is something OTHER than 'campaign' — that's where opposition committee
    # attacks (e.g. NRCC) tend to land.
    respond = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.content_category != "irrelevant",
            SourceItem.published_at >= cutoff_48h,
            SourceItem.actionability_label == "respond",
            SourceItem.source_owner_type.notin_(
                ["candidate_statement", "community/manual"]
            ),
            not_(and_(
                SourceItem.source_owner_type == "party_committee_statement",
                SourceItem.content_category == "campaign",
            )),
        )
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(5)
        .all()
    )

    # Section 2 — Most recent race-relevant articles (no time-window
    # filter; just the freshest N by published_at). Same content_category
    # exclusion as needs_response — we don't want national-trend pieces
    # crowding the local briefing.
    respond_ids = {i.id for i in respond}
    new_articles_raw = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.content_category != "irrelevant",
            SourceItem.race_relevance_score >= 50,
            SourceItem.published_at.isnot(None),
        )
        .order_by(SourceItem.published_at.desc())
        .limit(50)
        .all()
    )

    new_articles = [
        a for a in new_articles_raw
        if a.id not in respond_ids and _is_llm_scored(a)
    ][:5]

    # Section 3 — Narrative pulse. Uses the canonical Option-C definition
    # (distinct clusters with any article in the window) via the shared
    # frame_counts helper. Replaces a 2N-query loop over NarrativeFrameMention
    # — that table is largely stale since Phase D and produced "0 this week"
    # for nearly every frame even when coverage was active.
    from app.services.frame_counts import frame_pulse_counts
    from app.services.subject_classifier import get_subject_classifier
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712
    pulse_counts = frame_pulse_counts(db, [f.id for f in frames])
    _classify_subject = get_subject_classifier(db)
    pulse = [
        {
            "id": f.id,
            "name": f.name,
            "owner_type": f.owner_type,
            "subject_type": _classify_subject(f.name),  # V13.21
            "this_week": pulse_counts[f.id][0],
            "last_week": pulse_counts[f.id][1],
        }
        for f in frames
    ]
    pulse.sort(key=lambda x: x["this_week"], reverse=True)

    # Meta — ingested uses created_at, relevant uses published_at + same quality filter as new_articles
    total_today = (
        db.query(func.count(SourceItem.id))
        .filter(SourceItem.created_at >= cutoff_24h)
        .scalar()
    )
    relevant_candidates = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False,  # noqa: E712
                SourceItem.content_category != "irrelevant",
                SourceItem.published_at >= cutoff_48h,
                SourceItem.race_relevance_score >= 50)
        .all()
    )
    relevant_today = sum(1 for i in relevant_candidates if _is_llm_scored(i))

    # LLM race-situation memo
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).limit(3).all()
    outlets_map = preload_outlets(db, respond + new_articles)
    all_articles = (
        [_item_dict(i, outlets_map.get(i.outlet_id)) for i in respond]
        + [_item_dict(i, outlets_map.get(i.outlet_id)) for i in new_articles]
    )

    response: dict = {
        "generated_at": datetime.utcnow().isoformat(),
        "meta": {
            "total_articles_today": total_today,
            "relevant_articles_today": relevant_today,
        },
        "needs_response": [_item_dict(i, outlets_map.get(i.outlet_id)) for i in respond],
        "new_articles": [_item_dict(i, outlets_map.get(i.outlet_id)) for i in new_articles],
        "narrative_pulse": pulse,
        "spike_alerts": _compute_spikes(db),
    }

    if v == 2:
        # Grounded memo + structured retrieval. The frontend renders this
        # with citation superscript links and a "Sources used" expandable.
        top_claims = top_claims_for_briefing(db, days=7, limit=15)
        race_memo = briefing_svc.get_or_generate_grounded(
            db, all_articles, campaign, opponents, top_claims
        )
        response["race_memo"] = race_memo
        response["top_entities"] = top_entities_for_briefing(db, days=7)
        # "What changed in the race" — labeled candidate-specific claims
        # from the last 48h. May be empty in quiet windows; frontend hides.
        response["overnight_changes"] = overnight_changes(db)
        # Frames cited in the memo — top-confidence frame(s) per cited
        # article, ties included. Powers Dashboard "pinning": every frame
        # in this list is guaranteed a slot in Featured Narratives, so the
        # editorial memo and the algorithmic featured panel always agree.
        # Empty list when memo has no citations or cited articles have no
        # frame matches.
        response["cited_frames"] = _cited_frames_from_memo(db, race_memo)
    else:
        # v1: legacy prose memo (string). Default for the current frontend.
        response["race_memo"] = briefing_svc.get_or_generate(
            db, all_articles, campaign, opponents
        )

    return response


@router.get("/articles/recent")
def get_recent_relevant_articles(limit: int = 10, db: Session = Depends(get_db)):
    """Most recent race-relevant articles for the Dashboard right rail and
    the full Articles page.

    This is the *confirmed-relevant* feed — only articles that have already
    cleared review (either auto_review approved them, or a human marked them
    reviewed in the queue). Pending review-queue items are intentionally
    excluded so the right rail reflects "what's real and worth your time
    right now," not "what we're still triaging."

    Filters:
        - not archived as irrelevant
        - not user-dismissed
        - reviewed == True  (auto-approved by auto_review OR manually reviewed)
        - LLM-scored (real summary, not raw RSS)
        - race_relevance_score >= 50

    No upper date bound — the Articles page paginates back through the full
    lifetime pool (~2,700 reviewed-relevant articles) via `limit`. Ordered
    by published_at desc so the freshest reviewed items surface first.
    """
    # Over-fetch generously — both the LLM-scored filter AND the per-title
    # grouping below can drop rows, so we want plenty of headroom to still
    # return `limit` distinct stories.
    # COALESCE(published_at, created_at) — Postgres puts NULL first under
    # ORDER BY ... DESC, so a plain published_at sort was burning the
    # over-fetch budget on the 136 reviewed+relevant items that have
    # published_at IS NULL (mostly setup-time seed URLs without a feed-
    # provided publish date). Falling back to created_at here matches the
    # in-Python tiebreaker that runs after grouping.
    recency = func.coalesce(SourceItem.published_at, SourceItem.created_at)
    candidates = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.dismissed == False,               # noqa: E712
            SourceItem.reviewed == True,                 # noqa: E712 — cleared, not in queue
            SourceItem.race_relevance_score >= 50,
        )
        .order_by(recency.desc())
        .limit(limit * 6)
        .all()
    )
    llm_scored = [a for a in candidates if _is_llm_scored(a)]
    groups = _group_by_normalized_title(llm_scored, window_hours=24)
    groups.sort(
        key=lambda g: g[0].published_at or g[0].created_at or datetime.min,
        reverse=True,
    )
    groups = groups[:limit]
    # Preload outlets for both representatives and their duplicates.
    all_items: list[SourceItem] = []
    for rep, dupes in groups:
        all_items.append(rep)
        all_items.extend(dupes)
    outlets_map = preload_outlets(db, all_items)
    # Batch-load narrative frame matches for the representatives only — the
    # client-side Frame filter operates on whichever row is being rendered,
    # which is always the representative. Skipping duplicates keeps the
    # query small.
    rep_ids = [rep.id for rep, _ in groups]
    frames_by_item: dict[int, list[dict]] = {}
    if rep_ids:
        rows = (
            db.query(
                NarrativeFrameMention.source_item_id,
                NarrativeFrame.id,
                NarrativeFrame.name,
            )
            .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
            .filter(NarrativeFrameMention.source_item_id.in_(rep_ids))
            .filter(NarrativeFrame.active == True)  # noqa: E712
            .all()
        )
        for source_item_id, frame_id, frame_name in rows:
            frames_by_item.setdefault(source_item_id, []).append(
                {"id": frame_id, "name": frame_name}
            )
    return {
        "items": [
            {
                **_item_dict(rep, outlets_map.get(rep.outlet_id)),
                "frames": frames_by_item.get(rep.id, []),
                "duplicates": [
                    _duplicate_dict(d, outlets_map.get(d.outlet_id)) for d in dupes
                ],
            }
            for rep, dupes in groups
        ]
    }


@router.get("/articles/{article_id}")
def get_article_detail(article_id: int, db: Session = Depends(get_db)):
    """Everything we know about a single article.

    Powers the article-detail modal opened from the Dashboard's right rail.
    Returns the article plus eagerly-loaded related issues and opponent
    activities (attacks/claims/promises the AI extracted). JSON-blob fields
    (relevance_reasons, gdelt_themes, gdelt_tone, structured_extraction)
    are parsed so the UI doesn't need to know about the wire format.

    404 if the article doesn't exist.
    """
    a = (
        db.query(SourceItem)
        .options(
            joinedload(SourceItem.issue_mentions).joinedload(IssueMention.issue),
            joinedload(SourceItem.opponent_activities).joinedload(OpponentActivity.opponent),
        )
        .filter(SourceItem.id == article_id)
        .first()
    )
    if not a:
        raise HTTPException(status_code=404, detail=f"Article {article_id} not found")

    outlet = db.get(Outlet, a.outlet_id) if a.outlet_id else None
    return {
        "id": a.id,
        "title": a.title,

        # Source / authorship
        "source_name": display_source_name(a, outlet),
        "source_url": a.source_url,
        "source_type": a.source_type,
        "source_author": a.source_author,
        "source_owner_type": a.source_owner_type,
        "source_owner_confidence": a.source_owner_confidence,
        "publisher_domain": a.publisher_domain,

        # Timestamps
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "ingested_at": a.ingested_at.isoformat() if a.ingested_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,

        # Body
        "summary": a.summary,
        "raw_text": a.raw_text,

        # Scoring
        "race_relevance_score": a.race_relevance_score,
        "race_relevance_label": a.race_relevance_label,
        "relevance_reasons": _safe_json_list(a.relevance_reasons),
        "actionability_score": a.actionability_score,
        "actionability_label": a.actionability_label,
        "priority_score": a.priority_score,
        "urgency": a.urgency,
        "sentiment": a.sentiment,
        "content_category": a.content_category,
        "geo_relevance": a.geo_relevance,

        # Mentions / flags
        "candidate_mentioned": a.candidate_mentioned,
        "opponent_mentioned": a.opponent_mentioned,
        "district_mentioned": a.district_mentioned,
        "priority_issue_mentioned": a.priority_issue_mentioned,

        # Perspective classifier
        "perspective": a.perspective,
        "perspective_method": a.perspective_method,
        "perspective_confidence": a.perspective_confidence,
        "perspective_reason": a.perspective_reason,

        # Credibility / extraction quality
        "credibility_score": a.credibility_score,
        "source_credibility": a.source_credibility,
        "credibility_note": a.credibility_note,
        "extraction_quality_score": a.extraction_quality_score,
        "extraction_quality_label": a.extraction_quality_label,
        "extraction_quality_reasons": _safe_json_list(a.extraction_quality_reasons),

        # GDELT-derived data (when ingested from BigQuery)
        "gdelt_themes": _safe_json_list(a.gdelt_themes),
        "gdelt_tone": _safe_json_obj(a.gdelt_tone),

        # Full LLM analysis result (when present). Shape varies — the UI
        # renders it generically since it can include framing, claim
        # extracts, and other LLM-extracted bits beyond what we've
        # promoted to first-class columns.
        "structured_extraction": _safe_json_obj(a.structured_extraction),

        # `framing` is the LLM's judgment of how the article positions our
        # candidate — helps_candidate / hurts_candidate / opponent_news /
        # background / irrelevant. Surfaced top-level so the UI can show
        # it prominently instead of digging into structured_extraction.
        # This is a more precise signal than `perspective` (which only has
        # 3 buckets and is computed by a separate cascading classifier
        # primarily for landscape dot color).
        "framing": (
            (_safe_json_obj(a.structured_extraction) or {}).get("framing")
        ),

        # Lifecycle
        "reviewed": a.reviewed,
        "dismissed": a.dismissed,
        "archived_as_irrelevant": a.archived_as_irrelevant,
        "review_note": a.review_note,

        # Related issues (with link strength + reasons the AI flagged them)
        "issue_mentions": [
            {
                "issue_id": im.issue_id,
                "name": im.issue.name if im.issue else None,
                "summary": im.issue.summary if im.issue else None,
                "link_strength": im.link_strength,
                "link_reasons": _safe_json_list(im.link_reasons),
            }
            for im in a.issue_mentions
        ],

        # Opponent activities extracted from this article — claims,
        # attacks, promises. The Opponent UI uses these too.
        "opponent_activities": [
            {
                "id": oa.id,
                "opponent_id": oa.opponent_id,
                "opponent_name": oa.opponent.name if oa.opponent else None,
                "claim": oa.claim,
                "attack": oa.attack,
                "promise": oa.promise,
                "contradiction_note": oa.contradiction_note,
                "repeated_theme": oa.repeated_theme,
                "created_at": oa.created_at.isoformat() if oa.created_at else None,
            }
            for oa in a.opponent_activities
        ],
    }


# ─── Featured-narratives appearance log ───────────────────────────────────
# Frontend POSTs the IDs of frames it just rendered in the homepage's
# Featured Narratives panel. We record one row per (frame_id, today)
# using ON CONFLICT DO NOTHING so repeat renders on the same day are
# idempotent. The count of rows per frame in the last 7 days drives the
# saturation-penalty term in the frontend's multi-objective score —
# frames that stay featured 3+ days running get demoted unless their
# urgency keeps them at the top anyway.
#
# Not admin-gated: this is per-visit telemetry, not a write that costs
# money or changes campaign state. Any authenticated session can log.

from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import date as _date


class FeaturedAppearanceIn(BaseModel):
    frame_ids: list[int]


@router.post("/dashboard/featured-appearance")
def log_featured_appearance(
    payload: FeaturedAppearanceIn,
    db: Session = Depends(get_db),
):
    if not payload.frame_ids:
        return {"recorded": 0}
    # Dedupe in case the same id is sent twice in a single POST.
    unique_ids = sorted(set(payload.frame_ids))
    today = _date.today()
    rows = [{"frame_id": fid, "appeared_on": today} for fid in unique_ids]
    stmt = pg_insert(FeaturedAppearance).values(rows)
    stmt = stmt.on_conflict_do_nothing(constraint="uq_featured_frame_day")
    db.execute(stmt)
    db.commit()
    return {"recorded": len(unique_ids), "day": today.isoformat()}
