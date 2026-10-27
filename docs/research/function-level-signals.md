# Research: function-level understandability, conflicting code, dependency chains, side effects

- **Date:** 2026-10-27
- **Status:** research input for planning; not a design decision
- **Method:** literature and tool survey (web search, GitHub/PyPI/crates/npm
  metadata checked 2026-10-27, two arXiv PDFs read for figures). Claims listed
  under "Not verified" at the end were not confirmed.

---

## Part A: Understandability

### A1. Metrics and how well they predict human understanding

The overall evidence is weak. Scalabrino et al. (TSE 2019) tested 121 metrics
against 444 evaluations from 63 developers: no single metric captured
understandability, and combining them gave only modest gains. Trockman et al.
(MSR 2018) reanalysed the data and found small but significant signal in
combined syntactic and documentation features, with limited discriminating
power. Feitelson (CACM 2023) argues a comprehensive metric may be impossible
because the reader matters as much as the code.

- **Cognitive complexity (Campbell/SonarSource 2018)** has the best direct
  evidence. Muñoz Barón, Wyrich and Wagner (ESEM 2020) meta-analysed 10 studies,
  427 snippets and about 24k evaluations. Pooled correlations:
  - comprehension time: r = 0.54 [0.24, 0.75]
  - subjective difficulty rating: r = −0.29 [−0.49, −0.06]
  - correctness: r = −0.13 [−0.45, 0.21], not significant
  - physiological measures: r = 0.00

  Counterweights: Lavazza et al. (2023) found it predicts about as well as
  cyclomatic complexity and LOC, no better; Lenarduzzi et al. found it only
  slightly better than cyclomatic complexity for junior developers.
- **Cyclomatic complexity (McCabe):** weak to moderate, largely through size.
  Counts every switch/match arm, inflating simple dispatch tables.
- **Halstead measures and Maintainability Index:** weak. MI was calibrated in
  1992 on a few HP C/Pascal systems (van Deursen 2014). Peitek et al.'s fMRI
  study (ICSE 2021) found vocabulary size taxes working memory, but metrics
  explained cognition only to a limited degree.
- **Function length (LOC):** the largest confounder; most metrics correlate
  strongly with size (El Emam et al. 2001). A cheap, honest baseline.
- **Nesting depth:** plausible and built into cognitive complexity's nesting
  penalty; no strong standalone validation found. Ajami et al. (EMSE 2019):
  construct type (loops harder than ifs) and unusual idioms matter more.
- **Parameter count:** conventional thresholds only (clippy 7, pylint 5); no
  comprehension evidence found.
- **Identifier/naming quality:** real evidence. Hofmeister, Siegmund and Holt
  (SANER 2017 / EMSE 2019): full-word names gave 19% faster defect finding.
  Fakhoury et al. (ICPC 2018, fNIRS): linguistic antipatterns raise cognitive
  load. Hard to compute deterministically.
- **Comment density:** no good evidence; not predictive in Scalabrino's set.
- **Readability models** (Buse & Weimer TSE 2010; Scalabrino et al. JSEP 2018):
  Java-trained classifiers of *readability*, which is distinct from
  understandability. Sergeyuk et al. (2024): they correlate only weakly with
  developers' ratings of AI-generated code.
- **Caution:** Wyrich et al. (ICSE 2021) found that showing developers a
  comprehensibility score strongly anchors their subjective ratings (not their
  actual performance). Present scores carefully.

### A2. Tools that compute these per function

