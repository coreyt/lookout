from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from lookout.baseline import Baseline
from lookout.models import (
    Finding,
    ForbiddenCheck,
    RuleExamples,
    RuleReporting,
    RuleSpec,
    RuleSpecFile,
)
from lookout.runner import run_lookout, targets_outside_git


def _spec() -> RuleSpecFile:
    return RuleSpecFile(
        schema_version=1,
        rule=RuleSpec(
            id="ARCH-010",
            title="Avoid eval",
            description="Desc",
            rationale="Because",
            tier=1,
            category="security",
            severity="error",
            languages=["python"],
            check=ForbiddenCheck(type="forbidden", pattern="eval($X)"),
            reporting=RuleReporting(
                default_message="eval forbidden (ARCH-010)",
                confidence="high",
                documentation_url=None,
            ),
            examples=RuleExamples(good=[], bad=[]),
            references=[],
        ),
    )


def _finding(message: str = "eval forbidden (ARCH-010)") -> Finding:
    return Finding(
        pattern_id="ARCH-010",
        title="Avoid eval",
        category="security",
        severity="error",
        file="src/app.py",
        line=7,
        message=message,
        snippet="eval(x)",
        engine="semgrep",
        tier=1,
    )


def test_run_lookout_dry_run_returns_preview() -> None:
    findings, new_findings, instances, errors, dry_output = run_lookout(
        specs=[_spec()],
        targets=[Path("src")],
        dry_run=True,
    )

    assert findings == []
    assert new_findings == []
    assert instances == []
    assert errors == []
    assert dry_output is not None
    assert "Would execute:" in dry_output


def test_run_lookout_update_baseline_writes_current_findings(monkeypatch) -> None:
    fake_result = subprocess.CompletedProcess(
        args=["semgrep"], returncode=1, stdout='{"results":[]}', stderr=""
    )
    findings = [_finding()]
    captured = {}

    monkeypatch.setattr("lookout.runner.subprocess.run", lambda *args, **kwargs: fake_result)
    monkeypatch.setattr(
        "lookout.runner.parse_semgrep_output", lambda *args, **kwargs: (findings, [], [])
    )
    monkeypatch.setattr("lookout.runner.load_baseline", lambda *args, **kwargs: None)

    def _capture_write(baseline: Baseline, path: Path) -> None:
        captured["baseline"] = baseline
        captured["path"] = path

    monkeypatch.setattr("lookout.runner.write_baseline", _capture_write)

    all_findings, new_findings, instances, errors, dry_output = run_lookout(
        specs=[_spec()],
        targets=[Path("src")],
        update_baseline=True,
    )

    assert all_findings == findings
    assert new_findings == []
    assert instances == []
    assert errors == []
    assert dry_output is None
    assert "baseline" in captured
    assert captured["baseline"] == Baseline.from_findings(findings)


def test_run_lookout_filters_existing_baseline(monkeypatch) -> None:
    fake_result = subprocess.CompletedProcess(
        args=["semgrep"], returncode=1, stdout='{"results":[]}', stderr=""
    )
    known = _finding("known")
    fresh = _finding("new")
    baseline = Baseline.from_findings([known])

    monkeypatch.setattr("lookout.runner.subprocess.run", lambda *args, **kwargs: fake_result)
    monkeypatch.setattr(
        "lookout.runner.parse_semgrep_output", lambda *args, **kwargs: ([known, fresh], [], [])
    )
    monkeypatch.setattr("lookout.runner.load_baseline", lambda *args, **kwargs: baseline)

    all_findings, new_findings, instances, errors, dry_output = run_lookout(
        specs=[_spec()],
        targets=[Path("src")],
    )

    # Known findings come back flagged as baselined; new ones unchanged.
    assert all_findings == [replace(known, baselined=True), fresh]
    assert new_findings == [fresh]
    assert instances == []
    assert errors == []
    assert dry_output is None


def test_targets_outside_git_detects_plain_directory(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert targets_outside_git([plain]) is True


def test_targets_outside_git_false_inside_repo(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.py").write_text("x = 1\n")

    assert targets_outside_git([repo]) is False
    assert targets_outside_git([repo / "a.py"]) is False


def test_run_lookout_passes_no_git_ignore_for_non_git_targets(monkeypatch, tmp_path) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout='{"results":[]}', stderr=""
        )

    monkeypatch.setattr("lookout.runner.subprocess.run", fake_run)
    monkeypatch.setattr("lookout.runner.targets_outside_git", lambda targets: True)
    monkeypatch.setattr("lookout.runner.load_baseline", lambda *args, **kwargs: None)

    run_lookout(specs=[_spec()], targets=[tmp_path])

    assert "--no-git-ignore" in captured["command"]


def test_run_lookout_marks_baselined_findings(monkeypatch) -> None:
    fake_result = subprocess.CompletedProcess(
        args=["semgrep"], returncode=1, stdout='{"results":[]}', stderr=""
    )
    known = _finding("known")
    fresh = _finding("new")
    baseline = Baseline.from_findings([known])

    monkeypatch.setattr("lookout.runner.subprocess.run", lambda *args, **kwargs: fake_result)
    monkeypatch.setattr("lookout.runner.targets_outside_git", lambda targets: False)
    monkeypatch.setattr(
        "lookout.runner.parse_semgrep_output", lambda *args, **kwargs: ([known, fresh], [], [])
    )
    monkeypatch.setattr("lookout.runner.load_baseline", lambda *args, **kwargs: baseline)

    all_findings, new_findings, _, _, _ = run_lookout(specs=[_spec()], targets=[Path("src")])

    assert [f.baselined for f in all_findings] == [True, False]
    assert new_findings == [replace(fresh, baselined=False)]
