"""
Test candidate prompts against the frozen ground-truth corpus.

Loads /tmp/rescore_corpus.json (built by rescore_ground_truth.py) and runs
each (prompt × model) combo against the 100 labeled pairs. Reports:
  - Agreement with gpt-4o ground truth
  - Precision / Recall / F1
  - False-keep rate (says KEEP when GT says REJECT) — these are mis-assignments that survive
  - False-reject rate (says REJECT when GT says KEEP) — these are valid extracts incorrectly purged
  - Cost per 100 pairs

Cost per evaluation:
  - gpt-4o-mini: 100 × $0.0001 ≈ $0.01
  - gpt-4o:      100 × $0.0020 ≈ $0.20

Run: cd backend && .venv/bin/python scripts/rescore_eval_prompt.py [prompt_name]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

CORPUS_PATH = "/tmp/rescore_corpus.json"


# ── Prompt registry — add new variants here ──────────────────────────────

PROMPTS: dict[str, str] = {}

# V1 — my original strict prompt (used in rescore_validation.py)
# Reference baseline. Over-strict on "Cognetti criticizing Bresnahan" cases.
PROMPTS["v1_strict"] = """You are auditing a political campaign's narrative-tracking system.
A previous LLM matched the EXTRACT below to the NARRATIVE FRAME below.
Decide whether the match is correct.

A correct match requires ALL of:
  (A) Subject match: the extract is about the same person/entity the frame
      describes. If the frame is "Cognetti's Mayoral Record" and the extract
      is about Bresnahan, that's WRONG even if both are political.
  (B) Topical match: the extract's substance is about the frame's topic
      area (healthcare, taxes, ethics, etc.).
  (C) Content quality: the extract is a substantive claim or quote — NOT
      a Reddit user comment, NOT a sentence fragment, NOT generic
      narrative throat-clearing.

NARRATIVE FRAME
  name: {frame_name}
  description: {frame_description}
  owner_type: {frame_owner}

