"""Third-pass tie-breaker LLM run on classifier-vs-verifier disagreements only.

The verifier audit produces a CSV with ~700+ disagreements between classifier
and verifier. Many of those are NOT real classifier errors — they're caused by:
  1. The verifier's stricter "attack-vector" rule (it flips defensive coverage
     of scandals to pro_opponent even when the article reads as defensive).
  2. Label-direction confusion in either prompt (reasoning says A, label says B).
  3. Out-of-race articles where neither candidate is named (should be neutral).
  4. Genuine ambiguity / judgment calls.

This script runs a *judge* pass over disagreements ONLY. The judge sees BOTH
prompts' reasoning and is asked to pick a final answer + classify the error
type. This converts the audit from "noisy disagreement signal" into a clean
"actual classifier accuracy + categorized error list" report.

INPUTS:  scripts/perspective_verification.csv (from perspective_verify.py)
OUTPUTS: scripts/perspective_judged.csv  (one row per disagreement with judge call)
         summary stats printed to stdout

Cost: only runs on disagreements (~700 calls) × ~$0.0002 = ~$0.15.
Runtime: ~5-10 min.
"""
import csv
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.llm_provider import OpenAIProvider, _parse_json_response


JUDGE_PROMPT = """You are the final arbiter on political-news classification disputes.

Two LLM classifiers were given an article and asked: which campaign does this
article BENEFIT in a head-to-head race between CANDIDATE_A and CANDIDATE_B?
They disagreed. You're shown:
  - the article (title + summary + excerpt)
  - classifier A's pick + reasoning
  - classifier B's pick + reasoning

Pick the correct answer AND label the error type.

Output JSON:
  {"final": "<CANDIDATE_A>" | "<CANDIDATE_B>" | "neutral",
   "agrees_with": "A" | "B" | "neither",
   "error_type": "<one of: a_wrong | b_wrong | both_wrong | a_label_inversion | b_label_inversion | both_reasonable_neutral_better | out_of_race | scandal_visibility_disagreement | other>",
   "reason": "<one or two sentences>"}

ERROR-TYPE GUIDE:
  - a_wrong / b_wrong: the named classifier picked the wrong side outright.
  - a_label_inversion / b_label_inversion: classifier's REASONING is correct
    but the LABEL they output is the OPPOSITE of what the reasoning implies
    (e.g. they said "this attack helps Cognetti" but labeled it pro_opponent).
  - both_wrong: neither is right. Specify the right answer in "final".
  - both_reasonable_neutral_better: both could be defended but the article is
    genuinely off-topic / mixed enough that "neutral" is the best call.
  - out_of_race: the article is about a different race / candidates not in this
    one. Pick "neutral" as final.
  - scandal_visibility_disagreement: A and B differ purely because one applies
    the "any visibility of a candidate's scandal favors their opponent" rule
    aggressively and the other doesn't. Pick the side that reads the article's
    actual framing rather than its topic alone.
  - other: anything else; explain in "reason".

JUDGING RULES:
  1. An article that attacks a candidate (criticism, scandal, accusation) favors
     their OPPONENT — regardless of who wrote it.
  2. An article that promotes / endorses / defends a candidate favors THAT candidate.
  3. If the article is genuinely about a DIFFERENT race or different politicians
     (e.g. Iowa candidates when this race is in PA-08), the answer is "neutral"
     unless the article makes a clear partisan-frame argument that maps cleanly.
  4. If a Democratic candidate is one of the named candidates, articles favorable
     to Democrats / critical of Republicans generally favor that Democrat.
  5. Defensive coverage of a scandal that lets the candidate explain themselves
     should be evaluated on its framing: hostile framing → opponent;
     sympathetic framing → candidate; truly mixed → neutral.
"""


