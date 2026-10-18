"""Tests for the pattern registry core library."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lookout.discovery.exceptions import LookoutError
from lookout.models import (
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
    RawCheck,
    RuleReporting,
    VariantExamples,
)
from lookout.registry import (
    add_framework_variant,
    add_language_variant,
    approve_pattern,
    create_pattern,
    deprecate_pattern,
    from_validated_pattern,
    list_patterns,
    load_pattern,
    save_pattern,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_reporting() -> RuleReporting:
    return RuleReporting(default_message="Test message", confidence="medium")


def _make_check() -> RawCheck:
    return RawCheck(
        type="raw",
        config="rules:\n  - id: test\n    languages: [python]\n    severity: WARNING\n    message: test\n    pattern: foo(...)\n",
    )


def _make_spec_file(
    pattern_id: str = "TEST-001",
    status: str = "draft",
    variants: list[LanguageVariant] | None = None,
) -> PatternSpecFile:
    return PatternSpecFile(
        schema_version=2,
        pattern=PatternSpec(
            id=pattern_id,
            title="Test Pattern",
            description="A test pattern for unit tests.",
            rationale="Testing is important.",
            tier=2,
            category="testing",
            severity="warning",
            reporting=_make_reporting(),
            status=status,
            variants=variants or [],
        ),
    )


# ============================================================================
# create_pattern
# ============================================================================


class TestCreatePattern:
    def test_creates_draft_pattern_with_defaults(self) -> None:
        spec = create_pattern(
            id="ARCH-001",
            title="Constructor Injection",
            description="Use constructor injection for dependencies.",
            rationale="Makes dependencies explicit and testable.",
            category="architecture",
            severity="warning",
        )
        assert isinstance(spec, PatternSpecFile)
        assert spec.schema_version == 2
        assert spec.pattern.id == "ARCH-001"
        assert spec.pattern.status == "draft"
        assert spec.pattern.tier == 2
        assert spec.pattern.variants == []

    def test_creates_pattern_with_optional_fields(self) -> None:
        spec = create_pattern(
            id="SEC-001",
            title="SQL Injection Prevention",
            description="Prevent SQL injection attacks.",
            rationale="Security best practice.",
            category="security",
            severity="error",
            tier=1,
            subcategory="injection",
            tags=["owasp", "sql"],
            references=["https://owasp.org"],
            confidence="high",
        )
        assert spec.pattern.tier == 1
        assert spec.pattern.subcategory == "injection"
        assert spec.pattern.tags == ["owasp", "sql"]
        assert spec.pattern.references == ["https://owasp.org"]
        assert spec.pattern.reporting.confidence == "high"


# ============================================================================
# add_language_variant
# ============================================================================


class TestAddLanguageVariant:
    def test_adds_language_variant_to_empty_spec(self) -> None:
        spec = _make_spec_file()
        check = _make_check()
        result = add_language_variant(spec, "python", check)

        assert result is not spec  # immutable style
        assert len(result.pattern.variants) == 1
        assert result.pattern.variants[0].language == "python"
        assert result.pattern.variants[0].generic is not None
        assert result.pattern.variants[0].generic.check == check

    def test_adds_with_examples_and_version(self) -> None:
        spec = _make_spec_file()
        check = _make_check()
        examples = VariantExamples()
        result = add_language_variant(
            spec, "python", check, examples=examples, version_constraint=">=3.10"
        )
        assert result.pattern.variants[0].version_constraint == ">=3.10"
        assert result.pattern.variants[0].generic is not None
        assert result.pattern.variants[0].generic.examples == examples

    def test_raises_if_language_already_exists(self) -> None:
        spec = _make_spec_file(variants=[LanguageVariant(language="python")])
        with pytest.raises(LookoutError, match="already exists"):
            add_language_variant(spec, "python", _make_check())

    def test_original_spec_unchanged(self) -> None:
        spec = _make_spec_file()
        add_language_variant(spec, "python", _make_check())
        assert len(spec.pattern.variants) == 0


# ============================================================================
# add_framework_variant
# ============================================================================


class TestAddFrameworkVariant:
    def test_adds_framework_to_existing_language(self) -> None:
        spec = _make_spec_file(variants=[LanguageVariant(language="python")])
        check = _make_check()
        result = add_framework_variant(spec, "python", "django", check)

        assert len(result.pattern.variants) == 1
        assert len(result.pattern.variants[0].frameworks) == 1
        fw = result.pattern.variants[0].frameworks[0]
        assert fw.name == "django"
        assert fw.check == check

    def test_adds_with_version_constraint(self) -> None:
        spec = _make_spec_file(variants=[LanguageVariant(language="python")])
        result = add_framework_variant(
            spec, "python", "django", _make_check(), version_constraint=">=4.0"
        )
        assert result.pattern.variants[0].frameworks[0].version_constraint == ">=4.0"

    def test_raises_if_language_not_found(self) -> None:
        spec = _make_spec_file()
        with pytest.raises(LookoutError, match="Language .* not found"):
            add_framework_variant(spec, "python", "django", _make_check())

    def test_raises_if_framework_already_exists(self) -> None:
        from lookout.models import FrameworkVariant

        spec = _make_spec_file(
            variants=[
                LanguageVariant(
                    language="python",
                    frameworks=[
                        FrameworkVariant(
                            name="django",
                            check=_make_check(),
                            examples=VariantExamples(),
                        )
                    ],
                )
            ]
        )
        with pytest.raises(LookoutError, match="already exists"):
            add_framework_variant(spec, "python", "django", _make_check())

    def test_original_spec_unchanged(self) -> None:
        spec = _make_spec_file(variants=[LanguageVariant(language="python")])
        add_framework_variant(spec, "python", "django", _make_check())
        assert len(spec.pattern.variants[0].frameworks) == 0


# ============================================================================
# Lifecycle: deprecate and approve
# ============================================================================


class TestLifecycle:
    def test_deprecate_sets_status(self) -> None:
        spec = _make_spec_file(status="active")
        result = deprecate_pattern(spec)
        assert result.pattern.status == "deprecated"
        assert spec.pattern.status == "active"  # original unchanged

    def test_approve_sets_status(self) -> None:
        spec = _make_spec_file(status="draft")
        result = approve_pattern(spec)
        assert result.pattern.status == "active"
        assert spec.pattern.status == "draft"


# ============================================================================
# Persistence: save, load, list
# ============================================================================


class TestPersistence:
    def test_save_creates_yaml_file(self, tmp_path: Path) -> None:
        spec = _make_spec_file(pattern_id="TEST-001")
        path = save_pattern(spec, tmp_path)

        assert path == tmp_path / "TEST-001.yaml"
        assert path.exists()

        data = yaml.safe_load(path.read_text())
        assert data["schema_version"] == 2
        assert data["pattern"]["id"] == "TEST-001"

    def test_load_returns_spec(self, tmp_path: Path) -> None:
        spec = _make_spec_file(pattern_id="TEST-001")
        save_pattern(spec, tmp_path)

        loaded = load_pattern("TEST-001", tmp_path)
        assert loaded is not None
        assert loaded.pattern.id == "TEST-001"
        assert loaded.pattern.status == "draft"

    def test_load_returns_none_for_missing(self, tmp_path: Path) -> None:
        result = load_pattern("MISSING-999", tmp_path)
        assert result is None

    def test_list_patterns_returns_all(self, tmp_path: Path) -> None:
        save_pattern(_make_spec_file(pattern_id="AAA-001"), tmp_path)
        save_pattern(_make_spec_file(pattern_id="BBB-001"), tmp_path)
        save_pattern(_make_spec_file(pattern_id="CCC-001"), tmp_path)

        patterns = list_patterns(tmp_path)
        assert len(patterns) == 3
        ids = [p.pattern.id for p in patterns]
        assert ids == ["AAA-001", "BBB-001", "CCC-001"]  # sorted

    def test_list_patterns_empty_dir(self, tmp_path: Path) -> None:
        assert list_patterns(tmp_path) == []

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        spec1 = _make_spec_file(pattern_id="TEST-001", status="draft")
        save_pattern(spec1, tmp_path)

        spec2 = _make_spec_file(pattern_id="TEST-001", status="active")
        save_pattern(spec2, tmp_path)

        loaded = load_pattern("TEST-001", tmp_path)
        assert loaded is not None
        assert loaded.pattern.status == "active"

    def test_save_roundtrip_with_variants(self, tmp_path: Path) -> None:
        spec = _make_spec_file(pattern_id="TEST-001")
        spec = add_language_variant(spec, "python", _make_check())
        spec = add_framework_variant(spec, "python", "django", _make_check())
        save_pattern(spec, tmp_path)

        loaded = load_pattern("TEST-001", tmp_path)
        assert loaded is not None
        assert len(loaded.pattern.variants) == 1
        assert loaded.pattern.variants[0].language == "python"
        assert len(loaded.pattern.variants[0].frameworks) == 1
        assert loaded.pattern.variants[0].frameworks[0].name == "django"


# ============================================================================
# from_validated_pattern
# ============================================================================


class TestFromValidatedPattern:
    def test_converts_validated_pattern(self) -> None:
        from datetime import datetime

        from lookout.discovery.models import (
            DiscoveredPattern,
            PatternExample,
            PatternExamples,
            ValidatedPattern,
            ValidationMetadata,
        )

        discovered = DiscoveredPattern(
            pattern_id="ARCH-DI-001",
            title="Constructor Injection Required",
            category="architecture",
            description="Dependencies should be injected via constructors.",
            rationale="Makes dependencies explicit and testable for unit tests.",
            severity="warning",
            confidence="medium",
            languages=["python", "typescript"],
            tags=["solid", "di"],
            examples=PatternExamples(
                good=[PatternExample(language="python", code="def __init__(self, repo): ...")],
                bad=[
                    PatternExample(language="python", code="def __init__(self): self.repo = Repo()")
                ],
            ),
            references=["https://martinfowler.com/articles/injection.html"],
            discovered_by="test",
            discovered_at=datetime(2026, 1, 1),
        )

        validated = ValidatedPattern(
            discovered=discovered,
            validation=ValidationMetadata(
                validated_by="tester",
                validated_at=datetime(2026, 1, 2),
            ),
            effective_tier=2,
        )

        spec = from_validated_pattern(validated)

        assert isinstance(spec, PatternSpecFile)
        assert spec.schema_version == 2
        assert spec.pattern.id == "ARCH-DI-001"
        assert spec.pattern.title == "Constructor Injection Required"
        assert spec.pattern.category == "architecture"
        assert spec.pattern.severity == "warning"
        assert spec.pattern.tier == 2
        assert spec.pattern.tags == ["solid", "di"]
        assert spec.pattern.status == "draft"
        assert spec.pattern.variants == []
        assert spec.pattern.reporting.confidence == "medium"


# ============================================================================
# PatternSpec.status field
# ============================================================================


class TestPatternSpecStatus:
    def test_default_status_is_draft(self) -> None:
        spec = _make_spec_file()
        assert spec.pattern.status == "draft"

    def test_status_accepts_active(self) -> None:
        spec = _make_spec_file(status="active")
        assert spec.pattern.status == "active"

    def test_status_accepts_deprecated(self) -> None:
        spec = _make_spec_file(status="deprecated")
        assert spec.pattern.status == "deprecated"

    def test_variants_defaults_to_empty_list(self) -> None:
        spec = PatternSpec(
            id="TEST-001",
            title="Test",
            description="Test description",
            rationale="Test rationale",
            tier=2,
            category="testing",
            severity="warning",
            reporting=_make_reporting(),
        )
        assert spec.variants == []
