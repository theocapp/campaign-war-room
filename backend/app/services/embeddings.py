"""
Embeddings service — Gemini primary, OpenAI fallback, in-process cache.

Used by:
  - Frame variant clustering (group similar quotes within a frame)
  - Candidate-frame deduplication (detect when proposed frames are duplicates)
  - Future trend×narrative term mapping

Primary model: Google's gemini-embedding-001 (3072-dim, free tier).
Fallback model: OpenAI text-embedding-3-large with dimensions=3072
(same vector size — embeddings are cosine-comparable across providers).

Why a fallback at all: we kept silently failing when Gemini's daily quota
hit. The clusterer would return [None]*N, then 0 clusters, then UI showed
"no narratives detected" with no error. The fallback keeps the system
operating; the EmbedStats surface gives callers a way to surface failures
to the user.

In-process cache: text→embedding keyed by (sha256, task_type). Survives
within a process, lost on uvicorn restart. Trades RAM for fewer paid API
calls when the same candidate_frame is re-clustered across refresh cycles.

Rate limits (Gemini free tier, 2026):
  - 1,500 requests/minute per key
  - 1M tokens/minute per key
  - ~1,000 requests/day shared across keys on the same GCP project
"""
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Ensure .env is loaded before we read API keys at module-init time. The
# pool builds `_keys` from os.environ when this module is imported — if a
# caller imports `embeddings` before something else has called load_dotenv,
# the pool would come up empty. Defensive load with override=False so we
# never clobber values already set by uvicorn / the system env.
try:
    from dotenv import load_dotenv
    _env_paths = [
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"),  # repo root
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),         # backend/
    ]
    for _p in _env_paths:
        if os.path.exists(_p):
            load_dotenv(_p, override=False)
except Exception:
    logger.debug("embeddings: .env autoload skipped", exc_info=True)

# Suppress Google's deprecation warning at import time — we know.
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
import google.generativeai as genai  # noqa: E402

_GEMINI_MODEL = "models/gemini-embedding-001"
_OPENAI_MODEL = "text-embedding-3-large"


def current_primary_model_name() -> str:
    """Return the identifier of the currently-configured primary embedding
    model. Used by callers that cache embeddings to detect when the model
    has changed and re-embed.

    Mirrors the provider-selection logic in embed_texts: Gemini primary
    when EMBED_USE_GEMINI=1, else OpenAI.
    """
    use_gemini = os.environ.get("EMBED_USE_GEMINI", "").strip() in ("1", "true", "yes")
    return _GEMINI_MODEL if use_gemini else _OPENAI_MODEL
_EMBEDDING_DIM = 3072  # both Gemini and OpenAI configured to this dim → interchangeable
_TASK_DEFAULT = "SEMANTIC_SIMILARITY"

_GEMINI_BATCH_SIZE = 25
# Empirically, batches of 100 immediately 429 on the gemini-embedding-001
# free tier (likely a payload-size rather than RPM limit — batches of 50
# work, batches of 100 don't). 25 gives comfortable headroom across all
# 3 keys and keeps a 200-row run at ~8 batches, still well under per-
# minute limits.

_OPENAI_BATCH_SIZE = 256
# OpenAI's API accepts up to 2048 inputs per request, but smaller batches
# keep failure blast radius small (a 429 takes out 256 instead of 2048).

# In-process cache. Keyed by (sha256-of-text[:16], task_type). Lost on
# process restart — that's fine; the daily scheduler will rewarm it.
_EMBED_CACHE: dict[tuple[str, str], list[float]] = {}
_EMBED_CACHE_MAX = 10000  # roughly 120 MB at 3072 floats × 10k entries
_EMBED_CACHE_EVICT_BATCH = 1000  # evict this many when at cap
_cache_lock = threading.Lock()


