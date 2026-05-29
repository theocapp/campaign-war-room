# Noctua KG — is the architecture wrong? Second opinion needed.

> Background: you reviewed this same codebase a few hours ago (the input
> was a doc called `NOCTUA_KG_REVIEW.md`, ~5K words covering schema,
> extractor, the 14 principles we'd implemented, and 10 open questions).
> You wrote a substantive critique — your headline was *"You are building
> a political claims graph, not a generic semantic KG. That distinction
> should drive almost every schema decision."* You flagged the
> claims-as-triples problem as a medium-term redesign and green-lit
> running a $5–6 LLM backfill at v14.6 to ship.
>
> Today I tried to land that backfill. Quality on the new event/attended
> capabilities was worse than expected. I tightened the prompt. Quality
> got WORSE, not better. I'm now questioning whether the triple-based KG
> abstraction is fundamentally wrong for this domain, and I want your
> honest verdict before I burn more time and money.

---

## What the system is (one-paragraph refresher)

Political campaign intelligence tool, single-race (PA-08 House race,
Cognetti D vs Bresnahan R), being productized as a SaaS called Noctua.
~17K articles ingested, ~5,857 race-relevant. Stack: FastAPI + SQLite
+ React. The KG layer extracts entities (5 types: person, org, bill,
location, event) and relations (10 predicates: endorses, criticizes,
attacks, voted_for, voted_against, co_sponsored, represents, member_of,
predecessor_of, attended) via a single `gpt-4o-mini` call per article.
Strict ontology, domain/range constraints, commonsense rules,
dimensional stance, dual-write to a claim layer, drift versioning,
contradiction queue, force-directed UI with path-finder + claim
inspector. All the things you said were good engineering.

The KG was layered onto an EXISTING system that already had narrative
frames (named messages with stage progression: emerging → spreading →
mainstream → fading → dormant), frame variants (HDBSCAN-clustered
specific phrasings within each frame), story clusters (SimHash dedup),
frame momentum signals, and a morning briefing memo. The pre-existing
system is nuanced, story-shaped, and works well — the briefing doesn't
depend on the KG at all.

---

## What happened in the last 3 hours

### v14.6 stage 1 (50 highest-relevance articles, ~$0.05, 8 min)

- 50/50 articles processed, 0 failures.
- **9 event entities auto-discovered.** 5 of 9 were election PROCESSES,
  not events: "2024 US presidential election", "midterm elections",
  "2026 Midterms", "2024 Midterm Election", "2026 Congressional Election".
  The other 4 were real events (a press conference, a rally, a
  roundtable, a 2025 radio interview).
- **5 attended relations.** Quality:
  - ✅ Cognetti → press conference. Quote: *"Mayor of Scranton and Pa.
    Congressional candidate Paige Cognetti speaks to press during a
    conference at PSEA"*
  - ✅ Bresnahan → Farmers for Free Trade roundtable. Quote: *"U.S. Rep.
    Rob Bresnahan speaks before the Farmers for Free Trade round table
    discussion"*
  - ⚠️ Mackenzie → January 2026 rally. Quote: *"Mackenzie blamed the
    Biden administration for high prices"* (quote doesn't actually say
    he was at any rally)
  - ❌ Bresnahan → January 2026 rally. Quote: *"Bresnahan's office did
    not respond to a request for comment"* (negative evidence)
  - ❌ Cognetti → 2026 Congressional Election. Quote: *"Cognetti won
    comfortably despite announcing before the election..."* (she's
    running IN it, not attending it)

So: 2/5 good, 1/5 weak, 2/5 hallucinated.

### v14.7 retry (same 50 articles, tightened prompt, rewrite=True)

I added to the prompt:

For **event** entity type:
> REJECT these as events: "the 2024 election", "the midterms", "the
> 2026 Congressional Election" — these are months-long processes across
> many locations, NOT events. Election DAYS may be events ONLY when the
> article describes a specific gathering ON that day at one place.

For **attended** predicate:
> STRICT REQUIREMENTS:
> (a) The sample_quote MUST contain an attendance verb tying the subject
>     to the event — e.g. "spoke at", "attended", "appeared at",
>     "was at", "addressed", "headlined"...
> (b) DO NOT infer attendance from context. Examples that ARE NOT
>     evidence of attended:
>       - "X's office did not respond" — opposite signal
>       - "X blamed Y for high prices" — only attributes a position
>       - "X won the election" — describes an outcome, not attendance
>       - "X will appear at the rally tomorrow" — future tense
>       - "X tweeted about the rally" — comment, not attendance
> (c) "running in" or "campaigning for" an election is NEVER attended.

Bumped `EXTRACTOR_VERSION` to v14.7. Re-ran same 50 articles with
`rewrite=True` (clears prior contribution before re-extraction).

Result:
- **11 event entities**, MORE not fewer. 7 of 11 are STILL elections,
  despite the explicit prohibition: "2024 General Election", "2024 US
  presidential election", "midterm elections", "Scranton City Council
  election", "8th congressional district primary", "2026 Midterms",
  "2024 midterm election".
- **4 attended relations.** Quality:
  - ❌ Cognetti → 8th congressional district primary. Quote: *"Cognetti
    officially announced she's running as the democratic candidate in
    the 8th district primary"* — the exact "running in" inference the
    prompt explicitly forbade
  - ❌ Cognetti → Easter Church Services Threat. Quote: *"threatening to
    unleash her police force on church attendees during her COVID-19
    crackdown"* — the "event" is a controversy from 2020, the quote
    describes the threat itself
  - ✅ Cognetti → press conference. Same good one from v14.6.
  - ❌ Cognetti → Farmers for Free Trade round table. Quote: *"U.S. Rep.
    Rob BRESNAHAN speaks before the Farmers for Free Trade round table
    discussion at Eckels Farm in Clarks Summit"* — quote is about
    Bresnahan, LLM swapped the subject to Cognetti

