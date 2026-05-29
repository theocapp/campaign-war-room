"""Entity-network endpoint backing the Entity Network frontend page.

Replaces the hand-crafted entityNetworkMock.ts. Returns the same shape:
  { entities: [...], relations: [...] }

Entity IDs are canonical_ids (e.g. "person:cognetti") so the frontend's
saved queries can keep referencing entities by stable string IDs.

Also exposes multi-hop traversal endpoints (/neighbors, /path) for
analytical queries like "who criticizes someone Cognetti endorses" that
flat node+edge data can't answer in one request.
"""
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Entity, EntityMention, EntityRelation, Outlet, SourceItem
from app.services.stance import stance_for

router = APIRouter()


@router.get("/entity-network")
def get_entity_network(
    min_mentions: int = Query(1, ge=0, description="Hide entities below this mention count."),
    db: Session = Depends(get_db),
):
    """Return the entity-network payload for the frontend.

    Shape matches frontend/src/data/entityNetworkMock.ts:
      { entities: Entity[], relations: Relation[] }

    Entities below `min_mentions` are dropped; relations referencing
    dropped entities are also dropped.
    """
    # Events are rare in the corpus (single-mention almost always — the LLM
    # phrases them slightly differently each time so they don't dedup), so
    # exempt them from the mention threshold. Otherwise the UI's "events"
    # type chip lights up but the graph never shows any.
    entities = (
        db.query(Entity)
        .filter(or_(Entity.mention_count >= min_mentions, Entity.type == "event"))
        .all()
    )
    eid_to_canon = {e.id: e.canonical_id for e in entities}
    entity_ids = list(eid_to_canon.keys())

    # Recent article titles per entity — top 3 most recent.
    recent_titles: dict[int, list[str]] = defaultdict(list)
    if entity_ids:
        rows = (
            db.query(EntityMention.entity_id, SourceItem.title)
            .join(SourceItem, SourceItem.id == EntityMention.article_id)
            .filter(EntityMention.entity_id.in_(entity_ids))
            .filter(SourceItem.title.isnot(None))
            .order_by(SourceItem.published_at.desc().nullslast())
            .all()
        )
        for entity_id, title in rows:
            if title and len(recent_titles[entity_id]) < 3:
                recent_titles[entity_id].append(title)

    import json as _json2
    def _entity_dict(e: Entity) -> dict:
        # Surface type-specific metadata from metadata_json — currently used
        # for event date/location/type but the structure is general.
        try:
            meta = _json2.loads(e.metadata_json or "{}")
        except Exception:
            meta = {}
        return {
            "id": e.canonical_id,
            "name": e.name,
            "type": e.type,
            "description": e.description or "",
            "affiliation": e.affiliation,
            "mention_count": e.mention_count or 0,
            "recent_article_titles": recent_titles.get(e.id, []),
            "first_seen": e.first_seen.isoformat() if e.first_seen else None,
            "last_seen": e.last_seen.isoformat() if e.last_seen else None,
            "seeded": bool(e.seeded),
            "metadata": meta,
        }
    entities_out = [_entity_dict(e) for e in entities]

    # Relations — only between visible entities, ordered by weight desc.
    visible_eids = set(entity_ids)
    relations = (
        db.query(EntityRelation)
        .filter(EntityRelation.subject_id.in_(visible_eids))
        .filter(EntityRelation.object_id.in_(visible_eids))
        .order_by(EntityRelation.weight.desc())
        .all()
    )

    # Build a (subject, predicate, object) → (claim_id, claim_status) lookup
    # so the response can carry claim metadata + filter retracted claims.
    from app.models import Claim
    claim_lookup: dict[tuple[int, str, int], tuple[int, str]] = {}
    if relations:
        claim_rows = (
            db.query(Claim.id, Claim.subject_id, Claim.predicate, Claim.object_id, Claim.status)
            .filter(Claim.subject_id.in_(visible_eids))
            .filter(Claim.object_id.in_(visible_eids))
            .all()
        )
        claim_lookup = {(s, p, o): (cid, status) for cid, s, p, o, status in claim_rows}
    # Drop relations whose claim is retracted; keep the rest with claim metadata.
    relations = [
        r for r in relations
        if claim_lookup.get((r.subject_id, r.predicate, r.object_id), (None, "active"))[1] != "retracted"
    ]

    # Use UTC now to compute "expired" flag at server side. Frontend can also
    # do this itself but it's convenient to have the boolean precomputed.
    from datetime import datetime
    now = datetime.utcnow()

    import json as _json
    # Build article_id → (reliability_score, bias_label) lookup so each
    # relation can carry an aggregate source-reliability number. We pull this
    # in one batch query rather than N joins.
    all_article_ids: set[int] = set()
    relation_articles: dict[int, list[int]] = {}
    for r in relations:
        try:
            ids = _json.loads(r.source_articles) if r.source_articles else []
        except Exception:
            ids = []
        relation_articles[r.id] = ids
        all_article_ids.update(ids)
    article_reliability: dict[int, tuple[int | None, str | None]] = {}
    if all_article_ids:
        rows = (
            db.query(SourceItem.id, Outlet.reliability_score, Outlet.bias_label)
            .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(SourceItem.id.in_(all_article_ids))
            .all()
        )
        for sid, rel_score, bias in rows:
            article_reliability[sid] = (rel_score, bias)

    relations_out = []
    for r in relations:
        valid_from = r.valid_from.isoformat() if r.valid_from else None
        valid_to = r.valid_to.isoformat() if r.valid_to else None
        is_expired = bool(r.valid_to and r.valid_to < now)
        try:
            evidence = _json.loads(r.evidence_json) if r.evidence_json else []
        except Exception:
            evidence = []

        # Compute aggregate source reliability across the relation's articles.
        rel_scores = [
            article_reliability.get(aid, (None, None))[0]
            for aid in relation_articles.get(r.id, [])
        ]
        rel_scores = [s for s in rel_scores if s is not None]
        avg_reliability = round(sum(rel_scores) / len(rel_scores)) if rel_scores else None

        sv = stance_for(r.predicate)
        claim_id, claim_status = claim_lookup.get(
            (r.subject_id, r.predicate, r.object_id), (None, "active")
        )
        relations_out.append({
            "id": f"r-{r.id}",
            "source": eid_to_canon[r.subject_id],
            "target": eid_to_canon[r.object_id],
            "type": r.predicate,
            "weight": r.weight or 1,
            "sample_quote": r.sample_quote or "",
            "first_seen": r.first_seen.isoformat() if r.first_seen else None,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "is_expired": is_expired,
            "confidence": r.confidence or "medium",
            # GKG principle #6 — per-edge provenance.
            "evidence": evidence,
            "evidence_count": len(evidence),
            "avg_source_reliability": avg_reliability,
            "rated_source_count": len(rel_scores),
            "stance": sv.to_dict() if sv else None,
            # Claim-layer reference — lets the UI open the inspector modal
            # by clicking an edge. `claim_status` lets the UI render
            # contested-claim badges directly on the edge.
            "claim_id": claim_id,
            "claim_status": claim_status,
        })

    return {
        "entities": entities_out,
        "relations": relations_out,
        "stats": {
            "entity_count": len(entities_out),
            "relation_count": len(relations_out),
            "seeded_count": sum(1 for e in entities_out if e["seeded"]),
        },
    }


