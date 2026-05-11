#!/usr/bin/env python3
"""
End-to-end verification harness for the Knowledge Graph pipeline.

Tests: ingestion → dedup → clustering → merge → decay → alerts → credibility

Usage
─────
  python backend/scripts/verify_kg_pipeline.py
  make verify-kg                         # see Makefile target
  python -m backend.scripts.verify_kg_pipeline

Exit codes
──────────
  0  All assertions pass
  1  One or more assertions failed

Design
──────
Uses an in-memory SQLite database so every run starts from a clean slate.
LLM_PROVIDER is forced to "mock" so no real API keys are required.
Phase A runs the real KG extraction pipeline on 10 realistic source texts.
Phase B injects controlled embeddings to exercise merge / decay / credibility
assertions deterministically, since the hash-based mock embeddings are not
semantically meaningful.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── Environment ──────────────────────────────────────────────────────────────
# Force mock mode before importing anything from app.*
os.environ["LLM_PROVIDER"] = "mock"
os.environ["ENABLE_KG_PIPELINE"] = "1"

# Make `app.*` importable regardless of working directory
_BACKEND = Path(__file__).parent.parent
sys.path.insert(0, str(_BACKEND))

# ── SQLAlchemy ────────────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db import Base
import app.knowledge_graph.orm as _kg_orm   # noqa: F401 — registers KG ORM classes
import app.models as _models               # noqa: F401 — registers core ORM classes

from app.knowledge_graph.ingestion import (
    KGIngestionService,
    get_or_create_kg_source,
)
from app.knowledge_graph.extractor import KGExtractor
from app.knowledge_graph.narrative_engine import (
    INACTIVE_DAYS,
    MERGE_THRESHOLD,
    SIMILARITY_THRESHOLD,
    apply_inactivity_decay,
    generate_alerts,
    get_active_alerts,
    merge_narratives,
    run_clustering,
    _normalize,
)
from app.knowledge_graph.orm import (
    KGAlert,
    KGClaim,
    KGClaimEntity,
    KGEntity,
    KGNarrative,
    KGNarrativeClaim,
    KGSource,
)
from app.services.llm_provider import get_provider


# ── Terminal colour helpers ───────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

def _green(t):  return _c(t, "32")
def _red(t):    return _c(t, "31")
def _yellow(t): return _c(t, "33")
def _bold(t):   return _c(t, "1")
def _dim(t):    return _c(t, "2")
def _cyan(t):   return _c(t, "36")


# ── Source fixtures ───────────────────────────────────────────────────────────
# 10 realistic political source texts.  Keywords are drawn from the mock
# extractor's _ISSUE_KEYWORDS map so extraction is guaranteed to produce claims.
# Entity names (title-case multi-word) are present verbatim so they pass the
# groundedness check.

SOURCES: list[dict] = [
    # ── Housing cluster (3 items) ─────────────────────────────────────────────
    {
        "url": "https://citytimes.com/housing-crisis-rent-hike",
        "title": "City Faces Housing Crisis as Rents Climb 18 Percent",
        "text": (
            "Mayor Linda Ortega announced an emergency housing task force this week after rent in "
            "the district climbed 18 percent in twelve months. 'Tenants are being priced out of "
            "their neighborhoods,' said council member James Whitfield at the hearing. The mayor's "
            "plan includes new zoning changes to allow higher-density construction and an "
            "eviction moratorium through the winter. Critics argue the affordable housing "
            "proposals are insufficient given the current mortgage crisis."
        ),
        "source_type": "news",
        "source_name": "City Times",
        "source_owner_type": "media",
        "credibility_score_expected": 0.7,
    },
    {
        "url": "https://districtgazette.org/rent-control-debate",
        "title": "Rent Control Proposal Sparks Debate Among Developers",
        "text": (
            "A rent stabilization ordinance proposed by council member James Whitfield would cap "
            "annual rent increases at five percent. Developer groups say the housing plan will "
            "reduce new construction and worsen the affordability crisis. Tenant advocates, led by "
            "Maria Flores, called the ordinance a lifeline for low-income renters. The landlord "
            "association warned it could trigger a wave of evictions as property owners exit the "
            "rental market. Zoning reform remains the central debate in the district housing "
            "conversation this term."
        ),
        "source_type": "news",
        "source_name": "District Gazette",
        "source_owner_type": "media",
        "credibility_score_expected": 0.7,
    },
    {
        "url": "https://housing.gov/affordability-report-2024",
        "title": "HUD Affordability Index Shows Record Housing Stress",
        "text": (
            "Federal data from the Department of Housing and Urban Development shows that mortgage "
            "affordability has reached its lowest point in two decades. Homebuyer activity dropped "
            "22 percent year-over-year in competitive districts. The HUD report recommends "
            "expanded zoning flexibility and tenant protection laws to prevent mass eviction "
            "filings. Mayor Linda Ortega cited the federal housing report in her address to the "
            "district council, saying rent stabilization is now a public health issue."
        ),
        "source_type": "public_record",
        "source_name": "HUD Federal Report",
        "source_owner_type": "official",
        "credibility_score_expected": 0.9,   # .gov domain
    },
    # ── Public safety cluster (2 items) ──────────────────────────────────────
    {
        "url": "https://localwatch.com/crime-spike-downtown",
        "title": "Downtown Crime Spike Prompts Call for More Officers",
        "text": (
            "Police Chief David Nguyen reported a 14 percent rise in property crime in the "
            "downtown corridor. The chief credited understaffing as a factor and called on the "
            "city council to fund 40 new officer positions. Councilwoman Sarah Peterson argued "
            "that community policing and crime prevention programs offer a more cost-effective "
            "path to public safety than hiring. Enforcement of current laws must be the priority, "
            "said the police officers union. Residents expressed frustration about break-in "
            "incidents near the transit hub."
        ),
        "source_type": "news",
        "source_name": "Local Watch",
        "source_owner_type": "media",
        "credibility_score_expected": 0.7,
    },
    {
        "url": "https://x.com/safestreets_2024/status/1234",
        "title": "THREAD: Defund police narrative resurfaces after patrol cuts",
        "text": (
            "Crime stats released today show theft is up across patrol-reduced zones. The safety "
            "debate is back. Defund advocates say crime is driven by poverty not enforcement. "
            "Officer shortage is real — police chief said staffing is at a 20-year low. Break-in "
            "reports in residential areas rose last quarter. Councilwoman Sarah Peterson is "
            "calling for a community safety task force instead of new officer hires."
        ),
        "source_type": "social",
        "source_name": "Safe Streets Twitter",
        "source_owner_type": "unclear",
        "credibility_score_expected": 0.4,   # social, unclear owner → lowest tier
    },
    # ── Education cluster (2 items) ───────────────────────────────────────────
    {
        "url": "https://schoolboard.edu/overcrowding-report",
        "title": "School Board Releases Overcrowding Report for District 7",
        "text": (
            "District 7's school system is operating at 140 percent capacity, according to an "
            "education department report released Friday. Superintendent Thomas Brown called on "
            "the city to fund two new school buildings to relieve classroom overcrowding. "
            "Parent groups argue that arts and music programs have been the first cut in budget "
            "reductions. Teacher retention is at a five-year low, the report found. Student "
            "enrollment is projected to grow another 12 percent over the next three years."
        ),
        "source_type": "public_record",
        "source_name": "School Board District 7",
        "source_owner_type": "official",
        "credibility_score_expected": 0.75,  # public_record
    },
    {
        "url": "https://parentvoices.org/arts-music-cuts",
        "title": "Parents Rally Against Arts and Music Program Cuts",
        "text": (
            "Hundreds of parents gathered outside city hall to protest proposed cuts to arts and "
            "music programs in district schools. Organizer Rachel Kim said the education cuts "
            "would devastate student development. Superintendent Thomas Brown acknowledged that "
            "budget constraints are forcing difficult choices but promised no classroom teacher "
            "layoffs this year. The school board vote on the education budget is scheduled for "
            "next Tuesday. Parent advocates submitted a petition with over 2,000 signatures "
            "opposing the cuts."
        ),
        "source_type": "news",
        "source_name": "Parent Voices",
        "source_owner_type": "media",
        "credibility_score_expected": 0.7,
    },
    # ── Infrastructure (1 item) ───────────────────────────────────────────────
    {
        "url": "https://publicworks.gov/road-repair-plan-2024",
        "title": "Public Works Releases 5-Year Road Repair Plan",
        "text": (
            "The Department of Public Works issued its five-year infrastructure repair plan "
            "covering 340 miles of roads with documented pothole and sidewalk damage. Director "
            "Frank Reyes said the $200 million infrastructure package prioritizes transit "
            "corridors and flood-prone streets. The plan includes bus route improvements and "
            "dedicated repair crews for residential street maintenance. Opposition council members "
            "called the infrastructure spending excessive, while transit advocates praised the "
            "road repair prioritization."
        ),
        "source_type": "public_record",
        "source_name": "Dept. of Public Works",
        "source_owner_type": "official",
        "credibility_score_expected": 0.9,  # .gov domain
    },
    # ── Downtown Development (2 items) ────────────────────────────────────────
    {
        "url": "https://citytimes.com/downtown-megaproject-zoning",
        "title": "Megaproject Developer Seeks Zoning Variance for Downtown Tower",
        "text": (
            "Developer consortium Apex Group filed for a zoning variance to build a 42-story "
            "mixed-use tower in the downtown corridor. Construction critics warn the project "
            "will accelerate gentrification in the surrounding neighborhoods. Council member "
            "James Whitfield questioned the development deal's community benefit agreement. "
            "The Downtown Business Association supports the project, saying it will bring "
            "2,500 jobs to the district. Zoning board hearings begin next month."
        ),
        "source_type": "news",
        "source_name": "City Times",
        "source_owner_type": "media",
        "credibility_score_expected": 0.7,
    },
    {
        "url": "https://opponent-campaign.com/development-attack",
        "title": "Attack Ad: Candidate Took Money from Downtown Developers",
        "text": (
            "Opposition research reveals the candidate received $45,000 in developer "
            "contributions during the downtown project approval process. The development deal "
            "bypassed community input on zoning changes, critics say. Construction began before "
            "environmental review was complete. Gentrification in the downtown area has "
            "displaced over 400 families since the project was approved. The opponent campaign "
            "is calling for a full investigation into the development approval process."
        ),
        "source_type": "opponent_statement",
        "source_name": "Opponent Campaign",
        "source_owner_type": "opponent",
        "credibility_score_expected": 0.6,  # opponent_statement
    },
]


# ── DB setup ──────────────────────────────────────────────────────────────────

def make_db() -> tuple[any, Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine, SessionLocal()


# ── Assertion tracker ─────────────────────────────────────────────────────────

class AssertionFailure(Exception):
    pass

_assertions: list[tuple[str, bool, str]] = []   # (name, passed, detail)

def check(name: str, condition: bool, detail: str = "") -> bool:
    _assertions.append((name, condition, detail))
    status = _green("PASS") if condition else _red("FAIL")
    print(f"  {status}  {name}" + (f"  {_dim(detail)}" if detail else ""))
    return condition


# ── Helpers ───────────────────────────────────────────────────────────────────

def _v(angle_deg: float) -> list[float]:
    """2-D unit vector at *angle_deg* degrees.  Used for controlled embeddings."""
    r = math.radians(angle_deg)
    return [math.cos(r), math.sin(r)]


def _seed_controlled_narrative(
    db: Session,
    *,
    angle_deg: float,
    source: KGSource,
    label: str,
    last_seen_offset_days: int = 0,
    confidence: float = 0.8,
    n_extra_claims: int = 2,
) -> KGNarrative:
    """
    Insert a KGNarrative with a controlled 2-D centroid + *n_extra_claims* member
    claims.  *angle_deg* is the centroid direction.
    """
    vec = _v(angle_deg)
    now = datetime.utcnow()
    last_seen = now - timedelta(days=last_seen_offset_days)

    narr = KGNarrative(
        label=label,
        embedding=json.dumps(vec),
        velocity_score=1.0,
        first_seen_at=last_seen,
        last_seen_at=last_seen,
        status="active",
        clustering_method="cosine_threshold_v1",
    )
    db.add(narr)
    db.flush()

    for i in range(n_extra_claims):
        claim = KGClaim(
            text=f"Controlled claim {i} for narrative '{label}'",
            stance="neutral",
            confidence=confidence,
            source_id=source.id,
            embedding=json.dumps(vec),      # same direction as narrative
        )
        db.add(claim)
        db.flush()
        db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))

    db.flush()
    return narr


def _count(db: Session, model) -> int:
    return db.query(model).count()


def _section(title: str) -> None:
    print()
    print(_bold(_cyan(f"{'─' * 60}")))
    print(_bold(_cyan(f"  {title}")))
    print(_bold(_cyan(f"{'─' * 60}")))


# ── Phases ────────────────────────────────────────────────────────────────────

def phase_a_ingestion(db: Session, provider) -> dict:
    """
    Ingest each source through the real KG pipeline (mock LLM) and return
    per-source extraction reports.
    """
    _section("Phase A — KG Ingestion (10 sources, mock LLM)")
    svc = KGExtractor(provider)
    ingest_svc = KGIngestionService()
    reports = []

    for src in SOURCES:
        kg_src = get_or_create_kg_source(
            db,
            url=src["url"],
            title=src["title"],
            text=src["text"],
            source_type=src["source_type"],
            source_name=src["source_name"],
            source_owner_type=src["source_owner_type"],
        )
        result = svc.extract(src["text"])
        report = ingest_svc.ingest(result, source_id=kg_src.id, db=db)
        db.flush()
        reports.append((src["source_name"], report, kg_src))

    db.commit()

    print(f"\n  Ingested {len(reports)} sources:\n")
    for name, rep, src in reports:
        cred_label = f"cred={src.credibility_score:.2f}"
        print(
            f"    {name:<35} "
            f"claims={rep.claims_created}  entities={rep.entities_created}  "
            f"edges={rep.edges_created}  {_dim(cred_label)}"
        )

    return {
        "sources": _count(db, KGSource),
        "claims": _count(db, KGClaim),
        "entities": _count(db, KGEntity),
    }


def phase_b_dedup(db: Session, provider) -> dict:
    """Re-ingest every source and prove row counts don't increase."""
    _section("Phase B — Deduplication (re-ingest same 10 sources)")
    before = {
        "sources": _count(db, KGSource),
        "claims":  _count(db, KGClaim),
        "entities": _count(db, KGEntity),
    }

    svc = KGExtractor(provider)
    ingest_svc = KGIngestionService()
    for src in SOURCES:
        kg_src = get_or_create_kg_source(
            db,
            url=src["url"],
            title=src["title"],
            text=src["text"],
            source_type=src["source_type"],
            source_name=src["source_name"],
            source_owner_type=src["source_owner_type"],
        )
        result = svc.extract(src["text"])
        ingest_svc.ingest(result, source_id=kg_src.id, db=db)
        db.flush()
    db.commit()

    after = {
        "sources": _count(db, KGSource),
        "claims":  _count(db, KGClaim),
        "entities": _count(db, KGEntity),
    }

    print()
    for key in ("sources", "claims", "entities"):
        print(f"    {key:<12}  before={before[key]}  after={after[key]}")

    return before, after


