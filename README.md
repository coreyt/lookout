# Lookout

**Lookout is not ESLint** - it's a next-generation code pattern enforcement and discovery tool.

Lookout can be considered a "Architectural Compliance Engine" or even better, a "Governance-as-Code Platform (GaP) tool."
 * It enforces high-level architectural decisions (e.g., "All HTTP calls must use a circuit breaker") rather than low-level code style. It operates similarly to tools like ArchUnit or Open Policy Agent (OPA), but for code structure.
 * Key Differentiator: The "Discovery" aspect. Most tools only enforce known rules. Lookout proposes to learn what rules should exist, making it a "Knowledge-Driven Governance Tool".

## What is Lookout?

Lookout combines:
- **Pattern Enforcement**: Compile high-level coding guidelines into Semgrep rules and enforce them in your codebase
- **Pattern Discovery**: Use AI-powered research agents to discover best practices from trusted sources and generate enforcement rules automatically
- **Pattern Registry TUI**: A terminal UI for browsing, managing, and generating pattern specifications with LLM-powered Semgrep rule generation

## Features

### Built-In Patterns (ESLint-style)
- **10 curated architectural patterns** shipped with the package
- Works out-of-box without setup
- Covers resilience, code quality, dependency injection, layered architecture, and repository patterns
- Customizable: override or extend with local patterns

### Core Enforcement Engine
- Load pattern specifications from YAML files (v2 hierarchical schema with language variants and framework-specific checks)
- Compile specifications into Semgrep rules
- Run pattern matching across your codebase
- **Multiple Output Formats**: Text, JSON, and SARIF for CI/CD integration

### AI-Powered Discovery Pipeline
- **Web Research**: `lookout research` searches trusted sources (Martin Fowler, Refactoring Guru, cloud provider docs) and extracts coding patterns via LLM or keyword extraction
- **Documentation Analysis**: `lookout research --docs .` reads your project's own `ARCHITECTURE.md`, `DESIGN.md`, and ADR files to extract the architectural patterns your team has already documented
- **Validation**: `lookout validate` marks discovered patterns as reviewed and assigns enforcement tiers
- **Promotion**: `lookout promote` transpiles validated patterns into enforcement rules; `--generate-check` has an LLM write the Semgrep check and verifies it against the pattern's examples with real Semgrep before saving

### Lookout TUI — Pattern Registry and Generation Pipeline

A separate `lookout-tui` package provides a Textual-based terminal interface:

- **Dashboard**: Home screen with pattern counts, language coverage, and LLM status
- **Pattern Browser** (`b`): Browse all built-in and user patterns with filtering, tier grouping, and detail inspection
- **Pattern Registry** (`e`): Three-level tree view (pattern → language → framework) showing all built-in and user patterns with status tracking. Create, deprecate, approve, and generate checks inline
- **Generation Pipeline** (`g`): Create new pattern specs through a three-stage LLM workflow:
  1. **Top-Level Specification** — language-agnostic pattern definition (what, why, detection strategy)
  2. **Language-Generic Variant** — concrete Semgrep rule for a specific language
  3. **Language-Framework Variant** — framework-specific refinement (only where justified)
- **Config Editor** (`c`): Set LLM provider, model, temperature, and timeout
- **Batch Generation** (`t`): Bulk-generate Semgrep checks across the pattern × language × framework grid

Each generation stage feeds its approved output into the next stage's prompt. See the [User Guide](docs/User-Guide.md) for details.

### Governance Workflow
- **Interactive Setup**: `lookout init` command for easy onboarding
- **Pattern Discovery**: Use AI-powered agents to find patterns in your code
- **Architecture Conformance**: Verify that implementation matches documented architecture by deriving rules from your own design docs

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies (both core and TUI)
uv sync

# Core only (no TUI)
uv sync --package lookout
```

## Quick Start

```bash
# Initialize Lookout for your project (interactive setup)
uv run lookout init

# Or just start checking with built-in rules
uv run lookout check

# Discover patterns in your codebase
uv run lookout discover

# Research patterns from trusted web sources (keyword-only, no API key needed)
uv run lookout research --focus "dependency injection" --no-llm

# Derive patterns from your project's own architecture docs
uv run lookout research --focus "architectural patterns" --docs . --no-llm

# Validate and promote a discovered pattern to an enforcement rule
uv run lookout validate ARCH-DI-001
uv run lookout promote ARCH-DI-001 --generate-check

# Launch the TUI for browsing and generating patterns
uv run lookout-tui
```

Lookout works immediately without configuration - it ships with 10 built-in architectural patterns. The `init` command helps you customize settings and optionally add project-specific rules.

## Development

```bash
# Install all dependencies including dev tools
uv sync --dev

# Run tests
uv run pytest                        # core library
cd tui && uv run pytest              # TUI

# Lint and type check
uv run ruff format --check src/ tests/
uv run ruff check src/ tests/
uv run mypy src/lookout/
```

## Project Structure

```
src/lookout/          Core library — models, rules loader, semgrep compiler, CLI
  patterns/           10 built-in pattern specs (YAML)
  batch/              Batch generation orchestrator and providers
tui/                  Lookout TUI (separate package, workspace member)
  src/lookout_tui/    Textual screens, widgets, LLM pipeline
tests/                Core library tests (571 tests)
tui/tests/            TUI tests (123 tests)
docs/                 User guide, pattern catalog, prompt templates
```

## Project Status

Lookout is in active development. It was extracted from the [ling](https://github.com/coreyt/ling) project to become a standalone tool.

## Documentation

- **[User Guide](docs/User-Guide.md)** - Complete guide to using Lookout CLI and TUI
- **[Configuration](docs/configuration.md)** - How to configure Lookout via `pyproject.toml` or `lookout.toml`
- **[Pattern Catalog](docs/pattern-catalog.md)** - All built-in and planned patterns
- **`docs/prompts/`** - Prompt templates used by the generation pipeline
- **`docs/research/`** - Design docs, pattern discovery methodology, validation results
- **`docs/specs/`** - Rule specifications, pattern library, and examples

## License

This project is licensed under the **Elastic License v2 (ELv2)**.

We chose this license because we believe in building in the open, but we also believe in protecting the hard work our team has put into this project from being "strip-mined" by large cloud providers.

#### What this means for you:

* **Individuals & Companies:** You can use, modify, and redistribute the code for free for almost any internal or commercial use.
* **Modifications:** You are free to modify the code to suit your or your customer's needs without being forced to open-source your proprietary changes (no "viral" copyleft).
* **Contributions:** We welcome pull requests! (Note: contributions may require a simple CLA).

#### What you cannot do:

* **Managed Services:** You cannot provide this software as a managed service (SaaS) to third parties. If you are a hyperscaler or service provider looking to sell "Lookout-as-a-Service," you must contact us for a commercial license.
* **No Circumvention:** You cannot bypass any license key or security features built into the software.
* **No Sublicensing:** You cannot sell the license to others or claim the code as your own.

> **In short:** If you aren't trying to build a competing AWS-style managed service using our code, you are likely good to go.
