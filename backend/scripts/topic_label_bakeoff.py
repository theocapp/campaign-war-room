"""
Prompt × model bake-off for topic-region labeling.

Picks the best (prompt, model) combination for the new "topic regions"
feature on the Landscape page. Each region is a HDBSCAN cluster over
established narrative-frame UMAP positions; we need a short 1-3 word
topic label that captures what the member narratives have in common.

Run: .venv/bin/python -m scripts.topic_label_bakeoff

Cost note: each (prompt × model × region) is one LLM call. With 4 prompts
× 3 models × 4 regions, that's 48 calls. At ~$0.001 per call on gpt-4o-mini
the bake-off costs ~$0.05. Sticking to cheap models so we can iterate.
"""
from __future__ import annotations
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

# Bootstrap import path so we can import app modules without installing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()


# ── Real regions from current HDBSCAN output ──────────────────────────────
# These are the 4 actual regions HDBSCAN produced from the established
# landscape, exactly what the LLM will see in production.

REAL_REGIONS = [
    {
        "id": 0,
        "frames": [
            ("Cognetti's Maternity Leave Inconsistency",
             "The discrepancy between Cognetti's national advocacy and local policy actions presents a pattern of inconsistency that can be attacked in future articles."),
            ("Bresnahan's Tax Cuts Lack Benefits",
             "Bresnahan's tax cut policies seen as ineffectual"),
            ("Bresnahan's Local Office Hours Unfulfilling",
             "Bresnahan's office hours are criticized for being ineffective"),
        ],
        "human_intuition": "broken-promises / policy-criticism (hard case)",
    },
    {
        "id": 1,
        "frames": [
            ("Cognetti's Anti-Corruption",
             "Paige Cognetti is running on an anti-corruption message, highlighting her commitment to cleaning up Congress and attacking insider trading."),
            ("Bresnahan's Stock Trades",
             "Rob Bresnahan is being accused of making millions from stock trades while in office, a claim pushed by Paige Cognetti's campaign."),
            ("Cognetti's Dual Campaigns",
             "Paige Cognetti is being criticized for running for Congress while seeking re-election as mayor."),
            ("Bresnahan's Support for Trump Criticized",
             "Bresnahan's endorsement by Trump questioned"),
            ("Cognetti's Deal with Fidelity Bank Raises Ethics Concerns",
             "Allegations regarding financial deals involving campaign donations and taxpayer funds."),
        ],
        "human_intuition": "ethics / corruption (clean case)",
    },
    {
        "id": 2,
        "frames": [
            ("PA District Demographics Shift Further Left",
             "Pennsylvania's 8th district is shifting further left"),
            ("Bresnahan's Healthcare Record",
             "Rob Bresnahan is being praised for his work on healthcare, including the appointment of a local leader to a national advisory committee."),
            ("Cognetti Flips NEPA Seat",
             "Cognetti's campaign asserts growing momentum to turn the district blue in 2026."),
            ("Healthcare Debate",
             "The candidates repeatedly debate healthcare issues, including Medicaid and access to affordable healthcare in NEPA."),
        ],
        "human_intuition": "mixed — healthcare + election dynamics (muddy case)",
    },
    {
        "id": 3,
        "frames": [
            ("Bresnahan Delivers District Funding",
             "Bresnahan promotes securing millions in federal funding for local projects across NEPA."),
            ("NEPA Support",
             "Cognetti emphasizes her support from teachers, nurses, and other locals in Northeastern Pennsylvania."),
        ],
        "human_intuition": "local support / NEPA",
    },
]


# ── Prompt variants ────────────────────────────────────────────────────────

PROMPT_A_MINIMAL = """Generate a short topic label (1-3 words) for this group of political campaign narratives:

{narratives}

Return ONLY the label, no explanation."""

PROMPT_B_EXAMPLES = """You are labeling clusters of political campaign narratives so a campaign manager can scan a topic map at a glance.

Below is a cluster of narratives that an AI grouped together. Give it a single short topic label (1-3 words) that captures what they have in common.

Examples of good labels: "Healthcare", "Stock Trades", "Local Economy", "Immigration", "Campaign Finance"
Examples of bad labels: "Politics", "Issues", "Campaign", "Voters"

Narratives:
{narratives}

Return ONLY the label, no explanation."""

PROMPT_C_NEGATIVES = """Label this cluster of political narratives with a 1-3 word topic.

Rules:
- Describe WHAT the narratives are about (the topic), not WHO is involved (the candidates).
- Specific over generic. "Medicaid Cuts" not "Healthcare". "Insider Trading" not "Ethics".
- Avoid these generic words entirely: Politics, Issues, Campaign, Voters, Policy, Government.

Narratives:
{narratives}

Return ONLY the label, no explanation, no quotes."""

