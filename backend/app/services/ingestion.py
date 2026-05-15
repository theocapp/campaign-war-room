"""Ingestion helpers for RSS, URL, text, and CSV sources."""
import csv
import html as _html
import io
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

import feedparser
import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models import CanvassingNote, IssueMention, OpponentActivity, SourceItem
from app.services import campaign_analysis, intelligence, narrative_frames, narratives, race_relevance, scoring, story_clustering
from app.services.campaign_analysis import framing_to_action
from app.services.snapshots import build_source_summary
from app.services.source_ownership import classify_source_owner


# ── HTML cleaning ─────────────────────────────────────────────────────────────

_BLOCK_TAGS = re.compile(
    r'<(script|style|noscript|nav|header|footer|aside|form)[^>]*>.*?</\1>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')
_PARA_SPLIT = re.compile(r'(?:</p>|</div>|</li>|<br\s*/?>|\n)+', re.IGNORECASE)
_NOISE_BLOCK = re.compile(
    r'<[^>]+(?:class|id)=["\'][^"\']*(?:nav|menu|footer|header|sidebar|aside|related|trending|popular|most-read|newsletter|signup|promo|advert|share|social|video|gallery|breadcrumb|comments)[^"\']*["\'][^>]*>.*?</[^>]+>',
    re.DOTALL | re.IGNORECASE,
)
_BOILERPLATE_PHRASES = [
    "return to homepage", "top stories", "latest news", "latest", "most read",
    "trending", "related articles", "recommended", "watch now", "video",
    "gallery", "subscribe", "sign up", "newsletter", "share this article",
    "advertisement", "sponsored content", "read more", "continue reading",
    "skip to content", "privacy policy", "terms of service", "follow us",
    "download our app", "open in app", "comments", "around the web",
    "donate", "get involved", "take action", "volunteer", "join the team",
    "text victory", "contribute", "paid for by", "official campaign", "shop now",
    "learn more", "sign up now", "campaign headquarters", "contact us",
]
_TEASER_WORDS = re.compile(
    r'\b(top stories|latest|trending|most read|watch|photos|video|gallery|who is|what to know|newsletter|subscribe)\b',
    re.IGNORECASE,
)
def _decode_entities(text: str) -> str:
    """Decode all HTML entities including named, decimal (&#123;), and hex (&#x201c;)."""
    return _html.unescape(text)


def _normalize_text(text: str) -> str:
    """Decode HTML entities and collapse whitespace. Safe on already-clean Unicode text."""
    if not text:
        return text
    return _WHITESPACE.sub(' ', _html.unescape(text)).strip()


def _strip_tags(fragment: str) -> str:
    return _WHITESPACE.sub(' ', _TAG_STRIP.sub(' ', _decode_entities(fragment))).strip()


def _paragraph_score(paragraph: str) -> int:
    words = paragraph.split()
    if len(words) < 8:
        return -5
    score = min(len(words), 80)
    lower = paragraph.lower()
    if any(phrase in lower for phrase in _BOILERPLATE_PHRASES):
        score -= 45
    if _TEASER_WORDS.search(paragraph) and len(words) < 25:
        score -= 35
    if paragraph.count("|") + paragraph.count("›") + paragraph.count("»") > 2:
        score -= 25
    if len(re.findall(r"[\U0001F300-\U0001FAFF]", paragraph)) > 0:
        score -= 20
    if sum(1 for ch in paragraph if not ch.isalnum() and not ch.isspace()) / max(len(paragraph), 1) > 0.25 and len(words) < 20:
        score -= 10
    if re.search(r"\b(said|according|reported|campaign|candidate|election|district|assembly|council|primary)\b", lower):
        score += 20
    if re.search(r"[.!?]$", paragraph):
        score += 8
    return score


def _extract_paragraphs(html: str, title: str = "") -> list[str]:
    chunks = _PARA_SPLIT.split(html)
    paragraphs: list[str] = []
    seen: set[str] = set()
    title_key = re.sub(r"\s+", " ", title).strip().lower()
    title_words = set(re.findall(r"\b\w+\b", title_key))
    for chunk in chunks:
        text = _strip_tags(chunk)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        words = set(re.findall(r"\b\w+\b", key))
        if title_words and len(words) <= 14 and len(title_words & words) / max(len(title_words | words), 1) >= 0.8:
            continue
        seen.add(key)
        if _paragraph_score(text) <= 0:
            continue
        paragraphs.append(text)
    return paragraphs


