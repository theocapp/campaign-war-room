"""
Audit helper for the cleanup-scoring output.

Reads the checkpoint JSON written by rescore_cleanup_score.py and lets
you eyeball the verdicts in chunks. Used DURING scoring (it reads the
most recent checkpoint) to decide whether to keep the cleanup job running.

Usage:
  # Look at the latest checkpoint, sample 10 KEEPs + 10 REJECTs:
  .venv/bin/python scripts/rescore_cleanup_audit.py

  # Sample more per category:
  .venv/bin/python scripts/rescore_cleanup_audit.py --n 20

  # Look at only the LAST N scored (recent batch):
  .venv/bin/python scripts/rescore_cleanup_audit.py --window 100
"""
from __future__ import annotations
import argparse
import json
import os
import random
from collections import Counter

CHECKPOINT_PATH = "/tmp/rescore_cleanup_verdicts.json"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=10, help="samples per verdict category")
    p.add_argument("--window", type=int, default=0,
                   help="only consider the last N records (default: all)")
    p.add_argument("--frame-id", type=int, default=None,
                   help="filter to a specific frame_id")
    args = p.parse_args()

    if not os.path.exists(CHECKPOINT_PATH):
        print(f"No checkpoint at {CHECKPOINT_PATH} yet — scoring not started.")
        return

    with open(CHECKPOINT_PATH) as f:
        records = json.load(f)

    if args.window:
        records = records[-args.window:]
    if args.frame_id:
        records = [r for r in records if r.get("frame_id") == args.frame_id]

    if not records:
        print("No records matching filters.")
        return

    counts = Counter(r.get("verdict", "?") for r in records)
    print(f"\n=== {len(records)} records ===")
    for v, c in counts.most_common():
        print(f"  {v:>8}: {c} ({100*c/len(records):.1f}%)")

    # Reject distribution by frame — quick way to spot a frame being over-rejected
    print("\nReject rate by frame (top 10 most-mentioned frames):")
    by_frame = {}
    for r in records:
        f = r.get("frame_name", "?")
        d = by_frame.setdefault(f, {"keep": 0, "reject": 0})
        if r["verdict"] == "KEEP":   d["keep"] += 1
        if r["verdict"] == "REJECT": d["reject"] += 1
    top_frames = sorted(by_frame.items(), key=lambda x: -(x[1]["keep"] + x[1]["reject"]))[:10]
    for fname, d in top_frames:
        total = d["keep"] + d["reject"]
        rej_pct = 100 * d["reject"] / max(1, total)
        bar = "█" * int(rej_pct / 5)
        print(f"  {fname[:42]:<42} {total:>3} mentions, {rej_pct:5.1f}% reject {bar}")

    random.seed(42)
    keeps = [r for r in records if r.get("verdict") == "KEEP"]
    rejects = [r for r in records if r.get("verdict") == "REJECT"]

    print(f"\n=== Sample of {min(args.n, len(keeps))} KEEPs ===")
    for r in random.sample(keeps, min(args.n, len(keeps))):
        print(f"\n  frame:   {r['frame_name']}")
        print(f"  extract: {r['extract'][:200].strip()}")
        print(f"  reason:  {r['reason']}")

    print(f"\n\n=== Sample of {min(args.n, len(rejects))} REJECTs ===")
    for r in random.sample(rejects, min(args.n, len(rejects))):
        print(f"\n  frame:   {r['frame_name']}")
        print(f"  extract: {r['extract'][:200].strip()}")
        print(f"  reason:  {r['reason']}")


if __name__ == "__main__":
    main()
