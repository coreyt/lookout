"""Anthropic batch API provider."""

from __future__ import annotations

from typing import Any

from lookout.batch.models import BatchRequest, BatchStatus


class AnthropicBatchProvider:
    """Batch provider using Anthropic Messages Batches API."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # type: ignore[import-not-found,unused-ignore]

            self._client = anthropic.Anthropic()
        return self._client

    async def submit(self, requests: list[BatchRequest], model: str) -> str:
        """Submit batch requests to Anthropic."""
        client = self._get_client()

        api_requests = []
        for req in requests:
            api_requests.append(
                {
                    "custom_id": req.custom_id,
                    "params": {
                        "model": model,
                        "max_tokens": 4096,
                        "system": req.system_prompt,
                        "messages": [{"role": "user", "content": req.user_prompt}],
                    },
                }
            )

        batch = client.messages.batches.create(requests=api_requests)
        return str(batch.id)

    async def poll(self, job_id: str) -> tuple[BatchStatus, dict[str, int]]:
        """Check batch status."""
        client = self._get_client()
        batch = client.messages.batches.retrieve(job_id)

        counts = {
            "processing": batch.request_counts.processing,
            "succeeded": batch.request_counts.succeeded,
            "errored": batch.request_counts.errored,
            "expired": batch.request_counts.expired,
            "canceled": batch.request_counts.canceled,
        }

        if batch.processing_status == "ended":
            status = BatchStatus.COMPLETED
        elif batch.processing_status == "canceling":
            status = BatchStatus.CANCELLED
        else:
            status = BatchStatus.PROCESSING

        return status, counts

    async def retrieve(self, job_id: str) -> list[dict[str, Any]]:
        """Fetch results from completed batch."""
        client = self._get_client()
        raw_results = client.messages.batches.results(job_id)

        results: list[dict[str, Any]] = []
        for item in raw_results:
            if item.result.type == "succeeded":
                text = "".join(block.text for block in item.result.message.content)
                results.append(
                    {
                        "custom_id": item.custom_id,
                        "success": True,
                        "output": text,
                        "error": None,
                        "token_usage": {
                            "input_tokens": item.result.message.usage.input_tokens,
                            "output_tokens": item.result.message.usage.output_tokens,
                        },
                    }
                )
            else:
                results.append(
                    {
                        "custom_id": item.custom_id,
                        "success": False,
                        "output": "",
                        "error": str(item.result.type),
                        "token_usage": {},
                    }
                )

        return results

    async def cancel(self, job_id: str) -> None:
        """Cancel a batch job."""
        client = self._get_client()
        client.messages.batches.cancel(job_id)
