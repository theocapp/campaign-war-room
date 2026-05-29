"""Sample merges from a dump file for false-positive review.

Usage:
    python -m app.scripts.sample_merges /tmp/merges_iter1.json

Prints stats by rule, top-N largest merges, and a random sample of rule-3
merges (the riskiest tier) for manual eyeballing.
"""
from __future__ import annotations
import argparse
import json
import random
import sys
from collections import Counter


def _rule_of(reason: str) -> str:
    if reason == "url":
        return "url"
    if reason.startswith("title=") and "temporal" not in reason:
        return "rule2"
    if "temporal" in reason:
        return "rule3"
    return reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--sample", type=int, default=40,
                        help="How many rule-3 merges to sample (default 40)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    with open(args.path) as f:
        merges = json.load(f)
    random.seed(args.seed)

    by_rule = Counter(_rule_of(m["reason"]) for m in merges)

    print(f"\nTotal merges:  {len(merges)}")
    print("By rule:")
    for k, v in by_rule.most_common():
        print(f"  {k:<10} {v}")

    # Top largest incoming merges (by old_count) per rule
    for rule in ("url", "rule2", "rule3"):
        bucket = [m for m in merges if _rule_of(m["reason"]) == rule]
        if not bucket:
            continue
        print(f"\nTop 15 {rule} merges by old_count:")
        for m in sorted(bucket, key=lambda x: x["old_count"], reverse=True)[:15]:
            print(f"  [{m['old_count']:>3}] {m['reason']:<35}")
            print(f"        old: {m['old_title']}")
            print(f"        new: {m['new_title']}")

    # Random sample of rule-3 (the riskiest tier)
    rule3 = [m for m in merges if _rule_of(m["reason"]) == "rule3"]
    if rule3:
        sample = random.sample(rule3, min(args.sample, len(rule3)))
        print(f"\nRandom sample of {len(sample)} rule-3 merges (for FP review):")
        for m in sample:
            print(f"  {m['reason']:<35}")
            print(f"    old: {m['old_title']}")
            print(f"    new: {m['new_title']}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
