"""End-to-end test: research → validate → promote --generate-check.

Exercises the full discovery-to-enforcement pipeline using real filesystem
storage and the CLI, with only the LLM HTTP call mocked. Generated checks are
verified against the pattern's examples with the real Semgrep binary.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from click.testing import CliRunner

from lookout.cli import cli
from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExample,
    PatternExamples,
    ValidatedPattern,
)
from lookout.discovery.storage import PatternStorage
from lookout.models import ForbiddenCheck
from lookout.promotion import PromotionError, promote_to_spec
from lookout.semgrep import compile_semgrep_config


def _sample_discovered() -> DiscoveredPattern:
    """A realistic discovered pattern with good/bad examples."""
    return DiscoveredPattern(
        pattern_id="ARCH-DI-001",
        title="Avoid direct instantiation in service layer",
        category="architecture",
        subcategory="dependency-injection",
        description=(
            "Services should receive dependencies via constructor injection "
            "rather than instantiating them directly."
        ),
        rationale=(
            "Direct instantiation couples services tightly to concrete "
            "implementations and makes unit testing difficult."
        ),
        severity="error",
        confidence="high",
        languages=["python"],
        examples=PatternExamples(
            good=[
                PatternExample(
                    language="python",
                    code=(
                        "class OrderService:\n"
                        "    def __init__(self, repo: OrderRepo):\n"
                        "        self.repo = repo"
                    ),
                ),
            ],
            bad=[
                PatternExample(
                    language="python",
                    code=(
                        "class OrderService:\n"
                        "    def __init__(self):\n"
                        "        self.repo = OrderRepo()"
                    ),
                ),
            ],
        ),
        discovered_by="architecture-agent",
        references=["https://example.com/dependency-injection"],
    )


def _anthropic_response(text: str) -> Mock:
    return Mock(
        status_code=200,
        json=lambda: {
            "id": "msg_e2e",
            "content": [{"text": text}],
            "usage": {"input_tokens": 800, "output_tokens": 150},
        },
        raise_for_status=lambda: None,
    )


def _mock_anthropic_post(*texts: str) -> AsyncMock:
    """Build an AsyncMock returning canned Anthropic responses, one per call.

    The last response is repeated if the generator asks for more.
    """
    responses = [_anthropic_response(t) for t in texts]
    mock = AsyncMock(
        side_effect=lambda *a, **kw: responses.pop(0) if len(responses) > 1 else responses[0]
    )
    return mock


def _patch_llm(monkeypatch: pytest.MonkeyPatch, post: AsyncMock) -> None:
    """Install ``post`` as the httpx client's post inside RuleGenerator."""
    import lookout.rule_generator as rg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-e2e")
    original_aenter = rg.RuleGenerator.__aenter__

    async def patched_aenter(self: rg.RuleGenerator) -> rg.RuleGenerator:
        inst = await original_aenter(self)
        assert inst._client is not None
        inst._client.post = post  # type: ignore[method-assign]
        return inst

    monkeypatch.setattr(rg.RuleGenerator, "__aenter__", patched_aenter)


def _seed_validated(patterns_dir: Path) -> None:
    storage = PatternStorage(patterns_dir)
    raw = _sample_discovered()
    storage.save_pattern(raw, "raw")
    validated = ValidatedPattern.from_discovered(raw, validated_by="e2e-test")
    storage.save_pattern(validated, "validated")


