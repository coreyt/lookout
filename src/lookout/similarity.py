"""Rule-similarity checker.

Flags Lookout rules that closely resemble rules in a reference corpus, such as a
local clone of the Semgrep Registry (https://github.com/semgrep/semgrep-rules),
whose rules are under the Semgrep Rules License rather than an open-source one.
The corpus is read from disk at check time; it is never stored in this repository.

For each Lookout rule we measure how much of *our* rule appears in the closest
reference rule (containment, not Jaccard, so copying part of a large rule still
scores high):

- pattern score: share of our pattern token 4-grams found in the reference rule,
  after canonicalizing metavariable names ($X, $FOO -> $1, $2 ...) so renaming
  them does not hide a copy;
- message score: share of our message word 3-grams found in the reference message;
- shared patterns: identical non-trivial patterns present in both.

Rules too small to be distinctive (e.g. ``print(...)``) are reported but never
flagged: a trivial pattern matching a registry rule is not a copy.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lookout.semgrep import compile_semgrep_config, get_spec_id
from lookout.specs import SpecUnion

PATTERN_SHINGLE = 4
MESSAGE_SHINGLE = 3
#: Below these sizes a rule is too generic to judge (score reported as 0).
MIN_PATTERN_SHINGLES = 6
MIN_MESSAGE_SHINGLES = 5
#: Identical patterns shorter than this many tokens are not evidence of copying.
MIN_SHARED_PATTERN_TOKENS = 12
DEFAULT_THRESHOLD = 0.7

_TOKEN_RE = re.compile(
    r"\$\.\.\.[A-Z_][A-Z0-9_]*|\$[A-Z_][A-Z0-9_]*|\.\.\.|[A-Za-z_][A-Za-z0-9_]*|\d+|\S"
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_WILDCARD_LANGUAGES = {"generic", "regex", "none"}

try:  # the C loader is ~10x faster on a corpus of thousands of files
    _Loader: Any = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - depends on libyaml availability
    _Loader = yaml.SafeLoader


@dataclass(frozen=True)
class RuleText:
    """The comparable content of one Semgrep rule."""

    origin: str  # Lookout pattern ID, or corpus file path
    rule_id: str
    languages: frozenset[str]
    patterns: tuple[str, ...]  # canonicalized pattern strings
    message: str
    pattern_shingles: frozenset[tuple[str, ...]] = field(repr=False)
    message_shingles: frozenset[tuple[str, ...]] = field(repr=False)


@dataclass(frozen=True)
class Match:
    rule: RuleText
    reference: RuleText | None
    pattern_score: float
    message_score: float
    shared_patterns: tuple[str, ...]
    too_small: bool

    @property
    def score(self) -> float:
        return max(self.pattern_score, self.message_score)

    def flagged(self, threshold: float) -> bool:
        return bool(self.shared_patterns) or self.score >= threshold


def rule_text(origin: str, rule: dict[str, Any]) -> RuleText:
    """Extract and canonicalize the comparable parts of a Semgrep rule dict."""
    names: dict[str, str] = {}
    patterns = tuple(_canonical(p, names) for p in _pattern_strings(rule))
    message = str(rule.get("message", ""))
    return RuleText(
        origin=origin,
        rule_id=str(rule.get("id", "")),
        languages=frozenset(str(lang).lower() for lang in rule.get("languages", []) or []),
        patterns=patterns,
        message=message,
        pattern_shingles=frozenset(
            s for p in patterns for s in _shingles(p.split(), PATTERN_SHINGLE)
        ),
        message_shingles=frozenset(_shingles(_WORD_RE.findall(message.lower()), MESSAGE_SHINGLE)),
    )


def lookout_rules(specs: Iterable[SpecUnion]) -> list[RuleText]:
    """Compile each spec and extract its rules, tagged with the spec ID."""
    return [
        rule_text(get_spec_id(spec), rule)
        for spec in specs
        for rule in compile_semgrep_config([spec])["rules"]
    ]


def load_corpus(root: Path) -> list[RuleText]:
    """Load every Semgrep rule found in YAML files under ``root``; skip anything else."""
    rules: list[RuleText] = []
    for path in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            data = yaml.load(path.read_text(encoding="utf-8", errors="replace"), Loader=_Loader)
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            continue
        origin = str(path.relative_to(root))
        rules.extend(rule_text(origin, r) for r in data["rules"] if isinstance(r, dict))
    return rules


def compare(rules: list[RuleText], corpus: list[RuleText]) -> list[Match]:
    """Return the closest corpus match for each Lookout rule."""
    index: dict[tuple[str, ...], set[int]] = defaultdict(set)
    for i, ref in enumerate(corpus):
        for shingle in ref.pattern_shingles | ref.message_shingles:
            index[shingle].add(i)
    corpus_patterns: dict[str, set[int]] = defaultdict(set)
    for i, ref in enumerate(corpus):
        for p in ref.patterns:
            if len(p.split()) >= MIN_SHARED_PATTERN_TOKENS:
                corpus_patterns[p].add(i)

    matches: list[Match] = []
    for rule in rules:
        too_small = (
            len(rule.pattern_shingles) < MIN_PATTERN_SHINGLES
            and len(rule.message_shingles) < MIN_MESSAGE_SHINGLES
        )
        candidates: set[int] = set()
        for shingle in rule.pattern_shingles | rule.message_shingles:
            candidates |= index.get(shingle, set())
        best: Match = Match(rule, None, 0.0, 0.0, (), too_small)
        for i in candidates:
            ref = corpus[i]
            if not _languages_overlap(rule.languages, ref.languages):
                continue
            shared = tuple(p for p in rule.patterns if i in corpus_patterns.get(p, ()))
            candidate = Match(
                rule,
                ref,
                _containment(rule.pattern_shingles, ref.pattern_shingles, MIN_PATTERN_SHINGLES),
                _containment(rule.message_shingles, ref.message_shingles, MIN_MESSAGE_SHINGLES),
                shared,
                too_small,
            )
            if (len(candidate.shared_patterns), candidate.score) > (
                len(best.shared_patterns),
                best.score,
            ):
                best = candidate
        matches.append(best)
    return matches


def _pattern_strings(node: Any) -> Iterator[str]:
    """Yield every pattern/regex string in a rule, in document order."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (key.startswith("pattern") or key == "regex"):
                yield value
            elif key not in ("message", "metadata", "id", "languages", "severity", "paths"):
                yield from _pattern_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _pattern_strings(item)


