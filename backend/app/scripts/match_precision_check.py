"""Dry-run precision check for the narrative-frame matcher.

Picks N recent articles that already have a story_cluster_id and a relevance
score >= 55, runs the SAME prompt match_article_to_frames uses, and dumps the
results to a CSV WITHOUT writing to the database.

Goal: see the new (confidence-aware) prompt's output on real articles before
deciding whether to add a confidence threshold or change the prompt further.

Usage:
    cd backend
    python -m app.scripts.match_precision_check --limit 50 --out /tmp/precision.csv

Optionally restrict to certain frame owner types:
    python -m app.scripts.match_precision_check --owner media --limit 30

Cost: ~$0.0001 per article (judge LLM call). 50 articles ≈ $0.005.
"""
from __future__ import annotations
import argparse
import csv
import json
import sys
from pathlib import Path

# Make `app` importable when run as a script from the backend/ dir.
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db import SessionLocal  # noqa: E402
from app.models import SourceItem, NarrativeFrame  # noqa: E402
from app.services.narrative_frames import (  # noqa: E402
    _campaign_context,
    _article_passes_keyword_gate,
    _validate_snippet,
    _repair_json,
)
from app.services.llm_provider import get_judge_provider, MockLLMProvider  # noqa: E402


def _build_prompt(item: SourceItem, frames: list[NarrativeFrame], ctx: dict) -> str:
    """Mirror match_article_to_frames' prompt builder. Kept inline so this
    script is independent of internal refactors in narrative_frames.py."""
    OWNER_ROLE = {
        "candidate": "OUR MESSAGE",
        "opponent":  "OPPONENT ATTACK",
        "media":     "MEDIA THEME",
    }
    frames_list = "\n".join(
        f"{i+1}. [{OWNER_ROLE.get(f.owner_type, f.owner_type.upper())}] {f.name}: {f.description or ''}"
        for i, f in enumerate(frames)
    )

    cached_summary = item.summary or item.title or ""
    cached_framing = ""
    cached_attacks = ""
    if item.structured_extraction:
        try:
            extracted = json.loads(item.structured_extraction)
            cached_summary = extracted.get("one_sentence") or cached_summary
            cached_framing = extracted.get("framing") or ""
            attacks = extracted.get("opponent_attacks") or []
            if attacks:
                cached_attacks = "\n".join(
                    f"- {a.get('text','')}" for a in attacks if a.get("text")
                )
        except Exception:
            pass

    _MAX_BODY_CHARS = 2000
    body_excerpt = ""
    if item.raw_text:
        body_clean = item.raw_text.strip()
        if len(body_clean) > _MAX_BODY_CHARS:
            body_excerpt = body_clean[:_MAX_BODY_CHARS].rstrip() + " …[truncated]"
        else:
            body_excerpt = body_clean

    article_section = f"""Title: {item.title or "No title"}
Summary: {cached_summary}"""
    if cached_framing:
        article_section += f"\nFraming: {cached_framing}"
    if cached_attacks:
        article_section += f"\nOpponent statements:\n{cached_attacks}"
    if body_excerpt:
        article_section += f"\n\nArticle body:\n{body_excerpt}"

    return f"""You are a political research assistant tagging news articles with the campaign narratives they cover.

NARRATIVES (each tagged with its perspective):
{frames_list}

ARTICLE:
{article_section}

TASK:
Decide which narratives this article meaningfully covers. The tag on each narrative matters:

[OUR MESSAGE] — Match ONLY if the article reports on or amplifies {ctx["candidate"]}'s own messaging/record on this topic. Do NOT match if the article attacks, mocks, or disputes this message — an attack piece from the opponent that merely mentions the topic does not count.

[OPPONENT ATTACK] — Match if the article covers, repeats, or reports on this attack line, whether as criticism of the opponent or as the attack itself being made. Both "Bresnahan buys stocks" and "Cognetti attacks Bresnahan on stocks" count.

[MEDIA THEME] — Match ONLY if the article discusses this theme specifically in the context of the {ctx["race"]} race — that is, it names {ctx["candidate"]}, an opponent, the district, or the race itself while covering this theme. Generic national coverage of the same topic does NOT count as a match.

Additional rules:
- MOST articles match 0 or 1 narratives. Match more than one narrative ONLY when the article body contains substantively distinct information about each.
- Do NOT match on vague thematic overlap, shared keywords, or topical adjacency — match only when the article has specific, substantive information about that narrative.
- HARD REQUIREMENT: For each match you propose, you must quote a verbatim sentence from the "Article body" section above that directly supports the match. If no such sentence exists in the body, do NOT include the match. Snippets cannot be paraphrased or summarized — they must be copied character-for-character from the body.
- Rate your confidence (0-100) per match:
    90-100 — central topic of the article, named explicitly with detail
    75-89  — covered as a clear secondary topic with substantive detail
    60-74  — mentioned with some specificity but not the article's focus
    40-59  — loose thematic overlap or passing reference only — DO NOT INCLUDE
    0-39   — DO NOT INCLUDE
- If you are uncertain whether the article truly covers a narrative, omit it.

Return ONLY a JSON array. Each element: {{"frame": <number>, "confidence": <0-100>, "snippet": "<verbatim sentence from the article body>"}}
Return [] if no narratives apply."""


