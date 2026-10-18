from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from click.testing import CliRunner

from lookout.cli import cli
from lookout.models import RuleError
from lookout.project import SelectionReport


def _passthrough_selection(specs, profile, max_tier):
    return SelectionReport(specs, [], [], [], [])


def test_check_continues_on_rule_error_by_default(monkeypatch):
    # Mock run_lookout to return an error but no findings
    # Returns: (findings, new_findings, instances, errors, dry_output)
    errors = [RuleError(rule_id="TEST", message="Fail", level="error", type="Parse")]
    monkeypatch.setattr("lookout.cli.run_lookout", lambda **kwargs: ([], [], [], errors, None))

    # Mock load_rule_specs to return something so we don't fail early
    monkeypatch.setattr("lookout.cli.load_all_rules", lambda user_rules_dir: [Mock()])
    monkeypatch.setattr("lookout.cli.select_specs", _passthrough_selection)

    runner = CliRunner(mix_stderr=False)
    with runner.isolated_filesystem():
        Path("rules").mkdir()
        result = runner.invoke(cli, ["check", "--rules-dir", "rules"])

        # Should warn but succeed (exit code 0) because fail_on_rule_error is False by default
        assert result.exit_code == 0
        assert "Warning: 1 rules failed to execute" in result.stderr


def test_check_fails_on_rule_error_with_flag(monkeypatch):
    errors = [RuleError(rule_id="TEST", message="Fail", level="error", type="Parse")]
    monkeypatch.setattr("lookout.cli.run_lookout", lambda **kwargs: ([], [], [], errors, None))
    monkeypatch.setattr("lookout.cli.load_all_rules", lambda user_rules_dir: [Mock()])
    monkeypatch.setattr("lookout.cli.select_specs", _passthrough_selection)

    runner = CliRunner(mix_stderr=False)
    with runner.isolated_filesystem():
        Path("rules").mkdir()
        # Pass --fail-on-rule-error
        result = runner.invoke(cli, ["check", "--rules-dir", "rules", "--fail-on-rule-error"])

        assert result.exit_code == 1
        assert "Warning: 1 rules failed to execute" in result.stderr
