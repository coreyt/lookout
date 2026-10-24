# Changelog

All notable changes to Lookout are recorded here. Versions follow
[Semantic Versioning](https://semver.org/); before 1.0, minor and patch
releases may include breaking changes, which are called out below.

## [Unreleased]

### Added

- `lookout similarity --corpus DIR`: compares every Lookout rule against a
  reference rule corpus (e.g. a local clone of the Semgrep Registry) and flags
  close copies by pattern and message containment, with metavariable renaming
  normalized. Exits 1 when a rule is flagged.

## [0.1.2] — 2026-10-19

### Fixed

- **Snippets and baseline fingerprints contained no code.** Semgrep OSS reports
  the matched source as `"requires login"` unless you are logged in, so every
  snippet was that placeholder and baseline fingerprints fell back to
  occurrence order within a file. Lookout now reads the matched lines from the
  source file (up to 20 lines). Baselines written by 0.1.1 should be
  re-created with `lookout check --update-baseline`.
- **Language and framework detection missed parts of large repositories.**
  Detection now lists files with `git ls-files` (respecting `.gitignore`)
  instead of walking every directory, so ignored build or data output no
  longer exhausts the scan budget. Manifests are read wherever they appear in
  the project (e.g. `src/ts/tsconfig.json`, `src/python/pyproject.toml`), not
  only along the target's parent directories. Outside git, the walk also skips
  `target/` and `site-packages/`.
- **DI-001 flagged any call made in `__init__`**, such as
  `os.environ.get(...)`, `len(...)` and `np.asarray(...)`. It now flags only
  class instantiations (CapWords callee, excluding value types like `Path`),
  and reports the assignment line instead of the `class` line.

### Added

- Dependencies are read from `Cargo.toml` (`dependencies`, `dev-dependencies`,
  `build-dependencies`, `[workspace.dependencies]` and target-specific tables).

## [0.1.1] — 2026-10-19

### Breaking

- **Baseline format v2.** Findings are now fingerprinted by pattern, file,
  normalized code snippet and occurrence order instead of line number.
  Baselines written by 0.1.0 no longer match and are reported with a warning;
  re-create them with `lookout check --update-baseline`.
- **`lookout promote` no longer writes a placeholder check.** Without a real
  proposed check it exits with an error pointing at `--generate-check`. The
  old `["..."]` placeholder compiled into a rule that matched every file.
- **Promoted rules default to severity `warning`** until reviewed; pass
  `--severity` to override.
- `lookout check` now only runs variants for the languages and frameworks it
  detects in the project. Use `--all-variants` (or `detect_variants = false`)
  for the old behaviour.

### Added

- `paths: {include, exclude}` in pattern specs, at pattern level, per variant
  and on v1 rules, compiled into Semgrep `paths:` (including `raw` checks).
  New `exclude` config option applies to every rule.
- Project profile detection from manifests (`pyproject.toml`,
  `requirements.txt`, `package.json`, `tsconfig.json`, `go.mod`, …) and source
  files; skipped variants are listed in text output.
- `--max-tier` / `max_tier` to run only patterns at or above a tier;
  deprecated patterns are never run.
- Baselined findings are shown instead of hidden: `(baselined)` in text,
  `"baselined": true` in JSON and `baselineState` in SARIF.
- `promote --generate-check` verifies the generated rule with real Semgrep
  against the pattern's good/bad examples and retries the LLM with the failure
  report (`--max-attempts`, default 3; `--force` to save anyway).
- Example harness (`tests/integration/test_pattern_examples.py`) that runs every
  built-in pattern's good/bad examples through Semgrep.

### Fixed

- Semgrep scanned zero files when a target was outside a git work tree;
  Lookout now passes `--no-git-ignore` in that case.
- Built-in patterns corrected against their own examples: ARCH-001 (missed
  `httpx` calls), DI-001, LAYER-001 (TypeScript rule never matched) and
  REPO-001 (flagged code inside repository classes).
- Built-ins scoped to where they apply: LAYER-001 to controller/view/API
  paths, REPO-001 away from repository/DAO/migration code, ARCH-003 and DI-001
  away from tests and scripts.
- `lookout check` no longer runs discovery-only patterns.
- SARIF output and HTTP User-Agent now report the installed version instead of
  a hard-coded `0.1.0`.
- CI installs the dev tools and runs the test matrix on the intended Python
  versions.

## [0.1.0]

Initial release.
