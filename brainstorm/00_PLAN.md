# Overnight Brainstorm — Plan & Status

Started: 2026-05-24 ~04:00 UTC
User gave me ~5 hours and full creative freedom. Hard rules:
- No code edits
- No DB writes
- No external API calls (no Tavily, GDELT, OpenAI, Gemini, RSS fetches)
- No triggering rescore/rematch/suggest
- Database queries (read-only) and running pytest are fine
- Markdown files in `/brainstorm/` are the output

## Files I produced

| # | File | What it covers | Status |
|---|---|---|---|
| 0 | `00_TLDR.md` | Executive summary — top 10 recommendations across everything | ✅ DONE |
| 1 | `01_HIDDEN_DATA.md` | Columns/tables full of useful data the UI doesn't surface | ✅ DONE |
| 2 | `02_FEATURE_IDEAS.md` | 15 ranked feature ideas with UI sketches and effort estimates + 3 wild ideas | ✅ DONE |
| 3 | `03_FREE_DATA_SOURCES.md` | 17 APIs ranked by value | ✅ DONE |
| 4 | `04_FRONTEND_UX_COPY.md` | Language/labeling/density improvements | ✅ DONE |
| 5 | `05_BACKEND_ARCHITECTURE.md` | Code review of services + prompts + scoring + ingestion + test audit | ✅ DONE |
| 6 | `06_MARKET_STRATEGY.md` | Productization, positioning, generalizing to other races | ✅ DONE |
| 7 | `07_QUICK_WINS.md` | 24 <30-min changes that ship visible improvement | ✅ DONE |
| 8 | `08_OPEN_QUESTIONS.md` | 18 things I'm uncertain about and want your judgment on | ✅ DONE |
| 9 | `09_CLUSTER_INVESTIGATION.md` | 🎁 BONUS — Rabbit hole: why 94% of relevant clusters are size-1 | ✅ DONE |
| 10 | `10_FEATURE_SPEC_DAILY_DIFF.md` | 🎁 BONUS — Implementable spec for the highest-value feature | ✅ DONE |
| 11 | `11_INTELLIGENCE_TODAY.md` | 🎁 BONUS — What an analyst would tell you this morning from your data | ✅ DONE |

## Working order

1. Database deep-dive (gives me data for everything else) — 30 min
2. `01_HIDDEN_DATA.md` — 30 min
3. `02_FEATURE_IDEAS.md` — 60 min (the biggest one)
4. `03_FREE_DATA_SOURCES.md` — 30 min
5. `04_FRONTEND_UX_COPY.md` — 45 min
6. `05_BACKEND_ARCHITECTURE.md` — 60 min (the second-biggest)
7. `06_MARKET_STRATEGY.md` — 30 min
8. `07_QUICK_WINS.md` — 20 min
9. `08_OPEN_QUESTIONS.md` — 15 min
10. `00_TLDR.md` — 20 min (synthesizes everything)

Total: ~5.5 hours. Will trim/pace as I go.

## What I committed to up front

- Volume vs quality: 15 features (ranked), not 50 (listed). Same for sources.
- Confidence tagging: every external API claim flagged ✅ (sure) / ⚠ (recall but verify) / ❓ (need to check)
- Grep before recommending: don't propose features that already exist
- Time-box: ~1hr per file max
- Concrete examples from real data — not vague suggestions
