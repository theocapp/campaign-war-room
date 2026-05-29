"""Phase 3 — SQLite → Postgres data migration.

Reads a read-only SQLite snapshot and copies every active-table row into
a Postgres target, preserving primary keys. Validates checksums, row
counts, FK integrity, and aggregate sanity at the end.

Usage:
    python scripts/sqlite_to_postgres.py \\
        --src sqlite:////absolute/path/to/snapshot.db \\
        --dst "postgresql+psycopg://theo@localhost:5432/noctua" \\
        [--dry-run]            # show plan, don't write
        [--force]              # allow non-empty target (dangerous)
        [--limit-rows N]       # for testing — only copy N rows per table

Pre-conditions enforced at startup:
  - Source SQLite is at the same Alembic revision as the target Postgres.
  - Target Postgres is empty (override with --force).
  - Both DBs are reachable.

Behavior:
  - Tables walked in topological FK order (parents first).
  - Rows streamed in batches of 5000 to avoid loading whole tables into RAM.
  - Boolean values are cast to native bool (SQLite stores INTEGER 0/1).
  - DateTimes are parsed from ISO strings on the SQLite side.
  - JSON-as-text columns are passed through verbatim (Phase 1 decision —
    `jsonb` conversion is a separate follow-up).
  - After every table is loaded, the corresponding `<table>_id_seq` is
    bumped to MAX(id) so future Postgres inserts don't collide.
  - search_tsv on source_items is populated by the BEFORE INSERT trigger
    — no extra step needed.

Validation pass after data load:
  1. Row count match per table
  2. SHA256 of normalized rows per table (column subset common to both)
  3. 100 sampled deep-row diffs per table (random ids)
  4. FK integrity sweep on target (every FK resolves)
  5. Aggregate sanity: articles_per_outlet, mentions_per_frame, supports_per_claim

Exit codes:
  0 — clean migration, all validations passed
  1 — pre-condition or migration failure
  2 — validation failures (data copied but not byte-identical)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_SQLITE_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$"
)

from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy.engine import Engine

log = logging.getLogger("migrate")

# Topological FK order: every table's FK targets appear earlier in the list.
# Derived by walking ForeignKey declarations in models.py.
TABLE_ORDER: list[str] = [
    # Level 0 — no outbound FKs
    "campaign_config",
    "race_directory",
    "issues",
    "opponents",
    "rss_feeds",
    "source_monitors",
    "source_packs",
    "manual_source_reminders",
    "outlets",
    "narrative_frames",
    "entities",
    "google_trend_snapshots",
    "gdelt_tone_snapshots",
    "race_sentiment",
    "race_sentiment_snapshots",
    "entity_review_decisions",
    "topic_region_labels",
    "proposed_cluster_triage",
    # Level 1
    "race_candidates",         # → race_directory
    "source_pack_items",       # → source_packs
    "source_items",            # → outlets (nullable)
    "frame_variants",          # → narrative_frames
    "frame_stage_history",     # → narrative_frames
    "claims",                  # → entities ×2
    "entity_relations",        # → entities ×2
    # Level 2
    "issue_mentions",              # → issues, source_items
    "opponent_activities",         # → opponents, source_items
    "candidate_frames",            # → source_items, narrative_frames
    "entity_mentions",             # → source_items, entities
    "claim_records",               # → source_items
    "story_clusters",              # → source_items (×3)
    "claim_supports",              # → claims, source_items
    # Level 3
    "narrative_frame_mentions",    # → narrative_frames, source_items, frame_variants
    "frame_cluster_matches",       # → narrative_frames, story_clusters
    "cluster_opponent_activities", # → opponents, story_clusters
    "claim_record_entities",       # → claim_records, entities
]

BATCH_SIZE = 5000
SAMPLE_DEEP_DIFF_ROWS = 100

# Columns to skip when computing the row hash (dialect-specific or
# generated). Hashes only compare columns present on BOTH sides.
HASH_SKIP_COLUMNS: dict[str, set[str]] = {
    "source_items": {"search_tsv"},  # Postgres-only, computed by trigger
}

# FK pairs to sweep at the end. Mirrors preflight_audit.py but checks the
# target Postgres after data load.
FK_PAIRS: list[tuple[str, str, str, str]] = [
    ("issue_mentions", "issue_id", "issues", "id"),
    ("issue_mentions", "source_item_id", "source_items", "id"),
    ("opponent_activities", "opponent_id", "opponents", "id"),
    ("opponent_activities", "source_item_id", "source_items", "id"),
    ("race_candidates", "race_id", "race_directory", "id"),
    ("source_pack_items", "source_pack_id", "source_packs", "id"),
    ("source_items", "outlet_id", "outlets", "id"),
    ("frame_variants", "frame_id", "narrative_frames", "id"),
    ("frame_stage_history", "frame_id", "narrative_frames", "id"),
    ("narrative_frame_mentions", "frame_id", "narrative_frames", "id"),
    ("narrative_frame_mentions", "source_item_id", "source_items", "id"),
    ("narrative_frame_mentions", "variant_id", "frame_variants", "id"),
    ("story_clusters", "seed_source_item_id", "source_items", "id"),
    ("story_clusters", "representative_source_item_id", "source_items", "id"),
    ("story_clusters", "analysis_anchor_source_item_id", "source_items", "id"),
    ("frame_cluster_matches", "frame_id", "narrative_frames", "id"),
    ("frame_cluster_matches", "story_cluster_id", "story_clusters", "id"),
    ("cluster_opponent_activities", "opponent_id", "opponents", "id"),
    ("cluster_opponent_activities", "story_cluster_id", "story_clusters", "id"),
    ("candidate_frames", "source_item_id", "source_items", "id"),
    ("candidate_frames", "resolved_to_frame_id", "narrative_frames", "id"),
    ("entity_mentions", "article_id", "source_items", "id"),
    ("entity_mentions", "entity_id", "entities", "id"),
    ("claims", "subject_id", "entities", "id"),
    ("claims", "object_id", "entities", "id"),
    ("claim_supports", "claim_id", "claims", "id"),
    ("claim_supports", "article_id", "source_items", "id"),
    ("claim_records", "article_id", "source_items", "id"),
    ("claim_record_entities", "claim_record_id", "claim_records", "id"),
    ("claim_record_entities", "entity_id", "entities", "id"),
    ("entity_relations", "subject_id", "entities", "id"),
    ("entity_relations", "object_id", "entities", "id"),
]


@dataclass
class ValidationResult:
    table: str
    src_count: int = 0
    dst_count: int = 0
    src_hash: str = ""
    dst_hash: str = ""
    sample_size: int = 0
    sample_mismatches: list[dict] = field(default_factory=list)

    @property
    def counts_match(self) -> bool:
        return self.src_count == self.dst_count

    @property
    def hashes_match(self) -> bool:
        return self.src_hash == self.dst_hash and self.src_hash != ""

    @property
    def samples_match(self) -> bool:
        return not self.sample_mismatches

    @property
    def passed(self) -> bool:
        return self.counts_match and self.hashes_match and self.samples_match


# ─── core copy logic ──────────────────────────────────────────────────────

def _normalize_value(v: Any) -> str:
    """Map a column value to a canonical string so SQLite and Postgres
    produce the same byte sequence for the same logical value.

    The two main drifts to neutralize:
      - SQLite returns DateTime columns as ISO strings with a space
        separator ('2026-05-08 21:05:17.242424'); Postgres returns
        datetime objects. Both normalize to 'T'-separated isoformat with
        microseconds.
      - SQLite stores Boolean as INTEGER 0/1; Postgres returns Python
        bool. Both normalize to '0'/'1'.

    Returns a string. None becomes '\\0' so it doesn't collide with empty
    string.
    """
    if v is None:
        return "\x00"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, datetime):
        return v.isoformat(timespec="microseconds")
    if isinstance(v, str):
        # NUL byte stripping — the migration strips these to satisfy
        # Postgres TEXT, so the source SQLite value (with NUL) and the
        # target Postgres value (without NUL) need to hash equal.
        if "\x00" in v:
            v = v.replace("\x00", "")
        # SQLite datetime columns surface as strings like "2026-05-08 21:05:17"
        # or "2026-05-08 21:05:17.242424". Normalize to match Python's
        # datetime.isoformat() output so the hashes align.
        if _SQLITE_DATETIME_RE.match(v):
            s = v.replace(" ", "T", 1)
            if "." not in s:
                s += ".000000"
            else:
                # Pad / truncate microseconds to 6 digits
                date_part, micro = s.rsplit(".", 1)
                micro = (micro + "000000")[:6]
                s = f"{date_part}.{micro}"
            return s
        return v
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


def _hash_table(
    eng: Engine, table_name: str, columns: list[str], pk_col: str,
    exclude_pks: set | None = None,
) -> tuple[str, int]:
    """SHA256 of all rows ordered by primary key, hashed column-by-column
    using `_normalize_value`. Returns (hash, row_count).

    `exclude_pks` is the set of primary-key values to ignore — used when
    validating against a migration that intentionally skipped rows (FK
    orphans dropped via --skip-orphans). When excluding, the source-side
    hash drops those PKs so it matches the destination's narrower set.

    Streams via server-side cursor when possible so 17K+ row tables don't
    load fully into RAM.
    """
    h = hashlib.sha256()
    count = 0
    cols_sql = ", ".join(f'"{c}"' for c in columns)
    sql = f"SELECT {pk_col}, {cols_sql} FROM {table_name} ORDER BY {pk_col}"
    with eng.connect() as conn:
        if eng.dialect.name == "postgresql":
            conn = conn.execution_options(stream_results=True, yield_per=1000)
        result = conn.execute(text(sql))
        for row in result:
            pk_val = row[0]
            if exclude_pks and pk_val in exclude_pks:
                continue
            for v in row[1:]:
                h.update(_normalize_value(v).encode("utf-8"))
                h.update(b"\x1f")  # column separator
            h.update(b"\x1e")  # row separator
            count += 1
    return h.hexdigest(), count


def _table_columns(eng: Engine, table_name: str) -> list[str]:
    if eng.dialect.name == "sqlite":
        sql = f"PRAGMA table_info({table_name})"
        with eng.connect() as conn:
            return [r[1] for r in conn.execute(text(sql)).fetchall()]
    else:
        sql = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            ORDER BY ordinal_position
        """
        with eng.connect() as conn:
            return [r[0] for r in conn.execute(text(sql), {"t": table_name}).fetchall()]


