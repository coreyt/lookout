"""Tests for batch providers (mock-based, no real API calls)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lookout.batch.models import (
    BatchRequest,
    BatchStatus,
    CellStatus,
    RegistryCell,
)
from lookout.batch.providers.base import BatchProvider


def _make_request(custom_id: str = "DI-001__python__generic") -> BatchRequest:
    cell = RegistryCell("DI-001", "python", None, CellStatus.MISSING)
    return BatchRequest(custom_id=custom_id, cell=cell, system_prompt="sys", user_prompt="usr")


class TestBatchProviderProtocol:
    def test_protocol_has_required_methods(self) -> None:
        assert hasattr(BatchProvider, "submit")
        assert hasattr(BatchProvider, "poll")
        assert hasattr(BatchProvider, "retrieve")
        assert hasattr(BatchProvider, "cancel")


class TestAnthropicProvider:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        client = MagicMock()
        client.messages = MagicMock()
        client.messages.batches = MagicMock()
        return client

    async def test_submit(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        mock_batch = MagicMock()
        mock_batch.id = "msgbatch_01Hkc"
        mock_client.messages.batches.create.return_value = mock_batch

        provider = AnthropicBatchProvider(client=mock_client)
        job_id = await provider.submit([_make_request()], "claude-sonnet-4-20250514")

        assert job_id == "msgbatch_01Hkc"
        mock_client.messages.batches.create.assert_called_once()

    async def test_poll_processing(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        mock_batch = MagicMock()
        mock_batch.processing_status = "in_progress"
        mock_batch.request_counts = MagicMock()
        mock_batch.request_counts.processing = 5
        mock_batch.request_counts.succeeded = 3
        mock_batch.request_counts.errored = 0
        mock_batch.request_counts.expired = 0
        mock_batch.request_counts.canceled = 0
        mock_client.messages.batches.retrieve.return_value = mock_batch

        provider = AnthropicBatchProvider(client=mock_client)
        status, counts = await provider.poll("msgbatch_01Hkc")

        assert status == BatchStatus.PROCESSING
        assert counts["succeeded"] == 3

    async def test_poll_ended(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        mock_batch = MagicMock()
        mock_batch.processing_status = "ended"
        mock_batch.request_counts = MagicMock()
        mock_batch.request_counts.processing = 0
        mock_batch.request_counts.succeeded = 10
        mock_batch.request_counts.errored = 0
        mock_batch.request_counts.expired = 0
        mock_batch.request_counts.canceled = 0
        mock_client.messages.batches.retrieve.return_value = mock_batch

        provider = AnthropicBatchProvider(client=mock_client)
        status, counts = await provider.poll("msgbatch_01Hkc")

        assert status == BatchStatus.COMPLETED

    async def test_retrieve(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        # Mock a successful result
        mock_result = MagicMock()
        mock_result.custom_id = "DI-001__python__generic"
        mock_result.result = MagicMock()
        mock_result.result.type = "succeeded"
        mock_result.result.message = MagicMock()
        mock_result.result.message.content = [MagicMock(text="Generated output")]
        mock_result.result.message.usage = MagicMock()
        mock_result.result.message.usage.input_tokens = 100
        mock_result.result.message.usage.output_tokens = 200

        mock_client.messages.batches.results.return_value = [mock_result]

        provider = AnthropicBatchProvider(client=mock_client)
        results = await provider.retrieve("msgbatch_01Hkc")

        assert len(results) == 1
        assert results[0]["custom_id"] == "DI-001__python__generic"
        assert results[0]["success"] is True
        assert results[0]["output"] == "Generated output"

    async def test_cancel(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.anthropic import AnthropicBatchProvider

        provider = AnthropicBatchProvider(client=mock_client)
        await provider.cancel("msgbatch_01Hkc")
        mock_client.messages.batches.cancel.assert_called_once_with("msgbatch_01Hkc")


class TestGeminiProvider:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock()

    async def test_submit(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.gemini import GeminiBatchProvider

        mock_batch = MagicMock()
        mock_batch.name = "batches/123456"
        mock_client.batches.create.return_value = mock_batch

        provider = GeminiBatchProvider(client=mock_client)
        job_id = await provider.submit([_make_request()], "gemini-2.5-pro")

        assert job_id == "batches/123456"

    async def test_poll_processing(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.gemini import GeminiBatchProvider

        mock_batch = MagicMock()
        mock_batch.state = "JOB_STATE_RUNNING"
        mock_batch.dest = MagicMock()
        mock_batch.dest.inlined_responses = []
        mock_client.batches.get.return_value = mock_batch

        provider = GeminiBatchProvider(client=mock_client)
        status, counts = await provider.poll("batches/123456")
        assert status == BatchStatus.PROCESSING

    async def test_poll_completed(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.gemini import GeminiBatchProvider

        mock_batch = MagicMock()
        mock_batch.state = "JOB_STATE_SUCCEEDED"
        mock_client.batches.get.return_value = mock_batch

        provider = GeminiBatchProvider(client=mock_client)
        status, counts = await provider.poll("batches/123456")
        assert status == BatchStatus.COMPLETED

    async def test_retrieve(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.gemini import GeminiBatchProvider

        mock_response = MagicMock()
        mock_response.key = "DI-001__python__generic"
        mock_response.response = MagicMock()
        mock_response.response.candidates = [MagicMock()]
        mock_response.response.candidates[0].content = MagicMock()
        mock_response.response.candidates[0].content.parts = [MagicMock(text="Output")]
        mock_response.response.usage_metadata = MagicMock()
        mock_response.response.usage_metadata.prompt_token_count = 50
        mock_response.response.usage_metadata.candidates_token_count = 100

        mock_batch = MagicMock()
        mock_batch.dest = MagicMock()
        mock_batch.dest.inlined_responses = [mock_response]
        mock_client.batches.get.return_value = mock_batch

        provider = GeminiBatchProvider(client=mock_client)
        results = await provider.retrieve("batches/123456")

        assert len(results) == 1
        assert results[0]["custom_id"] == "DI-001__python__generic"
        assert results[0]["success"] is True

    async def test_cancel(self, mock_client: MagicMock) -> None:
        from lookout.batch.providers.gemini import GeminiBatchProvider

        provider = GeminiBatchProvider(client=mock_client)
        await provider.cancel("batches/123456")
        mock_client.batches.cancel.assert_called_once()
