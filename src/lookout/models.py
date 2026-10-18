from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class MustWrapCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["must_wrap"]
    target: list[str]
    wrapper: list[str]


class ForbiddenCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["forbidden"]
    pattern: str


class RequiredWithCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["required_with"]
    if_present: str
    must_have: str


class RawCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["raw"]
    config: str
    engine: str = "semgrep"


class MetavariableRegex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metavariable: str
    regex: str


class PatternDiscoveryCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["pattern_discovery"]
    patterns: list[str]
    metavariable_regex: list[MetavariableRegex] | None = None


class PathScope(BaseModel):
    """Restrict a check to files matching include globs and not matching exclude globs.

    Globs follow Semgrep's ``paths:`` semantics (matched against the path
    relative to the scan root, e.g. ``controllers/**`` or ``*_test.py``).
    """

    model_config = ConfigDict(extra="forbid")
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    def merged_with(self, override: PathScope | None) -> PathScope:
        """Combine with a narrower scope: includes are replaced, excludes accumulate."""
        if override is None:
            return self
        return PathScope(
            include=override.include or self.include,
            exclude=[*self.exclude, *override.exclude],
        )


RuleCheck = Annotated[
    MustWrapCheck | ForbiddenCheck | RequiredWithCheck | RawCheck | PatternDiscoveryCheck,
    Field(discriminator="type"),
]


class RuleReporting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_message: str
    confidence: Literal["low", "medium", "high"]
    documentation_url: str | None = None


class RuleExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    code: str


class RuleExamples(BaseModel):
    model_config = ConfigDict(extra="forbid")

    good: list[RuleExample]
    bad: list[RuleExample]


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    rationale: str
    tier: int = Field(ge=1, le=3)
    category: str
    severity: Literal["error", "warning", "info"]
    languages: list[str]
    check: RuleCheck
    paths: PathScope | None = None
    reporting: RuleReporting
    examples: RuleExamples
    references: list[str]


class RuleSpecFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    rule: RuleSpec


# --- Schema version 2: Hierarchical pattern specs ---


class VariantExamples(BaseModel):
    model_config = ConfigDict(extra="forbid")

    good: list[RuleExample] = Field(default_factory=list)
    bad: list[RuleExample] = Field(default_factory=list)


class GenerationMeta(BaseModel):
    """Tracks what inputs produced a variant's check."""

    model_config = ConfigDict(extra="forbid")

    input_hash: str
    generated_at: str
    model: str
    batch_id: str | None = None


class GenericVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: RuleCheck
    paths: PathScope | None = None
    examples: VariantExamples
    generation_meta: GenerationMeta | None = None


class FrameworkVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version_constraint: str | None = None
    check: RuleCheck
    paths: PathScope | None = None
    examples: VariantExamples
    generation_meta: GenerationMeta | None = None


class LanguageVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    version_constraint: str | None = None
    generic: GenericVariant | None = None
    frameworks: list[FrameworkVariant] = Field(default_factory=list)


class PatternSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    rationale: str
    version: str = "1.0.0"
    tier: int = Field(ge=1, le=3)
    category: str
    subcategory: str | None = None
    severity: Literal["error", "warning", "info"]
    tags: list[str] = Field(default_factory=list)
    reporting: RuleReporting
    references: list[str] = Field(default_factory=list)
    status: Literal["draft", "active", "deprecated"] = "draft"
    paths: PathScope | None = None
    variants: list[LanguageVariant] = Field(default_factory=list)


class PatternSpecFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    pattern: PatternSpec


@dataclass(frozen=True)
class Finding:
    """Represents a pattern violation (enforcement mode)."""

    pattern_id: str
    title: str
    category: str
    severity: str
    file: str
    line: int
    message: str
    snippet: str
    engine: str
    tier: int
    baselined: bool = False


@dataclass(frozen=True)
class PatternInstance:
    """Represents a discovered pattern instance (discovery mode)."""

    pattern_id: str
    title: str
    category: str
    file: str
    line: int
    snippet: str
    confidence: str


@dataclass(frozen=True)
class RuleError:
    """Represents an error executing a rule (e.g. parse error)."""

    rule_id: str
    message: str
    level: str
    type: str
