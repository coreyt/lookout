from __future__ import annotations

import json

from lookout.models import (
    ForbiddenCheck,
    FrameworkVariant,
    GenericVariant,
    LanguageVariant,
    MustWrapCheck,
    PathScope,
    PatternSpec,
    PatternSpecFile,
    RawCheck,
    RequiredWithCheck,
    RuleCheck,
    RuleExamples,
    RuleReporting,
    RuleSpec,
    RuleSpecFile,
    VariantExamples,
)
from lookout.semgrep import build_semgrep_command, compile_semgrep_config, parse_semgrep_output


def _spec_for(check: RuleCheck) -> RuleSpecFile:
    return RuleSpecFile(
        schema_version=1,
        rule=RuleSpec(
            id="ARCH-001",
            title="HTTP calls require resilience wrappers",
            description="Desc",
            rationale="Because",
            tier=1,
            category="resilience",
            severity="error",
            languages=["python"],
            check=check,
            reporting=RuleReporting(
                default_message="HTTP call outside resilience wrapper (ARCH-001)",
                confidence="high",
                documentation_url=None,
            ),
            examples=RuleExamples(good=[], bad=[]),
            references=[],
        ),
    )


def test_compile_must_wrap() -> None:
    spec = _spec_for(
        MustWrapCheck(
            type="must_wrap",
            target=["requests.get"],
            wrapper=["circuit_breaker", "@resilient"],
        )
    )
    config = compile_semgrep_config([spec])
    rule = config["rules"][0]

    assert "arch-001" in rule["id"]
    assert "ARCH-001" in rule["message"]
    assert rule["languages"] == ["python"]
    assert any("pattern-not-inside" in entry for entry in rule["patterns"])


def test_compile_required_with() -> None:
    spec = _spec_for(
        RequiredWithCheck(
            type="required_with",
            if_present="@service",
            must_have="@health_check",
        )
    )
    config = compile_semgrep_config([spec])
    rule = config["rules"][0]
    patterns = [entry for entry in rule["patterns"] if "pattern-not-inside" in entry]

    assert len(patterns) == 2


def test_parse_semgrep_output_normalizes_string_snippet() -> None:
    spec = _spec_for(ForbiddenCheck(type="forbidden", pattern="eval($X)"))
    output = json.dumps(
        {
            "results": [
                {
                    "check_id": "arch-001-impl",
                    "path": "src/app.py",
                    "start": {"line": 12},
                    "extra": {
                        "message": "HTTP call outside resilience wrapper (ARCH-001)",
                        "lines": "requests.get(url)\n",
                    },
                }
            ]
        }
    )

    findings, pattern_instances, errors = parse_semgrep_output(output, {"ARCH-001": spec})
    assert len(findings) == 1
    assert findings[0].snippet == "requests.get(url)"


def test_parse_semgrep_output_maps_multi_segment_pattern_ids() -> None:
    spec = RuleSpecFile(
        schema_version=1,
        rule=RuleSpec(
            id="PY-SEC-001",
            title="No eval",
            description="Desc",
            rationale="Because",
            tier=1,
            category="security",
            severity="error",
            languages=["python"],
            check=ForbiddenCheck(type="forbidden", pattern="eval($X)"),
            reporting=RuleReporting(
                default_message="No eval (PY-SEC-001)",
                confidence="high",
                documentation_url=None,
            ),
            examples=RuleExamples(good=[], bad=[]),
            references=[],
        ),
    )
    output = json.dumps(
        {
            "results": [
                {
                    "check_id": "py-sec-001-impl",
                    "path": "src/app.py",
                    "start": {"line": 3},
                    "extra": {"message": "No eval (PY-SEC-001)", "lines": ["eval(x)"]},
                }
            ]
        }
    )

    findings, pattern_instances, errors = parse_semgrep_output(output, {"PY-SEC-001": spec})
    assert len(findings) == 1
    assert findings[0].pattern_id == "PY-SEC-001"


