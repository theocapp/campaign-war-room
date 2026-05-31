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
    """Decode HTML entities, strip embedded NULs, and collapse whitespace.

    NUL stripping is mandatory now that the live DB is Postgres — Postgres TEXT
    rejects U+0000 with `UntranslatableCharacter`, which would silently fail
    the article insert. The 2026-05-29 preflight audit found 47 historical
    rows with embedded NULs from web-scrape contamination; this guards the
    forward path.
    """
    if not text:
        return text
    text = text.replace("\x00", "")
    return _WHITESPACE.sub(' ', _html.unescape(text)).strip()


# Unicode blocks that contain emoji + their joiners. Body text is left alone
# (emoji can be load-bearing inside the social-post content the AI scores),
# but article titles surface throughout the UI and we want them clean per the
# project's no-emoji-in-UI rule. Backfill ran 2026-05-30 — see
# scripts/strip_emoji_from_titles.sql for the one-off UPDATE.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FFFF"   # most pictographs, emoji, supplements
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "\U00002300-\U000023FF"   # misc technical (incl. ⌛️ ♻️ etc.)
    "‍"                  # ZWJ (binds emoji sequences)
    "️"                  # variation selector-16 (emoji presentation)
    "⃣"                  # combining enclosing keycap
    "]+",
    flags=re.UNICODE,
)


def clean_title(text: str | None) -> str | None:
    """Normalize a SourceItem title for storage and display.

    Runs `_normalize_text` then strips emoji and collapses the whitespace
    that emoji removal leaves behind. Call this at every SourceItem
    construction site that sets `title=...` — see grep for the inventory.
    """
    if not text:
        return text
    text = _normalize_text(text)
    if not text:
        return text
    text = _EMOJI_RE.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def _strip_tags(fragment: str) -> str:
    # _decode_entities → _TAG_STRIP → whitespace collapse → NUL strip.
    # Same Postgres-TEXT NUL hazard as _normalize_text.
    return _WHITESPACE.sub(
        ' ', _TAG_STRIP.sub(' ', _decode_entities(fragment))
    ).strip().replace("\x00", "")


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


# ── Junk-title filter ────────────────────────────────────────────────────────
#
# Some scraper paths produce SourceItem rows whose `title` is a placeholder
# left over from a paywall / login wall / SVG icon / aggregator front page,
# not real article content. Confirmed live in this DB:
#   - "Instagram", "Facebook" (scraper hit login wall on social-share URLs)
#   - "chevron-right" (SVG icon name pulled from a CSS class on the page)
#   - "Untitled", "Latest Articles", "BizToc", "Targeted News Service"
#     (generic placeholder titles from aggregator front pages)
#   - "idahostatejournal.com" (bare hostname after a paywall)
#   - "breeze 4.jpg", anything ending in .pdf / .png etc. (binary files
#     ingested as articles)
# These rows still get persisted (audit trail), but immediately archived so
# they don't reach clustering, outlet linking, LLM scoring, or the Articles
# list. Without this filter the "Instagram" cluster grew to 23 members.
_PLATFORM_PLACEHOLDER_TITLES = {
    "instagram", "facebook", "twitter", "x", "tiktok",
    "linkedin", "pinterest", "snapchat", "threads", "mastodon",
}
_GENERIC_PLACEHOLDER_TITLES = {
    "untitled", "latest articles", "biztoc", "chevron-right",
    "targeted news service", "home", "menu", "404", "page not found",
    "redirecting", "loading", "sign in", "log in", "subscribe",
}
# Match titles ending in a binary file extension — these are image / video
# / document URLs misingested as articles.
_FILE_EXT_TITLE_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|pdf|mp4|mov|mp3|wav|webm|tiff?|csv|xlsx?|zip)$",
    re.IGNORECASE,
)
# Match titles that are just a bare hostname (paywall fallback) — examples
# from the live DB: "idahostatejournal.com", "rockymounttelegram.com",
# "bozemandailychronicle.com". Accepts either `domain.tld` or
# `sub.domain.tld`. No spaces allowed (legitimate news titles always have
# spaces).
_BARE_HOSTNAME_RE = re.compile(
    r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$",
    re.IGNORECASE,
)


def _is_junk_title(title: str | None) -> bool:
    """Return True if `title` looks like a scraper artifact rather than
    real article content. See the constants above for examples.
    """
    if not title:
        return True
    t = title.strip()
    if not t:
        return True
    lower = t.lower()
    if lower in _PLATFORM_PLACEHOLDER_TITLES:
        return True
    if lower in _GENERIC_PLACEHOLDER_TITLES:
        return True
    if _FILE_EXT_TITLE_RE.search(t):
        return True
    if _BARE_HOSTNAME_RE.match(t):
        return True
    return False


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


