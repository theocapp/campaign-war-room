"""One-off measurement: LLM byline extraction vs the new deterministic extractor.

The new _clean_byline reads SourceItem.source_author (RSS / HTML meta).
The old code asked the LLM to extract a byline from raw_text. They process
different inputs, so this script runs both on the SAME articles and reports
where they agree / disagree.

Usage:
    .venv/bin/python -m app.scripts.byline_llm_vs_deterministic

Costs ~50 Groq calls (~$0.001).
"""
from __future__ import annotations

import csv
import random
import sys
from collections import Counter
from pathlib import Path

from app.db import SessionLocal
from app.models import Outlet, SourceItem
from app.services.llm_provider import MockLLMProvider, get_provider
from app.services.monitors import _byline_from_text, _clean_byline


def deterministic_byline(art, outlet_names):
    """Mirror the production fallback chain in auto_discover_journalists."""
    name = _clean_byline(art.source_author, outlet_names=outlet_names)
    if name:
        return name
    candidate = _byline_from_text(art.title, art.raw_text)
    return _clean_byline(candidate, outlet_names=outlet_names)

# Use the same prompt the OLD code in auto_discover_journalists used.
PROMPT_TEMPLATE = (
    "Title: {title}\n"
    "Source: {source}\n\n"
    "Article excerpt:\n{snippet}\n\n"
    "Who is the byline (author) of this article? Return ONLY the author's "
    "name — no titles, no quotes, no extra text. If the byline is an "
    "institution (AP, Reuters, Staff, Editorial Board, etc.) or there is "
    "no byline, return exactly: NONE"
)


def llm_byline(provider, art: SourceItem) -> str | None:
    """Replicates the OLD LLM byline extraction (auto_discover_journalists)."""
    snippet = (art.raw_text or art.summary or "")[:1500]
    prompt = PROMPT_TEMPLATE.format(
        title=art.title, source=art.source_name or "unknown", snippet=snippet,
    )
    try:
        raw = (provider.complete(prompt) or "").strip()
    except Exception as exc:
        return f"<ERROR: {exc}>"
    if not raw or raw.upper() == "NONE":
        return None
    return raw.removeprefix("By ").removeprefix("by ").strip(' "\'.,:') or None


def main(n_with_author: int = 30, n_without_author: int = 20, seed: int = 7) -> None:
    random.seed(seed)
    db = SessionLocal()
    provider = get_provider()
    if isinstance(provider, MockLLMProvider):
        print("ERROR: real LLM provider not configured (got mock).")
        sys.exit(1)

    outlet_names = {(n or "").lower()
                    for (n,) in db.query(Outlet.name).filter(Outlet.name.isnot(None)).all()}

    # Sample 1: source_author populated, raw_text non-trivial.
    pop_pool = (db.query(SourceItem)
                .filter(SourceItem.source_author.isnot(None),
                        SourceItem.raw_text.isnot(None),
                        SourceItem.race_relevance_score >= 50)
                .all())
    pop_pool = [a for a in pop_pool if a.raw_text and len(a.raw_text) > 300]
    populated = random.sample(pop_pool, min(n_with_author, len(pop_pool)))

    # Sample 2: source_author NULL — the case where deterministic logic finds
    # nothing and the LLM might still extract a byline from the body.
    null_pool = (db.query(SourceItem)
                 .filter(SourceItem.source_author.is_(None),
                         SourceItem.raw_text.isnot(None),
                         SourceItem.race_relevance_score >= 50)
                 .all())
    null_pool = [a for a in null_pool if a.raw_text and len(a.raw_text) > 300]
    null_sample = random.sample(null_pool, min(n_without_author, len(null_pool)))

    rows = []
    for label, sample in [("populated", populated), ("null", null_sample)]:
        for art in sample:
            det = deterministic_byline(art, outlet_names)
            llm = llm_byline(provider, art)
            rows.append({
                "id": art.id,
                "group": label,
                "source_name": art.source_name,
                "source_author_raw": art.source_author,
                "deterministic": det,
                "llm": llm,
                "title": (art.title or "")[:120],
            })
            print(f"  [{label}] id={art.id}  det={det!r}  llm={llm!r}")

    # Write full CSV for inspection.
    out_path = Path(__file__).parent.parent.parent / "byline_comparison.csv"
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {out_path}")

    # Classify each pair.
    cats = Counter()
    name_mismatch_examples = []
    llm_only_examples = []
    det_only_examples = []
    for r in rows:
        d, l = r["deterministic"], r["llm"]
        if d and l:
            # Both extracted something — check name equality (case-insensitive,
            # ignore middle-initial dots).
            def norm(s):
                return s.lower().replace(".", "").strip() if s else ""
            if norm(d) == norm(l):
                cats["agree_name"] += 1
            elif norm(l).startswith(norm(d)) or norm(d).startswith(norm(l)):
                # One is a substring (e.g. "Joe Smith" vs "Joe Smith Jr")
                cats["agree_substring"] += 1
            else:
                cats["disagree_name"] += 1
                name_mismatch_examples.append(r)
        elif d and not l:
            cats["deterministic_only"] += 1
            det_only_examples.append(r)
        elif l and not d:
            cats["llm_only"] += 1
            llm_only_examples.append(r)
        else:
            cats["agree_none"] += 1

    n = len(rows)
    print("\n=== Agreement summary ===")
    for cat in ["agree_name", "agree_substring", "agree_none",
                "deterministic_only", "llm_only", "disagree_name"]:
        c = cats[cat]
        print(f"  {cat:20s}  {c:3d}  ({100*c/n:5.1f}%)")
    print(f"  TOTAL                {n:3d}")
    agree_total = cats["agree_name"] + cats["agree_substring"] + cats["agree_none"]
    print(f"\n  Overall agreement: {agree_total}/{n} ({100*agree_total/n:.1f}%)")

    def dump(label, examples, k=8):
        if not examples:
            return
        print(f"\n=== {label} (showing up to {k}) ===")
        for r in examples[:k]:
            print(f"  id={r['id']}  group={r['group']}  src={r['source_name']!r}")
            print(f"     raw author : {r['source_author_raw']!r}")
            print(f"     det        : {r['deterministic']!r}")
            print(f"     llm        : {r['llm']!r}")
            print(f"     title      : {r['title']!r}")

    dump("LLM found a byline, deterministic missed (FALSE NEGATIVES)", llm_only_examples)
    dump("Deterministic found a byline, LLM missed", det_only_examples)
    dump("Both found names but disagreed", name_mismatch_examples)


if __name__ == "__main__":
    main()
