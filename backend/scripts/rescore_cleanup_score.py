"""
Cleanup phase 1: score every NarrativeFrameMention with the v5_refined
prompt + gpt-4o. Save verdicts to a checkpointed JSON file — NO
DESTRUCTIVE ACTION. Phase 2 will audit + apply approved deletes.

Why score-only first
--------------------
A bad prompt could purge half the dataset before anyone notices. By
separating "judgment" from "action", we get an artifact (the JSON) that
can be:
  - Spot-checked for quality
  - Selectively applied (only delete the high-confidence rejects)
  - Re-run with different prompts without re-deleting
  - Audited by hand or by a second model

Checkpointing
-------------
Every 50 calls, the script writes the partial results to disk so an
interrupt (ctrl-C, network blip, rate limit) doesn't lose work. Re-running
the script picks up where it left off (skips already-judged mention_ids).

Cost
----
1054 mentions × $0.0021 (gpt-4o) = ~$2.20 once.
~17 minutes at ~1 call/sec.

Run: cd backend && .venv/bin/python scripts/rescore_cleanup_score.py
"""
from __future__ import annotations
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()


OUTPUT_PATH = "/tmp/rescore_cleanup_verdicts.json"
CHECKPOINT_EVERY = 50  # save partial results every N calls
BATCH_REPORT_EVERY = 50  # print running stats every N calls
MODEL = "gpt-4o"


# Same prompt that won the bake-off (see scripts/rescore_eval_prompt.py V5).
PROMPT_V5 = """You are auditing a political narrative-tracking database.

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

Frame "Bresnahan's Healthcare Record" + "Cognetti criticized Bresnahan's past Medicaid votes" → KEEP

Frame "Bresnahan's Healthcare Record" + "Mayor Cognetti unveiled a $27M downtown traffic plan" → REJECT

Frame "Cognetti's Anti-Corruption" + "I've shown in Scranton we can build government for people and be honest with people" → KEEP

Frame "Cognetti Flips NEPA Seat" + "NEPA is fired up and ready to win in November" → KEEP

Frame "Cognetti Flips NEPA Seat" + "Democrats hope to win back the House in 2026" → REJECT

Frame "Bresnahan's Healthcare Record" + "DCCC says Bresnahan is poster child of corruption" → REJECT

Frame "Bresnahan's Tax Cuts Lack Benefits" + "ATR notes Bresnahan signed Taxpayer Protection Pledge" → KEEP

Frame "NEPA Support" + "Cognetti will be a voice in Congress that puts NEPA first" → KEEP

Frame "Healthcare Debate" + "Public healthcare premiums will skyrocket after Jan 2026 due to expiring subsidies" → REJECT

Frame "Bresnahan Delivers District Funding" + "Bresnahan worked to ensure PA-08 hospitals received funding" → KEEP

FRAME
  name: {frame_name}
  description: {frame_description}

EXTRACT
  text: "{extract}"

Respond with JSON only:
{{"verdict": "KEEP" | "REJECT", "reason": "<one short sentence>"}}"""


def load_existing() -> dict[int, dict]:
    """Resume from previous run if checkpoint exists."""
    if not os.path.exists(OUTPUT_PATH):
        return {}
    with open(OUTPUT_PATH) as f:
        data = json.load(f)
    return {int(r["mention_id"]): r for r in data}


def save(results: list[dict]):
    with open(OUTPUT_PATH, "w") as f:
        json.dump(results, f, indent=2, default=str)


def fetch_all_mentions() -> list[dict]:
    """Pull every active NarrativeFrameMention with metadata for scoring."""
    from app.db import SessionLocal
    from app.models import NarrativeFrameMention, NarrativeFrame, SourceItem, Outlet
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
    return [{
        "mention_id": r.id,
        "extract": r.extracted_text,
        "frame_id": r.frame_id,
        "frame_name": r.frame_name,
        "frame_description": r.frame_description,
        "frame_owner": r.frame_owner,
        "source": r.outlet_name or r.source_name or r.source_title or "unknown",
    } for r in rows]


def make_judge():
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def call(pair: dict) -> dict:
        prompt = PROMPT_V5.format(
            frame_name=pair["frame_name"],
            frame_description=pair["frame_description"] or "(no description)",
            extract=pair["extract"].replace('"', "'")[:600],
        )
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=180,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            return {
                "verdict": (data.get("verdict") or "").upper(),
                "reason": data.get("reason") or "",
            }
        except Exception as e:
            return {"verdict": "ERROR", "reason": str(e)[:120]}
    return call


def main():
    print("Loading mentions from DB…")
    mentions = fetch_all_mentions()
    print(f"  {len(mentions)} mentions total")

    existing = load_existing()
    print(f"  {len(existing)} already scored (resuming from checkpoint)")

    todo = [m for m in mentions if m["mention_id"] not in existing]
    print(f"  {len(todo)} remaining to score")

    if not todo:
        print("\nNothing to do. All mentions already scored.")
        return

    judge = make_judge()
    results = list(existing.values())
    t0 = time.time()
    n_keep_running = sum(1 for r in results if r.get("verdict") == "KEEP")
    n_reject_running = sum(1 for r in results if r.get("verdict") == "REJECT")

    for i, pair in enumerate(todo):
        verdict = judge(pair)
        record = {**pair, **verdict}
        results.append(record)

        if verdict["verdict"] == "KEEP":
            n_keep_running += 1
        elif verdict["verdict"] == "REJECT":
            n_reject_running += 1

        # Inline reporting
        total = n_keep_running + n_reject_running
        if (i + 1) % BATCH_REPORT_EVERY == 0 or (i + 1) == len(todo):
            elapsed = time.time() - t0
            rate = (i + 1) / max(0.001, elapsed)
            remaining = (len(todo) - i - 1) / max(0.001, rate)
            pct_reject = (100 * n_reject_running / max(1, total))
            print(f"  [{i+1:4}/{len(todo)}] verdict={verdict['verdict']:>6} | "
                  f"running KEEP={n_keep_running} REJECT={n_reject_running} ({pct_reject:.0f}% reject) | "
                  f"{rate:.1f}/s, ETA {remaining:.0f}s")

        if (i + 1) % CHECKPOINT_EVERY == 0:
            save(results)

    save(results)
    print(f"\nDone. {n_keep_running} KEEP, {n_reject_running} REJECT")
    print(f"Saved to: {OUTPUT_PATH}")
    print(f"Total cost: ~${len(todo) * 0.0021:.2f}")


if __name__ == "__main__":
    main()