def _classify_perspective(db: Session, item: SourceItem) -> None:
    """V13.21 — classify per-article perspective for landscape dot color.

    Runs the cascading classifier: existing labels (free) → outlet bias
    (free) → attribution heuristic (free) → LLM fallback. The LLM phase
    fires here on every race-relevant new article (~1-2s added latency);
    we accept the latency because (a) cost is tiny (gpt-4o-mini ~$0.0001/call)
    and (b) the chart only shows useful color if perspective is populated
    immediately rather than lagging a daily backfill.

    Skipped for off-topic items (race_relevance_score < 50) — they won't
    appear on the landscape anyway and shouldn't cost an LLM call.
    """
    if (item.race_relevance_score or 0) < 50:
        return
    if item.archived_as_irrelevant:
        return
    from app.services.article_perspective import (
        get_classifier, classify_with_llm,
    )
    from app.models import CampaignConfig, Opponent

    classify = get_classifier(db)
    r = classify(item)
    if r.method == "fallback":
        # Cascade to LLM. Reload campaign/opponent metadata for the prompt.
        cfg = db.query(CampaignConfig).first()
        opp = db.query(Opponent).first()
        if cfg and opp:
            r = classify_with_llm(
                item,
                candidate_name=cfg.candidate_name or "",
                candidate_party=cfg.party or "",
                opponent_name=opp.name or "",
                opponent_party=opp.party or "",
            )
    item.perspective = r.perspective
    item.perspective_method = r.method
    item.perspective_confidence = r.confidence
    item.perspective_reason = (r.reason or "")[:240]


# ── Provenance safety net ─────────────────────────────────────────────────────
# An item pulled by a monitor *named for the candidate or an opponent* (e.g. a
# Google-News search for "Paige Cognetti") is provenance-strong: the aggregator
# already filtered to the race. When such an item arrives with a body too thin
# to judge — almost always an unresolved Google-News redirect whose article body
# we never fetched — the pipeline's "irrelevant" verdict is really a FETCH
# failure, not a relevance judgment. Silently archiving it drops genuinely
# relevant local news (diagnosed 2026-05-31: a WVIA PA-08 primary piece and an
# NYT PA-08 polling piece vanished this way). Instead we route it to the human
# review queue so nothing provenance-strong is dropped on a fetch failure.
#
# Threshold grounding: unresolved Google-News redirect items sit < ~300 chars
# (headline + RSS blurb, no article body). Env-configurable so other campaigns
# can tune it without a code change.
_PROVENANCE_RESCUE_MAX_BODY_CHARS = int(
    os.environ.get("PROVENANCE_RESCUE_MAX_BODY_CHARS", "300")
)


def _campaign_people(db: Session) -> list[tuple[str, str, str]]:
    """(first_token, last_token, display_name) for candidate + opponents, with
    tokens lowercased. Generic: derived from CampaignConfig + Opponent rows, no
    race hard-coding. "Paige Cognetti" → ("paige","cognetti","Paige Cognetti");
    a single-token name yields first==last.
    """
    from app.models import CampaignConfig

    names: list[str] = []
    cfg = db.query(CampaignConfig).first()
    if cfg and cfg.candidate_name:
        names.append(cfg.candidate_name)
    for opp in db.query(Opponent).all():
        if opp.name:
            names.append(opp.name)

    out: list[tuple[str, str, str]] = []
    for n in names:
        toks = [t for t in re.split(r"\s+", n.strip().lower()) if t]
        if toks:
            out.append((toks[0], toks[-1], n.strip()))
    return out


def _district_label_variants(db: Session) -> set[str]:
    """Generic district identifiers from CampaignConfig.district: the raw form,
    the hyphen→space form, and the compact form. "PA-08" → {"pa-08","pa 08",
    "pa08"}. Variants shorter than 3 chars are dropped (a bare state abbrev like
    "pa" is too collision-prone for a label match).
    """
    from app.models import CampaignConfig

    cfg = db.query(CampaignConfig).first()
    d = ((cfg.district if cfg else None) or "").strip().lower()
    if not d:
        return set()
    variants = {d, d.replace("-", " "), re.sub(r"[^a-z0-9]", "", d)}
    return {v for v in variants if len(v) >= 3}


