"""Tests for LLM-based Semgrep rule generation at promotion time."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from lookout.discovery.extractors.llm import LLMProvider
from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExample,
    PatternExamples,
    ValidatedPattern,
)
from lookout.models import (
    ForbiddenCheck,
    MustWrapCheck,
    PatternDiscoveryCheck,
    RawCheck,
    RequiredWithCheck,
)
from lookout.rule_generator import RuleGenerator
from lookout.rule_verifier import ExampleOutcome, VerificationReport


def _make_validated_pattern(
    pattern_id: str = "ARCH-DI-001",
    description: str = "Services should receive dependencies via constructor injection rather than instantiating them directly.",
    rationale: str = "Direct instantiation couples services tightly and makes unit testing difficult.",
    languages: list[str] | None = None,
    good_examples: list[PatternExample] | None = None,
    bad_examples: list[PatternExample] | None = None,
) -> ValidatedPattern:
    """Create a ValidatedPattern for testing."""
    discovered = DiscoveredPattern(
        pattern_id=pattern_id,
        title="Avoid direct instantiation in service layer",
        description=description,
        rationale=rationale,
        category="architecture",
        severity="error",
        confidence="high",
        languages=languages or ["python"],
        examples=PatternExamples(
            good=good_examples
            or [
                PatternExample(
                    language="python",
                    code="class Service:\n    def __init__(self, repo: Repo):\n        self.repo = repo",
                )
            ],
            bad=bad_examples
            or [
                PatternExample(
                    language="python",
                    code="class Service:\n    def __init__(self):\n        self.repo = Repo()",
                )
            ],
        ),
        discovered_by="test-agent",
        references=["https://example.com/di"],
    )
    return ValidatedPattern.from_discovered(discovered, validated_by="reviewer")


def _anthropic_response(check_json: str) -> dict:
    """Build a mock Anthropic API response."""
    return {
        "id": "msg_test",
        "content": [{"text": check_json}],
        "usage": {"input_tokens": 500, "output_tokens": 200},
    }


def _openai_response(check_json: str) -> dict:
    """Build a mock OpenAI API response."""
    return {
        "choices": [{"message": {"content": check_json}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 150},
    }


def _gemini_response(check_json: str) -> dict:
    """Build a mock Gemini API response."""
    return {
        "candidates": [{"content": {"parts": [{"text": check_json}]}}],
        "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 100},
    }


# ============================================================================
# TestRuleGeneratorInit
# ============================================================================


class TestRuleGeneratorInit:
    def test_explicit_api_key(self) -> None:
        gen = RuleGenerator(api_key="test-key-123")
        assert gen.api_key == "test-key-123"

    def test_env_var_fallback_anthropic(self) -> None:
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "env-key"}):
            gen = RuleGenerator(provider=LLMProvider.ANTHROPIC)
            assert gen.api_key == "env-key"

    def test_env_var_fallback_openai(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "oai-key"}):
            gen = RuleGenerator(provider=LLMProvider.OPENAI)
            assert gen.api_key == "oai-key"

    def test_env_var_fallback_gemini(self) -> None:
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "gem-key"}):
            gen = RuleGenerator(provider=LLMProvider.GEMINI)
            assert gen.api_key == "gem-key"

    def test_missing_api_key_raises(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="API key required"),
        ):
            RuleGenerator(provider=LLMProvider.ANTHROPIC)

    def test_default_model_anthropic(self) -> None:
        gen = RuleGenerator(api_key="k", provider=LLMProvider.ANTHROPIC)
        assert "claude" in gen.model

    def test_custom_model(self) -> None:
        gen = RuleGenerator(api_key="k", model="custom-model")
        assert gen.model == "custom-model"

    def test_default_temperature(self) -> None:
        gen = RuleGenerator(api_key="k")
        assert gen.temperature == 0.0

    def test_default_max_retries(self) -> None:
        gen = RuleGenerator(api_key="k")
        assert gen.max_retries == 3


# ============================================================================
# TestBuildPrompt
# ============================================================================


class TestBuildPrompt:
    def test_prompt_includes_description(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "constructor injection" in prompt

    def test_prompt_includes_rationale(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "unit testing" in prompt

    def test_prompt_includes_languages(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern(languages=["python", "java"])
        prompt = gen._build_prompt(pattern)
        assert "python" in prompt
        assert "java" in prompt

    def test_prompt_includes_good_examples(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "self.repo = repo" in prompt

    def test_prompt_includes_bad_examples(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "self.repo = Repo()" in prompt

    def test_prompt_includes_check_type_catalog(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        # Should mention all 5 check types
        assert "forbidden" in prompt
        assert "must_wrap" in prompt
        assert "required_with" in prompt
        assert "raw" in prompt
        assert "pattern_discovery" in prompt

    def test_prompt_requests_json_output(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "JSON" in prompt

    def test_prompt_requests_scaffolding(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern()
        prompt = gen._build_prompt(pattern)
        assert "requirement" in prompt.lower()
        assert "constraints" in prompt.lower()
        assert "design" in prompt.lower()
        assert "scaffolding" in prompt.lower()

    def test_prompt_handles_no_examples(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern(good_examples=[], bad_examples=[])
        prompt = gen._build_prompt(pattern)
        # Should not crash, still contains description
        assert "constructor injection" in prompt

    def test_prompt_handles_no_languages(self) -> None:
        gen = RuleGenerator(api_key="k")
        pattern = _make_validated_pattern(languages=[])
        prompt = gen._build_prompt(pattern)
        assert "constructor injection" in prompt


# ============================================================================
# TestParseCheck
# ============================================================================


class TestParseCheck:
    def test_parse_forbidden_check(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        check, _ = gen._parse_check(data)
        assert isinstance(check, ForbiddenCheck)
        assert check.pattern == "Repo()"

    def test_parse_must_wrap_check(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps(
            {
                "type": "must_wrap",
                "target": ["requests.get"],
                "wrapper": ["circuit_breaker"],
            }
        )
        check, _ = gen._parse_check(data)
        assert isinstance(check, MustWrapCheck)
        assert check.target == ["requests.get"]

    def test_parse_required_with_check(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps(
            {
                "type": "required_with",
                "if_present": "open(",
                "must_have": "close(",
            }
        )
        check, _ = gen._parse_check(data)
        assert isinstance(check, RequiredWithCheck)
        assert check.if_present == "open("

    def test_parse_raw_check(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps(
            {
                "type": "raw",
                "config": "rules:\n  - id: test\n    pattern: foo()",
                "engine": "semgrep",
            }
        )
        check, _ = gen._parse_check(data)
        assert isinstance(check, RawCheck)

    def test_parse_pattern_discovery_check(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps(
            {
                "type": "pattern_discovery",
                "patterns": ["class $C:\n  def __init__(self): ..."],
            }
        )
        check, _ = gen._parse_check(data)
        assert isinstance(check, PatternDiscoveryCheck)
        assert len(check.patterns) == 1

    def test_parse_markdown_wrapped_json(self) -> None:
        gen = RuleGenerator(api_key="k")
        inner = json.dumps({"type": "forbidden", "pattern": "eval(...)"})
        data = f"```json\n{inner}\n```"
        check, _ = gen._parse_check(data)
        assert isinstance(check, ForbiddenCheck)
        assert check.pattern == "eval(...)"

    def test_parse_markdown_no_lang_tag(self) -> None:
        gen = RuleGenerator(api_key="k")
        inner = json.dumps({"type": "forbidden", "pattern": "exec(...)"})
        data = f"```\n{inner}\n```"
        check, _ = gen._parse_check(data)
        assert isinstance(check, ForbiddenCheck)

    def test_invalid_json_returns_none(self) -> None:
        gen = RuleGenerator(api_key="k")
        check, scaffolding = gen._parse_check("this is not json")
        assert check is None
        assert scaffolding is None

    def test_unknown_type_returns_none(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps({"type": "nonexistent_type", "foo": "bar"})
        check, _ = gen._parse_check(data)
        assert check is None

    def test_missing_required_fields_returns_none(self) -> None:
        gen = RuleGenerator(api_key="k")
        # forbidden requires "pattern" field
        data = json.dumps({"type": "forbidden"})
        check, _ = gen._parse_check(data)
        assert check is None

    def test_extra_fields_returns_none(self) -> None:
        gen = RuleGenerator(api_key="k")
        # extra="forbid" on all check models should reject extra fields
        data = json.dumps({"type": "forbidden", "pattern": "foo()", "extra_field": "bad"})
        check, _ = gen._parse_check(data)
        assert check is None

    def test_empty_string_returns_none(self) -> None:
        gen = RuleGenerator(api_key="k")
        check, scaffolding = gen._parse_check("")
        assert check is None
        assert scaffolding is None

    def test_parse_extracts_scaffolding(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps(
            {
                "scaffolding": {
                    "requirement": "Prevent direct instantiation",
                    "constraints": "Python only, class-level",
                    "design": "Match Repo() calls outside DI containers",
                },
                "type": "forbidden",
                "pattern": "Repo()",
            }
        )
        check, scaffolding = gen._parse_check(data)
        assert isinstance(check, ForbiddenCheck)
        assert scaffolding is not None
        assert "Prevent direct instantiation" in scaffolding
        assert "Python only" in scaffolding
        assert "Match Repo()" in scaffolding

    def test_parse_without_scaffolding_returns_none_scaffolding(self) -> None:
        gen = RuleGenerator(api_key="k")
        data = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        check, scaffolding = gen._parse_check(data)
        assert isinstance(check, ForbiddenCheck)
        assert scaffolding is None


# ============================================================================
# TestGenerateCheck
# ============================================================================


class TestGenerateCheck:
    @pytest.mark.asyncio
    async def test_anthropic_generate_check(self) -> None:
        check_json = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        mock_resp = _anthropic_response(check_json)

        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(_make_validated_pattern())

        assert result is not None
        assert isinstance(result.check, ForbiddenCheck)
        assert result.check.pattern == "Repo()"

    @pytest.mark.asyncio
    async def test_openai_generate_check(self) -> None:
        check_json = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        mock_resp = _openai_response(check_json)

        async with RuleGenerator(api_key="test-key", provider=LLMProvider.OPENAI) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(_make_validated_pattern())

        assert result is not None
        assert isinstance(result.check, ForbiddenCheck)

    @pytest.mark.asyncio
    async def test_gemini_generate_check(self) -> None:
        check_json = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        mock_resp = _gemini_response(check_json)

        async with RuleGenerator(api_key="test-key", provider=LLMProvider.GEMINI) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(_make_validated_pattern())

        assert result is not None
        assert isinstance(result.check, ForbiddenCheck)

    @pytest.mark.asyncio
    async def test_generate_check_with_scaffolding(self) -> None:
        check_json = json.dumps(
            {
                "scaffolding": {
                    "requirement": "Ban direct instantiation of Repo in services",
                    "constraints": "Python only; class constructors",
                    "design": "Use forbidden check on Repo() pattern",
                },
                "type": "forbidden",
                "pattern": "Repo()",
            }
        )
        mock_resp = _anthropic_response(check_json)

        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(_make_validated_pattern())

        assert result is not None
        assert isinstance(result.check, ForbiddenCheck)
        assert result.scaffolding is not None
        assert "Ban direct instantiation" in result.scaffolding

    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self) -> None:
        check_json = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        mock_responses = [
            Mock(
                status_code=429,
                raise_for_status=Mock(
                    side_effect=httpx.HTTPStatusError(
                        "Rate limited",
                        request=Mock(),
                        response=Mock(status_code=429),
                    )
                ),
            ),
            Mock(
                status_code=200,
                json=lambda: _anthropic_response(check_json),
                raise_for_status=lambda: None,
            ),
        ]

        async with RuleGenerator(
            api_key="test-key", provider=LLMProvider.ANTHROPIC, max_retries=2
        ) as gen:
            mock_post = AsyncMock(side_effect=mock_responses)
            gen._client.post = mock_post  # type: ignore[union-attr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await gen.generate_check(_make_validated_pattern())

        assert result is not None
        assert isinstance(result.check, ForbiddenCheck)
        assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_none(self) -> None:
        error_resp = Mock(
            status_code=500,
            raise_for_status=Mock(
                side_effect=httpx.HTTPStatusError(
                    "Server error",
                    request=Mock(),
                    response=Mock(status_code=500),
                )
            ),
        )

        async with RuleGenerator(
            api_key="test-key", provider=LLMProvider.ANTHROPIC, max_retries=2
        ) as gen:
            gen._client.post = AsyncMock(side_effect=[error_resp, error_resp])  # type: ignore[union-attr]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await gen.generate_check(_make_validated_pattern())

        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_llm_response_returns_none(self) -> None:
        mock_resp = _anthropic_response("not valid json at all")

        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(_make_validated_pattern())

        assert result is None

    @pytest.mark.asyncio
    async def test_context_manager_initializes_client(self) -> None:
        async with RuleGenerator(api_key="test-key") as gen:
            assert gen._client is not None

    @pytest.mark.asyncio
    async def test_context_manager_cleans_up_client(self) -> None:
        gen = RuleGenerator(api_key="test-key")
        async with gen:
            pass
        # After exiting, client should be closed (set to None or aclose called)
        # We just verify it doesn't raise


# ============================================================================
# TestGenerateCheckIntegration
# ============================================================================


class TestGenerateCheckIntegration:
    """End-to-end: generate_check → promote_to_spec → compile_semgrep_config."""

    @pytest.mark.asyncio
    async def test_generated_forbidden_check_compiles(self) -> None:
        from lookout.promotion import promote_to_spec
        from lookout.semgrep import compile_semgrep_config

        check_json = json.dumps({"type": "forbidden", "pattern": "Repo()"})
        mock_resp = _anthropic_response(check_json)

        pattern = _make_validated_pattern()
        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(pattern)

        assert result is not None
        spec = promote_to_spec(pattern, check_override=result.check)
        assert spec.rule.check == result.check

        # Should compile to valid Semgrep config
        config = compile_semgrep_config([spec])
        assert len(config["rules"]) == 1
        assert config["rules"][0]["id"] == "arch-di-001-impl"

    @pytest.mark.asyncio
    async def test_generated_must_wrap_check_compiles(self) -> None:
        from lookout.promotion import promote_to_spec
        from lookout.semgrep import compile_semgrep_config

        check_json = json.dumps(
            {
                "type": "must_wrap",
                "target": ["Repo()"],
                "wrapper": ["inject"],
            }
        )
        mock_resp = _anthropic_response(check_json)

        pattern = _make_validated_pattern()
        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(pattern)

        assert result is not None
        spec = promote_to_spec(pattern, check_override=result.check)
        config = compile_semgrep_config([spec])
        assert len(config["rules"]) == 1

    @pytest.mark.asyncio
    async def test_none_check_cannot_be_promoted(self) -> None:
        from lookout.promotion import PromotionError, promote_to_spec

        # Simulate LLM returning garbage → generate_check returns None
        mock_resp = _anthropic_response("garbage")

        pattern = _make_validated_pattern()
        async with RuleGenerator(api_key="test-key", provider=LLMProvider.ANTHROPIC) as gen:
            gen._client.post = AsyncMock(  # type: ignore[union-attr]
                return_value=Mock(
                    status_code=200,
                    json=lambda: mock_resp,
                    raise_for_status=lambda: None,
                )
            )
            result = await gen.generate_check(pattern)

        assert result is None
        # No placeholder fallback: a pattern without a check cannot become a rule
        with pytest.raises(PromotionError):
            promote_to_spec(pattern, check_override=None)


class TestGenerateVerifiedCheck:
    """The generate→verify retry loop, with both the LLM and Semgrep mocked."""

    @staticmethod
    def _failing_report(detail: str = "bad example #1") -> VerificationReport:
        report = VerificationReport()
        report.unmatched_bad.append(
            ExampleOutcome(kind="bad", index=0, language="python", code="x = Repo()")
        )
        report.notes.append(detail)
        return report

    @staticmethod
    def _llm(*texts: str) -> AsyncMock:
        """Return canned Anthropic responses, one per call (last one repeats)."""
        queue = [_anthropic_response(t) for t in texts]
        return AsyncMock(side_effect=lambda prompt: queue.pop(0) if len(queue) > 1 else queue[0])

    @pytest.mark.asyncio
    async def test_passes_first_time(self) -> None:
        verifier = Mock(return_value=VerificationReport())
        async with RuleGenerator(api_key="k") as gen:
            gen._call_llm_with_retry = self._llm(  # type: ignore[method-assign]
                json.dumps({"type": "forbidden", "pattern": "Repo()"})
            )
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=3, verifier=verifier
            )
        assert outcome.verified
        assert outcome.attempts == 1
        assert verifier.call_count == 1
        assert isinstance(outcome.generated.check, ForbiddenCheck)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_retries_with_report_in_prompt(self) -> None:
        reports = [self._failing_report("first-failure-marker"), VerificationReport()]
        verifier = Mock(side_effect=lambda check, pattern: reports.pop(0))
        async with RuleGenerator(api_key="k") as gen:
            llm = self._llm(
                json.dumps({"type": "forbidden", "pattern": "Wrong()"}),
                json.dumps({"type": "forbidden", "pattern": "Repo()"}),
            )
            gen._call_llm_with_retry = llm  # type: ignore[method-assign]
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=3, verifier=verifier
            )

        assert outcome.verified
        assert outcome.attempts == 2
        assert outcome.generated.check.pattern == "Repo()"  # type: ignore[union-attr]
        first_prompt = llm.await_args_list[0].args[0]
        second_prompt = llm.await_args_list[1].args[0]
        assert "Previous Attempt Failed Verification" not in first_prompt
        assert "Previous Attempt Failed Verification" in second_prompt
        assert "Wrong()" in second_prompt  # the rejected check
        assert "first-failure-marker" in second_prompt  # the report
        assert "x = Repo()" in second_prompt  # the unmatched bad example

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts_and_returns_last_candidate(self) -> None:
        verifier = Mock(side_effect=lambda check, pattern: self._failing_report())
        async with RuleGenerator(api_key="k") as gen:
            llm = self._llm(json.dumps({"type": "forbidden", "pattern": "Wrong()"}))
            gen._call_llm_with_retry = llm  # type: ignore[method-assign]
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=3, verifier=verifier
            )

        assert not outcome.verified
        assert outcome.attempts == 3
        assert llm.await_count == 3
        assert verifier.call_count == 3
        assert outcome.generated is not None
        assert outcome.generated.check.pattern == "Wrong()"  # type: ignore[union-attr]
        assert outcome.report is not None and not outcome.report.passed

    @pytest.mark.asyncio
    async def test_max_attempts_one_means_no_retry(self) -> None:
        verifier = Mock(side_effect=lambda check, pattern: self._failing_report())
        async with RuleGenerator(api_key="k") as gen:
            llm = self._llm(json.dumps({"type": "forbidden", "pattern": "Wrong()"}))
            gen._call_llm_with_retry = llm  # type: ignore[method-assign]
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=1, verifier=verifier
            )
        assert not outcome.verified
        assert llm.await_count == 1

    @pytest.mark.asyncio
    async def test_unparseable_response_counts_as_attempt_and_feeds_back(self) -> None:
        verifier = Mock(return_value=VerificationReport())
        async with RuleGenerator(api_key="k") as gen:
            llm = self._llm(
                "not json at all",
                json.dumps({"type": "forbidden", "pattern": "Repo()"}),
            )
            gen._call_llm_with_retry = llm  # type: ignore[method-assign]
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=3, verifier=verifier
            )
        assert outcome.verified
        assert outcome.attempts == 2
        assert verifier.call_count == 1
        assert "could not be parsed" in llm.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_never_parseable_returns_no_candidate(self) -> None:
        verifier = Mock(return_value=VerificationReport())
        async with RuleGenerator(api_key="k") as gen:
            gen._call_llm_with_retry = self._llm("garbage")  # type: ignore[method-assign]
            outcome = await gen.generate_verified_check(
                _make_validated_pattern(), max_attempts=2, verifier=verifier
            )
        assert outcome.generated is None
        assert outcome.report is None
        assert outcome.attempts == 2
        assert not outcome.verified
        verifier.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_attempts_rejected(self) -> None:
        async with RuleGenerator(api_key="k") as gen:
            with pytest.raises(ValueError):
                await gen.generate_verified_check(_make_validated_pattern(), max_attempts=0)

    def test_prompt_without_feedback_has_no_feedback_section(self) -> None:
        gen = RuleGenerator(api_key="k")
        assert "Previous Attempt Failed Verification" not in gen._build_prompt(
            _make_validated_pattern()
        )
