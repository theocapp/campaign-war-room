"""Apply deterministic label correction to existing claim_records.

Built after the 2026-05-28 manual audit found that LLM-emitted labels
were ~70% accurate. This script applies the same regex rules used by
persist_claims (going forward) to the records already in the DB. Zero
LLM calls — pure pattern matching.

USAGE:
    python scripts/relabel_claim_records.py              # dry-run, summary
    python scripts/relabel_claim_records.py --verbose    # show every change
    python scripts/relabel_claim_records.py --apply      # write changes to DB
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import ClaimRecord
from app.services.label_correction import correct_label


def main(apply: bool, verbose: bool, version: str) -> None:
    with SessionLocal() as db:
        records = (
            db.query(ClaimRecord)
            .filter(ClaimRecord.extractor_version == version)
            .all()
        )
        print(f"Loaded {len(records)} claim_records at version {version}")
        print(f"Apply mode: {'YES (writing to DB)' if apply else 'NO (dry-run)'}")
        print()

        # Tally
        transitions: Counter = Counter()  # (orig_label, new_label) → count
        rule_counts: Counter = Counter()
        changed = 0
        examples_by_transition: dict[tuple, list[dict]] = {}

        for cr in records:
            orig = cr.label
            new_label, rule = correct_label(cr.evidence_span, orig)
            transitions[(orig, new_label)] += 1
            rule_counts[rule] += 1
            if new_label != orig:
                changed += 1
                key = (orig, new_label)
                examples_by_transition.setdefault(key, [])
                if len(examples_by_transition[key]) < 2:
                    examples_by_transition[key].append({
                        "id": cr.id,
                        "rule": rule,
                        "span": cr.evidence_span[:160],
                    })
                if apply:
                    cr.label = new_label

        if apply:
            db.commit()
            print(f"COMMITTED {changed} label changes to DB.")
        else:
            db.rollback()
            print(f"DRY-RUN: would change {changed} of {len(records)} labels.")

        # Summary tables
        print()
        print("Transitions (orig → new) — most common first:")
        print(f"  {'orig':18s} {'→':3s} {'new':18s} count")
        for (orig, new), n in sorted(transitions.items(), key=lambda x: -x[1])[:30]:
            tag = " (no change)" if orig == new else ""
            print(f"  {str(orig):18s} {'→':3s} {str(new):18s} {n:>5}{tag}")

        print()
        print("Rule fire counts:")
        for rule, n in rule_counts.most_common(20):
            print(f"  {n:>5}  {rule}")

        if verbose and examples_by_transition:
            print()
            print("Examples of each change type (first 2 per transition):")
            for (orig, new), examples in sorted(examples_by_transition.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
                if orig == new:
                    continue
                print(f"\n  {orig} → {new}:")
                for e in examples:
                    print(f"    art-claim {e['id']} [{e['rule']}]")
                    print(f"      {e['span']!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write the corrected labels to the DB. Default is dry-run.")
    p.add_argument("--verbose", action="store_true",
                   help="Show example spans for each transition type.")
    p.add_argument("--version", default="v15.0",
                   help="Only process records at this extractor_version. Default v15.0.")
    args = p.parse_args()
    main(apply=args.apply, verbose=args.verbose, version=args.version)
