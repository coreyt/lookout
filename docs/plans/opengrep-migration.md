# Plan: Move Lookout from Semgrep CE to Opengrep

- **Status:** proposed — decisions on auto-install and Windows recorded 2026-10-27
- **Date:** 2026-10-27
- **Scope:** the scanning engine Lookout runs. Rule format, specs, baseline and
  output formats do not change.

## 1. Goal

Make [Opengrep](https://github.com/opengrep/opengrep) Lookout's default scanning
engine, keep Semgrep CE as a supported fallback behind the same interface, and
prove the two produce identical findings before switching the default.

**Non-goals:** changing the rule format, adopting Opengrep-only features in the
built-in rules, or removing Semgrep support in this plan.

## 2. Why

- **Feature gating in Semgrep CE.** Unauthenticated Semgrep CE withholds matched
  source in JSON output (`"requires login"`). Lookout already works around it
  (0.1.2 reads lines from disk), but the trend is toward more gating.
- **Opengrep is a credible replacement.** LGPL-2.1, forked from Semgrep CE
  1.100.0 in Dec 2024, backed by a consortium of AppSec vendors. Release cadence
  is high (v1.28.0 → v1.31.0 between 2026-08-25 and 2026-10-15) with ~800
  commits May–Oct 2026. Codacy migrated to it as a 1:1 replacement in Feb 2026.
- **Extra capabilities** Lookout may use later: per-rule timeouts,
  `max-match-per-file`, `--force-exclude`, intra-file cross-function taint
  (`--taint-intrafile`), SARIF fingerprints, more languages (Apex, Elixir, VB).

**Main risk:** the active team is small (three people account for nearly all
recent commits). The fallback in §5 exists for that reason.

## 3. Current Semgrep surface in Lookout

| Location | What it does | Change needed |
|---|---|---|
| `pyproject.toml` | `semgrep>=1.50.0` is a required dependency | Becomes optional extra in Phase 3 |
| `src/lookout/semgrep.py` | Compiles specs to Semgrep-format rules; `build_semgrep_command`; parses JSON; snippet fallback | Command building moves to the engine; compile/parse stay (same rule and JSON format) |
| `src/lookout/runner.py` | Runs the scan via `subprocess`; accepts exit codes 0/1/2; `check_semgrep_version` | Uses the engine object |
| `src/lookout/rule_verifier.py` | `shutil.which("semgrep")`; runs candidate rules against examples | Uses the engine object |
| `tests/integration/test_pattern_examples.py` | Real-scanner example harness | Parametrized over engines |
| `.github/workflows/ci.yml` | Semgrep arrives via the dev install | Installs a pinned, signature-verified Opengrep |
| `models.py` | `Finding.engine` / `RawCheck.engine` hard-coded `"semgrep"` | Record the engine actually used |
| `docs/`, `README.md` | Describe Semgrep as the engine | Updated in Phase 3 |

`similarity.py` and the spec schema only use the *rule format*, which both
engines share; they need no change.

## 4. Known differences

Checked against Opengrep's source (`src/osemgrep/cli_scan/Scan_CLI.ml`) and
release assets on 2026-10-27. Items marked *verify* are settled in Phase 0.

| Area | Semgrep CE (today) | Opengrep 1.31 | Impact |
|---|---|---|---|
| Rule YAML | — | Compatible (claimed by both projects) | Prove with the example harness |
| Invocation | `semgrep --config …` | `opengrep scan --config …` | Use the explicit `scan` subcommand for both engines |
| `--metrics=off` | Passed by Lookout to disable telemetry | **No telemetry exists, so no flag.** v1.31.0 has no metrics module in its Python CLI and no `metrics` option in either CLI; passing the flag would be rejected as an unknown option | Must be omitted for Opengrep. See §4.1 |
| Version check (network) | Contacts semgrep.dev and prints "A new version … is available" | `--enable/--disable-version-check` accepted but inert; source states Opengrep never contacts a server to compare versions | Scans are fully offline with a local config |
| `--json`, `--sarif`, `--no-git-ignore`, `--validate`, `--test`, `--timeout`, `--max-match-per-file`, `--force-exclude` | — | All present | No change |
| Matched source in JSON | `"requires login"` placeholder | No placeholder in source | Keep the from-disk fallback; *verify* real lines are returned |
| Exit codes, JSON `errors[]` shape | 0/1/2 | *verify* | Runner and parser depend on these |
| Target discovery outside git | Scans nothing without `--no-git-ignore` | *verify* | Keep `targets_outside_git` logic |
| Distribution | PyPI wheel | Not on PyPI; Cosign-signed binaries on GitHub Releases; `install.sh` installs to `~/.opengrep/cli/<version>` with a `latest` symlink | Lookout cannot `pip install` it; see §5.3 |
| Next major | — | `v2.0.0-nopython` alphas remove the Python CLI layer | Pin `>=1.31,<2` until 2.0 is stable and tested |

### 4.1 Does the missing `--metrics` flag reduce Opengrep's usefulness?

**No; it is an improvement.** In Semgrep CE, `--metrics` controls anonymous
usage telemetry sent to Semgrep, Inc. It does not affect scanning, rules or
output. Lookout passes `--metrics=off` only to keep scans private.

Opengrep removed the telemetry instead of the switch. Checked in the v1.31.0
source (2026-10-27):

- the Python CLI (`cli/src/semgrep/`) has no metrics module (Semgrep keeps its
  telemetry in `metrics.py`) and `commands/scan.py` declares no `--metrics`
  option;
- the native CLI (`src/osemgrep/cli_scan/Scan_CLI.ml`) defines no metrics
  option, and its comments state that Opengrep never contacts a server to check
  for newer versions;
- the only remaining `semgrep.dev` URL is the default for Registry configs
  (`--config p/...`), which Lookout never uses: it always passes a local
  config file.

So with Opengrep a Lookout scan makes no network calls at all, which suits
air-gapped and regulated environments better than Semgrep CE (which still runs
a version check). The one practical consequence is that Lookout must **not**
pass `--metrics` to Opengrep: the CLI rejects unknown options, which would fail
every scan. Phase 0 confirms this by running a scan with the network disabled.

## 5. Design

### 5.1 Engine interface

Add `src/lookout/engine.py`:

```python
class ScanEngine(Protocol):
    name: str                     # "opengrep" | "semgrep"
    binary: Path
    def version(self) -> str: ...
    def scan_command(self, config: Path, targets: list[Path], *, no_git_ignore: bool) -> list[str]: ...
    def validate_command(self, config: Path) -> list[str]: ...
```

`SemgrepEngine` keeps today's flags; `OpengrepEngine` uses `opengrep scan`
without `--metrics`. Rule compilation and JSON parsing stay shared in
`semgrep.py`, because both engines read and write the same formats; only command
construction and discovery differ. The runner, `rule_verifier` and the example
harness take an engine instead of calling `semgrep` directly.

### 5.2 Selection

- Config: `engine = "auto" | "opengrep" | "semgrep"` (default `auto`) and an
  optional `engine_path`. CLI: `--engine` on `check`, `discover`, `promote`.
- `auto` resolution: `engine_path` → `opengrep` on `PATH` →
  `~/.opengrep/cli/latest/opengrep` → `semgrep` on `PATH` → error naming both
  install commands.
- Enforce the version range (`opengrep >=1.31,<2`); outside it, warn and
  continue, or fail with `--strict-engine`.
- Text output prints `Engine: opengrep 1.31.0` to stderr; JSON findings and
  SARIF `tool.driver` record the engine name and version; `Finding.engine` holds
  the real engine.

### 5.3 Installation: Lookout installs Opengrep (decided)

Lookout will download and install Opengrep itself rather than ask users to run
a shell installer. It does not bundle the binary in its wheel (keeps the wheel
small and avoids LGPL redistribution duties: users download from Opengrep's own
GitHub release).

- **`lookout engine install [--version X]`** downloads the pinned release asset
  for the current platform from `github.com/opengrep/opengrep/releases`.
- **Verification is mandatory.** Lookout ships a table of SHA-256 digests for
  each platform asset of the pinned version (GitHub publishes them per asset,
  e.g. `opengrep_windows_x86.exe sha256:fdd2e225…`); a mismatch aborts the
  install. If `cosign` is on `PATH`, Lookout also runs `cosign verify-blob`
  against the published `.cert`/`.sig`.
- **Install location** matches Opengrep's own installers so the two coexist:
  `~/.opengrep/cli/<version>/opengrep` (Windows:
  `%USERPROFILE%\.opengrep\cli\<version>\opengrep.exe`). Lookout records the
  path it installed rather than relying on a `latest` symlink, which Windows
  users often cannot create without Developer Mode.
- **First run:** if no engine is found, `lookout check` prints the install
  command; with `engine_auto_install = true` in config (default off, so CI and
  offline machines never download implicitly) it installs first.
- **`lookout doctor`** reports the engine found, its version, whether it is in
  the supported range, and the install command if missing.
- **Upgrades:** bumping the pinned version and digest table is a normal Lookout
  release; a script regenerates the table from the GitHub release API.

Asset per platform (v1.31.0; sizes ~46–54 MB):

| Platform | Asset |
|---|---|
| Linux x86_64 (glibc / musl) | `opengrep_manylinux_x86` / `opengrep_musllinux_x86` |
| Linux aarch64 (glibc / musl) | `opengrep_manylinux_aarch64` / `opengrep_musllinux_aarch64` |
| macOS arm64 / x86_64 | `opengrep_osx_arm64` / `opengrep_osx_x86` |
| Windows x86_64 | `opengrep_windows_x86.exe` (ARM64 Windows runs it under emulation) |

### 5.4 Windows support (decided: required)

Opengrep ships a native Windows build, so Lookout itself must work on Windows:

- run engine binaries by explicit path (`opengrep.exe`), never via a shell;
- normalize Semgrep/Opengrep result paths (`\` vs `/`) before fingerprinting,
  so a baseline written on Linux matches findings on Windows and vice versa;
- open source files for snippets with explicit UTF-8 and tolerant decoding
  (already done) and handle CRLF line endings when slicing lines;
- `git ls-files`, temp-file handling and `--no-git-ignore` detection verified
  on Windows;
- path globs in specs (`paths.include`/`exclude`) stay forward-slash and are
  passed through unchanged (the engine matches them).

CI gains a `windows-latest` job running the unit tests, the example harness
and the engine-parity test against the Windows Opengrep binary.

### 5.5 Baseline and output compatibility

Fingerprints use pattern ID, file, snippet read from disk, and occurrence order.
None of these depend on the engine, so existing baselines should carry over
unchanged. Phase 2's parity test asserts this.

## 6. Phases

| Phase | Work | Exit criteria | Est. |
|---|---|---|---|
| **0. Spike** | Install Opengrep 1.31.0 in a scratch dir with signature verification. Run the compiled built-in rules, the example harness and `lookout check` on fixtures and two real repos under both engines; diff the results. Resolve every *verify* item in §4, including a scan with networking disabled. Repeat the harness on a Windows machine or runner. | Written findings: harness results per engine and OS, flag differences, exit-code and JSON differences, real-repo per-rule counts. Go/no-go. | 1 day |
| **1. Engine seam** | `engine.py`, `SemgrepEngine`, config/CLI selection, `doctor`; runner, verifier and harness use the engine; path normalization for Windows. **No behaviour change.** | All existing tests pass with Semgrep on Linux and Windows CI; new unit tests for resolution, version gating and path normalization. | 1.5 days |
| **2. Opengrep engine + installer + parity** | `OpengrepEngine`; `lookout engine install` with SHA-256 digest table (and cosign when present); CI installs Opengrep through that command on Linux, macOS and Windows; example harness parametrized over both engines; new `test_engine_parity.py` comparing normalized findings `(pattern_id, path, start, end)` and baseline fingerprints on fixtures. | CI green on both engines and all three OSes; parity test identical; real-repo counts match per rule; tampered-download test rejects a bad digest. | 2–2.5 days |
| **3. Switch default** | `auto` prefers Opengrep; `semgrep` moves to an optional `lookout[semgrep]` extra; docs, README, User Guide and CHANGELOG (install step called out as breaking). Release. | Fresh install + Opengrep install + `lookout check` works end to end; `engine = "semgrep"` still works. | 0.5 day |
| **4. Optional: use Opengrep features** | Per-rule `timeout` and `max-match-per-file` in the spec schema; evaluate `--taint-intrafile` for future checks; compare SARIF fingerprints with Lookout's. | Separate decisions; not required for the migration. | — |

## 7. Verification strategy

1. **Rule level:** the example harness (every built-in rule's good/bad examples)
   must pass identically under both engines.
2. **Finding level:** a parity test runs both engines on fixture projects and
   requires identical normalized findings and baseline fingerprints.
3. **Real repositories:** before Phase 3, run both engines on two real
   repositories (e.g. `airlock`, `fathomdb`) and require matching per-rule
   finding counts; investigate any difference before switching.
4. **Ongoing:** keep both engines in the CI matrix for at least one minor
   release after the default switch.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Small maintainer team; project slows or stalls | Engine seam keeps Semgrep a one-setting fallback; CI covers both |
| CLI or JSON drift between versions | Pin a version range; parity tests in CI catch drift on upgrade |
| Opengrep 2.0 breaking changes | Pin `<2`; evaluate 2.0 as its own change once it is stable |
| Install friction (no PyPI) | `lookout engine install` with pinned digests; `lookout doctor`; clear error with the install command |
| Supply chain (downloaded binary) | Pinned version; SHA-256 digest table shipped in Lookout and checked on every install; Cosign verification when available |
| Windows-specific breakage | Windows CI job from Phase 1; path normalization before fingerprinting |
| LGPL obligations | No bundling; if Lookout ever ships a container or installer with Opengrep, include the license and a source link |
| Hidden behaviour differences on real code | Phase 0 and Phase 3 real-repo parity runs |

## 9. Rollback

Set `engine = "semgrep"` in config (or pass `--engine semgrep`) with the
`lookout[semgrep]` extra installed. No data migration is involved; baselines
are engine-independent.

## 10. Decisions

**Decided (2026-10-27):**

- **Auto-install:** yes. Lookout installs Opengrep itself with digest
  verification (§5.3); implicit install on first run is opt-in.
- **Windows:** required (§5.4), with a Windows CI job.
- **`--metrics`:** not a loss; Opengrep has no telemetry, and the flag must be
  omitted (§4.1).

**Still open:**

1. **Release numbering** for the default switch (e.g. 0.2.0 vs. 0.1.x).
2. **End state for Semgrep:** keep it as a permanent fallback, or remove it
   after a deprecation period?
