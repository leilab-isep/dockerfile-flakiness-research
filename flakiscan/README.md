# FlakiScan

FlakiScan analyzes a Dockerfile and reports patterns known to cause **build
flakiness** -- a build that succeeds or fails inconsistently across separate runs of
the *same, unmodified* Dockerfile (for example, because a base image tag was
retagged, a package version became unavailable, or a remote script changed).
FlakiScan finds these patterns through static analysis of the Dockerfile source; it
does not build the image and does not observe actual build outcomes over time.

FlakiScan currently covers **detection and scoring**: it runs three independent
analyzers, combines their output into one report, and computes a numeric risk score.
Automated repair of detected issues is not yet implemented (see
[Roadmap](#roadmap)).

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

## Development

Run the test suite with:

```bash
python3 -m unittest discover -s flakiscan/tests -p "test_*.py" -v
```

No test framework beyond the standard library is required. Tests for the Hadolint and
Docker Parfum adapters are skipped automatically if the corresponding tool is not
installed; every other test runs unconditionally.

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

## Roadmap

Not yet implemented:

- **Automated repair**: deterministic patches for detected findings (for example,
  pinning a base image to a digest, or adding a missing checksum verification step).
- **Patch validation**: rebuilding a patched Dockerfile and comparing the outcome
  against the original build to confirm a patch does not introduce a new failure. This
  would be the only part of FlakiScan that builds the analyzed image.