def _assess_extraction_quality(text: str, title: str = "", source_html: str | None = None) -> tuple[int, str, list[str]]:
    lower = text.lower()
    raw_lower = source_html.lower() if source_html else lower
    reasons: list[str] = []
    phrase_hits = [p for p in _BOILERPLATE_PHRASES if p in lower or p in raw_lower]
    if phrase_hits:
        reasons.append(f"Boilerplate phrases detected: {', '.join(phrase_hits[:4])}")
    words = re.findall(r"\b\w+\b", text)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    short_teasers = [s for s in sentences if len(s.split()) <= 10 and _TEASER_WORDS.search(s)]
    if len(short_teasers) >= 2:
        reasons.append("Multiple short teaser/sidebar fragments detected")
    properish = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", text)
    unique_properish = set(properish)
    if len(unique_properish) >= 14 and len(words) < 800:
        reasons.append("Many unrelated proper-name/topic fragments detected")
    if len(words) < 80:
        reasons.append("Extracted article text is very short")
    score_penalty = 0
    if source_html and phrase_hits:
        raw_candidates = [chunk for chunk in _PARA_SPLIT.split(source_html) if _strip_tags(chunk)]
        kept_candidates = [chunk for chunk in _PARA_SPLIT.split(text) if _strip_tags(chunk)]
        if raw_candidates and len(raw_candidates) > len(kept_candidates):
            discarded = len(raw_candidates) - len(kept_candidates)
            discarded_ratio = discarded / max(len(raw_candidates), 1)
            if discarded_ratio >= 0.5:
                reasons.append("Many wrapper/sidebar fragments were discarded during extraction")
                score_penalty = 20
            elif discarded_ratio >= 0.25:
                score_penalty = 10

    score = 100
    score -= min(50, len(phrase_hits) * 12)
    score -= min(30, len(short_teasers) * 8)
    score -= score_penalty
    if len(unique_properish) >= 14 and len(words) < 800:
        score -= 20
    if len(words) < 80:
        score -= 10
    if title and title.lower() not in lower and len(words) > 250 and phrase_hits:
        score -= 10
        reasons.append("Body text appears weakly connected to the page title")
    score = max(0, min(100, score))
    label = "good" if score >= 75 else ("mixed" if score >= 45 else "poor")
    if not reasons and label == "good":
        reasons.append("Article extraction appears clean")
    return score, label, reasons


