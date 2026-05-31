"""
Background job: rescore existing articles with the LLM-based campaign_analysis pipeline.

Replaces keyword-based relevance scores for all articles that have raw text.
Runs in a background thread that fans work out across N worker threads — one per
loaded LLM provider key. The FallbackProvider underneath is thread-safe and
round-robins across keys, so 16 workers naturally spread load across 16 keys
without per-call pinning.

Progress is tracked in _state so the /api/admin/rescore-status endpoint can
report it. All _state mutations happen under _lock.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# In-memory job state — survives as long as the process is running
_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "processed": 0,
    "updated": 0,
    "errors": 0,
    "fallbacks": 0,
    "current_title": None,
    "max_workers": 1,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _rescore_one(db: Session, item_id: int):
    """Rescore a single article. Returns True if the article was updated,
    False if no-op, None if LLM fell back (article will be retried).

    v2 additions:
      - Saves source_credibility on the article
      - Writes per-claim data to NarrativeFrameMention (extracted_text + claim_meta)
      - Stages candidate_new_frames into the candidate_frames table
    """
    import json as _json
    from app.models import (
        SourceItem, NarrativeFrameMention, CandidateFrame,
    )
    from app.services import campaign_analysis, race_relevance
    from app.services.campaign_analysis import framing_to_action
    from app.services.ingestion import _compute_priority_score
    from app.services import scoring

    item = db.get(SourceItem, item_id)
    if not item:
        return False

    analysis = campaign_analysis.analyze(db, item)
    if analysis.get("_used_fallback"):
        return None  # signals fallback — not counted as processed

    # ---- Article-level fields (back-compat columns the dashboard reads) ----
    if analysis.get("one_sentence"):
        item.summary = analysis["one_sentence"]

    item.race_relevance_score = analysis["relevance_score"]
    item.race_relevance_label = race_relevance._label(analysis["relevance_score"])
    item.archived_as_irrelevant = not analysis["relevant"]
    # Rescore may upgrade a previously-dismissed item back to relevant — clear
    # the stale dismiss flag so it reappears in the review queue.
    if analysis["relevant"] and item.dismissed:
        item.dismissed = False
    item.actionability_label = framing_to_action(analysis["framing"])
    item.sentiment = analysis.get("sentiment", "neutral")
    item.source_credibility = analysis.get("source_credibility", "medium")

    if analysis.get("needs_attention"):
        item.urgency = "high"
    elif analysis["relevant"]:
        item.urgency = "medium"
    else:
        item.urgency = "low"

    item.priority_score = _compute_priority_score(db, item)
    item.evidence_score = scoring.compute_evidence_score(item)

    # ---- v2: per-claim writes to NarrativeFrameMention ----
    # Strategy: one NFM row per (frame_id, source_item_id). When multiple
    # claims in this article match the same frame, prefer the higher-confidence
    # one and store its quote + metadata. Unique constraint enforces this.
    claims = analysis.get("extracted_claims") or []
    # Group claims by frame_id, keeping the highest-confidence per frame.
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    per_frame_best: dict[int, dict] = {}
    for c in claims:
        for fid in c.get("matched_frame_ids", []):
            existing = per_frame_best.get(fid)
            if (
                existing is None
                or confidence_rank.get(c["confidence"], 0)
                   > confidence_rank.get(existing["confidence"], 0)
            ):
                per_frame_best[fid] = c

    if per_frame_best:
        # Delete existing NFM rows for this article so we don't accumulate
        # stale matches across rescores. Cheap — usually 0-5 rows.
        db.query(NarrativeFrameMention).filter(
            NarrativeFrameMention.source_item_id == item.id
        ).delete(synchronize_session=False)
        # Cache the active frames by id so the verifier can look up the
        # frame name/description without an extra query per claim.
        from app.models import NarrativeFrame
        from app.services.extract_verifier import verify_match
        frame_meta_cache: dict[int, NarrativeFrame] = {
            f.id: f for f in db.query(NarrativeFrame)
            .filter(NarrativeFrame.id.in_(per_frame_best.keys())).all()
        }
        for fid, c in per_frame_best.items():
            # V5 verifier — catches "topically adjacent but wrong" matches
            # that the per-claim matcher missed. Same prompt that purged
            # 552 bad assignments during the V12 cleanup pass.
            frame_meta = frame_meta_cache.get(fid)
            if frame_meta is not None:
                verdict = verify_match(
                    extract=c["quote"],
                    frame_id=fid,
                    frame_name=frame_meta.name,
                    frame_description=frame_meta.description,
                    source_item_id=item.id,
                )
                if not verdict.keep:
                    continue  # verifier already logged the rejection
            db.add(NarrativeFrameMention(
                frame_id=fid,
                source_item_id=item.id,
                confidence=confidence_rank.get(c["confidence"], 2) * 33,  # high=99 med=66 low=33
                matched_by="llm",
                extracted_text=c["quote"],
                claim_meta=_json.dumps({
                    "claim_type": c["claim_type"],
                    "actor_name": c["actor_name"],
                    "actor_role": c["actor_role"],
                    "claim_intensity": c["claim_intensity"],
                    "temporal_frame": c["temporal_frame"],
                    "attribution": c["attribution"],
                    "has_rebuttal": c["has_rebuttal"],
                    "rebuttal_quote": c["rebuttal_quote"],
                    "all_matched_frame_names": c["matched_frame_names"],
                }),
            ))

    # ---- v2: candidate_new_frames staging ----
    cnfs = analysis.get("candidate_new_frames") or []
    if cnfs:
        # Clear out any prior candidate_frames staged from this article — we want
        # this scoring run's proposals to be the source of truth for this article.
        db.query(CandidateFrame).filter(
            CandidateFrame.source_item_id == item.id,
            CandidateFrame.resolved_to_frame_id.is_(None),  # only unresolved
        ).delete(synchronize_session=False)

        # Owner-type sanity check: the LLM periodically inverts the
        # candidate↔opponent assignment (tagging an opponent-attack frame
        # as owner=opponent because Bresnahan IS the opponent, even though
        # the rule is "owner = who BENEFITS"). The heuristic flips clear
        # inversions and leaves ambiguous cases alone.
        from app.services.owner_type_correction import correct_owner_inversion
        from app.services.narrative_frames import _campaign_context as _ctx_fn
        try:
            _ctx = _ctx_fn(db)
            _candidate_name = _ctx.get("candidate") or ""
            _opponent_names = _ctx.get("opponents") or []
        except Exception as _exc:
            # Context fetch failed (test DB, etc.) — fall back to no-correction.
            logger.debug("rescore: campaign-context fetch failed: %s", _exc)
            _candidate_name, _opponent_names = "", []

        for cnf in cnfs:
            corrected_owner, correction_reason = correct_owner_inversion(
                suggested_name=cnf["suggested_name"],
                proposed_owner_type=cnf["owner_type"],
                candidate_name=_candidate_name,
                opponent_names=_opponent_names,
            )
            if correction_reason:
                logger.info(
                    "rescore: corrected owner_type for proposed frame %r "
                    "(article %d): %s → %s — %s",
                    cnf["suggested_name"], item.id, cnf["owner_type"],
                    corrected_owner, correction_reason,
                )
            db.add(CandidateFrame(
                source_item_id=item.id,
                suggested_name=cnf["suggested_name"],
                owner_type_hint=corrected_owner,
                evidence_quote=cnf["evidence_quote"],
                reasoning=cnf["reasoning"],
            ))

    db.commit()
    return True


def _process_item(item_id: int, pinned_provider) -> None:
    """Worker task: score one article end-to-end. Catches every exception
    so a single bad article never kills its worker thread.

    `pinned_provider` is set as the thread's get_ingestion_provider() override
    so this worker uses exactly one LLM key. Other workers (other threads)
    have their own pinned providers — no shared FallbackProvider lock, no
    cascading rate-limit stalls when one key is exhausted.
    """
    from app.db import SessionLocal
    from app.models import SourceItem
    from app.services.llm_provider import (
        set_thread_provider, ProviderRateLimitError,
    )

    # Cooperative cancellation — cheap check without the lock.
    if not _state["running"]:
        return

    # Pin this thread to its assigned LLM key for the duration of the call.
    # Pinning is idempotent — re-setting the same provider for the same
    # thread is a no-op, so it's safe to do per-item.
    set_thread_provider(pinned_provider)

    try:
        # Score with retry on per-key rate limits. Since each worker holds
        # exactly one key, rate-limiting means we must wait — there's no
        # other key to fail over to. 3 retries with exponential backoff
        # covers the worst-case per-minute rate-limit recovery window.
        #
        # Title read + scoring share one session to halve DB pool pressure
        # (16 workers × 1 session vs × 2 — matters when keys retry-loop).
        updated = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with SessionLocal() as db:
                    item = db.get(SourceItem, item_id)
                    if item:
                        title = (item.title or "")[:60]
                        with _lock:
                            _state["current_title"] = title
                    updated = _rescore_one(db, item_id)
                last_exc = None
                break
            except ProviderRateLimitError as e:
                last_exc = e
                # Parse "try again in 2m31s" from Groq/Gemini messages when
                # present — they tell us exactly when quota frees up. Falls
                # back to fixed backoff for providers that don't include it.
                import re
                msg = str(e)
                m = re.search(r"try again in (\d+)m([\d.]+)s", msg)
                m2 = re.search(r"try again in ([\d.]+)s", msg) if not m else None
                if m:
                    suggested = int(m.group(1)) * 60 + float(m.group(2))
                elif m2:
                    suggested = float(m2.group(1))
                else:
                    suggested = 30 * (attempt + 1)  # 30s, 60s, 90s
                # Add 10s safety margin + cap at 5 min to bound worst case.
                backoff = min(int(suggested) + 10, 300)
                logger.warning(
                    "rescore: worker rate-limited on item %d (attempt %d) — "
                    "sleeping %ds (provider suggested %.0fs)",
                    item_id, attempt + 1, backoff, suggested,
                )
                time.sleep(backoff)
        if last_exc is not None:
            raise last_exc

        with _lock:
            if updated is None:
                _state["fallbacks"] += 1
            else:
                _state["processed"] += 1
                if updated:
                    _state["updated"] += 1

    except Exception as e:
        # Never propagate — Future.result() would re-raise and pollute logs.
        # Log once, count the error, move on. Worker stays alive.
        logger.warning(
            "rescore: worker failed on item %d: %s", item_id, e, exc_info=False,
        )
        with _lock:
            _state["processed"] += 1
            _state["errors"] += 1


def _load_providers() -> list:
    """Unwrap the ingestion provider chain into a list of individual
    providers, one per LLM key. Probes each with a tiny test call and
    drops any that are rate-limited / dead, so the worker pool only
    contains keys that can actually serve requests right now.

    Special path: when OPENAI_RESCORE_MODEL + OPENAI_API_KEY are both set,
    returns a single OpenAIProvider for the rescore — paid, fast, JSON-mode.
    Ongoing ingestion is NOT affected (it always uses get_ingestion_provider
    which only respects GROQ_INGESTION_MODEL). Skips the liveness probe in
    this path since paid OpenAI doesn't have free-tier daily caps to dodge.

    RESCORE_SKIP_PROVIDERS env var lets us drop providers by classname
    (comma-separated, case-insensitive substring match). Example:
        RESCORE_SKIP_PROVIDERS=cerebras,gemini

    RESCORE_SKIP_PROBE=1 disables the liveness probe (use if probing
    itself burns through quota in an unwanted way).
    """
    import os

    # ── Paid OpenAI path — only when explicitly requested for the rescore ──
    openai_model = os.environ.get("OPENAI_RESCORE_MODEL", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_model and openai_key:
        try:
            from app.services.llm_provider import OpenAIProvider
            prov = OpenAIProvider(api_key=openai_key, model=openai_model)
            logger.info(
                "rescore: using paid OpenAI %s (OPENAI_RESCORE_MODEL set)",
                openai_model,
            )
            return [prov]
        except Exception as e:
            logger.warning(
                "rescore: OPENAI_RESCORE_MODEL set but provider init failed: %s — "
                "falling back to free chain", e,
            )


    skip_raw = os.environ.get("RESCORE_SKIP_PROVIDERS", "").strip().lower()
    skip_terms = [t.strip() for t in skip_raw.split(",") if t.strip()]
    skip_probe = os.environ.get("RESCORE_SKIP_PROBE", "").strip() == "1"

    def _allowed(prov) -> bool:
        cls = type(prov).__name__.lower()
        return not any(term in cls for term in skip_terms)

    def _probe(prov) -> tuple[bool, str]:
        """Tiny test call. Returns (alive, reason). 1-token reply keeps cost
        negligible (~10 tokens total per probe)."""
        try:
            r = prov.complete("Reply with the single word OK.")
            return (True, "ok") if r and r.strip() else (False, "empty response")
        except Exception as e:
            return (False, str(e)[:80])

    try:
        from app.services.llm_provider import (
            get_ingestion_provider,
            FallbackProvider,
            MockLLMProvider,
        )
        p = get_ingestion_provider()
        if isinstance(p, MockLLMProvider):
            return [p]  # no real keys — single worker
        if isinstance(p, FallbackProvider):
            candidates = [
                prov for prov in p._providers
                if not isinstance(prov, MockLLMProvider) and _allowed(prov)
            ]
            if skip_terms:
                logger.info(
                    "rescore: skipping providers matching %s — %d/%d candidates",
                    skip_terms, len(candidates), len(p._providers),
                )
            if skip_probe or not candidates:
                return candidates or [
                    prov for prov in p._providers
                    if not isinstance(prov, MockLLMProvider)
                ]

            # Probe each candidate in parallel — drops dead keys before workers
            # waste 3 retry cycles on them. ~2-5 sec total at 9 keys.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
                results = list(pool.map(_probe, candidates))
            alive = []
            for prov, (ok, reason) in zip(candidates, results):
                if ok:
                    alive.append(prov)
                else:
                    logger.warning(
                        "rescore: dropping dead key %s — %s",
                        type(prov).__name__, reason,
                    )
            if not alive:
                logger.error(
                    "rescore: all %d candidate keys failed probe — using full "
                    "list as last resort", len(candidates),
                )
                return candidates
            logger.info(
                "rescore: %d/%d keys passed liveness probe",
                len(alive), len(candidates),
            )
            return alive
        return [p] if _allowed(p) else [p]
    except Exception as e:
        logger.warning("rescore: provider load failed (%s) — defaulting to 1", e)
        return []


def _run_rescore(
    item_ids: list[int],
    max_workers: int,
    auto_rematch: bool,
) -> None:
    """Background thread: fan items out across a thread pool, one worker per
    LLM provider key. Each worker is pinned to its own key — no shared chain,
    no thundering-herd rate-limit cascades.

    Returns only when every item has been processed or the job was stopped
    via stop_rescore()."""
    providers = _load_providers()
    if not providers:
        logger.error("rescore: no LLM providers available — aborting")
        with _lock:
            _state["running"] = False
            _state["finished_at"] = datetime.utcnow().isoformat()
        return

    # Normally workers == providers (one key each, no contention). But when
    # the only provider is a paid OpenAI tier with high per-key rate limits,
    # we allow more workers to share the same key — OpenAI client is thread-safe
    # and tier 1 supports ~80 req/min from one key.
    from app.services.llm_provider import OpenAIProvider, FallbackProvider
    is_single_openai = (
        len(providers) == 1
        and isinstance(providers[0], OpenAIProvider)
        and not isinstance(providers[0], FallbackProvider)
    )
    if is_single_openai:
        effective_workers = max_workers  # honor the request; OpenAI handles concurrency
    else:
        # Free-tier providers: one worker per key to avoid rate-limit cascades.
        effective_workers = min(max_workers, len(providers))

    with _lock:
        _state["running"] = True
        _state["started_at"] = datetime.utcnow().isoformat()
        _state["finished_at"] = None
        _state["total"] = len(item_ids)
        _state["processed"] = 0
        _state["updated"] = 0
        _state["errors"] = 0
        _state["fallbacks"] = 0
        _state["current_title"] = None
        _state["max_workers"] = effective_workers

    logger.info(
        "rescore: starting %d items across %d workers (one LLM key each)",
        len(item_ids), effective_workers,
    )

    stopped_early = False
    futures: list[Future] = []

    try:
        with ThreadPoolExecutor(
            max_workers=effective_workers, thread_name_prefix="rescore"
        ) as pool:
            # Round-robin items across providers. Worker i gets every Nth
            # item starting from i, so each provider handles ~total/N items
            # over the course of the run.
            for idx, iid in enumerate(item_ids):
                # Round-robin: workers spread across providers. If workers >
                # providers (OpenAI case), multiple workers share each provider.
                pinned = providers[idx % len(providers)]
                futures.append(pool.submit(_process_item, iid, pinned))

            # Watch for stop. We poll _state["running"] periodically rather
            # than join the pool blindly, so stop_rescore() takes effect within
            # a couple seconds.
            while True:
                done = sum(1 for f in futures if f.done())
                if done >= len(futures):
                    break
                if not _state["running"]:
                    stopped_early = True
                    cancelled = 0
                    for f in futures:
                        if f.cancel():
                            cancelled += 1
                    logger.info(
                        "rescore: stop requested — cancelled %d queued items, "
                        "waiting for in-flight to finish",
                        cancelled,
                    )
                    break
                time.sleep(2.0)
            # ThreadPoolExecutor.__exit__ joins all remaining (in-flight) workers.
    except Exception as e:
        logger.exception("rescore: pool error: %s", e)

    with _lock:
        _state["running"] = False
        _state["finished_at"] = datetime.utcnow().isoformat()
        _state["current_title"] = None
        snapshot = dict(_state)

    logger.info(
        "rescore: done. processed=%d updated=%d errors=%d fallbacks=%d "
        "stopped_early=%s",
        snapshot["processed"], snapshot["updated"], snapshot["errors"],
        snapshot["fallbacks"], stopped_early,
    )

    # Auto-trigger rematch on clean completion so frame matches reflect the
    # freshly scored articles. Skipped if the user stopped the job manually.
    if auto_rematch and not stopped_early:
        try:
            from app.services.scheduler import enqueue_rematch
            logger.info("rescore: auto-triggering rematch after completion")
            enqueue_rematch(days_back=365)
        except Exception as exc:
            logger.warning("rescore: auto-rematch trigger failed: %s", exc)


def start_rescore(
    db: Session,
    delay_seconds: float = 2.5,  # legacy, ignored — kept for backward compat
    only_unscored: bool = False,
    auto_rematch: bool = False,
    max_workers: int | None = None,
) -> dict:
    """Start a background rescore job. Returns immediately.

    only_unscored=True skips articles that already have a summary — used after
    backfill so we only process newly ingested articles, not the full corpus.
    auto_rematch=True triggers rematch automatically when the job finishes.
    max_workers=None auto-sizes to one worker per loaded LLM key.

    delay_seconds is retained for backward compatibility with existing callers
    but is no longer used — throughput is now controlled by worker count,
    not inter-call sleep. FallbackProvider's round-robin spreads load across
    keys, and WAL mode in SQLite handles concurrent writes.
    """
    _ = delay_seconds  # silence linter — intentionally unused
    from app.models import SourceItem

    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "A rescore job is already running."}

    q = db.query(SourceItem.id).filter(
        SourceItem.raw_text.isnot(None), SourceItem.raw_text != ""
    )
    if only_unscored:
        # "Scored" means race_relevance_score is set — NOT summary. The LLM
        # only writes summary for relevant items, so summary IS NULL would
        # re-queue every irrelevant article on every restart.
        q = q.filter(SourceItem.race_relevance_score.is_(None))

    # Oldest first — backfill articles are oldest, so they get priority.
    # Parallelism means strict order isn't preserved at completion, but the
    # submission order still biases the oldest items to run first.
    item_ids = [row[0] for row in q.order_by(SourceItem.created_at.asc()).all()]

    if not item_ids:
        return {"started": False, "reason": "No articles found to score."}

    if max_workers is None:
        max_workers = len(_load_providers()) or 1
    # Sanity cap — protect against absurd values.
    max_workers = max(1, min(int(max_workers), 32))

    thread = threading.Thread(
        target=_run_rescore,
        args=(item_ids, max_workers, auto_rematch),
        daemon=True,
        name="rescore-orchestrator",
    )
    thread.start()

    # Throughput estimate: assume ~10s per article end-to-end (LLM + DB),
    # divided across workers. Real wall time varies with rate-limit jitter.
    est_minutes = round(len(item_ids) * 10 / max_workers / 60, 1)

    return {
        "started": True,
        "total": len(item_ids),
        "max_workers": max_workers,
        "estimated_minutes": est_minutes,
    }


def stop_rescore() -> dict:
    with _lock:
        if not _state["running"]:
            return {"stopped": False, "reason": "No job running."}
        _state["running"] = False
    return {"stopped": True}
