"""Empirical analysis of the article corpus to validate entity-schema design.

Three questions to answer:
  1. ENTITY TYPES — what kinds of entities actually appear in articles?
     Are 5 types enough or should we add Issue / Quote / etc.?
  2. RELATIONSHIP VERBS — what action patterns are most common?
     Are the 8 proposed verbs sufficient?
  3. SEED LIST — are the top-mentioned canonical entities all in the seed?
     What's missing?

We do this WITHOUT new LLM calls — using the existing scored corpus
(narrative_frames, source_items.summary, extracted_text where available).
"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import NarrativeFrame, SourceItem


# ── 1. Entity type heuristics ─────────────────────────────────────────────
# Manually curated regex patterns for common entity surface forms in
# political articles. We use these as a sampler — NOT to define what the
# real extractor catches, but to estimate type prevalence in the corpus.

PERSON_PATTERNS = [
    r"\bRep\.\s+[A-Z]\w+",            # "Rep. Bresnahan"
    r"\bSen\.\s+[A-Z]\w+",            # "Sen. Casey"
    r"\bGov\.\s+[A-Z]\w+",            # "Gov. Shapiro"
    r"\bMayor\s+[A-Z]\w+",            # "Mayor Cognetti"
    r"\bPresident\s+[A-Z]\w+",
    r"\bSpeaker\s+[A-Z]\w+",
    r"\b[A-Z][a-z]+\s+[A-Z][a-z]+",   # "Paige Cognetti" — generic first+last
]

ORG_KEYWORDS = [
    "NRCC", "DCCC", "PAC", "Committee", "Foundation", "Caucus", "Union",
    "AFL-CIO", "EMILYs List", "EMILY's List", "Heritage", "Club for Growth",
    "Times-Tribune", "Fox News", "NBC", "CNN", "Reuters", "AP", "MSNBC",
    "Republican Party", "Democratic Party", "Freedom Caucus", "ACLU",
    "Chamber of Commerce", "AARP", "NRA", "Planned Parenthood",
]

BILL_PATTERNS = [
    r"\b(?:bill|act|legislation)\b",
    r"\b(?:H\.?R\.?|S\.)\s*\d+",      # "H.R. 1234"
    r"voted (?:for|against|yes|no)",
    r"co-?sponsored",
    r"\bACA\b", r"\bMedicaid\b", r"\bMedicare\b", r"\bSocial Security\b",
    r"\btax cut", r"\btariff",
]

EVENT_KEYWORDS = [
    "rally", "fundraiser", "town hall", "debate", "press conference",
    "campaign launch", "speech", "convention", "vote on", "hearing",
    "ceremony", "gala", "visit", "tour",
]

LOCATION_KEYWORDS = [
    "Scranton", "Wilkes-Barre", "Hazleton", "Pittston", "Carbondale",
    "Lackawanna County", "Luzerne County", "Monroe County", "Wayne County",
    "Pike County", "Pennsylvania", "PA-08", "NEPA", "Pocono",
]

# Things NOT in our 5-type schema. Are these common enough to need their own type?
ISSUE_KEYWORDS = [
    "healthcare", "health care", "Medicaid", "ACA", "Affordable Care Act",
    "abortion", "reproductive", "Roe v. Wade",
    "immigration", "border", "ICE", "deportation",
    "economy", "inflation", "wages", "jobs",
    "education", "school", "student loan",
    "gun control", "Second Amendment", "firearms",
    "climate", "energy", "fossil fuel",
    "tax", "tariff", "trade",
    "crime", "public safety", "fentanyl", "police",
]

QUOTE_PATTERNS = [
    r'"[^"]{30,500}"',  # any quoted span of reasonable length
    r"['‘][^'’]{30,500}['’]",
]


def score_corpus():
    db = SessionLocal()
    print("=" * 70)
    print("ENTITY SCHEMA ANALYSIS — corpus-driven design check")
    print("=" * 70)

    # Sample race-relevant articles (top 500 by relevance score)
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(500)
        .all()
    )
    print(f"\nAnalyzing {len(items)} top race-relevant articles...")

    # ── Type prevalence ─────────────────────────────────────────────────
    type_hits: Counter = Counter()
    issue_hits: Counter = Counter()
    quote_count = 0

    for it in items:
        blob = " ".join([it.title or "", it.summary or "", (it.raw_text or "")[:1000]])

        # People
        for pat in PERSON_PATTERNS:
            for _ in re.finditer(pat, blob):
                type_hits["person"] += 1
                break  # 1 per article — we want frequency, not raw count

        # Orgs
        if any(k.lower() in blob.lower() for k in ORG_KEYWORDS):
            type_hits["organization"] += 1

        # Bills
        if any(re.search(p, blob, re.IGNORECASE) for p in BILL_PATTERNS):
            type_hits["bill"] += 1

        # Events
        if any(k in blob.lower() for k in EVENT_KEYWORDS):
            type_hits["event"] += 1

        # Locations
        if any(k in blob for k in LOCATION_KEYWORDS):
            type_hits["location"] += 1

        # Issues — special, not currently in schema
        for issue in ISSUE_KEYWORDS:
            if issue.lower() in blob.lower():
                issue_hits[issue] += 1

        # Quotes
        for pat in QUOTE_PATTERNS:
            if re.search(pat, blob):
                quote_count += 1
                break

    print(f"\n── 1. ENTITY TYPE PREVALENCE (% of {len(items)} articles) ──")
    for t, count in type_hits.most_common():
        pct = 100 * count / len(items)
        print(f"   {t:14s} {pct:5.1f}%  ({count:>4d} articles)")

    print(f"\n── Quote prevalence ──")
    qpct = 100 * quote_count / len(items)
    print(f"   quote in article: {qpct:5.1f}%  ({quote_count}/{len(items)})")
    print(f"   → Quotes are very common but NOT a separate entity type. They'd")
    print(f"     belong as evidence text on relations.")

    print(f"\n── Top issue keywords ──")
    for issue, count in issue_hits.most_common(15):
        pct = 100 * count / len(items)
        print(f"   {issue:25s} {pct:5.1f}%  ({count:>4d})")

    issue_articles_min = sum(1 for issue, c in issue_hits.most_common(5) if c >= len(items) * 0.05)
    print(f"\n   {issue_articles_min}/5 top issues appear in >5% of articles → broadly relevant")
    print(f"   → Strong case for Issue as a 6th entity type IF we can extract reliably.")

    # ── 2. Relationship verb prevalence ─────────────────────────────────
    print(f"\n── 2. RELATIONSHIP VERB PREVALENCE ──")
    VERB_PATTERNS = {
        "endorses": [r"endors\w+", r"backed", r"supports", r"throws (?:his|her|their) support"],
        "attacks": [r"attack\w+", r"slam\w+", r"blast\w+", r"hit\w+ back", r"accus\w+"],
        "criticizes": [r"criticiz\w+", r"slam\w+", r"rebuk\w+", r"deride\w+"],
        "voted_for": [r"voted (?:for|yes|to (?:pass|approve|support))"],
        "voted_against": [r"voted (?:against|no|to (?:reject|block|oppose))"],
        "co_sponsored": [r"co-?sponsor\w+", r"introduced"],
        "represents": [r"represents", r"district of", r"representing"],
        "member_of": [r"chair of", r"chairman of", r"member of", r"sits on"],
        "attended": [r"attended", r"appeared at", r"spoke at", r"keynote", r"rallied"],
        "donated_to": [r"donated to", r"contributed to", r"fundraised for", r"max-?ed out"],
        "raised_money": [r"raised \$", r"fundraising haul", r"campaign cash"],
        "polled": [r"polled at", r"trailing in polls", r"leading by"],
        "withdrew": [r"withdrew", r"dropped out", r"suspended (?:his|her|their) campaign"],
        "indicted": [r"indicted", r"charged with", r"investigation"],
        "predecessor_of": [r"predecessor", r"replaced", r"former (?:Rep|Sen|congress)"],
    }

    verb_hits: Counter = Counter()
    for it in items:
        blob = " ".join([it.title or "", it.summary or "", (it.raw_text or "")[:1500]]).lower()
        for verb, pats in VERB_PATTERNS.items():
            if any(re.search(p, blob) for p in pats):
                verb_hits[verb] += 1

    print(f"   {'verb':22s} {'% articles':>10s}  proposed in schema?")
    proposed = {"endorses", "attacks", "voted_for", "voted_against",
                "represents", "member_of", "attended", "donated_to"}
    for verb, count in verb_hits.most_common():
        pct = 100 * count / len(items)
        in_schema = "✓" if verb in proposed else "✗"
        print(f"   {verb:22s} {pct:5.1f}%      {in_schema}")

    # ── 3. Seed-list coverage ────────────────────────────────────────────
    print(f"\n── 3. SEED-LIST COVERAGE CHECK ──")
    # Load the seed list
    seed_path = Path(__file__).resolve().parent.parent / "data" / "canonical_entities.PA-08.json"
    import json
    with seed_path.open() as f:
        seed_data = json.load(f)
    seed_names = set()
    for e in seed_data["entities"]:
        seed_names.add(e["name"])
        for a in e.get("aliases", []):
            seed_names.add(a)

    # Top mentioned names in the corpus — naive: bag-of-bigrams that match
    # the "Capitalized Word Capitalized Word" pattern, filtered to known
    # names heuristics.
    name_mentions: Counter = Counter()
    name_re = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b")
    for it in items:
        blob = " ".join([it.title or "", it.summary or ""])
        for m in name_re.finditer(blob):
            name = m.group(1)
            # Skip obvious noise — single common phrases
            if name in ("United States", "New York", "Los Angeles", "Washington Post",
                        "United Kingdom", "South Korea", "North Korea",
                        "Supreme Court", "House Republicans", "House Democrats"):
                continue
            name_mentions[name] += 1

    print(f"   Top 30 capitalized phrases in articles:")
    print(f"   {'phrase':35s}  {'count':>6s}  {'in seed?':>9s}")
    missing_top = []
    for name, count in name_mentions.most_common(30):
        in_seed = name in seed_names
        marker = "✓" if in_seed else "✗ MISSING"
        print(f"   {name:35s}  {count:>6d}  {marker:>9s}")
        if not in_seed and count >= 20:
            missing_top.append((name, count))

    print()
    if missing_top:
        print(f"   ⚠ {len(missing_top)} HIGH-FREQUENCY entities NOT in seed list:")
        for name, count in missing_top:
            print(f"      - {name}  ({count} mentions)")
        print(f"   → These would create lots of duplicates during extraction.")
        print(f"     Recommend adding them to the seed list before backfill.")
    else:
        print(f"   All top-30 high-frequency names ARE in seed list. ✓")

    db.close()
    return {
        "type_hits": dict(type_hits),
        "verb_hits": dict(verb_hits),
        "issue_hits": dict(issue_hits.most_common(20)),
        "missing_seed": missing_top,
    }


if __name__ == "__main__":
    result = score_corpus()
    # Also dump to JSON for the summary
    out = Path(__file__).resolve().parent / "entity_schema_analysis.json"
    import json
    with out.open("w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n→ Full analysis saved to {out}")
