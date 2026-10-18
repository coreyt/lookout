from __future__ import annotations

import json
import logging

from lookout.baseline import (
    BASELINE_VERSION,
    Baseline,
    filter_findings,
    load_baseline,
    mark_baselined,
    write_baseline,
)
from lookout.models import Finding


def test_filter_findings_removes_known() -> None:
    finding = Finding(
        pattern_id="ARCH-001",
        title="HTTP calls require resilience wrappers",
        category="resilience",
        severity="error",
        file="src/app.py",
        line=10,
        message="HTTP call outside resilience wrapper (ARCH-001)",
        snippet="requests.get(url)",
        engine="semgrep",
        tier=1,
    )
    baseline = Baseline.from_findings([finding])
    remaining = filter_findings([finding], baseline)

    assert remaining == []


def _finding(line: int, snippet: str = 'print("x")', pattern_id: str = "ARCH-003") -> Finding:
    return Finding(
        pattern_id=pattern_id,
        title="t",
        category="c",
        severity="warning",
        file="src/app.py",
        line=line,
        message="m",
        snippet=snippet,
        engine="semgrep",
        tier=1,
    )


def test_fingerprint_is_stable_when_lines_shift() -> None:
    baseline = Baseline.from_findings([_finding(10)])

    # Same snippet, same file, moved down by an edit above it.
    assert filter_findings([_finding(14)], baseline) == []


def test_fingerprint_ignores_whitespace_differences_in_snippet() -> None:
    baseline = Baseline.from_findings([_finding(10, snippet='print(  "x" )')])

    assert filter_findings([_finding(10, snippet='print( "x" )')], baseline) == []


def test_identical_snippets_in_one_file_are_distinct_by_occurrence() -> None:
    baseline = Baseline.from_findings([_finding(10)])

    remaining = filter_findings([_finding(10), _finding(20)], baseline)

    # One occurrence is known; the second identical print is new.
    assert len(remaining) == 1


def test_fingerprint_differs_by_pattern_and_file() -> None:
    baseline = Baseline.from_findings([_finding(10)])

    other_pattern = _finding(10, pattern_id="ARCH-001")
    assert filter_findings([other_pattern], baseline) == [other_pattern]


def test_mark_baselined_sets_flag_and_preserves_order() -> None:
    known = _finding(10)
    fresh = _finding(20, snippet="eval(x)")
    baseline = Baseline.from_findings([known])

    marked = mark_baselined([fresh, known], baseline)

    assert [f.line for f in marked] == [20, 10]
    assert [f.baselined for f in marked] == [False, True]


def test_mark_baselined_without_baseline_is_identity() -> None:
    findings = [_finding(10)]
    assert mark_baselined(findings, None) is findings


def test_baseline_round_trip_writes_version_2(tmp_path) -> None:
    path = tmp_path / "baseline.json"
    write_baseline(Baseline.from_findings([_finding(10)]), path)

    data = json.loads(path.read_text())
    assert data["version"] == BASELINE_VERSION
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded.version == BASELINE_VERSION
    assert filter_findings([_finding(99)], loaded) == []


def test_load_old_baseline_warns(tmp_path, caplog) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "violations": [
                    {"pattern_id": "ARCH-003", "file": "src/app.py", "line": 10, "hash": "deadbeef"}
                ],
            }
        )
    )

    with caplog.at_level(logging.WARNING):
        loaded = load_baseline(path)

    assert loaded is not None
    assert loaded.version == 1
    assert "format version 1" in caplog.text
