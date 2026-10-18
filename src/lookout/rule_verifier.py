"""Verify a generated RuleCheck against a pattern's examples with real Semgrep.

``lookout promote --generate-check`` asks an LLM for a Semgrep check. Schema
validation alone only proves the JSON has the right shape; it says nothing
about whether the rule parses, matches the bad examples, or stays quiet on the
good ones. This module closes that gap: the check is compiled with
:func:`lookout.semgrep.compile_semgrep_config`, the pattern's good/bad examples
are written to a temp directory, Semgrep is run for real, and the outcome is
summarised in a :class:`VerificationReport`.

Enforcement semantics apply to every check type: each bad example must produce
at least one finding and no good example may produce any. A rule that is
promoted is an enforcement rule, so a ``pattern_discovery`` check is held to
the same bar here even though ``lookout check`` reports its hits as instances.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lookout.discovery.models import ValidatedPattern
from lookout.models import RuleCheck

#: File extension used when an example snippet is written to disk for Semgrep.
EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "typescript": ".ts",
    "javascript": ".js",
    "go": ".go",
    "java": ".java",
    "ruby": ".rb",
    "rust": ".rs",
    "kotlin": ".kt",
    "csharp": ".cs",
    "php": ".php",
    "scala": ".scala",
}

SemgrepRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass
class ExampleOutcome:
    """One example snippet and what Semgrep reported for it."""

    kind: str  # "good" | "bad"
    index: int
    language: str
    code: str
    matches: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    """Outcome of running a candidate check against a pattern's examples.

    ``passed`` is true only when Semgrep ran cleanly, every bad example was
    flagged and no good example was. A pattern with no examples at all passes
    vacuously; ``notes`` says so.
    """

    rule_errors: list[str] = field(default_factory=list)
    unmatched_bad: list[ExampleOutcome] = field(default_factory=list)
    matched_good: list[ExampleOutcome] = field(default_factory=list)
    skipped: list[ExampleOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    checked_bad: int = 0
    checked_good: int = 0

    @property
    def passed(self) -> bool:
        return not (self.rule_errors or self.unmatched_bad or self.matched_good)

    def format(self) -> str:
        """Human-readable summary, also appended to LLM retry prompts."""
        lines: list[str] = []
        if self.passed:
            lines.append(
                f"Verification passed: {self.checked_bad} bad example(s) flagged, "
                f"{self.checked_good} good example(s) clean."
            )
        else:
            lines.append("Verification failed.")
        if self.rule_errors:
            lines.append("")
            lines.append("Semgrep rule errors:")
            lines.extend(f"  - {err}" for err in self.rule_errors)
        if self.unmatched_bad:
            lines.append("")
            lines.append(
                f"{len(self.unmatched_bad)} bad example(s) were NOT matched "
                "(the rule must flag these):"
            )
            for ex in self.unmatched_bad:
                lines.append(f"  bad example #{ex.index + 1} ({ex.language}):")
                lines.extend(f"    {line}" for line in ex.code.splitlines())
        if self.matched_good:
            lines.append("")
            lines.append(
                f"{len(self.matched_good)} good example(s) WERE matched "
                "(the rule must not flag these):"
            )
            for ex in self.matched_good:
                lines.append(f"  good example #{ex.index + 1} ({ex.language}):")
                lines.extend(f"    {line}" for line in ex.code.splitlines())
                if ex.matches:
                    lines.append("    matched lines:")
                    lines.extend(f"      {m}" for m in ex.matches)
        if self.skipped:
            lines.append("")
            lines.append(
                f"{len(self.skipped)} example(s) skipped (no file extension known for language):"
            )
            lines.extend(
                f"  - {ex.kind} example #{ex.index + 1} ({ex.language})" for ex in self.skipped
            )
        for note in self.notes:
            lines.append("")
            lines.append(f"Note: {note}")
        return "\n".join(lines)


def semgrep_available() -> bool:
    return shutil.which("semgrep") is not None


def _default_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def verify_check(
    check: RuleCheck,
    pattern: ValidatedPattern,
    *,
    runner: SemgrepRunner | None = None,
) -> VerificationReport:
    """Compile ``check`` for ``pattern`` and run it against the pattern's examples.

    Args:
        check: Candidate check (typically LLM-generated).
        pattern: Validated pattern supplying id, languages and good/bad examples.
        runner: Executes the Semgrep command; injectable for tests. Defaults to
            :func:`subprocess.run`.

    Returns:
        A :class:`VerificationReport`. Never raises for Semgrep failures; a
        missing binary or non-zero exit is reported under ``rule_errors``.
    """
    from lookout.promotion import promote_to_spec
    from lookout.semgrep import build_semgrep_command, compile_semgrep_config

    report = VerificationReport()
    run = runner or _default_runner
    discovered = pattern.discovered

    outcomes: list[ExampleOutcome] = []
    for kind in ("good", "bad"):
        for index, example in enumerate(getattr(discovered.examples, kind)):
            outcome = ExampleOutcome(kind, index, example.language, example.code)
            if example.language not in EXTENSIONS:
                report.skipped.append(outcome)
            else:
                outcomes.append(outcome)

    if not outcomes:
        report.notes.append(
            "pattern has no runnable examples; the check could not be exercised against code."
        )

    spec = promote_to_spec(pattern, check_override=check)
    try:
        config = compile_semgrep_config([spec])
    except Exception as exc:  # compile errors (e.g. invalid raw YAML)
        report.rule_errors.append(f"failed to compile check: {exc}")
        return report
    if not config["rules"]:
        report.rule_errors.append("check compiled to zero Semgrep rules")
        return report
    # Examples exercise the pattern itself, not where it applies: temp files
    # would never satisfy a `paths.include` glob.
    for rule in config["rules"]:
        rule.pop("paths", None)

    if not outcomes:
        return report

    if runner is None and not semgrep_available():
        report.rule_errors.append("semgrep binary not found on PATH; cannot verify the rule")
        return report

    with tempfile.TemporaryDirectory(prefix="lookout-verify-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "rule.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

        targets: list[Path] = []
        by_name: dict[str, ExampleOutcome] = {}
        for outcome in outcomes:
            name = f"{outcome.kind}_{outcome.index}{EXTENSIONS[outcome.language]}"
            target = tmp_path / name
            target.write_text(outcome.code, encoding="utf-8")
            targets.append(target)
            by_name[name] = outcome

        try:
            proc = run(build_semgrep_command(config_path, targets, no_git_ignore=True))
        except OSError as exc:
            report.rule_errors.append(f"could not run semgrep: {exc}")
            return report

        if proc.returncode not in (0, 1):
            stderr = (proc.stderr or "").strip()
            report.rule_errors.append(
                f"semgrep exited with code {proc.returncode}" + (f": {stderr}" if stderr else "")
            )
            if not proc.stdout.strip():
                return report

        try:
            data: dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report.rule_errors.append(
                "semgrep produced no parseable JSON output"
                + (f": {proc.stderr.strip()}" if proc.stderr and proc.stderr.strip() else "")
            )
            return report

        for err in data.get("errors", []):
            report.rule_errors.append(_describe_error(err))

        for result in data.get("results", []):
            name = Path(str(result.get("path", ""))).name
            hit = by_name.get(name)
            if hit is None:
                continue
            line = result.get("start", {}).get("line")
            snippet = str(result.get("extra", {}).get("lines", "")).strip()
            hit.matches.append(f"line {line}: {snippet}" if line else snippet)

    for outcome in outcomes:
        if outcome.kind == "bad":
            report.checked_bad += 1
            if not outcome.matches:
                report.unmatched_bad.append(outcome)
        else:
            report.checked_good += 1
            if outcome.matches:
                report.matched_good.append(outcome)

    return report


def _describe_error(err: Any) -> str:
    if not isinstance(err, dict):
        return str(err)
    parts = [str(err.get("type") or "error")]
    message = err.get("message") or err.get("long_msg") or err.get("short_msg")
    if message:
        parts.append(str(message).strip())
    return ": ".join(parts)
