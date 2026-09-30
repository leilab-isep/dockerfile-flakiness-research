# Iteration 2: package-manager precondition bug in the Refactoring Engine

Post-submission fix, documented as a known limitation in the dissertation (Sections
7.3.2 and 8.2, Future Work). This is a code-only change: the submitted dissertation
document itself is not modified.

## The bug

`flakiscan/refactoring/rules.py`'s `repair_add_cache_cleanup` applies the
`npmCacheCleanAfterInstall` repair (appending `&& npm cache clean --force`) whenever
Docker Parfum flags a missing cache-clean step and the *same* RUN instruction's text
matches the regular expression `npm\s+install\b`. That regex has no leading word
boundary, so it also matches "npm install" as a bare substring of `pnpm install` --
`"pnpm"` is the letters `p`, `n`, `p`, `m` -- and characters 2-4 of that (`n`, `p`,
`m`) spell `npm` exactly. Python confirms it directly:

```pycon
>>> import re
>>> bool(re.search(r"npm\s+install\b", "RUN pnpm install --frozen-lockfile --prod"))
True
```

The result: a Dockerfile that installs dependencies with **pnpm** (or, in principle,
any other pnpm-based command) gets an `npm cache clean --force` appended. That command
finds no npm cache to remove -- npm was never invoked -- and instead *creates* npm's
own internal cache state inside the image layer, growing the image instead of
shrinking it.