def main() -> int:
    db = SessionLocal()
    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name
    opp_name = opp.name

    in_path = Path(__file__).parent / "perspective_verification.csv"
    out_path = Path(__file__).parent / "perspective_judged.csv"
    if not in_path.exists():
        print(f"Input not found: {in_path}")
        return 1

    with in_path.open() as f:
        rows = list(csv.DictReader(f))
    disagreements = [
        r for r in rows
        if r["verifier_agrees"].strip().lower() == "no"
    ]
    print(f"Verifier rows: {len(rows)}")
    print(f"Disagreements to judge: {len(disagreements)}")

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set; aborting.")
        return 1
    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")

    def persp_name(p: str | None) -> str:
        if p == "pro_candidate":
            return cand_name
        if p == "pro_opponent":
            return opp_name
        return "neutral"

    def to_persp(name: str) -> str:
        if name == cand_name:
            return "pro_candidate"
        if name == opp_name:
            return "pro_opponent"
        if name.lower() == "neutral":
            return "neutral"
        # surname fallback
        if cand_name.split()[-1].lower() in name.lower():
            return "pro_candidate"
        if opp_name.split()[-1].lower() in name.lower():
            return "pro_opponent"
        return "neutral"

    judge_picks: Counter = Counter()  # which classifier was right
    error_types: Counter = Counter()
    final_dist: Counter = Counter()

    # We need the article's full content (raw_text/summary). Load by id.
    ids = [int(r["id"]) for r in disagreements]
    item_by_id = {
        it.id: it
        for it in db.query(SourceItem).filter(SourceItem.id.in_(ids)).all()
    }

    start = time.time()
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "title", "source_name",
            "classifier_method", "classifier_pick", "classifier_reason",
            "verifier_pick", "verifier_reason",
            "judge_final", "judge_agrees_with", "judge_error_type",
            "judge_reason",
        ])

        for i, r in enumerate(disagreements, 1):
            it = item_by_id.get(int(r["id"]))
            if not it:
                continue
            title = (it.title or "")[:200]
            summary = (it.summary or "")[:600]
            excerpt = (it.raw_text or "")[:600]
            cla_pick = persp_name(r["classifier_perspective"])
            ver_pick = persp_name(r["verifier_pick_persp"])
            user_prompt = (
                f"CANDIDATE_A: {cand_name}\n"
                f"CANDIDATE_B: {opp_name}\n\n"
                f"Article title: {title}\n"
                f"Summary: {summary}\n"
                f"Excerpt: {excerpt}\n\n"
                f"Classifier A picked: {cla_pick}\n"
                f"  reasoning: {r['classifier_reason'][:300]}\n\n"
                f"Classifier B picked: {ver_pick}\n"
                f"  reasoning: {r['verifier_reason'][:300]}\n\n"
                "Output JSON only."
            )
            try:
                raw = provider._chat(
                    user_prompt=user_prompt, system_prompt=JUDGE_PROMPT,
                    json_mode=True, temperature=0, seed=42,
                )
                parsed = _parse_json_response(raw) or {}
                final = (parsed.get("final") or "").strip()
                agrees_with = (parsed.get("agrees_with") or "").strip()
                etype = (parsed.get("error_type") or "").strip()
                reason = (parsed.get("reason") or "")[:240]
                final_persp = to_persp(final)
                judge_picks[agrees_with] += 1
                error_types[etype] += 1
                final_dist[final_persp] += 1
                w.writerow([
                    it.id, title, it.source_name or "",
                    r["classifier_method"], r["classifier_perspective"],
                    r["classifier_reason"][:200],
                    r["verifier_pick_persp"], r["verifier_reason"][:200],
                    final_persp, agrees_with, etype, reason,
                ])
            except Exception as e:
                w.writerow([
                    it.id, title, it.source_name or "",
                    r["classifier_method"], r["classifier_perspective"],
                    r["classifier_reason"][:200],
                    r["verifier_pick_persp"], r["verifier_reason"][:200],
                    "ERROR", "", "", str(e)[:200],
                ])

            if i % 50 == 0:
                elapsed = time.time() - start
                rate = i / max(elapsed, 0.001)
                eta = (len(disagreements) - i) / max(rate, 0.001)
                print(
                    f"  [{i:5d}/{len(disagreements)}] {rate:.1f}/s "
                    f"ETA {eta/60:.1f} min | judge picks: {dict(judge_picks)}"
                )

    elapsed = time.time() - start
    print()
    print("=" * 100)
    print(f"Judged {sum(judge_picks.values())} disagreements in {elapsed/60:.1f} min")
    print()
    print("Judge agrees with:")
    for k, n in judge_picks.most_common():
        pct = 100 * n / max(sum(judge_picks.values()), 1)
        print(f"  {k:10s} {n:5d}  ({pct:.1f}%)")
    print()
    print("Error-type distribution:")
    for k, n in error_types.most_common():
        print(f"  {k:40s} {n:5d}")
    print()
    print("Judge's final-label distribution on disagreements:")
    for k, n in final_dist.most_common():
        print(f"  {k:18s} {n:5d}")
    print()
    print(f"Detail saved to {out_path}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
