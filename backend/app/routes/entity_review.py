"""Entity review queue — human triage of surfaced graph issues.

Surfaces three classes of items for human decision:

1. Contradictions: (subject, object) pairs where the subject has both
   support-type and opposition-type relations against the same target.
   Often legitimate (procedural support + rhetorical criticism), sometimes
   extraction noise.

2. Canonicalization review-tier: embedding similarity in [0.85, 0.92) —
   probably duplicates but not confident enough to auto-merge. User
   confirms or rejects.

3. Partisan-suspect (post-reclassification): co_sponsored relations whose
   evidence quote contains weak language ("supports", "praises"). These
   were already reclassified from `endorses`; user confirms whether the
   reclassification is right.

Decisions are recorded in entity_review_decisions and remove the item from
future listings (UNIQUE on item_type + item_key).
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Entity, EntityRelation, EntityReviewDecision, SourceItem
from app.services.stance import aggregate_stance, stance_for, vectors_conflict


router = APIRouter()


class DecisionIn(BaseModel):
    item_type: str
    item_key: str
    decision: str  # 'approve' | 'reject' | 'skip'
    notes: Optional[str] = None


def _existing_decisions(db: Session) -> set[tuple[str, str]]:
    rows = db.query(EntityReviewDecision.item_type, EntityReviewDecision.item_key).all()
    return {(t, k) for t, k in rows}


def _contradiction_items(db: Session, decided: set[tuple[str, str]]) -> list[dict]:
    """Find dimensional contradictions and skip ones already decided.

    The old logic was: subject has ANY relation in _SUPPORT and ANY in
    _OPPOSE against the same object → flag. That treated procedural
    support + rhetorical criticism as a contradiction even though they
    sit on different dimensions of stance.

    New logic uses the stance module: two relations only register as a
    contradiction if their stance vectors disagree on the SAME dimension.
    """
    entities = {e.id: e for e in db.query(Entity).all()}
    rels = db.query(EntityRelation).all()

    # Group all stance-bearing relations by (subject, object).
    pairs: dict[tuple[int, int], list[EntityRelation]] = defaultdict(list)
    for r in rels:
        if stance_for(r.predicate) is None:
            continue
        pairs[(r.subject_id, r.object_id)].append(r)

    items: list[dict] = []
    for (s_id, o_id), rel_list in pairs.items():
        if len(rel_list) < 2:
            continue
        subj = entities.get(s_id)
        obj = entities.get(o_id)
        if not subj or not obj:
            continue

        # Look for any pair whose stance vectors actually disagree on the
        # same dimension. If none, this isn't a contradiction — it's a
        # multi-dimensional stance (e.g., procedural support + rhetorical
        # criticism, which is coherent).
        conflict_dims: set[str] = set()
        for i, r_a in enumerate(rel_list):
            sa = stance_for(r_a.predicate)
            if not sa:
                continue
            for r_b in rel_list[i + 1:]:
                sb = stance_for(r_b.predicate)
                if not sb:
                    continue
                conflict, dims = vectors_conflict(sa, sb)
                if conflict:
                    conflict_dims.update(dims)
        if not conflict_dims:
            continue

        item_key = f"{s_id}-{o_id}"
        if ("contradiction", item_key) in decided:
            continue

        # Split for layout: anything with rhet=supportive or proc=advance
        # goes to the "support" column; rhet=critical/hostile or proc=oppose
        # goes to "oppose". Neutral on both is shown in the support column
        # by convention (typically `member_of` or similar structural rel).
        support_rels: list[EntityRelation] = []
        oppose_rels: list[EntityRelation] = []
        for r in rel_list:
            sv = stance_for(r.predicate)
            if not sv:
                continue
            if sv.rhetorical in ("critical", "hostile") or sv.procedural == "oppose":
                oppose_rels.append(r)
            else:
                support_rels.append(r)

        support_weight = sum((r.weight or 0) for r in support_rels)
        oppose_weight = sum((r.weight or 0) for r in oppose_rels)
        agg = aggregate_stance([(r.predicate, r.weight or 0) for r in rel_list])

        def sample_titles(rels_list, n=3):
            seen_ids: set[int] = set()
            ids: list[int] = []
            for r in rels_list:
                try:
                    aids = json.loads(r.source_articles or "[]")
                except Exception:
                    aids = []
                for aid in aids:
                    if aid in seen_ids:
                        continue
                    seen_ids.add(aid)
                    ids.append(aid)
                    if len(ids) >= n:
                        break
                if len(ids) >= n:
                    break
            if not ids:
                return []
            rows = db.query(SourceItem.title).filter(SourceItem.id.in_(ids)).all()
            return [t for (t,) in rows if t]

        items.append({
            "item_type": "contradiction",
            "item_key": item_key,
            "title": f"{subj.name} ↔ {obj.name}",
            "subject": {"id": subj.canonical_id, "name": subj.name, "type": subj.type,
                        "affiliation": subj.affiliation},
            "object": {"id": obj.canonical_id, "name": obj.name, "type": obj.type},
            "support_relations": [
                {"predicate": r.predicate, "weight": r.weight or 0,
                 "confidence": r.confidence, "sample_quote": r.sample_quote,
                 "stance": (stance_for(r.predicate).to_dict() if stance_for(r.predicate) else None)}
                for r in support_rels
            ],
            "oppose_relations": [
                {"predicate": r.predicate, "weight": r.weight or 0,
                 "confidence": r.confidence, "sample_quote": r.sample_quote,
                 "stance": (stance_for(r.predicate).to_dict() if stance_for(r.predicate) else None)}
                for r in oppose_rels
            ],
            "support_weight": support_weight,
            "oppose_weight": oppose_weight,
            "balance_score": min(support_weight, oppose_weight),
            "support_titles": sample_titles(support_rels),
            "oppose_titles": sample_titles(oppose_rels),
            # New dimensional info — the UI shows these so the user understands
            # WHY this pair is a contradiction (which dimension is at odds).
            "aggregate_stance": agg,
            "conflicting_dimensions": sorted(conflict_dims),
        })

    items.sort(key=lambda x: -x["balance_score"])
    return items


@router.get("/entity-review-queue/items")
def list_items(db: Session = Depends(get_db)):
    """Return all undecided review items, grouped by type."""
    decided = _existing_decisions(db)
    contradictions = _contradiction_items(db, decided)
    return {
        "summary": {
            "contradictions": len(contradictions),
            "total": len(contradictions),
        },
        "contradictions": contradictions,
    }


@router.post("/entity-review-queue/decide")
def record_decision(body: DecisionIn, db: Session = Depends(get_db)):
    if body.decision not in ("approve", "reject", "skip"):
        raise HTTPException(400, "decision must be approve, reject, or skip")
    # Upsert
    existing = (
        db.query(EntityReviewDecision)
        .filter(EntityReviewDecision.item_type == body.item_type,
                EntityReviewDecision.item_key == body.item_key)
        .one_or_none()
    )
    if existing:
        existing.decision = body.decision
        if body.notes is not None:
            existing.notes = body.notes
    else:
        db.add(EntityReviewDecision(
            item_type=body.item_type,
            item_key=body.item_key,
            decision=body.decision,
            notes=body.notes,
        ))
    db.commit()
    return {"ok": True}


@router.get("/entity-review-queue/decisions")
def list_decisions(db: Session = Depends(get_db)):
    """For audit / undo — return all recorded decisions."""
    rows = (
        db.query(EntityReviewDecision)
        .order_by(EntityReviewDecision.decided_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "item_type": r.item_type,
            "item_key": r.item_key,
            "decision": r.decision,
            "notes": r.notes,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
        }
        for r in rows
    ]
