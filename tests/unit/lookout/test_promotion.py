"""Tests for promotion logic (discovered pattern → rule spec)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExample,
    PatternExamples,
    ValidatedPattern,
)
from lookout.models import ForbiddenCheck
from lookout.promotion import DEFAULT_PROMOTED_SEVERITY, PromotionError, promote_to_spec, save_spec

DEFAULT_CHECK = ForbiddenCheck(type="forbidden", pattern="Repo()")


def _discovered(
    pattern_id: str = "ARCH-DI-001",
    proposed_check: ForbiddenCheck | None = DEFAULT_CHECK,
    references: list[str] | None = None,
    severity: str = "error",
    confidence: str = "high",
    good_examples: list[PatternExample] | None = None,
    bad_examples: list[PatternExample] | None = None,
) -> DiscoveredPattern:
    return DiscoveredPattern(
        pattern_id=pattern_id,
        title="Avoid direct instantiation in service layer",
        description="Services should receive dependencies via constructor injection rather than instantiating them directly.",
        rationale="Direct instantiation couples services tightly and makes unit testing difficult.",
        category="architecture",
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        proposed_check=proposed_check,
        references=references or [],
        examples=PatternExamples(
            good=good_examples or [],
            bad=bad_examples or [],
        ),
        discovered_by="test-agent",
    )


def _validated(
    pattern_id: str = "ARCH-DI-001",
    proposed_check: ForbiddenCheck | None = DEFAULT_CHECK,
    references: list[str] | None = None,
    tier_override: int | None = None,
) -> ValidatedPattern:
    return ValidatedPattern.from_discovered(
        _discovered(
            pattern_id=pattern_id,
            proposed_check=proposed_check,
            references=references,
        ),
        validated_by="reviewer",
        tier_override=tier_override,
    )


class TestPromoteToSpec:
    def test_returns_rule_spec_file(self) -> None:
        from lookout.models import RuleSpecFile

        result = promote_to_spec(_validated())
        assert isinstance(result, RuleSpecFile)

    def test_rule_id_matches_pattern_id(self) -> None:
        result = promote_to_spec(_validated(pattern_id="ARCH-DI-001"))
        assert result.rule.id == "ARCH-DI-001"

    def test_schema_version_is_one(self) -> None:
        result = promote_to_spec(_validated())
        assert result.schema_version == 1

    def test_uses_proposed_check_when_provided(self) -> None:
        check = ForbiddenCheck(type="forbidden", pattern="SomeClass()")
        result = promote_to_spec(_validated(proposed_check=check))
        assert result.rule.check == check

    def test_check_override_wins_over_proposed_check(self) -> None:
        override = ForbiddenCheck(type="forbidden", pattern="Other()")
        result = promote_to_spec(_validated(proposed_check=DEFAULT_CHECK), check_override=override)
        assert result.rule.check == override

    def test_no_check_raises_promotion_error(self) -> None:
        """No placeholder: an enforcement rule needs a real check."""
        with pytest.raises(PromotionError, match="--generate-check"):
            promote_to_spec(_validated(proposed_check=None))

    def test_promotion_error_names_pattern(self) -> None:
        with pytest.raises(PromotionError, match="ARCH-DI-001"):
            promote_to_spec(_validated(pattern_id="ARCH-DI-001", proposed_check=None))

    def test_severity_defaults_to_warning_regardless_of_discovered(self) -> None:
        discovered = _discovered(severity="error")
        validated = ValidatedPattern.from_discovered(discovered, validated_by="reviewer")
        result = promote_to_spec(validated)
        assert DEFAULT_PROMOTED_SEVERITY == "warning"
        assert result.rule.severity == "warning"

    def test_severity_override(self) -> None:
        result = promote_to_spec(_validated(), severity="error")
        assert result.rule.severity == "error"

    def test_first_reference_used_as_documentation_url(self) -> None:
        refs = ["https://docs.example.com/di", "https://example.com/other"]
        result = promote_to_spec(_validated(references=refs))
        assert result.rule.reporting.documentation_url == "https://docs.example.com/di"

    def test_no_references_yields_none_documentation_url(self) -> None:
        result = promote_to_spec(_validated(references=[]))
        assert result.rule.reporting.documentation_url is None

    def test_reporting_message_includes_title_and_id(self) -> None:
        result = promote_to_spec(_validated(pattern_id="ARCH-DI-001"))
        assert "ARCH-DI-001" in result.rule.reporting.default_message

    def test_tier_is_passed_from_validated_pattern(self) -> None:
        result = promote_to_spec(_validated(tier_override=2))
        assert result.rule.tier == 2

    def test_examples_are_mapped(self) -> None:
        good = PatternExample(
            language="python", code="class A:\n    def __init__(self, dep): self.dep = dep"
        )
        bad = PatternExample(
            language="python", code="class A:\n    def __init__(self): self.dep = Dep()"
        )
        pattern = _discovered(good_examples=[good], bad_examples=[bad])
        validated = ValidatedPattern.from_discovered(pattern, validated_by="reviewer")
        result = promote_to_spec(validated)

        assert len(result.rule.examples.good) == 1
        assert len(result.rule.examples.bad) == 1
        assert result.rule.examples.good[0].code == good.code
        assert result.rule.examples.bad[0].code == bad.code

    def test_references_are_preserved(self) -> None:
        refs = ["https://a.com", "https://b.com"]
        result = promote_to_spec(_validated(references=refs))
        assert result.rule.references == refs


class TestSaveSpec:
    def test_creates_output_directory_if_missing(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated())
        output_dir = tmp_path / "deep" / "nested" / "rules"

        save_spec(spec, output_dir)

        assert output_dir.exists()

    def test_returns_path_to_yaml_file(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated(pattern_id="ARCH-DI-001"))
        result_path = save_spec(spec, tmp_path)

        assert result_path == tmp_path / "ARCH-DI-001.yaml"

    def test_written_file_exists(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated(pattern_id="ARCH-DI-001"))
        result_path = save_spec(spec, tmp_path)

        assert result_path.exists()

    def test_written_file_is_valid_yaml(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated())
        result_path = save_spec(spec, tmp_path)

        content = result_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict)

    def test_written_yaml_contains_rule_id(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated(pattern_id="ARCH-DI-001"))
        result_path = save_spec(spec, tmp_path)

        parsed = yaml.safe_load(result_path.read_text(encoding="utf-8"))
        assert parsed["rule"]["id"] == "ARCH-DI-001"

    def test_written_yaml_contains_schema_version(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated())
        result_path = save_spec(spec, tmp_path)

        parsed = yaml.safe_load(result_path.read_text(encoding="utf-8"))
        assert parsed["schema_version"] == 1

    def test_filename_uses_rule_id(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated(pattern_id="SEC-XSS-001"))
        result_path = save_spec(spec, tmp_path)

        assert result_path.name == "SEC-XSS-001.yaml"

    def test_scaffolding_written_as_comments(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated())
        scaffolding = (
            "Requirement: Ban Repo() direct instantiation\n"
            "Constraints: Python only\n"
            "Design: forbidden check"
        )
        result_path = save_spec(spec, tmp_path, scaffolding=scaffolding)

        content = result_path.read_text(encoding="utf-8")
        assert content.startswith("# Requirement: Ban Repo()")
        assert "# Constraints: Python only" in content
        assert "# Design: forbidden check" in content
        # YAML should still be valid after stripping comments
        lines = [line for line in content.splitlines() if not line.startswith("#")]
        parsed = yaml.safe_load("\n".join(lines))
        assert isinstance(parsed, dict)

    def test_no_scaffolding_no_comments(self, tmp_path: Path) -> None:
        spec = promote_to_spec(_validated())
        result_path = save_spec(spec, tmp_path)

        content = result_path.read_text(encoding="utf-8")
        assert not content.startswith("#")


class TestPromoteToSpecCheckOverride:
    """Tests for the check_override parameter in promote_to_spec."""

    def test_check_override_beats_proposed_check(self) -> None:
        proposed = ForbiddenCheck(type="forbidden", pattern="OldPattern()")
        override = ForbiddenCheck(type="forbidden", pattern="NewPattern()")
        result = promote_to_spec(_validated(proposed_check=proposed), check_override=override)
        assert result.rule.check == override

    def test_check_override_rescues_pattern_without_proposed_check(self) -> None:
        override = ForbiddenCheck(type="forbidden", pattern="Override()")
        result = promote_to_spec(_validated(proposed_check=None), check_override=override)
        assert result.rule.check == override

    def test_none_override_falls_through_to_proposed_check(self) -> None:
        proposed = ForbiddenCheck(type="forbidden", pattern="Proposed()")
        result = promote_to_spec(_validated(proposed_check=proposed), check_override=None)
        assert result.rule.check == proposed

    def test_none_override_no_proposed_raises(self) -> None:
        with pytest.raises(PromotionError):
            promote_to_spec(_validated(proposed_check=None), check_override=None)
