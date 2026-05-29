"""
Cleanup phase 2: delete mentions that V5 + gpt-4o rejected.

Reads /tmp/rescore_cleanup_verdicts.json and deletes NarrativeFrameMention
rows where verdict == REJECT. Logs every deletion. Supports --dry-run.

After deletion, also clears the dot-landscape and topic-regions caches so
the next /api/narrative-frames/landscape-established-dots call rebuilds
with the cleaner data.

Safety
------
1. --dry-run by default. Pass --apply to actually delete.
2. Logs every deleted row's (mention_id, frame_id, frame_name, extract
   preview) so the change is auditable.
3. NEVER touches NarrativeFrame, SourceItem, or any other table. Only
   purges the M2M mention rows. Frames and articles stay intact.
4. Exclusions: a --keep-frame-id flag lets you protect specific frames
   (e.g. if you spot one you don't want auto-purged).

Run:
  # Preview what would be deleted:
  .venv/bin/python scripts/rescore_cleanup_apply.py

  # Actually delete:
  .venv/bin/python scripts/rescore_cleanup_apply.py --apply

  # Protect a specific frame from any deletes:
  .venv/bin/python scripts/rescore_cleanup_apply.py --apply --keep-frame-id 4
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


VERDICTS_PATH = "/tmp/rescore_cleanup_verdicts.json"
DELETION_LOG = "/tmp/rescore_cleanup_deletions.json"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Actually delete (default is dry-run preview only)")
    p.add_argument("--keep-frame-id", type=int, action="append", default=[],
                   help="Protect this frame_id from any deletes (repeat for multiple)")
    args = p.parse_args()

    with open(VERDICTS_PATH) as f:
        verdicts = json.load(f)

    rejects = [v for v in verdicts if v.get("verdict") == "REJECT"]
    protected = set(args.keep_frame_id)
    if protected:
        skipped = [r for r in rejects if r["frame_id"] in protected]
        rejects = [r for r in rejects if r["frame_id"] not in protected]
        print(f"Protected frame_ids {sorted(protected)} — skipping {len(skipped)} rejects within them.")

    # Breakdown
    print(f"\nWill delete {len(rejects)} of {len(verdicts)} mentions:")
    by_frame = Counter(r["frame_name"] for r in rejects)
    for name, n in by_frame.most_common(20):
        total_in_frame = sum(1 for v in verdicts if v["frame_name"] == name)
        print(f"  {n:>4} / {total_in_frame:<4} ({100*n/total_in_frame:.0f}%) — {name}")

    if not args.apply:
        print(f"\n[DRY-RUN] No DB changes made. Re-run with --apply to delete.")
        return

    # ── Apply ────────────────────────────────────────────────────────────
    from app.db import SessionLocal
    from app.models import NarrativeFrameMention

    print(f"\nApplying {len(rejects)} deletes…")
    ids_to_delete = [r["mention_id"] for r in rejects]

    with SessionLocal() as db:
        # Verify each row still exists (defends against race condition where
        # someone else deleted in between scoring + applying).
        existing_ids = {
            x[0] for x in db.query(NarrativeFrameMention.id)
            .filter(NarrativeFrameMention.id.in_(ids_to_delete)).all()
        }
        actual_targets = [i for i in ids_to_delete if i in existing_ids]
        missing = len(ids_to_delete) - len(actual_targets)
        if missing:
            print(f"  {missing} mention(s) already deleted elsewhere; skipping.")

        # Bulk delete — safer than per-row delete() when count is in the hundreds.
        n_deleted = (
            db.query(NarrativeFrameMention)
              .filter(NarrativeFrameMention.id.in_(actual_targets))
              .delete(synchronize_session=False)
        )
        db.commit()
        print(f"  Deleted {n_deleted} NarrativeFrameMention rows.")

    # ── Invalidate caches so the UI sees the new data ────────────────────
    try:
        from app.services.narrative_landscape_established import invalidate_cache
        invalidate_cache()  # cascades to topic_regions and landscape_dots
        print("  Invalidated landscape caches (next GET will rebuild).")
    except Exception as e:
        print(f"  cache invalidate failed (non-fatal): {e}")

    # ── Write deletion log for audit ─────────────────────────────────────
    with open(DELETION_LOG, "w") as f:
        json.dump([
            {
                "mention_id": r["mention_id"],
                "frame_id": r["frame_id"],
                "frame_name": r["frame_name"],
                "extract_preview": (r["extract"] or "")[:200],
                "reason": r.get("reason", ""),
            }
            for r in rejects
        ], f, indent=2)
    print(f"  Deletion log → {DELETION_LOG}")
    print(f"\nDone. {n_deleted} rows purged. The Landscape page will reflect the change on next load.")


if __name__ == "__main__":
    main()
