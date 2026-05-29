"""Phase 0.5 — preflight data audit before SQLite → Postgres migration.

Read-only audit of the live SQLite DB. Surfaces every place SQLite's
permissive typing has accumulated dirt that Postgres's stricter validation
will reject during data migration.

Checks (all read-only):
  1. JSON validity        — every TEXT column documented as JSON parses
  2. FK integrity         — every FK column resolves to an existing parent
  3. Unique constraints   — no duplicates for any UniqueConstraint defined in models.py
  4. Boolean coherence    — Boolean columns hold only 0/1 (not strings)
  5. Datetime sanity      — DateTime columns are ISO-parseable
  6. NOT NULL violations  — no NULLs in nullable=False columns
  7. Enum drift           — documented enum columns hold only documented values
  8. UTF-8 sanity         — title/raw_text/summary contain valid UTF-8
  9. Oversized payloads   — flag rows with text >1MB
 10. NUL bytes            — embedded U+0000 in TEXT columns (Postgres rejects)

Outputs:
  backend/scripts/_audit_report.json   — machine-readable, one entry per finding
  backend/scripts/_audit_report.md     — human summary, grouped by severity

Exit codes:
  0 — no FAILs, no WARNs (clean)
  1 — one or more FAILs (blocks Phase 1)
  2 — WARNs only (proceed-with-eyes-open)

Usage:
    cd backend && .venv/bin/python scripts/preflight_audit.py
    cd backend && .venv/bin/python scripts/preflight_audit.py --db /path/to/other.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "war_room.db"
REPORT_DIR = Path(__file__).resolve().parent

# Columns documented as JSON-encoded text. (table, column) → expected JSON shape
# ("array", "object", or "either"). Used only for descriptive output; the audit
# just checks that the value parses.
JSON_COLUMNS: list[tuple[str, str, str]] = [
    ("campaign_config", "neighborhood_keywords", "array"),
    ("campaign_config", "key_priorities", "array"),
    ("campaign_config", "relevance_keywords", "array"),
    ("campaign_config", "excluded_keywords", "array"),
    ("campaign_config", "geography_keywords", "array"),
    ("campaign_config", "trends_keywords", "array"),
    ("source_items", "relevance_reasons", "array"),
    ("source_items", "extraction_quality_reasons", "array"),
    ("source_items", "gdelt_themes", "array"),
    ("source_items", "structured_extraction", "object"),
    ("source_items", "gdelt_tone", "object"),
    ("issue_mentions", "link_reasons", "array"),
    ("narrative_frame_mentions", "claim_meta", "object"),
    ("narrative_frame_mentions", "quote_embedding", "array"),
    ("narrative_frames", "momentum_data", "object"),
    ("frame_variants", "centroid_embedding", "array"),
    ("frame_stage_history", "metrics_snapshot", "object"),
    ("topic_region_labels", "member_frame_ids_json", "array"),
    ("proposed_cluster_triage", "member_candidate_frame_ids_json", "array"),
    ("outlets", "districts", "array"),
    ("entities", "aliases", "array"),
    ("entities", "metadata_json", "object"),
    ("entity_relations", "source_articles", "array"),
    ("entity_relations", "evidence_json", "array"),
    ("race_sentiment", "external_metadata", "object"),
    ("race_sentiment_snapshots", "raw_response", "object"),
    ("story_clusters", "known_entities", "array"),
    ("story_clusters", "structured_extraction", "object"),
]

# Foreign-key checks: (child_table, child_col, parent_table, parent_col)
# Mirrors ForeignKey declarations in models.py.
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

# Unique constraints to check: (table, cols, name).
UNIQUE_CHECKS: list[tuple[str, tuple[str, ...], str]] = [
    ("race_directory", ("race_key",), "race_key UNIQUE"),
    ("issues", ("name",), "issue name UNIQUE"),
    ("rss_feeds", ("url",), "feed url UNIQUE"),
    ("outlets", ("domain",), "outlet domain UNIQUE"),
    ("entities", ("canonical_id",), "entity canonical_id UNIQUE"),
    ("entity_mentions", ("article_id", "entity_id"), "uq_entity_mention_article_entity"),
    ("claims", ("subject_id", "predicate", "object_id"), "uq_claim_triple"),
    ("claim_supports", ("claim_id", "article_id"), "uq_claim_support_pair"),
    ("claim_records", ("evidence_hash",), "uq_claim_record_evidence_hash"),
    ("claim_record_entities", ("claim_record_id", "entity_id"), "uq_claim_record_entity_pair"),
    ("entity_relations", ("subject_id", "predicate", "object_id"), "uq_entity_relation_triple"),
    ("entity_review_decisions", ("item_type", "item_key"), "uq_entity_review_decision_item"),
    ("narrative_frame_mentions", ("frame_id", "source_item_id"), "uq_nfm_frame_source"),
    ("frame_cluster_matches", ("frame_id", "story_cluster_id"), "uq_frame_cluster"),
    ("cluster_opponent_activities",
     ("opponent_id", "story_cluster_id", "fingerprint"), "uq_cluster_opponent_fp"),
    ("google_trend_snapshots", ("term", "snapshot_date", "geo"), "uq_google_trend_daily"),
    ("gdelt_tone_snapshots", ("query_label", "snapshot_date"), "uq_gdelt_tone_daily"),
    ("race_sentiment", ("source",), "race_sentiment source UNIQUE"),
    ("race_sentiment_snapshots", ("source", "captured_at"), "uq_race_sentiment_snapshot"),
]

# Boolean columns (SQLAlchemy Boolean → SQLite INTEGER 0/1).
BOOLEAN_COLUMNS: list[tuple[str, str]] = [
    ("campaign_config", "sparse_race_mode"),
    ("campaign_config", "historical_backfill_completed"),
    ("campaign_config", "extended_backfill_completed"),
    ("race_directory", "is_active"),
    ("race_candidates", "is_incumbent"),
    ("source_items", "reviewed"),
    ("source_items", "dismissed"),
    ("source_items", "candidate_mentioned"),
    ("source_items", "opponent_mentioned"),
    ("source_items", "district_mentioned"),
    ("source_items", "priority_issue_mentioned"),
    ("source_items", "archived_as_irrelevant"),
    ("rss_feeds", "active"),
    ("source_monitors", "active"),
    ("source_pack_items", "active"),
    ("manual_source_reminders", "active"),
    ("outlets", "active"),
    ("narrative_frames", "active"),
    ("entities", "seeded"),
    ("topic_region_labels", "edited_by_user"),
]

# Documented enum-like columns. None as a value in `allowed` means NULL is acceptable.
ENUM_COLUMNS: list[tuple[str, str, list[Any]]] = [
    ("source_items", "urgency", ["low", "medium", "high"]),
    ("source_items", "race_relevance_label",
     ["irrelevant", "low", "medium", "high", None]),
    ("source_items", "source_owner_type",
     ["unclear", "candidate", "opponent", "media", None]),
    ("source_items", "source_owner_confidence", ["low", "medium", "high", None]),
    ("source_items", "actionability_label",
     ["ignore", "low", "medium", "high", None]),
    ("source_items", "geo_relevance",
     ["none", "district", "state", "national", "local", None]),
    ("source_items", "extraction_quality_label",
     ["good", "medium", "poor", None]),
    ("source_items", "source_credibility", ["high", "medium", "low", None]),
    ("source_items", "perspective",
     ["pro_candidate", "pro_opponent", "neutral", None]),
    ("source_items", "perspective_method",
     ["existing", "outlet_bias", "attribution", "llm", "fallback", None]),
    ("source_items", "perspective_confidence", ["high", "medium", "low", None]),
    ("source_items", "sentiment",
     ["positive", "negative", "neutral", "mixed", None]),
    ("source_items", "content_category",
     ["irrelevant", "candidate_news", "opponent_news", "race_news",
      "policy", "election_admin", "endorsement", "other", None]),
    ("issues", "urgency", ["low", "medium", "high"]),
    ("issues", "trend", ["rising", "stable", "falling"]),
    ("narrative_frames", "owner_type", ["candidate", "opponent", "media"]),
    ("narrative_frames", "subject_type",
     ["candidate", "opponent", "media", None]),
    ("narrative_frames", "source", ["human", "llm"]),
    ("narrative_frames", "momentum_signal",
     ["viral", "missing_coverage", "elite_only", "stable", None]),
    ("narrative_frame_mentions", "matched_by", ["llm", "human"]),
    ("frame_cluster_matches", "matched_by", ["llm", "human"]),
    ("frame_cluster_matches", "source_type",
     ["cluster_runtime", "cluster_backfill", "cluster_retrigger"]),
    ("cluster_opponent_activities", "source_type",
     ["cluster_runtime", "cluster_backfill", "cluster_retrigger"]),
    ("entities", "type",
     ["person", "organization", "bill", "location", "issue", "event"]),
    ("entity_mentions", "confidence", ["high", "medium", "low"]),
    ("entity_mentions", "extraction_method",
     ["seed", "alias", "embedding", "llm"]),
    ("entity_relations", "predicate",
     ["endorses", "criticizes", "attacks", "voted_for", "voted_against",
      "co_sponsored", "represents", "member_of", "attended", "donated_to",
      "predecessor_of"]),
    ("entity_relations", "confidence", ["high", "medium", "low"]),
    ("entity_review_decisions", "decision", ["approve", "reject", "skip"]),
    ("claims", "status", ["active", "contested", "retracted"]),
    ("claims", "confidence", ["high", "medium", "low"]),
    ("claim_supports", "stance", ["supporting", "contesting"]),
    ("claim_supports", "confidence", ["high", "medium", "low"]),
    ("claim_records", "confidence", ["high", "medium", "low"]),
    ("claim_records", "label",
     ["statement", "attack", "defense", "endorsement", "policy_position",
      "vote", "announcement", "commitment", None]),
    ("race_sentiment", "source_type", ["market", "rating"]),
    ("race_sentiment_snapshots", "source_type", ["market", "rating"]),
    ("source_monitors", "monitor_type",
     ["rss", "search_query", "manual", "webpage"]),
    ("proposed_cluster_triage", "verdict",
     ["auto_reject", "auto_merge", "auto_promote_suggested", "human_review"]),
]

# NOT NULL columns (mostly auto-handled by NOT NULL constraint; this catches
# cases where SQLite tolerated NULLs that were inserted via raw SQL).
NOT_NULL_COLUMNS: list[tuple[str, str]] = [
    ("source_items", "title"),
    ("source_items", "source_type"),
    ("entities", "canonical_id"),
    ("entities", "type"),
    ("entities", "name"),
    ("claims", "subject_id"),
    ("claims", "predicate"),
    ("claims", "object_id"),
    ("claim_records", "article_id"),
    ("claim_records", "evidence_span"),
    ("claim_records", "evidence_hash"),
    ("claim_records", "confidence"),
    ("entity_relations", "subject_id"),
    ("entity_relations", "predicate"),
    ("entity_relations", "object_id"),
    ("entity_relations", "confidence"),
    ("narrative_frames", "name"),
    ("opponents", "name"),
]

DATETIME_COLUMNS: list[tuple[str, str]] = [
    ("source_items", "published_at"),
    ("source_items", "ingested_at"),
    ("source_items", "created_at"),
    ("narrative_frames", "created_at"),
    ("narrative_frames", "updated_at"),
    ("narrative_frame_mentions", "created_at"),
    ("story_clusters", "first_seen_at"),
    ("story_clusters", "last_seen_at"),
    ("story_clusters", "created_at"),
    ("story_clusters", "updated_at"),
    ("frame_cluster_matches", "first_seen_at"),
    ("frame_cluster_matches", "last_seen_at"),
    ("entities", "first_seen"),
    ("entities", "last_seen"),
    ("claims", "first_seen"),
    ("claims", "last_seen"),
    ("entity_relations", "valid_from"),
    ("entity_relations", "valid_to"),
    ("race_sentiment_snapshots", "captured_at"),
]

# Text columns where we look for invalid UTF-8 (rare, but catches any binary
# that snuck in via web-scrape encoding errors).
UTF8_COLUMNS: list[tuple[str, str]] = [
    ("source_items", "title"),
    ("source_items", "raw_text"),
    ("source_items", "summary"),
]

# Oversized payload threshold — 1 MB. Postgres handles this fine but it slows
# down indexing and copy.
OVERSIZE_BYTES = 1_000_000

# Columns to scan for embedded NUL bytes (U+0000). SQLite tolerates these;
# Postgres TEXT rejects them. The migration script strips NULs from every
# string column defensively, so this is a WARN (not a FAIL). Surfacing the
# count lets us validate that the migration's silent NUL-handling didn't drop
# anything significant. Rehearsal #1 (2026-05-27) found 47 affected rows
# across source_items.raw_text (35), source_items.summary (3), and
# story_clusters.summary_representative (9).
NUL_BYTE_COLUMNS: list[tuple[str, str]] = [
    ("source_items", "title"),
    ("source_items", "raw_text"),
    ("source_items", "summary"),
    ("story_clusters", "title_representative"),
    ("story_clusters", "summary_representative"),
    ("narrative_frames", "description"),
    ("narrative_frame_mentions", "extracted_text"),
    ("claim_records", "evidence_span"),
]


@dataclass
class Finding:
    severity: str  # PASS | WARN | FAIL
    category: str
    description: str
    detail: dict = field(default_factory=dict)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def audit_json_columns(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col, shape in JSON_COLUMNS:
        if not _table_exists(conn, table):
            findings.append(Finding(
                "WARN", "json", f"{table}.{col} skipped (table missing)",
                {"table": table, "column": col},
            ))
            continue
        bad: list[dict] = []
        wrong_shape: list[dict] = []
        cur = conn.execute(
            f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL AND {col} != ''"
        )
        for row_id, raw in cur:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                bad.append({"id": row_id, "error": str(e), "sample": raw[:80]})
                continue
            if shape == "array" and not isinstance(parsed, list):
                wrong_shape.append({"id": row_id, "got": type(parsed).__name__})
            elif shape == "object" and not isinstance(parsed, dict):
                wrong_shape.append({"id": row_id, "got": type(parsed).__name__})
        if bad:
            findings.append(Finding(
                "FAIL", "json",
                f"{table}.{col}: {len(bad)} rows fail JSON parse",
                {"table": table, "column": col, "rows": bad[:10],
                 "total_bad": len(bad)},
            ))
        if wrong_shape:
            findings.append(Finding(
                "WARN", "json",
                f"{table}.{col}: {len(wrong_shape)} rows have wrong JSON shape "
                f"(expected {shape})",
                {"table": table, "column": col, "rows": wrong_shape[:10]},
            ))
        if not bad and not wrong_shape:
            findings.append(Finding(
                "PASS", "json", f"{table}.{col} all rows parse",
                {"table": table, "column": col},
            ))
    return findings


def audit_fk_integrity(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for child_t, child_c, parent_t, parent_c in FK_PAIRS:
        if not _table_exists(conn, child_t) or not _table_exists(conn, parent_t):
            findings.append(Finding(
                "WARN", "fk", f"{child_t}.{child_c} → {parent_t}.{parent_c} skipped (missing table)",
                {"child_table": child_t, "child_column": child_c,
                 "parent_table": parent_t},
            ))
            continue
        orphans = conn.execute(
            f"SELECT COUNT(*) FROM {child_t} c "
            f"WHERE c.{child_c} IS NOT NULL "
            f"  AND NOT EXISTS (SELECT 1 FROM {parent_t} p WHERE p.{parent_c} = c.{child_c})"
        ).fetchone()[0]
        if orphans:
            sample = [r[0] for r in conn.execute(
                f"SELECT c.id FROM {child_t} c "
                f"WHERE c.{child_c} IS NOT NULL "
                f"  AND NOT EXISTS (SELECT 1 FROM {parent_t} p WHERE p.{parent_c} = c.{child_c}) "
                f"LIMIT 10"
            ).fetchall()]
            findings.append(Finding(
                "FAIL", "fk",
                f"{child_t}.{child_c} → {parent_t}.{parent_c}: {orphans} orphan(s)",
                {"child_table": child_t, "child_column": child_c,
                 "parent_table": parent_t, "orphan_count": orphans,
                 "sample_child_ids": sample},
            ))
        else:
            findings.append(Finding(
                "PASS", "fk",
                f"{child_t}.{child_c} → {parent_t}.{parent_c} all resolve",
                {"child_table": child_t, "child_column": child_c,
                 "parent_table": parent_t},
            ))
    return findings


def audit_unique_constraints(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, cols, name in UNIQUE_CHECKS:
        if not _table_exists(conn, table):
            findings.append(Finding(
                "WARN", "unique", f"{name} skipped (table {table} missing)",
                {"table": table, "constraint": name},
            ))
            continue
        col_list = ", ".join(cols)
        # Treat NULLs as not violating UNIQUE (matches Postgres NULL semantics).
        where = " AND ".join(f"{c} IS NOT NULL" for c in cols)
        dups = conn.execute(
            f"SELECT {col_list}, COUNT(*) c FROM {table} "
            f"WHERE {where} GROUP BY {col_list} HAVING c > 1 LIMIT 10"
        ).fetchall()
        if dups:
            findings.append(Finding(
                "FAIL", "unique",
                f"{table} {name}: {len(dups)} duplicate group(s)",
                {"table": table, "constraint": name, "columns": list(cols),
                 "sample_duplicates": [list(d) for d in dups]},
            ))
        else:
            findings.append(Finding(
                "PASS", "unique", f"{table} {name} clean",
                {"table": table, "constraint": name},
            ))
    return findings


def audit_booleans(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col in BOOLEAN_COLUMNS:
        if not _table_exists(conn, table):
            continue
        bad = conn.execute(
            f"SELECT id, {col} FROM {table} "
            f"WHERE {col} IS NOT NULL "
            f"  AND CAST({col} AS INTEGER) NOT IN (0, 1) "
            f"LIMIT 10"
        ).fetchall()
        if bad:
            findings.append(Finding(
                "FAIL", "boolean",
                f"{table}.{col}: {len(bad)} non-0/1 row(s)",
                {"table": table, "column": col,
                 "samples": [{"id": r[0], "value": r[1]} for r in bad]},
            ))
        else:
            findings.append(Finding(
                "PASS", "boolean", f"{table}.{col} only 0/1/NULL",
                {"table": table, "column": col},
            ))
    return findings


def audit_datetimes(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col in DATETIME_COLUMNS:
        if not _table_exists(conn, table):
            continue
        bad: list[dict] = []
        cur = conn.execute(
            f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL"
        )
        for row_id, raw in cur:
            if not isinstance(raw, str):
                # SQLite may return native datetime when detect_types is used;
                # we open without that, so it's almost always str. Tolerate.
                continue
            try:
                # SQLAlchemy stores as "YYYY-MM-DD HH:MM:SS[.ffffff]" or similar.
                datetime.fromisoformat(raw.replace("Z", "+00:00").rstrip())
            except ValueError as e:
                bad.append({"id": row_id, "value": raw, "error": str(e)})
        if bad:
            findings.append(Finding(
                "FAIL", "datetime",
                f"{table}.{col}: {len(bad)} unparseable row(s)",
                {"table": table, "column": col, "samples": bad[:10]},
            ))
        else:
            findings.append(Finding(
                "PASS", "datetime", f"{table}.{col} all parseable",
                {"table": table, "column": col},
            ))
    return findings


def audit_not_null(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col in NOT_NULL_COLUMNS:
        if not _table_exists(conn, table):
            continue
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL"
        ).fetchone()[0]
        if n:
            sample = [r[0] for r in conn.execute(
                f"SELECT id FROM {table} WHERE {col} IS NULL LIMIT 10"
            ).fetchall()]
            findings.append(Finding(
                "FAIL", "not_null",
                f"{table}.{col}: {n} NULL row(s) (column is NOT NULL in models.py)",
                {"table": table, "column": col, "null_count": n,
                 "sample_ids": sample},
            ))
        else:
            findings.append(Finding(
                "PASS", "not_null", f"{table}.{col} no NULLs",
                {"table": table, "column": col},
            ))
    return findings


def audit_enums(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col, allowed in ENUM_COLUMNS:
        if not _table_exists(conn, table):
            continue
        nulls_allowed = None in allowed
        non_null_allowed = [a for a in allowed if a is not None]
        placeholders = ", ".join("?" for _ in non_null_allowed)
        if nulls_allowed:
            sql = (
                f"SELECT {col}, COUNT(*) FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} NOT IN ({placeholders}) "
                f"GROUP BY {col} LIMIT 20"
            )
            bad = conn.execute(sql, non_null_allowed).fetchall()
        else:
            sql = (
                f"SELECT {col}, COUNT(*) FROM {table} "
                f"WHERE {col} IS NULL OR {col} NOT IN ({placeholders}) "
                f"GROUP BY {col} LIMIT 20"
            )
            bad = conn.execute(sql, non_null_allowed).fetchall()
        if bad:
            findings.append(Finding(
                "WARN", "enum",
                f"{table}.{col}: {len(bad)} undocumented value(s)",
                {"table": table, "column": col,
                 "allowed": [a if a is not None else "<NULL>" for a in allowed],
                 "found": [{"value": v if v is not None else "<NULL>",
                            "count": c} for v, c in bad]},
            ))
        else:
            findings.append(Finding(
                "PASS", "enum", f"{table}.{col} only documented values",
                {"table": table, "column": col},
            ))
    return findings


def audit_utf8(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col in UTF8_COLUMNS:
        if not _table_exists(conn, table):
            continue
        bad: list[dict] = []
        # SQLite returns str by default; bad UTF-8 will have been lost on read.
        # We check whether re-encoding to bytes round-trips cleanly. This won't
        # catch bytes already lost to Python's replacement char, but those are
        # extremely rare and would have been gibberish in the UI anyway.
        cur = conn.execute(
            f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL"
        )
        for row_id, raw in cur:
            if "�" in raw:
                bad.append({"id": row_id, "sample": raw[:80]})
                if len(bad) >= 20:
                    break
        if bad:
            findings.append(Finding(
                "WARN", "utf8",
                f"{table}.{col}: {len(bad)} row(s) contain U+FFFD "
                f"(likely encoding error during ingestion)",
                {"table": table, "column": col, "samples": bad[:10]},
            ))
        else:
            findings.append(Finding(
                "PASS", "utf8", f"{table}.{col} no replacement chars",
                {"table": table, "column": col},
            ))
    return findings


def audit_oversized(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    for table, col in [
        ("source_items", "raw_text"),
        ("source_items", "summary"),
        ("source_items", "structured_extraction"),
        ("story_clusters", "structured_extraction"),
    ]:
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT id, LENGTH({col}) AS sz FROM {table} "
            f"WHERE {col} IS NOT NULL AND LENGTH({col}) > ? "
            f"ORDER BY sz DESC LIMIT 10",
            (OVERSIZE_BYTES,),
        ).fetchall()
        if rows:
            findings.append(Finding(
                "WARN", "oversized",
                f"{table}.{col}: {len(rows)} row(s) over {OVERSIZE_BYTES} bytes",
                {"table": table, "column": col,
                 "samples": [{"id": r[0], "bytes": r[1]} for r in rows]},
            ))
        else:
            findings.append(Finding(
                "PASS", "oversized",
                f"{table}.{col} all rows under {OVERSIZE_BYTES} bytes",
                {"table": table, "column": col},
            ))
    return findings


def audit_nul_bytes(conn: sqlite3.Connection) -> list[Finding]:
    """Scan TEXT columns for embedded NUL bytes (U+0000).

    SQLite tolerates NULs in TEXT; Postgres TEXT rejects them. The migration
    script strips NULs from every string column before COPY, so this audit
    is a WARN (not a FAIL) — but knowing the count beforehand validates that
    the migration's silent stripping didn't drop something significant.
    """
    findings: list[Finding] = []
    for table, col in NUL_BYTE_COLUMNS:
        if not _table_exists(conn, table):
            continue
        # SQLite's INSTR returns 0 if the needle isn't found, positive otherwise.
        # char(0) is the only portable way to spell a NUL byte in SQLite SQL.
        n = conn.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE {col} IS NOT NULL AND INSTR({col}, char(0)) > 0"
        ).fetchone()[0]
        if n:
            sample = [r[0] for r in conn.execute(
                f"SELECT id FROM {table} "
                f"WHERE {col} IS NOT NULL AND INSTR({col}, char(0)) > 0 "
                f"LIMIT 20"
            ).fetchall()]
            findings.append(Finding(
                "WARN", "nul_byte",
                f"{table}.{col}: {n} row(s) contain embedded NUL "
                f"(migration strips silently)",
                {"table": table, "column": col, "row_count": n,
                 "sample_ids": sample},
            ))
        else:
            findings.append(Finding(
                "PASS", "nul_byte", f"{table}.{col} no embedded NULs",
                {"table": table, "column": col},
            ))
    return findings


def run_all(conn: sqlite3.Connection) -> list[Finding]:
    out: list[Finding] = []
    for name, fn in [
        ("JSON columns", audit_json_columns),
        ("FK integrity", audit_fk_integrity),
        ("Unique constraints", audit_unique_constraints),
        ("Boolean coherence", audit_booleans),
        ("Datetime parseability", audit_datetimes),
        ("NOT NULL violations", audit_not_null),
        ("Enum drift", audit_enums),
        ("UTF-8 sanity", audit_utf8),
        ("Oversized payloads", audit_oversized),
        ("NUL bytes", audit_nul_bytes),
    ]:
        print(f"\n=== {name} ===", file=sys.stderr)
        results = fn(conn)
        for f in results:
            tag = f.severity if f.severity != "PASS" else "·"
            print(f"  {tag:4s}  {f.description}", file=sys.stderr)
        out.extend(results)
    return out


def write_reports(findings: list[Finding]) -> tuple[Path, Path]:
    json_path = REPORT_DIR / "_audit_report.json"
    md_path = REPORT_DIR / "_audit_report.md"

    summary = {
        "pass": sum(1 for f in findings if f.severity == "PASS"),
        "warn": sum(1 for f in findings if f.severity == "WARN"),
        "fail": sum(1 for f in findings if f.severity == "FAIL"),
    }

    json_path.write_text(json.dumps({
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": summary,
        "findings": [asdict(f) for f in findings],
    }, indent=2))

    lines: list[str] = []
    lines.append("# Phase 0.5 preflight audit report\n")
    lines.append(f"_Generated {datetime.utcnow().isoformat()}Z_\n")
    lines.append(f"- ✅ PASS: {summary['pass']}\n")
    lines.append(f"- ⚠️  WARN: {summary['warn']}\n")
    lines.append(f"- ❌ FAIL: {summary['fail']}\n\n")
    if summary["fail"]:
        lines.append("## FAILs — block Phase 1 until resolved\n\n")
        for f in findings:
            if f.severity == "FAIL":
                lines.append(f"- **{f.category}** — {f.description}\n")
                lines.append(f"  ```json\n  {json.dumps(f.detail, indent=2)}\n  ```\n")
    if summary["warn"]:
        lines.append("\n## WARNs — review, then proceed with eyes open\n\n")
        for f in findings:
            if f.severity == "WARN":
                lines.append(f"- **{f.category}** — {f.description}\n")
                lines.append(f"  ```json\n  {json.dumps(f.detail, indent=2)}\n  ```\n")
    if not summary["fail"] and not summary["warn"]:
        lines.append("\nAll checks passed. Safe to proceed to Phase 1.\n")
    md_path.write_text("".join(lines))

    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help="SQLite DB to audit (default: backend/war_room.db)")
    args = parser.parse_args()
    if not args.db.exists():
        print(f"ERROR: {args.db} does not exist", file=sys.stderr)
        return 3

    # Open read-only via URI to guarantee no writes.
    uri = f"file:{args.db}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        findings = run_all(conn)
    finally:
        conn.close()

    json_path, md_path = write_reports(findings)
    fails = sum(1 for f in findings if f.severity == "FAIL")
    warns = sum(1 for f in findings if f.severity == "WARN")
    passes = sum(1 for f in findings if f.severity == "PASS")
    print(f"\n=== Audit complete ===", file=sys.stderr)
    print(f"  PASS: {passes}  WARN: {warns}  FAIL: {fails}", file=sys.stderr)
    print(f"  JSON: {json_path}", file=sys.stderr)
    print(f"  Markdown: {md_path}", file=sys.stderr)
    if fails:
        return 1
    if warns:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