def _canonical(pattern: str, names: dict[str, str]) -> str:
    """Tokenize a pattern and rename metavariables by first appearance (shared per rule)."""
    out: list[str] = []
    for token in _TOKEN_RE.findall(pattern):
        if token.startswith("$") and token != "$":
            prefix = "$..." if token.startswith("$...") else "$"
            token = names.setdefault(token, f"{prefix}{len(names) + 1}")
        out.append(token)
    return " ".join(out)


def _shingles(tokens: list[str], k: int) -> Iterator[tuple[str, ...]]:
    if len(tokens) < k:
        if tokens:
            yield tuple(tokens)
        return
    for i in range(len(tokens) - k + 1):
        yield tuple(tokens[i : i + k])


def _containment(ours: frozenset[Any], theirs: frozenset[Any], minimum: int) -> float:
    if len(ours) < minimum:
        return 0.0
    return len(ours & theirs) / len(ours)


def _languages_overlap(a: frozenset[str], b: frozenset[str]) -> bool:
    return not a or not b or bool(a & b) or bool((a | b) & _WILDCARD_LANGUAGES)


def format_report(matches: list[Match], corpus_size: int, threshold: float) -> str:
    lines = [f"Compared {len(matches)} Lookout rule(s) against {corpus_size} reference rule(s)."]
    for m in sorted(matches, key=lambda m: (-len(m.shared_patterns), -m.score)):
        status = "FLAG " if m.flagged(threshold) else "small" if m.too_small else "ok   "
        where = f"{m.reference.origin}:{m.reference.rule_id}" if m.reference else "-"
        lines.append(
            f"{status} {m.rule.origin:<18} {m.rule.rule_id:<40} "
            f"pattern {m.pattern_score:.2f}  message {m.message_score:.2f}  ~ {where}"
        )
        for shared in m.shared_patterns:
            lines.append(f"        identical pattern: {shared[:100]}")
    flagged = sum(1 for m in matches if m.flagged(threshold))
    lines.append(f"\n{flagged} rule(s) at or above threshold {threshold:.2f}.")
    return "\n".join(lines)


def report_json(matches: list[Match], threshold: float) -> list[dict[str, Any]]:
    return [
        {
            "pattern_id": m.rule.origin,
            "rule_id": m.rule.rule_id,
            "pattern_score": round(m.pattern_score, 3),
            "message_score": round(m.message_score, 3),
            "shared_patterns": list(m.shared_patterns),
            "too_small": m.too_small,
            "flagged": m.flagged(threshold),
            "reference": (
                {"file": m.reference.origin, "rule_id": m.reference.rule_id}
                if m.reference
                else None
            ),
        }
        for m in matches
    ]