EXTRACT
  source: {source}
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V2 — softened subject rule. Adds "subject as critic / target" exception.
PROMPTS["v2_softened"] = """You are auditing a political campaign's narrative-tracking system.
A previous LLM matched the EXTRACT below to the NARRATIVE FRAME below.
Decide whether the match is correct.

A correct match requires ALL of:
  (A) The frame's subject is RELEVANTLY involved in the extract — as the
      actor, the target of criticism, the central reference, or the topic.
      It is OK if the extract is about Person A criticizing Person B and
      the frame is about Person B's record.
  (B) Topical match: the extract relates to the frame's topic area
      (healthcare, taxes, ethics). It's OK if the stance is opposite —
      pro-tax and anti-tax content both topically match a tax frame.
  (C) Substantive: the extract is a real claim or context — not a
      sentence fragment, list item, or content with no specific tie to
      either side.

NARRATIVE FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V3 — full ground-truth-style prompt with examples (slightly shortened)
PROMPTS["v3_examples"] = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT is appropriately matched to the FRAME.

APPROPRIATE if BOTH:
  (A) The frame's subject is the substantive concern of the extract — as
      actor, target, critic, or central reference. The subject does NOT
      need to be named verbatim.
  (B) The extract is a substantive claim — not a fragment, not generic
      commentary mentioning neither side.

INAPPROPRIATE if ANY:
  - Extract is clearly about a different person and the frame's subject
    isn't relevantly involved.
  - Extract's topic doesn't relate to the frame's topic.
  - Extract is generic commentary naming neither the frame's subject
    nor a clearly related entity.
  - Extract is a fragment or throat-clearing.

EXAMPLES:
Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's Medicaid votes." → KEEP
Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a downtown traffic plan." → REJECT
Frame "Cognetti's Anti-Corruption" + "Bresnahan insists his advisor made the trade." → REJECT
Frame "Bresnahan's Healthcare Record" + "DCCC says Bresnahan is a corruption poster child." → REJECT (off-topic)
Frame "Bresnahan's Tax Cuts Lack Benefits" + "ATR notes Bresnahan signed the Taxpayer Protection Pledge." → KEEP (topical, opposing stance OK)

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V5 — refined v3 targeting the specific failure modes seen in round 1:
#  - "Critic-of-subject" cases (Cognetti criticizes Bresnahan's record →
#    matches Bresnahan's frame) were being incorrectly REJECTED.
#  - Generic political statements ("I'm honest", "NEPA is fired up") were
#    being incorrectly KEPT — they lack topic specificity.
#  - Frame names with narrow phrasing like "Criticized" or "Lack Benefits"
#    describe the frame's overall stance, but individual extracts don't
#    have to match that stance — they only have to be on-topic.
PROMPTS["v5_refined"] = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT is appropriately matched to the FRAME.

APPROPRIATE if BOTH:
  (A) The frame's subject is substantively involved — as actor, target,
      OR critic. If Frame is "Bresnahan's Healthcare Record" and the
      extract is Cognetti CRITICIZING Bresnahan's healthcare record,
      that is APPROPRIATE — Cognetti is the critic, Bresnahan's record
      is the topic.
  (B) The extract is specifically about the frame's TOPIC area — not
      generic political statements that could fit any frame.

The frame's NAME may have stance-loaded words like "Criticized",
"Lack Benefits", "Concerns" — these describe the frame's overall stance,
but individual extracts don't need to match the stance. An extract that
talks about the topic from a supportive angle still appropriately
matches a critical frame (and vice versa), as long as it's the same topic.

INAPPROPRIATE if ANY:
  - Extract is about a different person/topic and the frame's subject
    isn't relevantly involved.
  - Extract is generic political content with no specific tie to the
    frame's topic (e.g. "we will win in November" matches no specific
    topic frame).
  - Extract is a fragment, list item, or pure throat-clearing.

EXAMPLES (covering tricky cases):

Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's past Medicaid votes" → KEEP (Cognetti is critic, Bresnahan's healthcare record is topic)

Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a $27M downtown traffic plan" → REJECT (off-topic: traffic)

Frame "Cognetti's Anti-Corruption" + "I've shown in Scranton we can build government for people and be honest with people" → KEEP (generic-sounding but the 'honest government' framing is the anti-corruption message)

Frame "Cognetti Flips NEPA Seat" + "NEPA is fired up and ready to win in November" → KEEP (the flip narrative — campaign momentum)

Frame "Cognetti Flips NEPA Seat" + "Democrats hope to win back the House in 2026" → REJECT (national, not specifically about Cognetti's flip)

Frame "Bresnahan's Healthcare Record" + "DCCC says Bresnahan is poster child of corruption" → REJECT (about Bresnahan but topic is corruption, not healthcare)

Frame "Bresnahan's Tax Cuts Lack Benefits" + "ATR notes Bresnahan signed Taxpayer Protection Pledge" → KEEP (on-topic: Bresnahan + taxes; opposing stance is fine)

Frame "NEPA Support" + "Cognetti will be a voice in Congress that puts NEPA first" → KEEP (frames Cognetti as supportive of NEPA)

Frame "Healthcare Debate" + "Public healthcare premiums will skyrocket after Jan 2026 due to expiring subsidies" → REJECT (generic national news, no PA-08-specific debate)

Frame "Bresnahan Delivers District Funding" + "Bresnahan worked to ensure PA-08 hospitals received funding" → KEEP (Bresnahan delivering funding to district)

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V6 — refined v5 + tighter rules on the 6 remaining failure modes:
#   (1) Reject self-promotional generic statements ("I'm honest", "I'll
#       win") that don't reference the frame's TOPIC concretely.
#   (2) Reject overly short extracts (< 8 words) regardless of topic match.
#   (3) For frames with "Criticized" or "Lack" in the name, extracts that
#       FACTUALLY DESCRIBE the underlying thing without criticism are
#       NEUTRAL coverage, not criticism — REJECT them.
#   (4) "Healthcare Record" specifically means voting/policy record on
#       healthcare — stock trading in healthcare companies is a stretch.
PROMPTS["v6_calibrated"] = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT is appropriately matched to the FRAME.

A match is APPROPRIATE if ALL of:
  (A) The frame's subject is substantively involved — as actor, target,
      OR critic. (Frame "Bresnahan's Healthcare Record" + Cognetti
      CRITICIZING his record is APPROPRIATE.)
  (B) The extract is specifically about the frame's TOPIC area, not
      generic political content. If the topic is "Healthcare Record",
      the extract must engage the healthcare RECORD (votes, policies);
      tangential healthcare-adjacent content (stock trades in health
      companies, etc.) is NOT a match.
  (C) The extract is substantive: at least 8 meaningful words AND has a
      specific factual claim or position. Self-promotional generic
      campaign-speak ("I'm honest", "we'll win in November", "I've shown
      we can do better") that lacks topic specificity is NOT substantive.

For frames whose NAME contains words like "Criticized", "Lack Benefits",
"Concerns", "Inconsistency" — these signal the frame's THESIS, not a
filter on extracts. An on-topic extract that's neutral or even
oppositional in stance still matches the frame, AS LONG AS the topic is
specific. BUT: a neutral news report that merely DESCRIBES the topic
without engaging the frame's specific angle (e.g. just reporting "Trump
endorsed Bresnahan" for a frame about CRITICISM of Bresnahan's Trump
support) is NOT a match — it's adjacent news.

EXAMPLES:

Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's past Medicaid votes" → KEEP

Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a $27M downtown traffic plan" → REJECT (off-topic)

Frame "Bresnahan's Healthcare Record" + "Bresnahan recently sold stock in Centene, a Medicaid managed care org" → REJECT (about stock trading, not healthcare record)

Frame "Bresnahan's Support for Trump Criticized" + "Trump endorsed Bresnahan, calling him terrific" → REJECT (factual endorsement coverage, no engagement with criticism angle)

Frame "Bresnahan's Support for Trump Criticized" + "Bresnahan's votes show complete submission to Trump's agenda" → KEEP (engages the criticism angle)

Frame "Bresnahan's Tax Cuts Lack Benefits" + "he voted to cut benefits and is now saying that was actually an increase" → REJECT (this is about social benefit cuts, not the topic 'tax cuts' as a policy)

Frame "Bresnahan's Tax Cuts Lack Benefits" + "Rob Bresnahan voted for a bill that disproportionately benefits the wealthy" → KEEP (engages tax-cuts-and-their-distributional-effects)

Frame "Cognetti's Anti-Corruption" + "I'm running to clean it up." → REJECT (fragment, < 8 substantive words)

Frame "Cognetti's Anti-Corruption" + "I've shown in Scranton we can build government for people and be honest with people" → REJECT (generic campaign-speak, no specific corruption-fighting content)

Frame "Cognetti's Anti-Corruption" + "Cognetti will campaign as a corruption-fighting mayor who beat the party's nominee in 2019" → KEEP (specific anti-corruption framing)

Frame "Cognetti Flips NEPA Seat" + "NEPA is fired up and ready to win in November" → KEEP (campaign momentum is the flip narrative)

Frame "Cognetti Flips NEPA Seat" + "Democrats hope to win back the House in 2026" → REJECT (national, not Cognetti-specific)

Frame "NEPA Support" + "Cognetti will be a voice in Congress that puts NEPA first" → KEEP

Frame "Healthcare Debate" + "Public healthcare premiums will skyrocket due to expiring subsidies" → REJECT (national news, no PA-08 debate context)

Frame "Bresnahan Delivers District Funding" + "Bresnahan worked to ensure PA-08 hospitals received funding" → KEEP

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V7 — v5 + tighter explanation of stance-loaded frame names.
# Goal: keep v5's recall, lift mini's accuracy specifically on cases
# where the extract is the THING BEING CRITICIZED (e.g. Bresnahan saying
# he supports Trump, when the frame is "Support for Trump Criticized").
PROMPTS["v7_stance"] = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT is appropriately matched to the FRAME.

APPROPRIATE if BOTH:
  (A) The frame's subject is substantively involved — as actor, target,
      OR critic. (Frame "Bresnahan's Healthcare Record" + Cognetti
      CRITICIZING is APPROPRIATE — Cognetti is the critic, Bresnahan's
      record is the topic.)
  (B) The extract is specifically about the frame's TOPIC area — not
      generic political statements.

IMPORTANT — frame names with stance-loaded words ("Criticized", "Lack
Benefits", "Concerns", "Inconsistency") describe the frame's overall
THESIS. Three KINDS of extracts match such frames:
  1. The criticism itself ("Bresnahan's votes show submission to Trump")
  2. The SOURCE MATERIAL being criticized ("Bresnahan said he's proud
     to support Trump's agenda" → on-topic for "Trump Support
     Criticized" because it's the support being criticized)
  3. Factual context for the criticism (action descriptions, votes, etc.)
None of these require the extract itself to be critical — only the
overall narrative is.

INAPPROPRIATE if ANY:
  - Extract is about a different person/topic and the frame's subject
    isn't relevantly involved.
  - Extract is generic political content with no specific tie to the
    frame's topic ("we'll win in November" → no specific topic).
  - Extract is a sentence fragment (< 8 words) or pure throat-clearing.

EXAMPLES:

Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's past Medicaid votes" → KEEP

Frame "Bresnahan's Healthcare Record" + "Bresnahan joined Republicans in forcing a vote on healthcare subsidies" → KEEP (his healthcare action, even if neutral)

Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a $27M downtown traffic plan" → REJECT

Frame "Bresnahan's Support for Trump Criticized" + "I'm honored to have earned the support of President Trump" → KEEP (Bresnahan saying it; this IS the support being criticized — source material)

Frame "Bresnahan's Support for Trump Criticized" + "Bresnahan's votes show complete submission to Trump's agenda" → KEEP (the criticism itself)

Frame "Bresnahan's Support for Trump Criticized" + "Trump won Pennsylvania in 2024" → REJECT (not about Bresnahan)

Frame "Cognetti's Anti-Corruption" + "I'm running to clean it up." → REJECT (fragment)

Frame "Cognetti's Anti-Corruption" + "I've shown in Scranton we can build government for people and be honest" → KEEP (Cognetti positioning herself on honesty/anti-corruption, even if generic-sounding)

Frame "Cognetti Flips NEPA Seat" + "NEPA is fired up and ready to win in November" → KEEP (the flip narrative)

Frame "Cognetti Flips NEPA Seat" + "Democrats hope to win back the House in 2026" → REJECT (national, not Cognetti-specific)

Frame "Bresnahan's Tax Cuts Lack Benefits" + "ATR notes Bresnahan signed the Taxpayer Protection Pledge" → KEEP (on-topic, opposing stance OK)

Frame "NEPA Support" + "Cognetti will be a voice in Congress that puts NEPA first" → KEEP

Frame "Healthcare Debate" + "Public healthcare premiums will skyrocket due to expiring subsidies" → REJECT (national news, no PA-08 debate context)

Frame "Bresnahan Delivers District Funding" + "Bresnahan worked to ensure PA-08 hospitals received funding" → KEEP

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


# V4 — chain-of-thought before verdict. Test whether explicit reasoning
# improves consistency.
PROMPTS["v4_cot"] = """You are auditing a political narrative-tracking database.