@dataclass
class EmbedStats:
    """Per-call stats. Read via get_last_embed_stats() after embed_texts().

    Callers use this to distinguish "no narratives" from "system broken."
    """
    n_total: int = 0
    n_cached: int = 0          # served from in-process cache (no API call)
    n_gemini_ok: int = 0       # produced by Gemini
    n_openai_ok: int = 0       # produced by OpenAI fallback
    n_failed: int = 0          # neither path worked
    gemini_quota_exhausted: bool = False
    openai_attempted: bool = False
    openai_available: bool = False
    openai_error: str = ""
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        parts = [f"total={self.n_total}", f"cached={self.n_cached}",
                 f"gemini={self.n_gemini_ok}", f"openai={self.n_openai_ok}",
                 f"failed={self.n_failed}"]
        if self.gemini_quota_exhausted:
            parts.append("gemini_quota_exhausted=True")
        if self.openai_error:
            parts.append(f"openai_err={self.openai_error[:60]!r}")
        return " ".join(parts)


_LAST_STATS: EmbedStats = EmbedStats()
_stats_lock = threading.Lock()


def get_last_embed_stats() -> EmbedStats:
    """Return stats from the most recent embed_texts() call.

    NOT thread-safe across concurrent embed_texts() callers — if you need
    that, capture stats inline. For the daily-clustering use case where one
    refresh runs at a time, this is fine.
    """
    return _LAST_STATS


# ---------------- Cache helpers ----------------
#
# CRITICAL — cross-provider cache isolation
# The cache key is (text_hash, task_type, PROVIDER). It MUST include the
# provider name because Gemini and OpenAI embeddings live in completely
# different semantic spaces — even at the same `dimensions=3072`, cosine
# similarity between a Gemini vector and an OpenAI vector for the same
# text is essentially noise.
#
# Before this isolation was added (2026-05-24), the cache silently mixed
# Gemini vectors and OpenAI fallback vectors. Downstream clustering
# (candidate_frame_promoter) computed similarity across the mixed cache
# and got broken results: clusters that should have formed didn't,
# because their members happened to be from different providers. Symptom:
# the live API returned 4 clusters where a fresh process produced 19.
#
# The provider tag means a single text can have multiple cached entries
# (one per provider). That's intentional — those entries represent
# different vector spaces and should not be substituted for each other.

# Provider identifier constants. Used as cache-key salt + EmbedStats fields.
_PROVIDER_GEMINI = "gemini"
_PROVIDER_OPENAI = "openai"

# Comparing vectors across providers is not meaningful. Callers MUST use
# the same provider's vectors for cosine similarity. The clusterer doesn't
# yet enforce this directly — it just trusts the cache to be coherent.
# When asked to embed N texts, embed_texts() now uses the SAME provider
# for all of them: it picks one (Gemini if available, else OpenAI) and
# either fulfills the whole request from that provider or returns Nones.
# No more partial provider mixing within a single embed_texts call.


def _cache_key(text: str, task_type: str, provider: str) -> tuple[str, str, str]:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return (h, task_type, provider)


def _cache_get(text: str, task_type: str, provider: str) -> Optional[list[float]]:
    with _cache_lock:
        return _EMBED_CACHE.get(_cache_key(text, task_type, provider))


def _cache_put(text: str, task_type: str, provider: str, emb: list[float]) -> None:
    with _cache_lock:
        if len(_EMBED_CACHE) >= _EMBED_CACHE_MAX:
            # Simple FIFO eviction — drop oldest insertion-order keys.
            # We don't bother with true LRU because the access pattern here
            # is "refresh-then-idle", not random access.
            for k in list(_EMBED_CACHE.keys())[:_EMBED_CACHE_EVICT_BATCH]:
                del _EMBED_CACHE[k]
        _EMBED_CACHE[_cache_key(text, task_type, provider)] = emb


def clear_cache() -> int:
    """Drop the in-process embedding cache. Returns the number of entries dropped.

    Useful for tests and for the rare case where you've embedded a lot of
    one-off junk text and want to free RAM. Not called automatically.
    """
    with _cache_lock:
        n = len(_EMBED_CACHE)
        _EMBED_CACHE.clear()
    return n


# ---------------- Gemini path ----------------