# ── Multi-hop traversal (GKG principle #11) ─────────────────────────────


def _entity_by_canonical(db: Session, canonical_id: str) -> Entity | None:
    return db.query(Entity).filter(Entity.canonical_id == canonical_id).one_or_none()


def _build_adjacency(db: Session, min_relation_weight: int) -> tuple[dict, dict, dict]:
    """Return (forward_adj, backward_adj, entity_by_id) for graph traversal.

    forward_adj[e_id]  → list of (neighbor_id, predicate, weight) where e_id is subject
    backward_adj[e_id] → list of (neighbor_id, predicate, weight) where e_id is object

    Relations below min_relation_weight are excluded to keep the graph clean.
    """
    forward: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    backward: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    rels = db.query(EntityRelation).filter(EntityRelation.weight >= min_relation_weight).all()
    for r in rels:
        forward[r.subject_id].append((r.object_id, r.predicate, r.weight or 0))
        backward[r.object_id].append((r.subject_id, r.predicate, r.weight or 0))
    eid_to_ent = {e.id: e for e in db.query(Entity).all()}
    return forward, backward, eid_to_ent


def _entity_dict(e: Entity) -> dict:
    return {
        "id": e.canonical_id,
        "name": e.name,
        "type": e.type,
        "affiliation": e.affiliation,
        "mention_count": e.mention_count or 0,
        "seeded": bool(e.seeded),
    }


