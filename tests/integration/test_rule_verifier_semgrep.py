"""Run the rule verifier against the real Semgrep binary."""

from __future__ import annotations

import shutil

import pytest

from lookout.discovery.models import (
    DiscoveredPattern,
    PatternExample,
    PatternExamples,
    ValidatedPattern,
)
from lookout.models import ForbiddenCheck, RawCheck
from lookout.rule_verifier import verify_check

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("semgrep") is None, reason="semgrep binary not found on PATH"),
]


def _pattern() -> ValidatedPattern:
    discovered = DiscoveredPattern(
        pattern_id="ARCH-DI-001",
        title="Avoid direct instantiation in service layer",
        description="Services receive dependencies via constructor injection.",
        rationale="Direct instantiation couples services to concrete implementations.",
        category="architecture",
        severity="error",
        confidence="high",
        languages=["python"],
        examples=PatternExamples(
            good=[
                PatternExample(
                    language="python",
                    code=(
                        "class OrderService:\n"
                        "    def __init__(self, repo: OrderRepo):\n"
                        "        self.repo = repo\n"
                    ),
                )
            ],
            bad=[
                PatternExample(
                    language="python",
                    code=(
                        "class OrderService:\n"
                        "    def __init__(self):\n"
                        "        self.repo = OrderRepo()\n"
                    ),
                ),
                PatternExample(language="python", code="repo = OrderRepo()\n"),
            ],
        ),
        discovered_by="test",
    )
    return ValidatedPattern.from_discovered(discovered, validated_by="test")


def test_known_good_forbidden_check_passes() -> None:
    report = verify_check(ForbiddenCheck(type="forbidden", pattern="OrderRepo()"), _pattern())
    assert report.passed, report.format()
    assert report.checked_bad == 2
    assert report.checked_good == 1


def test_wrong_pattern_fails_on_unmatched_bad() -> None:
    report = verify_check(ForbiddenCheck(type="forbidden", pattern="SomethingElse()"), _pattern())
    assert not report.passed
    assert not report.rule_errors
    assert [ex.index for ex in report.unmatched_bad] == [0, 1]
    assert not report.matched_good
    assert "2 bad example(s) were NOT matched" in report.format()


def test_overbroad_pattern_fails_on_matched_good() -> None:
    # `$X = $Y` matches the assignment in the good example too.
    report = verify_check(ForbiddenCheck(type="forbidden", pattern="self.$F = $V"), _pattern())
    assert not report.passed
    assert len(report.matched_good) == 1
    assert report.matched_good[0].matches, "expected the matched line to be reported"


def test_invalid_rule_reports_semgrep_error() -> None:
    raw = RawCheck(
        type="raw",
        config=(
            "rules:\n"
            "  - id: broken\n"
            "    languages: [python]\n"
            "    severity: ERROR\n"
            "    message: broken\n"
            "    pattern: 'def ((('\n"
        ),
    )
    report = verify_check(raw, _pattern())
    assert not report.passed
    assert report.rule_errors, report.format()