| Tool | Languages | Metrics | License | Status | Python use |
|---|---|---|---|---|---|
| **lizard** | 30+ incl. Python, Rust, TS/TSX | CCN, NLOC, tokens, params, nesting, cognitive (`-Ecognitive`), Halstead extension | MIT (PyPI) | Very active: 1.24.3, 2026-10-24 | Pure Python, in-process `lizard.analyze_file()`; token-heuristic parsing, not a full AST |
| **rust-code-analysis** (Mozilla) | Rust, Python, JS, TS, C/C++, Java, Kotlin | CC, cognitive, Halstead, MI, args, exits, LOC family | MPL-2.0 | Effectively stale: last crate release Jan 2023 | CLI with JSON; build from git |
| **complexipy** | Python | Cognitive | MIT | Very active: 8.3.0, 2026-10-27 | Rust core, Python API/CLI |
| **radon** | Python | CC, Halstead, MI, raw | MIT | Stagnant: last release 2023 | Python API |
| **eslint-plugin-sonarjs** | JS/TS | Cognitive (15) | LGPL-3.0 | Active (v4.2.2; now in SonarJS repo) | Node only |
| **Biome** | JS/TS | `noExcessiveCognitiveComplexity` (15) | Apache-2.0 | Active | Node/binary |
| **clippy** | Rust | `cognitive_complexity` (25; restriction group, not true CogC), `too_many_lines`, `too_many_arguments`, `excessive_nesting` | Apache/MIT | Active | Needs a compile; JSON diagnostics |
| **SonarQube/SonarLint** | Many | S3776 cognitive (15) | Mixed/commercial | Active | Server/IDE, not embeddable |
| **gocognit / gocyclo** | Go | Cognitive / cyclomatic | MIT / BSD-3 | Active / maintained | Go CLI |
| **py-tree-sitter + tree-sitter-language-pack** | Any grammar | Build your own | MIT | Active | In-process; basis for a unified pass |

### A3. LLM-based scoring

Practical as an advisory signal, not as a gate.

- CoReEval (arXiv 2510.16579, 2025; 10 LLMs, ~1.4M evaluations): prompts grounded
  in human readability dimensions improved alignment but increased score
  variability.
- Vitale et al. (2025): a few-shot LLM underperformed a feature-based model;
  about a third of human evaluations in existing datasets contradict each other.
- The ceiling is low: humans agree only moderately (around r ≈ 0.5 with the mean
  rating, arXiv 2407.03790).
- Temperature 0 is not deterministic. Mitigate with coarse bands, median of 3
  samples, caching by function-body hash, and scoring only changed or
  metric-flagged functions.
- Cost: roughly 1–1.5k tokens per function; a 10k-function repository is about
  10–15M tokens per full scan. Keep the rubric as a cached prompt prefix.
- Useful for what metrics cannot see: misleading names, name/behaviour mismatch,
  comments contradicting code, long-but-trivial functions.

### A4. Recommendation for Lookout

- **Metrics:** cognitive complexity primary; NLOC and max nesting secondary; skip
  MI and Halstead. Default bands: CogC info ≥10, warning ≥15, error ≥25; NLOC
  warning ≥60, error ≥100; nesting ≥4; params ≥6.
- **Tool:** lizard (in-process Python; covers Python, TS, Rust; very active).
  Calibrate its heuristic cognitive complexity against complexipy and clippy on
  a FathomDB sample. Keep tree-sitter as the long-term backend.
- **New engine `metrics`** and check type `function_metric`: one Finding per
  function over threshold (`snippet` = normalized signature, `line` = function
  start, severity from the band, `engine="metrics"`). Tiers and path scoping
  unchanged.
- **Baseline and ratchet:** fingerprint on pattern ID, file and *qualified name*
  (`crate::mod::Type::fn`, `pkg.mod.Class.method`) plus an occurrence index, via
  a new optional `symbol` field on `Finding`. Store the metric value in the
  baseline (baseline format v3). Fail only when a new function exceeds a band or
  a baselined function gets worse; record improvements so the baseline ratchets
  down. Renames become new findings.

```yaml
schema_version: 1
rule:
  id: understandability.cognitive-complexity
  title: Function is hard to understand
  tier: 2
  category: understandability
  severity: warning
  languages: [python, typescript, rust]
  check:
    type: function_metric
    metric: cognitive_complexity      # also: nloc, max_nesting, params
    bands: {info: 10, warning: 15, error: 25}
    ratchet: true                     # fail only on new or worsened
  paths:
    exclude: ["**/tests/**", "**/*_pb2.py", "**/generated/**"]
  reporting: {default_message: "Cognitive complexity {value} > {threshold}", confidence: medium}
```

- **Pitfalls:** generated code (protobuf, bindings, `OUT_DIR`); Rust
  `#[cfg(test)] mod tests` inside `src/` (exclude by symbol `::tests::`);
  long-but-simple dispatch tables (use cognitive complexity, never NLOC alone);
  `macro_rules!` and async closures can confuse lizard; anonymous TS callbacks
  need `enclosing<anon#n>` names; calibrate bands per language.

---