class _KeyPool:
    """Round-robin pool of Gemini API keys with simple rate-limit cooldown."""

    def __init__(self) -> None:
        self._keys = self._load_keys()
        self._idx = 0
        self._lock = threading.Lock()
        self._cooldowns: dict[int, float] = {}  # idx → unix-ts when usable again

    @staticmethod
    def _load_keys() -> list[str]:
        keys = []
        primary = os.environ.get("GEMINI_API_KEY", "").strip()
        if primary:
            keys.append(primary)
        for n in range(2, 11):
            k = os.environ.get(f"GEMINI_API_KEY_{n}", "").strip()
            if k:
                keys.append(k)
        return keys

    def next_key(self) -> Optional[str]:
        """Return the next not-cooling-down key, or None if all are cooling.

        Unlike before, returns None promptly when all keys are exhausted —
        no `time.sleep()` inside the pool. Callers handle the fallback.
        """
        if not self._keys:
            return None
        with self._lock:
            now = time.time()
            for _ in range(len(self._keys)):
                idx = self._idx % len(self._keys)
                self._idx += 1
                cooldown_until = self._cooldowns.get(idx, 0)
                if cooldown_until <= now:
                    return self._keys[idx]
            return None

    def mark_rate_limited(self, key: str, retry_after: float = 60.0) -> None:
        with self._lock:
            try:
                idx = self._keys.index(key)
                self._cooldowns[idx] = time.time() + retry_after
                logger.info("embeddings: gemini key %d cooling down for %.0fs", idx, retry_after)
            except ValueError:
                pass

    def all_exhausted(self) -> bool:
        if not self._keys:
            return True
        with self._lock:
            now = time.time()
            return all(self._cooldowns.get(i, 0) > now for i in range(len(self._keys)))


_pool = _KeyPool()


def _embed_batch_gemini(
    batch: list[str],
    *,
    task_type: str,
    max_retries: int = 3,
) -> list[Optional[list[float]]]:
    """Embed one batch via Gemini. Returns per-text result (None for failed).

    On rate limit: rotates to next key, marks current key in cooldown. After
    max_retries with all keys 429ing, gives up on this batch and returns
    all-Nones. Caller is expected to try OpenAI fallback for the Nones.
    """
    out: list[Optional[list[float]]] = [None] * len(batch)
    attempt = 0
    while attempt < max_retries:
        key = _pool.next_key()
        if not key:
            logger.info("embeddings: all gemini keys cooling down, giving up batch")
            break
        try:
            genai.configure(api_key=key)
            result = genai.embed_content(
                model=_GEMINI_MODEL,
                content=batch,
                task_type=task_type,
            )
            embeddings = result.get("embedding", [])
            # Single vs batch shape — Gemini returns list[list[float]]
            # for multi-input, list[float] for single.
            if embeddings and not isinstance(embeddings[0], list):
                embeddings = [embeddings]
            for i, e in enumerate(embeddings):
                if i < len(out):
                    out[i] = e
            return out
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                logger.info(
                    "embeddings: gemini rate-limited (attempt %d): %s",
                    attempt + 1, msg[:120],
                )
                _pool.mark_rate_limited(key, retry_after=60.0)
                attempt += 1
                continue
            logger.warning("embeddings: gemini failed: %s", msg[:120])
            break
    return out


# ---------------- OpenAI fallback ----------------