def _provenance_rescue_label(db: Session, item: SourceItem) -> Optional[str]:
    """Return a label for the race participant whose monitor pulled `item` if it
    qualifies for rescue from a silent archive; else None.

    Both conditions required:
      1. thin body — raw_text shorter than the fetch-failure threshold, so the
         archive verdict reflects missing article text, not a real judgment.
      2. provenance-strong — the monitor label (source_name) names a *specific*
         race participant: a candidate/opponent SURNAME (whole word) PLUS a
         disambiguator that ties it to THIS race. Surname alone is too noisy —
         it collides with homonyms (the novelist Paolo Cognetti, the ballplayer
         Roger Bresnahan, the voice actress Alyssa Bresnahan all surfaced as
         bare-surname monitor hits, 2026-05-31). The disambiguator is one of:
           - the participant's first name (full name in the label), OR
           - a district identifier (PA-08 / pa 08 / pa08), OR
           - a second race participant's surname in the same label.
         All three are config-derived (CampaignConfig + Opponents) — no race
         hard-coding. Word-boundary (not bare substring) matching throughout.
    """
    if len(item.raw_text or "") >= _PROVENANCE_RESCUE_MAX_BODY_CHARS:
        return None
    name = (item.source_name or "").lower()
    if not name:
        return None

    def _wb(tok: str) -> bool:
        return re.search(rf"\b{re.escape(tok)}\b", name) is not None

    present = [
        (first, last, disp)
        for (first, last, disp) in _campaign_people(db)
        if len(last) >= 3 and _wb(last)
    ]
    if not present:
        return None

    # Disambiguator 1: full name (surname + first name) in the label.
    for first, last, disp in present:
        if len(first) >= 2 and _wb(first):
            return disp
    # Disambiguator 2: surname + a district identifier.
    if any(_wb(v) for v in _district_label_variants(db)):
        return present[0][2]
    # Disambiguator 3: two distinct race surnames named together.
    if len({last for (_first, last, _disp) in present}) >= 2:
        return present[0][2]

    return None


def _apply_provenance_rescue(db: Session, item: SourceItem) -> bool:
    """If `item` is currently archived but provenance-strong on a thin body,
    flip it into the human review queue. Returns True if a rescue was applied.

    Caller must have already set item.archived_as_irrelevant. The rescued state
    is deliberately honest: content_category + actionability carry the
    provenance override (so the review queue surfaces it), while the
    text-derived race_relevance_score/label are left untouched — we are
    overriding the ARCHIVE action, not claiming the visible text scored as
    relevant. actionability_label='review' is the master key: it satisfies the
    review queue's score floor AND bypasses its keyword gate.
    """
    if not item.archived_as_irrelevant:
        return False
    label = _provenance_rescue_label(db, item)
    if not label:
        return False

    item.archived_as_irrelevant = False
    item.reviewed = False
    item.content_category = "campaign"
    item.actionability_label = "review"
    if not item.urgency or item.urgency == "low":
        item.urgency = "medium"
    item.relevance_reasons = json.dumps([
        f"Provenance safety net: pulled by a monitor named for '{label}', but "
        f"the fetched body was too thin ({len(item.raw_text or '')} chars) to "
        f"confirm relevance — routed to review instead of archived."
    ])
    logger.info(
        "ingestion: provenance rescue item=%s named_for=%r body=%dchars "
        "source_name=%r title=%r — routed to review queue",
        getattr(item, "id", "?"), label, len(item.raw_text or ""),
        item.source_name, (item.title or "")[:60],
    )
    return True


# ── Headline feed promotion ───────────────────────────────────────────────────
# Companion to the provenance rescue. The rescue un-archives a thin-body,
# provenance-strong item into the REVIEW queue without touching its score. But
# when such an item NAMES a race participant in its HEADLINE, the headline is the
# strongest evidence we have — and the LLM's body-derived verdict is unreliable
# precisely because the body is a fetch-failure stub. The per-article scorer caps
# the title signal (+20) below the feed cutoff (50), so a headline that plainly
# names the candidate/opponent gets stranded in review at ~37. This lifts those —
# and only those — to the feed floor so they surface in the feed.
#
# Scoped to THIN bodies on purpose: for a full-body item the LLM read the text and
# its verdict is a real judgment (the headline name is already a scoring input),
# so we never override it here.
_HEADLINE_FEED_FLOOR = int(os.environ.get("HEADLINE_FEED_FLOOR", "50"))


def _headline_names_race_disambiguated(db: Session, item: SourceItem) -> bool:
    """True if a THIN-body item's HEADLINE names a race participant AND is
    disambiguated to THIS race (homonym-safe). No archive check — the caller
    decides what to flip. Two disambiguation paths:

      a) in-title: the headline itself carries a disambiguator — a participant's
         full name, a district identifier, or two distinct participant surnames.
         Robust for ANY name, including generic ones.
      b) provenance: a candidate/opponent-named monitor pulled it, which resolves
         which homonym a bare surname in the title refers to.

    Path (b) is the weak link for a VERY GENERIC candidate name (a
    "Google News: John Smith" monitor is itself full of unrelated John Smiths, so
    "came from our monitor" stops being strong evidence). GENERIC-NAME HARDENING
    HOOK: gate the (b) branch on an in-text geographic anchor (district / city /
    county) when the surname is common. Not enabled now — it would over-narrow a
    distinctive-name race, where most thin items are bare-surname headlines that
    only clear via provenance. All inputs are config-derived (CampaignConfig +
    Opponents) — no race hard-coding.
    """
    if len(item.raw_text or "") >= _PROVENANCE_RESCUE_MAX_BODY_CHARS:
        return False
    title = (item.title or "").lower()
    if not title:
        return False

    def _wb(tok: str) -> bool:
        return re.search(rf"\b{re.escape(tok)}\b", title) is not None

    present = [
        (first, last, disp)
        for (first, last, disp) in _campaign_people(db)
        if len(last) >= 3 and _wb(last)
    ]
    if not present:  # the headline must name a participant surname at all
        return False

    # (a) in-title disambiguation — homonym-safe for any name.
    for first, _last, _disp in present:
        if len(first) >= 2 and _wb(first):
            return True
    if any(_wb(v) for v in _district_label_variants(db)):
        return True
    if len({last for (_first, last, _disp) in present}) >= 2:
        return True

    # (b) provenance disambiguation — resolves a bare surname to this race.
    #     <-- generic-name hardening hook lives here (see docstring).
    if _provenance_rescue_label(db, item) is not None:
        return True

    return False