So: 1/4 good, 0/4 weak, 3/4 hallucinated. WORSE than v14.6.

---

## My current read

The LLM isn't being lazy or sloppy. It's doing what models do at this
scale: given a structured triple schema, it finds triple-shaped content
in articles whether the article contains it or not. The "Cognetti
attended Easter Church Services Threat" is a real article about a
controversy where Cognetti made a threat — the model contorts it into
`attended` because that's the closest fit in our predicate vocabulary.
The Bresnahan→Cognetti subject swap is the model "completing the
pattern" once it decides someone attended that event.

This makes me think the triple shape itself is fighting the corpus.
Political news isn't (X did Y to Z) atomic facts. It's stories with
actors, quotes, context, and narrative arcs. The model is willing to
play along with the schema, but it can't actually generate clean
triples from prose that doesn't contain triples.

---

## Four directions I'm weighing

### A. Push through with deterministic filters
Add post-LLM filters in code:
- For events: reject if name matches `/election|midterm|primary/i` unless
  `event_type` is specifically `vote` (a single recorded vote on a bill).
- For attended: require `sample_quote` to contain a verb from a whitelist
  AND the subject's name. Drop otherwise.

Accept residual noise. Run the backfill at ~$5-6. Move on.

**Implicit bet:** 80% accuracy is enough; humans clean up in review queue.

### B. Demote the KG to "who's who"
Keep entities + the slow-changing structural relations (`represents`,
`member_of`, `predecessor_of` — sourced from a seed file
`role_transitions.PA-08.json`, not from LLM extraction). Drop the action
predicates (`endorses`, `criticizes`, `attacks`, `attended`, `voted_for`,
`voted_against`, `co_sponsored`) — let narrative frames handle those.
The KG becomes a stable identity layer + a "who holds what office when",
not a network of actions.

**Implicit bet:** action data is genuinely too messy as triples; the
existing frame system already does this better, and the EntityNetwork UI
becomes an org-chart-style reference, not a hairball.

### C. Quote-driven claims (your medium-term recommendation, but NOW)
Stop emitting predicates. Each LLM extraction returns: notable quotes +
which entities they involve + a one-line summary. Cluster the quotes
semantically — we already have the HDBSCAN + embedding pipeline from
frame variants. Each cluster is a "claim assertion" anchored in real
text, not a logical triple. The downstream UI shows claims as
sentence-shaped propositions ("Bresnahan defended his stock trades as
unrelated to legislation"), not triples.

**Implicit bet:** triples were always the wrong shape; we should take
the redesign hit now rather than ship more brittle triple data and
migrate later.

### D. RAG instead of pre-extraction
Skip the extraction step for actions entirely. Articles + frames already
have full-text + summary fields; index them in a vector store. When the
user asks a question, retrieve relevant articles and ask the LLM to
synthesize the answer at query time. Keep entities only as canonical
identities so search can filter by them.

**Implicit bet:** LLM-at-query-time beats LLM-at-extraction-time for this
domain — cheaper, more flexible, less brittle. The "knowledge" lives in
the articles; we don't try to pre-compress it into a schema.

---

## What I want from you

Don't hedge. I'm not asking for "depends on your priorities" — I'm
asking for a verdict.

### 1. Which direction would YOU pick — A, B, C, D, or something else?
Commit to one. Tell me why. Tell me what would change your mind.

### 2. Was your earlier advice wrong, given the new data?
Three hours ago you wrote *"the system is good enough to justify the
backfill. I would run it."* Given the v14.7 quality data above, would
you still say that? If yes, why? If no, what should I have done
differently between then and now?

### 3. What's your read on the failure mode?
- **Model capability?** Would `gpt-4o` or `o1-mini` solve this where
  `gpt-4o-mini` can't? Worth the 5x cost?
- **Schema shape?** Triples are genuinely wrong for political news.
- **Prompt engineering?** Still tunable — I gave up too easily.
- **Something I'm not seeing?**

### 4. What would real users actually USE?
The EntityNetwork visualization — force-directed graph, side panel,
path finder, claim inspector — is impressive and expensive. Be honest:
when a campaign staffer opens the tool at 8am, do they go to the graph?
Or to the briefing? Or to the narrative frames? You've reviewed enough
of these systems to have an opinion.

### 5. If you pick C or D, what's the minimum-viable migration path?
Specifically: I have 5,857 race-relevant articles, ~$6 of LLM budget for
one full pass, an existing claims/claim_supports/entity_relations
schema in production, and need to ship something real campaigns can
use. Don't give me a wishlist — give me the smallest concrete thing I
can do this week.

### 6. Push back on my framing
If the four directions above are a false dichotomy or miss the obvious
right answer, say so. If "the KG is fine, you just need to ship and
stop polishing" is the right call, say that. If "tear it all down" is
the right call, say that too. The point of this prompt is to get the
critique I can't generate from inside the codebase.

---

Be specific. Be opinionated. Push back hard if you disagree with how
I've set this up. The pattern of this whole project has been: I build,
you critique, I iterate, you critique again — and that cycle has
produced a much better system than I'd have built alone. This is round
three. Make it land.
