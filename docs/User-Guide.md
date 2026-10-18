# Lookout User Guide

## Overview

Lookout is a Governance-as-Code platform that enforces architectural decisions using Semgrep rules. It has two main interfaces:

- **`lookout` CLI** — enforce patterns, discover patterns, validate, and promote
- **`lookout-tui`** — terminal UI for browsing patterns and generating new ones via LLM

## Installation

```bash
# Core package
pip install -e .

# TUI package (from repo root)
cd tui && pip install -e ".[dev]"
```

## Part 1: Lookout CLI

### Initialize a Project

```bash
lookout init
```

This creates a `lookout.toml` (or adds `[tool.lookout]` to `pyproject.toml`) and optionally sets up a `.lookout-patterns` directory for user-defined patterns.

### Check Your Codebase

```bash
# Run all built-in and user patterns
lookout check

# Target specific directories
lookout check src/ tests/

# Output as SARIF for CI integration
lookout check --format sarif > results.sarif
```

Lookout ships with 10 built-in patterns that work without any configuration. User patterns in `.lookout-patterns/` override built-in patterns with the same ID.

### Discover Patterns

```bash
# Discover architectural patterns in your code
lookout discover

# Research patterns from web sources
lookout research --focus "circuit breaker"

# Research without LLM (keyword extraction only)
lookout research --focus "dependency injection" --no-llm

# Derive patterns from your project's architecture docs
lookout research --focus "architectural patterns" --docs .
```

### Validate and Promote

```bash
# Mark a discovered pattern as reviewed
lookout validate PATTERN-ID

# Promote a pattern that already carries a proposed_check
lookout promote PATTERN-ID

# Have an LLM write the Semgrep check, verified against the pattern's examples
lookout promote PATTERN-ID --generate-check
```

#### Generated checks are verified with Semgrep before they are saved

With `--generate-check`, the LLM's check is not trusted on the strength of
schema validation alone. Lookout compiles it, writes the pattern's good and bad
examples to a temporary directory, and runs the real `semgrep` binary over them.
The check passes only when **every bad example produces a match and no good
example does**; Semgrep rule errors (unparseable patterns, invalid raw YAML) fail
it too.

When verification fails, the rejected check and the report (unmatched bad
examples, matched good examples, rule errors) are appended to the next prompt
and the LLM is asked again. This repeats up to `--max-attempts` times (default
3, also `promote_max_attempts` in config). If the last attempt still fails,
`promote` prints the report, exits non-zero and writes nothing. Pass `--force`
to save the unverified check anyway and fix it by hand.

```bash
lookout promote PATTERN-ID --generate-check --max-attempts 5
lookout promote PATTERN-ID --generate-check --force   # save even if unverified
```

Patterns whose examples use a language Lookout cannot map to a file extension
are skipped in verification and listed in the report. A pattern with no examples
at all passes vacuously; add examples to get a meaningful check.

#### No placeholder rules

A promoted spec is an enforcement rule, so `promote` refuses to invent one.
Without `--generate-check`, the validated pattern must already carry a
`proposed_check`; otherwise the command exits with an error pointing at
`--generate-check`. (Earlier versions wrote a `pattern_discovery` check with the
wildcard pattern `"..."`, which compiled into a rule matching every file.)

#### Promoted rules start as warnings

Regardless of the severity recorded during discovery, a promoted rule is written
with `severity: warning` so an unreviewed, machine-written rule never fails a
build on its own. The discovered severity still drives the pattern's tier.
Once you have reviewed the rule, raise it in the YAML or pass
`--severity error` at promotion time.

### Configuration

Lookout reads configuration from `lookout.toml` or `pyproject.toml`:

```toml
[tool.lookout]
rules_dir = ".lookout-rules"
patterns_dir = ".lookout-patterns"
target = ["src"]
format = "text"
```

See [Configuration](configuration.md) for the full reference.

---

## Part 2: Lookout TUI

The TUI provides a visual interface for browsing patterns and generating new pattern specifications using a three-stage LLM pipeline.

