from __future__ import annotations

import json
import subprocess
from pathlib import Path

from lookout.models import (
    ForbiddenCheck,
    RuleExamples,
    RuleReporting,
    RuleSpec,
    RuleSpecFile,
)
from lookout.runner import run_lookout


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


def test_run_lookout_reports_parse_errors(monkeypatch) -> None:
    # Simulate Semgrep output with both results and errors
    semgrep_output = json.dumps(
        {
            "results": [],
            "errors": [
                {
                    "code": 2,
                    "level": "error",
                    "type": "Rule parse error",
                    "rule_id": "arch-010-impl",
                    "message": "Invalid pattern",
                }
            ],
        }
    )

    fake_result = subprocess.CompletedProcess(
        args=["semgrep"], returncode=2, stdout=semgrep_output, stderr=""
    )

    monkeypatch.setattr("lookout.runner.subprocess.run", lambda *args, **kwargs: fake_result)

    # Mock other dependencies
    monkeypatch.setattr("lookout.runner.load_baseline", lambda *args, **kwargs: None)
    monkeypatch.setattr("lookout.runner.targets_outside_git", lambda targets: False)

    # Expecting run_lookout to return: (findings, new_findings, instances, errors, dry_output)
    findings, new_findings, instances, errors, dry_output = run_lookout(
        specs=[_spec()],
        targets=[Path("src")],
    )

    # Assert that we captured the error
    assert len(errors) == 1
    assert errors[0].rule_id == "ARCH-010"
    assert "Invalid pattern" in errors[0].message
