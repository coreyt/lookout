from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

from lookout.baseline import BASELINE_PATH, Baseline, load_baseline, mark_baselined, write_baseline
from lookout.models import Finding, PatternInstance, RuleError
from lookout.semgrep import (
    build_semgrep_command,
    build_spec_index,
    compile_semgrep_config,
    parse_semgrep_output,
    render_dry_run,
)
from lookout.specs import SpecUnion, is_discovery_spec


def check_semgrep_version() -> str | None:
    try:
        result = subprocess.run(
            ["semgrep", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    return result.stdout.strip()


def targets_outside_git(targets: list[Path]) -> bool:
    """True if any target is not inside a git work tree (or git is unavailable)."""
    for target in targets:
        directory = target if target.is_dir() else target.parent
        try:
            result = subprocess.run(
                ["git", "-C", str(directory), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return True
        if result.returncode != 0 or result.stdout.strip() != "true":
            return True
    return False


def run_lookout(
    specs: list[SpecUnion],
    targets: list[Path],
    dry_run: bool = False,
    update_baseline: bool = False,
    discovery_only: bool = False,
    enforcement_only: bool = False,
    global_exclude: list[str] | None = None,
) -> tuple[list[Finding], list[Finding], list[PatternInstance], list[RuleError], str | None]:
    """Run Lookout checks (enforcement and/or discovery).

    Args:
        specs: List of rule/pattern specifications to check
        targets: Paths to analyze
        dry_run: If True, show compiled rules without running
        update_baseline: If True, update baseline with current findings
        discovery_only: If True, only run pattern discovery rules
        enforcement_only: If True, skip pattern discovery rules
        global_exclude: Path globs excluded from every compiled rule

    Returns:
        Tuple of (all_findings, new_findings, pattern_instances, errors, dry_run_output).
        ``all_findings`` carry ``baselined=True`` where the baseline already knows them.
    """
    if discovery_only:
        specs = [s for s in specs if is_discovery_spec(s)]
    elif enforcement_only:
        specs = [s for s in specs if not is_discovery_spec(s)]

    config = compile_semgrep_config(specs, global_exclude=global_exclude)
    with tempfile.TemporaryDirectory(prefix="lookout-") as temp_dir:
        config_path = Path(temp_dir) / "semgrep.yaml"
        if dry_run:
            dry_output = render_dry_run(config, config_path, targets)
            return [], [], [], [], dry_output

        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        command = build_semgrep_command(
            config_path, targets, no_git_ignore=targets_outside_git(targets)
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode not in (0, 1, 2):
            raise RuntimeError(
                f"Semgrep execution failed with code {result.returncode}:\n{result.stderr.strip()}"
            )

        spec_idx = build_spec_index(specs)
        findings, pattern_instances, errors = parse_semgrep_output(result.stdout, spec_idx)
        baseline = load_baseline(BASELINE_PATH)
        findings = mark_baselined(findings, baseline)
        new_findings = [f for f in findings if not f.baselined]

        if update_baseline:
            write_baseline(Baseline.from_findings(findings), BASELINE_PATH)
            return findings, [], pattern_instances, errors, None

        return findings, new_findings, pattern_instances, errors, None


def format_findings_text(findings: list[Finding]) -> str:
    if not findings:
        return "No violations found."
    lines = []
    for finding in findings:
        suffix = "  (baselined)" if finding.baselined else ""
        lines.append(
            f"{finding.file}:{finding.line} [{finding.pattern_id}] {finding.message}{suffix}"
        )
    baselined = sum(1 for f in findings if f.baselined)
    if baselined:
        lines.append("")
        lines.append(f"{baselined} of {len(findings)} finding(s) are in the baseline")
    return "\n".join(lines)


def format_findings_json(findings: list[Finding]) -> str:
    return json.dumps([finding.__dict__ for finding in findings], indent=2)


def format_pattern_instances_text(instances: list[PatternInstance]) -> str:
    """Format pattern instances for text output (discovery mode)."""
    if not instances:
        return "No patterns discovered."

    # Group by pattern ID
    by_pattern: dict[str, list[PatternInstance]] = {}
    for instance in instances:
        if instance.pattern_id not in by_pattern:
            by_pattern[instance.pattern_id] = []
        by_pattern[instance.pattern_id].append(instance)

    lines = ["", "Pattern Discovery Results", "=" * 60, ""]

    for pattern_id in sorted(by_pattern.keys()):
        pattern_instances = by_pattern[pattern_id]
        first = pattern_instances[0]
        lines.append(f"{pattern_id}: {first.title}")
        lines.append(f"  Category: {first.category}")
        lines.append(f"  Instances found: {len(pattern_instances)}")
        lines.append("")

        # Show first 5 instances
        for instance in pattern_instances[:5]:
            lines.append(f"  - {instance.file}:{instance.line}")

        if len(pattern_instances) > 5:
            lines.append(f"  ... and {len(pattern_instances) - 5} more")

        lines.append("")

    lines.append(f"Total: {len(instances)} pattern instances discovered")
    return "\n".join(lines)


def format_pattern_instances_json(instances: list[PatternInstance]) -> str:
    """Format pattern instances for JSON output."""
    return json.dumps([instance.__dict__ for instance in instances], indent=2)
