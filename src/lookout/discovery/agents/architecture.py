"""Architecture pattern discovery agent.

This agent discovers architectural and design patterns from trusted web sources.
Focus areas:
- Design patterns (Gang of Four patterns, SOLID principles)
- Architectural patterns (layered, microservices, event-driven)
- Code organization patterns
- Dependency management patterns

Trusted sources (high credibility):
- martinfowler.com (0.95)
- refactoring.guru (0.90)
- docs.microsoft.com, AWS/Azure/GCP architecture docs
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lookout import __version__
from lookout.discovery.agents.base import SearchConstraints, SearchFocus
from lookout.discovery.models import DiscoveredPattern
from lookout.discovery.search import SearchQuery, WebSearchClient

if TYPE_CHECKING:
    from lookout.discovery.extractors.base import PatternExtractor

logger = logging.getLogger(__name__)


class ArchitectureAgent:
    """Agent for discovering architecture and design patterns.

    Uses web search to find patterns from authoritative sources like
    Martin Fowler's blog, Refactoring Guru, and cloud provider docs.

    Example:
        async with WebSearchClient(credibility_scorer) as search_client:
            agent = ArchitectureAgent(
                extractor=hybrid_extractor,
                search_client=search_client,
            )

            focus = SearchFocus(
                focus_type="architecture",
                description="Find dependency injection patterns",
                keywords=["dependency injection", "DI", "IoC"],
            )

            constraints = SearchConstraints(
                languages=["python", "typescript"],
                min_confidence="medium",
                max_results=10,
            )

            patterns = await agent.discover_patterns(focus, constraints)
    """

    # High-credibility domains for architecture patterns
    TRUSTED_DOMAINS = [
        "martinfowler.com",
        "refactoring.guru",
        "docs.microsoft.com",
        "aws.amazon.com",
        "cloud.google.com",
        "azure.microsoft.com",
        "spring.io",
        "reactjs.org",
        "vuejs.org",
        "angular.io",
    ]

    # Common architecture pattern keywords
    ARCHITECTURE_KEYWORDS = [
        # GoF Design Patterns (Creational)
        "singleton",
        "factory",
        "abstract factory",
        "builder",
        "prototype",
        # GoF Design Patterns (Structural)
        "adapter",
        "bridge",
        "composite",
        "decorator",
        "facade",
        "flyweight",
        "proxy",
        # GoF Design Patterns (Behavioral)
        "chain of responsibility",
        "command",
        "iterator",
        "mediator",
        "memento",
        "observer",
        "state",
        "strategy",
        "template method",
        "visitor",
        # Architectural Patterns
        "layered architecture",
        "microservices",
        "event-driven",
        "CQRS",
        "event sourcing",
        "hexagonal architecture",
        "clean architecture",
        "MVC",
        "MVP",
        "MVVM",
        "repository pattern",
        "dependency injection",
        "inversion of control",
        "service locator",
        # SOLID Principles
        "single responsibility",
        "open-closed",
        "liskov substitution",
        "interface segregation",
        "dependency inversion",
    ]

    def __init__(
        self,
        extractor: PatternExtractor,
        search_client: WebSearchClient | None = None,
        fetch_full_content: bool = False,
    ):
        """Initialize the architecture agent.

        Args:
            extractor: Pattern extractor for analyzing content
            search_client: Web search client (optional for testing)
            fetch_full_content: Whether to fetch full page text for each result
        """
        self.extractor = extractor
        self.search_client = search_client
        self.fetch_full_content = fetch_full_content

    async def discover_patterns(
        self,
        focus: SearchFocus,
        constraints: SearchConstraints,
    ) -> list[DiscoveredPattern]:
        """Discover architecture patterns based on search focus.

        Strategy:
        1. Build search query from focus and keywords
        2. Search for patterns using WebSearch (if available)
        3. Extract patterns using configured extractor
        4. Filter by constraints (languages, confidence)
        5. Deduplicate and return

        Args:
            focus: What patterns to look for
            constraints: Filters and limits

        Returns:
            List of discovered patterns
        """
        logger.info(f"Discovering architecture patterns: {focus.description}")

        # Build search query
        search_query = self._build_search_query(focus, constraints)

        # If no search client provided, return empty list
        # (This allows testing without network calls)
        if self.search_client is None:
            logger.warning("No search client provided, returning empty results")
            return []

        # Search for relevant content
        try:
            search_results = await self.search_client.search(search_query)
        except Exception as exc:
            logger.error(f"Search failed, returning empty results: {exc}")
            return []

        logger.info(f"Found {len(search_results)} search results, extracting patterns...")

        # Extract patterns from each result
        all_patterns: list[DiscoveredPattern] = []

        for result in search_results:
            from lookout.discovery.extractors.base import ExtractionContext

            if self.fetch_full_content:
                page_text = await self._fetch_page_text(result.url)
                source_text = page_text if page_text else f"{result.title}\n\n{result.snippet}"
            else:
                source_text = f"{result.title}\n\n{result.snippet}"

            context = ExtractionContext(
                source_url=result.url,
                source_text=source_text,
                focus=focus,
                metadata={
                    "credibility": str(result.credibility),
                    "rank": str(result.rank),
                },
            )

            extraction_result = await self.extractor.extract_patterns(context)

            # Filter patterns by constraints
            filtered_patterns = self._filter_patterns(
                extraction_result.patterns,
                constraints,
            )

            all_patterns.extend(filtered_patterns)

        # Deduplicate patterns
        deduplicated = self._deduplicate_patterns(all_patterns)

        logger.info(f"Discovered {len(deduplicated)} unique architecture patterns")

        # Limit to max_results
        return deduplicated[: constraints.max_results]

    async def _fetch_page_text(self, url: str) -> str:
        """Fetch full text content from a URL with retry.

        Uses BeautifulSoup4 to extract readable text, stripping script/style tags.
        Retries up to 2 times on transient network errors.

        Args:
            url: URL to fetch

        Returns:
            Extracted text content, or empty string on failure
        """
        import httpx
        from bs4 import BeautifulSoup

        from lookout.discovery.search.retry import retry_async

        async def _do_fetch() -> str:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": f"lookout/{__version__} pattern-discovery"},
                )
                response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)

        try:
            result: str = await retry_async(
                _do_fetch,
                max_retries=2,
                base_delay=1.0,
                retryable_exceptions=(httpx.RequestError,),
            )
            return result
        except httpx.RequestError as exc:
            logger.warning(f"Failed to fetch {url} after retries: {exc}")
            return ""
        except httpx.HTTPStatusError as exc:
            logger.warning(f"HTTP {exc.response.status_code} fetching {url}")
            return ""

    def _build_search_query(
        self,
        focus: SearchFocus,
        constraints: SearchConstraints,
    ) -> SearchQuery:
        """Build a search query from focus and constraints.

        Args:
            focus: Search focus
            constraints: Search constraints

        Returns:
            SearchQuery for web search
        """
        # Combine focus keywords with architecture keywords
        keywords = focus.keywords or []

        # Build query string
        query_parts = [focus.description]
        if keywords:
            query_parts.extend(keywords[:3])  # Top 3 keywords

        query = " ".join(query_parts)

        # Use trusted domains if no specific domains in constraints
        allowed_domains = self.TRUSTED_DOMAINS

        return SearchQuery(
            query=query,
            focus_type=focus.focus_type,
            max_results=constraints.max_results * 2,  # Over-fetch
            allowed_domains=allowed_domains,
        )

    def _filter_patterns(
        self,
        patterns: list[DiscoveredPattern],
        constraints: SearchConstraints,
    ) -> list[DiscoveredPattern]:
        """Filter patterns by constraints.

        Args:
            patterns: Patterns to filter
            constraints: Constraints to apply

        Returns:
            Filtered patterns
        """
        filtered = []

        for pattern in patterns:
            # Filter by language
            if (
                constraints.languages
                and pattern.languages
                and not any(lang in constraints.languages for lang in pattern.languages)
            ):
                continue

            # Filter by framework
            if (
                constraints.frameworks
                and pattern.framework
                and pattern.framework not in constraints.frameworks
            ):
                continue

            # Filter by confidence
            confidence_levels = {"low": 1, "medium": 2, "high": 3}
            min_level = confidence_levels.get(constraints.min_confidence, 1)
            pattern_level = confidence_levels.get(pattern.confidence, 1)

            if pattern_level < min_level:
                continue

            filtered.append(pattern)

        return filtered

    def _deduplicate_patterns(
        self,
        patterns: list[DiscoveredPattern],
    ) -> list[DiscoveredPattern]:
        """Remove duplicate patterns.

        Deduplication strategy:
        1. Group by pattern_id
        2. Keep the one with highest confidence
        3. If tied, keep the one from highest credibility source

        Args:
            patterns: Patterns to deduplicate

        Returns:
            Deduplicated patterns
        """
        seen: dict[str, DiscoveredPattern] = {}

        for pattern in patterns:
            if pattern.pattern_id not in seen:
                seen[pattern.pattern_id] = pattern
            else:
                # Compare confidence
                existing = seen[pattern.pattern_id]

                confidence_levels = {"low": 1, "medium": 2, "high": 3}
                existing_level = confidence_levels.get(existing.confidence, 1)
                new_level = confidence_levels.get(pattern.confidence, 1)

                if new_level > existing_level:
                    seen[pattern.pattern_id] = pattern
                elif new_level == existing_level:
                    # Compare credibility from evidence
                    existing_cred = float(existing.evidence.get("credibility", "0.5"))
                    new_cred = float(pattern.evidence.get("credibility", "0.5"))

                    if new_cred > existing_cred:
                        seen[pattern.pattern_id] = pattern

        return list(seen.values())