def _pk_column(meta: MetaData, table_name: str) -> str:
    """Return the table's primary key column name. Most tables use 'id';
    story_clusters uses 'id' (string PK). Composite PKs aren't in our schema."""
    tbl = meta.tables[table_name]
    pk = list(tbl.primary_key.columns)
    if len(pk) != 1:
        raise RuntimeError(
            f"{table_name}: expected single-column PK, got {[c.name for c in pk]}"
        )
    return pk[0].name


def _is_dst_empty(dst_eng: Engine) -> bool:
    meta = MetaData()
    meta.reflect(bind=dst_eng)
    with dst_eng.connect() as conn:
        for name in TABLE_ORDER:
            if name not in meta.tables:
                continue
            n = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
            if n and n > 0:
                return False
    return True


def _alembic_revision(eng: Engine) -> str | None:
    try:
        with eng.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None


def _orphans_against_dst(
    src_eng: Engine, dst_eng: Engine, table_name: str,
) -> dict[str, set]:
    """Return {column: set(values_to_skip)} per FK column on this table.

    Considers a value "orphaned" if the parent row doesn't exist in the
    DESTINATION. This catches two cases:
      (a) Pure source orphans — FK target was deleted from SQLite long ago.
      (b) Cascading skips — we skipped the parent row earlier in this run
          because it had its own orphan FK.

    Both cases would cause a FK violation on Postgres if we tried to insert,
    so they're treated identically: skip the row.
    """
    fks_for_table = [
        (c, parent_t, parent_c)
        for child_t, c, parent_t, parent_c in FK_PAIRS
        if child_t == table_name
    ]
    if not fks_for_table:
        return {}

    # Pull distinct source values for each FK column once, then query the
    # destination in batches to see which exist.
    out: dict[str, set] = {}
    with src_eng.connect() as src_conn, dst_eng.connect() as dst_conn:
        for col, parent_t, parent_c in fks_for_table:
            src_values: set = {
                r[0] for r in src_conn.execute(
                    text(f"SELECT DISTINCT {col} FROM {table_name} "
                         f"WHERE {col} IS NOT NULL")
                )
            }
            if not src_values:
                continue
            # Batch the IN-list to avoid Postgres parameter limits.
            present: set = set()
            values_list = list(src_values)
            BATCH = 5000
            for i in range(0, len(values_list), BATCH):
                chunk = values_list[i:i + BATCH]
                placeholders = ", ".join(f":v{j}" for j in range(len(chunk)))
                params = {f"v{j}": v for j, v in enumerate(chunk)}
                rows = dst_conn.execute(
                    text(f"SELECT {parent_c} FROM {parent_t} "
                         f"WHERE {parent_c} IN ({placeholders})"),
                    params,
                ).fetchall()
                present.update(r[0] for r in rows)
            orphans = src_values - present
            if orphans:
                out[col] = orphans
    return out


