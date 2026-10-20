"""Project profile detection and spec/variant selection.

Lookout ships variants for several languages and frameworks. Rather than run
every variant against every repository, ``detect_profile`` inspects the target
paths for manifests and source files, and ``select_specs`` keeps only the
variants that apply (plus any spec whose status/tier make it eligible).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from lookout.models import LanguageVariant, PatternSpecFile
from lookout.specs import SpecUnion

logger = logging.getLogger(__name__)

# Directories never worth walking for language detection.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".tox",
    "target",
    "site-packages",
}

# Source extensions → Semgrep language name.
_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".rs": "rust",
    ".cs": "csharp",
    ".php": "php",
    ".scala": "scala",
}

# Manifest files → languages they imply.
_MANIFEST_LANGUAGES = {
    "pyproject.toml": {"python"},
    "setup.py": {"python"},
    "setup.cfg": {"python"},
    "requirements.txt": {"python"},
    "Pipfile": {"python"},
    "package.json": {"javascript"},
    "tsconfig.json": {"typescript"},
    "go.mod": {"go"},
    "pom.xml": {"java"},
    "build.gradle": {"java"},
    "build.gradle.kts": {"kotlin"},
    "Gemfile": {"ruby"},
    "Cargo.toml": {"rust"},
    "composer.json": {"php"},
}

# Dependency names (normalized) that identify a framework variant name.
# Dependencies not listed here are matched to framework variants by their own
# normalized name (e.g. a dependency ``sqlalchemy`` matches variant ``sqlalchemy``).
_FRAMEWORK_ALIASES = {
    "@angular/core": "angular",
    "@nestjs/core": "nestjs",
    "@nestjs/common": "nestjs",
    "next": "nextjs",
    "nuxt": "nuxtjs",
    "django": "django",
    "djangorestframework": "django",
    "flask": "flask",
    "fastapi": "fastapi",
    "sqlalchemy": "sqlalchemy",
    "flask-sqlalchemy": "sqlalchemy",
    "spring-boot-starter": "spring-boot",
    "org.springframework.boot": "spring-boot",
}

_MAX_FILES_SCANNED = 20000
_MAX_MANIFESTS = 200


def normalize_name(name: str) -> str:
    """Normalize a dependency/framework name for comparison.

    Lowercases, strips version specifiers and extras, and unifies ``_``/``.``
    with ``-`` so that ``Flask_SQLAlchemy>=3`` and ``flask-sqlalchemy`` compare
    equal, as do ``nest.js`` and ``nestjs``. Scoped npm names keep their scope.
    """
    name = name.strip()
    if name.startswith("@"):
        base = "@" + re.split(r"[\s@<>=!~;\[]", name[1:], maxsplit=1)[0]
        return base.lower()
    base = re.split(r"[\s\[<>=!~;@]", name, maxsplit=1)[0]
    return base.lower().replace("_", "-").replace(".", "")


@dataclass
class ProjectProfile:
    """What was detected about the project under analysis."""

    languages: set[str] = field(default_factory=set)
    #: Known frameworks (see ``_FRAMEWORK_ALIASES``) found among the dependencies.
    frameworks: set[str] = field(default_factory=set)
    #: All normalized dependency names seen in manifests; framework variants whose
    #: name matches a dependency apply even when the framework is not a known alias.
    dependencies: set[str] = field(default_factory=set)
    #: False when nothing at all could be detected; selection then keeps every variant.
    detected: bool = False

    def describe(self) -> str:
        if not self.detected:
            return "Project profile: nothing detected (running all variants)"
        langs = ", ".join(sorted(self.languages)) or "none"
        fws = ", ".join(sorted(self.frameworks)) or "none"
        return f"Project profile: languages={langs}; frameworks={fws}"

    def has_framework(self, name: str) -> bool:
        norm = normalize_name(name)
        return norm in self.frameworks or norm in self.dependencies


def detect_profile(targets: list[Path]) -> ProjectProfile:
    """Detect languages and frameworks from manifests and source files under ``targets``.

    Files are enumerated with ``git ls-files`` when a target is inside a git work
    tree, so ignored build output and data directories are never walked; outside
    git, a directory walk that skips common vendor/build directories is used.
    Manifests are read wherever they appear among those files (e.g.
    ``src/ts/tsconfig.json``), and also in the targets' parent directories up to
    the current working directory, so ``--target src`` still finds a root
    ``pyproject.toml``.
    """
    profile = ProjectProfile()
    files = _list_files(targets)

    manifests: list[Path] = []
    for root in _manifest_roots(targets):
        manifests.extend(root / name for name in _MANIFEST_LANGUAGES if (root / name).is_file())
    manifests.extend(path for path in files if path.name in _MANIFEST_LANGUAGES)

    seen_manifests: set[Path] = set()
    for manifest in manifests:
        resolved = manifest.resolve()
        if resolved in seen_manifests or _in_skipped_dir(manifest):
            continue
        seen_manifests.add(resolved)
        if len(seen_manifests) > _MAX_MANIFESTS:
            logger.debug("More than %d manifests found; ignoring the rest", _MAX_MANIFESTS)
            break
        profile.languages.update(_MANIFEST_LANGUAGES[manifest.name])
        profile.dependencies.update(_read_dependencies(manifest))

    for path in files:
        lang = _EXTENSION_LANGUAGES.get(path.suffix)
        if lang:
            profile.languages.add(lang)

    for dep in profile.dependencies:
        if dep in _FRAMEWORK_ALIASES:
            profile.frameworks.add(_FRAMEWORK_ALIASES[dep])

    profile.detected = bool(profile.languages)
    return profile


def _manifest_roots(targets: list[Path]) -> list[Path]:
    cwd = Path.cwd().resolve()
    roots: list[Path] = []
    for target in targets:
        path = target.resolve()
        if path.is_file():
            path = path.parent
        # Walk up to cwd (inclusive) so manifests at the repo root are found.
        while True:
            if path not in roots:
                roots.append(path)
            if path == cwd or cwd not in path.parents:
                break
            path = path.parent
    if cwd not in roots:
        roots.append(cwd)
    return roots


def _list_files(targets: list[Path]) -> list[Path]:
    """List candidate files under ``targets``, honouring ``.gitignore`` when possible."""
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
            continue
        tracked = _git_list_files(target)
        if tracked is not None:
            files.extend(tracked)
        else:
            files.extend(_walk_files(target))
    return files


def _git_list_files(directory: Path) -> list[Path] | None:
    """Tracked and untracked-but-not-ignored files under ``directory``, or None outside git."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(directory),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [directory / name for name in names if name]


