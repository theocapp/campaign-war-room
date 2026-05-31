"""Search provider abstraction for monitor ingestion."""
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _load_search_env() -> None:
    """Load local env files without overriding explicitly exported values."""
    path = Path(__file__).resolve()
    backend_env = path.parents[2] / ".env"
    project_env = path.parents[3] / ".env"
    load_dotenv(backend_env, override=False)
    load_dotenv(project_env, override=False)


@dataclass
class SearchResult:
    title: str
    url: str
    source_name: str | None = None
    snippet: str | None = None
    published_at: datetime | None = None


@dataclass
class SearchResponse:
    results: list[SearchResult]
    provider: str
    message: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        ...


class MockSearchProvider:
    name = "mock"

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        message = (
            "Mock search provider is active. No live web search was performed; "
            "configure a real search provider before expecting automatic external results."
        )
        logger.info(message)
        return SearchResponse(results=[], provider=self.name, message=message)


def _source_name_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(value)
    except Exception:
        return None


class TavilySearchProvider:
    """Tavily web search provider with round-robin multi-key support.

    Tavily's free dev keys are limited to 1000 credits/month each. We accept
    multiple keys (comma-separated TAVILY_API_KEY or numbered
    TAVILY_API_KEY_1 / _2 / ... in .env) and rotate between them. When one
    returns a quota/auth error (see _EXHAUSTED_CODES) we mark it as
    exhausted for the rest of the process lifetime and continue with the
    others.
    """
    name = "tavily"
    endpoint = "https://api.tavily.com/search"
    # HTTP codes that mean "this key can't be used again until restart":
    #   401 invalid / revoked
    #   402 quota exhausted (some integrations)
    #   429 rate-limited (we treat this as exhausted-this-session for safety;
    #       the next process restart will retry)
    #   432 Tavily's non-standard "monthly credit limit exceeded" response.
    #       Without this, an exhausted key falls into the generic except branch
    #       and the rotation keeps re-trying it on every request — observed
    #       2026-05-29 with the live keys.
    _EXHAUSTED_CODES = (401, 402, 429, 432)

    def __init__(self, api_keys: list[str]):
        # Preserve insertion order; .keys list is treated as a round-robin.
        # _exhausted holds keys we've seen 401/402/429 from this process —
        # we don't retry them until restart.
        self.keys: list[str] = [k for k in api_keys if k]
        self._cursor = 0
        self._exhausted: set[str] = set()
        if not self.keys:
            raise ValueError("TavilySearchProvider needs at least one API key")

    def _next_key(self) -> str | None:
        """Round-robin to the next un-exhausted key, or None if all exhausted."""
        for _ in range(len(self.keys)):
            k = self.keys[self._cursor % len(self.keys)]
            self._cursor += 1
            if k not in self._exhausted:
                return k
        return None

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        last_err: str | None = None
        # Try each key in turn — if we get a quota/auth error, mark and rotate.
        attempts = 0
        max_attempts = len(self.keys)
        while attempts < max_attempts:
            attempts += 1
            key = self._next_key()
            if key is None:
                codes = "/".join(str(c) for c in self._EXHAUSTED_CODES)
                msg = f"Tavily: all keys exhausted ({codes}). Add more or wait for monthly reset."
                logger.warning(msg)
                return SearchResponse(results=[], provider=self.name, message=msg)
            try:
                resp = httpx.post(
                    self.endpoint,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {key}",
                    },
                    json={
                        "query": query,
                        "max_results": min(max(limit, 1), 20),
                        "search_depth": "basic",
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                    timeout=15,
                )
                if resp.status_code in self._EXHAUSTED_CODES:
                    # See _EXHAUSTED_CODES for the meaning of each.
                    logger.warning(
                        "Tavily: key …%s returned %d — marking exhausted, rotating",
                        key[-4:], resp.status_code,
                    )
                    self._exhausted.add(key)
                    last_err = f"key …{key[-4:]} → HTTP {resp.status_code}"
                    continue
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                # Network/timeout/etc — don't blacklist the key, but DO try
                # the next one. The prior behavior bailed the whole query on
                # any transient blip even when other keys would have worked.
                last_err = f"Tavily request failed (key …{key[-4:]}): {exc}"
                logger.warning(last_err)
                continue

            raw_results = payload.get("results") or []
            results: list[SearchResult] = []
            for row in raw_results[:limit]:
                url = row.get("url")
                title = row.get("title") or url
                if not url or not title:
                    continue
                results.append(SearchResult(
                    title=title,
                    url=url,
                    source_name=_source_name_from_url(url),
                    snippet=row.get("content") or row.get("snippet"),
                    published_at=_parse_date(row.get("published_date") or row.get("published_at")),
                ))
            return SearchResponse(results=results, provider=self.name)
        return SearchResponse(
            results=[], provider=self.name,
            message=f"Tavily: all {len(self.keys)} keys failed. Last error: {last_err}",
        )


def _load_tavily_keys() -> list[str]:
    """Read Tavily API keys from env. Mirrors the Groq/Cerebras pattern:

      TAVILY_API_KEY          = key #1
      TAVILY_API_KEY_2 … _10  = extra keys (gap-tolerant, empty slots skipped)

    Also accepts a legacy comma-separated TAVILY_API_KEY for back-compat with
    the old format. Order is: base, then numbered slots. Empty / whitespace
    values and duplicates are silently dropped.

    The provider round-robins between the returned keys and quarantines any
    that return 401/402/429/432 (see TavilySearchProvider._EXHAUSTED_CODES).
    """
    keys: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        v = value.strip()
        if v and v not in seen:
            keys.append(v)
            seen.add(v)

    # Base slot accepts a single key OR the legacy CSV format.
    base = os.getenv("TAVILY_API_KEY", "")
    if "," in base:
        for k in base.split(","):
            _add(k)
    else:
        _add(base)

    # Numbered extras. Bounded loop matches services/llm_provider.py — gaps
    # are fine, the loop doesn't bail on the first empty slot.
    for n in range(2, 11):
        _add(os.getenv(f"TAVILY_API_KEY_{n}", ""))
    return keys


