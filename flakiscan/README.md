# FlakiScan

FlakiScan analyzes a Dockerfile and reports patterns known to cause **build
flakiness** -- a build that succeeds or fails inconsistently across separate runs of
the *same, unmodified* Dockerfile (for example, because a base image tag was
retagged, a package version became unavailable, or a remote script changed).
FlakiScan finds these patterns through static analysis of the Dockerfile source; it
does not build the image and does not observe actual build outcomes over time.

FlakiScan covers **detection, scoring, and automated repair**: it runs three
independent analyzers, combines their output into one report, computes a numeric risk
score, and can rewrite the Dockerfile to fix what it safely can. Validating a repaired
Dockerfile by actually building it is not yet implemented (see [Roadmap](#roadmap)).

## How it works

FlakiScan runs three analyzers against the Dockerfile in parallel:

- **[Hadolint](https://github.com/hadolint/hadolint)**, a Dockerfile linter. FlakiScan
  uses 14 of its rules that relate to build flakiness.
- **[Docker Parfum](https://github.com/tdurieux/docker-parfum)**, a Dockerfile smell
  detector. FlakiScan uses 18 of its rules.
- A **custom rule engine** (8 rules) covering flakiness patterns the two tools above
  do not check, such as unpinned `git clone` and shell pipelines without `pipefail`.

Each analyzer's output is normalized into a common **finding** -- a single detected
issue with a rule ID, source tool, line number, message, and category. Findings from
all three analyzers are merged and de-duplicated, then each is assigned a **severity**
(`error`, `warning`, or `info`) based on its **category** (what kind of flakiness risk
it represents: `dependency`, `network`, `environment`, `base_image`, `security`,
`reproducibility`, or `best_practice`). The Dockerfile's overall **flakiness score** is
the sum of the weights (`error` = 3, `warning` = 1, `info` = 0) of every finding
flagged as relevant to flakiness; `best_practice` findings are reported but excluded
from the score.

With `--repair`, each finding is passed to a repair rule that either rewrites the
affected instruction (for example, pinning a base image to a digest, or adding a
missing package-manager flag) or, if it has no automated fix or a required external
lookup fails, leaves a `# TODO(flakiscan)` comment above the instruction instead of
silently dropping the finding. See [Repairing findings](#repairing-findings).

```
flakiscan/
├── schema.py                     # Finding, Category, Severity data types
├── detection/
│   ├── dockerfile_parser.py      # Minimal Dockerfile parser used by the custom rule engine
│   ├── ignore_comments.py        # `# flakiscan-ignore` suppression comment handling
│   ├── hadolint_adapter.py       # Runs Hadolint and maps its output to findings
│   ├── parfum_adapter.py         # Runs Docker Parfum and maps its output to findings
│   ├── parfum_runner.js          # Node.js helper invoked by parfum_adapter.py
│   ├── custom_rules.py           # The 8 custom pattern-matching rules
│   ├── uniformise.py             # Merges and de-duplicates findings from all sources
│   └── detector.py               # Orchestrates the three analyzers
├── scoring/
│   └── classifier.py             # Assigns severity/weight and computes flakiness_score
├── refactoring/
│   ├── resolvers.py              # Network lookups (package versions, image digests, git tags)
│   ├── rules.py                  # One repair function per group of related findings
│   └── engine.py                 # Applies repairs and produces a patched Dockerfile
├── cli.py                        # Command-line interface
└── tests/                        # unittest suite
```

## Installation

FlakiScan requires Python 3.10 or later and no third-party Python packages.

Two of the three analyzers are external tools and must be installed separately:

```bash
brew install hadolint          # or see https://github.com/hadolint/hadolint#install
npm install -g @tdurieux/docker-parfum
```

Both are optional: if either is missing, FlakiScan records a warning and continues
with the remaining analyzers rather than failing. The custom rule engine has no
external dependency and always runs.

## Usage

```bash
python3 -m flakiscan.cli path/to/Dockerfile
```

```
FlakiScan report for path/to/Dockerfile
  flakiness_score: 12
  findings: 4
    base_image: 1
    dependency: 3

  [error  ] DL3013                           L7    (hadolint, dependency) Pin versions in pip. Instead of `pip install <package>` use `pip install <package>==<version>` or `pip install --requirement <requirements file>`
  ...
```

Pass `--json` for a machine-readable report instead:

```bash
python3 -m flakiscan.cli path/to/Dockerfile --json
```

```json
{
  "dockerfile": "path/to/Dockerfile",
  "flakiness_score": 12,
  "findings": [
    {
      "rule_id": "DL3013",
      "tool_source": "hadolint",
      "category": "dependency",
      "severity": "error",
      "weight": 3,
      "line_number": 7,
      "message": "Pin versions in pip. ...",
      "flakiness_relevant": true
    }
  ],
  "summary": { "total_findings": 4, "by_category": { "dependency": 3, "base_image": 1 } },
  "warnings": [],
  "duration_seconds": 0.842
}
```

`warnings` lists any analyzer that could not run (for example, because `hadolint` is
not installed). `duration_seconds` is the wall-clock time the analysis took.

## Suppressing a finding

Place a `# flakiscan-ignore` comment on the line immediately above the instruction you
want to exclude from the report:

```dockerfile
# flakiscan-ignore: DL3007
FROM ubuntu:latest

# flakiscan-ignore
RUN apt-get install -y curl
```

`# flakiscan-ignore: <rule_id>` suppresses only that rule on the next line. A bare
`# flakiscan-ignore` suppresses every rule on the next line, regardless of which
analyzer would have reported it. The comment must be on its own line directly above
the instruction -- Dockerfile syntax only treats `#` as a comment at the start of a
line, so it cannot be appended after an instruction on the same line.

For an instruction that spans multiple physical lines using a trailing backslash, the
comment suppresses findings on the line directly below it, not necessarily the
instruction's first line -- place it directly above whichever line the finding is
reported on.

## Repairing findings

```bash
python3 -m flakiscan.cli path/to/Dockerfile --repair              # print the patched Dockerfile to stdout
python3 -m flakiscan.cli path/to/Dockerfile --repair --in-place   # overwrite the file
python3 -m flakiscan.cli path/to/Dockerfile --repair --json       # patched text plus a per-instruction change log
```

A repair only ever rewrites the specific instruction a finding was reported on -- it
never adds, removes, or reorders other instructions. Some repairs need a value this
tool cannot determine from the Dockerfile alone, so they look it up: a base image's
current digest (Docker Hub only), a Python/Node.js/Ruby package's current version (via
PyPI, the npm registry, or RubyGems), a GitHub repository's latest tag, or the sha256
of a file the Dockerfile downloads. When a lookup fails -- no network access, the
package or repository doesn't exist, the base image isn't on Docker Hub -- the finding
is left in place with a `# TODO(flakiscan)` comment explaining what could not be fixed,
rather than guessing at a value.

A few findings never have an automated fix and always get a `# TODO` comment:

- **`arg_no_default`**: the correct value for an `ARG` with no default depends on how
  the image is built (`--build-arg`), which this tool has no way to know.
- **`ruleMoreThanOneInstall`**: merging separate install commands into one would mean
  editing more than the single flagged instruction, which this engine does not do.
- **Version pinning for apt, apk, yum, zypper, and dnf packages**: unlike PyPI, npm,
  and RubyGems, these package managers have no registry API that returns "the current
  version" independent of the base image's specific OS release and configured mirrors.

Running `--repair` again on an already-repaired Dockerfile is safe: findings that were
already fixed are not detected a second time, and re-running never duplicates a
`# TODO` comment or produces a different result for the same, unfixable finding.

## Limitations

- FlakiScan detects **patterns associated with flakiness risk**, such as an unpinned
  base image tag or a dependency install without a version constraint. It does not
  build the Dockerfile and does not confirm that a flagged pattern has actually caused
  a build failure. A high flakiness score means the Dockerfile contains more
  risk-prone patterns, not that it has been observed to fail.
- Each analyzer only reports the subset of its rules that FlakiScan has classified as
  flakiness-relevant; findings from other rules in Hadolint or Docker Parfum are not
  surfaced.
- There is currently no rule that detects credential or certificate expiry, even
  though this is a real cause of build flakiness -- it is difficult to identify from
  Dockerfile source alone.
- Custom-rule pattern matching operates on instruction text with simple heuristics
  (for example, treating `&&`, `;`, and `|` as command separators). It can miss or
  misclassify unusual shell constructs, quoting, or variable expansion.
- Repairs are not build-verified. FlakiScan does not build the patched Dockerfile to
  confirm the fix actually works -- review a patch before relying on it, especially one
  that pins a version or digest.
- A repaired instruction spanning multiple physical lines (via a trailing `\`) is
  rewritten onto a single line. The fix is correct, but the original line-wrapping is
  not preserved.
- Pinning a git clone to a tag only recognizes `github.com` URLs, and appends the
  `git checkout` at the end of the RUN instruction -- if a later command in the same
  instruction changes to a different directory, the checkout can run in the wrong one.

## Development

```
flakiscan/tests/
├── fixtures/       # Sample Dockerfiles shared by unit and integration tests
├── unit/           # Pure logic: no subprocess, network, or filesystem beyond temp files
└── integration/    # Exercises real Hadolint/Docker Parfum subprocesses and the full pipeline
```

Run everything with:

```bash
python3 -m unittest discover -s flakiscan/tests -p "test_*.py" -v
```

Or run just one tier:

```bash
python3 -m unittest discover -s flakiscan/tests/unit -p "test_*.py"          # fast, no external tools needed
python3 -m unittest discover -s flakiscan/tests/integration -p "test_*.py"   # needs hadolint and docker-parfum on PATH
```

No test framework beyond the standard library is required. Integration tests for the
Hadolint and Docker Parfum adapters are skipped automatically if the corresponding tool
is not installed; every unit test runs unconditionally. Unit tests that exercise repair
rules needing a network lookup pass in a fake `Resolvers` instance instead of the real
one (see `refactoring/resolvers.py`), so the unit tier never depends on network access
or the availability of any third-party service.

### Linting, formatting, and coverage

Install the pinned tool versions with `pip install -r requirements-dev.txt` (repository
root), then:

```bash
ruff check flakiscan                 # lint
black --check --diff flakiscan       # formatting (drop --check --diff to auto-format)
coverage run --source=flakiscan --omit="flakiscan/tests/*" \
    -m unittest discover -s flakiscan/tests/unit -p "test_*.py"
coverage report -m --fail-under=80   # fails if total coverage drops below 80%
```

The unit tier alone reaches roughly 95% coverage, since every external boundary
(subprocess calls to Hadolint/Docker Parfum, network lookups in `refactoring/resolvers.py`)
is exercised through a mock rather than skipped.

### Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request, as four independent
jobs:

- **lint** -- `ruff check` and `black --check` against `flakiscan/`.
- **build** -- builds an installable wheel (`python -m build`), then installs it into a
  clean virtual environment and runs the `flakiscan` console script against a sample
  Dockerfile, catching packaging mistakes (like a runtime file missing from the wheel)
  that unit tests alone would not.
- **test** -- runs the unit test tier under `coverage` and fails the job if total
  coverage drops below 80%.
- **integration** -- installs Hadolint and Docker Parfum on the runner (a pinned
  Hadolint release binary, `@tdurieux/docker-parfum` via npm) and runs
  `flakiscan/tests/integration/` against the real tools.

## Troubleshooting

- **`HadolintUnavailableError` / `ParfumUnavailableError`**: the corresponding tool is
  not on `PATH`. Install it as described in [Installation](#installation), or ignore
  the warning if you only need the custom rule engine's findings.
- **`RuntimeError: could not parse hadolint output`** or the Parfum equivalent: the
  installed tool version produced unexpected output. Confirm the tool runs correctly
  on its own (`hadolint --format json <file>` or `docker-parfum analyze <file>`).
- **A finding you expected to see is missing**: check for a `# flakiscan-ignore`
  comment above that line, and confirm the rule is one FlakiScan reports on (see
  `RULE_CATEGORIES` in `hadolint_adapter.py` / `parfum_adapter.py`).
- **`--repair` left a `# TODO(flakiscan)` comment instead of fixing something**: the
  comment names which rule(s) could not be fixed. If it lists a rule that pins a
  version, digest, or tag, the most common cause is that the required lookup failed --
  confirm you have network access and that the package, image, or repository referenced
  actually exists. Some rules (listed in [Repairing findings](#repairing-findings))
  never have an automated fix and will always show a TODO.

