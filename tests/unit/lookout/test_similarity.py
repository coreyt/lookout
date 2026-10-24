"""Tests for the rule-similarity checker (synthetic corpus only; no registry rules)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from lookout.cli import cli
from lookout.rules_loader import load_built_in_patterns
from lookout.semgrep import compile_semgrep_config
from lookout.similarity import compare, load_corpus, lookout_rules, rule_text

# A distinctive multi-part rule standing in for a third-party registry rule.
REFERENCE = {
    "id": "ref-insecure-session-cookie",
    "languages": ["python"],
    "severity": "WARNING",
    "message": "Session cookie is set without the secure flag; set secure=True so it "
    "is never sent over plain HTTP connections.",
    "patterns": [
        {"pattern-inside": "def $HANDLER($REQ, ...):\n  ...\n"},
        {"pattern": "$RESP.set_cookie($NAME, $VALUE, ..., secure=False, ...)"},
        {"pattern-not-inside": "if $SETTINGS.DEBUG:\n  ...\n"},
    ],
}


def _write_corpus(root: Path, *rules: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "rules.yaml").write_text(yaml.safe_dump({"rules": list(rules)}))
    return root


def _renamed_copy() -> dict:
    """Same rule with metavariables renamed and a new id/message: still a copy."""
    return {
        "id": "ours-cookie",
        "languages": ["python"],
        "message": "Our own wording about cookies.",
        "patterns": [
            {"pattern-inside": "def $VIEW($R, ...):\n  ...\n"},
            {"pattern": "$OUT.set_cookie($K, $V, ..., secure=False, ...)"},
            {"pattern-not-inside": "if $CONF.DEBUG:\n  ...\n"},
        ],
    }


def test_renamed_metavariables_do_not_hide_a_copy(tmp_path: Path) -> None:
    corpus = load_corpus(_write_corpus(tmp_path / "c", REFERENCE))
    [match] = compare([rule_text("OURS-001", _renamed_copy())], corpus)

    assert match.reference is not None
    assert match.reference.rule_id == "ref-insecure-session-cookie"
    assert match.pattern_score == 1.0
    assert match.shared_patterns  # identical after canonicalization
    assert match.flagged(0.7)


def test_copying_part_of_a_larger_rule_is_flagged(tmp_path: Path) -> None:
    corpus = load_corpus(_write_corpus(tmp_path / "c", REFERENCE))
    partial = {
        "id": "partial",
        "languages": ["python"],
        "message": "x",
        "patterns": [
            {"pattern-inside": "def $F($A, ...):\n  ...\n"},
            {"pattern": "$X.set_cookie($Y, $Z, ..., secure=False, ...)"},
        ],
    }

    [match] = compare([rule_text("OURS-002", partial)], corpus)

    assert match.pattern_score == 1.0  # containment of ours in theirs, not Jaccard
    assert match.flagged(0.7)


def test_copied_message_is_flagged(tmp_path: Path) -> None:
    corpus = load_corpus(_write_corpus(tmp_path / "c", REFERENCE))
    rule = {
        "id": "msg",
        "languages": ["python"],
        "message": REFERENCE["message"],
        "pattern": "foo(...)",
    }

    [match] = compare([rule_text("OURS-003", rule)], corpus)

    assert match.message_score == 1.0
    assert match.flagged(0.7)


def test_unrelated_rule_is_not_flagged(tmp_path: Path) -> None:
    corpus = load_corpus(_write_corpus(tmp_path / "c", REFERENCE))
    rule = {
        "id": "unrelated",
        "languages": ["python"],
        "message": "Database sessions belong in repository classes, not services.",
        "patterns": [
            {"pattern-not-inside": "class $REPO:\n  ...\n"},
            {"pattern": "$SESSION.query($MODEL).filter($COND).all()"},
        ],
    }

    [match] = compare([rule_text("OURS-004", rule)], corpus)

    assert not match.flagged(0.7)
    assert match.score < 0.3


def test_trivial_identical_pattern_is_not_evidence(tmp_path: Path) -> None:
    trivial_ref = {
        "id": "no-print",
        "languages": ["python"],
        "message": "no",
        "pattern": "print(...)",
    }
    corpus = load_corpus(_write_corpus(tmp_path / "c", trivial_ref))
    ours = {
        "id": "arch",
        "languages": ["python"],
        "message": "Use logging",
        "pattern": "print(...)",
    }

    [match] = compare([rule_text("OURS-005", ours)], corpus)

    assert match.too_small
    assert not match.flagged(0.7)


def test_languages_must_overlap(tmp_path: Path) -> None:
    ts_ref = {**REFERENCE, "languages": ["typescript"]}
    corpus = load_corpus(_write_corpus(tmp_path / "c", ts_ref))

    [match] = compare([rule_text("OURS-006", _renamed_copy())], corpus)

    assert match.reference is None
    assert not match.flagged(0.7)


def test_generic_language_rules_are_compared(tmp_path: Path) -> None:
    generic_ref = {**REFERENCE, "languages": ["generic"]}
    corpus = load_corpus(_write_corpus(tmp_path / "c", generic_ref))

    [match] = compare([rule_text("OURS-007", _renamed_copy())], corpus)

    assert match.flagged(0.7)


def test_load_corpus_skips_non_rule_and_hidden_files(tmp_path: Path) -> None:
    root = _write_corpus(tmp_path / "c", REFERENCE)
    (root / "broken.yaml").write_text("rules: [unclosed\n")
    (root / "config.yml").write_text("name: not-a-rule-file\n")
    (root / ".github").mkdir()
    (root / ".github" / "rules.yaml").write_text(yaml.safe_dump({"rules": [_renamed_copy()]}))
    (root / "nested").mkdir()
    (root / "nested" / "more.yml").write_text(yaml.safe_dump({"rules": [{**REFERENCE, "id": "x"}]}))

    corpus = load_corpus(root)

    assert sorted(r.rule_id for r in corpus) == ["ref-insecure-session-cookie", "x"]
    assert {r.origin for r in corpus} == {"rules.yaml", str(Path("nested") / "more.yml")}


def test_built_in_rules_are_detected_against_themselves(tmp_path: Path) -> None:
    """Sanity check on real rules: every non-trivial built-in matches its own copy."""
    specs = load_built_in_patterns()
    ours = lookout_rules(specs)
    raw_rules = [rule for spec in specs for rule in compile_semgrep_config([spec])["rules"]]
    corpus = load_corpus(_write_corpus(tmp_path / "c", *raw_rules))

    matches = compare(ours, corpus)

    for match in matches:
        assert match.too_small or match.flagged(0.7), match.rule.rule_id
    assert sum(not m.too_small for m in matches) >= 5


class TestCli:
    def _project(self, tmp_path: Path, user_rule: dict | None) -> Path:
        project = tmp_path / "project"
        rules_dir = project / ".lookout-rules"
        rules_dir.mkdir(parents=True)
        if user_rule is not None:
            spec = {
                "schema_version": 1,
                "rule": {
                    "id": "USER-001",
                    "title": "t",
                    "description": "d",
                    "rationale": "r",
                    "tier": 2,
                    "category": "security",
                    "severity": "warning",
                    "languages": ["python"],
                    "check": {"type": "raw", "config": yaml.safe_dump({"rules": [user_rule]})},
                    "reporting": {"default_message": "m", "confidence": "high"},
                    "examples": {"good": [], "bad": []},
                    "references": [],
                },
            }
            (rules_dir / "USER-001.yaml").write_text(yaml.safe_dump(spec))
        return project

    def test_exits_1_when_a_rule_is_flagged(self, tmp_path: Path, monkeypatch) -> None:
        corpus = _write_corpus(tmp_path / "corpus", REFERENCE)
        monkeypatch.chdir(self._project(tmp_path, {**_renamed_copy(), "id": "user-001-impl"}))

        result = CliRunner().invoke(cli, ["similarity", "--corpus", str(corpus)])

        assert result.exit_code == 1, result.output
        assert "FLAG  USER-001" in result.output
        assert "ref-insecure-session-cookie" in result.output

    def test_built_ins_alone_pass_against_unrelated_corpus(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        corpus = _write_corpus(tmp_path / "corpus", REFERENCE)
        monkeypatch.chdir(self._project(tmp_path, None))

        result = CliRunner().invoke(
            cli, ["similarity", "--corpus", str(corpus), "--format", "json"]
        )

        assert result.exit_code == 0, result.output
        report = json.loads(result.output)
        assert report and not any(entry["flagged"] for entry in report)
        assert {"pattern_id", "pattern_score", "message_score", "reference"} <= set(report[0])

    def test_empty_corpus_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "README.md").write_text("no rules here")
        monkeypatch.chdir(self._project(tmp_path, None))

        result = CliRunner().invoke(cli, ["similarity", "--corpus", str(empty)])

        assert result.exit_code == 2
