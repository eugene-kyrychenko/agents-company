"""Web search via Tavily. Returns [] silently if no API key configured."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from apps.orchestrator.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


async def search(query: str, max_results: int = 5) -> list[SearchHit]:
    """Search the web. Returns empty list if Tavily not configured.

    Tavily has a generous free tier; sign up at tavily.com for an API key.
    """
    if not settings.tavily_api_key:
        logger.info("TAVILY_API_KEY not set — skipping web search for %r", query)
        return []

    # Imported lazily so tests/dry-runs without the package don't break.
    try:
        from tavily import TavilyClient
    except ImportError:
        logger.warning("tavily package not installed")
        return []

    client = TavilyClient(api_key=settings.tavily_api_key)
    try:
        resp = client.search(query=query, max_results=max_results, search_depth="basic")
    except Exception:
        logger.exception("Tavily search failed for %r", query)
        return []

    return [
        SearchHit(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("content", "")[:500],
        )
        for r in resp.get("results", [])
    ]
