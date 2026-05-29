"""Resolve Google News redirect URLs to real publisher URLs + fetch bodies.

Problem this solves: many race-relevant articles in the corpus have
`source_url` pointing at `news.google.com/rss/articles/CBMI...` (Google
News' opaque redirect URL). The raw_text we ingested is just the article
title — the actual body lives behind the redirect, on the publisher's
site. Under v15.0's verbatim-quote validator, these articles can't
produce any claims.

This script:
  1. For each race-relevant article with a GN redirect URL and short
     raw_text, decodes the URL via the googlenewsdecoder library
     (no HTTP — pure base64-protobuf decode).
  2. If the resolved URL is YouTube, marks the article unprocessable
     and skips (no transcript = no body).
  3. Otherwise fetches the publisher page using the same pipeline as
     `app.services.ingestion.ingest_url`:
       - httpx GET with Mozilla user-agent
       - On 4xx/timeout, fallback to Wayback Machine
       - _clean_html_with_quality → standard paragraph extraction
       - _try_readability_extraction → Mozilla Readability rescue
       - One more Wayback rescue if body still thin
  4. Updates source_items.raw_text and source_url in-place.

GENERIC — works for any campaign whose ingestion goes through Google
News (most do). No race-specific config.

USAGE:
    python scripts/resolve_google_news_redirects.py          # dry-run, sample 25
    python scripts/resolve_google_news_redirects.py --apply  # write to DB
    python scripts/resolve_google_news_redirects.py --apply --limit 0  # all
    python scripts/resolve_google_news_redirects.py --apply --workers 4  # 4x parallel

Idempotent — only processes articles where LENGTH(raw_text) < 500.
Successful resolves bump raw_text past that threshold so re-runs skip them.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from googlenewsdecoder import gnewsdecoder
from sqlalchemy import func

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import SourceItem
from app.services.ingestion import (
    _clean_html_with_quality,
    _try_readability_extraction,
    _try_wayback_fallback,
)


# Domains where the redirect resolves to a media platform that has no
# extractable text (a video / image / podcast). We mark these so the
# v15.0 backfill query can filter them out — no point paying LLM cost
# on a quote we'll never be able to verbatim-validate.
NO_BODY_DOMAINS: frozenset[str] = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "vimeo.com", "www.vimeo.com",
    "tiktok.com", "www.tiktok.com",
    "instagram.com", "www.instagram.com",
    "soundcloud.com", "www.soundcloud.com",
    "podcasts.apple.com", "open.spotify.com",
})

MIN_BODY_CHARS = 500   # below this, we leave the article alone


def _fetch_body(url: str) -> tuple[Optional[str], str]:
    """Mirror the body-extraction path from ingest_url: direct → Wayback
    → readability → Wayback rescue. Returns (body_text, method).

    method ∈ {"direct", "direct+readability", "wayback", "wayback (rescue)",
              "fetch_failed"}.
    """
    html_text: Optional[str] = None
    archived = False
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Noctua-Ingest/1.0)"
        })
        resp.raise_for_status()
        html_text = resp.text
    except Exception:
        html_text, archived = _try_wayback_fallback(url)
        if not html_text:
            return (None, "fetch_failed")

    _title, body_text, *_ = _clean_html_with_quality(html_text)
    method = "wayback" if archived else "direct"

    if not body_text or len(body_text.split()) < 80:
        _, rb_body = _try_readability_extraction(html_text)
        if rb_body and len(rb_body.split()) > len((body_text or "").split()):
            body_text = rb_body
            method += "+readability"

    if not archived and (not body_text or len(body_text.split()) < 80):
        wb_html, wb_ok = _try_wayback_fallback(url)
        if wb_ok and wb_html:
            _, wb_body, *_ = _clean_html_with_quality(wb_html)
            if wb_body and len(wb_body.split()) > len((body_text or "").split()):
                body_text = wb_body
                method = "wayback (rescue)"

    return (body_text or None, method)


def _resolve_one(
    article_id: int,
    source_url: str,
    title: Optional[str],
    sleep_after: float = 0.0,
) -> dict:
    """Resolve one article. Returns a stats dict per-article. Pure function —
    no DB writes (caller handles persistence to allow parallelism via threads
    without sharing a session)."""
    out = {
        "article_id": article_id,
        "status": "unknown",
        "resolved_url": None,
        "domain": None,
        "body_len": 0,
        "method": None,
        "error": None,
    }
    # Decode
    try:
        dec = gnewsdecoder(source_url, interval=1)
    except Exception as e:
        out["status"] = "decode_error"
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out
    if not dec or not dec.get("status"):
        out["status"] = "decode_failed"
        out["error"] = str(dec)[:120] if dec else "no result"
        return out
    pub_url = dec.get("decoded_url") or ""
    out["resolved_url"] = pub_url
    domain = urlparse(pub_url).netloc.lower()
    out["domain"] = domain

    if domain in NO_BODY_DOMAINS:
        out["status"] = "no_body_platform"
        return out

    # Fetch
    body, method = _fetch_body(pub_url)
    out["method"] = method
    if not body:
        out["status"] = "fetch_failed"
        return out
    out["body_len"] = len(body)
    if len(body) < MIN_BODY_CHARS:
        out["status"] = "still_short"
        out["body_preview"] = body[:200]
        return out

    out["status"] = "ok"
    out["body"] = body
    if sleep_after:
        time.sleep(sleep_after)
    return out


def run(apply: bool, limit: int, workers: int) -> dict:
    """Main driver."""
    stats: dict = {
        "considered": 0, "ok": 0, "still_short": 0,
        "no_body_platform": 0, "decode_failed": 0,
        "decode_error": 0, "fetch_failed": 0, "by_domain": {},
    }
    by_status_examples: dict[str, list] = {}

    with SessionLocal() as db:
        q = (
            db.query(SourceItem)
            .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
            .filter(SourceItem.race_relevance_score >= 50)
            .filter(func.length(SourceItem.raw_text) < MIN_BODY_CHARS)
            .filter(SourceItem.source_url.like("https://news.google.com/rss/articles%"))
        )
        if limit > 0:
            q = q.limit(limit)
        candidates = q.all()
        stats["considered"] = len(candidates)
        print(f"Found {len(candidates)} GN-redirect stub articles to process "
              f"(workers={workers}, apply={apply})")
        print()

        t0 = time.time()
        # Resolve in parallel (network-bound, GIL-friendly)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_resolve_one, a.id, a.source_url, a.title): a
                for a in candidates
            }
            for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
                art = futures[fut]
                res = fut.result()
                status = res["status"]
                stats[status] = stats.get(status, 0) + 1
                if res.get("domain"):
                    d = res["domain"]
                    stats["by_domain"][d] = stats["by_domain"].get(d, 0) + 1
                # Save first 2 examples of each status for the report
                by_status_examples.setdefault(status, [])
                if len(by_status_examples[status]) < 2:
                    by_status_examples[status].append({
                        "id": res["article_id"], "title": (art.title or "")[:80],
                        "resolved": res.get("resolved_url"), "body_len": res.get("body_len"),
                        "method": res.get("method"), "error": res.get("error"),
                    })

                # Apply
                if apply and status == "ok":
                    art_obj = db.query(SourceItem).filter(SourceItem.id == res["article_id"]).first()
                    if art_obj:
                        art_obj.raw_text = res["body"]
                        art_obj.source_url = res["resolved_url"]
                        # Commit in batches of 25 to avoid one huge transaction
                        if i % 25 == 0:
                            db.commit()

                if i % 25 == 0 or i == len(candidates):
                    elapsed = time.time() - t0
                    rate = i / max(elapsed, 0.001)
                    eta = (len(candidates) - i) / max(rate, 0.001)
                    print(f"  [{i:5d}/{len(candidates)}] {rate:.1f}/s  ETA {eta/60:.1f}m  "
                          f"| ok={stats.get('ok', 0)} still_short={stats.get('still_short', 0)} "
                          f"no_body={stats.get('no_body_platform', 0)} fail={stats.get('decode_failed', 0)+stats.get('decode_error', 0)+stats.get('fetch_failed', 0)}")

        if apply:
            db.commit()

    # Report
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for k, v in stats.items():
        if k == "by_domain":
            continue
        print(f"  {k:24s} {v}")
    print()
    print("Top 10 domains (when resolution succeeded):")
    for d, n in sorted(stats["by_domain"].items(), key=lambda x: -x[1])[:10]:
        print(f"  {n:>5}  {d}")
    print()
    print("Examples by status:")
    for status, examples in by_status_examples.items():
        print(f"  [{status}]")
        for e in examples:
            print(f"    art {e['id']}: {e['title']!r}")
            if e.get("resolved"):
                print(f"      → {e['resolved'][:110]}")
            if e.get("body_len"):
                print(f"      body: {e['body_len']} chars  method: {e.get('method')}")
            if e.get("error"):
                print(f"      error: {e['error']}")
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write resolved bodies + URLs to DB. Default is dry-run.")
    p.add_argument("--limit", type=int, default=25,
                   help="Process N articles (0 = all). Default 25 for fast dry-runs.")
    p.add_argument("--workers", type=int, default=4,
                   help="Parallel threads for the network fetches. Default 4.")
    args = p.parse_args()
    run(apply=args.apply, limit=args.limit, workers=args.workers)
