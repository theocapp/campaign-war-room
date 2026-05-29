# Phase 4 Cutover Runbook

**Target: SQLite war_room.db → Postgres noctua (local Pg 15)**

Last validated: 2026-05-27 — rehearsal #1 passed in ~8 seconds, all
validations green, backend smoke-tested against populated Postgres.

This document is the only thing you need open on cutover day. Every
command is copy-pasteable. Estimated total downtime: **5 minutes**
(less than that for the actual migration; most of the time is for
smoke tests).

## Prerequisites

Before running this, confirm:
- [ ] `POSTGRES_MIGRATION_PLAN.md` Phase 0–3 are all green in `INTER_SESSION.md`
- [ ] `backend/.env.bak-PRE-CUTOVER` exists (or you've taken a manual backup)
- [ ] Postgres 15 is running on localhost:5432 (`brew services list | grep postgres`)
- [ ] Database `noctua` exists (`psql -h localhost -d noctua -c '\dt' | head -5`)
- [ ] You have psql, the backend venv, and a terminal you can leave open

If any of these are unclear, **stop and re-verify** before continuing.

---

## Step 1 — Pre-flight (the day before, optional)

These are cheap dry runs against the rehearsal Postgres `noctua` DB.
Skip if Phase 3 rehearsal #1 was within the last 48 hours.

```bash
cd /Users/theo/noctua/backend

# Confirm SQLite is at expected alembic head
.venv/bin/alembic current
# Expect: bfbb065b4b7e (head) or whatever the latest revision is.

# Confirm Postgres noctua has the same schema (read-only check)
DATABASE_URL="postgresql+psycopg://theo@localhost:5432/noctua" \
  .venv/bin/alembic current
# Expect: same revision as above.

# Confirm preflight audit is clean (still read-only against SQLite)
.venv/bin/python scripts/preflight_audit.py
# Expect: "PASS: N, WARN: ≤13, FAIL: 0"
```

If FAIL count > 0, **stop**. Apply audit-fix migration first.

---

## Step 2 — Stop everything writing to the DB

The migration runs against a read-only snapshot, but the backend can't
be running against the SQLite source during the cutover or it'll keep
writing rows that aren't in our snapshot.

```bash
# 1. Find the backend / scheduler process
ps aux | grep -E "uvicorn app.main|app\.main" | grep -v grep
# Note the PID(s)

# 2. Stop the backend gracefully (SIGTERM so APScheduler can flush)
#    Replace <PID> with what you saw above
kill <PID>

# 3. Wait a few seconds, then confirm it's gone
ps aux | grep -E "uvicorn app.main" | grep -v grep
# Expect: no output

# 4. Stop any frontend dev server running against /api (optional —
#    affects UX but not data)
# pkill -f "vite|noctua frontend"
```

If you can't find the process, the backend may be running under a
launchd or systemd unit. Check `~/Library/LaunchAgents/` if so.

---

## Step 3 — Take a fresh SQLite snapshot

This is your insurance — the file you can roll back to if anything
goes sideways.

```bash
cd /Users/theo/noctua/backend
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SNAPSHOT_NAME="war_room.db.cutover-${TIMESTAMP}"
cp war_room.db "${SNAPSHOT_NAME}"
ls -la "${SNAPSHOT_NAME}"
# Expect: a copy ~112MB
```

**Write this filename down somewhere safe** — you'll need it if you
roll back, and you'll reference it from the post-mortem.

---

## Step 4 — Run the migration

```bash
cd /Users/theo/noctua/backend
TIMESTAMP=$(date +%Y%m%d-%H%M%S)  # if not set from step 3

# Truncate Postgres noctua (it has rehearsal data from earlier)
/opt/homebrew/opt/postgresql@15/bin/psql -h localhost -d noctua -c "
DO \$\$
DECLARE t text;
BEGIN
  FOR t IN SELECT table_name FROM information_schema.tables
           WHERE table_schema='public' AND table_name != 'alembic_version'
  LOOP
    EXECUTE 'TRUNCATE TABLE ' || quote_ident(t) || ' RESTART IDENTITY CASCADE';
  END LOOP;
END \$\$;
"

# Run the migration with --skip-orphans (the script handles FK orphans
# defensively; this is intentional)
.venv/bin/python scripts/sqlite_to_postgres.py \
    --src "sqlite:///$(pwd)/war_room.db" \
    --dst "postgresql+psycopg://theo@localhost:5432/noctua" \
    --skip-orphans 2>&1 | tee /tmp/cutover-${TIMESTAMP}.log
```

**Expected end of output:**
```
OVERALL: ✅ PASS
```

If it says `❌ FAIL`, **do not flip .env**. Read `/tmp/cutover-${TIMESTAMP}.log`,
identify what failed (probably a table count mismatch or a hash mismatch),
fix the underlying data issue in SQLite, then re-run from Step 4.

Expected wall-clock: 5–15 seconds. If it's still running after 60
seconds, something is wrong — check Postgres logs.

---

## Step 5 — Flip DATABASE_URL

```bash
cd /Users/theo/noctua

# Backup the .env so you can revert easily
cp .env .env.bak-PRE-CUTOVER

# Append the DATABASE_URL line (or edit by hand)
echo "" >> .env
echo "# Postgres cutover — $(date)" >> .env
echo "DATABASE_URL=postgresql+psycopg://theo@localhost:5432/noctua" >> .env

# Verify the new line is there
grep "^DATABASE_URL" .env
# Expect: DATABASE_URL=postgresql+psycopg://theo@localhost:5432/noctua
```

---

## Step 6 — Start the backend, watch the boot logs

```bash
cd /Users/theo/noctua/backend
.venv/bin/uvicorn app.main:app --reload 2>&1 | tee /tmp/cutover-boot-${TIMESTAMP}.log
```

**What to look for in the first 30 seconds:**

- ✅ `Running alembic upgrade head (target=postgresql+psycopg://...)` —
  confirms env-var was read correctly
- ✅ `INFO uvicorn.error: Application startup complete.`
- ✅ No `ERROR` or `ProgrammingError` lines

If you see `ProgrammingError`, it's a Postgres-only SQL incompatibility
we haven't caught yet. Note the error, then go to rollback (Step 9).

Leave this terminal running. Open a new terminal for the smoke tests.

---

## Step 7 — Smoke test checklist

In a new terminal:

```bash
BASE=http://localhost:8000/api

# 1. Health check
curl -s $BASE/system/scheduler-health | head -100
# Expect: JSON with last_success / last_skip times

# 2. DB stats — should show Postgres dialect
curl -s $BASE/admin/dbstats | python -m json.tool | head -20
# Expect:
#   "url_dialect": "postgresql"
#   "connections_in_use": 1+
#   "cache_hit_ratio": > 95

# 3. Search — try a known result
curl -s "$BASE/search?q=cognetti&limit=3" | python -m json.tool | head -20
# Expect: at least one result with "Cognetti" in title

# 4. Narrative frames
curl -s $BASE/narrative-frames | python -m json.tool | grep -c '"id"'
# Expect: 35

# 5. Morning briefing — slowest of the bunch
time curl -s $BASE/briefing/morning > /dev/null
# Expect: 200 in under 5s

# 6. Articles recent
curl -s $BASE/articles/recent?limit=5 | python -m json.tool | head -20
# Expect: dict with "items"
```

Now open the frontend:

- Visit `http://localhost:5174`
- Click Dashboard — should render
- Click Narratives — should show 35 frames
- Click Search bar, type "cognetti", press enter — should return results
- Click Briefing — should generate (or show cached)

If any of these fail, see Step 9 (rollback).

---

## Step 8 — Tail logs for the first hour

Keep the uvicorn terminal open. Watch for:

- ⚠️ Any `ProgrammingError` / `IntegrityError` (Postgres-only bug)
- ⚠️ `pool_stats` invalidations (connection problems)
- ⚠️ Postgres lock waits > 1 second (visible at `/admin/dbstats`)

Refresh `/admin/dbstats` periodically. Healthy:
- `connections_in_use` stays under 5 (we have a pool of 20)
- `lock_waiters` empty
- `slow_queries` mostly empty
- `cache_hit_ratio` > 95%

If anything looks bad and you can't diagnose in 5 minutes, rollback.

---

## Step 9 — Rollback (if needed)

Reverting is fast. The SQLite source was never modified.

```bash
# 1. Stop the Postgres-backed backend
# Find PID with: ps aux | grep uvicorn | grep -v grep
kill <PID>

# 2. Revert .env
cd /Users/theo/noctua
cp .env.bak-PRE-CUTOVER .env

# Verify DATABASE_URL is gone or commented
grep "^DATABASE_URL" .env
# Expect: no output (or a commented line)

# 3. Start the backend — it'll boot against SQLite as before
cd backend
.venv/bin/uvicorn app.main:app --reload
```

The SQLite war_room.db is exactly as it was — the migration was 100%
read-only against the source. No data loss.

If the rollback works: the migration uncovered something we missed.
Note the symptom, restore Postgres noctua to truncated state for the
next attempt, and investigate before retrying.

---

## Step 10 — Post-cutover monitoring (first day, first week)

**First hour after cutover** — watch live (Step 8 above).

**First 24 hours** — periodic checks:

- `/admin/dbstats` cache hit ratio stays > 90%
- No deadlocks reported in `pg_stat_database`
- Scheduler runs at expected cadence (RSS ingestion every 60min by default)
- Search returns results (different ranking from SQLite — that's expected)
- Frontend behaves normally

**First week** — confirm via `/admin/dbstats`:

- `lifetime_invalidations` stays low (< 10 unless there's a network issue)
- `cache_hit_ratio` settles above 99% after warm-up
- Pool size never approached `max_overflow` (60)

If all clean after 4 weeks (per POSTGRES_MIGRATION_PLAN.md Phase 5),
schedule the cleanup: delete the SQLite codepaths from db.py and
search_index.py, archive `backend/war_room.db*` files, update
CLAUDE.md to remove SQLite references.

---

## Quick reference: critical paths

- Pre-cutover SQLite snapshot: `backend/war_room.db.cutover-YYYYMMDD-HHMMSS`
- `.env` backup: `.env.bak-PRE-CUTOVER`
- Migration log: `/tmp/cutover-YYYYMMDD-HHMMSS.log`
- Boot log: `/tmp/cutover-boot-YYYYMMDD-HHMMSS.log`
- Postgres database: `noctua` on `localhost:5432`
- Alembic head expected: `bfbb065b4b7e` (or latest from `git log -- backend/alembic/versions/`)

## What's NOT in this runbook

- The kg_* table read-only archive migration (user chose to migrate
  kg_* as archive; that's a follow-up migration after Phase 4 stabilizes,
  not part of the cutover itself)
- Production hosting (Neon/Supabase/RDS) — explicit decision was to
  defer; runbook will be updated when a managed host is picked
- pg_stat_statements enabling — would require Postgres restart, affects
  4 other databases on this server; revisit if query-level perf becomes
  a real problem
