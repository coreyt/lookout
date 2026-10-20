# Configuration

Lookout supports modern configuration via `pyproject.toml` (recommended for Python projects) or a standalone `lookout.toml`.

## Quick Setup

The easiest way to configure Lookout is with the interactive setup:

```bash
lookout init
```

This will:
- Auto-detect your project type (Python, TypeScript, etc.)
- Create configuration file (`lookout.toml` or add to `pyproject.toml`)
- Set up `.lookout-rules` directory (optional)
- Optionally copy built-in rules for customization

## Built-In Rules

Lookout ships with 10 curated architectural patterns that work out-of-box:

**Enforcement Rules:**
- `ARCH-001`: HTTP calls require resilience wrappers
- `ARCH-003`: Use logging instead of print statements (skips `tests/`, `scripts/`, `examples/`, `docs/`)
- `DI-001`: Dependency injection via constructor
- `LAYER-001`: Layered architecture import boundaries (only scans controller/view/handler/API paths)
- `REPO-001`: Repository pattern for data access (skips repository/DAO/migration paths)

**Pattern Discovery Rules (PATTERN-DISC):**
- `PATTERN-DISC-006`: Adapter pattern
- `PATTERN-DISC-010`: Pipeline pattern
- `PATTERN-DISC-011`: Dependency Injection
- `PATTERN-DISC-012`: Context managers
- `PATTERN-DISC-015`: Exception hierarchy

These rules are always available and don't need to be in your project directory.

## Configuration Resolution

Lookout resolves configuration in the following order (highest priority first):
1. **CLI Flags**: Arguments passed directly to the command (e.g., `--format json`).
2. **`lookout.toml`**: A standalone TOML file in the project root.
3. **`pyproject.toml`**: The standard Python configuration file (under `[tool.lookout]`).

## Options Reference

| Option | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `rules_dir` | Path | `".lookout-rules"` | Directory containing user-defined Rule Specs (`.yaml`). Built-in rules are always loaded. |
| `patterns_dir` | Path | `".lookout-patterns"` | Directory for storing discovered/validated patterns. |
| `target` | List[Path] | `["."]` | Default paths to analyze if none provided via CLI. |
| `format` | String | `"text"` | Output format: `"text"`, `"json"`, or `"sarif"`. |
| `fail_on_rule_error` | Bool | `false` | Exit non-zero when a rule fails to compile or run. |
| `exclude` | List[String] | `[]` | Path globs excluded from **every** rule (Semgrep `paths.exclude` semantics), e.g. `["tests/**", "vendor/**"]`. |
| `max_tier` | Int (1–3) | `3` | Only run patterns of this tier or stricter. Tier 1 = non-negotiable, 2 = recommended, 3 = advisory. CLI: `--max-tier`. |
| `detect_variants` | Bool | `true` | Detect the project's languages and frameworks and run only matching variants. CLI: `--all-variants` disables it. |
| `promote_max_attempts` | Int (≥1) | `3` | Generate→verify rounds `lookout promote --generate-check` makes before giving up. CLI: `--max-attempts`. |

## Examples

### `pyproject.toml` (Recommended for Python projects)

```toml
[tool.lookout]
rules_dir = ".lookout-rules"
target = ["src", "tests"]
format = "text"
```

### `lookout.toml` (Standalone configuration)

```toml
rules_dir = ".lookout-rules"
target = ["."]
format = "text"
```

### Minimal Configuration

You can omit `rules_dir` entirely to use only built-in rules:

```toml
[tool.lookout]
target = ["src"]
format = "text"
```

## Which Rules Run

Before scanning, `lookout check` decides which specs and variants apply:

1. **Status**: patterns with `status: deprecated` never run.
2. **Tier**: patterns with `tier` above `max_tier` are skipped.
3. **Project profile**: Lookout inspects the targets (and the directories up to the
   current one) for manifests — `pyproject.toml`, `requirements.txt`, `package.json`,
   `tsconfig.json`, `go.mod`, … — and for source files, to determine the languages in
   use and the dependencies declared. A language variant runs only if that language was
   detected; a framework variant (`django`, `sqlalchemy`, `angular`, …) runs only if that
   framework is a declared dependency. If nothing can be detected, every variant runs.

In text mode the detected profile and any skipped variants are printed to stderr:

```
Project profile: languages=python; frameworks=sqlalchemy
Variants not applicable: DI-001/python/django, DI-001/typescript, LAYER-001/typescript
```

Use `--all-variants` (or `detect_variants = false`) to run everything regardless.

## Path Scoping in Specs

Any spec can restrict where its checks apply with a `paths` block. Globs use Semgrep's
`paths:` semantics and are matched against paths relative to the scan root.

```yaml
pattern:
  id: "LAYER-001"
  # ...
  paths:
    include:
      - "**/controllers/**"
      - "**/api/**"
    exclude:
      - "tests/**"
  variants:
    - language: python
      generic:
        paths:                 # optional: narrows the pattern-level scope for this variant
          exclude:
            - "**/legacy/**"
        check: ...
```

- Pattern-level `paths` apply to every variant (including `raw` checks).
- A variant-level `paths` block **replaces** `include` and **adds to** `exclude`.
- The global `exclude` config option is appended to every rule.
- v1 (`schema_version: 1`) rules accept the same block under `rule.paths`.

## Baseline

`lookout check --update-baseline` records the current findings in `.lookout-baseline.json`;
later runs exit 0 unless a finding not in the baseline appears. Findings are fingerprinted by
pattern, file, normalized code snippet and occurrence order — **not** by line number — so
editing unrelated code above a known finding does not make it "new" again. Baselined findings
are still listed (marked `(baselined)` in text output, `"baselined": true` in JSON, and
`baselineState: unchanged` in SARIF).

Baselines written by versions before the content-based format (`"version": 1`) no longer match;
re-create them with `--update-baseline`.

## Rule Loading

Lookout uses ESLint-style hierarchical rule loading:

1. **Built-in rules** are always loaded from the package
2. **User rules** are loaded from `rules_dir` (if it exists)
3. If a user rule has the same ID as a built-in rule, the user rule overrides it

This means:
- Lookout works immediately without any local rules
- You can customize or override built-in rules by creating rules with matching IDs
- You can add organization-specific rules in `.lookout-rules/`

## Migration from Old Default

**In pre-0.1.0 builds** (when the project was still named `sine`), the default `rules_dir` was `"rules"`. If you have a project set up with one of those builds:

- **If you have a config file**: No changes needed (explicit `rules_dir` preserved)
- **If you don't have a config**: Run `lookout init` or create `lookout.toml` with `rules_dir = "rules"`
