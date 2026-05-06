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
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, limit: int = 10) -> SearchResponse:
        try:
            resp = httpx.post(
                self.endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
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
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            message = f"Tavily Search request failed: {exc}"
            logger.warning(message)
            return SearchResponse(results=[], provider=self.name, message=message)

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


def get_search_provider() -> SearchProvider:
    _load_search_env()
    provider = os.getenv("SEARCH_PROVIDER", "mock").strip().lower()
    if provider == "tavily":
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            message = "SEARCH_PROVIDER=tavily but TAVILY_API_KEY is missing; falling back to mock search provider."
            logger.warning(message)
            return MockSearchProvider()
        return TavilySearchProvider(api_key)
    if provider != "mock":
        logger.warning("Unknown SEARCH_PROVIDER=%s; falling back to mock search provider.", provider)
    return MockSearchProvider()