def _clean_html_with_quality(html: str) -> tuple[str, str, int, str, list[str]]:
    """
    Return (title, body_text) extracted from raw HTML.
    Strips noise tags, decodes entities, collapses whitespace.
    """
    # Extract <title>
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    raw_title = title_match.group(1).strip() if title_match else ""

    candidate_blocks = []
    for match in re.finditer(
        r'<(article|main|section|div)[^>]*(?:class|id)=["\'][^"\']*(?:article|story|post|entry|content|main|body)[^"\']*["\'][^>]*>(.*?)</\1>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        block = match.group(2)
        cleaned_block = _NOISE_BLOCK.sub(' ', _BLOCK_TAGS.sub(' ', block))
        paragraphs = _extract_paragraphs(cleaned_block, raw_title)
        if paragraphs:
            candidate_blocks.append((sum(_paragraph_score(p) for p in paragraphs), paragraphs))

    if candidate_blocks:
        paragraphs = max(candidate_blocks, key=lambda pair: pair[0])[1]
    else:
        working_html = _NOISE_BLOCK.sub(' ', _BLOCK_TAGS.sub(' ', html))
        paragraphs = _extract_paragraphs(working_html, raw_title)
        if not paragraphs:
            fallback = _strip_tags(working_html)
            paragraphs = [fallback] if fallback else []

    text = " ".join(paragraphs)
    text = _WHITESPACE.sub(' ', text).strip()

    # Clean the title too
    title = _TAG_STRIP.sub('', raw_title)
    title = _decode_entities(title)
    title = _WHITESPACE.sub(' ', title).strip()
    score, label, reasons = _assess_extraction_quality(text, title, html)

    return title, text[:4000], score, label, reasons


def _clean_html(html: str) -> tuple[str, str]:
    title, text, _score, _label, _reasons = _clean_html_with_quality(html)
    return title, text


# ── Publication date extraction ───────────────────────────────────────────────

# Ordered by reliability; first match wins.
_HTML_META_DATE_PROPS = (
    ("property", "article:published_time"),
    ("property", "og:published_time"),
    ("name",     "pubdate"),
    ("name",     "DC.date"),
    ("name",     "date"),
    ("itemprop", "datePublished"),
)


def _parse_date_string(value: str) -> Optional[datetime]:
    """Parse an arbitrary date string to a naive UTC datetime. Returns None on failure."""
    try:
        from dateutil import parser as dp
        dt = dp.parse(value.strip())
        if dt.tzinfo is not None:
            # Convert to UTC then strip tzinfo so all stored datetimes are naive UTC.
            dt = (dt - dt.utcoffset()).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _parse_html_published_date(html: str) -> Optional[datetime]:
    """Extract publication date from HTML metadata. Returns naive UTC datetime or None."""
    # 1. <meta property/name/itemprop> tags
    for meta_m in re.finditer(r'<meta\s[^>]*>', html, re.IGNORECASE):
        tag = meta_m.group(0)
        for attr, target in _HTML_META_DATE_PROPS:
            if re.search(rf'\b{attr}\s*=\s*["\']?\s*{re.escape(target)}\s*["\']?', tag, re.IGNORECASE):
                content_m = re.search(r'\bcontent\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
                if content_m:
                    dt = _parse_date_string(content_m.group(1))
                    if dt:
                        return dt

    # 2. JSON-LD datePublished
    for script_m in re.finditer(
        r'<script\b[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', script_m.group(1))
        if m:
            dt = _parse_date_string(m.group(1))
            if dt:
                return dt

    # 3. <time datetime="YYYY-..."> elements (full dates only, not bare years or clock times)
    for time_m in re.finditer(r'<time\b[^>]+>', html, re.IGNORECASE):
        tag = time_m.group(0)
        dt_m = re.search(r'\bdatetime\s*=\s*["\'](\d{4}-\d{2}-\d{2}[^"\']*)["\']', tag, re.IGNORECASE)
        if dt_m:
            dt = _parse_date_string(dt_m.group(1))
            if dt:
                return dt

    return None


_REFERENCE_DOMAINS = {
    "ballotpedia.org",
    "votesmart.org",
    "opensecrets.org",
    "govtrack.us",
    "congress.gov",
    "house.gov",
    "senate.gov",
    "fec.gov",
    "ourcampaigns.com",
    "congressweb.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "paigeforpa.com",
    "leadershipnowproject.org",
    "breezy.hr",
    "democracy-summer.breezy.hr",
}


def _is_reference_url(url: Optional[str]) -> bool:
    """Return True if the URL points to an evergreen reference page (profile, bio, directory)."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        if any(host == d or host.endswith("." + d) for d in _REFERENCE_DOMAINS):
            return True
        # Campaign websites (.com/candidate-name, paigefor*, *forpa*, *forcongress*)
        if any(kw in host for kw in ("forpa", "forcongress", "for" + host.split(".")[0])):
            return True
    except Exception:
        pass
    return False


def _rss_published_at(published_parsed) -> Optional[datetime]:
    """Convert a feedparser published_parsed struct_time (UTC) to a naive UTC datetime.

    feedparser always normalises timestamps to UTC, so the six fields of
    published_parsed are already UTC values.  Using datetime(*fields[:6])
    reads them directly and avoids the local-timezone skew that
    time.mktime() would introduce on non-UTC servers.
    """
    if not published_parsed:
        return None
    try:
        return datetime(*published_parsed[:6])
    except (TypeError, ValueError):
        return None


# ── Core analyze-and-save pipeline ───────────────────────────────────────────

def _compute_priority_score(db: Session, item: SourceItem) -> int:
    score = 0
    score += int((item.race_relevance_score or 0) * 0.6)
    score += int((item.actionability_score or 0) * 0.35)
    if item.urgency == "high":
        score += 10
    elif item.urgency == "medium":
        score += 5
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
    if item.extraction_quality_label == "poor":
        score -= 25
    elif item.extraction_quality_label == "mixed":
        score -= 10
    return max(0, score)


def _create_and_analyze(db: Session, item: SourceItem) -> SourceItem:
    db.add(item)
    db.flush()
    ownership = classify_source_owner(db, item)
    item.source_owner_type = ownership.source_owner_type
    item.source_owner_confidence = ownership.source_owner_confidence
    story_clustering.assign_story_cluster(db, item)

    # Single LLM call: relevance + summary + framing
    analysis = campaign_analysis.analyze(db, item)

    if analysis.get("_used_fallback"):
        # Groq unavailable — fall back to keyword scoring and old summarize path
        if not item.summary and item.raw_text:
            if item.extraction_quality_label == "poor":
                item.summary = build_source_summary(item)
            else:
                item.summary = intelligence.summarize_source(item.raw_text)
        if not item.urgency or item.urgency == "low":
            item.urgency = intelligence.classify_urgency(f"{item.title} {item.raw_text or ''}")
        race_relevance.apply_relevance(db, item)
        db.commit()
        db.refresh(item)
    else:
        # Apply the LLM's single-call judgment
        if analysis.get("one_sentence"):
            item.summary = analysis["one_sentence"]
        elif not item.summary and item.raw_text and item.extraction_quality_label == "poor":
            item.summary = build_source_summary(item)

        item.race_relevance_score = analysis["relevance_score"]
        item.archived_as_irrelevant = not analysis["relevant"]
        item.actionability_label = framing_to_action(analysis["framing"])

        if analysis.get("needs_attention"):
            item.urgency = "high"
        elif not item.urgency or item.urgency == "low":
            item.urgency = "medium" if analysis["relevant"] else "low"

        db.commit()
        db.refresh(item)

        if analysis["relevant"]:
            narrative_frames.match_article_to_frames(db, item)

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
    source_author: Optional[str] = None,
) -> SourceItem:
    item = SourceItem(
        title=_normalize_text(title),
        raw_text=_normalize_text(raw_text),
        source_name=source_name,
        source_type=source_type,
        source_url=source_url,
        published_at=published_at,
        source_author=_normalize_text(source_author) if source_author else None,
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
        source_author: Optional[str] = None
        if "html" in content_type:
            title, body_text, quality_score, quality_label, quality_reasons = _clean_html_with_quality(resp.text)
            published_date = _parse_html_published_date(resp.text)
            # Extract author from <meta name="author"> or <meta property="article:author">
            author_match = re.search(
                r'<meta\s[^>]*?(?:name|property)=["\'](?:author|article:author)["\'][^>]*?content=["\']([^"\']{1,200})["\']'
                r'|<meta\s[^>]*?content=["\']([^"\']{1,200})["\'][^>]*?(?:name|property)=["\'](?:author|article:author)["\']',
                resp.text, re.IGNORECASE,
            )
            if author_match:
                raw_author = author_match.group(1) or author_match.group(2) or ""
                source_author = _normalize_text(raw_author) or None
        else:
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            body_text = resp.text[:4000]
            quality_score, quality_label, quality_reasons = _assess_extraction_quality(body_text, title)
            published_date = None

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
            published_at=published_date,
            source_author=source_author,
            extraction_quality_score=quality_score,
            extraction_quality_label=quality_label,
            extraction_quality_reasons=json.dumps(quality_reasons),
        )
        if item.extraction_quality_label == "poor":
            item.summary = build_source_summary(item)
        return _create_and_analyze(db, item)
    except httpx.TimeoutException as exc:
        logger.warning("Timeout fetching URL %s: %s", url, exc)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("HTTP %s fetching URL %s", exc.response.status_code, url)
        return None
    except Exception as exc:
        logger.warning("Failed to ingest URL %s: %s: %s", url, type(exc).__name__, exc)
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

    for entry in feed.entries[:50]:
        url = entry.get("link") or ""
        title = _strip_tags(entry.get("title") or "Untitled")[:200]

        # Deduplicate by source_url
        if url and db.query(SourceItem).filter_by(source_url=url).first():
            skipped += 1
            continue

        raw_text = _strip_tags(entry.get("summary") or entry.get("description") or "")[:4000]

        published = _rss_published_at(getattr(entry, "published_parsed", None))

        # feedparser surfaces the byline as entry.author or entry.author_detail.name
        rss_author = (
            entry.get("author")
            or (entry.get("author_detail") or {}).get("name")
            or ""
        )
        source_author = _normalize_text(rss_author)[:200] or None

        inferred_type = "reference" if (not published and _is_reference_url(url)) else "news"
        item = SourceItem(
            title=title,
            raw_text=raw_text,
            source_url=url or None,
            source_name=label or feed.feed.get("title", feed_url),
            source_type=inferred_type,
            published_at=published,
            source_author=source_author,
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
            except Exception as exc:
                logger.warning("Could not parse canvassing date %r: %s", date_str, exc)
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