## Part B: Conflicting code, dependency chains, side effects

### B1. Conflicting code

**Definition:** two or more pieces of code that encode the same concept but
would give different answers for the same input or state. Forms: diverged
clones; competing implementations (two retry policies, two date parsers, two
validators for one field); contradictory constants, config, checks or
invariants; semantic merge conflicts.

**Evidence:**
- Juergens et al. (ICSE 2009): 52% of clone groups contained inconsistencies;
  nearly every second unintentionally inconsistent clone change led to a fault.
  Krinke: only about half of clones evolve consistently.
- Engler et al. "Bugs as Deviant Behavior" (SOSP 2001) and PR-Miner: infer the
  majority behaviour and flag deviants ("9 callers wrap X in retry, 1 doesn't").
- Semantic merge conflicts: SAM (arXiv 2310.02395) generates unit tests, caught
  9 of about 28 conflicts with 3 false positives; promising, heavy, Java-only.

**Detection:**
1. Clone detection plus divergence: find clone pairs, then use git history for
   commits that touched one copy and not the other. Tools: jscpd v5 (Rust
   engine, 224 formats incl. Python/TS/Rust, MIT, 5.4.2 Oct 2026, JSON/SARIF,
   on PyPI) and PMD CPD.
2. Competing implementations: cluster by name tokens plus similarity, then have
   an LLM confirm "same concept, different behaviour?".
3. Constant conflicts: same normalized constant name, different literal values
   across modules. Deterministic, cheap, precise.
4. Deviant behaviour: extend `pattern_discovery` to report minority deviations.

**LLM role:** confirm candidate pairs and explain the difference; too expensive
for the search itself.

**Scoring:** similarity × divergence (tokens changed on one side only) × fan-in
of the less-used copy, with a bonus when the divergence is in a predicate or
constant.

**Lookout mapping:** a `clones` engine wrapping jscpd, plus a constant-conflict
analyser. Fingerprint: sorted pair of qualified names plus pattern ID.

```yaml
check: {type: conflicting_code, detector: clone_divergence, min_similarity: 0.85, min_tokens: 50, require_divergent_commit: true}
```

**Pitfalls:** intentional copies (tests, migrations, per-language bindings),
boilerplate, Type-4 false positives; needs git history (shallow CI clones break
it).

### B2. Dependency chains