def _copy_table(
    src_eng: Engine,
    dst_eng: Engine,
    table_name: str,
    *,
    dry_run: bool,
    limit_rows: int | None,
    skip_orphans: bool,
    skipped_pks_out: set | None = None,
) -> int:
    """Stream rows from src.{table} into dst.{table} preserving column values."""
    src_meta = MetaData()
    src_meta.reflect(bind=src_eng, only=[table_name])
    src_table = src_meta.tables[table_name]
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_eng, only=[table_name])
    dst_table = dst_meta.tables[table_name]
    pk_col = _pk_column(src_meta, table_name)

    # Only copy columns present in both. (search_tsv is Postgres-only and
    # populated by trigger; never copied from source.)
    src_cols = {c.name for c in src_table.columns}
    dst_cols = {c.name for c in dst_table.columns}
    common_cols = sorted(src_cols & dst_cols)

    def _is_bool(col) -> bool:
        # `c.type.python_type` raises NotImplementedError for dialect-specific
        # types like Postgres `tsvector`. Skip those — they're never bool.
        try:
            return col.type.python_type is bool
        except NotImplementedError:
            return False

    bool_cols = {c.name for c in dst_table.columns if _is_bool(c)}

    with src_eng.connect() as src_conn:
        total = src_conn.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar() or 0

    if limit_rows is not None:
        total = min(total, limit_rows)

    if total == 0:
        log.info(f"  {table_name}: 0 rows (skip)")
        return 0

    if dry_run:
        log.info(f"  {table_name}: would copy {total} rows (DRY)")
        return total

    pk_col_cached = pk_col  # for inner loop without re-resolving

    # Pre-scan for FK orphans against the DESTINATION. Catches both pure
    # source orphans and cascading skips (where the parent row was itself
    # skipped earlier in this run). Both cause FK violations on insert.
    orphan_fks = _orphans_against_dst(src_eng, dst_eng, table_name)
    if orphan_fks:
        total_orphans = sum(len(v) for v in orphan_fks.values())
        if skip_orphans:
            log.warning(
                "  %s: %d FK orphan reference(s) across columns %s — skipping affected rows",
                table_name, total_orphans, sorted(orphan_fks.keys()),
            )
        else:
            log.error(
                "  %s: %d FK orphan reference(s) across columns %s. "
                "Either clean the source (preflight_audit.py + Alembic fix) or pass --skip-orphans.",
                table_name, total_orphans, sorted(orphan_fks.keys()),
            )
            for col, pks in orphan_fks.items():
                log.error("    %s: %s%s",
                          col, sorted(pks)[:5], " ..." if len(pks) > 5 else "")
            raise RuntimeError(f"FK orphans in {table_name}; aborting")

    copied = 0
    skipped = 0
    offset = 0
    col_list_sql = ", ".join(f'"{c}"' for c in common_cols)
    while offset < total:
        batch_lim = min(BATCH_SIZE, total - offset)
        # We rely on PK ordering for deterministic streaming. (Tables with no
        # 'id' all have an integer PK we can use.)
        with src_eng.connect() as src_conn:
            rows = src_conn.execute(text(
                f"SELECT {col_list_sql} FROM {table_name} "
                f"ORDER BY {pk_col} LIMIT :lim OFFSET :off"
            ), {"lim": batch_lim, "off": offset}).fetchall()

        # Type coercion: SQLite booleans come as 0/1 ints; Postgres needs bool.
        # NUL byte stripping: 47 rows across (source_items.raw_text,
        # source_items.summary, story_clusters.summary_representative) have
        # embedded U+0000 from web-scrape contamination. SQLite tolerates,
        # Postgres TEXT rejects. Stripped here, not modified in SQLite —
        # source DB stays read-only.
        # FK orphan filtering: if --skip-orphans, drop rows whose FK points
        # at a row that doesn't exist in the source. We pre-scanned the
        # orphan IDs above.
        payload: list[dict] = []
        for r in rows:
            d = {col: r[i] for i, col in enumerate(common_cols)}

            if orphan_fks:
                bad = False
                for fk_col, bad_ids in orphan_fks.items():
                    if d.get(fk_col) in bad_ids:
                        bad = True
                        break
                if bad:
                    skipped += 1
                    if skipped_pks_out is not None:
                        skipped_pks_out.add(d.get(pk_col_cached))
                    continue

            for bc in bool_cols:
                if bc in d and d[bc] is not None:
                    d[bc] = bool(d[bc])
            for k, v in d.items():
                if isinstance(v, str) and "\x00" in v:
                    d[k] = v.replace("\x00", "")
            payload.append(d)

        if payload:
            with dst_eng.begin() as dst_conn:
                dst_conn.execute(dst_table.insert(), payload)

        copied += len(payload)
        offset += batch_lim
        if total > BATCH_SIZE and copied % (BATCH_SIZE * 4) == 0:
            log.info(f"    {table_name}: {copied}/{total}")
    if skipped:
        log.warning(f"  {table_name}: {copied} copied, {skipped} skipped (FK orphans)")
    else:
        log.info(f"  {table_name}: {copied} rows copied")
    return copied