PROMPT_D_STRUCTURED = """You're helping a political campaign visualize narrative clusters on a topic map. The cluster below is what an AI grouped together based on topical similarity.

Task: produce a single short label (1-3 words) for this cluster.

Constraints:
- The label is the TOPIC, not the candidate or party. Strip away who-is-attacking-whom.
- Specific where possible: prefer "Stock Trading" over "Ethics"; prefer "Medicaid" over "Healthcare" if the cluster is specifically about Medicaid.
- Title Case.
- 1-3 words. No punctuation. No quotes.

Cluster:
{narratives}

Label:"""

# Few-shot with worked political-campaign examples. Tests whether showing
# the model 2-3 ideal cluster-to-label mappings improves consistency on
# the muddy case (region 2) and the hard case (region 0).
PROMPT_E_FEW_SHOT = """Label this cluster of political narratives with a short topic (1-3 words, Title Case, no quotes).

Example 1:
- Voter ID Restrictions: GOP pushes for stricter voter ID requirements at the polls
- Mail Ballot Drop-Boxes: Local officials debate removing drop-boxes ahead of midterms
- Polling Place Closures: Several precincts consolidated in minority neighborhoods
→ Voting Access

Example 2:
- Childcare Subsidy Cuts: State proposes ending universal pre-K funding
- Maternity Coverage Gaps: Hospital announces it will stop accepting Medicaid maternity patients
- Daycare Affordability: Average cost in district hits $1,800/month, up 22% YoY
→ Family Affordability

Example 3:
- Federal Bridge Funding: Senator secures $400M for I-83 rehabilitation
- Highway Maintenance Backlog: PennDOT report cites $5B in deferred repairs
- Amtrak Expansion: New rail connection between Scranton and NYC proposed
→ Infrastructure Investment

Now label this cluster:
{narratives}

Output ONLY the label."""

# Role-priming + domain context. Tests whether telling the model what
# kind of analyst it is improves topical specificity.
PROMPT_F_ROLE = """You are a political analyst building a narrative topic map for the PA-08 congressional race (Cognetti vs. Bresnahan). Each cluster below was grouped because the narratives discuss related issues.

Give this cluster a precise 1-3 word topic label. Title Case. No punctuation. No quotes.

Be SPECIFIC: prefer "Medicaid Cuts" over "Healthcare"; prefer "Insider Trading" over "Ethics"; prefer "ACA Subsidies" over "Insurance".

If the cluster genuinely mixes 2+ topics, pick the dominant one and lead with it (e.g. "Healthcare & Demographics" if healthcare is the larger theme).

Cluster:
{narratives}"""

# Concise-reasoning then label. Tests whether forcing the model to
# briefly reason (one sentence) before committing to a label yields
# better labels than a one-shot direct answer.
PROMPT_G_REASONING = """Below is a cluster of political narratives. Identify the topic in two steps.

Step 1: In ONE sentence, state what the narratives have in common (the through-line, not the candidates).
Step 2: On a new line starting with "LABEL:", give a 1-3 word topic label in Title Case.

Cluster:
{narratives}"""

# Extreme-specificity bias. Tests pushing the model toward the most
# concrete noun phrase available in the cluster, even at the cost of
# losing breadth.
PROMPT_H_SPECIFIC = """Pick the most SPECIFIC noun phrase that captures what this cluster of political narratives is about. 1-3 words, Title Case.

Rules:
- Look for concrete things mentioned across multiple narratives (a program, a policy, a behavior). That's your label.
- If you're tempted to use a broad word like "Healthcare", "Ethics", "Economy", "Politics" — push harder. What SPECIFICALLY? "Medicaid Cuts", "Insider Trading", "District Funding"?
- If you genuinely cannot find a specific through-line, only then use a broader label.

Cluster:
{narratives}

Return ONLY the label, no explanation."""

PROMPTS = {
    "A_minimal": PROMPT_A_MINIMAL,
    "B_examples": PROMPT_B_EXAMPLES,
    "C_negatives": PROMPT_C_NEGATIVES,
    "D_structured": PROMPT_D_STRUCTURED,
    "E_few_shot": PROMPT_E_FEW_SHOT,
    "F_role": PROMPT_F_ROLE,
    "G_reasoning": PROMPT_G_REASONING,
    "H_specific": PROMPT_H_SPECIFIC,
}


# ── Provider adapters ─────────────────────────────────────────────────────

@dataclass
class ModelResult:
    label: str
    latency_ms: int
    error: Optional[str] = None