def _parse_response(raw: str) -> list:
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start != -1 and bracket_end != -1:
        text = text[bracket_start:bracket_end + 1]
    text = _repair_json(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30, help="Number of articles to test")
    ap.add_argument("--owner", choices=["candidate", "opponent", "media", "any"],
                    default="any", help="Restrict to frames of this owner_type")
    ap.add_argument("--out", default="/tmp/precision_check.csv",
                    help="Output CSV path")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        frames_q = db.query(NarrativeFrame).filter(NarrativeFrame.active == True)
        if args.owner != "any":
            frames_q = frames_q.filter(NarrativeFrame.owner_type == args.owner)
        frames = frames_q.all()
        if not frames:
            print(f"No active frames for owner={args.owner}", file=sys.stderr)
            return 1

        # Pull most-recent N relevant articles with a cluster id
        items = (
            db.query(SourceItem)
            .filter(
                SourceItem.story_cluster_id.isnot(None),
                SourceItem.race_relevance_score >= 55,
                SourceItem.title.isnot(None),
            )
            .order_by(SourceItem.published_at.desc().nullslast())
            .limit(args.limit)
            .all()
        )
        print(f"Testing {len(items)} articles against {len(frames)} frames "
              f"(owner={args.owner})", file=sys.stderr)

        ctx = _campaign_context(db)
        provider = get_judge_provider()
        if isinstance(provider, MockLLMProvider):
            print("LLM is in MockLLMProvider mode — set API keys and retry",
                  file=sys.stderr)
            return 2

        rows: list[dict] = []
        for n, item in enumerate(items, 1):
            prompt = _build_prompt(item, frames, ctx)
            try:
                raw = provider.complete(prompt)
            except Exception as e:
                print(f"  [{n}/{len(items)}] item={item.id} LLM error: {e}",
                      file=sys.stderr)
                rows.append({
                    "item_id": item.id,
                    "title": (item.title or "")[:120],
                    "source": item.source_name or "",
                    "published_at": str(item.published_at or ""),
                    "frame_id": "",
                    "frame_name": "",
                    "owner_type": "",
                    "confidence": "",
                    "keyword_gate": "",
                    "snippet_valid": "",
                    "snippet": "",
                    "error": str(e),
                })
                continue

            matches = _parse_response(raw)
            if not matches:
                rows.append({
                    "item_id": item.id,
                    "title": (item.title or "")[:120],
                    "source": item.source_name or "",
                    "published_at": str(item.published_at or ""),
                    "frame_id": "",
                    "frame_name": "(no match)",
                    "owner_type": "",
                    "confidence": "",
                    "keyword_gate": "",
                    "snippet_valid": "",
                    "snippet": "",
                    "error": "",
                })
                print(f"  [{n}/{len(items)}] item={item.id} no matches",
                      file=sys.stderr)
                continue

            for entry in matches:
                if not isinstance(entry, dict):
                    continue
                idx = entry.get("frame")
                if not isinstance(idx, int) or idx < 1 or idx > len(frames):
                    continue
                frame = frames[idx - 1]
                conf_raw = entry.get("confidence")
                conf = int(conf_raw) if isinstance(conf_raw, (int, float)) else None
                raw_snippet = entry.get("snippet") or ""
                snippet = raw_snippet[:200]
                gate_ok = _article_passes_keyword_gate(
                    item, frame.name, frame.description or "", ctx
                )
                snip_valid = _validate_snippet(raw_snippet, item) is not None
                rows.append({
                    "item_id": item.id,
                    "title": (item.title or "")[:120],
                    "source": item.source_name or "",
                    "published_at": str(item.published_at or ""),
                    "frame_id": frame.id,
                    "frame_name": frame.name,
                    "owner_type": frame.owner_type,
                    "confidence": conf if conf is not None else "",
                    "keyword_gate": "pass" if gate_ok else "FAIL",
                    "snippet_valid": "pass" if snip_valid else "FAIL",
                    "snippet": snippet,
                    "error": "",
                })
            print(f"  [{n}/{len(items)}] item={item.id} → {len(matches)} matches",
                  file=sys.stderr)

        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "item_id", "title", "source", "published_at",
                "frame_id", "frame_name", "owner_type",
                "confidence", "keyword_gate", "snippet_valid",
                "snippet", "error",
            ])
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        # Summary
        match_rows = [r for r in rows if r["frame_id"] != ""]
        if match_rows:
            confs = [r["confidence"] for r in match_rows if isinstance(r["confidence"], int)]
            print(f"\nTotal matches: {len(match_rows)}", file=sys.stderr)
            if confs:
                print(f"Confidence distribution: "
                      f"min={min(confs)} max={max(confs)} "
                      f"mean={sum(confs)/len(confs):.1f}", file=sys.stderr)
                buckets = {"<40": 0, "40-59": 0, "60-74": 0, "75-89": 0, "90+": 0}
                for c in confs:
                    if c < 40:    buckets["<40"]    += 1
                    elif c < 60:  buckets["40-59"]  += 1
                    elif c < 75:  buckets["60-74"]  += 1
                    elif c < 90:  buckets["75-89"]  += 1
                    else:         buckets["90+"]    += 1
                for k, v in buckets.items():
                    print(f"  {k}: {v}", file=sys.stderr)
            gate_fails = [r for r in match_rows if r["keyword_gate"] == "FAIL"]
            snip_fails = [r for r in match_rows if r["snippet_valid"] == "FAIL"]
            print(f"Keyword-gate failures: {len(gate_fails)}", file=sys.stderr)
            print(f"Snippet-validation failures: {len(snip_fails)}", file=sys.stderr)
            survive_snip = [r for r in match_rows if r["snippet_valid"] == "pass"]
            print(f"Survive snippet validation: {len(survive_snip)} "
                  f"of {len(match_rows)} ({100*len(survive_snip)/len(match_rows):.0f}%)",
                  file=sys.stderr)
        print(f"\nWrote {len(rows)} rows → {args.out}", file=sys.stderr)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
