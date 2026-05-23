"""
Gemini embeddings service.

Uses Google's gemini-embedding-001 model (free tier — 1500 req/min) to convert
text to 3072-dimensional embedding vectors. Used by:
  - Frame variant clustering (group similar quotes within a frame)
  - Candidate-frame deduplication (detect when proposed frames are duplicates)
  - Future trend×narrative term mapping (replace keyword-overlap matching)

Rate limits (free tier as of 2026):
  - 1,500 requests/minute per key
  - 1M tokens/minute per key
  - No daily cap on embeddings (separate from generation quota)

With multiple GEMINI_API_KEY_N values configured, we round-robin to spread
load. Most workloads here are well under one key's limit so this is mostly
for resilience.
"""
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Suppress Google's deprecation warning at import time — we know.
import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")
import google.generativeai as genai  # noqa: E402

_MODEL = "models/gemini-embedding-001"
_TASK_DEFAULT = "SEMANTIC_SIMILARITY"
_BATCH_SIZE = 100  # Gemini accepts up to ~100 strings per call


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
        """Return the next available key, or None if all are cooling down."""
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
            # All keys in cooldown — pick the one that recovers soonest.
            soonest_idx = min(self._cooldowns, key=self._cooldowns.get)
            wait = max(0.0, self._cooldowns[soonest_idx] - now)
            time.sleep(min(wait, 5.0))
            return self._keys[soonest_idx]

    def mark_rate_limited(self, key: str, retry_after: float = 60.0) -> None:
        with self._lock:
            try:
                idx = self._keys.index(key)
                self._cooldowns[idx] = time.time() + retry_after
                logger.info("embeddings: key %d cooling down for %.0fs", idx, retry_after)
            except ValueError:
                pass


_pool = _KeyPool()


def embed_texts(
    texts: list[str],
    *,
    task_type: str = _TASK_DEFAULT,
    max_retries: int = 3,
) -> list[Optional[list[float]]]:
    """Return embeddings for a batch of texts. Returns None for any text
    that couldn't be embedded after retries (caller decides how to handle).

    task_type controls how Gemini optimizes the embedding:
      - SEMANTIC_SIMILARITY (default) — for clustering/similarity work
      - RETRIEVAL_DOCUMENT / RETRIEVAL_QUERY — for search
      - CLASSIFICATION — for ML inputs
    """
    if not texts:
        return []
    if not _pool._keys:
        logger.warning("embeddings: no GEMINI_API_KEY configured")
        return [None] * len(texts)

    out: list[Optional[list[float]]] = [None] * len(texts)
    for start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[start:start + _BATCH_SIZE]
        attempt = 0
        while attempt < max_retries:
            key = _pool.next_key()
            if not key:
                logger.warning("embeddings: all keys exhausted, aborting batch")
                break
            try:
                genai.configure(api_key=key)
                result = genai.embed_content(
                    model=_MODEL,
                    content=batch,
                    task_type=task_type,
                )
                embeddings = result.get("embedding", [])
                # Single vs batch shape — Gemini returns list[list[float]]
                # for multi-input, list[float] for single.
                if embeddings and not isinstance(embeddings[0], list):
                    embeddings = [embeddings]
                for i, e in enumerate(embeddings):
                    out[start + i] = e
                break
            except Exception as exc:
                msg = str(exc)
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                    logger.info("embeddings: rate-limited, rotating key (attempt %d)", attempt + 1)
                    _pool.mark_rate_limited(key, retry_after=60.0)
                    attempt += 1
                    continue
                logger.warning("embeddings: failed for batch starting at %d: %s", start, msg[:120])
                break

    return out


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
