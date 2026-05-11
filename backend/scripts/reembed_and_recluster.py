#!/usr/bin/env python3
"""
Resets KGClaim embeddings and narrative assignments for the target window,
then re-embeds claims via the current batch TF-IDF scheme and re-runs
narrative clustering so that cross-source grouping can occur.

Why three steps?
────────────────
run_clustering() skips claims that already have a KGNarrativeClaim row.
So clearing embeddings alone is insufficient — we must also remove the stale
per-claim narrative assignments before clustering can reassign them.

Usage
─────
  python backend/scripts/reembed_and_recluster.py --yes
  python backend/scripts/reembed_and_recluster.py --yes --days 60
  python backend/scripts/reembed_and_recluster.py          # dry-run

  make reembed-recluster   (from repo root)

Options
───────
  --yes        Required to write to the DB.  Without it, only a dry-run
               summary is printed.
  --days N     (default 30) Limit to claims created in the last N days.
               Pass --days 0 to reset ALL claims.

Idempotency
───────────
  Safe to run multiple times.  Narrative links are re-created fresh each run.
  Claims outside the window are untouched.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("ENABLE_KG_PIPELINE", "1")

from app.db import SessionLocal
from app.knowledge_graph.orm import KGAlert, KGClaim, KGNarrative, KGNarrativeClaim
from app.knowledge_graph.narrative_engine import generate_alerts, run_clustering


# ── Reporting helpers ─────────────────────────────────────────────────────────

def _narrative_stats(db) -> dict:
    narratives = db.query(KGNarrative).all()
    if not narratives:
        return {
            "total_narratives": 0,
            "single_claim_pct": 0.0,
            "avg_unique_sources": 0.0,
            "max_velocity": 0.0,
        }

    single_claim = 0
    source_counts = []
    velocities = []

    for n in narratives:
        nc = db.query(KGNarrativeClaim).filter_by(narrative_id=n.id).count()
        if nc == 1:
            single_claim += 1

        src_ids = (
            db.query(KGClaim.source_id)
            .join(KGNarrativeClaim, KGNarrativeClaim.claim_id == KGClaim.id)
            .filter(KGNarrativeClaim.narrative_id == n.id, KGClaim.source_id.isnot(None))
            .distinct()
            .count()
        )
        source_counts.append(src_ids)
        velocities.append(n.velocity_score or 0.0)

    return {
        "total_narratives": len(narratives),
        "single_claim_pct": single_claim / len(narratives) * 100 if narratives else 0.0,
        "avg_unique_sources": sum(source_counts) / len(source_counts),
        "max_velocity": max(velocities),
    }


def _snapshot(db) -> dict:
    return {
        "total_claims":    db.query(KGClaim).count(),
        "active_alerts":   db.query(KGAlert).filter(KGAlert.resolved_at.is_(None)).count(),
        **_narrative_stats(db),
    }


def _print_row(label: str, before, after) -> None:
    changed = "  ←" if str(before) != str(after) else ""
    print(f"  {label:<34}  {str(before):>10}  →  {str(after):<10}{changed}")


def _print_report(before: dict, after: dict) -> None:
    print()
    print("  " + "─" * 70)
    print(f"  {'Metric':<34}  {'Before':>10}     {'After':<10}")
    print("  " + "─" * 70)
    _print_row("total kg_claims",          before["total_claims"],       after["total_claims"])
    _print_row("total kg_narratives",      before["total_narratives"],    after["total_narratives"])
    _print_row("% narratives 1-claim",
               f"{before['single_claim_pct']:.1f}%",
               f"{after['single_claim_pct']:.1f}%")
    _print_row("avg unique_sources/narr",
               f"{before['avg_unique_sources']:.2f}",
               f"{after['avg_unique_sources']:.2f}")
    _print_row("max velocity_score",
               f"{before['max_velocity']:.5f}",
               f"{after['max_velocity']:.5f}")
    _print_row("active alerts",            before["active_alerts"],       after["active_alerts"])
    print("  " + "─" * 70)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Required to actually write to the DB.",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Reset claims created in the last N days (0 = all). Default: 30.",
    )
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║           KG Re-embed + Re-cluster                       ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    db = SessionLocal()
    try:
        # ── 1. Snapshot before ────────────────────────────────────────────────
        print("  Collecting before-snapshot …")
        before = _snapshot(db)

        # Determine target claim IDs
        if args.days > 0:
            cutoff = datetime.utcnow() - timedelta(days=args.days)
            target_claims = db.query(KGClaim).filter(KGClaim.created_at >= cutoff).all()
        else:
            target_claims = db.query(KGClaim).all()

        target_ids = [c.id for c in target_claims]

        # Count how many KGNarrativeClaim links will be removed
        links_to_remove = (
            db.query(KGNarrativeClaim)
            .filter(KGNarrativeClaim.claim_id.in_(target_ids))
            .count()
        ) if target_ids else 0

        # Narratives that would become empty after link removal
        narrative_ids_with_links = set(
            row.narrative_id
            for row in db.query(KGNarrativeClaim.narrative_id)
            .filter(KGNarrativeClaim.claim_id.in_(target_ids))
            .all()
        ) if target_ids else set()

        print()
        print(f"  Claims in window ({('all' if args.days == 0 else f'last {args.days} days')}): {len(target_ids)}")
        print(f"  Narrative links to remove:  {links_to_remove}")
        print(f"  Narratives potentially emptied: {len(narrative_ids_with_links)}")
        print()

        if not args.yes:
            print("  DRY RUN — pass --yes to execute.")
            print()
            print("  Before snapshot:")
            for k, v in before.items():
                print(f"    {k:<34}: {v}")
            print()
            return 0

        if not target_ids:
            print("  No claims found in window — nothing to do.")
            return 0

        # ── 2. Remove narrative claim links for target claims ─────────────────
        print("  [1/5] Removing stale KGNarrativeClaim links …")
        removed_links = (
            db.query(KGNarrativeClaim)
            .filter(KGNarrativeClaim.claim_id.in_(target_ids))
            .delete(synchronize_session=False)
        )
        db.flush()
        print(f"        removed {removed_links} link(s)")

        # Delete narratives that are now empty (no remaining claims)
        orphaned = 0
        for nid in narrative_ids_with_links:
            remaining = db.query(KGNarrativeClaim).filter_by(narrative_id=nid).count()
            if remaining == 0:
                db.query(KGNarrative).filter_by(id=nid).delete(synchronize_session=False)
                orphaned += 1
        db.flush()
        print(f"        deleted {orphaned} now-empty narrative(s)")

        # ── 3. Clear stale embeddings ─────────────────────────────────────────
        print("  [2/5] Clearing stale embeddings …")
        cleared = (
            db.query(KGClaim)
            .filter(KGClaim.id.in_(target_ids))
            .update({"embedding": None}, synchronize_session=False)
        )
        db.commit()
        print(f"        cleared {cleared} embedding(s)")

        # ── 4. Re-cluster ─────────────────────────────────────────────────────
        print("  [3/5] Running run_clustering(days=30) …")
        report = run_clustering(db, days=30)
        db.commit()
        print(
            f"        claims_processed={report.claims_processed}  "
            f"embedded={report.claims_embedded}  "
            f"narratives_created={report.narratives_created}  "
            f"narratives_updated={report.narratives_updated}  "
            f"links_added={report.links_added}"
        )
        if report.errors:
            print(f"        ERRORS: {report.errors}")

        # ── 5. Generate alerts ────────────────────────────────────────────────
        print("  [4/5] Running generate_alerts() …")
        new_alerts = generate_alerts(db)
        db.commit()
        print(f"        alerts_generated={len(new_alerts)}")

        # ── 6. Snapshot after ─────────────────────────────────────────────────
        print("  [5/5] Collecting after-snapshot …")
        after = _snapshot(db)

        # ── 7. Report ─────────────────────────────────────────────────────────
        print()
        print("  Before → After")
        _print_report(before, after)

        single_pct = after["single_claim_pct"]
        avg_src    = after["avg_unique_sources"]
        if single_pct < 50 and avg_src > 1.5:
            verdict = "forming multi-source narratives ✓"
        elif single_pct < before["single_claim_pct"] - 5:
            verdict = "partial improvement — some cross-source clustering occurred"
        else:
            verdict = "still over-fragmenting — check embedding quality or corpus size"
        print(f"  DIAGNOSIS: {verdict}")
        print()

    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