def _pattern_with_paths(
    pattern_paths: PathScope | None,
    generic_paths: PathScope | None = None,
    framework_paths: PathScope | None = None,
    raw: bool = False,
) -> PatternSpecFile:
    generic_check: RuleCheck
    if raw:
        generic_check = RawCheck(
            type="raw",
            config=(
                "rules:\n"
                "  - id: arch-900-python-impl\n"
                "    languages: [python]\n"
                "    severity: WARNING\n"
                "    message: raw\n"
                "    pattern: print(...)\n"
            ),
        )
    else:
        generic_check = ForbiddenCheck(type="forbidden", pattern="print(...)")
    return PatternSpecFile(
        pattern=PatternSpec(
            id="ARCH-900",
            title="t",
            description="d",
            rationale="r",
            tier=1,
            category="c",
            severity="warning",
            reporting=RuleReporting(default_message="m", confidence="high"),
            paths=pattern_paths,
            variants=[
                LanguageVariant(
                    language="python",
                    generic=GenericVariant(
                        check=generic_check, paths=generic_paths, examples=VariantExamples()
                    ),
                    frameworks=[
                        FrameworkVariant(
                            name="django",
                            check=ForbiddenCheck(type="forbidden", pattern="x(...)"),
                            paths=framework_paths,
                            examples=VariantExamples(),
                        )
                    ],
                )
            ],
        )
    )


def test_pattern_level_paths_apply_to_every_variant() -> None:
    spec = _pattern_with_paths(PathScope(include=["**/controllers/**"], exclude=["tests/**"]))

    rules = compile_semgrep_config([spec])["rules"]

    assert [r["id"] for r in rules] == ["arch-900-python-impl", "arch-900-python-django-impl"]
    for rule in rules:
        assert rule["paths"] == {"include": ["**/controllers/**"], "exclude": ["tests/**"]}


def test_variant_paths_override_include_and_accumulate_exclude() -> None:
    spec = _pattern_with_paths(
        PathScope(include=["src/**"], exclude=["tests/**"]),
        generic_paths=PathScope(include=["src/api/**"], exclude=["src/api/legacy/**"]),
    )

    rules = compile_semgrep_config([spec])["rules"]

    generic, framework = rules
    assert generic["paths"] == {
        "include": ["src/api/**"],
        "exclude": ["tests/**", "src/api/legacy/**"],
    }
    assert framework["paths"] == {"include": ["src/**"], "exclude": ["tests/**"]}


def test_paths_applied_to_raw_rules() -> None:
    spec = _pattern_with_paths(PathScope(exclude=["scripts/**"]), raw=True)

    rules = compile_semgrep_config([spec])["rules"]

    assert rules[0]["id"] == "arch-900-python-impl"
    assert rules[0]["paths"] == {"exclude": ["scripts/**"]}


def test_no_paths_block_when_unscoped() -> None:
    rules = compile_semgrep_config([_pattern_with_paths(None)])["rules"]

    assert all("paths" not in rule for rule in rules)


def test_global_exclude_added_to_every_rule_without_duplicates() -> None:
    spec = _pattern_with_paths(PathScope(exclude=["tests/**"]))

    rules = compile_semgrep_config([spec], global_exclude=["tests/**", "vendor/**"])["rules"]

    for rule in rules:
        assert rule["paths"]["exclude"] == ["tests/**", "vendor/**"]


def test_v1_rule_paths() -> None:
    spec = _spec_for(ForbiddenCheck(type="forbidden", pattern="eval(...)"))
    spec = spec.model_copy(
        update={"rule": spec.rule.model_copy(update={"paths": PathScope(exclude=["tests/**"])})}
    )

    rules = compile_semgrep_config([spec])["rules"]

    assert rules[0]["paths"] == {"exclude": ["tests/**"]}


def test_build_semgrep_command_no_git_ignore_flag() -> None:
    from pathlib import Path

    with_flag = build_semgrep_command(Path("c.yaml"), [Path("src")], no_git_ignore=True)
    without = build_semgrep_command(Path("c.yaml"), [Path("src")])

    assert "--no-git-ignore" in with_flag
    assert "--no-git-ignore" not in without
    assert with_flag[-1] == "src"
