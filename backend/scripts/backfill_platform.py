"""Backfill: derive and set source_items.platform from URL/source_name.

Context (2026-05-31 social-ingestion audit)
-------------------------------------------
`source_type` does not track platform — RSS ingestion stamps every feed item
"news"/"reference" regardless of the feed's configured type, so Twitter (via
Nitter), YouTube, and Reddit-via-RSS hide inside "news". The new `platform`
column (see Alembic revision adding it) is an orthogonal tag computed by
app.services.platform_classify.derive_platform from the item URL (primary)
and source_name (fallback).

This one-shot computes the platform for every existing row and writes it.

Default mode is DRY-RUN (no writes): it prints the platform distribution, a
cross-tab against source_type (so you can see how much social was mislabeled),
and per-platform samples. Pass --commit to apply.

USAGE:
    cd backend && .venv/bin/python scripts/backfill_platform.py
    cd backend && .venv/bin/python scripts/backfill_platform.py --commit
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import engine
from app.services.platform_classify import derive_platform


def _fetch_rows():
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT id, source_url, source_name, source_type FROM source_items"
        )).fetchall()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write platform to DB. Default is dry-run.")
    args = parser.parse_args()

    rows = _fetch_rows()
    total = len(rows)
    print(f"Scanned {total} source_items.\n")

    dist = Counter()
    by_platform_sourcetype = defaultdict(Counter)   # platform -> source_type -> n
    samples = defaultdict(list)
    id_to_platform: dict[int, str] = {}

    for rid, url, name, stype in rows:
        p = derive_platform(url, name)
        key = p or "(news/web)"
        dist[key] += 1
        by_platform_sourcetype[key][stype or "(null)"] += 1
        if p is not None:
            id_to_platform[rid] = p
            if len(samples[key]) < 4:
                samples[key].append((stype, name, (url or "")[:60]))

    print("=== platform distribution ===")
    for key, n in dist.most_common():
        print(f"  {key:<12} {n:>6}  ({100.0*n/total:5.2f}%)")

    social_total = sum(n for k, n in dist.items() if k != "(news/web)")
    print(f"\n  social-platform total: {social_total} ({100.0*social_total/total:.2f}% of corpus)")

    print("\n=== how each platform is currently tagged in source_type ===")
    for key in dist:
        if key == "(news/web)":
            continue
        st = by_platform_sourcetype[key]
        breakdown = ", ".join(f"{k}={v}" for k, v in st.most_common())
        print(f"  {key:<12} -> {breakdown}")

    print("\n=== samples (source_type | source_name | url) ===")
    for key in dist:
        if key == "(news/web)":
            continue
        print(f"  [{key}]")
        for stype, name, url in samples[key]:
            print(f"     {stype} | {name} | {url}")

    if not args.commit:
        print(f"\nDRY-RUN. {len(id_to_platform)} rows would get a non-null platform. "
              f"Re-run with --commit to apply.")
        return

    # --- apply: group ids by platform, one UPDATE per platform (chunked) ----
    by_platform_ids: dict[str, list[int]] = defaultdict(list)
    for rid, p in id_to_platform.items():
        by_platform_ids[p].append(rid)

    written = 0
    with engine.begin() as conn:
        for p, ids in by_platform_ids.items():
            for i in range(0, len(ids), 5000):
                chunk = ids[i:i + 5000]
                conn.execute(
                    text("UPDATE source_items SET platform = :p WHERE id = ANY(:ids)"),
                    {"p": p, "ids": chunk},
                )
                written += len(chunk)
    print(f"\nCOMMITTED. Set platform on {written} rows.")


if __name__ == "__main__":
    main()
