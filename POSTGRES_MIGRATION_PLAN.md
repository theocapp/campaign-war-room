# SQLite → Postgres migration plan (LOCKED)

**Status:** In progress — Phase 0
**Started:** 2026-05-27
**Owner:** Coordinated across Claude sessions via INTER_SESSION.md
**Estimated total wall-clock:** 3–4 weeks elapsed (work is mostly gated on validation, not coding)

This document is the single source of truth for the migration. Other Claude
sessions: do not start schema work or DB-touching refactors without
coordinating here. If you need a column change while this migration is in
flight, append to INTER_SESSION.md and wait.

---

## Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Hosting | Local Docker Postgres; production host TBD | Lowest risk, no cloud cost yet |
| Dual-dialect support after migration? | No — Postgres only | Cleaner code, single test surface |
| Data migration | Migrate all data, preserve IDs | Campaign keeps full history |
| Schema scope | 1:1 with current SQLite. No `jsonb`, no `pgvector` | Smaller migration. Follow-up project for those |
| Schema management | Alembic, baseline hand-authored from live DB | SQLite autogenerate is lossy |
| Search after cutover | Postgres tsvector + GIN — **compatibility layer, not final** | Real search engine (Tantivy/OpenSearch) is a separate Q3 project |
| Rollback window | 4 weeks of green operation before SQLite codepaths deleted | Need at least one full weekly ingestion cycle under load |

## Phase structure

```
Phase 0    Foundations: Alembic install, baseline migration
Phase 0.5  Data audit — preflight_audit.py, BLOCKING gate
Phase 1    Portability: env-var URL, FTS abstraction, Alembic-managed schema
Phase 2    Postgres in Docker + observability wired
Phase 2.5  Concurrency soak test (≥1h with scheduler + rescore + firehose)
Phase 3    Data migration script + rehearsal (≥2 dry runs)
Phase 4    Cutover (runbook-driven, no improvisation)
Phase 5    Cleanup — 4 weeks after Phase 4
```

Each phase ends with a gate. Do not proceed past the gate without
user sign-off.

---

## Phase 0 — Foundations (no behavior change, still on SQLite)

**Goal:** Install Alembic, take ownership of the live schema as the
canonical baseline, lay groundwork for Phase 1 portability work.

**Deliverables:**
- `alembic` + `psycopg[binary]` (psycopg3) in `backend/requirements.txt`
- `backend/alembic/` tree with `env.py` reading `DATABASE_URL` from env
- One baseline migration version reflecting the live SQLite schema
- Live DB stamped at baseline revision (`alembic stamp head`)
- `backend/alembic/_live_sqlite_schema.sql.ref` — captured live schema for diff
- Verification: fresh empty SQLite + `alembic upgrade head` produces a schema
  that diffs-clean against the live DB

**Gate:** User reviews the baseline migration + the verification diff (empty
diff = pass). Sign-off before Phase 0.5.

---

## Phase 0.5 — Data audit (BLOCKING)

**Goal:** Find every place SQLite's permissiveness has accumulated dirt
that Postgres's strictness will reject. Read-only against the live DB.

**Script:** `backend/scripts/preflight_audit.py`

**Checks:**

1. **JSON validity** — every `Text` column documented as JSON-encoded must
   parse. Columns: `relevance_reasons`, `excluded_keywords`, `key_priorities`,
   `geography_keywords`, `relevance_keywords`, `trends_keywords`,
   `neighborhood_keywords`, `gdelt_themes`, `structured_extraction`,
   `gdelt_tone`, `aliases`, `metadata_json`, `evidence_json`, `source_articles`,
   `claim_meta`, `quote_embedding`, `centroid_embedding`,
   `member_frame_ids_json`, `member_candidate_frame_ids_json`,
   `momentum_data`, `metrics_snapshot`, `external_metadata`, `raw_response`,
   `link_reasons`, `extraction_quality_reasons`, `districts`.
2. **FK integrity** — every FK column must resolve to an existing parent.
   Notable risk: `outlet_id` on `source_items` (backfilled by
   `_backfill_outlet_links`).
3. **Unique constraint sanity** — verify no duplicates exist for every
   `UniqueConstraint` in `models.py`. Postgres will reject any.
4. **Boolean coherence** — every `Boolean` column should hold only 0/1.
   Audit for any rogue string values from raw-SQL writes.
5. **Datetime parseability** — every `DateTime` column must be ISO-parseable
   and naive UTC (the codebase convention).
6. **NULL where NOT NULL** — every `nullable=False` column has no NULLs.
   Sneaky in SQLite because of how default values are/aren't enforced.
7. **Enum drift** — columns documented as enums (`stage`, `owner_type`,
   `actionability_label`, `race_relevance_label`, `content_category`,
   `geo_relevance`, `urgency`, `source_credibility`, `perspective`,
   `momentum_signal`, `confidence`, `extraction_method`,
   `verdict`, `decision`, `predicate`, `status`, `stance`) must hold only
   documented values.
