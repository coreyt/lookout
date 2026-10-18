"""Tests for shared prompt loader and batch prompt builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from lookout.batch.models import CellStatus, RegistryCell
from lookout.batch.prompts import build_batch_prompts
from lookout.models import (
    ForbiddenCheck,
    GenericVariant,
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
    RuleReporting,
    VariantExamples,
)
from lookout.prompts import PromptTemplateLoader


def _make_spec(
    pattern_id: str = "DI-001",
    variants: list[LanguageVariant] | None = None,
) -> PatternSpecFile:
    return PatternSpecFile(
        schema_version=2,
        pattern=PatternSpec(
            id=pattern_id,
            title="Dependency Injection",
            description="Use constructor injection instead of service locator",
            rationale="Constructor injection makes dependencies explicit",
            tier=2,
            category="architecture",
            severity="warning",
            reporting=RuleReporting(
                default_message="Use constructor injection (DI-001)",
                confidence="medium",
            ),
            variants=variants or [],
        ),
    )


PROMPTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "prompts"


class TestPromptTemplateLoader:
    def test_load_existing_file(self) -> None:
        loader = PromptTemplateLoader(PROMPTS_DIR)
        content = loader.load("system-prompt-pattern-research.md")
        assert "software architecture" in content.lower()

    def test_load_caches(self) -> None:
        loader = PromptTemplateLoader(PROMPTS_DIR)
        c1 = loader.load("system-prompt-pattern-research.md")
        c2 = loader.load("system-prompt-pattern-research.md")
        assert c1 is c2

    def test_load_missing_file(self) -> None:
        loader = PromptTemplateLoader(PROMPTS_DIR)
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent.md")

    def test_render_substitutes_variables(self, tmp_path: Path) -> None:
        template_file = tmp_path / "test.md"
        template_file.write_text("```\nHello {{NAME}}, you are {{ROLE}}\n```")
        loader = PromptTemplateLoader(tmp_path)
        result = loader.render("test.md", NAME="Alice", ROLE="admin")
        assert result == "Hello Alice, you are admin"

    def test_render_unresolved_raises(self, tmp_path: Path) -> None:
        template_file = tmp_path / "test.md"
        template_file.write_text("```\nHello {{NAME}}, {{MISSING}}\n```")
        loader = PromptTemplateLoader(tmp_path)
        with pytest.raises(ValueError, match="MISSING"):
            loader.render("test.md", NAME="Alice")


class TestBuildBatchPrompts:
    def test_generic_variant_prompt(self) -> None:
        spec = _make_spec()
        cell = RegistryCell("DI-001", "python", None, CellStatus.MISSING)
        loader = PromptTemplateLoader(PROMPTS_DIR)
        system, user = build_batch_prompts(cell, spec, loader)
        assert "software architecture" in system.lower() or "pattern" in system.lower()
        assert "python" in user.lower()
        assert "DI-001" in user or "di-001" in user

    def test_framework_variant_prompt_with_generic(self) -> None:
        check = ForbiddenCheck(type="forbidden", pattern="ServiceLocator.get(...)")
        spec = _make_spec(
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(
                        check=check,
                        examples=VariantExamples(),
                    ),
                ),
            ],
        )
        cell = RegistryCell("DI-001", "python", "django", CellStatus.MISSING)
        loader = PromptTemplateLoader(PROMPTS_DIR)
        system, user = build_batch_prompts(cell, spec, loader)
        assert "django" in user.lower()
        # Should include the generic check as context
        assert "ServiceLocator" in user or "generic" in user.lower()

    def test_framework_variant_without_generic_falls_back(self) -> None:
        spec = _make_spec(variants=[])
        cell = RegistryCell("DI-001", "python", "django", CellStatus.MISSING)
        loader = PromptTemplateLoader(PROMPTS_DIR)
        # Should not crash even without a generic variant
        system, user = build_batch_prompts(cell, spec, loader)
        assert "django" in user.lower()