def phase_c_clustering(db: Session) -> any:
    """Run clustering on all claims (large days window)."""
    _section("Phase C — Clustering")
    report = run_clustering(db, days=365)
    db.commit()
    print(
        f"\n  claims_processed={report.claims_processed}  "
        f"narratives_created={report.narratives_created}  "
        f"narratives_updated={report.narratives_updated}  "
        f"links_added={report.links_added}"
    )
    if report.errors:
        print(_yellow(f"  Errors: {report.errors}"))
    return report


def phase_d_merge(db: Session) -> int:
    """
    Inject two narratives with centroids 8° apart (cos=0.99 > MERGE_THRESHOLD=0.90)
    and one at 60° (cos=0.50 < threshold).  Only the close pair should merge.
    """
    _section(f"Phase D — Narrative Merge  (threshold={MERGE_THRESHOLD})")

    # Shared source for controlled claims
    gov_src = KGSource(
        url="https://test.gov/merge-probe",
        content_hash="merge-probe-gov",
        source_type="public_record",
        credibility_score=0.9,
        verified_official=1,
    )
    db.add(gov_src)
    db.flush()

    # Two narratives 8° apart → should merge
    _seed_controlled_narrative(db, angle_deg=0,  source=gov_src, label="MERGE_A angle=0")
    _seed_controlled_narrative(db, angle_deg=8,  source=gov_src, label="MERGE_B angle=8")
    # One far away → should NOT merge
    _seed_controlled_narrative(db, angle_deg=60, source=gov_src, label="MERGE_CONTROL angle=60")
    db.flush()

    n_before = _count(db, KGNarrative)
    merged = merge_narratives(db)
    db.commit()
    n_after = _count(db, KGNarrative)

    print(f"\n  narratives_before_merge={n_before}  merged={merged}  active_after={n_after}")
    return merged