def _apply_headline_feed_promotion(db: Session, item: SourceItem) -> bool:
    """Lift a thin, race-naming-headline item into the feed: un-archive if needed,
    ensure a non-irrelevant category, floor race_relevance_score to the feed
    cutoff, and mark it adjudicated (reviewed, not dismissed) so it surfaces in the
    feed WITHOUT piling into the triage queue. Companion to _apply_provenance_rescue
    — run AFTER it. Idempotent; returns True only if it actually changed the row.

    Honest by construction: the headline IS part of the item's text and naming the
    race in it is a real relevance signal the scorer already rewards (via
    title_bonus — it just caps it below the cutoff). If the body is later recovered
    and the article rescored, the real text-derived score replaces this floor.
    """
    if not _headline_names_race_disambiguated(db, item):
        return False

    changed = False
    if item.archived_as_irrelevant:
        item.archived_as_irrelevant = False
        changed = True
    if item.content_category in (None, "irrelevant"):
        item.content_category = "campaign"
        changed = True
    if (item.race_relevance_score or 0) < _HEADLINE_FEED_FLOOR:
        item.race_relevance_score = _HEADLINE_FEED_FLOOR
        item.race_relevance_label = race_relevance._label(_HEADLINE_FEED_FLOOR)
        changed = True
    # A homonym-safe headline match is confident enough to FEED — so it does NOT
    # need human triage. Mark it adjudicated (reviewed, not dismissed) so it lands
    # in the feed and LEAVES the /review-queue instead of inflating it. This is
    # the key difference from the provenance rescue: that routes weaker-evidence
    # items TO review for a glance; this one is sure enough to skip review.
    if not item.reviewed:
        item.reviewed = True
        changed = True
    if item.dismissed:
        item.dismissed = False
        changed = True
    if not changed:
        return False

    try:
        reasons = json.loads(item.relevance_reasons) if item.relevance_reasons else []
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
    except Exception:
        reasons = []
    note = (
        f"Headline names the race (homonym-safe) on a thin body "
        f"({len(item.raw_text or '')} chars) — promoted to the feed floor "
        f"({_HEADLINE_FEED_FLOOR})."
    )
    if note not in reasons:
        reasons.append(note)
    item.relevance_reasons = json.dumps(reasons)
    logger.info(
        "ingestion: headline feed promotion item=%s body=%dchars source_name=%r "
        "title=%r — lifted to feed floor %d",
        getattr(item, "id", "?"), len(item.raw_text or ""), item.source_name,
        (item.title or "")[:60], _HEADLINE_FEED_FLOOR,
    )
    return True


