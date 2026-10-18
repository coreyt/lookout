"""Tests for batch grid builder and GenerationMeta."""

from __future__ import annotations

from lookout.batch.grid import build_registry_grid, compute_input_hash
from lookout.batch.models import CellStatus
from lookout.models import (
    ForbiddenCheck,
    GenerationMeta,
    GenericVariant,
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
    RuleReporting,
    VariantExamples,
)


def _make_spec(
    pattern_id: str = "DI-001",
    variants: list[LanguageVariant] | None = None,
) -> PatternSpecFile:
    return PatternSpecFile(
        schema_version=2,
        pattern=PatternSpec(
            id=pattern_id,
            title="Test Pattern",
            description="Test description",
            rationale="Test rationale",
            tier=2,
            category="architecture",
            severity="warning",
            reporting=RuleReporting(
                default_message="Test message",
                confidence="medium",
            ),
            variants=variants or [],
        ),
    )


def _make_check() -> ForbiddenCheck:
    return ForbiddenCheck(type="forbidden", pattern="bad_call(...)")


class TestGenerationMeta:
    def test_on_generic_variant(self) -> None:
        meta = GenerationMeta(
            input_hash="abc123",
            generated_at="2026-01-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        variant = GenericVariant(
            check=_make_check(),
            examples=VariantExamples(),
            generation_meta=meta,
        )
        assert variant.generation_meta is not None
        assert variant.generation_meta.input_hash == "abc123"
        assert variant.generation_meta.batch_id is None

    def test_optional_on_generic_variant(self) -> None:
        variant = GenericVariant(
            check=_make_check(),
            examples=VariantExamples(),
        )
        assert variant.generation_meta is None

    def test_roundtrip_serialization(self) -> None:
        meta = GenerationMeta(
            input_hash="def456",
            generated_at="2026-01-01T00:00:00Z",
            model="gemini-2.5-pro",
            batch_id="batch_123",
        )
        variant = GenericVariant(
            check=_make_check(),
            examples=VariantExamples(),
            generation_meta=meta,
        )
        data = variant.model_dump(mode="json")
        restored = GenericVariant.model_validate(data)
        assert restored.generation_meta is not None
        assert restored.generation_meta.batch_id == "batch_123"


class TestComputeInputHash:
    def test_deterministic(self) -> None:
        h1 = compute_input_hash("desc", "rationale", None)
        h2 = compute_input_hash("desc", "rationale", None)
        assert h1 == h2

    def test_changes_with_input(self) -> None:
        h1 = compute_input_hash("desc1", "rationale", None)
        h2 = compute_input_hash("desc2", "rationale", None)
        assert h1 != h2

    def test_includes_upstream_check(self) -> None:
        h1 = compute_input_hash("desc", "rationale", None)
        h2 = compute_input_hash("desc", "rationale", "upstream check yaml")
        assert h1 != h2


