"""Data models for the batch generation system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _parse_iso(s: str) -> datetime:
    """Parse ISO timestamp, handling Z suffix for Python 3.10 compat."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class CellStatus(str, Enum):
    PRESENT = "present"
    POSSIBLY_STALE = "stale_soft"
    LIKELY_STALE = "stale_hard"
    MISSING = "missing"


@dataclass(frozen=True)
class RegistryCell:
    """One cell in the pattern x language x framework grid."""

    pattern_id: str
    language: str
    framework: str | None
    status: CellStatus

    @property
    def cell_id(self) -> str:
        fw = self.framework or "generic"
        return f"{self.pattern_id}__{self.language}__{fw}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "language": self.language,
            "framework": self.framework,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegistryCell:
        return cls(
            pattern_id=data["pattern_id"],
            language=data["language"],
            framework=data["framework"],
            status=CellStatus(data["status"]),
        )


@dataclass(frozen=True)
class BatchRequest:
    """One LLM request within a batch."""

    custom_id: str
    cell: RegistryCell
    system_prompt: str
    user_prompt: str


class BatchStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BatchResult:
    """Result for one cell after batch completes."""

    custom_id: str
    cell: RegistryCell
    success: bool
    output: str
    error: str | None
    token_usage: dict[str, int]


@dataclass
class BatchJob:
    """Tracks a submitted batch."""

    job_id: str
    provider: str
    model: str
    status: BatchStatus
    requests: list[BatchRequest]
    results: list[BatchResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_at: datetime | None = None
    request_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict for persistence."""
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status.value,
            "requests": [
                {
                    "custom_id": r.custom_id,
                    "cell": r.cell.to_dict(),
                    "system_prompt": r.system_prompt,
                    "user_prompt": r.user_prompt,
                }
                for r in self.requests
            ],
            "results": [
                {
                    "custom_id": r.custom_id,
                    "cell": r.cell.to_dict(),
                    "success": r.success,
                    "output": r.output,
                    "error": r.error,
                    "token_usage": r.token_usage,
                }
                for r in self.results
            ],
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "request_counts": self.request_counts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchJob:
        """Deserialize from a JSON-compatible dict."""
        requests = [
            BatchRequest(
                custom_id=r["custom_id"],
                cell=RegistryCell.from_dict(r["cell"]),
                system_prompt=r["system_prompt"],
                user_prompt=r["user_prompt"],
            )
            for r in data["requests"]
        ]
        results = [
            BatchResult(
                custom_id=r["custom_id"],
                cell=RegistryCell.from_dict(r["cell"]),
                success=r["success"],
                output=r["output"],
                error=r["error"],
                token_usage=r["token_usage"],
            )
            for r in data["results"]
        ]
        return cls(
            job_id=data["job_id"],
            provider=data["provider"],
            model=data["model"],
            status=BatchStatus(data["status"]),
            requests=requests,
            results=results,
            created_at=_parse_iso(data["created_at"]),
            completed_at=(_parse_iso(data["completed_at"]) if data["completed_at"] else None),
            request_counts=data["request_counts"],
        )
