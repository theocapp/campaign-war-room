"""Simple story clustering for duplicate and near-duplicate source items."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from sqlalchemy.orm import Session

from app.models import SourceItem, StoryCluster

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "the", "to", "with", "will", "new", "says", "said",
    "over", "after", "before", "about", "primary", "election",
}
_SOURCE_SUFFIX = re.compile(r"\s+[-|:]\s+[^-|:]{2,60}$")


def normalize_title(title: str | None) -> str:
    text = _SOURCE_SUFFIX.sub("", title or "").lower()
    tokens = [
        t for t in re.split(r"[^a-z0-9]+", text)
        if len(t) > 2 and t not in _STOPWORDS
    ]
    return " ".join(tokens)


def title_similarity(left: str | None, right: str | None) -> float:
    a = set(normalize_title(left).split())
    b = set(normalize_title(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def url_family(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host or None


def _published_close(a: datetime | None, b: datetime | None, days: int = 7) -> bool:
    if not a or not b:
        return True
    return abs((a - b).days) <= days


def is_near_duplicate(candidate: SourceItem, existing: SourceItem) -> bool:
    similarity = title_similarity(candidate.title, existing.title)
    if similarity >= 0.92:
        return True

    same_source_family = bool(url_family(candidate.source_url) and url_family(candidate.source_url) == url_family(existing.source_url))
    same_source_name = bool(
        candidate.source_name and existing.source_name
        and candidate.source_name.strip().lower() == existing.source_name.strip().lower()
    )
    if similarity >= 0.72 and (same_source_family or same_source_name) and _published_close(candidate.published_at, existing.published_at):
        return True

    return False


def assign_story_cluster(db: Session, item: SourceItem) -> SourceItem:
    """Assign a stable cluster id. Duplicates point at the earliest matching source."""
    if item.story_cluster_id:
        return item

    candidates = (
        db.query(SourceItem)
        .filter(SourceItem.id != item.id)
        .order_by(SourceItem.created_at.asc())
        .limit(300)
        .all()
    )
    for existing in candidates:
        if is_near_duplicate(item, existing):
            root = existing.story_cluster_id or f"source-{existing.id}"
            existing.story_cluster_id = root
            item.story_cluster_id = root
            item.duplicate_of_source_id = existing.duplicate_of_source_id or existing.id
            return item

    item.story_cluster_id = f"source-{item.id}"
    item.duplicate_of_source_id = None
    return item


# ── SimHash + cluster-native assignment (Phase A) ─────────────────────────────

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "share", "sharedfrom", "amp",
}


def canonical_url(url: str | None) -> str | None:
    """Strip tracking params and normalize host/scheme so wire syndication shows
    up as the same canonical URL across outlet RSS feeds."""
    if not url:
        return None
    try:
        parts = urlparse(url)
    except Exception:
        return None
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (k, v) for (k, v) in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    rebuilt = urlunparse((
        parts.scheme.lower() or "https",
        host,
        parts.path.rstrip("/"),
        "",
        urlencode(sorted(query)),
        "",
    ))
    return rebuilt or None


def _tokens_for_hash(text: str | None) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _shingles(tokens: list[str], k: int = 4) -> list[str]:
    if len(tokens) < k:
        return tokens[:]
    return [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]


def simhash64(text: str | None) -> int:
    """64-bit SimHash over 4-word shingles. Returns 0 on empty input."""
    shingles = _shingles(_tokens_for_hash(text))
    if not shingles:
        return 0
    vec = [0] * 64
    for sh in shingles:
        h = int(hashlib.blake2b(sh.encode("utf-8"), digest_size=8).hexdigest(), 16)
        for bit in range(64):
            vec[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit in range(64):
        if vec[bit] > 0:
            out |= 1 << bit
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _hex_to_int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s, 16)
    except (ValueError, TypeError):
        return None


def _body_token_count(item: SourceItem) -> int:
    return len(_tokens_for_hash(item.raw_text or ""))


def _select_representative(db: Session, member_ids: list[int]) -> SourceItem:
    """Choose the best representative article from a list of source_item ids.

    Top-down, first satisfied wins:
      1. Highest Outlet.authority_score (NULL outlet → floor of 1)
      2. Longest body text (tiebreaker)
      3. Earliest published_at (final fallback)

    Entity-density tiebreaker from the plan is deferred — wire it in Phase B
    when race_relevance signal extraction is reused at backfill time.
    """
    from app.models import Outlet

    items = (
        db.query(SourceItem)
        .filter(SourceItem.id.in_(member_ids))
        .all()
    )
    if not items:
        raise ValueError("no items to choose representative from")

    outlets = {
        o.id: o for o in db.query(Outlet).filter(Outlet.id.in_({i.outlet_id for i in items if i.outlet_id}))
    } if any(i.outlet_id for i in items) else {}

    def _rank(it: SourceItem) -> tuple:
        outlet = outlets.get(it.outlet_id) if it.outlet_id else None
        authority = outlet.authority_score if outlet else 1
        length = len(it.raw_text or "")
        pub_ts = (it.published_at or it.ingested_at or it.created_at or datetime.utcnow()).timestamp()
        # Higher authority wins → negate. Longer wins → negate. Earlier pub wins.
        return (-authority, -length, pub_ts)

    items.sort(key=_rank)
    return items[0]


def _short_summary(item: SourceItem, max_chars: int = 400) -> str | None:
    text = item.summary or item.raw_text or ""
    if not text:
        return None
    return text[:max_chars]


def _ensure_id_format(item: SourceItem) -> str:
    """Phase A keeps the legacy 'source-{N}' id scheme so cluster ids written
    by either path are interchangeable. Phase B's backfill uses the same."""
    return f"source-{item.id}"


