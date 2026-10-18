"""Tests for project profile detection and spec/variant selection."""

from __future__ import annotations

import json
from pathlib import Path

from lookout.models import (
    ForbiddenCheck,
    FrameworkVariant,
    GenericVariant,
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
    RuleExamples,
    RuleReporting,
    RuleSpec,
    RuleSpecFile,
    VariantExamples,
)
from lookout.project import (
    ProjectProfile,
    detect_profile,
    normalize_name,
    select_specs,
)


def _generic(pattern: str = "print(...)") -> GenericVariant:
    return GenericVariant(
        check=ForbiddenCheck(type="forbidden", pattern=pattern),
        examples=VariantExamples(),
    )


def _framework(name: str) -> FrameworkVariant:
    return FrameworkVariant(
        name=name,
        check=ForbiddenCheck(type="forbidden", pattern="x(...)"),
        examples=VariantExamples(),
    )


def _pattern(
    pattern_id: str = "ARCH-900",
    *,
    tier: int = 2,
    status: str = "active",
    variants: list[LanguageVariant] | None = None,
) -> PatternSpecFile:
    return PatternSpecFile(
        pattern=PatternSpec(
            id=pattern_id,
            title="t",
            description="d",
            rationale="r",
            tier=tier,
            category="architecture",
            severity="warning",
            reporting=RuleReporting(default_message="m", confidence="high"),
            status=status,  # type: ignore[arg-type]
            variants=variants
            if variants is not None
            else [
                LanguageVariant(
                    language="python",
                    generic=_generic(),
                    frameworks=[_framework("django"), _framework("sqlalchemy")],
                ),
                LanguageVariant(language="typescript", generic=_generic()),
            ],
        )
    )


def _v1_rule(languages: list[str], tier: int = 1) -> RuleSpecFile:
    return RuleSpecFile(
        rule=RuleSpec(
            id="V1-001",
            title="t",
            description="d",
            rationale="r",
            tier=tier,
            category="c",
            severity="error",
            languages=languages,
            check=ForbiddenCheck(type="forbidden", pattern="eval(...)"),
            reporting=RuleReporting(default_message="m", confidence="high"),
            examples=RuleExamples(good=[], bad=[]),
            references=[],
        )
    )


class TestNormalizeName:
    def test_strips_version_and_extras(self) -> None:
        assert normalize_name("Flask_SQLAlchemy[async]>=3.0") == "flask-sqlalchemy"
        assert normalize_name("django == 4.2") == "django"

    def test_unifies_dots(self) -> None:
        assert normalize_name("nest.js") == "nestjs"

    def test_keeps_npm_scope(self) -> None:
        assert normalize_name("@angular/core@17.0.0") == "@angular/core"


class TestDetectProfile:
    def test_python_project_with_frameworks(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\ndependencies = ["Django>=4", "SQLAlchemy"]\n'
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("x = 1\n")

        profile = detect_profile([Path("src")])

        assert profile.detected
        assert profile.languages == {"python"}
        assert profile.has_framework("django")
        assert profile.has_framework("sqlalchemy")
        assert not profile.has_framework("flask")

    def test_node_project_aliases(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": {"@angular/core": "^17", "@nestjs/core": "^10"}})
        )
        (tmp_path / "tsconfig.json").write_text("{}")
        (tmp_path / "main.ts").write_text("export const x = 1;\n")

        profile = detect_profile([Path(".")])

        assert profile.languages == {"javascript", "typescript"}
        assert profile.has_framework("angular")
        assert profile.has_framework("nestjs")

    def test_source_files_detected_without_manifest(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "thing.go").write_text("package lib\n")

        profile = detect_profile([Path("lib")])

        assert profile.languages == {"go"}
        assert profile.frameworks == set()

    def test_nothing_detected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "README.md").write_text("hi\n")

        profile = detect_profile([Path(".")])

        assert not profile.detected
        assert "running all variants" in profile.describe()


class TestSelectSpecs:
    def test_no_profile_keeps_everything(self) -> None:
        report = select_specs([_pattern()], None)

        assert len(report.selected) == 1
        spec = report.selected[0]
        assert isinstance(spec, PatternSpecFile)
        assert len(spec.pattern.variants) == 2
        assert report.dropped_variants == []

    def test_undetected_profile_keeps_everything(self) -> None:
        report = select_specs([_pattern()], ProjectProfile(detected=False))

        assert len(report.selected) == 1
        assert report.dropped_variants == []

    def test_filters_languages_and_frameworks(self) -> None:
        profile = ProjectProfile(languages={"python"}, frameworks={"sqlalchemy"}, detected=True)

        report = select_specs([_pattern()], profile)

        spec = report.selected[0]
        assert isinstance(spec, PatternSpecFile)
        assert [v.language for v in spec.pattern.variants] == ["python"]
        assert [fw.name for fw in spec.pattern.variants[0].frameworks] == ["sqlalchemy"]
        assert report.dropped_variants == ["ARCH-900/python/django", "ARCH-900/typescript"]

    def test_does_not_mutate_input_spec(self) -> None:
        original = _pattern()
        profile = ProjectProfile(languages={"python"}, detected=True)

        select_specs([original], profile)

        assert len(original.pattern.variants) == 2
        assert len(original.pattern.variants[0].frameworks) == 2

    def test_drops_spec_with_no_matching_language(self) -> None:
        profile = ProjectProfile(languages={"go"}, detected=True)

        report = select_specs([_pattern()], profile)

        assert report.selected == []
        assert report.skipped_language == ["ARCH-900"]

    def test_framework_only_variant_dropped_when_framework_absent(self) -> None:
        variants = [
            LanguageVariant(language="python", generic=None, frameworks=[_framework("django")])
        ]
        profile = ProjectProfile(languages={"python"}, frameworks=set(), detected=True)

        report = select_specs([_pattern(variants=variants)], profile)

        assert report.selected == []

    def test_skips_deprecated(self) -> None:
        report = select_specs([_pattern(status="deprecated")], None)

        assert report.selected == []
        assert report.skipped_deprecated == ["ARCH-900"]

    def test_max_tier(self) -> None:
        specs = [_pattern("T1", tier=1), _pattern("T3", tier=3), _v1_rule(["python"], tier=2)]

        report = select_specs(specs, None, max_tier=1)

        assert [s.pattern.id for s in report.selected if isinstance(s, PatternSpecFile)] == ["T1"]
        assert report.skipped_tier == ["T3", "V1-001"]

    def test_v1_rule_language_filter(self) -> None:
        profile = ProjectProfile(languages={"typescript"}, detected=True)

        report = select_specs([_v1_rule(["python"])], profile)

        assert report.selected == []
        assert report.skipped_language == ["V1-001"]

    def test_summary_lines(self) -> None:
        profile = ProjectProfile(languages={"python"}, detected=True)

        report = select_specs([_pattern(), _pattern("OLD", status="deprecated")], profile)

        lines = report.summary_lines()
        assert any("deprecated" in line and "OLD" in line for line in lines)
        assert any("not applicable" in line for line in lines)