8. **UTF-8 sanity** — title / raw_text / summary should be valid UTF-8
   (Postgres COPY will fail on bad bytes).
9. **Oversized payloads** — flag any text column with >1MB content
   (potential indexing pain in Postgres).

**Output:** machine-readable JSON report + human-readable summary written
to `backend/scripts/_audit_report.{json,md}`. CI-friendly exit code.

**Gate:** User reviews the report. Anything that needs cleanup is fixed
before Phase 1 starts. Audit must pass clean (or with explicitly accepted
exceptions) before proceeding.

---

## Phase 1 — Portability (still on SQLite)

**Goal:** Application code works against either SQLite or Postgres by
flipping `DATABASE_URL`. CI runs against both.

**Changes:**

1. `backend/app/db.py`:
   - `DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")`
   - SQLite pragmas (`journal_mode=WAL`, `busy_timeout`) attached only when
     URL starts with `sqlite:`
   - `connect_args={"check_same_thread": False}` only on SQLite
2. Delete `_migrate()` function. Move every column-add and index-creation
   into an Alembic version file. Run via `alembic upgrade head` at app
   startup (or as a deploy step).
3. **FTS abstraction.** Both `_create_fts_table()` (SQLite path) and
   `_create_tsvector_column()` (Postgres path) live in
   `backend/app/services/search_index.py`. Route in `routes/sources.py`
   dispatches based on dialect:
   - SQLite: `WHERE source_items_fts MATCH :q`
   - Postgres: `WHERE search_tsv @@ websearch_to_tsquery('english', :q)`
4. `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING` (two spots:
   `candidate_frame_promoter.py:710`, `expand_outlets_catalog.py:72`)
5. Remove `sqlite_master` lookups (Alembic owns existence checks).
6. CI matrix: pytest runs against SQLite AND a Dockerized Postgres.

**Gate:** Backend boots and passes all tests against both dialects.
Setup wizard + a manual ingestion round-trip work against fresh Postgres.

---

## Phase 2 — Postgres in Docker + observability

**Goal:** Postgres is the dev DB for everyone. Observability is wired in
before we trust it.

**Deliverables:**
- `docker-compose.yml` at repo root: Postgres 16, named volume, port 5432
- `Makefile` targets: `db-up`, `db-down`, `db-reset`, `db-logs`, `db-shell`
- `.env.example` includes
  `DATABASE_URL=postgresql+psycopg://noctua:noctua@localhost:5432/noctua`
- Postgres config tuned for dev observability:
  - `log_min_duration_statement = 500`
  - `log_lock_waits = on`
  - `deadlock_timeout = 1s`
  - `log_checkpoints = on`
  - `pg_stat_statements` extension installed and loaded
  - `shared_preload_libraries = 'pg_stat_statements'`
- App-side observability in `db.py`:
  - Per-session `statement_timeout = '30s'` for API requests
  - Per-session `lock_timeout = '5s'`
  - Higher timeouts for rescore worker + GDELT backfill (separate session
    config they opt into)
  - SQLAlchemy pool event listeners log on `checkout` / `checkin` /
    `invalidate` / `connect`
- New admin route: `GET /api/admin/dbstats` — current pool state, top 10
  slow queries from `pg_stat_statements`, lock waits in the last hour
- README + CLAUDE.md updated: "Postgres via Docker is required for local dev"

**Gate:** `docker compose up` + `uvicorn` → dashboard loads against empty
Postgres. Admin dbstats endpoint returns sane data.

---

## Phase 2.5 — Concurrency soak test

**Goal:** Surface latent concurrency bugs against Postgres before cutover.
SQLite has been hiding write contention behind WAL+busy_timeout; Postgres
will expose it.

**Procedure:**
1. Load Postgres with a recent SQLite snapshot via the (work-in-progress)
   migration script (rough version is fine — only need realistic row volume)
2. Run for ≥1 hour with all of:
   - Scheduler (`scheduler.py`) running normal cadence
   - One manual GDELT backfill in progress
   - One manual rescore over a 100-article slice
   - Bluesky firehose ingesting if available
3. Tail Postgres logs in one terminal, `/api/admin/dbstats` in another
4. Look for:
   - Any deadlock
   - Lock waits > 1s
   - Pool size approaching `max_overflow` (60)
   - Autovacuum on `source_items` thrashing
   - `ON CONFLICT … DO UPDATE` paths in `cluster_writes.py:75` and
     `cluster_writes.py:133` behaving sanely under concurrent writers

**Gate:** No deadlocks. Lock waits < 1s. Pool stays under capacity. Any
issues block Phase 3.

---

## Phase 3 — Data migration script + rehearsal

**Goal:** A repeatable, validated migration of all 112MB of SQLite data
to Postgres, preserving IDs.

