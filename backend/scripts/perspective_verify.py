"""Second-pass LLM verifier audit on all classified articles.

For each classified article, ask gpt-4o-mini "is this classification correct?"
using a deliberately DIFFERENT phrasing than the classifier prompt (so we're
not just measuring agreement-by-prompt-similarity).

OUTPUTS:
  - scripts/perspective_verification.csv: every article + classifier verdict
    + verifier verdict + agreement flag + reason
  - Summary stats: agreement rate, disagreement breakdown by perspective
  - List of likely-misclassified articles (verifier flags) for human review

Cost: ~2350 LLM calls × ~$0.0001 = ~$0.25.
Runtime: ~5-15 min.
"""
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.llm_provider import OpenAIProvider, _parse_json_response


VERIFIER_PROMPT = """You're a quality reviewer for political-news classification.

You'll be shown an article + a classification someone already made (which campaign \
this article would BENEFIT in a head-to-head race). Your job: decide if the \
classification is correct.

Output JSON:
  {"agrees": true | false,
   "verifier_pick": "<CANDIDATE_A>" | "<CANDIDATE_B>" | "neutral",
   "reason": "<one short sentence>"}

agrees = true if the existing classification matches what you would pick.
verifier_pick = YOUR judgment of which campaign benefits.

Use these rules when evaluating:
  - Critical / scandal / accusation coverage of a candidate → favors their OPPONENT.
  - Positive / promotional / endorsement coverage of a candidate → favors THAT candidate.
  - If the candidate is in a TOPIC that originated as opposition research against them
    (e.g. stock trading scandal, ethics allegations, carpetbagger attacks), even neutral
    or defensive coverage on that topic favors the OPPONENT (the side that raised the
    issue benefits from continued visibility).
  - Partisan-figure coverage (Trump, Pelosi, Shapiro, Walz, etc.) without either named
    candidate → pick the candidate from the party whose framing wins.
  - GOP defectors backing Dem position → favors the Democratic candidate.
  - "neutral" should only apply to articles that have no political signal (off-topic
    construction, weather, unrelated business events, etc.).
"""


