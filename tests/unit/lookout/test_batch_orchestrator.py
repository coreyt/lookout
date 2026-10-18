"""Tests for batch orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lookout.batch.models import (
    BatchJob,
    BatchRequest,
    BatchStatus,
    CellStatus,
    RegistryCell,
)
from lookout.batch.orchestrator import BatchOrchestrator
from lookout.config import LookoutConfig
from lookout.models import (
    PatternSpec,
    PatternSpecFile,
    RuleReporting,
)


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
                default_message="Use constructor injection (DI-001)",
                confidence="medium",
            ),
        ),
    )


class TestBatchOrchestrator:
    def test_build_grid(self, tmp_path: Path) -> None:
        from lookout.registry import save_pattern

        spec = _make_spec()
        save_pattern(spec, tmp_path)

        config = LookoutConfig(rules_dir=tmp_path)
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)
        cells = orch.build_grid(["python", "typescript"], {})
        assert len(cells) == 2
        assert all(c.status == CellStatus.MISSING for c in cells)

    def test_build_grid_with_frameworks(self, tmp_path: Path) -> None:
        from lookout.registry import save_pattern

        spec = _make_spec()
        save_pattern(spec, tmp_path)

        config = LookoutConfig(rules_dir=tmp_path)
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)
        cells = orch.build_grid(["python"], {"python": ["django"]})
        assert len(cells) == 2  # 1 generic + 1 framework

    async def test_submit_batch(self, tmp_path: Path) -> None:
        from lookout.registry import save_pattern

        spec = _make_spec()
        save_pattern(spec, tmp_path)

        config = LookoutConfig(rules_dir=tmp_path, llm_provider="anthropic")
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)

        mock_provider = AsyncMock()
        mock_provider.submit.return_value = "batch_123"
        orch._provider = mock_provider

        cells = [RegistryCell("DI-001", "python", None, CellStatus.MISSING)]
        job = await orch.submit_batch(cells)

        assert job.job_id == "batch_123"
        assert job.status == BatchStatus.SUBMITTED
        assert len(job.requests) == 1
        mock_provider.submit.assert_called_once()

    async def test_poll_batch(self, tmp_path: Path) -> None:
        config = LookoutConfig(rules_dir=tmp_path)
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)

        mock_provider = AsyncMock()
        mock_provider.poll.return_value = (
            BatchStatus.PROCESSING,
            {"succeeded": 5, "processing": 3},
        )
        orch._provider = mock_provider

        job = BatchJob(
            job_id="batch_123",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            status=BatchStatus.SUBMITTED,
            requests=[],
        )
        updated = await orch.poll_batch(job)
        assert updated.status == BatchStatus.PROCESSING
        assert updated.request_counts["succeeded"] == 5

    async def test_retrieve_results(self, tmp_path: Path) -> None:
        from lookout.registry import save_pattern

        spec = _make_spec()
        save_pattern(spec, tmp_path)

        config = LookoutConfig(rules_dir=tmp_path)
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)

        cell = RegistryCell("DI-001", "python", None, CellStatus.MISSING)
        req = BatchRequest("DI-001__python__generic", cell, "sys", "usr")
        mock_provider = AsyncMock()
        mock_provider.retrieve.return_value = [
            {
                "custom_id": "DI-001__python__generic",
                "success": True,
                "output": "Generated content",
                "error": None,
                "token_usage": {"input_tokens": 100, "output_tokens": 200},
            }
        ]
        orch._provider = mock_provider

        job = BatchJob(
            job_id="batch_123",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            status=BatchStatus.COMPLETED,
            requests=[req],
        )
        updated = await orch.retrieve_results(job)
        assert len(updated.results) == 1
        assert updated.results[0].success is True
        assert updated.results[0].output == "Generated content"

    def test_save_and_load_job(self, tmp_path: Path) -> None:
        config = LookoutConfig(rules_dir=tmp_path)
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)

        cell = RegistryCell("DI-001", "python", None, CellStatus.MISSING)
        req = BatchRequest("DI-001__python__generic", cell, "sys", "usr")
        job = BatchJob(
            job_id="batch_123",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            status=BatchStatus.SUBMITTED,
            requests=[req],
        )

        job_path = tmp_path / "jobs"
        orch.save_job(job, job_path)
        loaded = orch.load_job(job_path / "batch_123.json")
        assert loaded.job_id == "batch_123"
        assert loaded.requests[0].cell.pattern_id == "DI-001"

    def test_create_provider_anthropic(self, tmp_path: Path) -> None:
        config = LookoutConfig(rules_dir=tmp_path, llm_provider="anthropic")
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        assert isinstance(orch._create_provider(), AnthropicBatchProvider)

    def test_create_provider_gemini(self, tmp_path: Path) -> None:
        config = LookoutConfig(rules_dir=tmp_path, llm_provider="gemini")
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)
        from lookout.batch.providers.gemini import GeminiBatchProvider

        assert isinstance(orch._create_provider(), GeminiBatchProvider)

    def test_create_provider_unknown_raises(self, tmp_path: Path) -> None:
        config = LookoutConfig(rules_dir=tmp_path, llm_provider="unknown")
        orch = BatchOrchestrator(config=config, patterns_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported"):
            orch._create_provider()
