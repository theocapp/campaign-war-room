"""
Build a 100-pair ground-truth corpus for prompt iteration.

One-time expensive pass (~$0.20) that uses gpt-4o + a careful audit prompt
to label whether each (extract, frame) pair in the existing DB is an
appropriate match. The resulting labels are saved to /tmp/rescore_corpus.json
so subsequent prompt-eval runs can be cheap (no gpt-4o calls).

Sample composition (target 100 pairs):
  - 30 heuristic-flagged (likely wrong — wrong candidate / sentence fragment)
  - 40 random (representative of overall data quality)
  - 30 high-confidence-correct (extract names frame's candidate AND topic
    words from frame name appear in extract)

This balanced design lets us measure:
  - PRECISION: of cases the prompt KEEPS, what fraction are actually right?
  - RECALL: of cases that SHOULD be kept, what fraction does prompt keep?
  - SPECIFICITY: of cases that SHOULD be rejected, what fraction does
    prompt reject?

Run: cd backend && .venv/bin/python scripts/rescore_ground_truth.py
"""
from __future__ import annotations
import json
import os
import random
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


GROUND_TRUTH_OUT = "/tmp/rescore_corpus.json"
TARGET_TOTAL = 100


# ── Audit prompt for gpt-4o ground truth ─────────────────────────────────
# Carefully designed to be LENIENT on "subject as critic / target" cases
# (where the extract is about Candidate A criticizing Candidate B and the
# frame is about Candidate B). Reject only when truly off-topic or
# substanceless.

GROUND_TRUTH_PROMPT = """You are auditing a political narrative-tracking database.

Decide whether the EXTRACT below is appropriately matched to the FRAME below.

A match is APPROPRIATE if BOTH:
  (A) The frame's subject (its named candidate, or the named topic) is the
      substantive concern of the extract — as the actor, the target, the
      critic, or the central reference. The subject does NOT need to be
      named verbatim in the extract; context counts.
  (B) The extract is a substantive claim, statement, or contextual fact —
      not a sentence fragment, generic political throat-clearing, or a
      quote that mentions neither side.

A match is INAPPROPRIATE if ANY:
  - The extract is clearly about a DIFFERENT person and the frame's
    subject isn't relevantly involved.
  - The extract's topic doesn't relate to the frame's topic (e.g. extract
    is about traffic and frame is about healthcare).
  - The extract is generic political commentary that names neither the
    frame's subject nor a clear related entity.
  - The extract is a sentence fragment, list item, or just throat-clearing.

EXAMPLES:

Frame: "Bresnahan's Healthcare Record"
Extract: "Cognetti criticized Bresnahan's past votes on Medicaid expansion."
→ KEEP (Cognetti is critic; Bresnahan's healthcare record is the topic)

Frame: "Bresnahan's Healthcare Record"
Extract: "Mayor Cognetti unveiled a $27M downtown traffic plan."
→ REJECT (not about Bresnahan or healthcare)

Frame: "Cognetti's Anti-Corruption"
Extract: "Bresnahan insists his financial advisor made the stock trade."
→ REJECT (extract is about Bresnahan, not Cognetti's anti-corruption message)

Frame: "Bresnahan's Healthcare Record"
Extract: "DCCC says Bresnahan is the poster child of Washington corruption."
→ REJECT (about Bresnahan but topic is corruption, not healthcare)

Frame: "Bresnahan's Tax Cuts Lack Benefits"
Extract: "ATR notes Bresnahan signed the Taxpayer Protection Pledge."
→ KEEP (about Bresnahan and taxes, just from the pro-tax side; topical
   stance opposition is still a topical match)

Frame: "NEPA Support"
Extract: "Mayor Cognetti has been a NEPA resident for over 10 years."
→ KEEP (Cognetti's NEPA ties = NEPA support context; brief but substantive)

Frame: "Bresnahan Delivers District Funding"
Extract: "the second is he will kneel to Trump"
→ REJECT (sentence fragment, no funding content)

FRAME
  name:        {frame_name}
  description: {frame_description}
  owner_type:  {frame_owner}

EXTRACT
  source: {source}
  text:   "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "confidence": "high" | "medium" | "low", "reason": "<one short sentence>"}}"""


