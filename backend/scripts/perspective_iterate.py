"""Iteratively refine the perspective classification prompt.

Each iteration:
  1. Run a candidate prompt against a fixed seeded sample.
  2. Persist classifications keyed by (iter_id, item_id).
  3. Print diffs vs the previous iteration so we can spot regressions
     and improvements.

Saves all iteration results to scripts/perspective_iterations.json so we
can review the full history.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_iterate.py [N] [iter_id]
"""
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.article_perspective import get_classifier
from app.services.llm_provider import OpenAIProvider, _parse_json_response


# ── Candidate prompts to iterate on ────────────────────────────────────────

PROMPTS: dict[str, str] = {}

PROMPTS["v1_baseline"] = """You classify political news articles for perspective in a head-to-head race.

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


PROMPTS["v2_party_framing"] = """You classify political news articles for perspective in a head-to-head race.

You'll be told the two candidates (CANDIDATE_A from Party_A vs CANDIDATE_B from Party_B). Decide which campaign would WANT to spread this article.

CRITICAL RULES:

A. "neutral" is ONLY for genuinely off-topic content — bridge construction, weather, school events, sports, accidents, unrelated national news that doesn't reflect on the race at all.

B. Every politically-relevant article picks a side. Use these rules in order:

  1. SCANDAL / NEGATIVE COVERAGE rule:
     Coverage of a scandal, controversy, or critique of CANDIDATE_A → favors CANDIDATE_B.
     The mere fact that a candidate is in the news for negative reasons benefits their opponent.

  2. POSITIVE COVERAGE rule:
     Coverage of an endorsement, achievement, or positive announcement about CANDIDATE_A → favors CANDIDATE_A.
     Press releases, official statements, and promotional content favor the speaker's side.

  3. PARTY FRAMING rule:
     National story that favorably frames a PARTY (e.g. "Democrats' big tent coalition", "GOP unity on issue X") →
     favors the CANDIDATE FROM THAT PARTY.
     A critique of one party → favors the candidate from the OTHER party.
     This applies even when neither named candidate is in the article.

  4. OPPOSITION ATTACK VECTOR rule:
     If a candidate is mentioned WITHIN a topic that originated as an OPPOSITION ATTACK on them
     (e.g. Bresnahan in the context of "stock trading", Cognetti in the context of "carpetbagger"),
     even articles where they DEFEND themselves or shift their behavior STILL favor the OPPOSING campaign,
     because the topic itself is the attack vector.

  5. TIE-BREAKER:
     If both candidates are featured roughly equally, pick the one whose FRAMING WINS in the headline/lead.
     The candidate whose actions are being DESCRIBED (vs the candidate doing the describing) is the SUBJECT;
     coverage about CANDIDATE_X usually favors CANDIDATE_Y.

C. Off-topic detection: if the article doesn't mention either candidate, their party, the race, the district,
   or a topic that's a known attack vector — and it's not a partisan-coded national story — return "neutral".

EXAMPLES:

  "Cognetti's mayoral budget passes 7-0" → favors Cognetti (positive coverage of her work)
  "Bresnahan votes for healthcare cuts" → favors Cognetti (Bresnahan in negative-framing topic)
  "Bresnahan stops stock trading after disclosures" → favors Cognetti (still in the stock-trade attack vector)
  "Cognetti criticized for missing 5 council meetings" → favors Bresnahan (scandal coverage about Cognetti)
  "Walz says Democrats have huge tent: 'Sanders and Cheney are buddies'" → favors Cognetti (Dem self-promotion)
  "Republicans rally around tax cut bill" → favors Bresnahan (GOP framing wins)
  "Trump tests message at PA rally" → favors Cognetti (Trump-skeptical framing benefits Dem)
  "Bresnahan introduces bipartisan farming bill" → favors Bresnahan (positive Bresnahan coverage)
  "Bridge replacement causes detours in central PA" → neutral (genuinely off-topic)
  "Tree planting at Valley Middle School" → neutral (genuinely off-topic)

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence>"}
"""


PROMPTS["v8_chain_of_thought"] = """Classify which campaign benefits from this article being in the press.

You're given CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Output the favored candidate's name.

REASONING PROCESS — think step-by-step in your reasoning, then output the verdict:

STEP 1: Who is the article's subject?
  - One candidate by name? Both candidates? A partisan figure (Trump/Pelosi/etc.)?
    A topic only? Off-topic entirely?

STEP 2: What's the framing?
  - Negative / critical / accusatory of a named candidate → favors the OPPOSITE candidate.
  - Positive / endorsing / promotional of a named candidate → favors THAT candidate
    (UNLESS the topic is a known attack vector — see step 3).
  - Symmetric / mixed coverage of both → use first-named in title.
  - Pure partisan framing without either candidate as subject → see step 4.
  - No political content → neutral.