Decide if the EXTRACT below is appropriately matched to the FRAME.

Think step by step. Output JSON only with three fields:
  - "subject_check": is the frame's subject (the named person or topic)
    substantively involved in the extract (as actor/target/critic/central
    reference)? Answer "yes" or "no" with one sentence why.
  - "topic_check": does the extract's substance fit the frame's topic
    area? Answer "yes" or "no" with one sentence why. (Note: opposing
    stance still counts as topical match.)
  - "substance_check": is the extract a real claim/statement (not a
    fragment or generic noise)? Answer "yes" or "no".
  - "verdict": "KEEP" if all three checks are "yes", else "REJECT".

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Output JSON:"""


# ── Model adapters ──────────────────────────────────────────────────────

def make_openai(model: str) -> Callable[[str], dict]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def call(prompt: str) -> dict:
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=180,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return {
                "verdict": (data.get("verdict") or "").upper(),
                "reason": data.get("reason") or "",
                "latency_ms": int((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"verdict": "ERROR", "reason": str(e)[:120], "latency_ms": int((time.time() - t0) * 1000)}
    return call


MODELS = [
    ("gpt-4o-mini", make_openai("gpt-4o-mini")),
    ("gpt-4o",      make_openai("gpt-4o")),
]


# ── Evaluation ─────────────────────────────────────────────────────────

def load_corpus() -> list[dict]:
    with open(CORPUS_PATH) as f:
        return json.load(f)


def evaluate(prompt_name: str, prompt_template: str, model_name: str, model_call, corpus: list[dict]) -> dict:
    """Run a (prompt, model) combo against the corpus; return metrics."""
    results = []
    for pair in corpus:
        prompt = prompt_template.format(
            frame_name=pair["frame_name"],
            frame_description=pair["frame_description"] or "(no description)",
            frame_owner=pair["frame_owner"],
            extract=pair["extract"].replace('"', "'")[:600],
            source=pair["source"] or "unknown",
        )
        out = model_call(prompt)
        results.append({
            "pair": pair,
            "predicted": out["verdict"],
            "reason": out["reason"],
            "latency_ms": out["latency_ms"],
        })

    # ── Metrics ──
    # Treat "should KEEP" as positive class.
    tp = sum(1 for r in results if r["pair"]["ground_truth"] == "KEEP" and r["predicted"] == "KEEP")
    fp = sum(1 for r in results if r["pair"]["ground_truth"] == "REJECT" and r["predicted"] == "KEEP")
    tn = sum(1 for r in results if r["pair"]["ground_truth"] == "REJECT" and r["predicted"] == "REJECT")
    fn = sum(1 for r in results if r["pair"]["ground_truth"] == "KEEP" and r["predicted"] == "REJECT")
    errors = sum(1 for r in results if r["predicted"] == "ERROR")
    n = len(results) - errors

    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)
    accuracy = (tp + tn) / max(1, n)

    # False rates (the things that actually cause user-visible problems):
    #  - False-keep: we say KEEP but GT says REJECT → bad assignment survives
    #  - False-reject: we say REJECT but GT says KEEP → valid extract purged
    false_keep_rate = fp / max(1, tp + fp + tn + fn)
    false_reject_rate = fn / max(1, tp + fp + tn + fn)

    return {
        "prompt": prompt_name, "model": model_name,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn, "errors": errors,
        "accuracy": accuracy,
        "precision": precision, "recall": recall, "f1": f1,
        "false_keep_rate": false_keep_rate,
        "false_reject_rate": false_reject_rate,
        "median_latency_ms": sorted(r["latency_ms"] for r in results)[len(results)//2],
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default=None,
                        help="Run only this prompt name (default: all). Use 'list' to see names.")
    parser.add_argument("--models", default="both",
                        help="'mini', '4o', or 'both' (default).")
    parser.add_argument("--show-disagreements", action="store_true",
                        help="Print every case where prompt disagreed with ground truth.")
    args = parser.parse_args()

    if args.prompt == "list":
        print("Available prompts:")
        for k in PROMPTS: print(f"  - {k}")
        return

    if args.prompt and args.prompt not in PROMPTS:
        print(f"Unknown prompt: {args.prompt}")
        print("Available:", list(PROMPTS.keys()))
        return

    corpus = load_corpus()
    print(f"Loaded {len(corpus)} pairs from {CORPUS_PATH}")
    gt_keep = sum(1 for p in corpus if p["ground_truth"] == "KEEP")
    print(f"Ground truth: {gt_keep} KEEP, {len(corpus) - gt_keep} REJECT\n")

    prompt_names = [args.prompt] if args.prompt else list(PROMPTS.keys())
    models = MODELS
    if args.models == "mini":
        models = [m for m in MODELS if "mini" in m[0]]
    elif args.models == "4o":
        models = [m for m in MODELS if m[0] == "gpt-4o"]

    all_results = []
    for pname in prompt_names:
        for mname, mcall in models:
            print(f"== Evaluating {pname} × {mname} ==", flush=True)
            res = evaluate(pname, PROMPTS[pname], mname, mcall, corpus)
            all_results.append(res)
            print(f"  accuracy:          {res['accuracy']:.3f}")
            print(f"  precision (KEEP):  {res['precision']:.3f}")
            print(f"  recall (KEEP):     {res['recall']:.3f}")
            print(f"  F1:                {res['f1']:.3f}")
            print(f"  false-keep rate:   {res['false_keep_rate']:.3f}  (bad data survives)")
            print(f"  false-reject rate: {res['false_reject_rate']:.3f}  (good data purged)")
            print(f"  confusion:         TP={res['tp']} FP={res['fp']} TN={res['tn']} FN={res['fn']} ERR={res['errors']}")
            print(f"  median latency:    {res['median_latency_ms']}ms\n")

    # Final comparison table
    print("\n" + "=" * 100)
    print("SUMMARY (sorted by F1)")
    print("=" * 100)
    all_results.sort(key=lambda x: -x['f1'])
    print(f"{'Prompt':<18} {'Model':<14} {'Acc':>6} {'Prec':>6} {'Rec':>6} {'F1':>6} {'FK%':>6} {'FR%':>6}")
    print("-" * 70)
    for r in all_results:
        print(f"{r['prompt']:<18} {r['model']:<14} "
              f"{r['accuracy']:>6.3f} {r['precision']:>6.3f} {r['recall']:>6.3f} {r['f1']:>6.3f} "
              f"{100*r['false_keep_rate']:>5.1f}% {100*r['false_reject_rate']:>5.1f}%")

    # If asked, dump every disagreement for the best run
    if args.show_disagreements:
        best = all_results[0]
        print("\n" + "=" * 100)
        print(f"DISAGREEMENTS for best combo: {best['prompt']} × {best['model']}")
        print("=" * 100)
        for r in best["results"]:
            gt = r["pair"]["ground_truth"]
            pred = r["predicted"]
            if gt == pred:
                continue
            print(f"\n  GT={gt} PRED={pred}  | {r['pair']['frame_name']}")
            print(f"  extract: {r['pair']['extract'][:180]}")
            print(f"  reason:  {r['reason']}")
            print(f"  GT reason: {r['pair']['gt_reason']}")


if __name__ == "__main__":
    main()
