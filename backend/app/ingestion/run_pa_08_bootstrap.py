#!/usr/bin/env python3
"""
PA-08 bootstrap ingestion runner (Cognetti vs Bresnahan).

Ingests PA_08_SEED_SOURCES through the existing KG pipeline:
  get_or_create_kg_source → KGExtractor.extract → KGIngestionService.ingest
  → run_clustering → generate_alerts

This is the identical pipeline used by verify_kg_pipeline.py and by
_run_kg_pipeline() inside ingestion.py.  We call it directly here because
the seed entries are known-relevant PA-08 content and we want to skip the
SourceItem analysis stage (race-relevance scoring, urgency classification,
story clustering) which is designed for live monitoring, not bootstrap seeding.

Safe to re-run — all stages are idempotent:
  • KGSource:  dedup on SHA-256(url + text)
  • KGClaim:   dedup on (source_id, claim_text)
  • KGEntity:  dedup on canonical_name
  • KGNarrative: upsert via run_clustering

Usage
─────
  cd backend
  python -m app.ingestion.run_pa_08_bootstrap

  # or via Makefile (sets ENABLE_KG_PIPELINE=1):
  make ingest-pa08
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Must set env before any app.* imports ────────────────────────────────────
os.environ["ENABLE_KG_PIPELINE"] = "1"
os.environ.setdefault("LLM_PROVIDER", "mock")

_BACKEND = Path(__file__).parent.parent.parent   # .../backend/
sys.path.insert(0, str(_BACKEND))

# ── App imports ───────────────────────────────────────────────────────────────
from app.db import Base, SessionLocal
import app.knowledge_graph.orm as _kg_orm   # noqa: F401 — registers KG ORM
import app.models as _models                # noqa: F401 — registers core ORM

from app.ingestion.pa_08_seed import PA_08_SEED_SOURCES
from app.knowledge_graph.extractor import KGExtractor
from app.knowledge_graph.ingestion import KGIngestionService, get_or_create_kg_source
from app.knowledge_graph.narrative_engine import run_clustering, generate_alerts, get_active_alerts
from app.knowledge_graph.orm import (
    KGAlert,
    KGClaim,
    KGEntity,
    KGNarrative,
    KGNarrativeClaim,
    KGSource,
)
from app.services.llm_provider import get_provider


# ── DB setup ──────────────────────────────────────────────────────────────────

def _get_db():
    from app.db import engine
    Base.metadata.create_all(engine)
    return SessionLocal()


# ── Colour helpers ────────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()

def _c(t, code):   return f"\033[{code}m{t}\033[0m" if _TTY else t
def _green(t):     return _c(t, "32")
def _yellow(t):    return _c(t, "33")
def _red(t):       return _c(t, "31")
def _bold(t):      return _c(t, "1")
def _dim(t):       return _c(t, "2")
def _cyan(t):      return _c(t, "36")

def _section(title: str) -> None:
    print()
    print(_bold(_cyan(f"{'─' * 60}")))
    print(_bold(_cyan(f"  {title}")))
    print(_bold(_cyan(f"{'─' * 60}")))

def _count(db, model) -> int:
    return db.query(model).count()


# ── Stage 1 — KG Ingestion ────────────────────────────────────────────────────

def stage_ingest(db, provider) -> dict:
    _section("Stage 1 — KG Ingestion  (PA-08 seed sources)")

    extractor = KGExtractor(provider)
    ingest_svc = KGIngestionService()
    entries = PA_08_SEED_SOURCES["urls"]

    total_claims = 0
    total_entities = 0
    total_edges = 0
    ingested = 0
    errors = 0

    for entry in entries:
        url = entry["url"]
        label = entry.get("source_name", url)
        print(f"  → {label:<45}", end="", flush=True)
        try:
            kg_src = get_or_create_kg_source(
                db,
                url=url,
                title=label,
                text=entry["text"],
                source_type=entry.get("source_type"),
                source_name=entry.get("source_name"),
                source_owner_type=entry.get("source_owner_type"),
            )
            result = extractor.extract(entry["text"])
            report = ingest_svc.ingest(result, source_id=kg_src.id, db=db)
            db.flush()

            total_claims   += report.claims_created
            total_entities += report.entities_created
            total_edges    += report.edges_created
            ingested += 1

            print(
                _green("OK")
                + _dim(f"  claims={report.claims_created}"
                       f"  ents={report.entities_created}"
                       f"  edges={report.edges_created}")
            )
        except Exception as exc:
            print(_red(f"ERROR: {exc}"))
            errors += 1

    db.commit()

    print(
        f"\n  sources={ingested}  claims_new={total_claims}"
        f"  entities_new={total_entities}  edges_new={total_edges}"
        f"  errors={errors}"
    )
    return {
        "ingested": ingested,
        "claims": total_claims,
        "entities": total_entities,
        "errors": errors,
    }


# ── Stage 2 — Clustering ──────────────────────────────────────────────────────

def stage_cluster(db) -> dict:
    _section("Stage 2 — KG Narrative Clustering")

    report = run_clustering(db, days=365)
    db.commit()

    print(
        f"\n  claims_processed={report.claims_processed}"
        f"  narratives_created={report.narratives_created}"
        f"  narratives_updated={report.narratives_updated}"
        f"  links_added={report.links_added}"
    )
    if report.errors:
        for err in report.errors:
            print(_yellow(f"  warning: {err}"))

    return {
        "claims_processed": report.claims_processed,
        "narratives_created": report.narratives_created,
        "narratives_updated": report.narratives_updated,
    }


# ── Stage 3 — Alerts ──────────────────────────────────────────────────────────

def stage_alerts(db) -> int:
    _section("Stage 3 — Alert Generation")

    alerts = generate_alerts(db)
    db.commit()

    print(f"\n  alerts_generated={len(alerts)}")
    for a in alerts:
        print(
            f"    [{a.alert_type:<22}]  severity={a.severity_score:.3f}"
            f"  narrative_id={a.narrative_id}"
        )
    return len(alerts)


# ── Verification report ───────────────────────────────────────────────────────

def print_verification_report(db) -> bool:
    _section("Verification Report")

    sources_ingested   = _count(db, KGSource)
    claims_generated   = _count(db, KGClaim)
    entities_created   = _count(db, KGEntity)
    narratives_created = _count(db, KGNarrative)
    alerts_generated   = _count(db, KGAlert)

    print(f"""
  {'Table':<22}  {'Rows':>6}
  {'─' * 30}
  {'kg_sources':<22}  {sources_ingested:>6}
  {'kg_claims':<22}  {claims_generated:>6}
  {'kg_entities':<22}  {entities_created:>6}
  {'kg_narratives':<22}  {narratives_created:>6}
  {'kg_alerts':<22}  {alerts_generated:>6}
