"""Ingestion helpers for RSS, URL, and text sources."""
import html as _html
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

from app.models import NarrativeFrame, Opponent, OpponentActivity, SourceItem
from app.services import campaign_analysis, intelligence, narrative_frames, race_relevance, scoring, story_clustering
from app.services.campaign_analysis import framing_to_action
from app.services.snapshots import build_source_summary
from app.services.source_ownership import classify_source_owner
from app.services.text_utils import strip_html_to_text


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


def _persist_cluster_native(
    db: Session,
    item: SourceItem,
    cluster,  # StoryCluster
    frames: list[NarrativeFrame],
    attacks: list[dict],
    matched_indices: list[int],
) -> None:
    """Phase A dual-write: mirror legacy NarrativeFrameMention / OpponentActivity
    inserts into the cluster-native tables. Idempotent via UPSERT helpers.

    Reads its inputs from the same LLM payload the legacy persisters consumed
    so the two paths cannot disagree on what was extracted from this article.
    """
    from app.services import cluster_writes

    # Frame matches → FrameClusterMatch (1-indexed)
    for idx in matched_indices:
        if not isinstance(idx, int) or idx < 1 or idx > len(frames):
            continue
        frame = frames[idx - 1]
        cluster_writes.upsert_frame_match(
            db,
            frame_id=frame.id,
            cluster_id=cluster.id,
            confidence=75,
            source_type="cluster_runtime",
            matched_by="llm",
            representative_snapshot_ts=datetime.utcnow(),
            article_date=item.published_at,
        )

    # Opponent attacks → ClusterOpponentActivity
    if attacks:
        opponents = {o.name.strip().lower(): o for o in db.query(Opponent).all()}
        for entry in attacks:
            opp = opponents.get((entry.get("opponent_name") or "").strip().lower())
            if not opp:
                continue
            clean_text = strip_html_to_text(entry.get("text") or "")[:500]
            if not clean_text:
                continue
            atype = entry.get("type")
            cluster_writes.upsert_opponent_activity(
                db,
                opponent_id=opp.id,
                cluster_id=cluster.id,
                claim=clean_text if atype == "claim" else None,
                attack=clean_text if atype == "attack" else None,
                promise=clean_text[:300] if atype == "promise" else None,
                source_type="cluster_runtime",
            )


def _create_and_analyze(db: Session, item: SourceItem) -> SourceItem:
    db.add(item)
    db.flush()
    ownership = classify_source_owner(db, item)
    item.source_owner_type = ownership.source_owner_type
    item.source_owner_confidence = ownership.source_owner_confidence
    # v2 assigns item.story_cluster_id AND ensures a StoryCluster row exists.
    # Phase A logs the retrigger reason but does not act on it — per-article
    # LLM still runs unconditionally below.
    cluster, _is_new, retrigger = story_clustering.assign_story_cluster_v2(db, item)
    if retrigger:
        logger.info(
            "ingestion: cluster retrigger reason=%s (not acted on in Phase A) "
            "item=%d cluster=%s",
            retrigger, item.id, cluster.id,
        )

    # Link to outlet by URL domain so authority-weighted reach is correct.
    from app.services.outlet_linking import build_outlet_index, link_outlet_to_item
    link_outlet_to_item(item, build_outlet_index(db))

    # Cheap keyword pre-filter: drop obvious noise (no candidate/opponent/district
    # mention AND very low keyword score) before paying for the LLM call.
    prefilter = race_relevance.analyze_source_item(db, item)
    no_political_signal = not (
        prefilter.candidate_mentioned
        or prefilter.opponent_mentioned
        or prefilter.district_mentioned
        or prefilter.priority_issue_mentioned
    )
    prefilter_threshold = int(os.environ.get("PREFILTER_THRESHOLD", "15"))
    if no_political_signal and prefilter.race_relevance_score < prefilter_threshold:
        logger.info(
            "ingestion: prefilter dropped item=%d (score=%d, category=%s) — title=%r",
            item.id, prefilter.race_relevance_score, prefilter.content_category,
            (item.title or "")[:60],
        )
        item.race_relevance_score = prefilter.race_relevance_score
        item.race_relevance_label = "irrelevant"
        item.archived_as_irrelevant = True
        item.actionability_label = "ignore"
        item.content_category = prefilter.content_category or "irrelevant"
        item.relevance_reasons = json.dumps(
            (prefilter.relevance_reasons or []) + ["Prefilter: no political signal; LLM skipped"]
        )
        item.urgency = "low"
        item.priority_score = 0
        db.commit()
        db.refresh(item)
        return item

    # Fetch active frames once so we can pass them into the combined LLM call.
    active_frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()

    # Single combined LLM call: relevance + summary + framing + sentiment +
    # opponent attacks + frame matching.
    # Per PRODUCT_BRIEF this is the ONLY LLM call per article on ingest.
    analysis = campaign_analysis.analyze_with_frames(db, item, frames=active_frames)

    if analysis.get("_used_fallback"):
        # LLM unavailable even after the patient wait (rare). Apply only cheap
        # keyword relevance as a stopgap — no further LLM calls — and leave
        # `summary` NULL so the next rescore (only_unscored) re-does this article
        # properly: real LLM score + frame match. Never a permanent keyword-only,
        # frame-unmatched entry.
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
        item.race_relevance_label = race_relevance._label(analysis["relevance_score"])
        item.archived_as_irrelevant = not analysis["relevant"]
        item.actionability_label = framing_to_action(analysis["framing"])
        item.sentiment = analysis.get("sentiment", "neutral")

        # Cache the structured extraction so rematch can avoid re-reading article text.
        try:
            cacheable = {
                k: analysis.get(k)
                for k in (
                    "one_sentence", "framing", "sentiment", "relevance_score",
                    "relevant", "opponent_attacks", "reason",
                )
            }
            item.structured_extraction = json.dumps(cacheable)
        except Exception:
            pass

        reason = (analysis.get("reason") or "").strip()
        if reason:
            item.relevance_reasons = reason

        if analysis.get("needs_attention"):
            item.urgency = "high"
        elif not item.urgency or item.urgency == "low":
            item.urgency = "medium" if analysis["relevant"] else "low"

        db.commit()
        db.refresh(item)

        if analysis["relevant"]:
            # Cluster-native only (Phase D). The legacy NarrativeFrameMention
            # and OpponentActivity tables are no longer written by ingestion;
            # historical data remains in place for rollback / drill-down until
            # a follow-up cleanup PR drops them.
            _persist_cluster_native(
                db, item, cluster, active_frames,
                analysis.get("opponent_attacks") or [],
                analysis.get("frame_matches") or [],
            )
            db.commit()

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


