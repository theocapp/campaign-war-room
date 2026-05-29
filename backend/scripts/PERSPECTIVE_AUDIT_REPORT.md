# Perspective Classification Audit — Final Report

**Run date:** 2026-05-25
**Scope:** All 2,350 race-relevant articles classified by the perspective classifier
**Method:** Two independent LLM passes (verifier + judge) over every classification

---

## TL;DR

| Metric | Value |
|---|---|
| Articles audited | **2,350** |
| Classifier ↔ verifier agreement | **56.9%** (1,337) |
| Classifier ↔ verifier disagreement | **43.1%** (1,013) |
| Classifier correct after judge adjudication | **~71.4%** (1,678 / 2,350) |
| Judge says classifier was wrong | **~28.6%** (672 / 2,350) |

The headline number is roughly **71% accuracy**, but the more important finding is that **78.8% of all disagreements should have been "neutral"** — meaning the classifier is consistently *over-confident* about assigning a side to articles that are about national politics, other races, or partisan figures with no clear PA-08 angle.

---

## Where errors come from

### By classifier method

| Method | Volume | Agreement w/ verifier | Judge-confirmed error rate |
|---|---:|---:|---:|
| `existing` (label lookup) | 70 | 68.6% | 24.3% |
| `llm` (gpt-4o-mini v8 prompt) | 1,909 | 59.8% | 23.7% |
| `attribution` (regex name+verb) | 222 | 56.3% | 36.9% |
| `outlet_bias` (Fox=R, Alternet=L, ...) | 149 | 15.4% | **77.9%** |

**Outlet bias is the worst offender by far.** It fires on articles from partisan outlets even when the article is about national politics with no candidate mention. Recommend either deprecating it entirely or gating it: only apply if the article *also* names a candidate or PA-08.

### By error type (judge's verdict on the 1,013 disagreements)

| Error type | Count | What it means |
|---|---:|---|
| `both_reasonable_neutral_better` | 530 | Both classifiers picked a side, but the article is genuinely off-topic / mixed |
| `b_wrong` | 269 | Classifier was right, verifier was wrong (verifier's "any visibility favors opponent" rule misfired) |
| `a_wrong` | 142 | Classifier was wrong, verifier was right (real classifier misclassification) |
| `out_of_race` | 67 | Article isn't about PA-08 at all — should arguably be archived |
| `both_wrong` | 4 | Both wrong, judge picked a different label |
| `b_label_inversion` | 1 | Verifier's reasoning was right but label was opposite |

---

## The dominant failure mode

**The classifier and verifier are both too aggressive about turning national-politics signals into a Cognetti-or-Bresnahan label.**

Examples from the audit (judge resolved all to `neutral`):

- *"13 House Republicans join Democrats to advance bill reversing Trump's union crackdown"* — about a national vote, doesn't mention Bresnahan voting either way.
- *"What to watch in Tuesday's elections: Trump loyalty tests, midterm House battlegrounds"* — Trump's GOP influence, no PA-08 angle.
- *"James Comer gets more than he can handle from Newsmax host"* — Kentucky congressman, irrelevant to PA-08.
- *"Cannes: Spanish Director Pedro Almodovar Declares 'The U.S. Is Not a Democracy'"* — completely off-topic.
- *"Trump's speech on combating inflation turns to grievances about immigrants"* — Trump speech, not PA-08.

The classifier was right that these articles are "vibes" pieces that favor one party's framing in the abstract — but they don't move the dial in a head-to-head Cognetti vs Bresnahan race because they don't mention either candidate or any PA-08-specific issue.

---

## Real classifier errors (judge says verifier was right) — 142 cases

These are *substantive* misclassifications worth fixing. Patterns:

1. **Attribution misfires on adversarial coverage of the attributed party.** Example: *"NEPA residents deliver petition to Rep. Bresnahan's office over Medicaid"* — attribution rule fired on "Bresnahan's office" and tagged it pro_opponent, but the article *attacks* him.
2. **Outlet bias on partisan outlets covering their own side's scandals.** Example: *"Rob Bresnahan puts more guardrails on stock trades after scrutiny"* on washingtonexaminer.com — tagged pro_opponent because right-leaning outlet, but the article is *about* the scandal scrutiny → favors Cognetti.
3. **LLM mis-reads mayoral-violence coverage.** Example: *"Stark numbers — Scranton Mayor Cognetti discusses uptick in violence"* — classifier saw "Cognetti discusses" and called it favorable, but it's negative coverage of her city.

---

## Recommended fixes (in priority order)

### 1. Deprecate or gate `outlet_bias` (saves ~100 errors)
22% accuracy. Either:
- **Gate it:** only apply if the article *also* mentions a candidate (Cognetti, Bresnahan, or PA-08).
- **Use it as a tie-breaker only:** if LLM is unsure, fall back to outlet lean.

### 2. Tighten the LLM prompt's "neutral" definition (saves ~530 errors)
The v8 prompt is too eager to call partisan-figure coverage as favoring whichever side. Add an explicit gate:

> "If neither CANDIDATE_A nor CANDIDATE_B is named or directly affected, and the article is about a different race or a national political figure (Trump, Pelosi, etc.) without a PA-08 implication, output `neutral`."

### 3. Filter out-of-race articles upstream (saves ~67 errors)
67 articles in the audit are about other races entirely (Florida representatives, Iowa candidates, Kentucky congressman). These slipped past the race-relevance scorer with score ≥ 50 but shouldn't have. Consider raising the relevance threshold for perspective classification to 70+, or adding a "candidate-or-district named?" check.

### 4. Fix attribution-rule polarity confusion (saves ~50 errors)
The attribution rule maps `"Bresnahan's office"` → pro_opponent unconditionally. Need to *also* check article framing — if the article attacks the attributed party, flip.

---

## Files generated by this audit

- `scripts/perspective_verification.csv` — every classification + verifier verdict + reasoning (2,350 rows)
- `scripts/perspective_judged.csv` — judge adjudication of the 1,013 disagreements (1,013 rows)

Both are sortable in any spreadsheet. To find real classifier bugs, filter `perspective_judged.csv` where `judge_error_type = a_wrong`. To find articles that should be filtered out entirely, filter where `judge_error_type = out_of_race`.

---

## Important caveat about audit confidence

Both the verifier and judge are LLM passes themselves, and both occasionally show the same "label inversion" bug — their stated reasoning is correct, but the label they assign is the opposite direction. We caught ~5% of this in spot checks. The 71% accuracy figure should be read as "**roughly 71%, give or take 5 points**", not as a precise ground-truth measurement.

For genuinely-precise accuracy, a human ground-truth set of 100-200 articles would be needed. The current audit's value is **pattern detection** — it surfaces *systematic* failure modes (over-asserting on off-topic articles, outlet_bias being broken) that can be fixed without needing the exact accuracy number.