""")

    # Top narratives by velocity
    top = (
        db.query(KGNarrative)
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .limit(5)
        .all()
    )
    if top:
        print("  Top active narratives by velocity:\n")
        print(f"  {'ID':>4}  {'velocity':>8}  {'claims':>6}  label")
        print(f"  {'─' * 65}")
        for narr in top:
            n = db.query(KGNarrativeClaim).filter_by(narrative_id=narr.id).count()
            v = narr.velocity_score or 0.0
            v_str = _yellow(f"{v:8.3f}") if v > 0 else f"{v:8.3f}"
            print(f"  {narr.id:>4}  {v_str}  {n:>6}  {narr.label[:55]}")
        print()

    # Success criteria checks
    ok = True

    def _chk(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        mark = _green("PASS") if cond else _red("FAIL")
        suffix = f"  {_dim(detail)}" if detail else ""
        print(f"  {mark}  {name}{suffix}")
        if not cond:
            ok = False

    print("  Success criteria:\n")
    _chk("sources_ingested > 0",   sources_ingested  > 0, f"got {sources_ingested}")
    _chk("claims_generated > 0",   claims_generated  > 0, f"got {claims_generated}")
    _chk("narratives_created > 0", narratives_created > 0, f"got {narratives_created}")

    has_velocity = db.query(KGNarrative).filter(KGNarrative.velocity_score > 0).count() > 0
    has_alert    = alerts_generated > 0
    _chk(
        "velocity_score > 0 OR alert generated",
        has_velocity or has_alert,
        f"velocity_narratives>0={has_velocity}  alerts={alerts_generated}",
    )

    print()

    if not ok:
        _section("Debug Breakdown")
        print(f"  ENABLE_KG_PIPELINE = {os.environ.get('ENABLE_KG_PIPELINE', '(not set)')!r}")
        print(f"  LLM_PROVIDER       = {os.environ.get('LLM_PROVIDER', '(not set)')!r}")
        print()
        if claims_generated == 0:
            print("  claims == 0: check LLM_PROVIDER and KGExtractor output above")
        if narratives_created == 0:
            print("  narratives == 0: run_clustering needs claims with embeddings")

    return ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print(_bold(_cyan("""
╔══════════════════════════════════════════════════════════╗
║     PA-08 Bootstrap Ingestion  (Cognetti / Bresnahan)    ║
╚══════════════════════════════════════════════════════════╝
""")))

    provider = get_provider()
    print(f"  Provider           : {provider.__class__.__name__}")
    print(f"  ENABLE_KG_PIPELINE : {os.environ.get('ENABLE_KG_PIPELINE', '(not set)')!r}")
    print(f"  Sources to ingest  : {len(PA_08_SEED_SOURCES['urls'])}")
    print(f"  Reddit queries     : {len(PA_08_SEED_SOURCES['reddit_queries'])}  (reference only — create monitors separately)")
    print(f"  YouTube queries    : {len(PA_08_SEED_SOURCES['youtube_queries'])}  (reference only — create monitors separately)")

    db = _get_db()
    try:
        stage_ingest(db, provider)
        stage_cluster(db)
        stage_alerts(db)
        ok = print_verification_report(db)
    finally:
        db.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
