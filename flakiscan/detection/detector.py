"""Runs Hadolint, Parfum, and the custom rule engine, and merges their findings.

The three sources run concurrently and share no state, so a slow or unavailable source
never blocks the others. A missing tool is recorded as a warning and skipped rather than
failing the whole run, so detection still completes with whichever sources are
available. This module never invokes `docker build` or otherwise runs the analyzed
Dockerfile -- detection is static analysis only.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from flakiscan.detection import custom_rules, hadolint_adapter, parfum_adapter
from flakiscan.detection.dockerfile_parser import parse_dockerfile
from flakiscan.detection.ignore_comments import IgnoreMap, parse_ignore_map
from flakiscan.detection.uniformise import uniformise
from flakiscan.schema import Finding


@dataclass
class DetectionResult:
    findings: list[Finding]
    warnings: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def _run_hadolint(dockerfile_path: str, ignore_map: IgnoreMap) -> tuple[list[dict], str | None]:
    if not hadolint_adapter.is_available():
        return [], "hadolint not available; skipped (see hadolint_adapter.is_available)"
    return hadolint_adapter.run(dockerfile_path, ignore_map), None


def _run_parfum(dockerfile_path: str, ignore_map: IgnoreMap) -> tuple[list[dict], str | None]:
    if not parfum_adapter.is_available():
        return [], "docker-parfum not available; skipped (see parfum_adapter.is_available)"
    return parfum_adapter.run(dockerfile_path, ignore_map), None


def detect(dockerfile_path: str) -> DetectionResult:
    """Analyze `dockerfile_path` with all available detection sources.

    Returns a DetectionResult with the merged, deduplicated findings, any warnings about
    unavailable tools, and the wall-clock time the analysis took. Raises OSError if
    `dockerfile_path` cannot be read.
    """
    start = time.monotonic()
    warnings: list[str] = []

    instructions = parse_dockerfile(dockerfile_path)
    ignore_map = parse_ignore_map(dockerfile_path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        hadolint_future = pool.submit(_run_hadolint, dockerfile_path, ignore_map)
        parfum_future = pool.submit(_run_parfum, dockerfile_path, ignore_map)
        custom_future = pool.submit(custom_rules.run, instructions, ignore_map)

        hadolint_findings, hadolint_warning = hadolint_future.result()
        parfum_findings, parfum_warning = parfum_future.result()
        custom_findings = custom_future.result()

    for warning in (hadolint_warning, parfum_warning):
        if warning:
            warnings.append(warning)

    findings = uniformise(hadolint_findings, parfum_findings, custom_findings)

    return DetectionResult(
        findings=findings,
        warnings=warnings,
        duration_seconds=time.monotonic() - start,
    )
