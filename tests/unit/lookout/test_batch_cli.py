"""Tests for batch CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from lookout.batch.models import BatchJob, BatchStatus
from lookout.cli import cli
from lookout.models import (
    PatternSpec,
    PatternSpecFile,
    RuleReporting,
)
from lookout.registry import save_pattern


def _make_spec(pattern_id: str = "DI-001") -> PatternSpecFile:
    return PatternSpecFile(
        schema_version=2,
        pattern=PatternSpec(
            id=pattern_id,
            title="Dependency Injection",
            description="Use constructor injection",
            rationale="Makes dependencies explicit",
            tier=2,
            category="architecture",
            severity="warning",
            reporting=RuleReporting(
                default_message="Use constructor injection",
                confidence="medium",
            ),
        ),
    )


class TestBatchSubmit:
    def test_dry_run(self, tmp_path: Path) -> None:
        runner = CliRunner()
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        save_pattern(_make_spec(), rules_dir)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "submit",
                    "--languages",
                    "python",
                    "--all-missing",
                    "--dry-run",
                    "--rules-dir",
                    str(rules_dir),
                ],
                catch_exceptions=False,
            )
            assert result.exit_code == 0
            assert "1 cell" in result.output or "1 missing" in result.output.lower()

    def test_no_cells_selected(self, tmp_path: Path) -> None:
        runner = CliRunner()
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        # No patterns saved — should find nothing

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "submit",
                    "--languages",
                    "python",
                    "--all-missing",
                    "--rules-dir",
                    str(rules_dir),
                ],
            )
            assert result.exit_code == 0
            assert "no cells" in result.output.lower()


class TestBatchList:
    def test_empty_jobs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "list",
                    "--jobs-dir",
                    str(jobs_dir),
                ],
            )
            assert result.exit_code == 0
            assert "no batch jobs" in result.output.lower()

    def test_list_with_jobs(self, tmp_path: Path) -> None:
        runner = CliRunner()
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        job = BatchJob(
            job_id="batch_123",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            status=BatchStatus.COMPLETED,
            requests=[],
        )
        with (jobs_dir / "batch_123.json").open("w") as f:
            json.dump(job.to_dict(), f)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "list",
                    "--jobs-dir",
                    str(jobs_dir),
                ],
            )
            assert result.exit_code == 0
            assert "batch_123" in result.output


class TestBatchStatus:
    def test_status_with_saved_job(self, tmp_path: Path) -> None:
        runner = CliRunner()
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        job = BatchJob(
            job_id="batch_123",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            status=BatchStatus.PROCESSING,
            requests=[],
            request_counts={"succeeded": 3, "processing": 2},
        )
        with (jobs_dir / "batch_123.json").open("w") as f:
            json.dump(job.to_dict(), f)

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "status",
                    "batch_123",
                    "--jobs-dir",
                    str(jobs_dir),
                ],
            )
            assert result.exit_code == 0
            assert "batch_123" in result.output
            assert "processing" in result.output.lower()

    def test_status_not_found(self, tmp_path: Path) -> None:
        runner = CliRunner()
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()

        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(
                cli,
                [
                    "batch",
                    "status",
                    "nonexistent",
                    "--jobs-dir",
                    str(jobs_dir),
                ],
            )
            assert result.exit_code == 1