**Script:** `backend/scripts/sqlite_to_postgres.py`

**Behavior:**
- Reads a **read-only snapshot** path (not the live DB)
- Writes to a fresh Postgres DB (refuses to run against non-empty target
  unless `--force`)
- Iterates tables in FK-dependency order (topological sort of `models.py`)
- Streams rows in batches of 5,000 with progress logging
- Preserves every primary key using `OVERRIDING SYSTEM VALUE` for SERIAL
  columns; resets sequences to `MAX(id)+1` at the end
- Rebuilds `search_tsv` column for all `source_items` after data load
- Validation pass at the end:
  - Per-table row count match
  - Per-table SHA256 of rows ordered by PK
  - 100 sampled deep-comparison rows per table (selected via
    `id % (count / 100)`)
  - FK integrity sweep (every FK resolves)
  - Aggregate sanity: articles-per-outlet, mentions-per-frame,
    supports-per-claim
- Refuses to mark success unless all five validations pass
- Prints wall-clock time per table for cutover-day estimation

**Rehearsal (≥2 dry runs):**
1. Restore SQLite snapshot to scratch path
2. Drop + recreate scratch Postgres database
3. Run script end-to-end
4. Capture timing, paste output to user
5. Spot-check: dashboard, search, briefing, narratives, opponents,
   monitors against the migrated DB

**Runbook:** `backend/scripts/CUTOVER_RUNBOOK.md` (working doc — not
checked-in product docs) with exact commands in order.

**Gate:** Two clean rehearsals back-to-back. Last rehearsal uses a
snapshot < 24h old.

---

## Phase 4 — Cutover (the only step that can't be done piecemeal)

**Goal:** Flip production to Postgres with minimum downtime and a
clean rollback path.

**Procedure (runbook-driven):**
1. Announce maintenance window (campaign team awareness)
2. Stop scheduler + backend
3. Take final SQLite backup: `war_room.db.cutover-YYYYMMDD-HHMMSS`
4. Run `sqlite_to_postgres.py` against the chosen Postgres target
5. Flip `DATABASE_URL` in `.env`
6. Start backend, run smoke checklist:
   - Dashboard loads
   - `/api/search?q=cognetti` returns sane results
   - Briefing generation runs
   - Opponents list renders
   - Monitors page renders
   - At least one ingestion cycle completes cleanly
7. Restart scheduler
8. Tail logs for first hour

**Rollback:** Revert `.env`, restart backend. The SQLite snapshot is
untouched because the migration ran against a read-only copy.

**Gate:** All smoke checks pass. One ingestion cycle completes.

---

## Phase 5 — Cleanup (4 weeks after Phase 4)

**Goal:** Remove dual-dialect code now that Postgres has proven stable
under one full weekly ingestion cycle.

**Changes:**
- Delete `_set_sqlite_pragmas` listener
- Delete `check_same_thread` connect_arg
- Delete SQLite branch from `search_index.py`
- Delete FTS5 setup code
- Delete dual-dialect handling from any remaining sites
- Archive `backend/war_room.db*` files out of the repo (move to
  `~/Library/Application Support/noctua/db-backups/`)
- Update CLAUDE.md "Database" section: SQLite-only references removed,
  Postgres-only documented
- Remove `sqlite` references from `.env.example`

**Gate:** Final code review. After this, the bridge is burned.

---

## Cross-phase risks

| Risk | Mitigation |
|---|---|
| FTS ranking will change (SQLite BM25 → Postgres `ts_rank`) | Spot-check top 10 user queries pre-cutover; label as "compatibility search layer" not the final answer |
| Schema drift between live DB and `models.py` (because of `_migrate()` history) | Baseline authored from live DB, not models. Phase 0 verification diff catches any miss |
| `INSERT OR IGNORE` and other SQLite-isms | Audited in Phase 1, replaced with `ON CONFLICT DO NOTHING` |
| Concurrent writers causing lock contention | Phase 2.5 soak test surfaces before cutover |
| Live data corruption during migration | Migration runs against read-only snapshot; live DB untouched until Phase 4 flip |
| 11 backup `.db` files in working tree confusing things | Move out of `backend/` before Phase 3 |
| Pytest gets slower (no in-memory SQLite) | Acceptable given "Postgres only" choice; revisit if it becomes painful |

## Coordination protocol for other sessions

Until this migration completes:
- **Do not** add ALTER TABLE statements to `_migrate()` in `db.py`. Any new
  schema work goes through an Alembic migration after Phase 0.
- **Do not** add `INSERT OR IGNORE`, `PRAGMA`, or other SQLite-specific
  SQL.
- **Do** flag concerns or conflicts in INTER_SESSION.md.
- **Do** check this doc before starting any DB-touching work.

---

## Status log

- 2026-05-27 — Phase 0 started. Plan locked. Tasks created.
