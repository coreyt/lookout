"""Tests for SARIF output formatting."""

from __future__ import annotations

import json

from lookout.models import Finding
from lookout.sarif import _map_severity_to_sarif, format_findings_sarif


def _finding(
    pattern_id: str = "ARCH-001",
    title: str = "HTTP resilience wrappers",
    severity: str = "error",
    file: str = "src/app.py",
    line: int = 10,
    message: str = "Missing resilience wrapper",
    snippet: str = "requests.get(url)",
    tier: int = 1,
) -> Finding:
    return Finding(
        pattern_id=pattern_id,
        title=title,
        category="architecture",
        severity=severity,
        file=file,
        line=line,
        message=message,
        snippet=snippet,
        engine="semgrep",
        tier=tier,
    )


class TestMapSeverityToSarif:
    def test_error_maps_to_error(self) -> None:
        assert _map_severity_to_sarif("error") == "error"

    def test_warning_maps_to_warning(self) -> None:
        assert _map_severity_to_sarif("warning") == "warning"

    def test_info_maps_to_note(self) -> None:
        assert _map_severity_to_sarif("info") == "note"

    def test_unknown_severity_defaults_to_warning(self) -> None:
        assert _map_severity_to_sarif("critical") == "warning"

    def test_case_insensitive_match(self) -> None:
        assert _map_severity_to_sarif("ERROR") == "error"
        assert _map_severity_to_sarif("WARNING") == "warning"
        assert _map_severity_to_sarif("INFO") == "note"


class TestFormatFindingsSarif:
    def test_empty_findings_returns_valid_sarif(self) -> None:
        output = format_findings_sarif([])
        sarif = json.loads(output)

        assert sarif["version"] == "2.1.0"
        assert len(sarif["runs"]) == 1
        assert sarif["runs"][0]["results"] == []
        assert sarif["runs"][0]["tool"]["driver"]["rules"] == []

    def test_sarif_schema_field_present(self) -> None:
        output = format_findings_sarif([])
        sarif = json.loads(output)

        assert "$schema" in sarif
        assert "sarif" in sarif["$schema"].lower()

    def test_single_finding_produces_one_result_and_one_rule(self) -> None:
        output = format_findings_sarif([_finding()])
        sarif = json.loads(output)

        run = sarif["runs"][0]
        assert len(run["results"]) == 1
        assert len(run["tool"]["driver"]["rules"]) == 1

    def test_result_contains_correct_fields(self) -> None:
        f = _finding(pattern_id="ARCH-001", message="Missing wrapper", severity="error")
        output = format_findings_sarif([f])
        sarif = json.loads(output)

        result = sarif["runs"][0]["results"][0]
        assert result["ruleId"] == "ARCH-001"
        assert result["level"] == "error"
        assert result["message"]["text"] == "Missing wrapper"

    def test_result_location_is_populated(self) -> None:
        f = _finding(file="src/service.py", line=42, snippet="requests.post(url)")
        output = format_findings_sarif([f])
        sarif = json.loads(output)

        loc = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/service.py"
        assert loc["region"]["startLine"] == 42
        assert loc["region"]["snippet"]["text"] == "requests.post(url)"

    def test_rule_entry_contains_correct_fields(self) -> None:
        f = _finding(pattern_id="ARCH-001", title="HTTP resilience wrappers", tier=1)
        output = format_findings_sarif([f])
        sarif = json.loads(output)

        rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["id"] == "ARCH-001"
        assert rule["shortDescription"]["text"] == "HTTP resilience wrappers"
        assert rule["properties"]["tier"] == 1

    def test_multiple_findings_same_rule_deduplicated(self) -> None:
        findings = [
            _finding(file="src/a.py", line=1),
            _finding(file="src/b.py", line=2),
        ]
        output = format_findings_sarif(findings)
        sarif = json.loads(output)

        run = sarif["runs"][0]
        assert len(run["results"]) == 2
        assert len(run["tool"]["driver"]["rules"]) == 1

    def test_multiple_findings_different_rules(self) -> None:
        findings = [
            _finding(pattern_id="ARCH-001", title="Rule One"),
            _finding(pattern_id="ARCH-002", title="Rule Two"),
        ]
        output = format_findings_sarif(findings)
        sarif = json.loads(output)

        run = sarif["runs"][0]
        assert len(run["results"]) == 2
        assert len(run["tool"]["driver"]["rules"]) == 2

    def test_version_parameter_is_used(self) -> None:
        output = format_findings_sarif([], version="9.9.9")
        sarif = json.loads(output)

        assert sarif["runs"][0]["tool"]["driver"]["version"] == "9.9.9"

    def test_tool_driver_name_is_lookout(self) -> None:
        output = format_findings_sarif([])
        sarif = json.loads(output)

        assert sarif["runs"][0]["tool"]["driver"]["name"] == "Lookout"

    def test_severity_mapped_correctly_in_results(self) -> None:
        findings = [
            _finding(pattern_id="R1", severity="error"),
            _finding(pattern_id="R2", severity="warning"),
            _finding(pattern_id="R3", severity="info"),
        ]
        output = format_findings_sarif(findings)
        sarif = json.loads(output)

        levels = {r["ruleId"]: r["level"] for r in sarif["runs"][0]["results"]}
        assert levels["R1"] == "error"
        assert levels["R2"] == "warning"
        assert levels["R3"] == "note"

    def test_output_is_valid_json_string(self) -> None:
        output = format_findings_sarif([_finding()])
        # Should not raise
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_sarif_uses_finding_category(self) -> None:
        """Category in SARIF rule properties comes from finding.category, not hardcoded."""
        f = Finding(
            pattern_id="SEC-001",
            title="Security Rule",
            category="security",
            severity="error",
            file="src/app.py",
            line=10,
            message="Security violation",
            snippet="some_call()",
            engine="semgrep",
            tier=1,
        )
        sarif = json.loads(format_findings_sarif([f]))
        rule = sarif["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["properties"]["category"] == "security"


class TestBaselineState:
    def test_new_finding_is_marked_new(self) -> None:
        sarif = json.loads(format_findings_sarif([_finding()]))
        assert sarif["runs"][0]["results"][0]["baselineState"] == "new"

    def test_baselined_finding_is_marked_unchanged(self) -> None:
        from dataclasses import replace

        sarif = json.loads(format_findings_sarif([replace(_finding(), baselined=True)]))
        assert sarif["runs"][0]["results"][0]["baselineState"] == "unchanged"
