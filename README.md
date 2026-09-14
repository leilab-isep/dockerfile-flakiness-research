# Dockerfile Flakiness Research

This repository is the research artifact produced for the Master's dissertation of
**Leila Boaze**, submitted at ISEP, Instituto Superior de Engenharia do
Porto. It implements and empirically evaluates a tool for detecting, automatically
repairing, and build-verifying flakiness in Dockerfiles, a build that succeeds or
fails inconsistently across separate runs of the same, unmodified Dockerfile (for
example, because a base image tag was retagged, a package version became unavailable,
or a remote script changed).

## Repository contents

- **[`flakiscan/`](flakiscan/README.md)** -- the detection, scoring, and automated
  repair component. Statically analyzes a Dockerfile with Hadolint, Docker Parfum, and
  a custom rule engine, combines their findings into a single flakiness score, and can
  rewrite the Dockerfile to fix what it safely can. See
  [`flakiscan/README.md`](flakiscan/README.md) for how it works, usage, and
  development setup.

- **[`validation/`](validation/README.md)** -- the `flakiscan-validate` component: a
  second, independent package that build-verifies a repair by actually building the
  original and the modified Dockerfile with Docker and classifying the outcome
  (preserved, regressed, improved, or a pre-existing failure). See
  [`validation/README.md`](validation/README.md).

- **[`dataset/`](dataset/)** -- the empirical evaluation corpus and the pipeline built
  around it: `repositories.json` (the public GitHub repositories the corpus was drawn
  from), `Dockerfiles/` (the fetched corpus itself, one subdirectory per repository,
  with a `manifest.json` tracing every file back to its source), `fetch_dockerfiles.py`
  (fetches the corpus from `repositories.json`), and `run_validation.py` (runs
  `flakiscan` and `flakiscan-validate` across the whole corpus, producing the results
  analyzed in the dissertation) -- driven in CI by
  `.github/workflows/dataset-validation.yml`.

- **[`validation-results.zip`](validation-results.zip)** -- the build artifacts
  produced by this ['Dataset Validation'](https://github.com/leilab-isep/dockerfile-flakiness-research/actions/runs/34147664129) workflow pipeline: `validation_results.json` (one entry per Dockerfile in the
  corpus, with the outcome and build logs) and `validation_files/` (the original and
  repaired Dockerfile for each entry). This is the actual data behind the empirical
  analysis of `flakiscan-validate` in the dissertation and if ran again, the 'Dataset 
  Validation' workflow might produce a different output.

- **[`literature_review/`](literature_review/)** -- the systematic literature review
  underpinning the dissertation's background and related-work discussion: search
  results (`sq1.csv`, `sq2.csv`, `sq3.csv`, `google_scholar.csv`) and the PRISMA
  screening phases (`prisma_phases/`).

## Continuous integration

`.github/workflows/` has four workflows:

- **[`ci.yml`](.github/workflows/ci.yml) (CI)** -- runs on every push and pull request
  to `main`, plus manually. Four jobs always run: **lint** (`ruff` + `black`),
  **build** (builds the `flakiscan` wheel and smoke-tests installing it into a clean
  virtualenv), **test** (the unit suite under `coverage`, failing below 80%), and
  **integration** (installs real Hadolint and Docker Parfum and runs
  `flakiscan/tests/integration/` against them). A fifth job, **e2e**, only runs when
  the workflow is triggered manually (`needs` all four jobs above): it installs the
  wheel `build` just produced and runs `flakiscan/tests/e2e/` -- one real,
  network-backed test per detection rule, driven through the actual installed
  `flakiscan` command rather than the library internals.

- **[`validation-ci.yml`](.github/workflows/validation-ci.yml) (Validation CI)** --
  the same lint/build/test/integration structure as `ci.yml`, scoped to
  `flakiscan-validate`: triggers only on a push or pull request to `main` that touches
  `validation/`. Its integration job needs no setup step for Docker, since
  GitHub-hosted `ubuntu-latest` runners already have it installed and running.

- **[`dataset-validation.yml`](.github/workflows/dataset-validation.yml) (Dataset
  Validation)** -- manually triggered only. Reads `dataset/repositories.json`, splits
  any repository with more than 20 Dockerfiles into several matrix jobs (so one large
  repository can't push a job past its time budget), and runs `run_validation.py`
  against each repo/shard in parallel, installing Hadolint and Docker Parfum on every
  runner. A final job merges every shard's output into one `validation_results.json`
  plus `validation_files/`, uploaded as a workflow artifact -- the same data archived
  in [`validation-results.zip`](validation-results.zip).

- **[`copilot-code-review.yml`](.github/workflows/copilot-code-review.yml) (Copilot
  Code Review Setup)** -- infrastructure, not a test: grants the minimal `contents:
  read` / `pull-requests: read` permissions GitHub Copilot's automated PR review
  needs. Only runs when this file itself changes, or manually.

## Installation

`flakiscan` and `flakiscan-validate` are **two separate Python distributions, each with
its own `pyproject.toml`**, and must be installed separately:

```bash
pip install -e .                 # installs flakiscan only, from the repository root
pip install -e ./validation      # installs flakiscan-validate
```

Running `pip install .` (or `-e .`) from the repository root installs **only
`flakiscan`** -- the root `pyproject.toml` describes that package alone.
`flakiscan-validate` has its own `pyproject.toml` inside `validation/` and is not
pulled in as a dependency of `flakiscan`.

See each package's own README for its detailed usage, development setup, and CI:
[`flakiscan/README.md`](flakiscan/README.md) and
[`validation/README.md`](validation/README.md).
