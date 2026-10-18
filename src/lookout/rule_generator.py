"""LLM-based Semgrep rule check generation.

Given a ValidatedPattern's description, rationale, and examples, generates a
valid RuleCheck that can be used for enforcement. This bridges the gap between
discovery (rich metadata) and enforcement (runnable Semgrep rules).

Follows the same provider patterns as LLMExtractor — supports Anthropic,
OpenAI, and Gemini.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from lookout.discovery.extractors.llm import (
    API_ENDPOINTS,
    DEFAULT_MODELS,
    LLMProvider,
)
from lookout.discovery.models import ValidatedPattern
from lookout.models import (
    ForbiddenCheck,
    MustWrapCheck,
    PatternDiscoveryCheck,
    RawCheck,
    RequiredWithCheck,
    RuleCheck,
)
from lookout.rule_verifier import VerificationReport, verify_check

#: Default number of generate→verify rounds before giving up.
DEFAULT_MAX_ATTEMPTS = 3

Verifier = Callable[[RuleCheck, ValidatedPattern], VerificationReport]


@dataclass
class GeneratedCheck:
    """Result of LLM-based check generation.

    Attributes:
        check: The generated RuleCheck (Semgrep rule)
        scaffolding: Optional scaffolding text (Requirement, Constraints, Design)
    """

    check: RuleCheck
    scaffolding: str | None = None


@dataclass
class VerifiedGeneration:
    """Outcome of the generate→verify loop.

    Attributes:
        generated: The last check the LLM produced, or None if every attempt
            failed to yield a parseable check.
        report: Verification report for ``generated`` (None when nothing was
            generated).
        attempts: Number of LLM calls made.
    """

    generated: GeneratedCheck | None
    report: VerificationReport | None
    attempts: int

    @property
    def verified(self) -> bool:
        return self.generated is not None and self.report is not None and self.report.passed


logger = logging.getLogger("lookout.rule_generator")

# Map check type strings to their Pydantic model classes
_CHECK_TYPE_MAP: dict[
    str, type[ForbiddenCheck | MustWrapCheck | RequiredWithCheck | RawCheck | PatternDiscoveryCheck]
] = {
    "forbidden": ForbiddenCheck,
    "must_wrap": MustWrapCheck,
    "required_with": RequiredWithCheck,
    "raw": RawCheck,
    "pattern_discovery": PatternDiscoveryCheck,
}


class RuleGenerator:
    """Generates RuleCheck objects from ValidatedPatterns using LLM APIs.

    Usage:
        async with RuleGenerator(api_key="...") as gen:
            check = await gen.generate_check(pattern)
    """

    def __init__(
        self,
        provider: LLMProvider = LLMProvider.ANTHROPIC,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        max_retries: int = 3,
    ) -> None:
        self.provider = provider
        self.model = model or DEFAULT_MODELS[provider]
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries

        env_var_map = {
            LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
            LLMProvider.OPENAI: "OPENAI_API_KEY",
            LLMProvider.GEMINI: "GOOGLE_API_KEY",
        }
        self.api_key = api_key or os.environ.get(env_var_map[provider])

        if not self.api_key:
            raise ValueError(
                f"API key required for {provider}. "
                f"Set {env_var_map[provider]} environment variable or pass api_key parameter."
            )

        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RuleGenerator:
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._client:
            await self._client.aclose()

    async def generate_check(
        self, pattern: ValidatedPattern, feedback: str | None = None
    ) -> GeneratedCheck | None:
        """Generate a RuleCheck from a validated pattern.

        Args:
            pattern: The validated pattern with description, rationale, examples
            feedback: Verification feedback from a previous attempt, appended to
                the prompt so the model can correct its rule

        Returns:
            A GeneratedCheck with the check and optional scaffolding, or None on failure
        """
        if not self._client:
            raise RuntimeError("RuleGenerator must be used as async context manager")

        prompt = self._build_prompt(pattern, feedback=feedback)

        try:
            response_data = await self._call_llm_with_retry(prompt)
        except Exception:
            logger.exception("LLM call failed after retries")
            return None

        text = self._extract_response_text(response_data)
        if not text:
            return None

        check, scaffolding = self._parse_check(text)
        if check is None:
            return None
        return GeneratedCheck(check=check, scaffolding=scaffolding)

    async def generate_verified_check(
        self,
        pattern: ValidatedPattern,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        verifier: Verifier = verify_check,
    ) -> VerifiedGeneration:
        """Generate a check and verify it against the pattern's examples with Semgrep.

        Each failed verification is fed back into the next prompt (the rejected
        check plus the report) until a check passes or ``max_attempts`` LLM
        calls have been made. The last candidate and its report are returned
        either way so callers can show the report or force-save the rule.

        Args:
            pattern: The validated pattern to generate a check for
            max_attempts: Maximum number of LLM calls (>= 1)
            verifier: Runs the check against the examples; injectable for tests
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        feedback: str | None = None
        last_generated: GeneratedCheck | None = None
        last_report: VerificationReport | None = None
        attempts = 0

        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            generated = await self.generate_check(pattern, feedback=feedback)
            if generated is None:
                logger.warning("Attempt %d/%d produced no valid check", attempt, max_attempts)
                feedback = (
                    "Your previous response could not be parsed into a valid check object. "
                    'Return exactly one JSON object with a supported "type" and the '
                    "fields that type requires."
                )
                continue

            last_generated = generated
            report = verifier(generated.check, pattern)
            last_report = report
            if report.passed:
                logger.info("Check verified on attempt %d/%d", attempt, max_attempts)
                return VerifiedGeneration(generated=generated, report=report, attempts=attempt)

            logger.warning(
                "Attempt %d/%d failed verification:\n%s", attempt, max_attempts, report.format()
            )
            feedback = (
                "The check below was compiled and run with Semgrep against the examples "
                "above and failed:\n\n"
                f"{json.dumps(generated.check.model_dump(mode='json'), indent=2)}\n\n"
                f"{report.format()}\n\n"
                "Fix the rule so that EVERY bad example produces a match and NO good "
                "example does. Prefer precise patterns over broad wildcards."
            )

        return VerifiedGeneration(generated=last_generated, report=last_report, attempts=attempts)

    def _build_prompt(self, pattern: ValidatedPattern, feedback: str | None = None) -> str:
        """Build a prompt that asks the LLM to generate a single RuleCheck JSON object.

        ``feedback`` (a verification report from a previous attempt) is
        appended so the model can correct its rule.
        """
        discovered = pattern.discovered

        # Format examples
        good_examples = ""
        for ex in discovered.examples.good:
            good_examples += f"\n```{ex.language}\n{ex.code}\n```\n"

        bad_examples = ""
        for ex in discovered.examples.bad:
            bad_examples += f"\n```{ex.language}\n{ex.code}\n```\n"

        languages = ", ".join(discovered.languages) if discovered.languages else "language-agnostic"

        feedback_section = ""
        if feedback:
            feedback_section = f"""

## Previous Attempt Failed Verification

{feedback}
"""

        return f"""You are a Semgrep rule expert. Given a coding pattern description, generate a single JSON check object that can enforce the pattern.

## Pattern Information

**Title**: {discovered.title}
**Description**: {discovered.description}
**Rationale**: {discovered.rationale}
**Languages**: {languages}

### Good Examples (code that follows the pattern):
{good_examples if good_examples else "None provided."}

### Bad Examples (code that violates the pattern):
{bad_examples if bad_examples else "None provided."}

## Available Check Types

Choose the most appropriate check type from these 5 options:

### 1. forbidden
Matches a Semgrep pattern that should never appear.
```json
{{"type": "forbidden", "pattern": "Repo()"}}
```

### 2. must_wrap
Requires that calls to target functions/methods are wrapped by specified wrappers.
```json
{{"type": "must_wrap", "target": ["requests.get", "requests.post"], "wrapper": ["circuit_breaker", "with_retry"]}}
```

### 3. required_with
If one pattern is present, another must also be present.
```json
{{"type": "required_with", "if_present": "open(...)", "must_have": "close(...)"}}
```

### 4. raw
Raw Semgrep YAML config for complex rules that don't fit other types.
```json
{{"type": "raw", "config": "rules:\\n  - id: test\\n    pattern: foo()", "engine": "semgrep"}}
```

### 5. pattern_discovery
Semgrep patterns for discovering code instances (informational, not enforcement).
```json
{{"type": "pattern_discovery", "patterns": ["class $C:\\n  def __init__(self, $DEP):\\n    self.$FIELD = $DEP"]}}
```

## Instructions

1. First, reason through the rule design by filling in the scaffolding:
   - **Requirement**: What specific coding pattern must be enforced or detected?
   - **Constraints**: What languages, scopes, or edge cases bound the rule?
   - **Design**: Why did you pick this check type and pattern? Brief rationale.
2. Analyze the bad examples to determine what code pattern should be detected
3. Choose the single best check type for this pattern
4. Use Semgrep pattern syntax ($VAR for metavariables, ... for wildcards)
5. Return ONLY a single valid JSON object with a top-level "scaffolding" key
   containing requirement/constraints/design, plus all check-type fields at
   the top level. No markdown wrapping, no explanation.

Example output format:
```
{{"scaffolding": {{"requirement": "...", "constraints": "...", "design": "..."}}, "type": "forbidden", "pattern": "..."}}
```
{feedback_section}
Return the JSON object now:"""

    def _parse_check(self, text: str) -> tuple[RuleCheck | None, str | None]:
        """Parse LLM response text into a RuleCheck and optional scaffolding.

        Handles markdown wrapping and validates against Pydantic models.
        Extracts the ``scaffolding`` key (requirement/constraints/design)
        before validating the check model.

        Returns:
            Tuple of (RuleCheck or None, scaffolding text or None)
        """
        text = text.strip()
        if not text:
            return None, None

        # Strip markdown code block wrapping
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```")
            text = text.strip()
            if text.endswith("```"):
                text = text[: -len("```")]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON: %s", text[:200])
            return None, None

        # Extract scaffolding before model validation (models use extra="forbid")
        scaffolding_data = data.pop("scaffolding", None)
        scaffolding_text: str | None = None
        if isinstance(scaffolding_data, dict):
            parts = []
            for key in ("requirement", "constraints", "design"):
                val = scaffolding_data.get(key)
                if val:
                    parts.append(f"{key.title()}: {val}")
            if parts:
                scaffolding_text = "\n".join(parts)

        check_type = data.get("type")
        model_class = _CHECK_TYPE_MAP.get(check_type)
        if model_class is None:
            logger.warning("Unknown check type: %s", check_type)
            return None, scaffolding_text

        try:
            return model_class.model_validate(data), scaffolding_text
        except Exception:
            logger.warning("Validation failed for check type %s", check_type, exc_info=True)
            return None, scaffolding_text

    async def _call_llm_with_retry(self, prompt: str) -> dict[str, Any]:
        """Call LLM API with exponential backoff retry."""
        for attempt in range(self.max_retries):
            try:
                if self.provider == LLMProvider.ANTHROPIC:
                    return await self._call_anthropic(prompt)
                elif self.provider == LLMProvider.OPENAI:
                    return await self._call_openai(prompt)
                elif self.provider == LLMProvider.GEMINI:
                    return await self._call_gemini(prompt)
                else:
                    raise ValueError(f"Unsupported provider: {self.provider}")

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait_time = 2**attempt
                    logger.warning(
                        "Rate limited by %s, retrying in %ss (attempt %d/%d)",
                        self.provider,
                        wait_time,
                        attempt + 1,
                        self.max_retries,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise
                logger.warning(
                    "LLM API error: %s, retrying (attempt %d/%d)",
                    e,
                    attempt + 1,
                    self.max_retries,
                )
                await asyncio.sleep(2**attempt)

        raise RuntimeError(f"Failed to call {self.provider} API after {self.max_retries} attempts")

    async def _call_anthropic(self, prompt: str) -> dict[str, Any]:
        assert self._client is not None
        response = await self._client.post(
            API_ENDPOINTS[LLMProvider.ANTHROPIC],
            headers={
                "x-api-key": self.api_key or "",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def _call_openai(self, prompt: str) -> dict[str, Any]:
        assert self._client is not None
        response = await self._client.post(
            API_ENDPOINTS[LLMProvider.OPENAI],
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def _call_gemini(self, prompt: str) -> dict[str, Any]:
        assert self._client is not None
        url = API_ENDPOINTS[LLMProvider.GEMINI].format(model=self.model)
        response = await self._client.post(
            f"{url}?key={self.api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.temperature,
                    "maxOutputTokens": self.max_tokens,
                },
            },
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def _extract_response_text(self, response_data: dict[str, Any]) -> str | None:
        """Extract text content from provider-specific response format."""
        try:
            if self.provider == LLMProvider.ANTHROPIC:
                return response_data["content"][0]["text"]  # type: ignore[no-any-return]
            elif self.provider == LLMProvider.OPENAI:
                return response_data["choices"][0]["message"]["content"]  # type: ignore[no-any-return]
            elif self.provider == LLMProvider.GEMINI:
                return response_data["candidates"][0]["content"]["parts"][0]["text"]  # type: ignore[no-any-return]
        except (KeyError, IndexError):
            logger.warning("Failed to extract text from %s response", self.provider)
        return None
