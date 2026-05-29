"""
Expanded bake-off: bigger dataset + quantitative scoring + new prompt variants.

V1 used the 4 established-mode regions (small corpus). V2 adds the 20
proposed-mode clusters from the live API, giving ~24 real test cases.
Also adds quantitative scoring (generic-word penalty, length penalty,
format check) so we can compare prompts numerically, not just visually.

Run: cd backend && .venv/bin/python scripts/topic_label_bakeoff_v2.py

Cost estimate: ~24 clusters × 5 prompts × 2 models = 240 calls.
  - gpt-4o-mini:  240 × $0.00013 = ~$0.03
  - gpt-4o:       240 × $0.0021  = ~$0.50
Total: ~$0.55 to find the winning prompt definitively.
"""
from __future__ import annotations
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()


# ── Build dataset from live API ────────────────────────────────────────────

def fetch_corpus() -> list[dict]:
    """Pull both established regions and proposed clusters as test cases."""
    import requests
    from hdbscan import HDBSCAN
    import numpy as np

    corpus = []

    # Established: pre-cluster via HDBSCAN (same as production will)
    est = requests.get("http://localhost:8000/api/narrative-frames/landscape-established").json()
    est_frames = est["frames"]
    if len(est_frames) >= 2:
        coords = np.array([[f["x"], f["y"]] for f in est_frames])
        labels = HDBSCAN(
            min_cluster_size=2, min_samples=1, metric="euclidean",
            cluster_selection_method="leaf",
        ).fit_predict(coords)
        by_rid = defaultdict(list)
        for f, l in zip(est_frames, labels):
            if l < 0:
                continue
            by_rid[int(l)].append((f["name"], f.get("description") or ""))
        for rid, members in by_rid.items():
            corpus.append({
                "source": "established",
                "id": f"est_{rid}",
                "members": members,
            })

    # Proposed: each cluster has many candidate-frames with suggested_name + evidence_quote.
    # Group by cluster_id, take the first 6 members per cluster (saves tokens, plenty for labeling).
    prop = requests.get(
        "http://localhost:8000/api/narrative-frames/candidate-frames/landscape?days_back=21",
    ).json()
    by_cid = defaultdict(list)
    for p in prop["points"]:
        if p["cluster_id"] >= 0:
            by_cid[p["cluster_id"]].append((p["suggested_name"], (p.get("evidence_quote") or "")[:200]))
    for cid, members in sorted(by_cid.items()):
        corpus.append({
            "source": "proposed",
            "id": f"prop_{cid}",
            "members": members[:6],
        })

    return corpus


# ── Prompt variants ───────────────────────────────────────────────────────
# Carrying forward F_role (V1 winner) plus 4 new variants designed to
# attack F_role's edge-case weaknesses (sometimes too long; doesn't always
# pick the most specific noun phrase when "&" notation isn't called for).

PROMPT_F_ROLE = """You are a political analyst building a narrative topic map for the {race} race. Each cluster below was grouped because the narratives discuss related issues.

Give this cluster a precise 1-3 word topic label. Title Case. No punctuation. No quotes.

Be SPECIFIC: prefer "Medicaid Cuts" over "Healthcare"; prefer "Insider Trading" over "Ethics"; prefer "ACA Subsidies" over "Insurance".

If the cluster genuinely mixes 2+ topics, pick the dominant one and lead with it (e.g. "Healthcare & Demographics" if healthcare is the larger theme).

Cluster:
{narratives}"""

# F_role + few-shot examples from E. Tests whether adding 3 example
# clusters with ideal labels improves consistency.
PROMPT_I_ROLE_FEW_SHOT = """You are a political analyst building a narrative topic map for the {race} race.

Give each cluster a precise 1-3 word topic label. Title Case. No punctuation. No quotes.

Examples:
Cluster: "Medicaid Cuts in Pennsylvania", "ACA Subsidy Expiration", "Hospital Closures Surge"
Label: Medicaid Cuts

Cluster: "Voter ID Restrictions", "Mail Ballot Drop-Boxes Removed", "Polling Place Closures"
Label: Voting Access

Cluster: "Insider Trading Allegations", "Stock Disclosure Failures", "Ethics Committee Probe"
Label: Insider Trading

Cluster: "Federal Bridge Funding", "Amtrak Expansion Proposal", "Highway Maintenance Backlog"
Label: Infrastructure Investment

Now label this cluster:
{narratives}

Label:"""

# F_role with HARD length cap. Tests whether forcing 1-2 words (3 max)
# improves usability without losing accuracy.
PROMPT_J_TIGHT = """You are a political analyst labeling narrative clusters for the {race} race topic map.

Label this cluster with a topic. Rules:
- PREFER 1-2 words. 3 words MAX. Never more.
- Title Case. No punctuation. No quotes. No "&" or "and".
- Specific over generic ("Insider Trading" not "Ethics"; "Medicaid" not "Healthcare").
- If two topics are mixed, pick the dominant ONE — don't combine.

Cluster:
{narratives}

Label:"""

