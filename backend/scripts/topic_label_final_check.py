"""
Final validation: run the chosen prompt (I_role_few_shot + compound notation)
against the full 24-cluster corpus to confirm it doesn't regress on the
muddy cases the "&" line was added to fix.

Compares head-to-head against the original I_role_few_shot (no "&" line)
so we can see exactly what the new instruction changes.
"""
from __future__ import annotations
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


# Original I_role_few_shot (no compound instruction).
PROMPT_ORIGINAL = """You are a political analyst building a narrative topic map for the {race} race.

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

# Final candidate (one new line added for compound notation).
PROMPT_FINAL = """You are a political analyst building a narrative topic map for the {race} race.

Give each cluster a precise 1-3 word topic label. Title Case. No punctuation. No quotes.

If the cluster genuinely combines two equal themes, use "X & Y".

Examples:
Cluster: "Medicaid Cuts in Pennsylvania", "ACA Subsidy Expiration", "Hospital Closures Surge"
Label: Medicaid Cuts

Cluster: "Voter ID Restrictions", "Mail Ballot Drop-Boxes Removed", "Polling Place Closures"
Label: Voting Access

Cluster: "Insider Trading Allegations", "Stock Disclosure Failures", "Ethics Committee Probe"
Label: Insider Trading

Cluster: "Federal Bridge Funding", "Amtrak Expansion Proposal", "Highway Maintenance Backlog"
Label: Infrastructure Investment

Cluster: "Healthcare Subsidies Debate", "Census Migration Patterns", "Suburban Demographic Shift"
Label: Healthcare & Demographics

Now label this cluster:
{narratives}

Label:"""

PROMPTS = {
    "original":      PROMPT_ORIGINAL,
    "final":         PROMPT_FINAL,
}

RACE_DESCRIPTOR = "PA-08 U.S. House 2026"


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
                max_tokens=40, temperature=0.3,
            )
            return ModelResult(
                label=_extract_label(resp.choices[0].message.content or ""),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return ModelResult(label="", latency_ms=int((time.time() - t0) * 1000), error=str(e))
    return call


def fetch_corpus() -> list[dict]:
    import requests
    from hdbscan import HDBSCAN
    import numpy as np
    corpus = []

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
            if l < 0: continue
            by_rid[int(l)].append((f["name"], f.get("description") or ""))
        for rid, members in by_rid.items():
            corpus.append({"source": "est", "id": f"est_{rid}", "members": members})

    prop = requests.get(
        "http://localhost:8000/api/narrative-frames/candidate-frames/landscape?days_back=21",
    ).json()
    by_cid = defaultdict(list)
    for p in prop["points"]:
        if p["cluster_id"] >= 0:
            by_cid[p["cluster_id"]].append((p["suggested_name"], (p.get("evidence_quote") or "")[:200]))
    for cid, members in sorted(by_cid.items()):
        corpus.append({"source": "prop", "id": f"prop_{cid}", "members": members[:6]})

    return corpus


def format_narratives(members):
    return "\n".join(f"- {name}: {desc}" if desc else f"- {name}" for name, desc in members)


GENERIC = {"politics","issues","campaign","voters","policy","government","matters","concerns","topics","candidates","race","election","political"}

def score(label):
    if not label: return 0
    fmt = 3
    if any(c in label for c in '."\'`'): fmt -= 1
    if any(c in label for c in ',;:!?'): fmt -= 1
    if not label[0].isupper(): fmt -= 1
    fmt = max(0, fmt)
    words = label.split()
    length = 3 if len(words) <= 3 else max(0, 3 - (len(words) - 3))
    low = [w.lower().strip(".,&") for w in words]
    spec = max(0, 4 - 2 * sum(1 for w in low if w in GENERIC))
    return fmt + length + spec


def main():
    print("Fetching live corpus…")
    corpus = fetch_corpus()
    print(f"Got {len(corpus)} clusters\n")

    caller = make_openai_caller("gpt-4o-mini")

    results = {}
    total = len(corpus) * len(PROMPTS)
    i = 0
    for cluster in corpus:
        cid = cluster["id"]
        results[cid] = {}
        ns = format_narratives(cluster["members"])
        for pname, template in PROMPTS.items():
            i += 1
            print(f"  [{i}/{total}] {cid} | {pname}", flush=True)
            results[cid][pname] = caller(template.format(race=RACE_DESCRIPTOR, narratives=ns))

    # Head-to-head
    print("\n" + "=" * 90)
    print("HEAD-TO-HEAD: original I_role_few_shot vs FINAL (with '&' instruction)")
    print("=" * 90)
    print(f"\n{'Cluster':<14}{'Original':<32}{'Final':<32}{'Δ'}")
    print("-" * 90)
    changes = 0
    compound_used = 0
    same = 0
    for cluster in corpus:
        cid = cluster["id"]
        orig = results[cid]["original"].label
        final = results[cid]["final"].label
        delta = "" if orig.lower() == final.lower() else "CHANGED"
        if "&" in final:
            compound_used += 1
            delta += " [&]"
        if orig.lower() == final.lower():
            same += 1
        else:
            changes += 1
        print(f"{cid:<14}{orig[:30]:<32}{final[:30]:<32}{delta}")

    print(f"\nSummary: {same}/{len(corpus)} identical, {changes} changed, {compound_used} used '&' compound")

    # Quality scores
    orig_scores = [score(results[c['id']]['original'].label) for c in corpus if results[c['id']]['original'].label]
    final_scores = [score(results[c['id']]['final'].label) for c in corpus if results[c['id']]['final'].label]
    print(f"\nAverage score:")
    print(f"  original:  {sum(orig_scores)/len(orig_scores):.2f}  (n={len(orig_scores)})")
    print(f"  final:     {sum(final_scores)/len(final_scores):.2f}  (n={len(final_scores)})")

    # Print which clusters the new prompt would label as compound
    print("\nClusters where FINAL used '&' compound:")
    for cluster in corpus:
        cid = cluster["id"]
        final = results[cid]["final"].label
        if "&" in final:
            print(f"\n  {cid} → {final}")
            print(f"     Members:")
            for name, _ in cluster["members"][:5]:
                print(f"       - {name}")

    print("\nDone.")


if __name__ == "__main__":
    main()