### Launch

```bash
lookout-tui
```

Or via module invocation:

```bash
python -m lookout_tui
```

### Global Key Bindings

| Key | Action |
|-----|--------|
| `b` | Switch to Pattern Browser screen |
| `g` | Switch to Generation Pipeline screen |
| `q` | Quit |

### Pattern Browser

The browser screen shows all built-in and user patterns in a filterable table with a detail panel.

```
+--------------------------------------------------+
| Lookout > Pattern Browser          10 patterns    |
+--------------------------------------------------+
| Filter: [________]                                |
+----------------------------+---------------------+
| ID       | Title     |Tier | Detail Panel         |
|----------|-----------|-----|                      |
| ARCH-001 | HTTP Res..| 1   | ARCH-001             |
| ARCH-003 | Use Logg..| 1   | HTTP Calls Require   |
| DI-001   | Dep Inj...| 2   | Resilience Wrappers  |
| LAYER-001| Layer Bo..| 2   |                      |
| REPO-001 | Repo Pat..| 2   | Category: resilience |
| ...      |           |     | Severity: error      |
|          |           |     | Languages: python    |
+--------------------------------------------------+
| [/]filter [r]efresh [1-3]tier [Esc]back          |
+--------------------------------------------------+
```

**Browser key bindings:**

| Key | Action |
|-----|--------|
| `/` | Focus the filter input |
| `r` | Rebuild the pattern index |
| `1` | Show Tier 1 patterns only |
| `2` | Show Tier 2 patterns only |
| `3` | Show Tier 3 patterns only |
| `0` | Show all tiers |
| `j/k` | Navigate rows |
| `Esc` | Go back |

The filter searches across pattern ID, title, category, and tags. The detail panel updates as you navigate rows, showing the full metadata for the selected pattern.

### Generation Pipeline

The generation screen runs a three-stage LLM pipeline to produce new pattern specifications. It connects to an Airlock proxy (OpenAI-compatible endpoint) that forwards requests to a backing LLM.

```
+--------------------------------------------------+
| Lookout > Generation Pipeline                     |
+--------------------+-----------------------------+
| Job Queue          | Pipeline: CQRS-001          |
|                    |                             |
| CQRS-001 [stg 1]  | * top_level: approved       |
| EVENT-001 [pend]   | o language_generic: running  |
|                    | o language_framework: pending|
|                    |-----------------------------|
|                    | Stage: language_generic      |
|                    | Status: awaiting_review      |
|                    | -------------------------    |
|                    |                             |
|                    | [LLM output here...]        |
|                    |                             |
+--------------------------------------------------+
| [n]ew [a]pprove [r]eject [t]retry [w]rite [Esc]  |
+--------------------------------------------------+
```

**Generation key bindings:**

| Key | Action |
|-----|--------|
| `n` | Create a new generation job |
| `t` | Run/retry the current stage |
| `a` | Approve the current stage output |
| `r` | Reject the current stage output |
| `w` | Write compiled output to the diff view |
| `Esc` | Go back |

### Generating a New Pattern

This is the core workflow for creating new pattern specifications.

#### Step 1: Create a Job

Press `n` to create a new generation job. The job starts with a placeholder ID (`NEW-001`) and `python` as the default target language. The pipeline creates stages based on the job's target languages and frameworks.

#### Step 2: Run Stage 1 — Top-Level Specification

Press `t` to run the first stage. The system sends two prompts to the LLM:

- **System prompt** (`docs/prompts/system-prompt-pattern-research.md`): establishes the LLM as a static analysis pattern expert with strict quality standards for Semgrep rules, code examples, and references
- **User prompt** (`docs/prompts/user-prompt-top-level.md`): asks for the language-agnostic pattern specification

The LLM produces Section 1 of the pattern spec:
- Identity: ID, title, category, subcategory, tags, tier, severity, confidence
- Description: what the pattern enforces and why
- Detection strategy: what structural signal makes static detection possible
- False positive/negative analysis
- Reference URLs
- Target language relevance table

The output appears in the diff view for review.