def _create_and_analyze(db: Session, item: SourceItem) -> SourceItem:
    # Junk-title short-circuit: scraper artifacts (placeholder titles,
    # binary file URLs, bare hostnames, social-platform login walls) get
    # persisted as archived so the audit trail exists, but skip
    # clustering, outlet linking, and the LLM call. Without this filter
    # the "Instagram" cluster grew to 23 members in this DB.
    if _is_junk_title(item.title):
        logger.info(
            "ingestion: junk-title filter dropped item title=%r url=%s",
            item.title, (item.source_url or "")[:80],
        )
        item.archived_as_irrelevant = True
        item.race_relevance_score = 0
        item.race_relevance_label = "irrelevant"
        item.content_category = "irrelevant"
        item.reviewed = True  # treat as auto-triaged so it doesn't sit in queue
        db.add(item)
        db.flush()
        return item

    # Inline duplicate check: catches the same article arriving via
    # multiple feeds (Google News redirect, direct publisher RSS, etc.).
    # Default dedup is keyed on source_url so SQL sees these as distinct
    # rows; this check fuzzy-matches the title instead. Verdict semantics:
    #   - "new_is_duplicate": existing row already covers this article
    #     with a longer body → archive the new one without scoring (saves
    #     an LLM call) and return early.
    #   - "existing_is_duplicate": the existing row is a stub, the new
    #     row is the canonical version → archive the existing in place,
    #     continue normal pipeline for the new item.
    #   - "neither_canonical" or "no_match": let the normal pipeline run.
    #     The batch dedup pass catches any duplicates that surface later.
    from app.services.dedup_merge import find_canonical_for_item, mark_as_duplicate
    dedup_decision = find_canonical_for_item(db, item)
    if dedup_decision.verdict == "new_is_duplicate":
        canon = dedup_decision.canonical
        logger.info(
            "ingestion: inline dedup — new item is duplicate of source_item_id=%d "
            "(similarity=%.3f) title=%r",
            canon.id, dedup_decision.similarity, (item.title or "")[:60],
        )
        item.archived_as_irrelevant = True
        item.race_relevance_score = canon.race_relevance_score or 0
        item.race_relevance_label = canon.race_relevance_label or "irrelevant"
        item.content_category = canon.content_category or "irrelevant"
        item.reviewed = True
        item.relevance_reasons = json.dumps([{
            "reason": "duplicate",
            "canonical_source_item_id": canon.id,
            "title_similarity": round(dedup_decision.similarity, 3),
            "merged_at": datetime.utcnow().isoformat(),
        }])
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    db.add(item)
    db.flush()

    # If the existing row is the stub and we're the canonical, archive it.
    # Has to run after `db.flush()` so `item.id` exists for the duplicate
    # marker pointing at us.
    if dedup_decision.verdict == "existing_is_duplicate":
        canon_target = dedup_decision.canonical
        logger.info(
            "ingestion: inline dedup — new item supersedes existing source_item_id=%d "
            "(similarity=%.3f); archiving the existing row",
            canon_target.id, dedup_decision.similarity,
        )
        mark_as_duplicate(
            db, duplicate=canon_target, canonical=item,
            similarity=dedup_decision.similarity,
        )
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
        _apply_provenance_rescue(db, item)
        _apply_headline_feed_promotion(db, item)
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
                    "verdict", "extracted_claims", "source_credibility",
                )
            }
            item.structured_extraction = json.dumps(cacheable)
        except Exception:
            logger.debug("structured_extraction cache write failed for item %s", getattr(item, "id", "?"), exc_info=True)

        reason = (analysis.get("reason") or "").strip()
        if reason:
            item.relevance_reasons = json.dumps([reason])

        if analysis.get("needs_attention"):
            item.urgency = "high"
        elif not item.urgency or item.urgency == "low":
            item.urgency = "medium" if analysis["relevant"] else "low"

        # Provenance safety net: a candidate/opponent-named monitor pulled this,
        # but the body was too thin to judge — don't silently archive it on a
        # fetch failure; route it to human review instead.
        _apply_provenance_rescue(db, item)
        # Headline feed promotion: if the (thin) item NAMES the race in its
        # headline, the headline is decisive — lift it past the feed cutoff.
        _apply_headline_feed_promotion(db, item)

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

            # V13.21 — classify article perspective (pro_candidate /
            # pro_opponent / neutral) so dot color on the landscape
            # reflects this specific article's framing, not just the
            # narrative's owner_type. Cheap phases (existing labels +
            # outlet bias + attribution) are instant; LLM phase adds
            # ~1-2s to ingest. Wrapped in try/except so any failure
            # (no LLM key, rate limit, etc.) doesn't break ingestion.
            try:
                _classify_perspective(db, item)
                db.commit()
            except Exception as exc:
                logger.warning(
                    "ingestion: perspective classification failed for item %s: %s",
                    item.id, exc,
                )

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
        title=clean_title(title),
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

        # Defense in depth: title comes from readability/wayback/extractor and
        # bypasses _normalize_text, so strip NULs here. body_text already
        # passed through _strip_tags via _clean_html_with_quality, which
        # strips NULs after the 2026-05-29 hot-fix.
        title = title.replace("\x00", "") if title else title
        cleaned_title = clean_title(title) or ""
        item = SourceItem(
            title=cleaned_title[:200],
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


_YOUTUBE_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/v/|/shorts/)([A-Za-z0-9_-]{11})")
# Cap transcript text to keep LLM-scoring token costs bounded. A 4000-char
# RSS summary + ~20K of transcript = ~24K total, well below the LLM context
# window but big enough to capture most political-content speeches.
_YOUTUBE_TRANSCRIPT_CHAR_CAP = 20000


def _youtube_video_id(url: Optional[str]) -> Optional[str]:
    """Extract the 11-char video ID from a YouTube URL, or None."""
    if not url:
        return None
    if "youtube.com" not in url and "youtu.be" not in url:
        return None
    m = _YOUTUBE_VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