def assign_story_cluster_v2(db: Session, item: SourceItem) -> tuple[StoryCluster, bool, str | None]:
    """Cluster-native assignment.

    Returns (cluster_row, is_new, retrigger_reason_or_None).

    Decision rules, cheap → expensive, evaluated against clusters whose
    last_seen_at falls within CLUSTER_WINDOW_DAYS (default 14):

      1. URL canonical match → same cluster.
      2. Title Jaccard ≥ 0.92 → same cluster.
      3. Title Jaccard ≥ CLUSTER_TITLE_JACCARD_MIN AND SimHash Hamming ≤
         CLUSTER_SIMHASH_HAMMING_MAX AND published within 7 days → same cluster.
      4. Otherwise → create new cluster.

    Short-text fallback: items with <50 body tokens skip rule 3 (body simhash
    unreliable) — they need title-only proof.

    Side-effects: writes item.story_cluster_id; creates / updates the
    StoryCluster row; returns retrigger reason for the caller to act on.
    Phase A logs the reason but does not act on it (per "no behavior change"
    constraint).
    """
    window_days = int(os.environ.get("CLUSTER_WINDOW_DAYS", "14"))
    jaccard_min = float(os.environ.get("CLUSTER_TITLE_JACCARD_MIN", "0.65"))
    hamming_max = int(os.environ.get("CLUSTER_SIMHASH_HAMMING_MAX", "6"))

    cutoff = datetime.utcnow() - timedelta(days=window_days)

    item_canonical = canonical_url(item.source_url)
    item_hash = simhash64(item.raw_text or item.title)
    item_tokens = _body_token_count(item)
    short_text = item_tokens < 50

    # Pull recent cluster candidates joined to their representative article
    # so we have a title + body simhash + pub date to compare against.
    candidates = (
        db.query(StoryCluster, SourceItem)
        .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
        .filter(StoryCluster.last_seen_at >= cutoff)
        .order_by(StoryCluster.last_seen_at.desc())
        .limit(500)
        .all()
    )

    matched: StoryCluster | None = None
    for cluster, rep in candidates:
        # Rule 1: URL canonical match
        if item_canonical and canonical_url(rep.source_url) == item_canonical:
            matched = cluster
            break
        # Rule 2: very-high-similarity title
        sim = title_similarity(item.title, rep.title)
        if sim >= 0.92:
            matched = cluster
            break
        # Rule 3: mid-similarity title + body-hash proximity + temporal proximity
        if not short_text and sim >= jaccard_min:
            cluster_hash = _hex_to_int(cluster.simhash_64)
            if cluster_hash is not None and hamming(item_hash, cluster_hash) <= hamming_max:
                if _published_close(item.published_at, rep.published_at, days=7):
                    matched = cluster
                    break

    if matched is not None:
        retrigger = _attach_to_cluster(db, item, matched)
        if retrigger:
            logger.info(
                "cluster v2: attach item=%d cluster=%s retrigger=%s",
                item.id, matched.id, retrigger,
            )
        return matched, False, retrigger

    # New cluster path.
    cluster = _create_cluster(db, item, item_hash)
    return cluster, True, None


def _create_cluster(db: Session, item: SourceItem, item_hash: int) -> StoryCluster:
    cluster_id = _ensure_id_format(item)
    now = datetime.utcnow()
    cluster = StoryCluster(
        id=cluster_id,
        seed_source_item_id=item.id,
        representative_source_item_id=item.id,
        # analysis_anchor_* stays NULL until the LLM run writes it (Phase A
        # ingestion does not write the anchor since per-article LLM is still
        # the policy; Phase D will).
        title_representative=item.title,
        summary_representative=_short_summary(item),
        simhash_64=f"{item_hash:016x}" if item_hash else None,
        first_seen_at=item.published_at or item.ingested_at or now,
        last_seen_at=item.published_at or item.ingested_at or now,
        article_count=1,
        outlet_count=1 if item.outlet_id else 0,
        source_diversity_score=0.0,
        known_entities=None,
        dormant_since=None,
    )
    db.add(cluster)
    db.flush()
    item.story_cluster_id = cluster_id
    return cluster


