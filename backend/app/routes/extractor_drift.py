"""Extractor-drift API — surfaces ontology drift between the live extractor
and the versions that produced existing evidence.

Endpoints:
  GET /api/extractor-drift/summary
    Returns per-version evidence counts, identifies stale relations, and
    explains what changed between the live version and each older one.

  GET /api/extractor-drift/stale-articles?limit=N
    Returns the article IDs whose extractions could be improved by
    re-running under the current version. Used by the re-extraction
    script to target only what's actually stale.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import EntityRelation, SourceItem
from app.services.extractor_versions import VERSIONS, current, diff_summary


router = APIRouter()


def _collect_versions_per_relation(db: Session) -> tuple[Counter, dict[int, set[str]], dict[int, set[int]]]:
    """Single pass over entity_relations.evidence_json.

    Returns:
      - Counter mapping extractor_version → total evidence count (across all rows)
      - dict mapping relation_id → set of versions appearing in its evidence_json
      - dict mapping relation_id → set of article_ids covering it
    """
    by_version: Counter = Counter()
    rel_versions: dict[int, set[str]] = defaultdict(set)
    rel_articles: dict[int, set[int]] = defaultdict(set)

    for r in db.query(EntityRelation).all():
        try:
            evidence = json.loads(r.evidence_json or "[]")
        except Exception:
            evidence = []
        if not evidence:
            # Fall back to source_articles if evidence_json is missing
            try:
                aids = json.loads(r.source_articles or "[]")
            except Exception:
                aids = []
            rel_articles[r.id].update(aids)
            # Mark version as unknown
            rel_versions[r.id].add("unknown")
            by_version["unknown"] += len(aids)
            continue
        for ev in evidence:
            v = ev.get("extractor_version") or "unknown"
            by_version[v] += 1
            rel_versions[r.id].add(v)
            aid = ev.get("article_id")
            if aid is not None:
                rel_articles[r.id].add(aid)

    return by_version, rel_versions, rel_articles


@router.get("/extractor-drift/summary")
def drift_summary(db: Session = Depends(get_db)):
    cur = current()
    by_version, rel_versions, _rel_articles = _collect_versions_per_relation(db)

    # Per-version breakdown: count + stale flag + summary
    per_version_out = []
    for v in VERSIONS:
        per_version_out.append({
            "version": v.version,
            "released_at": v.released_at,
            "summary": v.summary,
            "breaking_changes": list(v.breaking_changes),
            "evidence_count": by_version.get(v.version, 0),
            "stale": v.version != cur.version,
        })
    # Account for any unknown / backfilled-only versions not in registry
    known_versions = {v.version for v in VERSIONS}
    for v_name, count in by_version.items():
        if v_name not in known_versions:
            per_version_out.append({
                "version": v_name,
                "released_at": None,
                "summary": "Unrecognized version — not in the registry. Either pre-registry data or an external import.",
                "breaking_changes": [],
                "evidence_count": count,
                "stale": True,
            })

    # Per-version stale-relation count: how many relations have AT LEAST ONE evidence at this version
    stale_per_version: Counter = Counter()
    relations_with_all_stale = 0
    relations_with_any_stale = 0
    relations_fresh = 0
    for rel_id, vers in rel_versions.items():
        non_current = vers - {cur.version}
        if non_current:
            relations_with_any_stale += 1
            for v_name in non_current:
                stale_per_version[v_name] += 1
            if not (vers & {cur.version}):
                relations_with_all_stale += 1
        else:
            relations_fresh += 1
    # Decorate per_version_out with relation counts
    for entry in per_version_out:
        entry["relations_with_evidence_at_this_version"] = stale_per_version.get(entry["version"], 0)
        if entry["version"] == cur.version:
            entry["relations_with_evidence_at_this_version"] = sum(
                1 for vers in rel_versions.values() if cur.version in vers
            )

    # Pairwise diff summary between older versions and current
    diffs = []
    for v in VERSIONS:
        if v.version == cur.version:
            continue
        if by_version.get(v.version, 0) == 0:
            continue
        diffs.append({
            "from_version": v.version,
            "to_version": cur.version,
            "changes": diff_summary(v.version, cur.version),
        })

    return {
        "current_version": cur.version,
        "current_summary": cur.summary,
        "total_relations": sum(stale_per_version.values()) + relations_fresh,
        "relations_fresh": relations_fresh,
        "relations_with_any_stale_evidence": relations_with_any_stale,
        "relations_with_all_stale_evidence": relations_with_all_stale,
        "by_version": per_version_out,
        "diffs": diffs,
    }


@router.get("/extractor-drift/stale-articles")
def stale_articles(
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    """Return article IDs supporting relations with stale evidence,
    ordered by aggregate relation weight (highest-impact first).

    Used by the re-extraction script to target only what's actually stale.
    """
    cur = current()
    by_version, rel_versions, rel_articles = _collect_versions_per_relation(db)
    # For each article, sum the weights of stale relations it supports
    rel_weights = {r.id: (r.weight or 0) for r in db.query(EntityRelation).all()}
    article_weight: Counter = Counter()
    for rel_id, vers in rel_versions.items():
        if cur.version in vers:
            continue  # this relation has fresh evidence — skip
        w = rel_weights.get(rel_id, 0)
        for aid in rel_articles.get(rel_id, ()):
            article_weight[aid] += w

    top = article_weight.most_common(limit)
    out = []
    for aid, score in top:
        article = db.query(SourceItem).filter(SourceItem.id == aid).first()
        if not article:
            continue
        out.append({
            "article_id": aid,
            "title": article.title,
            "published_at": article.published_at.isoformat() if article.published_at else None,
            "stale_relation_weight": score,
        })
    return {
        "current_version": cur.version,
        "count": len(out),
        "articles": out,
    }
