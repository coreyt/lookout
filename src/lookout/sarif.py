"""SARIF (Static Analysis Results Interchange Format) support for Lookout."""

from __future__ import annotations

import json

from lookout import __version__
from lookout.models import Finding


def format_findings_sarif(findings: list[Finding], version: str = __version__) -> str:
    """Format findings as a SARIF JSON string.

    Args:
        findings: List of violations
        version: Lookout version

    Returns:
        SARIF JSON string
    """
    results = []
    rules = {}

    for finding in findings:
        # Register rule if not seen
        if finding.pattern_id not in rules:
            rules[finding.pattern_id] = {
                "id": finding.pattern_id,
                "shortDescription": {"text": finding.title},
                "properties": {
                    "category": finding.category,
                    "tier": finding.tier,
                },
            }

        # Create result
        results.append(
            {
                "ruleId": finding.pattern_id,
                "level": _map_severity_to_sarif(finding.severity),
                "baselineState": "unchanged" if finding.baselined else "new",
                "message": {"text": finding.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": finding.file},
                            "region": {
                                "startLine": finding.line,
                                "snippet": {"text": finding.snippet},
                            },
                        }
                    }
                ],
            }
        )

    sarif = {
        "$schema": "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Lookout",
                        "version": version,
                        "informationUri": "https://github.com/coreyt/lookout",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }

    return json.dumps(sarif, indent=2)


def _map_severity_to_sarif(severity: str) -> str:
    mapping = {
        "error": "error",
        "warning": "warning",
        "info": "note",
    }
    return mapping.get(severity.lower(), "warning")
