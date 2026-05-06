"""Simple story clustering for duplicate and near-duplicate source items."""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import SourceItem

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
