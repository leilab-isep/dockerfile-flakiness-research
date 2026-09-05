# flakiscan-validate

flakiscan-validate checks whether a modified Dockerfile still builds, compared to its
original. It builds both with `docker build` and classifies the result:

| Original build | Modified build | Outcome | Meaning |
|---|---|---|---|
| succeeds | succeeds | `preserved` | The modification is safe to accept. |
| succeeds | fails | `regressed` | The modification broke a build that used to work. |
| fails | succeeds | `improved` | The modification happens to fix a pre-existing failure. |
| fails | fails | `pre_existing_failure` | The failure predates the modification. |

This package is **independent of flakiscan**: it does not import it and has no
dependency on it. It takes two Dockerfile paths and reports which outcome applies --
nothing about where either file came from. flakiscan's `--repair` is one natural
source of a "modified" Dockerfile worth checking this way, but any other source works
identically.

`docker build` is the only external command this package runs. It requires Docker to
be installed and its daemon running.

## Installation

```bash
pip install -e ./validation      # from the repository root
```

This installs the `flakiscan-validate` console script and the `flakiscan_validate`
package (note the different spelling: the distribution name uses a hyphen, the import
name uses an underscore, per Python packaging convention).

## Usage

```bash
flakiscan-validate original.Dockerfile modified.Dockerfile
```

```
Outcome: regressed
  original: success (1.4s)
  modified: FAILED (0.6s)

--- modified build log (last 40 lines) ---
...
```

By default both Dockerfiles are built against the original's own directory as the
build context (so `COPY`/`ADD` sources resolve the same way for both). Pass `--context`
to use a different directory, or `--json` for a machine-readable report:

```bash
flakiscan-validate original.Dockerfile modified.Dockerfile --context ./app --json
```

```json
{
  "outcome": "regressed",
  "original": { "success": true, "log": "...", "duration_seconds": 1.4, "timed_out": false },
  "modified": { "success": false, "log": "...", "duration_seconds": 0.6, "timed_out": false }
}
```

The process exits `0` for `preserved` and `improved`, and `1` for `regressed` and
`pre_existing_failure` -- so it can gate a script or CI step on "did this change break
the build?" without parsing the output.

If Docker is not installed, or its daemon is not reachable, the command exits `2` with
an error message instead of reporting an outcome -- neither case means anything about
the Dockerfiles themselves, so it is never folded into the four outcomes above.

## Using it with flakiscan

flakiscan-validate does not call flakiscan or read its output format -- you supply two
plain Dockerfile paths. A typical pairing:

```bash
cp Dockerfile Dockerfile.original
flakiscan Dockerfile --repair --in-place
flakiscan-validate Dockerfile.original Dockerfile
```

## Development

```bash
python3 -m unittest discover -s tests/unit -p "test_*.py"          # no Docker required
python3 -m unittest discover -s tests/integration -p "test_*.py"   # needs Docker running
```

Integration tests build small real images (`tests/fixtures/healthy.Dockerfile` and
`broken.Dockerfile`, based on `alpine:3.20`) to exercise all four outcomes against the
real Docker CLI, and are skipped automatically if Docker or its daemon is unavailable.
Unit tests mock `subprocess.run` and never invoke Docker.

## Limitations

- Every build runs sequentially and to completion (or timeout, default 600s per
  build) -- validating a Dockerfile with a slow dependency download takes as long as
  building it normally, twice.
- A `regressed` or `pre_existing_failure` outcome does not identify *which* line or
  change caused the failure -- only the build log, which you can inspect via `--json`
  or the human-readable output's last 40 lines.
- Build layer caching is not disabled, so a flaky dependency that resolved differently
  between the original and modified build could produce a false `regressed` result
  unrelated to the modification's content. Pass `--context` pointing at a context with
  a fresh cache, or extend `flakiscan_validate.builder.build` to pass `--no-cache`, if
  you need to rule this out.