def _reset_sequences(dst_eng: Engine) -> None:
    """After loading rows with explicit IDs, bump each Postgres SERIAL
    sequence to MAX(id) so future inserts don't collide.

    Skips string-PK tables (story_clusters) — no sequence on those.
    """
    meta = MetaData()
    meta.reflect(bind=dst_eng)
    with dst_eng.begin() as conn:
        for tbl in meta.tables.values():
            pk_cols = list(tbl.primary_key.columns)
            if len(pk_cols) != 1:
                continue
            pk = pk_cols[0]
            if pk.type.python_type is not int:
                continue  # story_clusters etc.
            seq_name = f"{tbl.name}_{pk.name}_seq"
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.sequences "
                "WHERE sequence_schema='public' AND sequence_name=:n"
            ), {"n": seq_name}).fetchone()
            if not exists:
                continue
            max_id = conn.execute(
                text(f"SELECT MAX({pk.name}) FROM {tbl.name}")
            ).scalar()
            if max_id is None:
                continue
            conn.execute(
                text(f"SELECT setval(:s, :v, true)"),
                {"s": seq_name, "v": int(max_id)},
            )
            log.info(f"    {seq_name} -> {max_id}")


# ─── validation ────────────────────────────────────────────────────────────

def _validate_table(
    src_eng: Engine, dst_eng: Engine, table_name: str,
    excluded_pks: set | None = None,
) -> ValidationResult:
    src_cols = set(_table_columns(src_eng, table_name))
    dst_cols = set(_table_columns(dst_eng, table_name))
    skip = HASH_SKIP_COLUMNS.get(table_name, set())
    common = sorted((src_cols & dst_cols) - skip)

    src_meta = MetaData()
    src_meta.reflect(bind=src_eng, only=[table_name])
    pk_col = _pk_column(src_meta, table_name)

    result = ValidationResult(table=table_name)

    # Source hash excludes rows we intentionally skipped (FK orphans). They
    # don't exist in dst, so their absence isn't a real mismatch.
    src_hash, src_count = _hash_table(
        src_eng, table_name, common, pk_col, exclude_pks=excluded_pks,
    )
    dst_hash, dst_count = _hash_table(dst_eng, table_name, common, pk_col)
    result.src_count = src_count
    result.dst_count = dst_count
    result.src_hash = src_hash
    result.dst_hash = dst_hash

    if src_count > 0 and src_count == dst_count:
        result.sample_mismatches = _sample_deep_diff(
            src_eng, dst_eng, table_name, common, pk_col, src_count,
            exclude_pks=excluded_pks,
        )
        result.sample_size = min(SAMPLE_DEEP_DIFF_ROWS, src_count)

    return result


