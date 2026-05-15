# Phase Prompts

One prompt per phase. Paste the whole block into a fresh Claude Code session in this directory. Each prompt is self-contained — the session starts cold.

**Before pasting**: make sure the previous phase's PR is merged and you've clicked through the app for ~10 min. Add anything surprising to `AUDIT.md`'s decision log.

---

## Phase 0 — Stop the bleeding

```
Read PRODUCT_BRIEF.md, then read AUDIT.md in full, then read the Phase 0 section carefully.

Your job: ship Phase 0 as described in AUDIT.md. Bugs 1–7, gaps 8–10 and 13, and debt items 15, 16, 18, 19, 20, 21, 22, 23, 24, 27, 29.

For "Pick one" decisions (e.g. wire issue clustering back in OR delete it): ask me before deciding. Don't guess.

Process:
1. Propose a plan as a checklist. Do not write code yet. Wait for my approval.
2. After approval, work in small commits — one per logical chunk (e.g. "fix HTML stripping", "delete dead schemas", "delete stale tests"). Don't bundle everything into one commit.
3. Run backend tests and frontend tests after each commit that could affect them. Don't proceed if tests regress.
4. When all tasks are done, run the full backend test suite and frontend test suite. Show me the results.
5. Boot the backend and frontend and verify the 7 bugs are visibly fixed in the browser. Take screenshots.
6. Open a PR titled "Phase 0: cleanup + live bug fixes" with a body that lists every checkbox from AUDIT.md Phase 0 with ✅ or ❌ for each.
7. Update AUDIT.md: flip Phase 0 status to "done" and check off each task.

Acceptance: all backend tests pass, all frontend tests pass, no HTML in summaries, sidebar badge shows the real count (254+), no duplicate opponent, frame owner_type correctly classified.

Do NOT start Phase 1 work even if you finish early. Stop at the PR.
```

---

## Phase 1 — Correct, cheap, searchable ingest

```
Read PRODUCT_BRIEF.md, then read AUDIT.md, then read the Phase 1 section carefully. Verify Phase 0 is marked done in AUDIT.md — if not, stop and tell me.

Your job: ship Phase 1 as described in AUDIT.md.

Key constraints:
- Single combined LLM call per article. Do not add a second call. The brief is explicit about this.
- Sentiment is added to the same call's JSON response, not a new call.
- FTS5 is the search backend. Do not introduce Elasticsearch or any external search service.
- The source-detail page should reuse existing components where possible. Don't build a design system yet.

Process:
1. Propose a plan as a checklist. Include the exact LLM prompt schema you'll use for the combined call. Wait for my approval.
2. Write the new combined-call code. Write a test for it that uses MockLLMProvider with a stubbed response — this is the first real test for campaign_analysis.
3. Migrate the DB to add `sentiment` and FTS5. Make sure migrations are idempotent.
4. Build the search endpoint. Test it with curl before touching the frontend.
5. Build the source-detail page. Make it a real React Router route, not a modal.
6. Move `rematch_all` to a background job using APScheduler. Expose a "trigger rematch" button that just enqueues, returns immediately.
7. Commit per chunk. Run tests after each chunk.
8. Open a PR titled "Phase 1: combined LLM call, FTS5 search, source detail". Include a screenshot of the source-detail page and a sample search query.
9. Update AUDIT.md.

Acceptance: ingest makes exactly one LLM call per article, search returns relevant results within 200ms, source-detail page shows extracted text + sentiment + reasons + frame mentions, rematch endpoint returns in <1s.

Stop at the PR.
```

---

## Phase 2 — Real coverage

