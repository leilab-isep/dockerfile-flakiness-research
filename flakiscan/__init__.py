"""FlakiScan -- static analysis and automated repair for flakiness-prone patterns in Dockerfiles.

This package implements detection, scoring, and refactoring: running Hadolint, Docker
Parfum, and a custom rule engine against a Dockerfile, classifying and weighting the
combined findings, and rewriting what it can safely fix (leaving a `# TODO` comment for
findings it cannot). Build-verified validation of the resulting patch -- comparing the
original and refactored Dockerfile by actually building both -- is implemented as a
separate, independent package, `flakiscan_validate` (see `validation/` in the
repository root), which this package does not depend on.
"""