def _walk_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            files.append(Path(dirpath) / name)
            if len(files) >= _MAX_FILES_SCANNED:
                logger.debug("Stopped scanning %s after %d files", directory, _MAX_FILES_SCANNED)
                return files
    return files


def _in_skipped_dir(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts[:-1])


def _read_dependencies(manifest: Path) -> set[str]:
    """Extract normalized dependency names from a manifest file. Best effort."""
    try:
        if manifest.name == "pyproject.toml":
            return _pyproject_dependencies(manifest)
        if manifest.name == "requirements.txt":
            return _requirements_dependencies(manifest)
        if manifest.name == "package.json":
            return _package_json_dependencies(manifest)
        if manifest.name == "Pipfile":
            return _pipfile_dependencies(manifest)
        if manifest.name == "Cargo.toml":
            return _cargo_dependencies(manifest)
    except Exception as exc:  # noqa: BLE001 — detection must never abort a scan
        logger.debug("Could not read dependencies from %s: %s", manifest, exc)
    return set()


def _pyproject_dependencies(path: Path) -> set[str]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    deps: set[str] = set()
    project = data.get("project", {})
    for dep in project.get("dependencies", []):
        deps.add(normalize_name(dep))
    for group in project.get("optional-dependencies", {}).values():
        deps.update(normalize_name(d) for d in group)
    for group in data.get("dependency-groups", {}).values():
        deps.update(normalize_name(d) for d in group if isinstance(d, str))
    poetry = data.get("tool", {}).get("poetry", {})
    for key in ("dependencies", "dev-dependencies"):
        deps.update(normalize_name(d) for d in poetry.get(key, {}) if d.lower() != "python")
    for group in poetry.get("group", {}).values():
        deps.update(normalize_name(d) for d in group.get("dependencies", {}))
    return deps