def _sample_deep_diff(
    src_eng: Engine, dst_eng: Engine, table: str, cols: list[str],
    pk_col: str, total: int, exclude_pks: set | None = None,
) -> list[dict]:
    """Pick up to 100 random rows by PK and diff column-by-column."""
    n = min(SAMPLE_DEEP_DIFF_ROWS, total)
    # Pick representative IDs via a uniform stride. This is deterministic
    # (random.seed) so the sample is reproducible across re-runs.
    rnd = random.Random(0xCAFE)
    cols_sql = ", ".join(f'"{c}"' for c in cols)

    # Get all PKs from src in order, then sample. Exclude rows that we
    # intentionally skipped during migration.
    with src_eng.connect() as conn:
        all_pks = [
            r[0] for r in conn.execute(
                text(f"SELECT {pk_col} FROM {table} ORDER BY {pk_col}")
            )
            if not (exclude_pks and r[0] in exclude_pks)
        ]
    sample_pks = rnd.sample(all_pks, n) if len(all_pks) > n else all_pks

    mismatches: list[dict] = []
    for pk_val in sample_pks:
        src_row = _fetch_row(src_eng, table, cols_sql, pk_col, pk_val)
        dst_row = _fetch_row(dst_eng, table, cols_sql, pk_col, pk_val)
        if src_row is None and dst_row is None:
            continue
        if src_row is None or dst_row is None:
            mismatches.append({
                "pk": pk_val, "missing_on": "dst" if dst_row is None else "src",
            })
            continue
        for i, col in enumerate(cols):
            s = _normalize_value(src_row[i])
            d = _normalize_value(dst_row[i])
            if s != d:
                mismatches.append({
                    "pk": pk_val, "column": col,
                    "src": s[:80], "dst": d[:80],
                })
                break  # one mismatch per row is enough
    return mismatches