#### Step 3: Review and Approve

Read the LLM output in the diff view. You have three options:

- **`a` (Approve)**: Lock in this stage. The approved text becomes input for the next stage. Stage 2 auto-starts immediately.
- **`r` (Reject)**: Mark the output as rejected. Press `t` to retry — the LLM is called again from scratch.
- **`t` (Retry)**: Re-run any rejected or errored stage.

#### Step 4: Stage 2 — Language-Generic Variant (auto-starts)

After approving Stage 1, Stage 2 runs automatically. The prompt (`docs/prompts/user-prompt-language-generic.md`) receives the full approved Stage 1 output as context, plus the target language. It asks for:

- A concrete Semgrep rule using the target language's actual syntax
- Check type selection (forbidden, must_wrap, required_with, raw, or pattern_discovery)
- Good example code that follows the pattern (must NOT match the rule)
- Bad example code that violates the pattern (MUST match the rule)
- Language-specific notes
- Rule ID convention: `{pattern-id}-{language}-impl`

Review and approve/reject as before.

#### Step 5: Stage 3 — Language-Framework Variant (if configured)

If the job has target frameworks, Stage 3 runs after Stage 2 approval. The prompt (`docs/prompts/user-prompt-language-framework.md`) receives both approved outputs and asks the LLM to either:

- **Justify a framework-specific variant** — explain why the generic pattern is insufficient for this framework, then provide a more specific Semgrep rule
- **Declare SKIP** — if the generic pattern already covers the framework adequately

Framework variants should only exist when the framework's base classes, decorators, or conventions genuinely change the detection approach. Rule ID convention: `{pattern-id}-{language}-{framework}-impl`.

#### Step 6: Write Output

Press `w` to assemble all approved stage outputs into a single document shown in the diff view. This is the research artifact — the structured output from all three stages combined as Markdown.

### Stage State Machine

Stages follow this lifecycle:

```
PENDING --[t]--> RUNNING --[success]--> AWAITING_REVIEW --[a]--> APPROVED
                    |                         |
                    v                         v
                  ERROR                   REJECTED
                    |                         |
                    +----------[t]------------+--> PENDING (reset)
```

- **Auto-advance**: Approving a stage automatically starts the next pending stage
- **Prerequisites**: A language stage requires an approved top-level. A framework stage requires both an approved top-level and an approved language stage for the same language.
- **Retry**: Press `t` to retry any rejected or errored stage. The pipeline finds the first failed stage and re-runs it.

### Airlock Configuration

The TUI connects to an Airlock LLM proxy — an OpenAI-compatible endpoint that forwards to a backing model. Default settings (configured via `TUIConfig`):

| Setting | Default | Description |
|---------|---------|-------------|
| `airlock_endpoint` | `http://localhost:4000/v1/chat/completions` | Airlock proxy URL |
| `airlock_model` | `gemini-3.1-pro` | Model name sent in requests |
| `airlock_api_key` | (empty) | Bearer token for auth |
| `airlock_temperature` | `0.3` | LLM temperature |
| `airlock_max_tokens` | `4096` | Max response tokens |
| `airlock_timeout` | `120.0` | HTTP timeout in seconds |
| `prompts_dir` | `docs/prompts` | Directory containing prompt templates |
| `index_path` | `.lookout-index.json` | Pattern index output path |
| `patterns_dir` | `.lookout-patterns` | User patterns directory |

The client retries automatically on HTTP 429, 500, 502, and 503 with exponential backoff (up to 3 attempts).

---

## Pattern Schema Versions

Lookout supports two YAML schema versions:

### v1 — Flat Rule Spec

Single-language, flat structure. Used by the original enforcement rules.

```yaml
schema_version: 1
rule:
  id: ARCH-001
  title: HTTP Calls Require Resilience Wrappers
  description: ...
  rationale: ...
  tier: 1
  category: resilience
  severity: error
  languages: [python]
  check:
    type: must_wrap
    target: [requests.get, requests.post]
    wrapper: [circuit_breaker, with_retry]
  reporting:
    default_message: HTTP call missing resilience wrapper
    confidence: high
  examples:
    good: [{ language: python, code: "..." }]
    bad: [{ language: python, code: "..." }]
  references: [https://...]
```