def phase_e_decay(db: Session) -> int:
    """
    Insert a narrative whose last_seen_at is older than INACTIVE_DAYS.
    Verify it transitions to 'inactive'.
    """
    _section(f"Phase E — Inactivity Decay  (threshold={INACTIVE_DAYS} days)")
    stale_src = KGSource(
        url="https://test.gov/decay-probe",
        content_hash="decay-probe-src",
        credibility_score=0.7,
    )
    db.add(stale_src)
    db.flush()

    old_ts = datetime.utcnow() - timedelta(days=INACTIVE_DAYS + 2)
    stale_narr = KGNarrative(
        label="DECAY_TEST stale narrative",
        embedding=json.dumps(_v(45)),
        velocity_score=0.1,
        first_seen_at=old_ts,
        last_seen_at=old_ts,
        status="active",
    )
    db.add(stale_narr)
    db.flush()

    decayed = apply_inactivity_decay(db)
    db.commit()
    db.refresh(stale_narr)

    print(f"\n  decayed={decayed}  stale_narrative_status='{stale_narr.status}'")
    return decayed, stale_narr.status


def phase_f_alerts(db: Session) -> list:
    """
    Create a high-velocity narrative and trigger generate_alerts().
    """
    _section("Phase F — Alert Generation")

    alert_src = KGSource(
        url="https://test.gov/alert-probe",
        content_hash="alert-probe-src",
        source_type="news",
        credibility_score=0.8,
    )
    db.add(alert_src)
    db.flush()

    # Seed a high-velocity narrative with multiple sources and entities
    now = datetime.utcnow()
    alert_narr = KGNarrative(
        label="ALERT_TEST high velocity narrative",
        embedding=json.dumps(_v(30)),
        velocity_score=5.0,         # well above any threshold
        first_seen_at=now - timedelta(days=3),
        last_seen_at=now,
        status="active",
    )
    db.add(alert_narr)
    db.flush()

    # 4 claims from 4 distinct sources with 3 distinct entities
    for i in range(4):
        extra_src = KGSource(
            url=f"https://news-{i}.com/alert-story",
            content_hash=f"alert-story-{i}",
            source_type="news",
            credibility_score=0.7,
        )
        db.add(extra_src)
        db.flush()

        entity = KGEntity(entity_type="PERSON", name=f"Alert Entity {i}")
        db.add(entity)
        db.flush()

        claim = KGClaim(
            text=f"Alert claim {i} about political topic X",
            stance="neutral",
            confidence=0.9,
            source_id=extra_src.id,
            embedding=json.dumps(_v(30)),
        )
        db.add(claim)
        db.flush()
        db.add(KGClaimEntity(claim_id=claim.id, entity_id=entity.id))
        db.add(KGNarrativeClaim(narrative_id=alert_narr.id, claim_id=claim.id))

    db.flush()

    alerts = generate_alerts(db)
    db.commit()

    print(f"\n  alerts_generated={len(alerts)}")
    for a in alerts:
        print(
            f"    [{a.alert_type:<20}]  severity={a.severity_score:.3f}  "
            f"narrative_id={a.narrative_id}"
        )
    return alerts