def _fetch_row(eng: Engine, table: str, cols_sql: str, pk_col: str, pk_val):
    with eng.connect() as conn:
        return conn.execute(
            text(f"SELECT {cols_sql} FROM {table} WHERE {pk_col} = :v"),
            {"v": pk_val},
        ).fetchone()


def _fk_sweep(dst_eng: Engine) -> list[dict]:
    """Every FK in the target must resolve. Returns one entry per FK with
    orphan count."""
    out: list[dict] = []
    with dst_eng.connect() as conn:
        for child_t, child_c, parent_t, parent_c in FK_PAIRS:
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {child_t} c "
                f"WHERE c.{child_c} IS NOT NULL "
                f"  AND NOT EXISTS (SELECT 1 FROM {parent_t} p "
                f"                  WHERE p.{parent_c} = c.{child_c})"
            )).scalar()
            out.append({
                "child": f"{child_t}.{child_c}",
                "parent": f"{parent_t}.{parent_c}",
                "orphans": n or 0,
            })
    return out


def _aggregate_sanity(src_eng: Engine, dst_eng: Engine) -> list[dict]:
    """Cross-cut summaries that should match exactly."""
    checks: list[tuple[str, str]] = [
        ("articles_with_outlet",
         "SELECT COUNT(*) FROM source_items WHERE outlet_id IS NOT NULL"),
        ("frames_total",
         "SELECT COUNT(*) FROM narrative_frames"),
        ("mentions_per_frame_max",
         "SELECT MAX(c) FROM (SELECT COUNT(*) c FROM narrative_frame_mentions GROUP BY frame_id) sub"),
        ("entities_seeded",
         "SELECT COUNT(*) FROM entities WHERE seeded = 1"),  # SQLite syntax
        ("clusters_total",
         "SELECT COUNT(*) FROM story_clusters"),
        ("opponents_with_fec",
         "SELECT COUNT(*) FROM opponents WHERE fec_candidate_id IS NOT NULL"),
    ]
    out: list[dict] = []
    with src_eng.connect() as s, dst_eng.connect() as d:
        for label, sql in checks:
            # Postgres boolean cast — `seeded = 1` works in SQLite but not Pg
            sql_dst = sql.replace("= 1", "= true")
            try:
                sv = s.execute(text(sql)).scalar()
                dv = d.execute(text(sql_dst)).scalar()
            except Exception as e:
                out.append({"check": label, "error": str(e)})
                continue
            out.append({"check": label, "src": sv, "dst": dv,
                        "match": sv == dv})
    return out


