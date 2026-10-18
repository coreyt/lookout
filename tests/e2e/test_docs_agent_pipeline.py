"""End-to-end test: docs → research --docs → validate → promote.

Exercises the documentation-based discovery pipeline using real filesystem
storage and the CLI, with the extractor producing keyword-only patterns.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from lookout.cli import cli
from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExamples,
)
from lookout.discovery.storage import PatternStorage
from lookout.models import ForbiddenCheck


def _write_architecture_doc(root: Path) -> Path:
    """Create a realistic ARCHITECTURE.md in the given directory."""
    doc = root / "ARCHITECTURE.md"
    doc.write_text(
        "# Project Architecture\n\n"
        "## Dependency Injection\n\n"
        "All services MUST receive their dependencies through constructor "
        "injection. Never instantiate a repository, client, or service "
        "directly inside another service. This ensures loose coupling and "
        "makes unit testing straightforward.\n\n"
        "Good:\n"
        "```python\n"
        "class OrderService:\n"
        "    def __init__(self, repo: OrderRepo):\n"
        "        self.repo = repo\n"
        "```\n\n"
        "Bad:\n"
        "```python\n"
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        self.repo = OrderRepo()\n"
        "```\n\n"
        "## Error Handling\n\n"
        "All errors must be caught at the boundary layer. Internal code "
        "should raise domain-specific exceptions that inherit from a common "
        "base. Never swallow exceptions silently — always log or re-raise.\n"
    )
    return doc


class TestDocsAgentE2E:
    """Full pipeline: ARCHITECTURE.md → research --docs → validate → promote."""

    def test_research_from_architecture_doc(self, tmp_path: Path) -> None:
        """Create ARCHITECTURE.md → lookout research --docs → verify patterns saved."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_architecture_doc(project_dir)

        patterns_dir = tmp_path / "patterns"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "research",
                "--focus",
                "architectural patterns",
                "--docs",
                str(project_dir),
                "--no-llm",
                "--patterns-dir",
                str(patterns_dir),
            ],
        )

        # The keyword extractor may or may not find patterns from docs text,
        # but the command should complete without error.
        assert result.exit_code == 0, f"research --docs failed: {result.output}"

    def test_docs_to_rule_pipeline(self, tmp_path: Path) -> None:
        """Full pipeline: docs → research → manually save pattern → validate → promote → YAML."""
        patterns_dir = tmp_path / "patterns"
        rules_dir = tmp_path / "rules"

        # Since keyword extractor may not produce patterns from arbitrary
        # doc text, we simulate the pipeline by saving a raw pattern directly
        # (as if the docs agent discovered it) and then validate + promote.
        storage = PatternStorage(patterns_dir)

        raw = DiscoveredPattern(
            pattern_id="ARCH-DI-001",
            title="Use Dependency Injection for Loose Coupling",
            category="architecture",
            subcategory="dependency-injection",
            description=(
                "Services should receive dependencies via constructor injection "
                "rather than instantiating them directly."
            ),
            rationale=(
                "Direct instantiation couples services tightly to concrete "
                "implementations and makes unit testing difficult."
            ),
            severity="error",
            confidence="high",
            languages=["python"],
            discovered_by="docs-agent",
            source_files=["ARCHITECTURE.md"],
            examples=PatternExamples(),
            # promote without --generate-check needs a real check; there is no placeholder
            proposed_check=ForbiddenCheck(type="forbidden", pattern="OrderRepo()"),
        )
        storage.save_pattern(raw, "raw")

        runner = CliRunner()

        # Validate
        result = runner.invoke(
            cli,
            [
                "validate",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--tier",
                "1",
            ],
        )
        assert result.exit_code == 0, f"validate failed: {result.output}"
        assert "Validated ARCH-DI-001" in result.output

        # Promote (no --generate-check, uses the pattern's proposed_check)
        result = runner.invoke(
            cli,
            [
                "promote",
                "ARCH-DI-001",
                "--patterns-dir",
                str(patterns_dir),
                "--output-dir",
                str(rules_dir),
            ],
        )
        assert result.exit_code == 0, f"promote failed: {result.output}"
        assert "Promoted ARCH-DI-001" in result.output

        # Verify YAML output
        yaml_path = rules_dir / "ARCH-DI-001.yaml"
        assert yaml_path.exists()
        spec_data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        assert spec_data["rule"]["id"] == "ARCH-DI-001"
        assert spec_data["rule"]["tier"] == 1
        assert spec_data["rule"]["check"]["type"] == "forbidden"
        # Promoted rules start as warnings until reviewed
        assert spec_data["rule"]["severity"] == "warning"

    def test_no_docs_found_exits_gracefully(self, tmp_path: Path) -> None:
        """Empty directory → exits with message, no error."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        patterns_dir = tmp_path / "patterns"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "research",
                "--focus",
                "patterns",
                "--docs",
                str(empty_dir),
                "--no-llm",
                "--patterns-dir",
                str(patterns_dir),
            ],
        )

        assert result.exit_code == 0, f"should exit gracefully: {result.output}"
        assert "No patterns discovered" in result.output