def phase_g_credibility(db: Session) -> tuple[float, float]:
    """
    Create two identical narratives — one backed by a .gov source, one by an
    unverified social account — and compare their velocity scores after clustering.
    High-credibility source should produce higher velocity.
    """
    _section("Phase G — Credibility-Weighted Velocity")

    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    gov_src = KGSource(
        url="https://policy.gov/credibility-probe",
        content_hash="cred-gov-src",
        source_type="public_record",
        credibility_score=0.9,
        verified_official=1,
    )
    social_src = KGSource(
        url="https://anon-blog.info/credibility-probe",
        content_hash="cred-social-src",
        source_type="social",
        credibility_score=0.3,
        verified_official=0,
    )
    db.add(gov_src)
    db.add(social_src)
    db.flush()

    # Distinct directions so they don't merge
    for src, angle, label in [
        (gov_src,    70, "CRED_HIGH .gov"),
        (social_src, 80, "CRED_LOW social"),
    ]:
        narr = KGNarrative(
            label=label,
            embedding=json.dumps(_v(angle)),
            velocity_score=0.0,
            first_seen_at=now - timedelta(hours=12),
            last_seen_at=now,
            status="active",
        )
        db.add(narr)
        db.flush()

        # Same number of claims with same confidence
        for i in range(3):
            claim = KGClaim(
                text=f"Credibility test claim {i} for {label}",
                stance="neutral",
                confidence=0.8,
                source_id=src.id,
                embedding=json.dumps(_v(angle)),
                created_at=now,   # within the last-24h window
            )
            db.add(claim)
            db.flush()
            db.add(KGNarrativeClaim(narrative_id=narr.id, claim_id=claim.id))

        db.flush()

    # Rerun clustering to update velocity scores for these new narratives
    run_clustering(db, days=1, now=now)
    db.commit()

    high_narr = db.query(KGNarrative).filter(KGNarrative.label == "CRED_HIGH .gov").first()
    low_narr  = db.query(KGNarrative).filter(KGNarrative.label == "CRED_LOW social").first()

    high_v = high_narr.velocity_score if high_narr else 0.0
    low_v  = low_narr.velocity_score  if low_narr  else 0.0

    print(
        f"\n  CRED_HIGH (.gov, cred=0.9)   velocity={high_v:.4f}\n"
        f"  CRED_LOW  (social, cred=0.3)  velocity={low_v:.4f}"
    )
    return high_v, low_v


