"""Unit tests for rule_verifier with Semgrep mocked out."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExample,
    PatternExamples,
    ValidatedPattern,
)
from lookout.models import ForbiddenCheck, RawCheck
from lookout.rule_verifier import VerificationReport, verify_check

GOOD = "class S:\n    def __init__(self, repo):\n        self.repo = repo\n"
BAD = "class S:\n    def __init__(self):\n        self.repo = Repo()\n"
CHECK = ForbiddenCheck(type="forbidden", pattern="Repo()")


def _pattern(
    good: list[PatternExample] | None = None,
    bad: list[PatternExample] | None = None,
) -> ValidatedPattern:
    discovered = DiscoveredPattern(
        pattern_id="ARCH-DI-001",
        title="Inject dependencies",
        description="Inject dependencies through the constructor.",
        rationale="Constructor injection keeps services testable and decoupled.",
        category="architecture",
        severity="error",
        confidence="high",
        languages=["python"],
        examples=PatternExamples(
            good=[PatternExample(language="python", code=GOOD)] if good is None else good,
            bad=[PatternExample(language="python", code=BAD)] if bad is None else bad,
        ),
        discovered_by="test",
    )
    return ValidatedPattern.from_discovered(discovered, validated_by="test")


class FakeSemgrep:
    """Stand-in for subprocess.run that answers with canned Semgrep JSON.

    ``hits`` maps example file names (``bad_0.py``) to the number of results
    to report for that file.
    """

    def __init__(
        self,
        hits: dict[str, int],
        errors: list[dict[str, Any]] | None = None,
        returncode: int = 0,
        stdout: str | None = None,
        stderr: str = "",
    ) -> None:
        self.hits = hits
        self.errors = errors or []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.stdout is not None:
            return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)
        targets = [Path(arg) for arg in command if arg.endswith((".py", ".ts", ".go"))]
        results = []
        for target in targets:
            for i in range(self.hits.get(target.name, 0)):
                results.append(
                    {
                        "check_id": "rule.arch-di-001-impl",
                        "path": str(target),
                        "start": {"line": 3 + i},
                        "extra": {"lines": "        self.repo = Repo()"},
                    }
                )
        payload = json.dumps({"results": results, "errors": self.errors})
        return subprocess.CompletedProcess(command, self.returncode, payload, self.stderr)


class TestVerifyCheck:
    def test_passes_when_bad_matches_and_good_is_clean(self) -> None:
        fake = FakeSemgrep({"bad_0.py": 1})
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert report.passed
        assert report.checked_bad == 1
        assert report.checked_good == 1
        assert "Verification passed" in report.format()

    def test_runs_semgrep_with_no_git_ignore_on_temp_files(self) -> None:
        fake = FakeSemgrep({"bad_0.py": 1})
        verify_check(CHECK, _pattern(), runner=fake)
        (command,) = fake.commands
        assert command[0] == "semgrep"
        assert "--no-git-ignore" in command
        assert "--json" in command
        names = sorted(Path(a).name for a in command if a.endswith(".py"))
        assert names == ["bad_0.py", "good_0.py"]

    def test_unmatched_bad_fails(self) -> None:
        fake = FakeSemgrep({})
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert not report.passed
        assert [ex.index for ex in report.unmatched_bad] == [0]
        assert not report.matched_good
        text = report.format()
        assert "1 bad example(s) were NOT matched" in text
        assert "self.repo = Repo()" in text

    def test_matched_good_fails(self) -> None:
        fake = FakeSemgrep({"bad_0.py": 1, "good_0.py": 2})
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert not report.passed
        assert len(report.matched_good) == 1
        assert len(report.matched_good[0].matches) == 2
        text = report.format()
        assert "1 good example(s) WERE matched" in text
        assert "line 3:" in text

    def test_semgrep_rule_errors_fail(self) -> None:
        fake = FakeSemgrep(
            {"bad_0.py": 1},
            errors=[{"type": "SemgrepError", "message": "invalid pattern syntax"}],
        )
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert not report.passed
        assert report.rule_errors == ["SemgrepError: invalid pattern syntax"]

    def test_nonzero_exit_without_output_is_reported(self) -> None:
        fake = FakeSemgrep({}, returncode=2, stdout="", stderr="boom")
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert not report.passed
        assert report.rule_errors == ["semgrep exited with code 2: boom"]

    def test_unparseable_output_is_reported(self) -> None:
        fake = FakeSemgrep({}, stdout="not json")
        report = verify_check(CHECK, _pattern(), runner=fake)
        assert not report.passed
        assert "no parseable JSON" in report.rule_errors[0]

    def test_multiple_examples_tracked_by_index(self) -> None:
        pattern = _pattern(
            bad=[
                PatternExample(language="python", code=BAD),
                PatternExample(language="python", code="x = Repo()\n"),
                PatternExample(language="python", code="y = Other()\n"),
            ]
        )
        fake = FakeSemgrep({"bad_0.py": 1, "bad_1.py": 1})
        report = verify_check(CHECK, pattern, runner=fake)
        assert not report.passed
        assert [ex.index for ex in report.unmatched_bad] == [2]
        assert "bad example #3" in report.format()

    def test_unknown_language_examples_are_skipped(self) -> None:
        pattern = _pattern(
            bad=[
                PatternExample(language="python", code=BAD),
                PatternExample(language="cobol", code="MOVE A TO B."),
            ]
        )
        fake = FakeSemgrep({"bad_0.py": 1})
        report = verify_check(CHECK, pattern, runner=fake)
        assert report.passed
        assert len(report.skipped) == 1
        assert "skipped" in report.format()

    def test_no_examples_passes_vacuously_without_running_semgrep(self) -> None:
        fake = FakeSemgrep({})
        report = verify_check(CHECK, _pattern(good=[], bad=[]), runner=fake)
        assert report.passed
        assert fake.commands == []
        assert any("no runnable examples" in note for note in report.notes)

    def test_compile_failure_is_reported(self) -> None:
        fake = FakeSemgrep({})
        bad_raw = RawCheck(type="raw", config="rules: [\n  - not: valid: yaml")
        report = verify_check(bad_raw, _pattern(), runner=fake)
        assert not report.passed
        assert report.rule_errors and "compile" in report.rule_errors[0]
        assert fake.commands == []

    def test_paths_are_stripped_from_compiled_rules(self, tmp_path: Path) -> None:
        """Example temp files must not be filtered out by a spec's paths block."""
        captured: dict[str, str] = {}

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            config_path = Path(command[command.index("--config") + 1])
            captured["config"] = config_path.read_text(encoding="utf-8")
            return FakeSemgrep({"bad_0.py": 1})(command)

        verify_check(CHECK, _pattern(), runner=runner)
        assert "paths:" not in captured["config"]
        assert "Repo()" in captured["config"]


class TestVerificationReport:
    def test_empty_report_passes(self) -> None:
        assert VerificationReport().passed

    def test_any_rule_error_fails(self) -> None:
        assert not VerificationReport(rule_errors=["x"]).passed


@pytest.mark.parametrize("returncode", [0, 1])
def test_exit_codes_zero_and_one_are_normal(returncode: int) -> None:
    fake = FakeSemgrep({"bad_0.py": 1}, returncode=returncode)
    assert verify_check(CHECK, _pattern(), runner=fake).passed