def _print_report(
    results: list[ValidationResult],
    fk_sweep: list[dict],
    aggregates: list[dict],
    timings: dict[str, tuple[int, float]],
) -> bool:
    print("\n" + "=" * 70)
    print("MIGRATION VALIDATION REPORT")
    print("=" * 70 + "\n")

    print(f"{'TABLE':<35s} {'SRC':>10s} {'DST':>10s} {'COUNT':>6s} {'HASH':>6s} {'SAMPLE':>8s}")
    print("-" * 80)
    all_passed = True
    for r in results:
        c = "OK" if r.counts_match else "FAIL"
        h = "OK" if r.hashes_match else "FAIL"
        s = "OK" if r.samples_match else "FAIL"
        if not r.passed:
            all_passed = False
        print(f"{r.table:<35s} {r.src_count:>10d} {r.dst_count:>10d} "
              f"{c:>6s} {h:>6s} {s:>8s}")

    print("\nWall-clock per table (top 10):")
    for name, (n, secs) in sorted(timings.items(),
                                   key=lambda kv: -kv[1][1])[:10]:
        print(f"  {name:<35s} {n:>6d} rows  {secs:>6.1f}s")

    print("\nFK INTEGRITY SWEEP (target Postgres):")
    fk_clean = True
    for r in fk_sweep:
        status = "OK" if r["orphans"] == 0 else "FAIL"
        if r["orphans"] > 0:
            fk_clean = False
        print(f"  {status:>4s}  {r['child']:<45s} → {r['parent']:<35s} "
              f"{r['orphans']} orphan(s)")
    if not fk_clean:
        all_passed = False

    print("\nAGGREGATE SANITY:")
    agg_clean = True
    for r in aggregates:
        if "error" in r:
            print(f"  ERR   {r['check']:<35s} {r['error']}")
            agg_clean = False
            continue
        ok = r["match"]
        if not ok:
            agg_clean = False
        print(f"  {'OK' if ok else 'FAIL':>4s}  {r['check']:<35s} "
              f"src={r['src']}  dst={r['dst']}")
    if not agg_clean:
        all_passed = False

    print("\n" + "=" * 70)
    print("OVERALL:", "✅ PASS" if all_passed else "❌ FAIL")
    print("=" * 70)
    return all_passed