def main() -> int:
    db = SessionLocal()
    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name
    opp_name = opp.name
    print(f"Verifier auditing classifications for: {cand_name} vs {opp_name}\n")

    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(SourceItem.perspective.isnot(None))
        .order_by(SourceItem.id)
        .all()
    )
    print(f"Total classified articles to verify: {len(items)}")

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set; aborting.")
        return 1
    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")

    out_path = Path(__file__).parent / "perspective_verification.csv"

    # Map persisted perspective → display name for the prompt
    def persp_name(p: str | None) -> str:
        if p == "pro_candidate":
            return cand_name
        if p == "pro_opponent":
            return opp_name
        return "neutral"

    disagreements: list[dict] = []
    method_agree: Counter = Counter()  # by classifier method
    method_disagree: Counter = Counter()
    perspective_swaps: Counter = Counter()  # (original, verifier_pick) pairs

    # RESUMABILITY: skip ids already present in CSV. Open append mode if file exists.
    already_done: set[int] = set()
    if out_path.exists():
        with out_path.open("r") as rf:
            for row in csv.DictReader(rf):
                try:
                    already_done.add(int(row["id"]))
                except (ValueError, KeyError):
                    pass
    file_mode = "a" if already_done else "w"
    print(f"Resume mode: {len(already_done)} ids already verified, skipping those.")

    start = time.time()
    with out_path.open(file_mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not already_done:
            writer.writerow([
                "id", "title", "source_name",
                "classifier_method", "classifier_confidence", "classifier_perspective",
                "classifier_reason",
                "verifier_agrees", "verifier_pick_persp", "verifier_reason",
            ])

        for i, item in enumerate(items, 1):
            if item.id in already_done:
                continue
            title = (item.title or "")[:200]
            summary = (item.summary or "")[:600]
            excerpt = (item.raw_text or "")[:600]
            current_pick = persp_name(item.perspective)
            user_prompt = (
                f"CANDIDATE_A: {cand_name}\n"
                f"CANDIDATE_B: {opp_name}\n\n"
                f"Article title: {title}\n"
                f"Summary: {summary}\n"
                f"Excerpt: {excerpt}\n\n"
                f"EXISTING classification: favored_candidate = {current_pick!r}\n\n"
                f"Does this classification look right? Output JSON. "
                f"verifier_pick must be exactly {cand_name!r}, {opp_name!r}, or \"neutral\"."
            )
            try:
                raw = provider._chat(
                    user_prompt=user_prompt, system_prompt=VERIFIER_PROMPT,
                    json_mode=True, temperature=0, seed=42,
                )
                parsed = _parse_json_response(raw) or {}
                agrees = bool(parsed.get("agrees"))
                vpick_raw = (parsed.get("verifier_pick") or "").strip()
                # Map verifier name back to persistence value
                if vpick_raw == cand_name:
                    vpersp = "pro_candidate"
                elif vpick_raw == opp_name:
                    vpersp = "pro_opponent"
                elif vpick_raw.lower() == "neutral":
                    vpersp = "neutral"
                else:
                    # Fall back to surname match
                    if cand_name.split()[-1].lower() in vpick_raw.lower():
                        vpersp = "pro_candidate"
                    elif opp_name.split()[-1].lower() in vpick_raw.lower():
                        vpersp = "pro_opponent"
                    else:
                        vpersp = "neutral"
                reason = (parsed.get("reason") or "")[:240]

                if agrees:
                    method_agree[item.perspective_method or ""] += 1
                else:
                    method_disagree[item.perspective_method or ""] += 1
                    perspective_swaps[(item.perspective, vpersp)] += 1
                    disagreements.append({
                        "id": item.id, "title": title,
                        "classifier": item.perspective,
                        "classifier_reason": item.perspective_reason,
                        "verifier": vpersp,
                        "verifier_reason": reason,
                    })

                writer.writerow([
                    item.id, title, item.source_name or "",
                    item.perspective_method or "",
                    item.perspective_confidence or "",
                    item.perspective,
                    item.perspective_reason or "",
                    "yes" if agrees else "no",
                    vpersp,
                    reason,
                ])
            except Exception as e:
                writer.writerow([
                    item.id, title, item.source_name or "",
                    item.perspective_method or "",
                    item.perspective_confidence or "",
                    item.perspective,
                    item.perspective_reason or "",
                    "ERROR", "", str(e)[:200],
                ])

            f.flush()
            if i % 50 == 0:
                elapsed = time.time() - start
                rate = i / max(elapsed, 0.001)
                eta = (len(items) - i) / max(rate, 0.001)
                agree_count = sum(method_agree.values())
                disagree_count = sum(method_disagree.values())
                tot = agree_count + disagree_count
                pct = 100 * agree_count / max(tot, 1)
                print(
                    f"  [{i:5d}/{len(items)}] {rate:.1f}/s ETA {eta/60:.1f} min "
                    f"| agree={agree_count} ({pct:.1f}%) disagree={disagree_count}"
                )

    # Final stats
    print()
    print("=" * 100)
    total = sum(method_agree.values()) + sum(method_disagree.values())
    agree = sum(method_agree.values())
    print(f"AGREEMENT RATE: {agree}/{total} ({100*agree/max(total,1):.1f}%)")
    print()
    print("Agreement rate by classifier method:")
    for m in sorted(set(list(method_agree.keys()) + list(method_disagree.keys()))):
        a = method_agree[m]
        d = method_disagree[m]
        t = a + d
        print(f"  {m:14s} {a:5d}/{t:5d} agree ({100*a/max(t,1):.1f}%)")
    print()
    print("Top disagreement patterns (classifier → verifier):")
    for (orig, new), count in perspective_swaps.most_common(10):
        print(f"  {orig!r:18s} → {new!r:18s} {count}")
    print()
    print(f"Disagreement list saved to {out_path}")
    print(f"Open the CSV, sort by verifier_agrees=no to review flagged cases.")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