# F_role + decisive tiebreaking. Tests whether explicit guidance for
# uncertain cases improves muddy clusters.
PROMPT_K_DECISIVE = """You are a political analyst building a topic map for the {race} race.

Label this cluster with a 1-3 word topic. Title Case. No punctuation. No quotes.

Be SPECIFIC: prefer concrete nouns ("Medicaid Cuts") over abstract categories ("Healthcare", "Policy", "Ethics").

If uncertain between two themes:
- Pick the one mentioned in MORE narratives in the cluster.
- If still tied, pick the more concrete one.
- Only use "X & Y" notation if both are absolutely co-equal.

Cluster:
{narratives}

Label:"""

# Hybrid: role + few-shot + tight format + decisive tiebreaking.
PROMPT_L_HYBRID = """You are a political analyst building a narrative topic map for the {race} race.

Label this cluster with a 1-3 word topic. Title Case. No punctuation. No quotes.

Rules:
- Specific concrete nouns > broad categories ("Medicaid Cuts" not "Healthcare"; "Insider Trading" not "Ethics").
- Prefer 1-2 words; 3 word max.
- For mixed clusters, pick the dominant theme (the topic in most narratives). Use "X & Y" only as a last resort.

Examples:
- Stock trades, ethics probe, financial disclosures → Insider Trading
- Medicaid cuts, ACA subsidies, hospital closures → Healthcare Cuts
- District funding, federal grants, local infrastructure → District Funding
- Voter ID, drop-boxes, polling closures → Voting Access

Cluster:
{narratives}

Label:"""

PROMPTS = {
    "F_role":           PROMPT_F_ROLE,
    "I_role_few_shot":  PROMPT_I_ROLE_FEW_SHOT,
    "J_tight":          PROMPT_J_TIGHT,
    "K_decisive":       PROMPT_K_DECISIVE,
    "L_hybrid":         PROMPT_L_HYBRID,
}

RACE_DESCRIPTOR = "PA-08 U.S. House 2026"


# ── Provider adapters (reused from V1) ────────────────────────────────────

@dataclass
class ModelResult:
    label: str
    latency_ms: int
    error: Optional[str] = None


def _extract_label(raw: str) -> str:
    text = (raw or "").strip()
    if "LABEL:" in text:
        text = text.split("LABEL:", 1)[1].strip()
    if "\n" in text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = lines[-1] if lines else text
    return text.strip().strip('"').strip("'").rstrip(".").strip()