# ─── main ──────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", required=True,
                        help="SQLite source URL, e.g. sqlite:///path/to/snapshot.db")
    parser.add_argument("--dst", required=True,
                        help="Postgres target URL")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan, don't write")
    parser.add_argument("--force", action="store_true",
                        help="Allow non-empty target (DESTRUCTIVE — appends)")
    parser.add_argument("--limit-rows", type=int, default=None,
                        help="Only copy first N rows per table (testing)")
    parser.add_argument("--skip-orphans", action="store_true",
                        help="Drop rows whose FKs don't resolve in the source. "
                             "Without this flag, FK orphans abort the migration "
                             "so they get noticed and fixed via Alembic. With "
                             "this flag, orphan rows are logged and skipped. "
                             "Use for rehearsals; clean up source before real cutover.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Source is treated as read-only by convention — script never writes.
    # Pass a SQLite file URL; we don't bother with `?mode=ro` because
    # SQLAlchemy URL parsing complicates it and the snapshot path is the
    # caller's responsibility.
    src_eng = create_engine(
        args.src, connect_args={"check_same_thread": False}
    )
    dst_eng = create_engine(args.dst)

    # Pre-flight
    src_rev = _alembic_revision(src_eng)
    dst_rev = _alembic_revision(dst_eng)
    log.info("SRC alembic head: %s", src_rev)
    log.info("DST alembic head: %s", dst_rev)
    if src_rev != dst_rev or src_rev is None:
        log.error("Alembic revisions don't match. Run `alembic upgrade head` "
                  "on both first.")
        return 1
    if not args.force and not _is_dst_empty(dst_eng):
        log.error("DST is non-empty. Use --force to append (DANGEROUS).")
        return 1

    log.info("Starting copy — %d tables, batch=%d", len(TABLE_ORDER), BATCH_SIZE)
    timings: dict[str, tuple[int, float]] = {}
    skipped_per_table: dict[str, set] = {}
    for table in TABLE_ORDER:
        t0 = time.time()
        skipped_pks: set = set()
        try:
            n = _copy_table(src_eng, dst_eng, table,
                            dry_run=args.dry_run, limit_rows=args.limit_rows,
                            skip_orphans=args.skip_orphans,
                            skipped_pks_out=skipped_pks)
        except Exception:
            log.exception("FAILED on %s", table)
            return 1
        timings[table] = (n, time.time() - t0)
        if skipped_pks:
            skipped_per_table[table] = skipped_pks

    if args.dry_run:
        log.info("DRY RUN complete — no validation, no sequence reset")
        return 0

    log.info("Resetting Postgres sequences to MAX(id)+1")
    _reset_sequences(dst_eng)

    log.info("Validating …")
    results: list[ValidationResult] = []
    for table in TABLE_ORDER:
        excluded = skipped_per_table.get(table)
        results.append(_validate_table(src_eng, dst_eng, table,
                                       excluded_pks=excluded))
        r = results[-1]
        verdict = "OK" if r.passed else "FAIL"
        log.info("  %s %s  src=%d dst=%d", verdict, table, r.src_count, r.dst_count)

    fk_sweep = _fk_sweep(dst_eng)
    aggregates = _aggregate_sanity(src_eng, dst_eng)
    all_passed = _print_report(results, fk_sweep, aggregates, timings)

    return 0 if all_passed else 2


if __name__ == "__main__":
    sys.exit(main())