# ── Transcript proper-noun cleanup ───────────────────────────────────────────
#
# YouTube auto-generated captions garble proper nouns ("Cognetti" came back
# as "Connetty" in a real session run). The LLM scoring tolerates these
# fuzzy errors, but FTS5 search and downstream string matches don't. We
# apply per-word fuzzy substitution using a campaign-specific canonical
# list derived from CampaignConfig.candidate_name + every Opponent.name.
#
# Trade-offs in the heuristic:
#   - SequenceMatcher ratio threshold (>= 0.75) is roughly "1-2 edits for
#     short words, more for longer ones." Catches typical caption errors.
#   - First-letter match gate prevents cross-name confusion ("Connecticut"
#     scoring high against "Cognetti" because of the matching middle).
#   - Min length 4 — too-short tokens have too many false-positive matches
#     against random words.
#   - Single-pass `re.sub` is O(transcript-tokens × canonical-names);
#     canonical is usually ≤ 4 names so this stays fast.

from difflib import SequenceMatcher as _SequenceMatcher

_TRANSCRIPT_WORD_RE = re.compile(r"\b[A-Za-z]+\b")


def _campaign_canonical_names(db: Session) -> list[str]:
    """Build the list of proper-noun words we want preserved in transcript
    text. Pulled from the candidate's first/last/middle parts and every
    opponent's name, since those are the words auto-captions garble most
    consistently and where FTS5 search precision matters most.
    """
    from app.models import CampaignConfig, Opponent  # local to avoid cycles

    nouns: list[str] = []
    config = db.query(CampaignConfig).first()
    if config and config.candidate_name:
        for word in config.candidate_name.split():
            if len(word) >= 4 and word.isalpha():
                nouns.append(word)
    for opp in db.query(Opponent).all():
        if opp.name:
            for word in opp.name.split():
                if len(word) >= 4 and word.isalpha():
                    nouns.append(word)
    # Case-insensitive de-dup, preserving the canonical capitalization.
    seen: set[str] = set()
    out: list[str] = []
    for n in nouns:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def _correct_transcript_proper_nouns(transcript: str, canonical_names: list[str]) -> str:
    """Two-pass fuzzy substitution to fix caption-mangled proper nouns.

    Pass 1 — single-word: each word in the transcript is compared against
    each canonical via SequenceMatcher; high enough ratio + first-letter
    match + length tolerance ≤ 2 → substitute.

    Pass 2 — multi-word (added 2026-05-29): some caption errors split a
    name across multiple tokens ("Bresnahan" → "press no hand"). After
    pass 1, scan for 2-word windows whose concatenation fuzzy-matches a
    canonical name. Stricter gates here (only canonicals ≥ 6 chars,
    length tolerance ≤ 3, first-letter match still required) because
    multi-word substitution is more user-visible if wrong.
    """
    if not transcript or not canonical_names:
        return transcript
    canonical_by_lower = {c.lower(): c for c in canonical_names}

    # ── Pass 1: single-word ───────────────────────────────────────────────
    def replace_word(match: re.Match) -> str:
        word = match.group()
        if len(word) < 4:
            return word
        wl = word.lower()
        if wl in canonical_by_lower:
            return canonical_by_lower[wl]
        for cl, canonical in canonical_by_lower.items():
            if wl[0] != cl[0]:
                continue
            if abs(len(cl) - len(wl)) > 2:
                continue
            ratio = _SequenceMatcher(None, wl, cl).ratio()
            if ratio >= 0.75:
                return canonical
        return word

    out = _TRANSCRIPT_WORD_RE.sub(replace_word, transcript)

    # ── Pass 2: multi-word ────────────────────────────────────────────────
    # Only attempt windows when there are canonicals long enough that
    # caption splits make sense — short names (≤ 5 chars) rarely garble
    # into multiple tokens, and the false-positive risk on small windows
    # is high.
    multi_canonicals = [c for c in canonical_names if len(c) >= 6]
    if not multi_canonicals:
        return out

    # Walk through the text matching 2-word windows. We do a single pass
    # left-to-right; once a window is replaced we skip past it.
    result_parts: list[str] = []
    pos = 0
    word_iter = list(_TRANSCRIPT_WORD_RE.finditer(out))
    i = 0
    while i < len(word_iter) - 1:
        m1 = word_iter[i]
        m2 = word_iter[i + 1]
        if m1.end() >= m2.start():
            # Shouldn't happen for our pattern but guard anyway
            i += 1
            continue
        between = out[m1.end():m2.start()]
        # Only attempt when the two words are separated by simple
        # whitespace (typical caption rendering); skip across punctuation.
        if not between.isspace():
            i += 1
            continue
        w1 = m1.group()
        w2 = m2.group()
        # Skip windows where either word is already a canonical name —
        # those tokens were just fixed by pass 1, and combining them with
        # an adjacent unrelated word would over-consume content
        # ("Cognetti for" should not collapse back into "Cognetti").
        if w1.lower() in canonical_by_lower or w2.lower() in canonical_by_lower:
            i += 1
            continue
        # Both must be alphabetic short-ish tokens — caption garbling
        # tends to produce short common-looking words.
        if len(w1) > 8 or len(w2) > 8:
            i += 1
            continue
        concat = (w1 + w2).lower()
        if len(concat) < 5:
            i += 1
            continue
        matched_canonical: str | None = None
        for canonical in multi_canonicals:
            cl = canonical.lower()
            if concat[0] != cl[0]:
                continue
            if abs(len(concat) - len(cl)) > 3:
                continue
            ratio = _SequenceMatcher(None, concat, cl).ratio()
            if ratio >= 0.70:
                matched_canonical = canonical
                break
        if matched_canonical is not None:
            # Emit everything up to w1, then the canonical, then resume
            # past w2 (skip the whitespace in between).
            result_parts.append(out[pos:m1.start()])
            result_parts.append(matched_canonical)
            pos = m2.end()
            i += 2  # consume both words
            continue
        i += 1
    result_parts.append(out[pos:])
    return "".join(result_parts)