class CachedSearchProvider:
    """Disk-backed cache wrapper for any SearchProvider.

    Stores every result in `search_result_cache` (SQLite table) keyed on
    (provider, query, limit). Hits return instantly without touching the
    inner provider. Misses fall through to the inner provider and store
    the response before returning.

    TTL defaults to 7 days, configurable via SEARCH_CACHE_TTL_DAYS env
    var. Set SEARCH_CACHE_DISABLED=1 to bypass the cache entirely (useful
    for one-off freshness or when debugging the inner provider).

    Negative caching: we DO cache empty results — same query, same empty
    answer, no point in retrying every time. Errors from the inner
    provider (network down, all keys exhausted) are NOT cached — caller
    sees the `message` field set, and a subsequent retry has a real
    chance of succeeding.
    """
    name = "cached"

    def __init__(self, inner: "SearchProvider", ttl_days: int = 7):
        self._inner = inner
        self.name = f"cached:{inner.name}"
        self._ttl_seconds = ttl_days * 86400
        self._disabled = os.environ.get("SEARCH_CACHE_DISABLED", "").strip() == "1"

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        # Lazy imports — search_provider gets imported early during app
        # startup, before the DB is fully initialized. Deferring these
        # avoids a circular-init issue.
        from app.db import SessionLocal
        from app.models import SearchResultCache

        if self._disabled:
            return self._inner.search(query, limit)

        # Cache lookup
        try:
            with SessionLocal() as db:
                row = (
                    db.query(SearchResultCache)
                    .filter_by(provider=self._inner.name, query=query, limit_n=limit)
                    .first()
                )
                if row and self._fresh(row):
                    return self._row_to_response(row)
                # Stale — fall through to live fetch, then update in place
        except Exception as e:
            # Don't let cache failures block real searches.
            logger.warning("search cache lookup failed: %s", e)

        # Cache miss / stale — call the inner provider
        response = self._inner.search(query, limit)

        # Don't cache transient errors (message set + empty results). Empty
        # results with no message are a legit "nothing found" answer worth
        # caching.
        if response.message and not response.results:
            return response

        try:
            with SessionLocal() as db:
                self._upsert(db, query, limit, response)
                db.commit()
        except Exception as e:
            logger.warning("search cache write failed: %s", e)
        return response

    def _fresh(self, row: "SearchResultCache") -> bool:
        age = (datetime.utcnow() - row.cached_at).total_seconds()
        return age < self._ttl_seconds

    def _row_to_response(self, row: "SearchResultCache") -> SearchResponse:
        import json as _json
        try:
            raw = _json.loads(row.results_json or "[]")
        except Exception:
            raw = []
        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                source_name=r.get("source_name"),
                snippet=r.get("snippet"),
                published_at=_parse_date(r.get("published_at")),
            )
            for r in raw
        ]
        return SearchResponse(
            results=results,
            provider=row.provider,
            message=row.message,
        )

    def _upsert(self, db, query: str, limit: int, response: SearchResponse) -> None:
        import json as _json
        from app.models import SearchResultCache

        payload = _json.dumps([
            {
                "title": r.title,
                "url": r.url,
                "source_name": r.source_name,
                "snippet": r.snippet,
                "published_at": r.published_at.isoformat() if r.published_at else None,
            }
            for r in response.results
        ])
        existing = (
            db.query(SearchResultCache)
            .filter_by(provider=self._inner.name, query=query, limit_n=limit)
            .first()
        )
        if existing:
            existing.results_json = payload
            existing.message = response.message
            existing.cached_at = datetime.utcnow()
        else:
            db.add(SearchResultCache(
                provider=self._inner.name,
                query=query,
                limit_n=limit,
                cached_at=datetime.utcnow(),
                results_json=payload,
                message=response.message,
            ))


def get_search_provider() -> SearchProvider:
    """Return the configured search provider, wrapped in a disk-backed
    cache by default. Set SEARCH_CACHE_DISABLED=1 to skip the cache.
    """
    _load_search_env()
    provider = os.getenv("SEARCH_PROVIDER", "mock").strip().lower()
    inner: SearchProvider
    if provider == "tavily":
        keys = _load_tavily_keys()
        if not keys:
            message = "SEARCH_PROVIDER=tavily but no TAVILY_API_KEY(s) configured; falling back to mock search provider."
            logger.warning(message)
            inner = MockSearchProvider()
        else:
            logger.info("Tavily provider initialized with %d key(s)", len(keys))
            inner = TavilySearchProvider(keys)
    elif provider == "mock":
        inner = MockSearchProvider()
    else:
        logger.warning("Unknown SEARCH_PROVIDER=%s; falling back to mock search provider.", provider)
        inner = MockSearchProvider()

    # Always wrap — mock provider too, for consistency in tests. The cache
    # is harmless for mock (empty results) and the singleton wrapping
    # behavior stays uniform.
    try:
        ttl_days = int(os.environ.get("SEARCH_CACHE_TTL_DAYS", "7"))
    except ValueError:
        ttl_days = 7
    return CachedSearchProvider(inner, ttl_days=ttl_days)