@router.get("/entity-network/neighbors")
def neighbors(
    entity: str = Query(..., description="canonical_id of the seed entity"),
    depth: int = Query(2, ge=1, le=4, description="hop depth, 1-4"),
    min_relation_weight: int = Query(2, ge=0, description="hide low-weight relations"),
    db: Session = Depends(get_db),
):
    """Return the N-hop ego network around a seed entity. Both directions
    (subject and object roles) are followed."""
    seed = _entity_by_canonical(db, entity)
    if not seed:
        raise HTTPException(404, f"entity {entity!r} not found")

    forward, backward, eid_to_ent = _build_adjacency(db, min_relation_weight)

    visited_eids: set[int] = {seed.id}
    visited_hops: dict[int, int] = {seed.id: 0}
    edges_out: list[dict] = []

    frontier: deque[tuple[int, int]] = deque([(seed.id, 0)])
    while frontier:
        cur_id, hop = frontier.popleft()
        if hop >= depth:
            continue
        for neigh_id, pred, w in forward.get(cur_id, []):
            edges_out.append({"from": cur_id, "to": neigh_id, "predicate": pred,
                              "weight": w, "direction": "forward"})
            if neigh_id not in visited_eids:
                visited_eids.add(neigh_id)
                visited_hops[neigh_id] = hop + 1
                frontier.append((neigh_id, hop + 1))
        for neigh_id, pred, w in backward.get(cur_id, []):
            edges_out.append({"from": neigh_id, "to": cur_id, "predicate": pred,
                              "weight": w, "direction": "backward"})
            if neigh_id not in visited_eids:
                visited_eids.add(neigh_id)
                visited_hops[neigh_id] = hop + 1
                frontier.append((neigh_id, hop + 1))

    entities_out = []
    for eid in visited_eids:
        ent = eid_to_ent.get(eid)
        if not ent:
            continue
        d = _entity_dict(ent)
        d["hop"] = visited_hops[eid]
        entities_out.append(d)

    # Re-export edges using canonical_ids
    edges_canonical = []
    seen_edges = set()
    for e in edges_out:
        src = eid_to_ent.get(e["from"])
        tgt = eid_to_ent.get(e["to"])
        if not src or not tgt:
            continue
        key = (src.canonical_id, e["predicate"], tgt.canonical_id)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges_canonical.append({
            "source": src.canonical_id,
            "target": tgt.canonical_id,
            "predicate": e["predicate"],
            "weight": e["weight"],
        })

    return {
        "seed": _entity_dict(seed),
        "depth": depth,
        "entities": entities_out,
        "edges": edges_canonical,
        "stats": {
            "entity_count": len(entities_out),
            "edge_count": len(edges_canonical),
        },
    }


@router.get("/entity-network/path")
def paths(
    from_: str = Query(..., alias="from", description="canonical_id of source entity"),
    to: str = Query(..., description="canonical_id of target entity"),
    max_hops: int = Query(3, ge=1, le=5),
    min_relation_weight: int = Query(2, ge=0),
    db: Session = Depends(get_db),
):
    """Return all paths from one entity to another, up to max_hops long.
    Useful for queries like 'how is A connected to B'."""
    src = _entity_by_canonical(db, from_)
    tgt = _entity_by_canonical(db, to)
    if not src:
        raise HTTPException(404, f"entity {from_!r} not found")
    if not tgt:
        raise HTTPException(404, f"entity {to!r} not found")

    forward, backward, eid_to_ent = _build_adjacency(db, min_relation_weight)

    # Treat as undirected for path-finding — both directions count as a step.
    # Predicate + direction recorded on each edge in the path.
    def neighbors_with_meta(eid: int):
        # Yields (neighbor_id, predicate, weight, direction)
        for n_id, p, w in forward.get(eid, []):
            yield n_id, p, w, "forward"
        for n_id, p, w in backward.get(eid, []):
            yield n_id, p, w, "backward"

    # BFS collecting all paths up to max_hops. Cap result count to avoid explosion.
    MAX_PATHS = 30
    results: list[list[dict]] = []
    queue: deque[list[dict]] = deque([
        # Initial frontier: just the source as a single-node path
        [{"entity_id": src.id, "predicate": None, "direction": None, "weight": None}]
    ])
    while queue and len(results) < MAX_PATHS:
        path = queue.popleft()
        last_id = path[-1]["entity_id"]
        if last_id == tgt.id and len(path) > 1:
            results.append(path)
            continue
        if len(path) - 1 >= max_hops:
            continue
        seen_in_path = {step["entity_id"] for step in path}
        for n_id, pred, w, direction in neighbors_with_meta(last_id):
            if n_id in seen_in_path:
                continue
            queue.append(path + [{"entity_id": n_id, "predicate": pred,
                                  "direction": direction, "weight": w}])

    # Materialize as canonical_ids + entity dicts
    paths_out = []
    for p in results:
        steps = []
        for s in p:
            ent = eid_to_ent.get(s["entity_id"])
            if not ent:
                steps.append(None)
            else:
                steps.append({
                    **_entity_dict(ent),
                    "predicate": s["predicate"],
                    "direction": s["direction"],
                    "weight": s["weight"],
                })
        if None in steps:
            continue
        paths_out.append(steps)

    return {
        "from": _entity_dict(src),
        "to": _entity_dict(tgt),
        "max_hops": max_hops,
        "paths": paths_out,
        "path_count": len(paths_out),
        "truncated": len(paths_out) >= MAX_PATHS,
    }