### v2 — Hierarchical Pattern Spec

Multi-language, with per-language and per-framework variants. This is the target output of the generation pipeline.

```yaml
schema_version: 2
pattern:
  id: DI-001
  title: Dependency Injection via Constructor
  description: ...
  rationale: ...
  version: "1.0.0"
  tier: 2
  category: architecture
  subcategory: dependency-injection
  severity: warning
  tags: [solid, testability]
  reporting:
    default_message: Use constructor injection for dependencies
    confidence: medium
  references: [https://...]
  variants:
    - language: python
      version_constraint: ">=3.10"
      generic:
        check:
          type: raw
          config: |
            rules:
              - id: di-001-python-impl
                pattern: ...
                languages: [python]
                severity: WARNING
                message: ...
          engine: semgrep
        examples:
          good: [{ language: python, code: "..." }]
          bad: [{ language: python, code: "..." }]
      frameworks:
        - name: django
          check:
            type: raw
            config: ...
            engine: semgrep
          examples:
            good: [{ language: python, code: "..." }]
            bad: [{ language: python, code: "..." }]
    - language: typescript
      generic:
        check:
          type: pattern_discovery
          patterns: ["class $C { constructor($DEPS) { ... } }"]
        examples:
          good: []
          bad: []
```

The three-stage generation pipeline maps directly to this structure:
- **Stage 1** produces the `pattern:` metadata fields (id, title, description, tier, detection strategy, etc.)
- **Stage 2** produces a `variants[].generic` entry for each language
- **Stage 3** produces `variants[].frameworks[]` entries where justified

### Check Types

Patterns use one of five check types, from simplest to most powerful:

| Type | Use Case | Example |
|------|----------|---------|
| `forbidden` | Flag a single pattern that should never appear | `print(...)`, `eval($X)` |
| `must_wrap` | Call X must appear inside wrapper Y | HTTP calls inside circuit breaker |
| `required_with` | If pattern A exists, pattern B must also exist | `@route` requires `@auth` |
| `raw` | Complex multi-pattern Semgrep rules | `pattern-either`, `pattern-not-inside`, etc. |
| `pattern_discovery` | Find instances without judging (informational) | Architectural inventory |

---

## Appendix: Project Layout

```
src/lookout/              # Core enforcement engine
  patterns/               # 10 built-in pattern YAML files
  models.py               # PatternSpecFile, RuleSpecFile, RuleCheck types
  specs.py                # load_specs() — YAML → Pydantic models
  rules_loader.py         # ESLint-style hierarchical loading
  runner.py               # run_lookout() — main enforcement pipeline
  semgrep.py              # Semgrep rule compilation
  sarif.py                # SARIF output format
  config.py               # LookoutConfig
  cli.py                  # Click CLI commands
  rule_generator.py       # LLM-based Semgrep rule generation
  discovery/              # AI-powered pattern discovery agents

tui/                      # Lookout TUI package (separate)
  src/lookout_tui/
    app.py                # Textual App with screen registry
    cli.py                # Click entry point (lookout-tui command)
    config.py             # TUIConfig (Airlock endpoint, model, etc.)
    clients/airlock.py    # OpenAI-compatible async HTTP client
    prompts/loader.py     # Prompt template loading and rendering
    index/builder.py      # Pattern index builder (YAML → JSON)
    pipeline/generator.py # Three-stage generation orchestrator
    pipeline/models.py    # GenerationJob, StageResult, StageStatus
    screens/browser.py    # Pattern Browser screen
    screens/generation.py # Generation Pipeline screen
    widgets/              # PatternTable, PatternDetail, JobQueue, etc.

docs/prompts/             # LLM prompt templates
  system-prompt-pattern-research.md
  user-prompt-top-level.md
  user-prompt-language-generic.md
  user-prompt-language-framework.md
```
