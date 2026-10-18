"""Promotion logic for converting discovered patterns into enforcement rules."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml

from lookout.discovery.models import ValidatedPattern
from lookout.models import (
    RuleCheck,
    RuleExample,
    RuleExamples,
    RuleReporting,
    RuleSpec,
    RuleSpecFile,
)

Severity = Literal["error", "warning", "info"]

#: Promoted rules start at this severity until a human reviews them, whatever
#: the discovery stage proposed. ``lookout promote --severity`` overrides it.
DEFAULT_PROMOTED_SEVERITY: Severity = "warning"


class PromotionError(ValueError):
    """Raised when a pattern cannot be promoted into an enforcement rule."""


def promote_to_spec(
    pattern: ValidatedPattern,
    check_override: RuleCheck | None = None,
    severity: Severity | None = None,
) -> RuleSpecFile:
    """Convert a validated pattern into a Lookout rule specification.

    Args:
        pattern: The validated pattern to promote
        check_override: Optional check to use instead of the pattern's proposed_check
        severity: Severity for the promoted rule. Defaults to
            :data:`DEFAULT_PROMOTED_SEVERITY` ("warning") regardless of the
            discovered pattern's severity, so unreviewed rules never fail a
            build on their own.

    Returns:
        A RuleSpecFile instance

    Raises:
        PromotionError: if neither ``check_override`` nor the pattern's
            ``proposed_check`` provides a check. A promoted spec is an
            enforcement rule, so there is no placeholder fallback; run
            ``lookout promote --generate-check`` or add a ``proposed_check``.
    """
    discovered = pattern.discovered

    check = check_override or discovered.proposed_check
    if not check:
        raise PromotionError(
            f"{discovered.pattern_id} has no enforcement check: the pattern carries no "
            "proposed_check and none was supplied. Re-run with --generate-check to have an "
            "LLM write and verify one, or add a proposed_check to the validated pattern."
        )

    rule = RuleSpec(
        id=discovered.pattern_id,
        title=discovered.title,
        description=discovered.description,
        rationale=discovered.rationale,
        tier=pattern.effective_tier,
        category=discovered.category,
        severity=severity or DEFAULT_PROMOTED_SEVERITY,
        languages=discovered.languages,
        check=check,
        reporting=RuleReporting(
            default_message=f"{discovered.title} ({discovered.pattern_id})",
            confidence=discovered.confidence,
            documentation_url=discovered.references[0] if discovered.references else None,
        ),
        examples=RuleExamples(
            good=[
                RuleExample(language=ex.language, code=ex.code) for ex in discovered.examples.good
            ],
            bad=[RuleExample(language=ex.language, code=ex.code) for ex in discovered.examples.bad],
        ),
        references=discovered.references,
    )

    return RuleSpecFile(schema_version=1, rule=rule)


def save_spec(
    spec: RuleSpecFile,
    output_dir: Path,
    scaffolding: str | None = None,
) -> Path:
    """Save a RuleSpecFile to a YAML file.

    If scaffolding text is provided (Requirement/Constraints/Design from the
    LLM rule generator), it is prepended as YAML comments above the rule body.

    Args:
        spec: The specification to save
        output_dir: Directory to save the YAML file in
        scaffolding: Optional scaffolding text to include as YAML comments

    Returns:
        Path to the saved file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{spec.rule.id}.yaml"

    # Convert to dict for YAML serialization
    # We use model_dump(mode="json") to handle Pydantic objects/enums
    data = spec.model_dump(mode="json")

    with file_path.open("w", encoding="utf-8") as f:
        if scaffolding:
            for line in scaffolding.splitlines():
                f.write(f"# {line}\n")
            f.write("#\n")
        yaml.safe_dump(data, f, sort_keys=False, indent=2)

    return file_path
