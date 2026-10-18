"""Baseline support: remember known findings so only new ones fail a run.

Fingerprints are content-based (pattern, file, normalized snippet, occurrence
index within the file) rather than line-based, so edits elsewhere in a file do
not make an accepted finding reappear as "new".
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from lookout.models import Finding

logger = logging.getLogger(__name__)

BASELINE_VERSION = 2
BASELINE_PATH = Path(".lookout-baseline.json")

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class BaselineEntry:
    pattern_id: str
    file: str
    line: int
    hash: str


@dataclass(frozen=True)
class Baseline:
    entries: set[BaselineEntry]
    version: int = BASELINE_VERSION

    @classmethod
    def from_findings(cls, findings: Iterable[Finding]) -> Baseline:
        return cls(
            entries={
                BaselineEntry(
                    pattern_id=finding.pattern_id,
                    file=finding.file,
                    line=finding.line,
                    hash=fingerprint,
                )
                for finding, fingerprint in fingerprint_findings(list(findings))
            }
        )

    @property
    def hashes(self) -> set[str]:
        return {entry.hash for entry in self.entries}

    def to_dict(self) -> dict[str, object]:
        return {
            "version": BASELINE_VERSION,
            "violations": [
                {
                    "pattern_id": entry.pattern_id,
                    "file": entry.file,
                    "line": entry.line,
                    "hash": entry.hash,
                }
                for entry in sorted(
                    self.entries, key=lambda item: (item.pattern_id, item.file, item.line)
                )
            ],
        }


def load_baseline(path: Path = BASELINE_PATH) -> Baseline | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    version = int(data.get("version", 1))
    if version != BASELINE_VERSION:
        logger.warning(
            "Baseline %s uses format version %s (current is %s); its entries will not match. "
            "Re-create it with `lookout check --update-baseline`.",
            path,
            version,
            BASELINE_VERSION,
        )
    entries = {
        BaselineEntry(
            pattern_id=item.get("pattern_id", item.get("guideline_id", "")),
            file=item["file"],
            line=item["line"],
            hash=item["hash"],
        )
        for item in data.get("violations", [])
    }
    return Baseline(entries=entries, version=version)


def write_baseline(baseline: Baseline, path: Path = BASELINE_PATH) -> None:
    path.write_text(json.dumps(baseline.to_dict(), indent=2) + "\n", encoding="utf-8")


def mark_baselined(findings: list[Finding], baseline: Baseline | None) -> list[Finding]:
    """Return ``findings`` with ``baselined=True`` on those the baseline already knows."""
    if baseline is None:
        return findings
    known = baseline.hashes
    return [
        replace(finding, baselined=fingerprint in known)
        for finding, fingerprint in fingerprint_findings(findings)
    ]


def filter_findings(findings: list[Finding], baseline: Baseline | None) -> list[Finding]:
    """Return only the findings not present in the baseline."""
    return [finding for finding in mark_baselined(findings, baseline) if not finding.baselined]


def fingerprint_findings(findings: list[Finding]) -> list[tuple[Finding, str]]:
    """Pair each finding with its content-based fingerprint, preserving order.

    Identical snippets in the same file are disambiguated by their order of
    appearance (by line), so two ``print("x")`` calls get distinct fingerprints
    without depending on their absolute line numbers.
    """
    groups: dict[tuple[str, str, str], list[Finding]] = defaultdict(list)
    for finding in findings:
        groups[_group_key(finding)].append(finding)

    fingerprints: dict[int, str] = {}
    for key, members in groups.items():
        for index, finding in enumerate(sorted(members, key=lambda f: f.line)):
            fingerprints[id(finding)] = _hash(f"{key[0]}|{key[1]}|{key[2]}|{index}")

    return [(finding, fingerprints[id(finding)]) for finding in findings]


def _group_key(finding: Finding) -> tuple[str, str, str]:
    return finding.pattern_id, finding.file, _normalize_snippet(finding.snippet)


def _normalize_snippet(snippet: str) -> str:
    return _WHITESPACE_RE.sub(" ", snippet).strip()


def _hash(fingerprint: str) -> str:
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:12]
