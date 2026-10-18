"""Run every built-in pattern's good/bad examples through real Semgrep.

For each built-in spec in ``src/lookout/patterns`` and each variant (generic and
per-framework), only that variant's rule is compiled and executed against each
example snippet. Enforcement specs must flag every bad example and leave every
good example clean. Discovery specs (all checks are ``pattern_discovery``) are
descriptive, so their good examples must be *matched* and their bad examples
must not be.

Semgrep is invoked once per variant over all of its example files and the
results are cached, so each parametrized case stays cheap.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml

from lookout.models import FrameworkVariant, GenericVariant, PatternSpecFile, RuleExample
from lookout.rules_loader import load_built_in_patterns
from lookout.semgrep import build_semgrep_command, compile_semgrep_config
from lookout.specs import is_discovery_spec

pytestmark = pytest.mark.integration

SEMGREP = shutil.which("semgrep")

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


@dataclass(frozen=True)
class ExampleCase:
    spec: PatternSpecFile
    variant_language: str
    framework: str | None  # None == generic variant
    kind: str  # "good" | "bad"
    index: int
    example: RuleExample

    @property
    def pattern_id(self) -> str:
        return self.spec.pattern.id

    @property
    def variant_key(self) -> tuple[str, str, str | None]:
        return (self.pattern_id, self.variant_language, self.framework)

    @property
    def test_id(self) -> str:
        variant = self.framework or "generic"
        return f"{self.pattern_id}-{self.variant_language}-{variant}-{self.kind}{self.index}"

    @property
    def file_name(self) -> str:
        return f"{self.kind}_{self.index}{EXTENSIONS[self.example.language]}"


def _single_variant_spec(
    spec: PatternSpecFile, language: str, framework: str | None
) -> PatternSpecFile:
    """Return a copy of ``spec`` holding only one variant check."""
    pruned = spec.model_copy(deep=True)
    kept = []
    for variant in pruned.pattern.variants:
        if variant.language != language:
            continue
        if framework is None:
            variant.frameworks = []
            if variant.generic is None:
                continue
        else:
            variant.generic = None
            variant.frameworks = [fw for fw in variant.frameworks if fw.name == framework]
            if not variant.frameworks:
                continue
        kept.append(variant)
    assert kept, f"variant {language}/{framework} not found in {spec.pattern.id}"
    pruned.pattern.variants = kept
    return pruned


def _collect_cases() -> list[ExampleCase]:
    cases: list[ExampleCase] = []
    for spec in load_built_in_patterns():
        if not isinstance(spec, PatternSpecFile):
            continue
        for variant in spec.pattern.variants:
            holders: list[tuple[str | None, GenericVariant | FrameworkVariant]] = []
            if variant.generic is not None:
                holders.append((None, variant.generic))
            holders.extend((fw.name, fw) for fw in variant.frameworks)
            for framework, holder in holders:
                for kind in ("good", "bad"):
                    for index, example in enumerate(getattr(holder.examples, kind)):
                        cases.append(
                            ExampleCase(spec, variant.language, framework, kind, index, example)
                        )
    return cases


CASES = _collect_cases()
CASES_BY_VARIANT: dict[tuple[str, str, str | None], list[ExampleCase]] = {}
for _case in CASES:
    CASES_BY_VARIANT.setdefault(_case.variant_key, []).append(_case)


@cache
def _run_variant(variant_key: tuple[str, str, str | None]) -> dict[str, list[dict[str, Any]]]:
    """Run Semgrep once for a variant; return results keyed by example file name."""
    cases = CASES_BY_VARIANT[variant_key]
    pattern_id, language, framework = variant_key
    spec = _single_variant_spec(cases[0].spec, language, framework)
    config = compile_semgrep_config([spec])
    assert config["rules"], f"{pattern_id} {language}/{framework} compiled to no rules"

    with tempfile.TemporaryDirectory(prefix="lookout-examples-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "rule.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        targets: list[Path] = []
        for case in cases:
            target = tmp_path / case.file_name
            target.write_text(case.example.code, encoding="utf-8")
            targets.append(target)

        proc = subprocess.run(
            build_semgrep_command(config_path, targets),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode in (0, 1), f"semgrep failed ({proc.returncode}):\n{proc.stderr}"
        data = json.loads(proc.stdout)
        assert not data.get("errors"), (
            f"semgrep reported errors for {pattern_id} {language}/{framework}:\n"
            f"{json.dumps(data['errors'], indent=2)}\n--- compiled rule ---\n"
            f"{config_path.read_text()}"
        )

        by_file: dict[str, list[dict[str, Any]]] = {t.name: [] for t in targets}
        for result in data.get("results", []):
            check_id = str(result.get("check_id", "")).rsplit(".", 1)[-1]
            assert check_id.startswith(pattern_id.lower()), (
                f"unexpected rule id {check_id!r} for {pattern_id}"
            )
            by_file[Path(str(result["path"])).name].append(result)
        return by_file


def _describe(results: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"  line {r.get('start', {}).get('line')}: {r.get('extra', {}).get('lines', '')!s}"
        for r in results
    )


@pytest.mark.skipif(SEMGREP is None, reason="semgrep binary not found on PATH")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.test_id)
def test_pattern_example(case: ExampleCase) -> None:
    if case.example.language not in EXTENSIONS:
        pytest.skip(f"no file extension known for language {case.example.language!r}")

    results = _run_variant(case.variant_key)[case.file_name]
    discovery = is_discovery_spec(case.spec)
    should_match = (case.kind == "bad") != discovery
    label = "match" if discovery else "flag"

    if should_match:
        assert results, (
            f"[{case.test_id}] expected the rule to {label} this {case.kind} example, "
            f"but Semgrep returned nothing.\nCode:\n{case.example.code}"
        )
    else:
        assert not results, (
            f"[{case.test_id}] expected no {label} on this {case.kind} example, "
            f"but Semgrep returned {len(results)} result(s):\n{_describe(results)}\n"
            f"Code:\n{case.example.code}"
        )