# ── Report ────────────────────────────────────────────────────────────────────

def print_summary_report(db: Session) -> None:
    _section("Final Verification Report")

    kg_sources  = _count(db, KGSource)
    kg_claims   = _count(db, KGClaim)
    kg_entities = _count(db, KGEntity)
    kg_edges    = db.query(_kg_orm.KGEdge).count()
    kg_narrs    = _count(db, KGNarrative)
    kg_alerts   = _count(db, KGAlert)

    print(f"""
  {'Table':<22}  {'Rows':>6}
  {'─'*30}
  {'kg_sources':<22}  {kg_sources:>6}
  {'kg_claims':<22}  {kg_claims:>6}
  {'kg_entities':<22}  {kg_entities:>6}
  {'kg_edges':<22}  {kg_edges:>6}
  {'kg_narratives':<22}  {kg_narrs:>6}
  {'kg_alerts':<22}  {kg_alerts:>6}
""")

    narratives = (
        db.query(KGNarrative)
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .limit(5)
        .all()
    )

    print(f"  Top 5 active narratives by velocity:\n")
    print(
        f"  {'ID':>4}  {'velocity':>8}  {'status':<10}  "
        f"{'claims':>6}  {'src':>4}  {'ent':>4}  label"
    )
    print(f"  {'─'*80}")

    for narr in narratives:
        n_claims = db.query(KGNarrativeClaim).filter_by(narrative_id=narr.id).count()
        claim_ids = [r.claim_id for r in db.query(KGNarrativeClaim).filter_by(narrative_id=narr.id).all()]
        src_ids: set[int] = set()
        ent_ids: set[int] = set()
        for cid in claim_ids:
            c = db.get(KGClaim, cid)
            if c and c.source_id:
                src_ids.add(c.source_id)
            for lnk in db.query(KGClaimEntity).filter_by(claim_id=cid).all():
                ent_ids.add(lnk.entity_id)
        v = narr.velocity_score or 0.0
        v_str = _yellow(f"{v:8.3f}") if v >= 1.0 else f"{v:8.3f}"
        print(
            f"  {narr.id:>4}  {v_str}  {narr.status:<10}  "
            f"{n_claims:>6}  {len(src_ids):>4}  {len(ent_ids):>4}  "
            f"{narr.label[:55]}"
        )

    active_alerts = get_active_alerts(db, limit=10)
    if active_alerts:
        print(f"\n  Unresolved alerts:\n")
        print(f"  {'ID':>4}  {'severity':>8}  {'type':<22}  narrative")
        print(f"  {'─'*70}")
        for a in active_alerts:
            sev = a.severity_score
            sev_str = _red(f"{sev:8.3f}") if sev >= 0.7 else _yellow(f"{sev:8.3f}")
            label = a.narrative.label[:40] if a.narrative else "?"
            print(f"  {a.id:>4}  {sev_str}  {a.alert_type:<22}  {label}")