def _openai_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _embed_batch_openai(
    batch: list[str],
) -> tuple[list[Optional[list[float]]], str]:
    """Embed one batch via OpenAI text-embedding-3-large with dimensions=3072.

    Same vector DIMENSION as Gemini but DIFFERENT SEMANTIC SPACE — these
    vectors are NOT cosine-comparable with Gemini vectors. The cache
    tags every entry with its provider so consumers (callers using cosine
    similarity downstream) only ever see vectors from one provider per
    embed_texts call.

    Returns (embeddings_or_None, error_msg). error_msg is "" on success.

    Cost (as of 2026): $0.13/1M tokens for text-embedding-3-large. At ~75
    tokens per row × 200 rows = 15K tokens = ~$0.002 per full refresh.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return [None] * len(batch), "no_openai_key"
    try:
        from openai import OpenAI
    except ImportError:
        return [None] * len(batch), "openai_package_missing"

    try:
        client = OpenAI(api_key=api_key)
        resp = client.embeddings.create(
            model=_OPENAI_MODEL,
            input=batch,
            dimensions=_EMBEDDING_DIM,
        )
        # Sort by index because the API guarantees ordering but defensive.
        results: list[Optional[list[float]]] = [None] * len(batch)
        for d in resp.data:
            if 0 <= d.index < len(batch):
                results[d.index] = d.embedding
        return results, ""
    except Exception as exc:
        return [None] * len(batch), str(exc)[:200]


# ---------------- Public API ----------------

def embed_texts(
    texts: list[str],
    *,
    task_type: str = _TASK_DEFAULT,
    max_retries: int = 3,
) -> list[Optional[list[float]]]:
    """Return embeddings for a batch of texts.

    Provider policy (changed 2026-05-24):
      OpenAI text-embedding-3-large is now the PRIMARY provider. Gemini is
      DISABLED by default — re-enable by setting `EMBED_USE_GEMINI=1` in
      the environment. The switch happened because:
        1. Gemini's daily quota was a recurring operational pain
        2. Cross-provider cache pollution silently broke clustering
        3. OpenAI cost is negligible at this scale (~$0.001 per full refresh
           of 200 candidate_frames, verified empirically 2026-05-24)
        4. `google.generativeai` is deprecated; OpenAI SDK is actively maintained

    Provider coherence (CRITICAL):
      Every returned vector comes from a SINGLE provider. If the primary
      fails for ≥80% of uncached texts, we discard the partial result and
      retry the entire call with the fallback. No mid-call provider mixing,
      ever — that's the bug that produced "19 clusters fresh vs 4 cached"
      before the fix.

    Why not deleted Gemini entirely:
      Kept as opt-in fallback because (a) it's free if quota holds, useful
      for high-volume backfill scenarios, (b) the code is already there +
      tested, deletion is a separate PR's worth of work. Set
      EMBED_USE_GEMINI=1 to flip the policy: Gemini primary, OpenAI fallback.
    """
    global _LAST_STATS
    start_ts = time.time()
    n_total = len(texts)

    if not texts:
        stats = EmbedStats(n_total=0, openai_available=_openai_available())
        with _stats_lock:
            _LAST_STATS = stats
        return []

    # Determine provider preference from env. Default = OpenAI primary.
    use_gemini = os.environ.get("EMBED_USE_GEMINI", "").strip() in ("1", "true", "yes")
    gemini_usable = bool(_pool._keys) and not _pool.all_exhausted()
    openai_usable = _openai_available()

    if use_gemini and gemini_usable:
        primary, fallback = _PROVIDER_GEMINI, _PROVIDER_OPENAI
        primary_usable, fallback_usable = True, openai_usable
    elif openai_usable:
        primary, fallback = _PROVIDER_OPENAI, _PROVIDER_GEMINI
        primary_usable, fallback_usable = True, gemini_usable
    elif gemini_usable:
        # OpenAI not available; fall through to Gemini even without the
        # env flag — better than returning all Nones.
        primary, fallback = _PROVIDER_GEMINI, _PROVIDER_OPENAI
        primary_usable, fallback_usable = True, False
    else:
        # Neither provider available.
        failed_stats = EmbedStats(
            n_total=n_total, n_failed=n_total,
            gemini_quota_exhausted=not gemini_usable,
            openai_available=openai_usable,
            elapsed_seconds=time.time() - start_ts,
        )
        with _stats_lock:
            _LAST_STATS = failed_stats
        logger.warning("embeddings: no provider available — returning all Nones")
        return [None] * n_total

    # Try primary.
    out, primary_stats = _embed_via_provider(
        texts, task_type=task_type, provider=primary, max_retries=max_retries,
    )
    uncached_attempted = primary_stats.n_total - primary_stats.n_cached
    primary_broken = (
        uncached_attempted > 0
        and primary_stats.n_failed / uncached_attempted >= 0.8
    )

    if not primary_broken or not fallback_usable:
        primary_stats.elapsed_seconds = time.time() - start_ts
        with _stats_lock:
            _LAST_STATS = primary_stats
        if primary_stats.n_failed > 0:
            logger.warning("embeddings: %s", primary_stats.summary())
        else:
            logger.info("embeddings: %s", primary_stats.summary())
        return out

    # Primary failed for ≥80% of uncached texts — restart the entire call
    # with the fallback so the output stays single-provider.
    logger.info(
        "embeddings: %s failed %d/%d (≥80%%) — retrying entire call with %s "
        "for provider coherence",
        primary, primary_stats.n_failed, uncached_attempted, fallback,
    )
    out, fb_stats = _embed_via_provider(
        texts, task_type=task_type, provider=fallback, max_retries=max_retries,
    )
    if primary == _PROVIDER_GEMINI:
        fb_stats.gemini_quota_exhausted = True
    fb_stats.elapsed_seconds = time.time() - start_ts
    with _stats_lock:
        _LAST_STATS = fb_stats
    if fb_stats.n_failed > 0:
        logger.warning("embeddings: %s", fb_stats.summary())
    else:
        logger.info("embeddings: %s", fb_stats.summary())
    return out


def _embed_via_provider(
    texts: list[str],
    *,
    task_type: str,
    provider: str,
    max_retries: int = 3,
) -> tuple[list[Optional[list[float]]], EmbedStats]:
    """Embed via ONE provider with cache. Returns (vectors, stats).

    Internal helper for embed_texts(). Encapsulates the cache lookup +
    batched API calls for a single provider so embed_texts() can try
    one provider, observe the outcome, and optionally start over with
    a different provider — all without ever mixing providers in the
    output array.
    """
    stats = EmbedStats(n_total=len(texts), openai_available=_openai_available())
    out: list[Optional[list[float]]] = [None] * len(texts)

    # Step 1: provider-scoped cache lookups.
    uncached_indices: list[int] = []
    for i, text in enumerate(texts):
        cached = _cache_get(text, task_type, provider)
        if cached is not None:
            out[i] = cached
            stats.n_cached += 1
        else:
            uncached_indices.append(i)

    if not uncached_indices:
        return out, stats

    # Step 2: embed via the chosen provider.
    if provider == _PROVIDER_GEMINI:
        for batch_start in range(0, len(uncached_indices), _GEMINI_BATCH_SIZE):
            chunk_idx = uncached_indices[batch_start:batch_start + _GEMINI_BATCH_SIZE]
            chunk_texts = [texts[i] for i in chunk_idx]
            results = _embed_batch_gemini(chunk_texts, task_type=task_type, max_retries=max_retries)
            for j, emb in enumerate(results):
                idx = chunk_idx[j]
                if emb is not None:
                    out[idx] = emb
                    _cache_put(texts[idx], task_type, _PROVIDER_GEMINI, emb)
                    stats.n_gemini_ok += 1
            if _pool.all_exhausted():
                stats.gemini_quota_exhausted = True
                break
    else:  # _PROVIDER_OPENAI
        stats.openai_attempted = True
        for batch_start in range(0, len(uncached_indices), _OPENAI_BATCH_SIZE):
            chunk_idx = uncached_indices[batch_start:batch_start + _OPENAI_BATCH_SIZE]
            chunk_texts = [texts[i] for i in chunk_idx]
            results, err = _embed_batch_openai(chunk_texts)
            if err and not stats.openai_error:
                stats.openai_error = err
            for j, emb in enumerate(results):
                idx = chunk_idx[j]
                if emb is not None:
                    out[idx] = emb
                    _cache_put(texts[idx], task_type, _PROVIDER_OPENAI, emb)
                    stats.n_openai_ok += 1

    stats.n_failed = sum(1 for e in out if e is None)
    return out, stats


def embed_one(text: str, **kwargs) -> Optional[list[float]]:
    """Convenience for single-text embedding."""
    results = embed_texts([text], **kwargs)
    return results[0] if results else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors. Returns -1..1."""
    if not a or not b:
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
