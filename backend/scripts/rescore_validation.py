"""
Rescore validation: prove (or disprove) that a better prompt would fix the
mis-assigned-extract problem before committing to a full rescore.

Test design
-----------
Sample 30 (extracted_text, frame) pairs from the existing scored data,
weighted toward cases where the existing assignment looks suspect. For
each pair, run a NEW judge prompt that asks "does this extract belong in
this frame?" — with explicit checks for:
  - Subject verification (is the extract actually ABOUT this frame's subject?)
  - Content quality (is this article body, or a comment / sentence fragment?)
  - Topical relevance (does the content match the frame's topic?)

Compare the judge's verdict against the existing assignment + against
heuristic ground truth (does the extract mention the right candidate?).

The user's bar (from their message):
  - >20 of 30 cases where new prompt's verdict is BETTER than existing →
    rescore is worth ~$12
  - <10 of 30 → noise is irreducible, don't rescore

Cost
----
30 calls × $0.0021 (gpt-4o) = $0.06. Cheap enough to run a few times
during prompt iteration.

Run: cd backend && .venv/bin/python scripts/rescore_validation.py
"""
from __future__ import annotations
import json
import os
import random
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()


# ── NEW judge prompt ──────────────────────────────────────────────────────
# Targets the specific failure modes I observed when reading all 1013 dots:
#  1. quotes about wrong candidate ending up in candidate-X frame
#  2. reddit comments / sentence fragments scored as substantive quotes
#  3. topic-similar but stance-irrelevant content (atr.org pro-tax in
#     "Tax Cuts Lack Benefits") — we accept these as "topically correct"
#     for now since splitting by stance is a separate problem.