def _requirements_dependencies(path: Path) -> set[str]:
    deps: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        deps.add(normalize_name(line))
    return deps


def _package_json_dependencies(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(normalize_name(d) for d in data.get(key, {}))
    return deps


def _pipfile_dependencies(path: Path) -> set[str]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    deps: set[str] = set()
    for key in ("packages", "dev-packages"):
        deps.update(normalize_name(d) for d in data.get(key, {}))
    return deps


def _cargo_dependencies(path: Path) -> set[str]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    deps: set[str] = set()
    sections = [data, data.get("workspace", {})]
    for section in sections:
        for key in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps.update(normalize_name(d) for d in section.get(key, {}))
    for target in data.get("target", {}).values():
        for key in ("dependencies", "dev-dependencies"):
            deps.update(normalize_name(d) for d in target.get(key, {}))
    return deps


@dataclass(frozen=True)
class SelectionReport:
    """What ``select_specs`` kept and why things were dropped."""

    selected: list[SpecUnion]
    skipped_deprecated: list[str]
    skipped_tier: list[str]
    skipped_language: list[str]
    dropped_variants: list[str]

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.skipped_deprecated:
            lines.append(f"Skipped deprecated: {', '.join(self.skipped_deprecated)}")
        if self.skipped_tier:
            lines.append(f"Skipped by tier: {', '.join(self.skipped_tier)}")
        if self.skipped_language:
            lines.append(f"Skipped (no matching language): {', '.join(self.skipped_language)}")
        if self.dropped_variants:
            lines.append(f"Variants not applicable: {', '.join(self.dropped_variants)}")
        return lines


def select_specs(
    specs: list[SpecUnion],
    profile: ProjectProfile | None,
    max_tier: int = 3,
) -> SelectionReport:
    """Filter specs and their variants to what applies to ``profile``.

    - ``deprecated`` specs are never run.
    - Specs whose ``tier`` exceeds ``max_tier`` are skipped (tier 1 = non-negotiable).
    - When ``profile`` is given and detected something, only variants for detected
      languages are kept, and framework variants only when that framework is a
      dependency. A profile of ``None`` (or one that detected nothing) keeps all.
    """
    selected: list[SpecUnion] = []
    skipped_deprecated: list[str] = []
    skipped_tier: list[str] = []
    skipped_language: list[str] = []
    dropped_variants: list[str] = []
    use_profile = profile is not None and profile.detected

    for spec in specs:
        if isinstance(spec, PatternSpecFile):
            pattern = spec.pattern
            if pattern.status == "deprecated":
                skipped_deprecated.append(pattern.id)
                continue
            if pattern.tier > max_tier:
                skipped_tier.append(pattern.id)
                continue
            if not use_profile or not pattern.variants:
                selected.append(spec)
                continue
            assert profile is not None
            kept = _select_variants(pattern.id, pattern.variants, profile, dropped_variants)
            if not kept:
                skipped_language.append(pattern.id)
                continue
            selected.append(
                spec.model_copy(update={"pattern": pattern.model_copy(update={"variants": kept})})
            )
        else:
            rule = spec.rule
            if rule.tier > max_tier:
                skipped_tier.append(rule.id)
                continue
            if (
                use_profile
                and profile is not None
                and rule.languages
                and not set(rule.languages) & profile.languages
            ):
                skipped_language.append(rule.id)
                continue
            selected.append(spec)

    return SelectionReport(
        selected=selected,
        skipped_deprecated=skipped_deprecated,
        skipped_tier=skipped_tier,
        skipped_language=skipped_language,
        dropped_variants=dropped_variants,
    )


def _select_variants(
    pattern_id: str,
    variants: list[LanguageVariant],
    profile: ProjectProfile,
    dropped: list[str],
) -> list[LanguageVariant]:
    kept: list[LanguageVariant] = []
    for variant in variants:
        if variant.language not in profile.languages:
            dropped.append(f"{pattern_id}/{variant.language}")
            continue
        frameworks = []
        for fw in variant.frameworks:
            if profile.has_framework(fw.name):
                frameworks.append(fw)
            else:
                dropped.append(f"{pattern_id}/{variant.language}/{fw.name}")
        if variant.generic is None and not frameworks:
            continue
        kept.append(variant.model_copy(update={"frameworks": frameworks}))
    return kept