def _try_wayback_fallback(url: str) -> tuple[str | None, bool]:
    """Try to recover an article from any web archive when direct fetch fails
    or extracts poorly. Tries Wayback first, then archive.today as second
    fallback. Returns (html_or_None, archived_flag).

    The name is kept for back-compat; the function now uses the broader
    try_archive_fallbacks helper.
    """
    from app.services.wayback import try_archive_fallbacks
    html, source = try_archive_fallbacks(url)
    if not html:
        return None, False
    logger.info("archive recovery: %s via %s", url[:80], source)
    return html, True


def _try_readability_extraction(html: str) -> tuple[str | None, str | None]:
    """Try Mozilla Readability algorithm as a fallback extractor when our
    standard HTML cleaner returns poor quality. Returns (title, body_text)
    on success, (None, None) on failure.

    Readability operates on the legally-fetched HTML — it's a better text
    extractor for pages with heavy DOM chrome, embedded apps, or non-standard
    article structures. Different category from paywall-bypass services.
    """
    if not html:
        return None, None
    try:
        from readability import Document
        doc = Document(html)
        title = (doc.title() or "").strip() or None
        content_html = doc.summary() or ""
        # Strip HTML tags from the content_html (readability returns HTML for content)
        text = re.sub(r"<[^>]+>", " ", content_html)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text.split()) < 40:
            return None, None
        return title, text
    except Exception as exc:
        logger.debug("readability extraction failed: %s", exc)
        return None, None


