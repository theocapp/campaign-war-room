"""Ingestion helpers for RSS, URL, text, and CSV sources."""
import csv
import io
import re
from datetime import datetime
from typing import Optional

import feedparser
import httpx
from sqlalchemy.orm import Session

from app.models import CanvassingNote, IssueMention, OpponentActivity, SourceItem
from app.services import intelligence, issue_clustering, opponent_analysis, scoring


# ── HTML cleaning ─────────────────────────────────────────────────────────────

_BLOCK_TAGS = re.compile(
    r'<(script|style|noscript|nav|header|footer|aside|form)[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')
_HTML_ENTITIES = {
    '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"',
    '&#39;': "'", '&nbsp;': ' ', '&ndash;': '–', '&mdash;': '—',
    '&lsquo;': "'", '&rsquo;': "'", '&ldquo;': '"', '&rdquo;': '"',
}


def _clean_html(html: str) -> tuple[str, str]:
    """
    Return (title, body_text) extracted from raw HTML.
    Strips noise tags, decodes entities, collapses whitespace.
    """
    # Extract <title>
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    raw_title = title_match.group(1).strip() if title_match else ""

    # Try to isolate the main content area first
    main_match = re.search(
        r'<(article|main|div[^>]+(?:class|id)=["\'][^"\']*(?:content|article|body|story|post)[^"\']*["\'])[^>]*>(.*?)</\1>',
        html, re.DOTALL | re.IGNORECASE,
    )
    working_html = main_match.group(2) if main_match else html

    # Remove noisy block tags
    working_html = _BLOCK_TAGS.sub(' ', working_html)
    # Strip all remaining tags
    text = _TAG_STRIP.sub(' ', working_html)
    # Decode entities
    for entity, replacement in _HTML_ENTITIES.items():
        text = text.replace(entity, replacement)
    # Decode numeric entities
    text = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), text)
    # Collapse whitespace
    text = _WHITESPACE.sub(' ', text).strip()

    # Clean the title too
    title = _TAG_STRIP.sub('', raw_title)
    for entity, replacement in _HTML_ENTITIES.items():
        title = title.replace(entity, replacement)
    title = _WHITESPACE.sub(' ', title).strip()

    return title, text[:4000]


# ── Core analyze-and-save pipeline ───────────────────────────────────────────

def _compute_priority_score(db: Session, item: SourceItem) -> int:
    score = 0
    if item.urgency == "high":
        score += 30
    elif item.urgency == "medium":
        score += 10
    if db.query(IssueMention).filter_by(source_item_id=item.id).count():
        score += 10
    if db.query(OpponentActivity).filter(OpponentActivity.source_item_id == item.id).count():
        score += 20
    if item.credibility_note:
        score += 15
    if item.published_at:
        age = max(0, (datetime.utcnow() - item.published_at).days)
        if age <= 3:
            score += 10
        elif age <= 7:
            score += 5
    return score


def _create_and_analyze(db: Session, item: SourceItem) -> SourceItem:
    if not item.summary and item.raw_text:
        item.summary = intelligence.summarize_source(item.raw_text)
    if not item.urgency or item.urgency == "low":
        item.urgency = intelligence.classify_urgency(f"{item.title} {item.raw_text or ''}")
    db.add(item)
    db.commit()
    db.refresh(item)
    issue_clustering.assign_issues_to_source(db, item)
    opponent_analysis.analyze_source_for_opponents(db, item)
    item.priority_score = _compute_priority_score(db, item)
    item.evidence_score = scoring.compute_evidence_score(item)
    item.credibility_score = scoring.compute_credibility_score(item)
    db.commit()
    return item


# ── Public ingestion functions ────────────────────────────────────────────────

def ingest_text(
    db: Session,
    title: str,
    raw_text: str,
    source_name: str,
    source_type: str,
    source_url: Optional[str] = None,
    published_at: Optional[datetime] = None,
) -> SourceItem:
    item = SourceItem(
        title=title,
        raw_text=raw_text,
        source_name=source_name,
        source_type=source_type,
        source_url=source_url,
        published_at=published_at or datetime.utcnow(),
    )
    return _create_and_analyze(db, item)


def ingest_url(db: Session, url: str, source_type: str) -> Optional[SourceItem]:
    # Dedup by URL
    existing = db.query(SourceItem).filter_by(source_url=url).first()
    if existing:
        return existing

    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)"
        })
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" in content_type:
            title, body_text = _clean_html(resp.text)
        else:
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            body_text = resp.text[:4000]

        if not title:
            # Fall back to URL slug
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").replace("_", " ").title() or url

        item = SourceItem(
            title=title[:200],
            raw_text=body_text,
            source_url=url,
            source_name=url.split("/")[2] if "://" in url else url[:50],
            source_type=source_type,
            published_at=datetime.utcnow(),
        )
        return _create_and_analyze(db, item)
    except Exception:
        return None


class RSSIngestResult:
    def __init__(self, added: int, skipped: int, items: list[SourceItem]):
        self.added = added
        self.skipped = skipped
        self.items = items


def ingest_rss(db: Session, feed_url: str, label: Optional[str] = None) -> RSSIngestResult:
    feed = feedparser.parse(feed_url)
    added_items: list[SourceItem] = []
    skipped = 0

    for entry in feed.entries[:20]:
        url = entry.get("link") or ""
        title = (entry.get("title") or "Untitled")[:200]

        # Deduplicate by source_url
        if url and db.query(SourceItem).filter_by(source_url=url).first():
            skipped += 1
            continue

        raw_text = (entry.get("summary") or entry.get("description") or "")[:4000]

        published: Optional[datetime] = None
        if getattr(entry, "published_parsed", None):
            import time
            try:
                published = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            except (OSError, OverflowError):
                published = None

        item = SourceItem(
            title=title,
            raw_text=raw_text,
            source_url=url or None,
            source_name=label or feed.feed.get("title", feed_url),
            source_type="news",
            published_at=published or datetime.utcnow(),
        )
        created = _create_and_analyze(db, item)
        added_items.append(created)

    return RSSIngestResult(added=len(added_items), skipped=skipped, items=added_items)


def ingest_canvassing_csv(db: Session, csv_content: str) -> int:
    reader = csv.DictReader(io.StringIO(csv_content))
    count = 0
    for row in reader:
        precinct = (row.get("precinct") or "").strip()
        if not precinct:
            continue
        date: Optional[datetime] = None
        date_str = (row.get("date") or "").strip()
        if date_str:
            try:
                from dateutil import parser as dp
                date = dp.parse(date_str)
            except Exception:
                date = datetime.utcnow()
        note = CanvassingNote(
            voter_name=(row.get("voter_name") or "").strip() or None,
            address=(row.get("address") or "").strip() or None,
            precinct=precinct,
            issue=(row.get("issue") or "").strip() or None,
            sentiment=(row.get("sentiment") or "neutral").strip(),
            notes=(row.get("notes") or "").strip() or None,
            date=date or datetime.utcnow(),
        )
        db.add(note)
        count += 1
    db.commit()
    return count
