"""A/B test Option A vs Option C perspective prompts on the same articles.

Option A: tightened single-axis — pro_candidate / pro_opponent / neutral
          where 'neutral' is reserved for genuinely off-topic content
          (every politically-relevant article picks a side).

Option C: two-axis — separates political_relevance (yes/tangential/no)
          from lean (pro_candidate / balanced / pro_opponent / NA).
          Lets us distinguish "truly balanced political coverage" from
          "off-topic content" — same gray color today.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_ab_test.py [N]

N defaults to 30.

Cost: ~$0.001 (60 LLM calls at gpt-4o-mini pricing).
"""
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.article_perspective import get_classifier
from app.services.llm_provider import OpenAIProvider, _parse_json_response


# ── Option A prompt (tightened neutral) ────────────────────────────────────

PROMPT_A_SYSTEM = """You classify political news articles for perspective in a head-to-head race.

You'll be told the two candidates (CANDIDATE_A vs CANDIDATE_B). Decide which campaign would WANT to spread this article.

CRITICAL RULES:
1. "neutral" is ONLY for genuinely off-topic content — bridge construction, weather, school events, sports, unrelated national news that doesn't reflect on the race in any way.

2. Every politically-relevant article picks a side. Even balanced reporting on a candidate-relevant topic favors someone:
   - Coverage of a scandal involving CANDIDATE_A → favors CANDIDATE_B (the scandal is opposition research now in the public sphere)
   - Coverage of an achievement by CANDIDATE_A → favors CANDIDATE_A (positive press)
   - National story about the partisan landscape → pick the side whose framing it reinforces
   - "Both sides equally critical" framing → still pick whichever side is being LESS criticized

3. If the article mentions either candidate by name, it has a lean. Find it.

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence>"}
"""


# ── Option C prompt (two-axis) ─────────────────────────────────────────────

PROMPT_C_SYSTEM = """You classify political news articles on TWO dimensions.

You'll be told the two candidates (CANDIDATE_A vs CANDIDATE_B).

DIMENSION 1 — political_relevance:
- "high":       article is directly about this race or one of the candidates
- "tangential": article is about a related topic (district, party, broader race politics) but not directly about these two candidates
- "none":       article is unrelated to the race entirely (bridge construction, weather, etc.)

DIMENSION 2 — lean (only meaningful when relevance is high or tangential):
- favored_candidate = NAME_A if the article would benefit CANDIDATE_A's campaign
- favored_candidate = NAME_B if the article would benefit CANDIDATE_B's campaign
- favored_candidate = "balanced" if the article is politically relevant but truly even-handed (rare — both sides quoted equally, neither's framing privileged)
- favored_candidate = "n/a" if relevance is "none"

Return strict JSON:
  {"political_relevance": "high" | "tangential" | "none",
   "favored_candidate": "<NAME_A>" | "<NAME_B>" | "balanced" | "n/a",
   "reason": "<one sentence>"}
"""


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()

    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name
    cand_party = cfg.party
    opp_name = opp.name
    opp_party = opp.party

    print(f"Race: {cand_name} ({cand_party}) vs {opp_name} ({opp_party})")

    classify = get_classifier(db)
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .all()
    )
    fallback = [it for it in items if classify(it).method == "fallback"]
    print(f"Fallback pool: {len(fallback)}. Sampling {n}…\n")

    random.seed(42)  # reproducible: same sample as earlier inspections
    sample = random.sample(fallback, min(n, len(fallback)))

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set; aborting.")
        return 1
    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")

    rows: list[dict] = []
    for i, item in enumerate(sample, 1):
        title = (item.title or "")[:200]
        summary = (item.summary or "")[:600]
        excerpt = (item.raw_text or "")[:600]
        user_prompt = (
            f"CANDIDATE_A: {cand_name} ({cand_party})\n"
            f"CANDIDATE_B: {opp_name} ({opp_party})\n\n"
            f"Article title: {title}\n"
            f"Summary: {summary}\n"
            f"Excerpt: {excerpt}\n"
        )

        # Option A
        try:
            raw_a = provider._chat(
                user_prompt=user_prompt + f"\nClassify. favored_candidate must be exactly {cand_name!r}, {opp_name!r}, or \"neutral\".",
                system_prompt=PROMPT_A_SYSTEM, json_mode=True, temperature=0, seed=42,
            )
            a = _parse_json_response(raw_a) or {}
        except Exception as e:
            a = {"favored_candidate": "ERROR", "reason": str(e)}

        # Option C
        try:
            raw_c = provider._chat(
                user_prompt=user_prompt + f"\nClassify on BOTH dimensions. favored_candidate must be exactly {cand_name!r}, {opp_name!r}, \"balanced\", or \"n/a\".",
                system_prompt=PROMPT_C_SYSTEM, json_mode=True, temperature=0, seed=42,
            )
            c = _parse_json_response(raw_c) or {}
        except Exception as e:
            c = {"political_relevance": "ERROR", "favored_candidate": "ERROR", "reason": str(e)}

        rows.append({"item": item, "a": a, "c": c})
        if i % 10 == 0:
            print(f"  [{i}/{len(sample)}]")

    # ── Side-by-side comparison ──────────────────────────────────────────
    print()
    print("=" * 110)
    print(f"{'id':>6} {'option_a':>15} {'C: rel':>14} {'C: lean':>14}  title")
    print("-" * 110)
    for r in rows:
        item = r["item"]
        a_pick = (r["a"].get("favored_candidate") or "?")[:14]
        c_rel = (r["c"].get("political_relevance") or "?")[:12]
        c_lean = (r["c"].get("favored_candidate") or "?")[:12]
        title = (item.title or "")[:60]
        print(f"  [{item.id:5d}] {a_pick:>15} {c_rel:>14} {c_lean:>14}  {title!r}")

    print()
    print("=" * 110)
    print("DETAILED:")
    for r in rows:
        item = r["item"]
        print(f"\n  [{item.id}] {(item.title or '')[:90]!r}")
        print(f"        summary: {(item.summary or '')[:160]!r}")
        print(f"        Option A → {r['a'].get('favored_candidate')!r}")
        print(f"                   reason: {(r['a'].get('reason') or '')[:120]!r}")
        print(f"        Option C → relevance={r['c'].get('political_relevance')!r}, "
              f"lean={r['c'].get('favored_candidate')!r}")
        print(f"                   reason: {(r['c'].get('reason') or '')[:120]!r}")

    # ── Disagreement / divergence stats ──────────────────────────────────
    from collections import Counter
    a_dist = Counter(r["a"].get("favored_candidate") for r in rows)
    c_rel_dist = Counter(r["c"].get("political_relevance") for r in rows)
    c_lean_dist = Counter(r["c"].get("favored_candidate") for r in rows)

    print()
    print("=" * 110)
    print("Option A distribution:")
    for k, v in a_dist.most_common():
        print(f"  {k!r:20s} {v}")
    print()
    print("Option C relevance distribution:")
    for k, v in c_rel_dist.most_common():
        print(f"  {k!r:20s} {v}")
    print("Option C lean distribution:")
    for k, v in c_lean_dist.most_common():
        print(f"  {k!r:20s} {v}")

    # Articles where they disagree
    print()
    print("=== Articles where Option C says 'balanced' but A picks a side ===")
    for r in rows:
        if r["c"].get("favored_candidate") == "balanced":
            item = r["item"]
            print(f"  [{item.id}] A={r['a'].get('favored_candidate')!r}, C=balanced")
            print(f"        {(item.title or '')[:90]!r}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