def _extract_label(raw: str) -> str:
    """Strip wrappers from LLM output. Handles 'LABEL: X' (G_reasoning),
    trailing periods, surrounding quotes, leading bullet markers."""
    text = (raw or "").strip()
    # G_reasoning prompt asks the model to put the final label on a "LABEL:" line.
    if "LABEL:" in text:
        text = text.split("LABEL:", 1)[1].strip()
    # If multi-line, take the last non-empty line (some models put rationale above).
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
            # 80 tokens covers G_reasoning's brief sentence + label.
            # Other prompts only need ~10 but the larger budget is harmless.
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.3,
            )
            return ModelResult(
                label=_extract_label(resp.choices[0].message.content or ""),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return ModelResult(label="", latency_ms=int((time.time() - t0) * 1000), error=str(e))
    return call


def make_openai_compat_caller(model: str, api_key: str, base_url: str) -> Callable[[str], ModelResult]:
    """Caller for any OpenAI-API-compatible endpoint (Groq, Cerebras)."""
    import requests as _r

    def call(prompt: str) -> ModelResult:
        t0 = time.time()
        try:
            resp = _r.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 80,
                    "temperature": 0.3,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            return ModelResult(
                label=_extract_label(data["choices"][0]["message"]["content"] or ""),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return ModelResult(label="", latency_ms=int((time.time() - t0) * 1000), error=str(e))
    return call


def make_gemini_caller(model: str) -> Callable[[str], ModelResult]:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    client = genai.GenerativeModel(model)

    def call(prompt: str) -> ModelResult:
        t0 = time.time()
        try:
            resp = client.generate_content(
                prompt,
                generation_config={"max_output_tokens": 80, "temperature": 0.3},
            )
            return ModelResult(
                label=_extract_label(resp.text or ""),
                latency_ms=int((time.time() - t0) * 1000),
            )
        except Exception as e:
            return ModelResult(label="", latency_ms=int((time.time() - t0) * 1000), error=str(e))
    return call


# Each entry: (display_name, caller_factory_call)
# Ordered by expected quality (best → cheapest/fastest).
MODELS = [
    ("gpt-4o",                lambda: make_openai_caller("gpt-4o")),
    ("gpt-4o-mini",           lambda: make_openai_caller("gpt-4o-mini")),
    ("gemini-2.5-flash",      lambda: make_gemini_caller("gemini-2.5-flash")),
    ("groq-llama-3.3-70b",    lambda: make_openai_compat_caller(
        "llama-3.3-70b-versatile",
        os.environ.get("GROQ_API_KEY", ""),
        "https://api.groq.com/openai/v1",
    )),
    ("cerebras-qwen-3-235b",  lambda: make_openai_compat_caller(
        "qwen-3-235b-a22b-instruct-2507",
        os.environ.get("CEREBRAS_API_KEY", ""),
        "https://api.cerebras.ai/v1",
    )),
]


# ── Bake-off runner ───────────────────────────────────────────────────────

def format_narratives(frames: list[tuple[str, str]]) -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in frames)


def main():
    # Initialize callers (catches missing keys early).
    callers: dict[str, Callable[[str], ModelResult]] = {}
    for name, factory in MODELS:
        try:
            callers[name] = factory()
        except Exception as e:
            print(f"!! skipping {name}: {e}", file=sys.stderr)

    # Run the matrix.
    # results[region_id][prompt_name][model_name] = ModelResult
    results: dict[int, dict[str, dict[str, ModelResult]]] = {}

    for region in REAL_REGIONS:
        rid = region["id"]
        results[rid] = {}
        narrative_block = format_narratives(region["frames"])
        for prompt_name, template in PROMPTS.items():
            results[rid][prompt_name] = {}
            prompt = template.format(narratives=narrative_block)
            for model_name, caller in callers.items():
                print(f"  region {rid} | prompt {prompt_name} | model {model_name}…", flush=True)
                results[rid][prompt_name][model_name] = caller(prompt)

    # ── Print results ──────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("BAKE-OFF RESULTS")
    print("=" * 80)

    for region in REAL_REGIONS:
        rid = region["id"]
        print(f"\n## REGION {rid}: human intuition = {region['human_intuition']}")
        print("Frames:")
        for name, _ in region["frames"]:
            print(f"  - {name}")
        print()
        # Table: rows = prompts, columns = models
        col_w = 24
        header = "Prompt".ljust(14) + "".join(m[:col_w-1].ljust(col_w) for m in callers)
        print(header)
        print("-" * len(header))
        for prompt_name in PROMPTS:
            row_cells = [prompt_name.ljust(14)]
            for model_name in callers:
                r = results[rid][prompt_name][model_name]
                cell = (r.label or f"ERR: {r.error[:18]}").strip()
                latency = f" ({r.latency_ms}ms)"
                row_cells.append((cell + latency)[:col_w-1].ljust(col_w))
            print("".join(row_cells))

    # Cost / latency summary
    print("\n## Latency per model (median across all calls)")
    for model_name in callers:
        lats = []
        for rid in results:
            for pn in PROMPTS:
                r = results[rid][pn][model_name]
                if r.label:
                    lats.append(r.latency_ms)
        if lats:
            lats.sort()
            median = lats[len(lats)//2]
            print(f"  {model_name:<28} median {median}ms  (n={len(lats)})")
        else:
            print(f"  {model_name:<28} no successful calls")

    print("\nDone. Inspect results above. Pick the (prompt, model) that gives the most")
    print("specific, non-generic labels — especially for region 2 (the muddy case).")


if __name__ == "__main__":
    main()