def print_assertion_summary() -> int:
    _section("Assertion Summary")
    total   = len(_assertions)
    passed  = sum(1 for _, ok, _ in _assertions if ok)
    failed  = total - passed
    print()
    for name, ok, detail in _assertions:
        mark = _green("✓") if ok else _red("✗")
        line = f"  {mark}  {name}"
        if detail and not ok:
            line += f"  {_dim(detail)}"
        print(line)
    print()
    if failed == 0:
        print(_green(_bold(f"  All {total} assertions passed.")))
    else:
        print(_red(_bold(f"  {failed}/{total} assertion(s) FAILED.")))
    return failed


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print(_bold(_cyan("""
╔══════════════════════════════════════════════════════════╗
║       KG Pipeline Verification Harness  (mock LLM)      ║
╚══════════════════════════════════════════════════════════╝
""")))

    engine, db = make_db()
    provider = get_provider()     # resolves to MockLLMProvider

    print(f"  DB:       in-memory SQLite")
    print(f"  Provider: {provider.__class__.__name__}")

    failures = 0

    try:
        # ── Phase A: Ingestion ─────────────────────────────────────────────
        counts_a = phase_a_ingestion(db, provider)

        print()
        print(_bold("  Assertions:"))
        check(
            "kg_sources == 10 (one per fixture)",
            counts_a["sources"] == 10,
            f"got {counts_a['sources']}",
        )
        check(
            "kg_claims >= 5 (mock extract found claims)",
            counts_a["claims"] >= 5,
            f"got {counts_a['claims']}",
        )
        check(
            "kg_entities >= 3 (capitalized names extracted)",
            counts_a["entities"] >= 3,
            f"got {counts_a['entities']}",
        )

        # Spot-check credibility scores on gov sources
        hud = db.query(KGSource).filter(KGSource.url.contains("housing.gov")).first()
        pw  = db.query(KGSource).filter(KGSource.url.contains("publicworks.gov")).first()
        check(
            ".gov source credibility >= 0.85",
            bool(hud and hud.credibility_score >= 0.85 and pw and pw.credibility_score >= 0.85),
            f"hud={hud.credibility_score if hud else 'N/A'}  pw={pw.credibility_score if pw else 'N/A'}",
        )
        social = db.query(KGSource).filter(KGSource.url.contains("x.com")).first()
        check(
            "Social source credibility <= 0.5",
            bool(social and social.credibility_score <= 0.5),
            f"got {social.credibility_score if social else 'N/A'}",
        )

        # ── Phase B: Dedup ─────────────────────────────────────────────────
        before, after = phase_b_dedup(db, provider)

        print()
        print(_bold("  Assertions:"))
        for key in ("sources", "claims", "entities"):
            check(
                f"dedup: {key} count unchanged after re-ingest",
                before[key] == after[key],
                f"before={before[key]} after={after[key]}",
            )

        # ── Phase C: Clustering ────────────────────────────────────────────
        cluster_report = phase_c_clustering(db)
        n_narratives = _count(db, KGNarrative)

        print()
        print(_bold("  Assertions:"))
        check(
            "clustering produced >= 2 narratives from mock sources",
            n_narratives >= 2,
            f"got {n_narratives}",
        )
        check(
            "no clustering errors",
            len(cluster_report.errors) == 0,
            f"errors={cluster_report.errors}" if cluster_report.errors else "",
        )

        # ── Phase D: Merge ─────────────────────────────────────────────────
        merged = phase_d_merge(db)

        print()
        print(_bold("  Assertions:"))
        check(
            "merge_narratives() absorbed >= 1 near-duplicate narrative",
            merged >= 1,
            f"merged={merged}",
        )

        # Confirm the absorbed narrative is marked 'merged'
        absorbed = db.query(KGNarrative).filter(
            KGNarrative.label == "MERGE_B angle=8"
        ).first()
        check(
            "absorbed narrative has status='merged'",
            absorbed is not None and absorbed.status == "merged",
            f"status={absorbed.status if absorbed else 'not found'}",
        )
        survivor = db.query(KGNarrative).filter(
            KGNarrative.label == "MERGE_A angle=0"
        ).first()
        control  = db.query(KGNarrative).filter(
            KGNarrative.label == "MERGE_CONTROL angle=60"
        ).first()
        check(
            "distant narrative (60°) not merged (status still active)",
            control is not None and control.status == "active",
            f"status={control.status if control else 'not found'}",
        )

        # ── Phase E: Decay ─────────────────────────────────────────────────
        decayed, stale_status = phase_e_decay(db)

        print()
        print(_bold("  Assertions:"))
        check(
            "apply_inactivity_decay() decayed >= 1 stale narrative",
            decayed >= 1,
            f"decayed={decayed}",
        )
        check(
            "stale narrative transitioned to status='inactive'",
            stale_status == "inactive",
            f"status={stale_status}",
        )

        # ── Phase F: Alerts ────────────────────────────────────────────────
        alerts = phase_f_alerts(db)

        print()
        print(_bold("  Assertions:"))
        check(
            "generate_alerts() produced >= 1 alert",
            len(alerts) >= 1,
            f"alerts={len(alerts)}",
        )
        if alerts:
            check(
                "alert has severity_score > 0 and alert_type set",
                all(a.severity_score > 0 and a.alert_type for a in alerts),
                "",
            )
            check(
                "get_active_alerts() returns the newly created alerts",
                len(get_active_alerts(db)) >= 1,
                "",
            )

        # ── Phase G: Credibility comparison ───────────────────────────────
        high_v, low_v = phase_g_credibility(db)

        print()
        print(_bold("  Assertions:"))
        check(
            ".gov source (cred=0.9) velocity > social source (cred=0.3) velocity",
            high_v > low_v,
            f"gov={high_v:.4f}  social={low_v:.4f}",
        )

        # ── Final report ───────────────────────────────────────────────────
        print_summary_report(db)

    except Exception as exc:
        print(_red(f"\n  FATAL: unexpected exception: {exc}"))
        import traceback
        traceback.print_exc()
        failures += 1
    finally:
        db.close()
        engine.dispose()

    failures += print_assertion_summary()
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
