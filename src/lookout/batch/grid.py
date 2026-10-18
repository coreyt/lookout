"""Grid builder — creates cells for the pattern x language x framework matrix."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel

from lookout.batch.models import CellStatus, RegistryCell, _parse_iso
from lookout.models import (
    FrameworkVariant,
    GenerationMeta,
    GenericVariant,
    LanguageVariant,
    PatternSpec,
    PatternSpecFile,
)


def compute_input_hash(
    description: str,
    rationale: str,
    upstream_check: str | None,
) -> str:
    """SHA-256 hash of inputs that affect generation output."""
    parts = [description, rationale]
    if upstream_check is not None:
        parts.append(upstream_check)
    combined = "\n---\n".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()


def build_registry_grid(
    patterns: list[PatternSpecFile],
    languages: list[str],
    frameworks: dict[str, list[str]],
) -> list[RegistryCell]:
    """Build cells for every pattern x language x (generic + frameworks) combination.

    Returns a flat list of RegistryCell with appropriate status.
    """
    cells: list[RegistryCell] = []

    for spec in patterns:
        pat = spec.pattern
        variant_map: dict[str, LanguageVariant] = {v.language: v for v in pat.variants}

        for lang in languages:
            lang_variant = variant_map.get(lang)

            # Generic cell
            generic_status = _compute_generic_status(pat, lang_variant)
            cells.append(
                RegistryCell(
                    pattern_id=pat.id,
                    language=lang,
                    framework=None,
                    status=generic_status,
                )
            )

            # Framework cells
            lang_frameworks = frameworks.get(lang, [])
            for fw_name in lang_frameworks:
                fw_status = _compute_framework_status(pat, lang_variant, fw_name)
                cells.append(
                    RegistryCell(
                        pattern_id=pat.id,
                        language=lang,
                        framework=fw_name,
                        status=fw_status,
                    )
                )

    return cells


def _compute_generic_status(
    pat: PatternSpec,
    lang_variant: LanguageVariant | None,
) -> CellStatus:
    """Determine status for a generic (non-framework) cell."""
    if lang_variant is None or lang_variant.generic is None:
        return CellStatus.MISSING

    generic = lang_variant.generic
    return _check_variant_staleness(pat, generic, upstream_check=None)


def _compute_framework_status(
    pat: PatternSpec,
    lang_variant: LanguageVariant | None,
    fw_name: str,
) -> CellStatus:
    """Determine status for a framework cell."""
    if lang_variant is None:
        return CellStatus.MISSING

    fw_variant: FrameworkVariant | None = None
    for fw in lang_variant.frameworks:
        if fw.name == fw_name:
            fw_variant = fw
            break

    if fw_variant is None:
        return CellStatus.MISSING

    # For framework variants, check if the generic variant is newer
    generic = lang_variant.generic
    if (
        generic is not None
        and generic.generation_meta is not None
        and fw_variant.generation_meta is not None
    ):
        generic_time = _parse_iso(generic.generation_meta.generated_at)
        fw_time = _parse_iso(fw_variant.generation_meta.generated_at)
        if generic_time > fw_time:
            return CellStatus.LIKELY_STALE

    # Get upstream check string for hash comparison
    upstream_check: str | None = None
    if generic is not None:
        upstream_check = _check_to_string(generic.check)

    return _check_variant_staleness(pat, fw_variant, upstream_check)


def _check_variant_staleness(
    pat: PatternSpec,
    variant: GenericVariant | FrameworkVariant,
    upstream_check: str | None,
) -> CellStatus:
    """Check if a variant is stale based on generation_meta hash."""
    meta: GenerationMeta | None = variant.generation_meta
    if meta is None:
        return CellStatus.PRESENT

    current_hash = compute_input_hash(pat.description, pat.rationale, upstream_check)
    if meta.input_hash == current_hash:
        return CellStatus.PRESENT
    return CellStatus.POSSIBLY_STALE


def _check_to_string(check: BaseModel) -> str:
    """Convert a check to a string representation for hash input."""
    return check.model_dump_json()
