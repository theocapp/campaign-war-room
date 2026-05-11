#!/usr/bin/env python3
"""
Load a source pack JSON, register its feeds/URLs, run ingestion, cluster, and alert.

Usage
─────
  python scripts/ingest_source_pack.py --pack pa_08
  python scripts/ingest_source_pack.py --pack pa_08 --limit 200 --relevance-min 0.3 --days 60

  make ingest-pa08          (from repo root — sets ENABLE_KG_PIPELINE + LLM_PROVIDER)
  make ingest-pack PACK=pa_08

Options
───────
  --pack NAME        Source pack name (looks for app/source_packs/<name>.json)
  --limit N          Max RSS entries to process per feed (default: 50)
  --relevance-min F  Only KG-ingest items with race_relevance_score >= F (default: 0.0)
  --days N           Clustering look-back window in days (default: 60)
  --dry-run          Print what would be done without writing to the DB

Idempotency
───────────
  Existing RssFeeds are skipped (matched by URL).
  Existing SourceItems are skipped (matched by source_url).
  Running twice does not duplicate rows.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("ENABLE_KG_PIPELINE", "1")

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ingest_source_pack")

# ── App imports (after path setup) ───────────────────────────────────────────
from app.db import SessionLocal
from app.models import RssFeed, SourceItem
from app.knowledge_graph.orm import KGAlert, KGClaim, KGNarrative, KGSource
from app.knowledge_graph.narrative_engine import generate_alerts, get_emerging_narratives, run_clustering
from app.services.ingestion import ingest_rss, ingest_url


# ── Source pack loader ────────────────────────────────────────────────────────

_PACKS_DIR = _BACKEND / "app" / "source_packs"

_RSS_SIGNALS = ("/rss", "/feed", "/atom", ".rss", ".atom", "rss.xml", "feed.xml", "feed/")


def _is_rss(url: str) -> bool:
    low = url.lower()
    return any(tok in low for tok in _RSS_SIGNALS)


def load_pack(name: str) -> dict:
    path = _PACKS_DIR / f"{name}.json"
    if not path.exists():
        available = [p.stem for p in _PACKS_DIR.glob("*.json")]
        raise FileNotFoundError(
            f"Source pack '{name}' not found at {path}. "
            f"Available packs: {available or ['(none)']}"
        )
    with path.open() as f:
        data = json.load(f)
    # Basic validation
    required = ("name", "items")
    for field in required:
        if field not in data:
            raise ValueError(f"Source pack JSON missing required field: '{field}'")
    if not isinstance(data["items"], list):
        raise ValueError("Source pack 'items' must be a list")
    return data


# ── Feed registration ─────────────────────────────────────────────────────────

def ensure_rss_feed(db, item: dict) -> tuple[RssFeed, bool]:
    """Upsert an RssFeed row.  Returns (feed, created)."""
    url = item["url"]
    existing = db.query(RssFeed).filter_by(url=url).first()
    if existing:
        return existing, False
    feed = RssFeed(
        name=item.get("name", url),
        url=url,
        source_type=item.get("source_type", "news"),
        active=True,
        created_at=datetime.utcnow(),
    )
    db.add(feed)
    db.flush()
    return feed, True


# ── Ingestion runners ─────────────────────────────────────────────────────────

def run_rss_item(db, item: dict, limit: int) -> tuple[int, int, str]:
    """Returns (added, skipped, error_msg)."""
    url = item["url"]
    label = item.get("name", url)
    try:
        result = ingest_rss(db, url, label=label)
        actual_added = min(result.added, limit)
        return actual_added, result.skipped, ""
    except Exception as exc:
        return 0, 0, str(exc)


def run_url_item(db, item: dict) -> tuple[int, str]:
    """Returns (added, error_msg).  added is 0 or 1."""
    url = item["url"]
    source_type = item.get("source_type", "news")
    try:
        result = ingest_url(db, url, source_type)
        return (1 if result else 0), ""
    except Exception as exc:
        return 0, str(exc)


# ── Summary helpers ───────────────────────────────────────────────────────────

def _db_counts(db) -> dict:
    return {
        "source_items": db.query(SourceItem).count(),
        "kg_sources":   db.query(KGSource).count(),
        "kg_claims":    db.query(KGClaim).count(),
        "kg_narratives": db.query(KGNarrative).count(),
        "active_alerts": db.query(KGAlert).filter(KGAlert.resolved_at.is_(None)).count(),
    }


def _print_table(rows: list[tuple], headers: list[str], col_widths: list[int]) -> None:
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("  " + "  ".join("─" * w for w in col_widths))
    for row in rows:
        print(fmt.format(*[str(v) for v in row]))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack", required=True, help="Source pack name (e.g. pa_08)")
    parser.add_argument("--limit", type=int, default=50, help="Max entries per RSS feed (default 50)")
    parser.add_argument("--relevance-min", type=float, default=0.0,
                        help="Minimum race_relevance_score for KG ingestion (default 0.0)")
    parser.add_argument("--days", type=int, default=60, help="Clustering look-back days (default 60)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing to DB")
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║  Source Pack Ingestion: {args.pack:<34}║")
    print("╚══════════════════════════════════════════════════════════╝")

    # ── Load pack ─────────────────────────────────────────────────────────────
    try:
        pack = load_pack(args.pack)
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n  ERROR: {exc}")
        return 1

    items = pack["items"]
    rss_items = [it for it in items if it.get("url") and _is_rss(it["url"])]
    url_items = [it for it in items if it.get("url") and not _is_rss(it["url"])]

    print(f"\n  Pack     : {pack['name']}")
    print(f"  Level    : {pack.get('race_level', '?')}  /  {pack.get('geography', '?')}")
    print(f"  Items    : {len(items)} total  ({len(rss_items)} RSS feeds, {len(url_items)} URLs)")
    print(f"  Limit    : {args.limit} entries/feed")
    print(f"  Days     : {args.days} (clustering window)")
    print(f"  KG env   : ENABLE_KG_PIPELINE={os.environ.get('ENABLE_KG_PIPELINE', 'unset')}")
    print(f"  Provider : LLM_PROVIDER={os.environ.get('LLM_PROVIDER', 'unset (will use default)')}")

    if args.dry_run:
        print("\n  DRY RUN — no DB writes.\n")
        print("  RSS feeds that would be registered:")
        for it in rss_items:
            print(f"    [{it.get('source_type','?'):20}] {it.get('name','?')}")
            print(f"       {it['url']}")
        print("\n  URLs that would be ingested:")
        for it in url_items:
            print(f"    [{it.get('source_type','?'):20}] {it.get('name','?')}")
            print(f"       {it['url']}")
        return 0

    db = SessionLocal()
    try:
        before = _db_counts(db)

        # ── Step 1: RSS feeds ──────────────────────────────────────────────────
        print(f"\n  ── [1/4] RSS Feeds ({len(rss_items)}) {'─'*40}")
        feed_rows: list[tuple] = []
        total_rss_added = 0
        total_rss_skipped = 0

        for it in rss_items:
            feed, feed_created = ensure_rss_feed(db, it)
            db.commit()

            added, skipped, err = run_rss_item(db, it, args.limit)
            if err:
                status = f"ERROR: {err[:60]}"
                log.warning("RSS ingestion failed for %s: %s", it.get("name"), err)
            else:
                status = "new" if feed_created else "existing"
                total_rss_added += added
                total_rss_skipped += skipped

            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("Commit failed after RSS %s: %s", it.get("name"), exc)
                err = str(exc)

            feed_rows.append((
                it.get("name", it["url"])[:40],
                it.get("source_type", "?"),
                status,
                added if not err else "-",
                skipped if not err else "-",
            ))

        _print_table(
            feed_rows,
            ["Feed", "Type", "Feed Status", "Added", "Skipped"],
            [40, 20, 14, 6, 7],
        )
        print(f"\n  RSS total: {total_rss_added} new items, {total_rss_skipped} skipped")

        # ── Step 2: URL sources ────────────────────────────────────────────────
        print(f"\n  ── [2/4] URL Sources ({len(url_items)}) {'─'*39}")
        url_rows: list[tuple] = []
        total_url_added = 0

        for it in url_items:
            added, err = run_url_item(db, it)
            if err:
                status = f"ERROR: {err[:60]}"
                log.warning("URL ingestion failed for %s: %s", it.get("name"), err)
            else:
                status = "added" if added else "skipped (dup)"
                total_url_added += added

            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("Commit failed after URL %s: %s", it.get("name"), exc)

            url_rows.append((
                it.get("name", it["url"])[:40],
                it.get("source_type", "?"),
                status,
            ))

        _print_table(url_rows, ["Source", "Type", "Status"], [40, 20, 20])
        print(f"\n  URL total: {total_url_added} new items")

        # ── Step 3: Clustering ────────────────────────────────────────────────
        print(f"\n  ── [3/4] Clustering (days={args.days}) {'─'*37}")
        try:
            cluster_report = run_clustering(db, days=args.days)
            db.commit()
            print(
                f"  claims_processed={cluster_report.claims_processed}  "
                f"embedded={cluster_report.claims_embedded}  "
                f"narratives_created={cluster_report.narratives_created}  "
                f"narratives_updated={cluster_report.narratives_updated}  "
                f"links_added={cluster_report.links_added}"
            )
            if cluster_report.errors:
                print(f"  Clustering errors: {cluster_report.errors[:3]}")
        except Exception as exc:
            db.rollback()
            print(f"  WARNING: clustering failed — {exc}")

        # ── Step 4: Alerts ────────────────────────────────────────────────────
        print(f"\n  ── [4/4] Alerts {'─'*50}")
        try:
            new_alerts = generate_alerts(db)
            db.commit()
            print(f"  alerts_generated={len(new_alerts)}")
        except Exception as exc:
            db.rollback()
            print(f"  WARNING: alert generation failed — {exc}")
            new_alerts = []

        # ── Summary ───────────────────────────────────────────────────────────
        after = _db_counts(db)

        print(f"\n  ── Summary {'─'*55}")
        print(f"  {'Metric':<28}  {'Before':>8}  {'After':>8}  {'Delta':>6}")
        print(f"  {'─'*56}")
        for key in ("source_items", "kg_sources", "kg_claims", "kg_narratives", "active_alerts"):
            b, a = before[key], after[key]
            delta = a - b
            delta_str = f"+{delta}" if delta > 0 else str(delta)
            changed = "  ←" if delta != 0 else ""
            print(f"  {key:<28}  {b:>8}  {a:>8}  {delta_str:>6}{changed}")

        # ── Emerging narratives top 10 ────────────────────────────────────────
        print(f"\n  ── Top Emerging Narratives {'─'*40}")
        emerging = get_emerging_narratives(db, limit=10)
        if emerging:
            print(f"  {'ID':>4}  {'score':>7}  {'vel':>7}  {'srcs':>4}  {'ents':>4}  label")
            print(f"  {'─'*72}")
            for en in emerging:
                n = en.narrative
                print(
                    f"  {n.id:>4}  {en.score:>7.4f}  {n.velocity_score or 0:>7.4f}  "
                    f"{en.unique_sources:>4}  {en.unique_entities:>4}  "
                    f"{n.label[:50]}"
                )
        else:
            print("  (no narratives with velocity > 0 yet)")

    finally:
        db.close()

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