def _fetch_youtube_transcript(video_id: str) -> Optional[str]:
    """Fetch auto-generated or manual captions for a YouTube video.

    Returns the joined transcript text, capped at
    `_YOUTUBE_TRANSCRIPT_CHAR_CAP` chars. Returns None on any failure —
    videos without captions, age-restricted videos, network errors, etc.
    Never raises; the caller treats a missing transcript as "no signal
    beyond the RSS description" and moves on.

    NOTE: the proper-noun correction step is applied in `ingest_rss` after
    this returns, not inside here — it needs a DB session to look up the
    campaign's canonical names.
    """
    try:
        # Import here so the module doesn't hard-fail if the package isn't
        # installed in some deployment.
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.debug("youtube-transcript-api not installed; skipping transcript fetch")
        return None
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id)
        # The result is iterable of segment objects with a `.text` attribute.
        # Join with spaces; YouTube captions don't include punctuation that
        # would help our scoring pipeline distinguish sentences, but the LLM
        # handles unpunctuated speech well.
        parts = []
        char_count = 0
        for segment in transcript:
            text = getattr(segment, "text", "") or ""
            text = text.strip()
            if not text:
                continue
            parts.append(text)
            char_count += len(text) + 1
            if char_count >= _YOUTUBE_TRANSCRIPT_CHAR_CAP:
                break
        if not parts:
            return None
        return " ".join(parts)[:_YOUTUBE_TRANSCRIPT_CHAR_CAP]
    except Exception as e:
        # Catch broadly because the upstream library raises a wide variety
        # of types (TranscriptsDisabled, VideoUnavailable, NoTranscriptFound,
        # plus generic network errors). Treating all as "no transcript" is
        # the right policy — never block ingestion on transcript failure.
        logger.debug("transcript fetch failed for %s: %s", video_id, e)
        return None