def ingest_url(db: Session, url: str, source_type: str) -> Optional[SourceItem]:
    # Dedup by URL
    existing = db.query(SourceItem).filter_by(source_url=url).first()
    if existing:
        return existing

    html_text: str | None = None
    content_type = ""
    archived_via_wayback = False

    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)"
        })
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        html_text = resp.text
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError) as exc:
        # Direct fetch failed (timeout / paywall / 4xx / 5xx). Try Wayback.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        logger.info(
            "ingest_url: direct fetch failed for %s (%s%s) — trying Wayback",
            url, type(exc).__name__,
            f" {status}" if status else "",
        )
        html_text, archived_via_wayback = _try_wayback_fallback(url)
        if not html_text:
            logger.warning("ingest_url: both direct + Wayback failed for %s", url)
            return None
        content_type = "text/html"  # Wayback always returns HTML

    try:
        source_author: Optional[str] = None
        if "html" in content_type:
            title, body_text, quality_score, quality_label, quality_reasons = _clean_html_with_quality(html_text)
            published_date = _parse_html_published_date(html_text)

            # Readability rescue: when standard extraction returned poor/thin
            # content, try Mozilla Readability on the same HTML before paying
            # the network cost of archive lookup. Often saves pages with weird
            # DOM (lots of nav/ad chrome, embedded apps, non-standard tags).
            if (
                not archived_via_wayback
                and html_text
                and (not body_text or len(body_text.split()) < 80)
            ):
                read_title, read_body = _try_readability_extraction(html_text)
                if read_body and len(read_body.split()) > len(body_text.split() if body_text else []):
                    logger.info(
                        "ingest_url: Readability rescue for %s (std=%d words, readability=%d words)",
                        url[:80], len(body_text.split()) if body_text else 0, len(read_body.split()),
                    )
                    title = read_title or title
                    body_text = read_body
                    quality_score, quality_label, quality_reasons = _assess_extraction_quality(body_text, title or "")

            # If still poor/empty after Readability, fall back to web archives.
            # Sometimes the live page has aggressive JS or paywall while the
            # archived snapshot has the printable text.
            if (
                not archived_via_wayback
                and (not body_text or len(body_text.split()) < 80)
            ):
                wb_html, wb_ok = _try_wayback_fallback(url)
                if wb_ok and wb_html:
                    wb_title, wb_body, wb_score, wb_label, wb_reasons = _clean_html_with_quality(wb_html)
                    if wb_body and len(wb_body.split()) > len(body_text.split()):
                        logger.info(
                            "ingest_url: Wayback rescue for %s (live=%d words, wayback=%d words)",
                            url, len(body_text.split()), len(wb_body.split()),
                        )
                        title = wb_title or title
                        body_text = wb_body
                        quality_score, quality_label, quality_reasons = wb_score, wb_label, wb_reasons
                        archived_via_wayback = True
                        if not published_date:
                            published_date = _parse_html_published_date(wb_html)
            # Extract author from <meta name="author"> or <meta property="article:author">
            author_match = re.search(
                r'<meta\s[^>]*?(?:name|property)=["\'](?:author|article:author)["\'][^>]*?content=["\']([^"\']{1,200})["\']'
                r'|<meta\s[^>]*?content=["\']([^"\']{1,200})["\'][^>]*?(?:name|property)=["\'](?:author|article:author)["\']',
                html_text, re.IGNORECASE,
            )
            if author_match:
                raw_author = author_match.group(1) or author_match.group(2) or ""
                source_author = _normalize_text(raw_author) or None
        else:
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            body_text = html_text[:4000]
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
    except Exception as exc:
        # Network failures already handled above (with Wayback fallback).
        # This catches downstream errors: HTML parsing, _create_and_analyze, etc.
        logger.warning("Failed to ingest URL %s: %s: %s", url, type(exc).__name__, exc)
        return None


class RSSIngestResult:
    def __init__(self, added: int, skipped: int, items: list[SourceItem]):
        self.added = added
        self.skipped = skipped
        self.items = items


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_rss_content(feed_url: str) -> str | None:
    """Fetch RSS feed content using httpx with browser headers (handles Reddit blocks)."""
    try:
        resp = httpx.get(feed_url, headers=_BROWSER_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def ingest_rss(db: Session, feed_url: str, label: Optional[str] = None) -> RSSIngestResult:
    raw = _fetch_rss_content(feed_url)
    feed = feedparser.parse(raw if raw else feed_url)
    added_items: list[SourceItem] = []
    skipped = 0
    _build_outlet_index_cache: dict = {}  # lazy-loaded once per feed, not per entry

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

        # For Google News feeds, each entry carries a <source url="publisher.com">
        # element that feedparser exposes as entry.source.  Extract the publisher
        # domain so we can attribute the article to the right outlet even though
        # the stored source_url remains the Google News redirect (for dedup).
        publisher_domain: str | None = None
        entry_source = entry.get("source") or {}
        if entry_source and "news.google.com" in feed_url:
            src_href = entry_source.get("href") or ""
            if src_href:
                from urllib.parse import urlparse as _urlparse
                import re as _re
                publisher_domain = _re.sub(r"^www\.", "", _urlparse(src_href).netloc).lower() or None

        item = SourceItem(
            title=title,
            raw_text=raw_text,
            source_url=url or None,
            source_name=label or feed.feed.get("title", feed_url),
            source_type=inferred_type,
            published_at=published,
            source_author=source_author,
        )

        # Resolve outlet before persisting so reach weighting is immediate.
        if publisher_domain:
            from app.services.outlet_linking import build_outlet_index as _build_idx
            _oidx = _build_outlet_index_cache.get("cache") or _build_idx(db)
            _build_outlet_index_cache["cache"] = _oidx
            outlet_id = _oidx.get(publisher_domain)
            if outlet_id:
                item.outlet_id = outlet_id

        created = _create_and_analyze(db, item)
        added_items.append(created)

    return RSSIngestResult(added=len(added_items), skipped=skipped, items=added_items)