def make_openai_caller(model: str) -> Callable[[str], ModelResult]:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def call(prompt: str) -> ModelResult:
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=40,
                temperature=0.3,
            )
            return ModelResult(
                label=_extract_label(resp.choices[0].message.content or ""),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return ModelResult(label="", latency_ms=int((time.time() - t0) * 1000), error=str(e))
    return call


MODELS = [
    ("gpt-4o-mini", lambda: make_openai_caller("gpt-4o-mini")),
    ("gpt-4o",      lambda: make_openai_caller("gpt-4o")),
]


# ── Quantitative scoring ──────────────────────────────────────────────────
# Each label gets a score; HIGHER is better. We're proxying for the things
# a user would actually care about: specific, well-formed, scannable.

GENERIC_WORDS = {
    "politics", "issues", "campaign", "voters", "policy", "government",
    "matters", "concerns", "topics", "candidates", "race", "election",
    "political",
}

def score_label(label: str) -> dict:
    """Score a label out of 10. Components:
      - format (0-3): well-formed Title Case, no junk chars
      - length (0-3): 1-3 words is full; 4+ words penalized
      - specificity (0-4): no generic placeholders
    """
    if not label:
        return {"format": 0, "length": 0, "specificity": 0, "total": 0}

    # Format
    fmt = 3
    if any(c in label for c in '."\'`'):
        fmt -= 1
    # Punctuation that's NOT '&' (allowed for compound topics)
    if any(c in label for c in ',;:!?'):
        fmt -= 1
    if not label[0].isupper():
        fmt -= 1
    fmt = max(0, fmt)

    # Length
    words = label.split()
    if len(words) == 0:
        length = 0
    elif len(words) <= 3:
        length = 3
    elif len(words) == 4:
        length = 2
    elif len(words) == 5:
        length = 1
    else:
        length = 0

    # Specificity — penalize each generic word
    lower_words = [w.lower().strip(".,&") for w in words]
    generic_count = sum(1 for w in lower_words if w in GENERIC_WORDS)
    spec = max(0, 4 - 2 * generic_count)

    total = fmt + length + spec
    return {"format": fmt, "length": length, "specificity": spec, "total": total}


# ── Runner ────────────────────────────────────────────────────────────────

def format_narratives(members: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {name}: {desc}" if desc else f"- {name}" for name, desc in members)


def main():
    print("Fetching corpus from live API…", flush=True)
    corpus = fetch_corpus()
    print(f"Got {len(corpus)} test clusters "
          f"({sum(1 for c in corpus if c['source'] == 'established')} established + "
          f"{sum(1 for c in corpus if c['source'] == 'proposed')} proposed)\n")

    callers = {name: factory() for name, factory in MODELS}

    # results[cluster_id][prompt_name][model_name] = ModelResult
    results: dict[str, dict[str, dict[str, ModelResult]]] = {}

    total_calls = len(corpus) * len(PROMPTS) * len(callers)
    call_i = 0
    for cluster in corpus:
        cid = cluster["id"]
        results[cid] = {}
        narratives = format_narratives(cluster["members"])
        for prompt_name, template in PROMPTS.items():
            results[cid][prompt_name] = {}
            prompt = template.format(race=RACE_DESCRIPTOR, narratives=narratives)
            for model_name, caller in callers.items():
                call_i += 1
                print(f"  [{call_i}/{total_calls}] {cid} | {prompt_name} | {model_name}", flush=True)
                results[cid][prompt_name][model_name] = caller(prompt)

    # ── Per-cluster results ────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("PER-CLUSTER RESULTS")
    print("=" * 100)
    for cluster in corpus:
        cid = cluster["id"]
        print(f"\n## {cid} ({cluster['source']}, {len(cluster['members'])} members)")
        print("Member narratives:")
        for name, _ in cluster["members"][:5]:
            print(f"  - {name}")
        if len(cluster["members"]) > 5:
            print(f"  ... and {len(cluster['members']) - 5} more")
        print()
        col_w = 32
        header = "Prompt".ljust(18) + "".join(m.ljust(col_w) for m in callers)
        print(header)
        print("-" * len(header))
        for prompt_name in PROMPTS:
            row = [prompt_name.ljust(18)]
            for model_name in callers:
                r = results[cid][prompt_name][model_name]
                label = r.label or f"ERR: {r.error[:18] if r.error else 'empty'}"
                row.append(label[:col_w-1].ljust(col_w))
            print("".join(row))

    # ── Aggregate scoring ──────────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("AGGREGATE QUALITY SCORES (per-prompt × per-model averages)")
    print("=" * 100)
    print(f"\n{'Prompt':<18}{'Model':<18}{'Avg Score':>10}{'Format':>8}{'Length':>8}{'Specific':>10}{'Generic':>9}")
    print("-" * 81)
    for prompt_name in PROMPTS:
        for model_name in callers:
            totals = []
            formats = []
            lengths = []
            specs = []
            generics = 0
            n = 0
            for cid in results:
                r = results[cid][prompt_name][model_name]
                if not r.label:
                    continue
                s = score_label(r.label)
                totals.append(s["total"])
                formats.append(s["format"])
                lengths.append(s["length"])
                specs.append(s["specificity"])
                low = [w.lower().strip(".,&") for w in r.label.split()]
                if any(w in GENERIC_WORDS for w in low):
                    generics += 1
                n += 1
            if n == 0:
                continue
            print(f"{prompt_name:<18}{model_name:<18}"
                  f"{sum(totals)/n:>10.2f}"
                  f"{sum(formats)/n:>8.2f}"
                  f"{sum(lengths)/n:>8.2f}"
                  f"{sum(specs)/n:>10.2f}"
                  f"{generics:>9}")

    # ── Cross-model agreement per prompt ──────────────────────────────────
    # Higher agreement = prompt is robust regardless of model choice.
    print("\n## Cross-model agreement (gpt-4o vs gpt-4o-mini matching labels)")
    for prompt_name in PROMPTS:
        matches = 0
        n = 0
        for cid in results:
            la = results[cid][prompt_name].get("gpt-4o")
            lb = results[cid][prompt_name].get("gpt-4o-mini")
            if not la or not lb or not la.label or not lb.label:
                continue
            n += 1
            if la.label.lower() == lb.label.lower():
                matches += 1
        if n:
            print(f"  {prompt_name:<18} {matches}/{n} ({100*matches/n:.0f}%)")

    # ── Length distribution ───────────────────────────────────────────────
    print("\n## Word count distribution (mode → ideal for scanning)")
    for prompt_name in PROMPTS:
        for model_name in callers:
            counts = defaultdict(int)
            for cid in results:
                r = results[cid][prompt_name][model_name]
                if r.label:
                    counts[len(r.label.split())] += 1
            dist = " ".join(f"{w}w:{c}" for w, c in sorted(counts.items()))
            print(f"  {prompt_name:<18} {model_name:<18} {dist}")

    print("\nDone. Look at per-cluster results for nuance; aggregate scores for quick comparison.")


if __name__ == "__main__":
    main()
