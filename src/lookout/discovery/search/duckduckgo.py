"""DuckDuckGo HTML search provider (free, no API key required)."""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

from lookout import __version__
from lookout.discovery.search.providers import SearchProviderError

logger = logging.getLogger(__name__)


class DuckDuckGoSearchProvider:
    """Search provider using DuckDuckGo HTML endpoint.

    No API key required. Suitable for low-volume usage and first-time setup.
    For higher reliability and volume, use BraveSearchProvider or GoogleSearchProvider.

    Args:
        timeout: HTTP request timeout in seconds
    """

    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def execute_search(
        self,
        query: str,
        *,
        max_results: int = 10,
        allowed_domains: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Execute a search via DuckDuckGo HTML endpoint.

        Args:
            query: Search query string
            max_results: Maximum results to return
            allowed_domains: Optional domain allowlist (post-filtering)

        Returns:
            List of dicts with keys: url, title, snippet

        Raises:
            SearchProviderError: On HTTP or network errors
        """
        try:
            response = await self._client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": f"lookout/{__version__} pattern-discovery"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SearchProviderError(
                f"DuckDuckGo returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise SearchProviderError(f"DuckDuckGo request failed: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        results: list[dict[str, str]] = []

        for element in soup.select(".web-result"):
            title_el = element.select_one(".result__title")
            url_el = element.select_one(".result__url")
            snippet_el = element.select_one(".result__snippet")
            if not (title_el and url_el):
                continue
            url = url_el.get_text(strip=True)
            if allowed_domains and not any(d in url for d in allowed_domains):
                continue
            results.append(
                {
                    "url": url,
                    "title": title_el.get_text(strip=True),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                }
            )
            if len(results) >= max_results:
                break

        return results

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