**Measures:** fan-in/fan-out (Martin's Ca/Ce), instability I = Ce/(Ca+Ce),
transitive reach, longest path in the cycle-condensed graph (chain depth),
cycle (SCC) size, propagation cost (MacCormack et al.), co-change/logical
coupling from git.

**Evidence (strongest of the three):**
- Zimmermann & Nagappan (ICSE 2008): dependency-network measures gave about 10
  points higher defect recall than complexity metrics on Windows Server 2003;
  partly replicated on open-source Java (Premraj & Herzig, ESEM 2011).
- D'Ambros, Lanza & Robbes (WCRE 2009): change coupling correlates with defects
  better than complexity metrics.
- MacCormack & Sturtevant: coupling relates to defect activity.
- Martin's instability/distance metrics: design heuristics with little
  empirical validation. Ripple-effect metrics: no strong recent validation.

**Tools:**

| Language | Module/import graph | Function/call graph |
|---|---|---|
| Python | grimp, import-linter, pydeps, tach (all active) | PyCG archived (2023); pyan (GPL-2.0); scip-python |
| TypeScript | dependency-cruiser (very active), madge | scip-typescript, TS compiler API |
| Rust | `cargo metadata`, cargo-modules, planned syn extractor | rust-analyzer SCIP export |
| Co-change | code-maat, or `git log --name-only` | |

**Fit with the planned graph engine:** compute over the graph it builds —
Tarjan SCCs, condensation DAG, per-node depth, reach, Ca/Ce/I, and global
propagation cost. Rules: `max_depth`/`max_reach` for scoped paths, Stable
Dependencies Principle (flag A→B where I(B) > I(A)), `max_cycle_size`, a
co-change ratchet. Use repository-relative thresholds (percentiles).

```yaml
check: {type: dependency_metric, level: module, metric: transitive_reach, max_percentile: 95, ratchet: true}
```

**Pitfalls:** dynamic imports and DI hide edges; re-export barrels (`index.ts`,
`mod.rs`, `__init__.py`) inflate fan-in; filter std/vendor deps; cap commit size
for co-change to avoid bulk-commit noise.

### B3. Side effects (extra load)

**Definition:** observable behaviour beyond what the name, signature or role
promises — I/O, mutation of shared/global/argument state, expensive work, or
import-time execution.

**Evidence:** Arnaoudova et al. (EMSE 2016): catalogue of 17 linguistic
antipatterns (getters that do more than return, `is*` returning non-boolean…);
most developers judged them poor practice. Fakhoury et al. (ICPC 2018): they
raise cognitive load. Deep-learning inconsistent-name detectors degrade sharply
on realistic data (arXiv 2501.12617); evidence for LLM judges is thin.

**Detection tiers:**
1. **Pattern rules (Semgrep/Opengrep, available now):** properties, TS getters
   and constructors that call I/O or write globals; `is_`/`has_` functions that
   assign to `self`; Python module-level calls beyond definitions/constants;
   Rust `fn is_*/get_*/as_*(&mut self)` and clippy `wrong_self_convention`,
   `mut_from_ref`; `global`, `static mut`; TS eslint-plugin-functional and
   `package.json` `"sideEffects"` audits. High precision, direct effects only.
2. **Effect propagation:** tag direct effects per function (io, net, fs, db,
   global-write, arg-mutation, log) and propagate over the call graph; Python's
   `deal` library is a model. Needs the call graph from B2.
3. **LLM judge:** "does this function do more than its name says?", given the
   body and its propagated effect set; cache by body hash.

**Scoring:** effect weight (net/db > fs > global/arg mutation > logging) ×
name-expectation strength (property/getter/`is_` strong; `get_` weak) × fan-in.

```yaml
check: {type: side_effect, expect: pure, applies_to: {name_regex: "^(is|has|as|to)_|^get[A-Z]?", decorators: [property]}, effects: [net, fs, db, global_write], transitive: true}
```

**Pitfalls:** lazy caching in getters; Rust interior mutability (`RefCell`,
`Mutex`) behind `&self`; logging/metrics as effects; dynamic dispatch; Rust has
almost no import-time effects.

---

## Combined recommendation

1. **Understandability first:** one tool, moderate evidence, cheapest.
2. **Dependency chains next:** strongest defect-prediction evidence; rides on the
   planned graph engine. Meanwhile ship 5–8 direct side-effect Semgrep rules,
   which need no new engine.
3. **Transitive side-effect propagation,** reusing the call graph.
4. **Conflicting code last** (highest false-positive rate; needs git history and
   LLM confirmation), except cheap constant-value conflicts.

**One shared per-function pass:** parse each file once (lizard now, tree-sitter
later) into a `FunctionRecord` cached by file content hash: qualified name, span,
normalized signature, CogC/CCN/NLOC/nesting/params, token shingles plus MinHash,
direct callee names, direct effect tags, name verb prefix. Understandability reads
the metrics; conflicting code runs similarity search over the shingles plus git
divergence; dependency chains merge callee edges into the module graph; side
effects propagate effect tags over those edges. An optional LLM stage scores only
records the deterministic stages flagged. All findings carry a `symbol` and a
stored value, so one baseline v3 format supports qualified-name fingerprints and
the ratchet.

---

## Sources

- Muñoz Barón, Wyrich, Wagner 2020: https://arxiv.org/abs/2007.12520
- Scalabrino et al. ASE'17: https://www.cs.wm.edu/~denys/pubs/ASE'17-Readability.pdf ; TSE 2019 data: https://ieee-dataport.org/documents/automatically-assessing-code-understandability
- Trockman et al. MSR 2018: https://2018.msrconf.org/details/msr-2018-papers/36/Automatically-Assessing-Code-Understandability-Reanalyzed-Combined-Metrics-Matter
- Lavazza et al. 2023: https://arxiv.org/abs/2303.07722
- Peitek et al. ICSE 2021: https://www.se.cs.uni-saarland.de/publications/docs/PAP+21.pdf
- Buse & Weimer TSE 2010: https://web.eecs.umich.edu/~weimerw/p/weimer-tse2010-readability-preprint.pdf
- Scalabrino et al. JSEP 2018: https://www.cs.wm.edu/~denys/pubs/JSEP'18-ReadabilityPaper.pdf
- van Deursen on MI: https://avandeursen.com/2014/08/29/think-twice-before-using-the-maintainability-index/
- Hofmeister et al.: https://www.se.cs.uni-saarland.de/publications/docs/HSH16.pdf
- Ajami et al.: https://www.cse.huji.ac.il/~feit/papers/Complexity19EmpSE.pdf
- Feitelson CACM 2023: https://www.cse.huji.ac.il/~feit/papers/Comp23CACM.pdf
- Wyrich et al. ICSE 2021: https://arxiv.org/abs/2012.09590
- El Emam size confound: https://neverworkintheory.org/2011/07/07/the-confounding-effect-of-class-size-on-the-validity-of-object-oriented-metrics.html
- CoReEval: https://arxiv.org/abs/2510.16579 ; Vitale et al.: https://arxiv.org/abs/2503.07870 ; Sergeyuk et al.: https://arxiv.org/abs/2401.14936 ; human agreement: https://arxiv.org/pdf/2407.03790
- Sonar threshold 15: https://community.sonarsource.com/t/s3776-reason-for-the-current-default-value-of-15/127103 ; Biome: https://biomejs.dev/linter/rules/no-excessive-cognitive-complexity
- clippy threshold: https://doc.rust-lang.org/stable/nightly-rustc/clippy_config/conf/defaults/fn.cognitive_complexity_threshold.html
- Tools: https://github.com/terryyin/lizard , https://github.com/mozilla/rust-code-analysis , https://github.com/rohaquinlop/complexipy , https://github.com/rubik/radon , https://github.com/SonarSource/SonarJS , https://github.com/uudashr/gocognit , https://github.com/fzipp/gocyclo , https://github.com/kucherenko/jscpd , https://github.com/pmd/pmd , https://github.com/python-grimp/grimp , https://github.com/seddonym/import-linter , https://github.com/thebjorn/pydeps , https://github.com/tach-org/tach , https://github.com/vitsalis/PyCG , https://github.com/Technologicat/pyan , https://github.com/sverweij/dependency-cruiser , https://github.com/pahen/madge , https://github.com/regexident/cargo-modules , https://github.com/sourcegraph/scip-python , https://github.com/sourcegraph/scip-typescript , https://github.com/adamtornhill/code-maat , https://github.com/life4/deal , https://github.com/eslint-functional/eslint-plugin-functional , https://github.com/tree-sitter/py-tree-sitter , https://github.com/xberg-io/tree-sitter-language-pack
- Juergens et al. ICSE 2009: https://arxiv.org/abs/1701.05472
- Engler et al. SOSP 2001: https://www.cs.columbia.edu/~junfeng/09fa-e6998/papers/deviant.pdf ; PR-Miner: https://www.cs.purdue.edu/homes/xyzhang/fall07/Papers/PRMiner.pdf
- SAM: https://arxiv.org/abs/2310.02395
- Zimmermann & Nagappan 2008: https://www.microsoft.com/en-us/research/publication/predicting-defects-using-network-analysis-on-dependency-graphs/ ; Premraj & Herzig: https://www.st.cs.uni-saarland.de/publications/files/premraj-esem-2011.pdf
- D'Ambros et al. 2009: https://inf.usi.ch/lanza/Downloads/DAmb2009e.pdf
- MacCormack & Sturtevant: https://hbswk.hbs.edu/item/july-19-2016
- Arnaoudova et al.: https://doi.org/10.1007/s10664-014-9350-8
- Fakhoury et al. ICPC 2018: https://conf.researchr.org/details/icpc-2018/icpc-2018-Technical-Research/35/The-Effect-of-Poor-Source-Code-Lexicon-and-Readability-on-Developers-Cognitive-Load
- Inconsistent method names: https://arxiv.org/abs/2501.12617 ; ChatGPT naming bugs: https://www.scitepress.org/PublishedPapers/2025/133544

## Not verified

- How lizard's cognitive-complexity numbers for Rust and Python compare with
  Sonar, complexipy or clippy (needs a calibration run).
- Whether rust-code-analysis's grammars parse newer Rust syntax.
- lizard's license discrepancy (MIT on PyPI, NOASSERTION on GitHub).
- MacCormack's core/periphery details and Cataldo et al.'s specific findings.
- The exact developer-perception percentage in Arnaoudova et al.