STEP 3: Is the topic a known attack vector? (OVERRIDES STEP 2's positive framing)
  PA-08 attack vectors (when these come up, the OPPOSING side benefits regardless of
  how the candidate is framed):
    Bresnahan + (stock trading | trades | ethics | helicopter | corruption | insider trading)
      → favors COGNETTI (the side that raised the issue benefits from visibility)
    Cognetti + (carpetbagger | dual campaigns | running for two offices | abandoning Scranton |
                  maternity leave inconsistency)
      → favors BRESNAHAN

STEP 4: Partisan-figure framing (only when neither candidate is the subject):
  Democratic figure / framing wins → favors COGNETTI (Dem candidate)
  Republican figure / framing wins → favors BRESNAHAN (Rep candidate)
  Critique of Dems → favors BRESNAHAN
  Critique of GOP → favors COGNETTI
  GOP defectors backing Dem position → favors COGNETTI (Dem framing wins)

STEP 5: Verify the output:
  - If your reasoning says "criticized" or "attack vector against Bresnahan" → output Cognetti
  - If your reasoning says "criticized" or "attack vector against Cognetti" → output Bresnahan
  - If your reasoning says "positive coverage of Bresnahan" and NOT an attack vector → output Bresnahan
  - If your reasoning says "positive coverage of Cognetti" and NOT an attack vector → output Cognetti

WORKED EXAMPLES:

  "Letter: Bresnahan voted to enable ICE"
    STEP 1: Bresnahan is subject. STEP 2: critical → favors opposite = COGNETTI.
    Output: Cognetti.

  "Bresnahan welcomes Dr. Oz to Scranton"
    STEP 1: Bresnahan is subject. STEP 2: positive (his event). STEP 3: not an attack vector
    (Dr. Oz isn't a known attack vector against Bresnahan). → favors Bresnahan.
    Output: Bresnahan.

  "Bresnahan signs discharge petition to ban congressional stock trading"
    STEP 1: Bresnahan is subject. STEP 2: positive framing (reform). STEP 3: stock trading IS
    an attack vector against Bresnahan → favors Cognetti regardless of positive framing.
    Output: Cognetti.

  "Walz says Democrats' coalition is huge"
    STEP 1: Walz is subject; neither candidate. STEP 4: Dem self-promo → favors Cognetti.
    Output: Cognetti.

  "Four Republicans join Democrats to force healthcare vote"
    STEP 1: GOP defectors as subject; neither named candidate is the subject.
    STEP 4: Dem framing wins (defectors validating Dem position) → favors Cognetti.
    Output: Cognetti.

  "Cognetti, Bresnahan trade barbs as PA-08 heats up"
    STEP 1: Both candidates, symmetric. STEP 2: mixed coverage → first-name tie-break.
    Cognetti appears first → favors Cognetti.
    Output: Cognetti.

  "Bridge replacement in central Pa."
    STEP 1: off-topic. → neutral.
    Output: neutral.

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence summarizing the steps that decided it>"}
"""


PROMPTS["v7_merged"] = """Classify which campaign benefits from this article being in the press.

You're given CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Output the favored candidate's name.

DECISION RULES — apply in order, first match wins:

RULE 1 — Off-topic:
  Article doesn't mention either candidate by name, doesn't reference the race or district,
  doesn't quote a partisan figure on a substantive issue, and isn't about a topic associated
  with either candidate.
  → "neutral".
  STILL OFF-TOPIC: bridge construction, weather, unrelated crime, sports, school events.
  NOT OFF-TOPIC (always classify): article naming Cognetti or Bresnahan, op-eds about
  either, partisan figure (Trump/Pelosi/Shapiro/Walz/etc.) quoted on issues.

RULE 2 — A candidate is criticized:
  Article criticizes, scrutinizes, accuses, or carries opposition research against ONE candidate.
  → favors THE OPPOSITE candidate.
  Examples:
    "Letter: Bresnahan voted to enable ICE" → Cognetti
    "Cognetti owes public explanation on bank issue" → Bresnahan
    "Bresnahan's stock trading raises ethics questions" → Cognetti
    "Letters: Can Bresnahan relate to constituents?" → Cognetti
    "Bessent calls for stock trade ban, highlights Bresnahan's trading" → Cognetti

RULE 3 — A candidate gets positive coverage (and the topic is NOT a known attack vector):
  Article highlights an endorsement, achievement, official event, or constructive activity
  by ONE candidate on a topic that's NOT a known attack vector against them.
  → favors THAT CANDIDATE.
  Examples:
    "Bresnahan endorsed by Fraternal Order of Police" → Bresnahan
    "Bresnahan welcomes Dr. Oz to Scranton" → Bresnahan
    "Bresnahan introduces bipartisan farming bill" → Bresnahan
    "Bresnahan operates drone at Tobyhanna Depot" → Bresnahan
    "Bresnahan's Greenlight Project donates grants" → Bresnahan
    "Rep. Bresnahan speaks at Transportation hearing" → Bresnahan
    "Cognetti endorsed by Pennsylvania Nurses Association" → Cognetti
    "Cognetti launches campaign for House" → Cognetti
    "Cognetti named in CQ-Roll Call most-watched-Dem list" → Cognetti
    "'Paige against the machine' profile of Cognetti" → Cognetti

RULE 4 — Attack-vector topic (this rule OVERRIDES Rule 3 — apply Rule 4 first):
  The candidate is mentioned in a topic that ORIGINATED as opposition research against them,
  EVEN IF the article shows them reforming, defending, or doing something positive on that topic.
  The visibility of the attack vector is the campaign asset for the side that raised it.

  Known PA-08 attack vectors:
    Bresnahan + (stock trading | trades | congressional stock | insider trading | ethics
                  allegation | helicopter | corruption) → ALWAYS favors COGNETTI
    Cognetti + (carpetbagger | dual campaigns | running for two offices | abandoning Scranton |
                  maternity leave inconsistency) → ALWAYS favors BRESNAHAN

  Critical examples:
    "Bresnahan stops frequent stock trading" → Cognetti (vector still active)
    "Bresnahan signs discharge petition to ban congressional stock trading" → Cognetti
       (reforming WITHIN the attack vector still favors the side that raised it)
    "Bresnahan disputes ethics allegation" → Cognetti
    "Cognetti defends her dual-campaign decision" → Bresnahan

RULE 5 — Partisan-figure framing (only if neither candidate is the article's subject):
  Article advances a partisan framing through a national figure (Trump, Pelosi, Schumer,
  McConnell, Shapiro, Walz, Crow, Massie, etc.) WITHOUT focusing on Cognetti or Bresnahan.
    Pro-Democratic framing → Cognetti (Dem candidate)
    Critique of Democrats → Bresnahan
    Pro-Republican framing → Bresnahan
    Critique of Republicans → Cognetti
    "Republicans break with party to back Dem position" → Cognetti
       (defectors validate Dem framing — even though they're Republicans)
  Examples:
    "Walz says Democrats' coalition is huge" → Cognetti
    "Crow: Massie loss shows GOP no diversity" → Cognetti
    "Shapiro tests clout flipping Dem seats" → Cognetti
    "Four Republicans break party lines, force Dem-favored healthcare vote" → Cognetti
       (Dem framing wins because GOP cracked)
    "Trump hits road on affordability message" → check article framing:
       - Article positive about Trump's economic message → Bresnahan
       - Article skeptical / notes poor polling → Cognetti

RULE 6 — Both candidates in title with no clear framing winner:
  "Cognetti, Bresnahan trade barbs" / "Cognetti vs Bresnahan" / "PA-08: Cognetti, Bresnahan ..."
  Pick whichever name appears FIRST IN THE TITLE (literally — read the title left-to-right and
  find the first candidate surname).
  Examples:
    "Cognetti, Bresnahan trade barbs as PA-08 heats up" → Cognetti (first name)
    "'Game on!' in 8th: Cognetti, Bresnahan contest off to wild start" → Cognetti
    "Bresnahan, Cognetti tied in latest poll" → Bresnahan (first name)
    "#PA-08 Robert Bresnahan (R): Won by 1.6 points" → Bresnahan
       (Bresnahan is the only candidate in the title; treat as positive coverage of him
        UNLESS the framing of the stat is negative/critical)

OUTPUT RULES:
  RULE 2 / RULE 4: favored = OPPOSITE of the criticized / attack-vector candidate
  RULE 3: favored = SAME as the positively-framed candidate
  RULE 5: favored = the candidate whose party's framing wins
  RULE 6: favored = first candidate surname in the title

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<cite the rule; one short sentence>"}
"""


PROMPTS["v6_explicit_subjects"] = """Classify which campaign benefits from this article being in the press.

You're given CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Output the favored candidate's name.

DECISION RULES — first match wins, STOP at first match:

RULE 1 — Off-topic:
  Article doesn't mention either candidate by name, doesn't reference the race or district,
  doesn't quote a partisan figure, and isn't about a topic associated with either candidate.
  → "neutral".
  Examples that ARE off-topic: bridge construction, weather, unrelated local crime, sports,
    school events, accidents, unrelated business announcements.
  Examples that ARE NOT off-topic (always classify these):
    - Any article mentioning Cognetti or Bresnahan in the title or lead
    - Any article about PA-08 or the district's primary/general
    - Any article quoting a national partisan figure on a substantive issue
    - Op-eds / letters about a candidate

RULE 2 — Candidate framed NEGATIVELY:
  The article criticizes, scrutinizes, accuses, or carries opposition research against ONE candidate.
  → favors THE OPPOSITE candidate.
  Examples:
    "Letter: Bresnahan voted to enable ICE" → Cognetti
    "Letter: Cognetti's rhetoric doesn't match her record" → Bresnahan
    "Cognetti owes public explanation on bank issue" → Bresnahan
    "Bresnahan's stock trading raises ethics questions" → Cognetti
    "Letters: Can Bresnahan relate to constituents?" → Cognetti
    "Bessent rips Pelosi, highlights Bresnahan's trading" → Cognetti (Bresnahan in negative spotlight)

RULE 3 — Candidate framed POSITIVELY (PURE positive, NOT in an attack-vector topic):
  The article highlights an endorsement, achievement, event, or constructive activity by ONE candidate
  on a topic that's NOT a known attack vector against them.
  → favors THAT CANDIDATE.
  Examples:
    "Cognetti endorsed by Pennsylvania Nurses Association" → Cognetti
    "Cognetti launches campaign for House" → Cognetti
    "Bresnahan endorsed by Fraternal Order of Police" → Bresnahan
    "Bresnahan welcomes Dr. Oz to Scranton" → Bresnahan
    "Bresnahan introduces bipartisan farming bill" → Bresnahan
    "Bresnahan operates drone at Tobyhanna Depot" → Bresnahan (his official-rep activity)
    "Bresnahan's Greenlight Project donates grants" → Bresnahan (positive constituent service)
    "Rep. Bresnahan speaks at Transportation hearing" → Bresnahan
    "Rep. Bresnahan provided recommendation letter" → Bresnahan
    "Cognetti named in CQ-Roll Call's most-watched-Dem-pickup list" → Cognetti

RULE 4 — Candidate active in AN ATTACK-VECTOR TOPIC (overrides RULE 3):
  KEY INSIGHT: if the topic ITSELF originated as opposition research, the OPPONENT benefits regardless
  of whether the candidate is being criticized, defending themselves, OR doing something positive
  on that topic. The mere visibility of the attack vector helps the side that raised it.

  Known PA-08 attack vectors:
    - Bresnahan + (stock trading | trades | congressional stock | insider trading) → ALWAYS favors COGNETTI
    - Bresnahan + (ethics allegation | helicopter | corruption | insider trading) → ALWAYS favors COGNETTI
    - Cognetti + (carpetbagger | dual campaigns | running for two offices | abandoning Scranton) → ALWAYS favors BRESNAHAN
    - Cognetti + (maternity leave inconsistency) → ALWAYS favors BRESNAHAN

  Examples (and these are CRITICAL — don't get them wrong):
    "Bresnahan stops frequent stock trading" → Cognetti (attack vector still active)
    "Bresnahan signs discharge petition to ban congressional stock trading" → Cognetti
        (he's REFORMING but the visibility of the topic still helps the side who raised it)
    "Bresnahan disputes ethics allegation" → Cognetti (the dispute keeps the topic alive)
    "Cognetti defends her dual-campaign decision" → Bresnahan

RULE 5 — Partisan-figure framing (only if neither named candidate is the article's subject):
  Article advances a partisan framing through a national figure (Trump, Pelosi, Schumer, McConnell,
  Shapiro, Walz, Crow, Massie, etc.) without focusing on Cognetti or Bresnahan.
    Pro-Democratic Party framing → Cognetti
    Critique of Democratic Party → Bresnahan
    Pro-Republican Party framing → Bresnahan
    Critique of Republican Party → Cognetti
  Examples:
    "Walz says Democrats' coalition is huge" → Cognetti
    "Shapiro flips Dem House seats" → Cognetti
    "Crow: Massie loss shows GOP no diversity" → Cognetti
    "GOP's solution to corruption" piece → Cognetti (it's a critique)
    "Trump signs executive order on X" → check framing:
       - Article positive about Trump → Bresnahan
       - Article skeptical of Trump → Cognetti
    "Republicans force vote on healthcare" → check whether it's framed as a win or a critique:
       - Win for GOP → Bresnahan
       - Critique of GOP → Cognetti
       - "Republicans break with party to support Dem position" → Cognetti
        (the GOP defectors are crossing to Dem framing)
    "Letter: 'Paige against the machine' — Democrat bucks party" → Cognetti
        (positive Cognetti profile, even if she's bucking her own party)

RULE 6 — Article about both candidates equally:
  "Cognetti, Bresnahan trade barbs" type. Look at WHICH side's framing wins. If genuinely
  even-handed (rare), pick whichever name appears FIRST in the headline.
  Examples:
    "Cognetti, Bresnahan trade barbs as PA-08 heats up" → Cognetti (first name)
    "'Game on!' in 8th: Cognetti, Bresnahan contest off to wild start" → Cognetti (first name)

CRITICAL OUTPUT NOTE:
  Each rule MUST resolve to a specific candidate name (not just "the opposite candidate").
  RULE 2: favored = the OPPOSITE of the criticized candidate
  RULE 3: favored = the SAME candidate as the positive subject
  RULE 4: favored = the OPPOSITE of the candidate in the attack-vector topic
  RULE 5: favored = the Democratic candidate when Dem framing wins; the Republican when GOP framing wins
  RULE 6: tie-break to first-named candidate

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<cite the rule number; one short sentence>"}
"""


PROMPTS["v5_blunt_attack_vector"] = """Classify which campaign benefits from this article being in the press.

You're given CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Output the favored candidate's name.

DECISION RULES — first match wins, STOP at first match:

RULE 1 — Off-topic:
  Article doesn't mention either candidate, their party, the district, or any race topic.
  → "neutral".
  Examples: "Bridge construction", "School tree planting", "Unrelated crime".

RULE 2 — One candidate is mentioned NEGATIVELY (criticism, scandal, complaint, controversy):
  → favors THE OTHER candidate.
  Examples:
    "Letter: Bresnahan voted to enable ICE" → Cognetti (Bresnahan critiqued)
    "Cognetti criticized for missing meetings" → Bresnahan (Cognetti critiqued)
    "Bresnahan disputes ethics allegation" → Cognetti (Bresnahan in negative news)
    "Bessent calls for stock trade ban, highlights Bresnahan's trading" → Cognetti
    "GOP's solution to corruption" piece about Bresnahan → Cognetti

RULE 3 — One candidate is mentioned POSITIVELY (achievement, endorsement, official statement):
  → favors THAT CANDIDATE.
  Examples:
    "Bresnahan welcomes Dr. Oz to Scranton" → Bresnahan (his event)
    "Bresnahan introduces bipartisan farming bill" → Bresnahan (legislative win)
    "Bresnahan led Cognetti in campaign cash" → Bresnahan (fundraising win)
    "Cognetti launches campaign for House" → Cognetti
    "Cognetti supported by nurses union" → Cognetti
    "IAFF conference featuring Bresnahan" → Bresnahan (his speaking slot)

RULE 4 — Attack-vector topic (overrides RULE 3 when the candidate is "doing the right thing" in their own scandal):
  If the article is about a candidate REFORMING / RESPONDING / DEFENDING themselves in a topic
  that ORIGINATED as opposition research, the OPPOSITION still benefits because the topic is in the news.
  Known PA-08 attack vectors:
    - Bresnahan + stock trading / trades / ethics / corruption → attack vector against Bresnahan → favors COGNETTI
    - Cognetti + carpetbagger / dual campaigns / abandoning Scranton → attack vector against Cognetti → favors BRESNAHAN
  Examples:
    "Bresnahan stopped frequent stock trades" → Cognetti (the topic is still active)
    "Cognetti defends running for two offices" → Bresnahan (the topic is still active)
    "Bresnahan's response to ethics allegation" → Cognetti

RULE 5 — Party-level coverage (only if neither candidate is the article's subject):
  Article advances a partisan framing without focusing on either named candidate.
    Positive about Democratic Party / its figures → Cognetti (Dem candidate).
    Critical of Democratic Party / its figures → Bresnahan.
    Positive about Republican Party / its figures → Bresnahan.
    Critical of Republican Party / its figures → Cognetti.
  Examples:
    "Walz says Democrats' coalition is huge" → Cognetti (Dem self-promo)
    "Crow: Massie loss shows GOP has no diversity" → Cognetti (Dem critiquing GOP)
    "Shapiro tests clout flipping seats for Dems" → Cognetti
    "Republicans rally around tax cuts" → Bresnahan (GOP coordinated framing)

RULE 6 — Truly mixed but politically relevant:
  Article mentions both candidates evenly, no clear winner in framing. Pick whichever side
  is being framed slightly more favorably; truly even-handed is rare. If you literally cannot
  tell, prefer the side mentioned first in the headline.

CRITICAL OUTPUT NOTE:
  - RULE 2 and RULE 4 always favor the OPPOSITE candidate from the one mentioned in negative context.
  - RULE 3 favors the SAME candidate mentioned in positive context.
  - Don't confuse these. If you cite RULE 2 or RULE 4 in your reason, your favored_candidate
    must be the OPPOSITE of the criticized candidate.

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<cite which rule; one sentence>"}
"""


PROMPTS["v4_step2_dominates"] = """You classify political news articles for perspective in a head-to-head race.

You'll be told CANDIDATE_A (Party_A) vs CANDIDATE_B (Party_B). Decide which campaign would WANT this article in their press clippings.

REASONING PROCESS (apply in order — FIRST MATCH WINS, do not continue to later steps):

STEP 1 — Off-topic check:
  Does the article mention EITHER candidate, OR their party, OR the district/race, OR a topic widely
  associated with the race? If NO → "neutral". Stop.

STEP 2 — Direct candidate coverage (HIGHEST PRIORITY when applicable):
  Is one or both candidates the article's SUBJECT (mentioned in title/lead, the article is ABOUT
  something they did/said/are accused of)?

  This step OVERRIDES any party-level framing in STEP 3. If a candidate is the article's focus,
  the article's perspective is determined by HOW that candidate is framed, REGARDLESS of broader
  party framing.

  Sub-rules:
    2a. Candidate framed NEGATIVELY (scandal, critique, opposition research, criticism, complaint):
        → favors the OPPONENT of the criticized candidate.
        Examples:
          "Bresnahan's stock trading raises ethics questions" → favors Cognetti
          "Letter: Cognetti should focus on crime" → favors Bresnahan
          "Bresnahan campaigned on stock trade ban; now trades freely" → favors Cognetti

    2b. Candidate framed POSITIVELY (achievement, endorsement, event, official statement, press release):
        → favors THE CANDIDATE.
        Examples:
          "Bresnahan welcomes Dr. Oz to Scranton" → favors Bresnahan (his event)
          "Bresnahan part of bipartisan farming bill" → favors Bresnahan (positive legislative coverage)
          "Cognetti's anti-corruption pitch lands with voters" → favors Cognetti
          "Cognetti launches campaign for House" → favors Cognetti

    2c. Candidate framed NEUTRALLY but the topic is an OPPOSITION ATTACK VECTOR:
        A topic is an "attack vector" if it ORIGINATED as opposition research and the candidate
        keeps getting tied to it. Once a topic is established as an attack vector, even neutral
        or defensive coverage favors the OPPONENT because visibility of the attack vector is
        the campaign asset.
        Known attack vectors in PA-08:
          - Bresnahan + "stock trading / stock trades / congressional trading" → favors Cognetti
          - Bresnahan + "ethics allegation / corruption / insider trading" → favors Cognetti
          - Cognetti + "carpetbagger / running for two offices / mayor abandoning Scranton" → favors Bresnahan
        Examples:
          "Bresnahan stops frequent stock trades" → favors Cognetti (still in the attack vector)
          "Cognetti's response on dual campaigns" → favors Bresnahan (defending against the attack vector)

STEP 3 — Party-frame coverage (ONLY if STEP 2 doesn't apply, i.e. neither candidate is the article's subject):
  The article isn't about either candidate by name, but does it advance a partisan framing?
  - Positive framing of Party_A (without negative critique of Party_B) → favors CANDIDATE_A.
  - Critical framing of Party_A → favors CANDIDATE_B (the opposing party's candidate).
  - Bipartisan / non-partisan framing → "neutral" (return to step 1's logic).

  IMPORTANT: "bipartisan" is NOT a party-favored framing. Don't say "bipartisan favors Democrats"
  or "bipartisan favors Republicans". It's bipartisan.

  Examples:
    "Walz says Democrats' coalition is so big" → Dem self-promotion → favors Cognetti
    "Republicans force vote on tax bill" → GOP framing wins → favors Bresnahan
    "Trump signs executive order on X" → check article framing:
       - If article is POSITIVE about Trump's action → favors Bresnahan (GOP)
       - If article is CRITICAL of Trump's action → favors Cognetti (Dem)

STEP 4 — Tie-breaker for truly mixed coverage:
  Both candidates featured roughly equally, no clear framing winner.
  Pick the side whose campaign would more eagerly clip this for their press kit.
  Truly even-handed articles are rare; usually there's a subtle framing tilt.

WORKED EXAMPLES:

  "Rep. Bresnahan welcomes 'Dr. Oz' to Scranton for healthcare roundtable"
    → STEP 2b → Bresnahan is the subject, positive framing (his event) → favors Bresnahan.
    DO NOT apply STEP 3 here because STEP 2 matched.

  "Bresnahan part of bipartisan legislation to put local food on local tables"
    → STEP 2b → Bresnahan is the subject, positive framing (legislative work) → favors Bresnahan.
    DO NOT say "bipartisan favors the Democrat" — that's wrong.

  "Walz says Democrats' coalition is so big 'Sanders and Cheney are buddies'"
    → STEP 2 doesn't apply (article is about Walz, not Cognetti/Bresnahan).
    → STEP 3 → Dem self-promotion → favors Cognetti.

  "Trump tests affordability message at PA rally"
    → STEP 2 doesn't apply.
    → STEP 3 → Trump (GOP frame). Read the summary:
       - If neutral/positive about Trump → favors Bresnahan.
       - If skeptical about Trump's claim → favors Cognetti.

  "Bridge replacement in central Pa."
    → STEP 1 → no political content → "neutral".

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence; cite which step decided it>"}
"""


PROMPTS["v3_layered"] = """You classify political news articles for perspective in a head-to-head race.

You'll be told the two candidates (CANDIDATE_A from Party_A vs CANDIDATE_B from Party_B). Decide which campaign would WANT to spread this article in their press clippings.

REASONING PROCESS (apply in this order — first match wins):

STEP 1 — Off-topic check:
  Does the article mention EITHER candidate by name, OR their party, OR the district/race, OR a topic widely
  associated with one of them (e.g. a known attack vector, a signature issue)?
  If NO to all → "neutral". Stop.

STEP 2 — Direct candidate coverage:
  Does the article focus on ONE candidate (mentioned in title/lead)?
  - If the framing of that candidate is NEGATIVE (scandal, critique, opposition research) → favors the OTHER candidate.
  - If the framing is POSITIVE (endorsement, achievement, success) → favors THIS candidate.
  - If the framing is NEUTRAL but the topic is an OPPOSITION ATTACK VECTOR (e.g. "Bresnahan + stock trades" is
    an attack vector; "Cognetti + carpetbagger" is an attack vector) → still favors the OTHER candidate.
    The mere visibility of the attack vector is a campaign asset for whoever raised the issue.

STEP 3 — Party-frame coverage (when neither candidate is the article's focus):
  Is the article praising one PARTY or critiquing the other PARTY?
  - Positive framing of Party_A → favors CANDIDATE_A.
  - Critical framing of Party_A → favors CANDIDATE_B.
  - Article cites a partisan figure (Trump, Pelosi, Schumer, McConnell, Shapiro, Walz, etc.) advancing their
    party's framing → favors the candidate from that party.

STEP 4 — Mixed / unclear:
  If the article features both candidates with even hands and no clear framing winner → still pick the side
  whose campaign would be more likely to put it in their press packet. Truly even-handed articles are rare;
  there's usually a subtle framing tilt.

EXAMPLES (with reasoning):

  Title: "Bresnahan's stock trading raises ethics questions"
    → STEP 2 → Bresnahan + negative framing → favors Cognetti.

  Title: "Cognetti's anti-corruption pitch lands with voters"
    → STEP 2 → Cognetti + positive framing → favors Cognetti.

  Title: "Bresnahan stops frequent stock trades after disclosures"
    → STEP 2 → Bresnahan + stock-trades is the opposition attack vector → still favors Cognetti.

  Title: "Cognetti's campaign launch in PA-08"
    → STEP 2 → Cognetti + positive framing (launch announcement) → favors Cognetti.

  Title: "Walz says Democrats' coalition is so big"
    → STEP 3 → Dem party self-promotion → favors Cognetti (Dem candidate).

  Title: "Republicans force vote on healthcare bill"
    → STEP 3 → GOP action framing → favors Bresnahan (GOP candidate) if framing is favorable to the action.

  Title: "Bessent rips Pelosi, calls for stock trade ban; mentions Bresnahan's trading"
    → STEP 2 → Bresnahan + negative-attack-vector framing → favors Cognetti.

  Title: "Trump rally tests affordability message in PA"
    → STEP 3 → Trump (GOP frame) → favors Bresnahan, UNLESS the article's framing is critical of Trump,
       in which case favors Cognetti. Read the summary carefully.

  Title: "Bridge replacement in Pa. causes lane closures through 2028"
    → STEP 1 → no candidate, no party, no attack vector → "neutral".

  Title: "Tree planting at Valley Middle School"
    → STEP 1 → no political content → "neutral".

Return strict JSON:
  {"favored_candidate": "<NAME_A>" | "<NAME_B>" | "neutral",
   "reason": "<one sentence; cite which step decided it>"}
"""


# ── Test runner ────────────────────────────────────────────────────────────

def run_iteration(
    iter_id: str, prompt: str, sample: list[SourceItem],
    cand_name: str, cand_party: str, opp_name: str, opp_party: str,
    provider: OpenAIProvider,
) -> dict[int, dict]:
    """Run one prompt across the sample. Returns {item_id: result dict}."""
    results: dict[int, dict] = {}
    for i, item in enumerate(sample, 1):
        title = (item.title or "")[:200]
        summary = (item.summary or "")[:600]
        excerpt = (item.raw_text or "")[:600]
        user_prompt = (
            f"CANDIDATE_A: {cand_name} ({cand_party})\n"
            f"CANDIDATE_B: {opp_name} ({opp_party})\n\n"
            f"Article title: {title}\n"
            f"Summary: {summary}\n"
            f"Excerpt: {excerpt}\n\n"
            f"Classify. favored_candidate must be exactly {cand_name!r}, {opp_name!r}, or \"neutral\"."
        )
        try:
            raw = provider._chat(
                user_prompt=user_prompt, system_prompt=prompt,
                json_mode=True, temperature=0, seed=42,
            )
            parsed = _parse_json_response(raw) or {}
            results[item.id] = {
                "favored": (parsed.get("favored_candidate") or "?")[:60],
                "reason": (parsed.get("reason") or "")[:200],
            }
        except Exception as e:
            results[item.id] = {"favored": "ERROR", "reason": str(e)[:200]}
        if i % 10 == 0:
            print(f"    [{i}/{len(sample)}]")
    return results


def diff_iterations(prev: dict[int, dict], curr: dict[int, dict], sample: list[SourceItem]) -> None:
    """Print a diff of changed classifications."""
    items_by_id = {it.id: it for it in sample}
    changed = []
    for iid in prev:
        if iid not in curr:
            continue
        if prev[iid].get("favored") != curr[iid].get("favored"):
            changed.append(iid)
    print(f"\n  Δ changed classifications: {len(changed)}")
    for iid in changed:
        item = items_by_id.get(iid)
        title = (item.title or "")[:60] if item else "?"
        p = prev[iid].get("favored")
        c = curr[iid].get("favored")
        print(f"    [{iid}] {p!r} → {c!r}  | {title!r}")
        print(f"           new reason: {curr[iid].get('reason')[:120]!r}")


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    selected_iter = sys.argv[2] if len(sys.argv) > 2 else None
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 42

    db = SessionLocal()
    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name; cand_party = cfg.party
    opp_name = opp.name; opp_party = opp.party
    print(f"Race: {cand_name} ({cand_party}) vs {opp_name} ({opp_party})")

    classify = get_classifier(db)
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .all()
    )
    fallback = [it for it in items if classify(it).method == "fallback"]
    random.seed(seed)
    sample = random.sample(fallback, min(n, len(fallback)))
    print(f"Sample size: {len(sample)} (deterministic seed={seed})")

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")

    # Load existing results (keyed per seed so different sample sets
    # don't collide).
    out_path = Path(__file__).parent / f"perspective_iterations_seed{seed}.json"
    all_results: dict[str, dict[int, dict]] = {}
    if out_path.exists():
        with out_path.open() as f:
            raw = json.load(f)
            all_results = {k: {int(iid): r for iid, r in d.items()} for k, d in raw.items()}

    iters_to_run = [selected_iter] if selected_iter else list(PROMPTS.keys())
    for iter_id in iters_to_run:
        if iter_id not in PROMPTS:
            print(f"Unknown iter_id {iter_id!r}; available: {list(PROMPTS)}")
            continue
        print(f"\n=== Running iter {iter_id!r} ===")
        results = run_iteration(
            iter_id, PROMPTS[iter_id], sample,
            cand_name, cand_party, opp_name, opp_party, provider,
        )
        all_results[iter_id] = results

        # Print distribution
        from collections import Counter
        c = Counter(r["favored"] for r in results.values())
        print(f"  Distribution: {dict(c)}")

        # Print diff against previous iteration if any
        keys = list(all_results.keys())
        idx = keys.index(iter_id)
        if idx > 0:
            prev_id = keys[idx - 1]
            print(f"\n  Diff vs {prev_id!r}:")
            diff_iterations(all_results[prev_id], results, sample)

    # Save all
    with out_path.open("w") as f:
        json.dump(
            {k: {str(iid): r for iid, r in d.items()} for k, d in all_results.items()},
            f, indent=2,
        )
    print(f"\nSaved results → {out_path}")

    # Print final per-article view across all iterations
    print(f"\n=== Per-article cross-iteration view ===")
    iters = list(all_results.keys())
    items_by_id = {it.id: it for it in sample}
    for iid in sorted(items_by_id):
        item = items_by_id[iid]
        title = (item.title or "")[:55]
        row = "  ".join(
            f"{(all_results[it].get(iid, {}).get('favored') or '?')[:18]:>18}"
            for it in iters
        )
        print(f"  [{iid:5d}] {row}  | {title!r}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