```
Read PRODUCT_BRIEF.md, then read AUDIT.md, then read the Phase 2 section carefully. Verify Phase 1 is marked done.

Your job: ship Phase 2 as described in AUDIT.md.

Decisions to make WITH ME before writing code:
- X/Twitter: snscrape (free, fragile) vs Nitter RSS bridge (free, depends on a public instance) vs paid X API ($100+/mo). Recommend one and let me approve.
- Facebook: skip entirely, use Apify, or use a paid vendor. Recommend one.
- Reddit: free official API is fine, no decision needed.
- Generic crawler: trafilatura is the default unless you have a strong reason to pick something else.

Process:
1. Propose a plan + decisions. Wait for approval before writing code or signing up for any service.
2. For each new source type: create a new ingester service in backend/app/services/ingestion_<type>.py. Reuse the existing SourceItem schema. Don't fork the schema.
3. Add a SourceMonitor UI page surfacing the already-defined `SourceMonitor` model (it's wired in the API but has no page — see AUDIT.md debt 23).
4. Outlet authority + geo tagging: add columns to a new `Outlet` table; backfill known PA outlets manually. Don't auto-detect.
5. Commit per source type. Test each ingester with a fixture, not by hitting the live service in tests.
6. Open a PR. Include a table in the PR description showing articles ingested per source in the last 24h after deploy.
7. Update AUDIT.md.

Acceptance: at least Reddit + trafilatura crawler flowing; outlets have location + authority; SourceMonitor page works in the UI.

Stop at the PR.
```

---

## Phase 3 — Analytics layer

```
Read PRODUCT_BRIEF.md, then read AUDIT.md, then read the Phase 3 section carefully. Verify Phase 2 is marked done.

Your job: ship Phase 3 as described in AUDIT.md.

Charting library: use Recharts. Don't introduce D3 directly or any heavier viz lib.

Process:
1. Propose a plan. Include wireframes (ASCII is fine) for the per-frame detail page and the filter chip pattern. Wait for approval.
2. Time-series endpoint first. Test it with curl. Make sure it handles empty data, single-day data, and full 30-day windows.
3. Sparklines on frame cards. Should be self-contained components — one prop in (array of numbers), one chart out.
4. Per-frame detail page (`/narratives/:id`) with day-by-day chart, top sources, top mentions, share-of-voice donut.
5. Velocity / spike detection: compute on-the-fly in the briefing endpoint, not stored. Add a "🚨 spike" badge to briefing cards above threshold.
6. Filter chips: implement once as a shared component, then apply to ReviewQueue, Briefing, and Narratives.
7. Commit per feature. Open a PR with screenshots of every new chart.
8. Update AUDIT.md.

Acceptance: every list page has filter chips that work, frame detail page renders with at least one chart, briefing surfaces velocity spikes, share-of-voice donut renders correctly with all three slices.

Stop at the PR.
```

---

## Phase 4 — Workflow + alerting

```
Read PRODUCT_BRIEF.md, then read AUDIT.md, then read the Phase 4 section carefully. Verify Phase 3 is marked done.

Your job: ship Phase 4 as described in AUDIT.md.

Decisions to make WITH ME before writing code:
- Email provider: Resend (simpler) vs SES (cheaper at scale). Recommend one.
- Slack: incoming webhooks only, no Slack app. No OAuth.
- PDF library: weasyprint vs reportlab vs jsPDF (frontend). Recommend one.

Process:
1. Propose a plan + decisions. Wait for approval.
2. Alert rules: build the schema first (alert_rule table with trigger_type, threshold, target_email/slack_webhook). UI for creating rules can be minimal — a single form on a new /alerts page.
3. Background evaluator: APScheduler job that runs every 5 min, checks rules, sends notifications. Deduplicate so the same condition doesn't fire repeatedly.
4. Daily/weekly digests: separate scheduled job, renders a template, sends via the chosen email provider.
5. Saved views: serialize the filter chip state to a URL query string. "Save view" persists the URL + name.
6. PDF export: server-side. Take the briefing endpoint's payload, render an HTML template, convert to PDF. Don't try to clone the in-app styling.
7. Response status + notes: add columns to SourceItem, add UI for them on the source-detail page.
8. Commit per feature. Open a PR. Test that an alert actually fires to a real email + Slack.
9. Update AUDIT.md.

Acceptance: alert rule fires to email + Slack on a real condition, daily digest sends, PDF export downloads a readable file, saved views persist and reload correctly.

Stop at the PR.
```