def ingest_rss(db: Session, feed_url: str, label: Optional[str] = None) -> RSSIngestResult:
    raw = _fetch_rss_content(feed_url)
    if not raw:
        # CRITICAL: do NOT fall back to feedparser.parse(feed_url). That
        # path uses Python's stdlib urllib internally with NO timeout —
        # a slow/dead feed will hang FOREVER, holding ingest_lock and
        # blocking every subsequent RSS cycle. This was the root cause
        # of a 4-hour ingestion outage on 2026-05-23: one bad feed stalled
        # the entire scheduler. Always use the timeout-bounded httpx
        # path; if it returns None, skip this feed for this cycle.
        logger.warning("ingest_rss: skipping %s (httpx fetch failed)", feed_url)
        return RSSIngestResult(added=0, skipped=0, items=[])
    feed = feedparser.parse(raw)
    added_items: list[SourceItem] = []
    skipped = 0
    _build_outlet_index_cache: dict = {}  # lazy-loaded once per feed, not per entry
    # Cache the campaign's canonical proper-noun list once per feed cycle —
    # used to fix auto-caption garbling on YouTube transcripts. Cheap query
    # and small payload, so doing it eagerly per ingest_rss call is fine.
    _canonical_names_cache: list[str] = _campaign_canonical_names(db)

    for entry in feed.entries[:50]:
        url = entry.get("link") or ""
        title = _strip_tags(entry.get("title") or "Untitled")[:200]

        # Deduplicate by source_url
        if url and db.query(SourceItem).filter_by(source_url=url).first():
            skipped += 1
            continue

        raw_text = _strip_tags(entry.get("summary") or entry.get("description") or "")[:4000]

        # Extract publisher_domain BEFORE body recovery so recover_body's
        # title-search fallback (used when the Google News decoder fails)
        # has the publisher to search against. Same code that ran below
        # before — moved up so the recovery step can see it.
        publisher_domain: str | None = None
        entry_source = entry.get("source") or {}
        if entry_source and "news.google.com" in feed_url:
            src_href = entry_source.get("href") or ""
            if src_href:
                from urllib.parse import urlparse as _urlparse
                import re as _re
                publisher_domain = _re.sub(r"^www\.", "", _urlparse(src_href).netloc).lower() or None

        # Fallback: ~60% of Google News entries carry no `source.href`, so the
        # block above leaves publisher_domain None. Derive it from the title's
        # "- Publisher" suffix mapped against the outlet catalog. This unlocks
        # recover_body's title-search path (which otherwise has no site to
        # query) and improves outlet attribution. Catalog-driven — no per-
        # campaign hardcoding, so it generalizes to any NOCTUA tenant.
        if not publisher_domain and "news.google.com" in feed_url:
            from app.services.outlet_linking import (
                build_outlet_name_domain_index,
                derive_publisher_domain_from_title,
            )
            _ndx = _build_outlet_index_cache.get("name_domain")
            if _ndx is None:
                _ndx = build_outlet_name_domain_index(db)
                _build_outlet_index_cache["name_domain"] = _ndx
            publisher_domain = derive_publisher_domain_from_title(title, _ndx)

        # Body recovery for short RSS payloads.
        #
        # Three scenarios this catches:
        #   (a) Google News intermediary feeds where, since 2026-05-26,
        #       Google stopped including body excerpts — every entry is
        #       just a `<title> <outlet>` string. We try to decode the
        #       redirect to the underlying publisher URL and fetch from
        #       there; if the decoder fails (it currently does, in any
        #       geography — Google removed the data attrs), we fall back
        #       to a title-based search on `publisher_domain`.
        #   (b) Direct publisher feeds (e.g. timesleader.com/feed/) that
        #       only ship 200-400 char excerpts. We follow `entry.link`
        #       to the article page and run our readability pipeline.
        #   (c) YouTube-via-Google-News items: the decoder, when it
        #       works, resolves to a youtube.com URL — captured below as
        #       `resolved_url` for the transcript path.
        #
        # Recovery is best-effort — failure leaves raw_text unchanged.
        resolved_url: str | None = None
        if url:
            from app.services.article_body_recovery import recover_body
            recovered_body, resolved_url = recover_body(
                url, raw_text,
                publisher_domain=publisher_domain,
                title=title,
            )
            if recovered_body:
                raw_text = recovered_body[:4000]

        # YouTube videos: the RSS feed only carries title + description, not
        # the spoken content. Fetch the auto-generated transcript when we
        # can — politicians' video remarks are first-party signal that
        # otherwise wouldn't reach the scoring pipeline. Append to raw_text
        # so downstream LLM scoring / frame matching sees it as if it were
        # article body.
        #
        # The video_id lookup tries the raw URL first; if that's a Google
        # News redirect that we decoded above, the resolved URL is where
        # the youtube.com link actually lives.
        video_id = _youtube_video_id(url) or _youtube_video_id(resolved_url)
        if video_id:
            transcript = _fetch_youtube_transcript(video_id)
            if transcript:
                # Normalize caption-mangled proper nouns ("Connetty" →
                # "Cognetti") so FTS5 search and string matches don't
                # silently miss transcript hits.
                transcript = _correct_transcript_proper_nouns(
                    transcript, _canonical_names_cache
                )
                if raw_text:
                    raw_text = f"{raw_text}\n\n[Transcript]\n{transcript}"
                else:
                    raw_text = f"[Transcript]\n{transcript}"

        published = _rss_published_at(getattr(entry, "published_parsed", None))

        # feedparser surfaces the byline as entry.author or entry.author_detail.name
        rss_author = (
            entry.get("author")
            or (entry.get("author_detail") or {}).get("name")
            or ""
        )
        source_author = _normalize_text(rss_author)[:200] or None

        inferred_type = "reference" if (not published and _is_reference_url(url)) else "news"

        # publisher_domain was extracted above (before recover_body), so the
        # title-search fallback could see it. This block just handles the
        # fallback for when entry.source was empty but the recovery path
        # resolved a URL we can derive a domain from.
        # Fallback: if the decoder resolved a Google News redirect, use the
        # resolved URL's domain when entry.source didn't carry one. This
        # mostly affects YouTube-via-Google-News entries where entry.source
        # is the youtube.com homepage anyway, but it also covers feeds where
        # Google omits the source element.
        if not publisher_domain and resolved_url:
            from urllib.parse import urlparse as _urlparse
            import re as _re
            try:
                publisher_domain = _re.sub(r"^www\.", "", _urlparse(resolved_url).netloc).lower() or None
            except Exception:
                publisher_domain = None

        item = SourceItem(
            title=clean_title(title),
            raw_text=raw_text,
            source_url=url or None,
            source_name=label or feed.feed.get("title", feed_url),
            source_type=inferred_type,
            published_at=published,
            source_author=source_author,
            publisher_domain=publisher_domain,
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