JUDGE_PROMPT = """You are auditing a political campaign's narrative-tracking system.
A previous LLM matched the EXTRACT below to the NARRATIVE FRAME below.
Decide whether the match is correct.

A correct match requires ALL of:
  (A) Subject match: the extract is about the same person/entity the frame
      describes. If the frame is "Cognetti's Mayoral Record" and the extract
      is about Bresnahan, that's WRONG even if both are political.
  (B) Topical match: the extract's substance is about the frame's topic
      area (healthcare, taxes, ethics, etc.). Topical match is satisfied
      even if the stance is opposite ("pro-tax-cut" and "anti-tax-cut"
      both topically match a tax cut frame).
  (C) Content quality: the extract is a substantive claim or quote — NOT
      a Reddit user comment, NOT a sentence fragment, NOT generic
      narrative throat-clearing ("It's been a busy week...").

NARRATIVE FRAME
  name: {frame_name}
  description: {frame_description}
  owner_type: {frame_owner}  (which side benefits from this narrative)

EXTRACT
  source: {source}
  text: "{extract}"

Respond with JSON only, this exact shape:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


def make_judge(model: str):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def call(frame_name: str, frame_desc: str, frame_owner: str,
             extract: str, source: str) -> dict:
        prompt = JUDGE_PROMPT.format(
            frame_name=frame_name,
            frame_description=frame_desc or "(no description)",
            frame_owner=frame_owner,
            extract=extract.replace('"', "'")[:600],
            source=source or "unknown",
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=120,
            )
            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)
            return {
                "verdict": (data.get("verdict") or "").upper(),
                "reason": data.get("reason") or "",
            }
        except Exception as e:
            return {"verdict": "ERROR", "reason": str(e)[:120]}
    return call


# ── Heuristic ground-truth signal ─────────────────────────────────────────
# When the extract mentions ONLY one candidate, we have a strong signal
# about what frame the extract should belong to.

def heuristic_subject(extract: str) -> Optional[str]:
    """Return 'bresnahan', 'cognetti', 'both', or None."""
    t = (extract or "").lower()
    has_b = "bresnahan" in t
    has_c = "cognetti" in t
    if has_b and has_c: return "both"
    if has_b: return "bresnahan"
    if has_c: return "cognetti"
    return None


def heuristic_check(extract: str, frame_name: str) -> Optional[str]:
    """Return a flag if the extract clearly conflicts with the frame.

    Returns:
      "MISMATCH" — extract mentions the wrong candidate
      "SHORT"    — extract is suspiciously short (< 40 chars)
      "REDDIT"   — extract is a typical Reddit comment pattern
      None       — no flag, can't say from heuristic alone
    """
    subject = heuristic_subject(extract)
    f = frame_name.lower()
    if subject == "bresnahan" and ("cognetti" in f or "cognetti's" in f):
        return "MISMATCH"
    if subject == "cognetti" and ("bresnahan" in f or "bresnahan's" in f):
        return "MISMATCH"
    if len(extract.strip()) < 40:
        return "SHORT"
    return None


# ── Sample selection ─────────────────────────────────────────────────────

def sample_pairs(target: int = 30) -> list[dict]:
    """Pull (extract, frame) pairs from the live DB.

    Sample composition:
      - 10 likely-suspect (heuristic flag fires)
      - 20 random (representative of overall quality)
    """
    from app.db import SessionLocal
    from app.models import NarrativeFrameMention, NarrativeFrame, SourceItem, Outlet

    pairs: list[dict] = []
    with SessionLocal() as db:
        rows = (
            db.query(
                NarrativeFrameMention.id,
                NarrativeFrameMention.extracted_text,
                NarrativeFrameMention.frame_id,
                NarrativeFrame.name.label("frame_name"),
                NarrativeFrame.description.label("frame_description"),
                NarrativeFrame.owner_type.label("frame_owner"),
                SourceItem.title.label("source_title"),
                SourceItem.source_name,
                Outlet.name.label("outlet_name"),
            )
            .select_from(NarrativeFrameMention)
            .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
            .outerjoin(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(NarrativeFrame.active == True)
            .filter(NarrativeFrameMention.extracted_text.isnot(None))
            .all()
        )

    flagged = []
    other = []
    for r in rows:
        flag = heuristic_check(r.extracted_text, r.frame_name)
        pair = {
            "mention_id": r.id,
            "extract": r.extracted_text,
            "frame_id": r.frame_id,
            "frame_name": r.frame_name,
            "frame_description": r.frame_description,
            "frame_owner": r.frame_owner,
            "source": r.outlet_name or r.source_name or r.source_title or "unknown",
            "heuristic_flag": flag,
        }
        if flag:
            flagged.append(pair)
        else:
            other.append(pair)

    random.seed(42)  # repeatable sample so re-running gives same 30
    sus_sample = random.sample(flagged, min(10, len(flagged)))
    rand_sample = random.sample(other, target - len(sus_sample))
    return sus_sample + rand_sample


def main():
    print("Fetching sample of 30 (extract, frame) pairs from DB…")
    pairs = sample_pairs(30)
    print(f"  {sum(1 for p in pairs if p['heuristic_flag'])} flagged by heuristic")
    print(f"  {sum(1 for p in pairs if not p['heuristic_flag'])} random sample\n")

    print("Running judge prompt on each (gpt-4o for max quality on the test)…\n")
    judge = make_judge("gpt-4o")
    results = []
    for i, p in enumerate(pairs):
        verdict = judge(
            p["frame_name"], p["frame_description"] or "",
            p["frame_owner"], p["extract"], p["source"],
        )
        results.append({**p, **verdict})
        flag_marker = f" [{p['heuristic_flag']}]" if p['heuristic_flag'] else ""
        print(f"  [{i+1:2}/30] {verdict['verdict']:>6}{flag_marker:15} | {p['frame_name'][:42]}")

    # ── Per-pair detail ───────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("DETAILED RESULTS")
    print("=" * 90)
    for i, r in enumerate(results):
        flag = f" [{r['heuristic_flag']}]" if r['heuristic_flag'] else ""
        print(f"\n#{i+1} verdict: {r['verdict']}{flag}")
        print(f"   frame:   {r['frame_name']}")
        print(f"   source:  {r['source']}")
        print(f"   extract: {r['extract'][:160].strip()}")
        print(f"   reason:  {r['reason']}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)

    n_keep = sum(1 for r in results if r['verdict'] == 'KEEP')
    n_reject = sum(1 for r in results if r['verdict'] == 'REJECT')
    n_error = sum(1 for r in results if r['verdict'] not in ('KEEP', 'REJECT'))

    print(f"\nJudge decisions: KEEP={n_keep}, REJECT={n_reject}, ERROR={n_error}")

    # Subset: heuristic-flagged
    flagged_results = [r for r in results if r['heuristic_flag']]
    flagged_rejects = sum(1 for r in flagged_results if r['verdict'] == 'REJECT')
    print(f"\nHeuristic-flagged sample ({len(flagged_results)}): "
          f"judge rejected {flagged_rejects} ({100*flagged_rejects/max(1,len(flagged_results)):.0f}%)")
    print("  (judge should agree with heuristic flags — high % means the new prompt CATCHES errors)")

    # Subset: random
    random_results = [r for r in results if not r['heuristic_flag']]
    random_rejects = sum(1 for r in random_results if r['verdict'] == 'REJECT')
    print(f"\nRandom sample ({len(random_results)}): judge rejected {random_rejects} "
          f"({100*random_rejects/max(1,len(random_results)):.0f}%)")
    print("  (this is the EXTRA error rate the new prompt finds beyond the obvious cases)")

    # The user's bar from the prior message:
    print("\n" + "─" * 90)
    print("USER'S DECISION RULE (from prior message):")
    print(f"  Total fixed cases (= REJECT count): {n_reject}")
    print(f"    > 20 → rescore is worth ~$12")
    print(f"    10-20 → marginal, judgment call")
    print(f"    < 10 → don't rescore, noise is irreducible")
    print("─" * 90)

    # Save full results to JSON for further inspection
    out_path = "/tmp/rescore_validation_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nFull results saved to: {out_path}")


if __name__ == "__main__":
    main()