def _attach_to_cluster(db: Session, item: SourceItem, cluster: StoryCluster) -> str | None:
    """Attach item as evidence; update aggregates; recompute representative.

    Returns a retrigger reason string when a semantic trigger fires, or None.
    Phase A logs the reason; the caller does NOT skip/repeat the LLM call
    based on it (behavior parity with the legacy path).
    """
    item.story_cluster_id = cluster.id

    # Aggregates
    prev_article_count = cluster.article_count or 1
    cluster.article_count = prev_article_count + 1
    now = datetime.utcnow()
    obs_ts = item.published_at or item.ingested_at or now
    if obs_ts > (cluster.last_seen_at or datetime.min):
        cluster.last_seen_at = obs_ts
    if not cluster.first_seen_at or obs_ts < cluster.first_seen_at:
        cluster.first_seen_at = obs_ts

    # Distinct outlet count
    if item.outlet_id:
        distinct_outlets = (
            db.query(SourceItem.outlet_id)
            .filter(SourceItem.story_cluster_id == cluster.id, SourceItem.outlet_id.isnot(None))
            .distinct()
            .count()
        )
        # +1 because this item isn't yet persisted with cluster_id at commit time
        # — but SQLAlchemy will see the assignment within this session's identity map.
        cluster.outlet_count = max(cluster.outlet_count or 0, distinct_outlets)

    # Re-run representative selection across all known members (including this one).
    member_ids = [
        r[0] for r in db.query(SourceItem.id)
        .filter(SourceItem.story_cluster_id == cluster.id)
        .all()
    ]
    if item.id not in member_ids:
        member_ids.append(item.id)
    new_rep = _select_representative(db, member_ids)
    rep_changed = new_rep.id != cluster.representative_source_item_id
    cluster.representative_source_item_id = new_rep.id
    cluster.title_representative = new_rep.title
    cluster.summary_representative = _short_summary(new_rep)
    # Recompute the representative simhash so future Hamming comparisons stay
    # in sync with whichever article is the current best rep.
    cluster.simhash_64 = f"{simhash64(new_rep.raw_text or new_rep.title):016x}"

    # Resurrection — was dormant, just got woken up.
    dormant_days = int(os.environ.get("CLUSTER_RETRIGGER_DORMANT_DAYS", "30"))
    if cluster.dormant_since is not None and (now - cluster.dormant_since).days > dormant_days:
        return "resurrection"

    # Authority promotion — representative jumped to a strictly higher tier
    # than the article whose text anchored the most recent LLM analysis.
    if rep_changed and cluster.analysis_anchor_source_item_id:
        from app.models import Outlet
        anchor = db.query(SourceItem).filter_by(id=cluster.analysis_anchor_source_item_id).first()
        if anchor:
            anchor_auth = 1
            new_auth = 1
            if anchor.outlet_id:
                o = db.query(Outlet).filter_by(id=anchor.outlet_id).first()
                if o:
                    anchor_auth = o.authority_score or 1
            if new_rep.outlet_id:
                o = db.query(Outlet).filter_by(id=new_rep.outlet_id).first()
                if o:
                    new_auth = o.authority_score or 1
            if new_auth > anchor_auth:
                return "authority_promotion"

    # Size-milestone fallback — bounded fallback for an organically growing
    # cluster that didn't trip any semantic trigger.
    size_thresholds_env = os.environ.get("CLUSTER_RETRIGGER_SIZE_FALLBACK", "15,40")
    try:
        thresholds = sorted({int(s) for s in size_thresholds_env.split(",") if s.strip()})
    except ValueError:
        thresholds = [15, 40]
    for t in thresholds:
        if prev_article_count < t <= cluster.article_count:
            return f"size_milestone_{t}"

    return None


def known_entities_list(cluster: StoryCluster) -> list[str]:
    """Deserialize StoryCluster.known_entities (JSON list) safely."""
    if not cluster.known_entities:
        return []
    try:
        v = json.loads(cluster.known_entities)
        return [str(x) for x in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def add_known_entities(cluster: StoryCluster, entities: list[str]) -> None:
    """Merge new entity strings into cluster.known_entities (in-place).

    No retrigger logic here — caller decides whether any of these are salient.
    """
    existing = set(known_entities_list(cluster))
    for e in entities:
        s = (e or "").strip()
        if s:
            existing.add(s)
    cluster.known_entities = json.dumps(sorted(existing))


def unique_by_cluster(items: list[SourceItem]) -> list[SourceItem]:
    seen: set[str] = set()
    unique: list[SourceItem] = []
    for item in items:
        key = item.story_cluster_id or f"source-{item.id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