def make_gpt4o_caller():
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def call(pair: dict) -> dict:
        prompt = GROUND_TRUTH_PROMPT.format(
            frame_name=pair["frame_name"],
            frame_description=pair["frame_description"] or "(no description)",
            frame_owner=pair["frame_owner"],
            extract=pair["extract"].replace('"', "'")[:600],
            source=pair["source"] or "unknown",
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.05,  # very low for ground-truth stability
                max_tokens=120,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return {
                "verdict": (data.get("verdict") or "").upper(),
                "confidence": (data.get("confidence") or "").lower(),
                "reason": data.get("reason") or "",
            }
        except Exception as e:
            return {"verdict": "ERROR", "confidence": "", "reason": str(e)[:120]}
    return call


# ── Heuristic flags for sample stratification ────────────────────────────

def _subject(text: str) -> Optional[str]:
    t = (text or "").lower()
    has_b = "bresnahan" in t
    has_c = "cognetti" in t
    if has_b and has_c: return "both"
    if has_b: return "bresnahan"
    if has_c: return "cognetti"
    return None


def heuristic_label(extract: str, frame_name: str) -> str:
    """Bucket each pair by heuristic guess of correctness.

    Returns one of:
      FLAG_MISMATCH — extract mentions wrong candidate (likely INVALID match)
      FLAG_FRAGMENT — extract is suspiciously short (likely INVALID)
      LIKELY_CORRECT — extract names frame's candidate AND topic words appear
                       (likely VALID match)
      NEUTRAL — no strong signal either way
    """
    subj = _subject(extract)
    fn_lower = frame_name.lower()

    # Determine the frame's expected subject from its name
    if "bresnahan" in fn_lower or "bresnahan's" in fn_lower:
        expected = "bresnahan"
    elif "cognetti" in fn_lower or "cognetti's" in fn_lower:
        expected = "cognetti"
    else:
        expected = None

    if subj and expected and subj != expected and subj != "both":
        return "FLAG_MISMATCH"
    if len((extract or "").strip()) < 40:
        return "FLAG_FRAGMENT"
    # Likely-correct: subject matches frame AND frame's topic noun appears in extract
    if subj == expected:
        topic_nouns = [w for w in fn_lower.split() if w not in
            {"the", "a", "of", "in", "on", "to", "for", "with", "by",
             "bresnahan", "bresnahan's", "cognetti", "cognetti's", "and"}]
        if any(noun in extract.lower() for noun in topic_nouns):
            return "LIKELY_CORRECT"
    return "NEUTRAL"


# ── Sample selection ──────────────────────────────────────────────────────

def build_corpus():
    """Pull a stratified 100-pair sample from the DB."""
    from app.db import SessionLocal
    from app.models import NarrativeFrameMention, NarrativeFrame, SourceItem, Outlet

    print("Loading all NarrativeFrameMention rows…")
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
            .filter(NarrativeFrameMention.extracted_text != "")
            .all()
        )

    pairs_by_bucket: dict[str, list[dict]] = {
        "FLAG_MISMATCH": [],
        "FLAG_FRAGMENT": [],
        "LIKELY_CORRECT": [],
        "NEUTRAL": [],
    }
    for r in rows:
        bucket = heuristic_label(r.extracted_text, r.frame_name)
        pairs_by_bucket[bucket].append({
            "mention_id": r.id,
            "extract": r.extracted_text,
            "frame_id": r.frame_id,
            "frame_name": r.frame_name,
            "frame_description": r.frame_description,
            "frame_owner": r.frame_owner,
            "source": r.outlet_name or r.source_name or r.source_title or "unknown",
            "heuristic_bucket": bucket,
        })

    print("Bucket sizes available:")
    for k, v in pairs_by_bucket.items():
        print(f"  {k:18}: {len(v)} pairs")

    random.seed(42)
    target = {
        "FLAG_MISMATCH": 25,
        "FLAG_FRAGMENT": 5,
        "LIKELY_CORRECT": 30,
        "NEUTRAL": 40,
    }
    sample = []
    for bucket, n_want in target.items():
        available = pairs_by_bucket[bucket]
        n_take = min(n_want, len(available))
        sample.extend(random.sample(available, n_take))
        print(f"  → sampling {n_take} from {bucket}")
    print(f"Total sampled: {len(sample)}")
    return sample


def main():
    corpus = build_corpus()
    print(f"\nGround-truth labeling with gpt-4o (≈ {len(corpus) * 0.002:.2f} USD)…\n")

    judge = make_gpt4o_caller()
    for i, pair in enumerate(corpus):
        result = judge(pair)
        pair["ground_truth"] = result["verdict"]
        pair["gt_confidence"] = result["confidence"]
        pair["gt_reason"] = result["reason"]
        print(f"  [{i+1:3}/{len(corpus)}] {result['verdict']:>6} ({result['confidence']:>6}) | "
              f"{pair['heuristic_bucket']:14} | {pair['frame_name'][:42]}")

    # Summary by bucket × ground truth
    from collections import Counter
    print("\nBy bucket × ground truth:")
    print(f"  {'Bucket':<18} {'KEEP':>6} {'REJECT':>6} {'ERROR':>6}")
    for bucket in ["FLAG_MISMATCH", "FLAG_FRAGMENT", "LIKELY_CORRECT", "NEUTRAL"]:
        rows = [p for p in corpus if p["heuristic_bucket"] == bucket]
        c = Counter(p["ground_truth"] for p in rows)
        print(f"  {bucket:<18} {c.get('KEEP',0):>6} {c.get('REJECT',0):>6} {c.get('ERROR',0):>6}")
    total_c = Counter(p["ground_truth"] for p in corpus)
    print(f"  {'TOTAL':<18} {total_c.get('KEEP',0):>6} {total_c.get('REJECT',0):>6} {total_c.get('ERROR',0):>6}")
    print(f"\nOverall: {total_c.get('REJECT',0)}/{len(corpus)} would be rejected by gold standard")

    # Save corpus + ground truth
    with open(GROUND_TRUTH_OUT, "w") as f:
        json.dump(corpus, f, indent=2, default=str)
    print(f"\nSaved corpus + ground truth to {GROUND_TRUTH_OUT}")
    print("Use scripts/rescore_eval_prompt.py to test prompt variants against this corpus.")


if __name__ == "__main__":
    main()