class TestPromoteWithGenerateCheckE2E:
    """Full pipeline e2e: disk storage → CLI validate → CLI promote --generate-check."""

    def test_full_pipeline_raw_to_enforced_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Raw pattern on disk → validate CLI → promote --generate-check → YAML with real check."""
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"

        # ── Step 1: Save a raw pattern to disk ──────────────────────────
        storage = PatternStorage(patterns_dir)
        raw = _sample_discovered()
        storage.save_pattern(raw, "raw")

        # Verify it landed on disk
        loaded = storage.load_pattern("ARCH-DI-001", stage="raw", model_class=DiscoveredPattern)
        assert loaded is not None
        assert loaded.pattern_id == "ARCH-DI-001"

        # ── Step 2: Validate via CLI ────────────────────────────────────
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "validate",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--tier",
                "1",
                "--notes",
                "E2E test validation",
            ],
        )
        assert result.exit_code == 0, f"validate failed: {result.output}"
        assert "Validated ARCH-DI-001" in result.output

        # Verify validated pattern on disk
        validated = storage.load_pattern(
            "ARCH-DI-001", stage="validated", model_class=ValidatedPattern
        )
        assert validated is not None
        assert validated.effective_tier == 1
        assert validated.validation.review_notes == "E2E test validation"

        # ── Step 3: Promote with --generate-check (LLM mocked) ─────────
        check_json = json.dumps({"type": "forbidden", "pattern": "OrderRepo()"})
        _patch_llm(monkeypatch, _mock_anthropic_post(check_json))

        result = runner.invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
                "--generate-check",
                "--provider",
                "anthropic",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, f"promote failed: {result.output}"
        assert "Promoted ARCH-DI-001" in result.output

        # ── Step 4: Verify the output YAML ──────────────────────────────
        yaml_path = rules_dir / "ARCH-DI-001.yaml"
        assert yaml_path.exists(), f"Expected YAML at {yaml_path}"

        spec_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert spec_data["schema_version"] == 1
        assert spec_data["rule"]["id"] == "ARCH-DI-001"
        assert spec_data["rule"]["check"]["type"] == "forbidden"
        assert spec_data["rule"]["check"]["pattern"] == "OrderRepo()"
        assert spec_data["rule"]["tier"] == 1
        # Promoted rules start as warnings until reviewed, even for an "error" pattern
        assert spec_data["rule"]["severity"] == "warning"
        assert "Verified against examples" in result.output

        # ── Step 5: Verify it compiles to valid Semgrep config ──────────
        from lookout.models import RuleSpecFile

        spec = RuleSpecFile.model_validate(spec_data)
        config = compile_semgrep_config([spec])
        assert len(config["rules"]) == 1

        semgrep_rule = config["rules"][0]
        assert semgrep_rule["id"] == "arch-di-001-impl"
        assert semgrep_rule["languages"] == ["python"]
        assert semgrep_rule["severity"] == "WARNING"

    def test_promote_without_check_is_refused(self, tmp_path: Path) -> None:
        """Without --generate-check and without a proposed_check, nothing is written.

        The old ``["..."]`` placeholder compiled into a rule that matched
        everything; promote now errors out and points at --generate-check.
        """
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
            ],
        )
        assert result.exit_code == 1
        assert "no enforcement check" in result.output
        assert "--generate-check" in result.output
        assert not (rules_dir / "ARCH-DI-001.yaml").exists()

    def test_promote_generate_check_llm_failure_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the LLM never yields a parseable check, promote exits 1 and writes nothing."""
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)
        _patch_llm(monkeypatch, _mock_anthropic_post("I cannot generate a check for this."))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
                "--generate-check",
                "--provider",
                "anthropic",
                "--max-attempts",
                "2",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 1, result.output
        assert "Check generation failed after 2 attempt(s)" in result.output
        assert not (rules_dir / "ARCH-DI-001.yaml").exists()

    def test_promote_retries_with_report_then_saves_verified_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wrong first check is rejected by real Semgrep; the corrected retry is saved."""
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)

        wrong = json.dumps({"type": "forbidden", "pattern": "SomethingElse()"})
        right = json.dumps({"type": "forbidden", "pattern": "OrderRepo()"})
        post = _mock_anthropic_post(wrong, right)
        _patch_llm(monkeypatch, post)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
                "--generate-check",
            ],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        assert post.await_count == 2
        # The second prompt carried the verification report for the first check
        second_prompt = post.await_args_list[1].kwargs["json"]["messages"][0]["content"]
        assert "Previous Attempt Failed Verification" in second_prompt
        assert "SomethingElse()" in second_prompt
        assert "bad example(s) were NOT matched" in second_prompt

        spec_data = yaml.safe_load((rules_dir / "ARCH-DI-001.yaml").read_text(encoding="utf-8"))
        assert spec_data["rule"]["check"]["pattern"] == "OrderRepo()"

    def test_promote_refuses_unverified_check_unless_forced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A check that never passes Semgrep verification is not saved without --force."""
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)
        _patch_llm(
            monkeypatch,
            _mock_anthropic_post(json.dumps({"type": "forbidden", "pattern": "SomethingElse()"})),
        )

        runner = CliRunner()
        args = [
            "promote",
            "ARCH-DI-001",
            "--patterns-dir",
            str(patterns_dir),
            "--output-dir",
            str(rules_dir),
            "--generate-check",
            "--max-attempts",
            "2",
        ]
        result = runner.invoke(cli, args, catch_exceptions=False)
        assert result.exit_code == 1, result.output
        assert "Verification failed after 2 attempt(s)" in result.output
        assert "bad example(s) were NOT matched" in result.output
        assert "--force" in result.output
        assert not (rules_dir / "ARCH-DI-001.yaml").exists()

        result = runner.invoke(cli, [*args, "--force"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "saving unverified check" in result.output
        spec_data = yaml.safe_load((rules_dir / "ARCH-DI-001.yaml").read_text(encoding="utf-8"))
        assert spec_data["rule"]["check"]["pattern"] == "SomethingElse()"

    def test_promote_severity_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)
        _patch_llm(
            monkeypatch,
            _mock_anthropic_post(json.dumps({"type": "forbidden", "pattern": "OrderRepo()"})),
        )

        result = CliRunner().invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
                "--generate-check",
                "--severity",
                "error",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        spec_data = yaml.safe_load((rules_dir / "ARCH-DI-001.yaml").read_text(encoding="utf-8"))
        assert spec_data["rule"]["severity"] == "error"

    def test_promote_generate_check_must_wrap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A must_wrap check that compiles but never fires is caught by verification.

        ``must_wrap`` compiles to a rule about calls inside functions lacking a
        wrapper decorator; it does not flag ``self.repo = OrderRepo()`` in the
        bad example. Schema validation alone used to let this through. With
        --force the check is still saved and compiles.
        """
        patterns_dir = tmp_path / ".lookout-patterns"
        rules_dir = tmp_path / ".lookout-rules"
        _seed_validated(patterns_dir)

        check_json = json.dumps(
            {
                "type": "must_wrap",
                "target": ["OrderRepo()"],
                "wrapper": ["inject"],
            }
        )
        _patch_llm(monkeypatch, _mock_anthropic_post(check_json))

        runner = CliRunner()
        args = [
            "promote",
            "ARCH-DI-001",
            "--patterns-dir",
            str(patterns_dir),
            "--output-dir",
            str(rules_dir),
            "--generate-check",
            "--max-attempts",
            "1",
        ]
        result = runner.invoke(cli, args, catch_exceptions=False)
        assert result.exit_code == 1, result.output
        assert "bad example(s) were NOT matched" in result.output
        assert not (rules_dir / "ARCH-DI-001.yaml").exists()

        result = runner.invoke(cli, [*args, "--force"], catch_exceptions=False)
        assert result.exit_code == 0, f"promote failed: {result.output}"

        yaml_path = rules_dir / "ARCH-DI-001.yaml"
        spec_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert spec_data["rule"]["check"]["type"] == "must_wrap"
        assert spec_data["rule"]["check"]["target"] == ["OrderRepo()"]

        # Verify Semgrep compilation
        from lookout.models import RuleSpecFile

        spec = RuleSpecFile.model_validate(spec_data)
        config = compile_semgrep_config([spec])
        assert len(config["rules"]) == 1


class TestPromoteToSpecWithRuleGeneratorUnit:
    """Unit-level integration: RuleGenerator output → promote_to_spec → compile."""

    def test_forbidden_check_round_trip(self) -> None:
        """ForbiddenCheck from RuleGenerator flows through promotion to compilation."""
        raw = _sample_discovered()
        validated = ValidatedPattern.from_discovered(raw, validated_by="test")

        check = ForbiddenCheck(type="forbidden", pattern="OrderRepo()")
        spec = promote_to_spec(validated, check_override=check)

        assert spec.rule.check == check
        assert spec.rule.id == "ARCH-DI-001"

        config = compile_semgrep_config([spec])
        rules = config["rules"]
        assert len(rules) == 1
        assert rules[0]["severity"] == "WARNING"

    def test_no_override_and_no_proposed_check_raises(self) -> None:
        """Without override and no proposed_check there is no placeholder: promotion fails."""
        raw = _sample_discovered()
        validated = ValidatedPattern.from_discovered(raw, validated_by="test")

        with pytest.raises(PromotionError):
            promote_to_spec(validated)