---

## Phase 5 — Multi-tenant + auth

```
SKIP THIS PHASE for now unless we're onboarding a second campaign. For Cognetti alone it's unnecessary work. Ask me before starting.

If approved:

Read PRODUCT_BRIEF.md, then read AUDIT.md, then read the Phase 5 section. Verify Phase 4 is marked done.

This phase is invasive — every query in the backend needs a workspace_id scope. Treat it as a full-week project.

Process:
1. Propose a plan. Include a migration strategy that keeps the existing Cognetti data working. Wait for approval.
2. Add `Workspace` model. Backfill: create one workspace, assign every existing row to it.
3. Add workspace_id to every table. Migrate. Add a NOT NULL constraint after backfill.
4. Add Clerk (or magic-link) auth. Frontend gets a sign-in page. Backend gets middleware that resolves auth → workspace_id.
5. Update every query in every route + service to filter by workspace_id. Easiest way: a SQLAlchemy session-level filter via event listener.
6. RBAC: admin / staff / read-only. Hide destructive UI actions for non-admins.
7. Per-workspace LLM keys: optional field on Workspace; falls back to env var.
8. Commit per layer. Open a PR. Test by creating a second workspace and confirming isolation.
9. Update AUDIT.md.

Acceptance: two workspaces with isolated data, non-admin can't reset workspace, sign-in flow works end-to-end.

Stop at the PR.
```

---

## Phase 6 — Auto narrative discovery

```
DO NOT START unless: (a) Phase 4 is done, (b) we've used the product for 2+ weeks on a real campaign, (c) we've decided manual frames aren't enough. Ask me before starting.

Read PRODUCT_BRIEF.md (note: this phase explicitly contradicts the brief — we're revisiting that decision), then read AUDIT.md Phase 6.

Process:
1. Propose a plan. Include cost estimate for embeddings on the current corpus + projected monthly cost. Wait for approval.
2. Add `embedding` column to SourceItem (or separate table). Backfill via OpenAI `text-embedding-3-small`.
3. Weekly APScheduler job: run HDBSCAN on last 30 days of embeddings.
4. For each cluster: LLM call to generate a category-style frame name + description (not event-specific).
5. Surface suggested frames in a new "Discovered narratives" tab on the Narratives page. Staff accept / reject / merge.
6. Merge UX: combine two frames into one, re-link all NarrativeFrameMention rows.
7. Open a PR. Include the first batch of discovered frames for me to review.
8. Update AUDIT.md and the decision log.

Acceptance: weekly job runs, produces 2–5 reasonable frames, staff can accept/merge cleanly.

Stop at the PR.
```

---

## Phase 7 — Polish

This phase is open-ended. Don't run it as one session. Pick one item, do one session per item:

- Broadcast ingestion (Otter / AssemblyAI)
- Fact-checking / claim contradiction tracking
- Mobile-responsive layout
- A11y pass
- Design system extraction

For each, the prompt template is:

```
Read PRODUCT_BRIEF.md and AUDIT.md. We're doing one polish task: <task name>.

Propose a plan. Wait for approval. Commit per chunk. Open a PR. Update AUDIT.md.

Stop at the PR.
```

---

## Notes for every session

- If the session encounters something the prompt doesn't anticipate, **stop and ask** — don't improvise.
- If a test fails for reasons unrelated to the current task, fix it in a separate commit clearly labeled "drive-by fix".
- If you find a new bug not in AUDIT.md, add it to AUDIT.md's critical bugs section in the same PR; don't fix it silently.
- Don't refactor surrounding code unless the task requires it.
- Don't add error handling for cases that can't happen.
