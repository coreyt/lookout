"""Pattern registry — core library for managing PatternSpecFile instances.

Pure functions: no CLI/IO beyond file read/write. All mutation functions
return new PatternSpecFile instances (immutable-style).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from lookout.discovery.exceptions import LookoutError
from lookout.discovery.models import ValidatedPattern
from lookout.models import (
    FrameworkVariant,
    GenericVariant,
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
    RuleCheck,
    RuleReporting,
    VariantExamples,
)

# ============================================================================
# Creation
# ============================================================================


def create_pattern(
    id: str,
    title: str,
    description: str,
    rationale: str,
    category: str,
    severity: Literal["error", "warning", "info"],
    tier: int = 2,
    subcategory: str | None = None,
    tags: list[str] | None = None,
    references: list[str] | None = None,
    confidence: Literal["low", "medium", "high"] = "medium",
) -> PatternSpecFile:
    """Create a new draft PatternSpecFile with no variants."""
    return PatternSpecFile(
        schema_version=2,
        pattern=PatternSpec(
            id=id,
            title=title,
            description=description,
            rationale=rationale,
            tier=tier,
            category=category,
            subcategory=subcategory,
            severity=severity,
            tags=tags or [],
            references=references or [],
            reporting=RuleReporting(
                default_message=f"{title} ({id})",
                confidence=confidence,
            ),
            status="draft",
            variants=[],
        ),
    )


# ============================================================================
# Mutation (immutable-style — return new instances)
# ============================================================================


def add_language_variant(
    spec: PatternSpecFile,
    language: str,
    check: RuleCheck,
    examples: VariantExamples | None = None,
    version_constraint: str | None = None,
) -> PatternSpecFile:
    """Add a language variant with a generic check. Raises if language exists."""
    for v in spec.pattern.variants:
        if v.language == language:
            raise LookoutError(f"Language '{language}' already exists in pattern {spec.pattern.id}")

    new_variant = LanguageVariant(
        language=language,
        version_constraint=version_constraint,
        generic=GenericVariant(
            check=check,
            examples=examples or VariantExamples(),
        ),
    )

    new_variants = list(spec.pattern.variants) + [new_variant]
    return _replace_variants(spec, new_variants)


def add_framework_variant(
    spec: PatternSpecFile,
    language: str,
    framework: str,
    check: RuleCheck,
    examples: VariantExamples | None = None,
    version_constraint: str | None = None,
) -> PatternSpecFile:
    """Add a framework variant under a language. Raises if language missing or framework exists."""
    lang_found = False
    new_variants: list[LanguageVariant] = []

    for v in spec.pattern.variants:
        if v.language == language:
            lang_found = True
            for fw in v.frameworks:
                if fw.name == framework:
                    raise LookoutError(
                        f"Framework '{framework}' already exists for language "
                        f"'{language}' in pattern {spec.pattern.id}"
                    )
            new_fw = FrameworkVariant(
                name=framework,
                version_constraint=version_constraint,
                check=check,
                examples=examples or VariantExamples(),
            )
            # Rebuild the language variant with the new framework appended
            new_variants.append(
                LanguageVariant(
                    language=v.language,
                    version_constraint=v.version_constraint,
                    generic=v.generic,
                    frameworks=list(v.frameworks) + [new_fw],
                )
            )
        else:
            new_variants.append(v)

    if not lang_found:
        raise LookoutError(f"Language '{language}' not found in pattern {spec.pattern.id}")

    return _replace_variants(spec, new_variants)


# ============================================================================
# Lifecycle
# ============================================================================


def deprecate_pattern(spec: PatternSpecFile) -> PatternSpecFile:
    """Return a copy with status set to 'deprecated'."""
    return _replace_status(spec, "deprecated")


def approve_pattern(spec: PatternSpecFile) -> PatternSpecFile:
    """Return a copy with status set to 'active'."""
    return _replace_status(spec, "active")


# ============================================================================
# Persistence
# ============================================================================


def load_pattern(pattern_id: str, rules_dir: Path) -> PatternSpecFile | None:
    """Load a single pattern by ID from the rules directory."""
    path = rules_dir / f"{pattern_id}.yaml"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return PatternSpecFile.model_validate(data)


def save_pattern(spec: PatternSpecFile, rules_dir: Path) -> Path:
    """Save a PatternSpecFile to YAML in the rules directory."""
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{spec.pattern.id}.yaml"
    data = spec.model_dump(mode="json", exclude_none=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return path


def list_patterns(rules_dir: Path) -> list[PatternSpecFile]:
    """Load all v2 PatternSpecFiles from a directory, sorted by ID."""
    if not rules_dir.exists():
        return []
    from lookout.specs import load_specs

    return [s for s in load_specs(rules_dir) if isinstance(s, PatternSpecFile)]


# ============================================================================
# Bridge from discovery pipeline
# ============================================================================


def from_validated_pattern(pattern: ValidatedPattern) -> PatternSpecFile:
    """Convert a ValidatedPattern from the discovery pipeline into a draft PatternSpecFile."""
    d = pattern.discovered
    return create_pattern(
        id=d.pattern_id,
        title=d.title,
        description=d.description,
        rationale=d.rationale,
        category=d.category,
        severity=d.severity,
        tier=pattern.effective_tier,
        subcategory=d.subcategory,
        tags=list(d.tags),
        references=list(d.references),
        confidence=d.confidence,
    )


# ============================================================================
# Internal helpers
# ============================================================================


def _replace_variants(
    spec: PatternSpecFile, new_variants: list[LanguageVariant]
) -> PatternSpecFile:
    """Return a new PatternSpecFile with replaced variants list."""
    new_pattern = spec.pattern.model_copy(update={"variants": new_variants})
    return spec.model_copy(update={"pattern": new_pattern})


def _replace_status(spec: PatternSpecFile, status: str) -> PatternSpecFile:
    """Return a new PatternSpecFile with replaced status."""
    new_pattern = spec.pattern.model_copy(update={"status": status})
    return spec.model_copy(update={"pattern": new_pattern})