class TestBuildRegistryGrid:
    def test_empty_patterns(self) -> None:
        cells = build_registry_grid([], ["python"], {})
        assert cells == []

    def test_missing_cells_for_no_variants(self) -> None:
        spec = _make_spec("DI-001", variants=[])
        cells = build_registry_grid([spec], ["python", "typescript"], {})
        # Should produce 2 generic cells (one per language), all MISSING
        assert len(cells) == 2
        assert all(c.status == CellStatus.MISSING for c in cells)
        assert all(c.framework is None for c in cells)
        languages = {c.language for c in cells}
        assert languages == {"python", "typescript"}

    def test_present_cells_for_existing_variant(self) -> None:
        check = _make_check()
        meta = GenerationMeta(
            input_hash=compute_input_hash("Test description", "Test rationale", None),
            generated_at="2026-01-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        spec = _make_spec(
            "DI-001",
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(
                        check=check,
                        examples=VariantExamples(),
                        generation_meta=meta,
                    ),
                ),
            ],
        )
        cells = build_registry_grid([spec], ["python"], {})
        assert len(cells) == 1
        assert cells[0].status == CellStatus.PRESENT

    def test_stale_cell_when_hash_mismatch(self) -> None:
        check = _make_check()
        meta = GenerationMeta(
            input_hash="old_hash_that_doesnt_match",
            generated_at="2026-01-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        spec = _make_spec(
            "DI-001",
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(
                        check=check,
                        examples=VariantExamples(),
                        generation_meta=meta,
                    ),
                ),
            ],
        )
        cells = build_registry_grid([spec], ["python"], {})
        assert len(cells) == 1
        assert cells[0].status == CellStatus.POSSIBLY_STALE

    def test_present_cell_no_generation_meta(self) -> None:
        """A variant with a check but no generation_meta is treated as PRESENT."""
        check = _make_check()
        spec = _make_spec(
            "DI-001",
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
        cells = build_registry_grid([spec], ["python"], {})
        assert len(cells) == 1
        # Has a check but no meta — treat as present (manually created)
        assert cells[0].status == CellStatus.PRESENT

    def test_framework_cells(self) -> None:
        spec = _make_spec("DI-001", variants=[])
        frameworks = {"python": ["django", "flask"]}
        cells = build_registry_grid([spec], ["python"], frameworks)
        # 1 generic + 2 framework = 3 cells
        assert len(cells) == 3
        generic = [c for c in cells if c.framework is None]
        fw = [c for c in cells if c.framework is not None]
        assert len(generic) == 1
        assert len(fw) == 2
        fw_names = {c.framework for c in fw}
        assert fw_names == {"django", "flask"}

    def test_framework_present_when_exists(self) -> None:
        from lookout.models import FrameworkVariant

        check = _make_check()
        # The upstream_check for framework hash is the generic check's JSON
        upstream_json = check.model_dump_json()
        meta = GenerationMeta(
            input_hash=compute_input_hash("Test description", "Test rationale", upstream_json),
            generated_at="2026-01-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        spec = _make_spec(
            "DI-001",
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(check=check, examples=VariantExamples()),
                    frameworks=[
                        FrameworkVariant(
                            name="django",
                            check=check,
                            examples=VariantExamples(),
                            generation_meta=meta,
                        ),
                    ],
                ),
            ],
        )
        frameworks = {"python": ["django", "flask"]}
        cells = build_registry_grid([spec], ["python"], frameworks)
        django_cell = next(c for c in cells if c.framework == "django")
        flask_cell = next(c for c in cells if c.framework == "flask")
        assert django_cell.status == CellStatus.PRESENT
        assert flask_cell.status == CellStatus.MISSING

    def test_likely_stale_framework_when_generic_newer(self) -> None:
        from lookout.models import FrameworkVariant

        check = _make_check()
        generic_meta = GenerationMeta(
            input_hash=compute_input_hash("Test description", "Test rationale", None),
            generated_at="2026-03-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        # Framework was generated with an older generic check
        fw_meta = GenerationMeta(
            input_hash="old_hash",
            generated_at="2026-01-01T00:00:00Z",
            model="claude-sonnet-4-20250514",
        )
        spec = _make_spec(
            "DI-001",
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(
                        check=check,
                        examples=VariantExamples(),
                        generation_meta=generic_meta,
                    ),
                    frameworks=[
                        FrameworkVariant(
                            name="django",
                            check=check,
                            examples=VariantExamples(),
                            generation_meta=fw_meta,
                        ),
                    ],
                ),
            ],
        )
        frameworks = {"python": ["django"]}
        cells = build_registry_grid([spec], ["python"], frameworks)
        django_cell = next(c for c in cells if c.framework == "django")
        assert django_cell.status == CellStatus.LIKELY_STALE

    def test_multiple_patterns(self) -> None:
        s1 = _make_spec("DI-001")
        s2 = _make_spec("LAYER-001")
        cells = build_registry_grid([s1, s2], ["python"], {})
        assert len(cells) == 2
        ids = {c.pattern_id for c in cells}
        assert ids == {"DI-001", "LAYER-001"}

    def test_na_framework_not_included(self) -> None:
        """Frameworks not in the frameworks dict for a language are not included."""
        spec = _make_spec("DI-001")
        # typescript has no frameworks listed
        cells = build_registry_grid([spec], ["python", "typescript"], {"python": ["django"]})
        ts_cells = [c for c in cells if c.language == "typescript"]
        # Only generic for typescript
        assert len(ts_cells) == 1
        assert ts_cells[0].framework is None