The same substring-collision bug was independently present a second time, in
`_NPM_INSTALL_ANCHOR` (used by `repair_pin_package_version` for Hadolint's DL3016,
"unpinned npm install"), which could have mis-pinned a bare `pnpm install <package>`
as if it were an npm install. This path is not known to be reachable today (DL3016 is
raised by Hadolint's own detector, which is not known to have the same substring bug),
but it is the identical defect class in the same file, so it was fixed alongside the
primary bug for consistency.

`yarnCacheCleanAfterInstall`'s anchor (`yarn\s+install\b`) has the same missing leading
boundary, but no common package-manager name is known to contain `yarn` as a bare
substring the way `pnpm` contains `npm` -- it was fixed defensively, for the same
reason, without a known real-world trigger.

## Concrete evidence (real corpus)

From the 655-Dockerfile empirical validation
([`validation-results.zip`](../validation-results.zip)):
`dataset/Dockerfiles/emmanuelgautier__awesome-dockerfiles__express__express-pnpm__Dockerfile`
(source: `express/express-pnpm/Dockerfile`, a pure pnpm project -- `corepack enable` +
`pnpm install --frozen-lockfile --prod` + `pnpm-lock.yaml`, no npm anywhere) received
`npmCacheCleanAfterInstall` in `applied_rule_ids`, and the build-verified image grew:

| | bytes |
|---|---:|
| Original image size | 280,122,271 |
| Patched (buggy repair) image size | 281,212,510 |
| **Delta** | **+1,090,239 (~1.04 MiB)** |

The patched Dockerfile's only change from the original was the single line:

```diff
-RUN pnpm install --frozen-lockfile --prod
+RUN pnpm install --frozen-lockfile --prod && npm cache clean --force
```

Docker Parfum still flags `npmCacheCleanAfterInstall` on that exact line today -- this
is the external tool's own detection behavior, unchanged and out of this project's
control (per the task constraint: only FlakiScan's own decision to *apply* the finding
was changed, not Parfum's detection logic).

## The fix

Anchor every `npm`/`yarn`/`pnpm`-related regex in `rules.py` with a leading `\b`, so
`npm` can no longer match inside `pnpm`:

```diff
-_NPM_CACHE_CLEAN_RE = re.compile(r"npm\s+cache\s+clean\b[^&;]*")
+_NPM_CACHE_CLEAN_RE = re.compile(r"\bnpm\s+cache\s+clean\b[^&;]*")

-    if "npmCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"npm\s+install\b", text) ...
+    if "npmCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"\bnpm\s+install\b", text) ...

-    if "yarnCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"yarn\s+install\b", text) ...
+    if "yarnCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"\byarn\s+install\b", text) ...

-_NPM_INSTALL_ANCHOR = re.compile(r"npm\s+install\b")
+_NPM_INSTALL_ANCHOR = re.compile(r"\bnpm\s+install\b")
```

No architectural change was needed. The engine's existing convention -- a rule id
that no sub-repair function adds to its `handled` set is automatically left in the
`RepairAction.fallback_rule_ids` and gets a `# TODO(flakiscan)` comment
(`flakiscan/refactoring/engine.py`) -- already implements "abstain rather than guess
when the precondition fails." Once the *precondition itself* (the instruction-scoped
regex check) stopped being fooled by the substring collision, the existing fallback
path took over correctly with no further change. This keeps the fix minimal and
consistent with the project's existing design: fixes are instruction-scoped, explicit,
and abstain to a `# TODO` rather than guess (see `flakiscan/refactoring/resolvers.py`
for the same abstention convention applied to network-resolved values).

## Tests added

**Unit** (`flakiscan/tests/unit/test_refactoring_rules.py`, fake resolvers, no
external tools):
- `test_does_not_apply_npm_cache_clean_to_a_pnpm_install` -- reproduces the bug
  directly against `repair_add_cache_cleanup`.
- `test_npm_cache_clean_still_applies_when_npm_genuinely_runs_elsewhere_in_the_line`
  -- regression safety net, using the real corpus line
  `RUN npm install -g pnpm@9 --force` (cypress-io/cypress-docker-images), where "npm
  install" is genuine and the repair must still apply.
- `test_does_not_pin_a_pnpm_install_as_if_it_were_npm` -- same coverage for the second
  instance of the bug (`_NPM_INSTALL_ANCHOR` / DL3016).

**Integration** (`flakiscan/tests/integration/test_refactoring_engine.py`, real
Hadolint + Docker Parfum, fake resolvers): a new `TestPackageManagerCacheCleanPrecondition`
class runs `repair_dockerfile()` against synthetic Dockerfiles through the real
detection pipeline, so the test fails if Parfum's own behavior ever changes in a way
that would hide the regression:
- `test_pnpm_only_dockerfile_does_not_get_npm_cache_clean` -- confirms Parfum still
  flags the finding, and the engine now defers it to a `# TODO` instead of applying it.
- `test_genuine_npm_install_still_gets_cache_clean` / `test_yarn_only_dockerfile_still_gets_yarn_cache_clean`
  -- confirm genuine npm and yarn projects are unaffected.

All 155 unit tests and 27 integration tests pass; `ruff check` and `black --check` are
clean on every changed file.

## Before/after on the real corpus files named for this iteration

Ran `flakiscan <path> --repair --json` (real Hadolint + Docker Parfum, local) against
each of the 9 files below, once with the pre-fix code and once with the fix applied.
Only `express-pnpm` changed behavior; the other 8 are byte-identical before and after,
confirming the fix is precisely scoped:

| File | Before (buggy) | After (fixed) |
|---|---|---|
| `express/express-pnpm/Dockerfile` | **applied:** `npmCacheCleanAfterInstall` (wrong -- pnpm project) | **applied:** none. **fallback:** `npmCacheCleanAfterInstall` (correctly deferred to `# TODO`) |
| `cypress-docker-images/.../node20.18.1-bullseye-chrome131-ff133-edge/Dockerfile` | applied: `..., npmCacheCleanAfterInstall, ...` (genuine `npm install -g pnpm@9`) | unchanged |
| `docker-templates/16-nextjs/Dockerfile` | applied: `npmCacheCleanAfterInstall` (genuine `npm install`) | unchanged |
| `frontend-production-dockerfiles/next.js/Dockerfile.serve` | applied: `npmCacheCleanAfterInstall`; fallback: `npmCacheCleanAfterInstall, yarnCacheCleanAfterInstall` (mixed: one genuine npm line, one conditional npm/yarn/pnpm block already guarded separately) | unchanged |
| `.../next.js/Dockerfile.export` | fallback: `npmCacheCleanAfterInstall, yarnCacheCleanAfterInstall` (conditional install block) | unchanged |
| `.../next.js/Dockerfile.standalone` | fallback: `npmCacheCleanAfterInstall, yarnCacheCleanAfterInstall` (conditional install block) | unchanged |
| `express/express-yarn/Dockerfile` | no findings (already has `yarn install && yarn cache clean` inline) | unchanged |
| `nestjs/Dockerfile` | no findings (already has `yarn install ... && yarn cache clean` inline) | unchanged |
| `grafana/grafana/Dockerfile` | fallback includes `yarnCacheCleanAfterInstall` (conditional install block) | unchanged |

### Image size

A first attempt at a live rebuild of all 9 files today (via `dataset/run_validation.py`,
restricted to just these entries) stalled at `load metadata` on every one of the 9
builds -- a slow/rate-limited Docker Hub pull in this environment, not a hard failure:
a direct `docker pull` of the same base image, run separately, eventually completed
successfully. With that image then cached locally, a focused live rebuild was run for
`express-pnpm` specifically -- cloning `emmanuelgautier/awesome-dockerfiles` for a real
build context, then building both the original `Dockerfile` and the patched output of
the *fixed* engine (`Dockerfile.patched`, which the diff above confirms differs only by
the added `# TODO(flakiscan)` comment) with plain `docker build`:

| Image | Size (bytes) |
|---|---:|
| `iter2-original:test` (original `Dockerfile`) | 107,128,413 |
| `iter2-patched:test` (fixed engine's output) | 107,128,413 |
| **Delta** | **0** |

This is a fresh, live confirmation (not just an inference) that the fix restores the
original build's image size exactly, replacing the buggy repair's +1,090,239-byte
growth (`validation-results.zip`, corpus run captured 2026-09-07) with **0 bytes** --
the patched Dockerfile no longer differs from the original in anything Docker actually
executes. The underlying reasoning is direct: a `#`-prefixed line is a Dockerfile
comment, never sent to the shell and producing no layer, so a `# TODO(flakiscan)`
comment placed above the instruction cannot affect a built image's size -- only an
executed instruction can, and that instruction is now byte-for-byte identical to the
original.

## Files changed

- `flakiscan/refactoring/rules.py` -- the fix (4 regex anchors), plus updated
  docstrings explaining the bug and the fix.
- `flakiscan/tests/unit/test_refactoring_rules.py` -- 3 new regression tests.
- `flakiscan/tests/integration/test_refactoring_engine.py` -- 1 new test class (3
  tests) exercising the real detection pipeline.
- `docs/iteration-2.md` -- this document.
