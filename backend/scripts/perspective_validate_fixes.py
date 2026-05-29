"""V13.22 — Empirical validation of the audit-driven classifier fixes.

Re-classifies the entire 2,350-article corpus with the updated heuristic
classifier (Phases 0-2; the LLM phase is unchanged in test so we don't burn
$$ here). Compares the new method/perspective against:
  - the OLD classification (in perspective_verification.csv)
  - the JUDGE's final adjudication (in perspective_judged.csv, where available)

OUTPUTS:
  Headline counts:
    - outlet_bias: how many now skip vs still fire
    - attribution: how many now skip vs still fire
    - For the previously-classified-but-judged-wrong cases, how many now
      get a different (presumably better) classification

Cost: $0 — only runs the FREE heuristic phases (Phases 0-2). LLM phase is
NOT invoked here; that's a separate one-time rescore job.
Runtime: ~30 seconds.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import SourceItem
from app.services.article_perspective import get_classifier


def main() -> int:
    db = SessionLocal()
    classify = get_classifier(db)

    # Load existing audit (old classification + verifier verdict)
    verif_path = Path(__file__).parent / "perspective_verification.csv"
    judge_path = Path(__file__).parent / "perspective_judged.csv"

    with verif_path.open() as f:
        old_rows = {int(r["id"]): r for r in csv.DictReader(f)}
    judge_rows = {}
    if judge_path.exists():
        with judge_path.open() as f:
            judge_rows = {int(r["id"]): r for r in csv.DictReader(f)}

    print(f"Loaded {len(old_rows)} old classifications, {len(judge_rows)} judged disagreements")
    print()

    # Re-classify every item in the audit set
    ids = list(old_rows.keys())
    items = db.query(SourceItem).filter(SourceItem.id.in_(ids)).all()
    print(f"Re-classifying {len(items)} articles with V13.22 heuristics...")

    # Transition tracking
    method_transition = Counter()  # (old_method, new_method)
    persp_transition = Counter()   # (old_persp, new_persp)
    method_volume_old = Counter()
    method_volume_new = Counter()

    # Did the fix flip the previously-wrong cases to a different method?
    # If old method was outlet_bias OR attribution AND the judge said it was
    # wrong (or the article shouldn't have been classified), did we now skip?
    audit_known_wrong_old_method = Counter()
    audit_known_wrong_new_method = Counter()

    for item in items:
        old = old_rows[item.id]
        old_method = old["classifier_method"]
        old_persp = old["classifier_perspective"]
        method_volume_old[old_method] += 1

        new = classify(item)
        new_method = new.method
        new_persp = new.perspective
        method_volume_new[new_method] += 1

        method_transition[(old_method, new_method)] += 1
        persp_transition[(old_persp, new_persp)] += 1

        # If the audit judged this case wrong (or "neutral better"), what
        # method does the new classifier use?
        j = judge_rows.get(item.id)
        if j and j.get("judge_error_type") in (
            "a_wrong", "both_reasonable_neutral_better", "out_of_race"
        ):
            audit_known_wrong_old_method[old_method] += 1
            audit_known_wrong_new_method[new_method] += 1

    print()
    print("=" * 80)
    print("METHOD VOLUME — OLD vs NEW")
    print("=" * 80)
    all_methods = set(method_volume_old) | set(method_volume_new)
    print(f"  {'method':<14s} {'old':>6s}  {'new':>6s}  {'delta':>7s}")
    for m in sorted(all_methods):
        o = method_volume_old[m]
        n = method_volume_new[m]
        delta = n - o
        sign = "+" if delta >= 0 else ""
        print(f"  {m:<14s} {o:6d}  {n:6d}  {sign}{delta:6d}")
    print()

    print("=" * 80)
    print("METHOD TRANSITIONS — old method → new method (top 12)")
    print("=" * 80)
    for (om, nm), n in method_transition.most_common(12):
        marker = "  " if om == nm else "→ "
        print(f"  {marker}{om:<14s} → {nm:<14s} {n}")
    print()

    print("=" * 80)
    print("PERSPECTIVE TRANSITIONS — old → new (top 10)")
    print("=" * 80)
    for (op, np), n in persp_transition.most_common(10):
        marker = "  " if op == np else "→ "
        print(f"  {marker}{op:<14s} → {np:<14s} {n}")
    print()

    if judge_rows:
        print("=" * 80)
        print("EFFECT ON KNOWN-WRONG CLASSIFICATIONS (judged a_wrong / neutral-better / out_of_race)")
        print("=" * 80)
        print(f"Total known-wrong cases:")
        print(f"  By OLD method:")
        for m, n in audit_known_wrong_old_method.most_common():
            print(f"    {m:<14s} {n}")
        print()
        print(f"  By NEW method (same cases, re-classified):")
        for m, n in audit_known_wrong_new_method.most_common():
            print(f"    {m:<14s} {n}")
        print()

        # The headline: how many previously-wrong cases now fall through to
        # LLM/fallback (i.e. the heuristic-induced error was removed)?
        moved_to_llm_or_fallback = sum(
            1 for item in items
            if (j := judge_rows.get(item.id))
            and j.get("judge_error_type") in ("a_wrong", "both_reasonable_neutral_better", "out_of_race")
            and old_rows[item.id]["classifier_method"] in ("outlet_bias", "attribution")
            and classify(item).method in ("fallback", "llm")
        )
        print(f"Previously-heuristic-misclassified cases that now defer to LLM/fallback: {moved_to_llm_or_fallback}")
        print("  → These will be re-checked by the (also-improved) LLM prompt in the next backfill.")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
