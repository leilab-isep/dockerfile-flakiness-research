"""Adapter for Hadolint, a Dockerfile linter.

Invokes Hadolint as a subprocess with JSON output and keeps only the rules relevant to
build flakiness (see RULE_CATEGORIES). Hadolint's full rule set covers many more
general best-practice checks that this project does not report on.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from flakiscan.detection.ignore_comments import IgnoreMap, filter_ignored
from flakiscan.schema import Category

# Hadolint rules this project reports on, and the flakiness category each belongs to.
# DL3020 is a best-practice rule with no flakiness category of its own; it is still
# reported, but marked flakiness_relevant=False so it does not affect the score.
RULE_CATEGORIES: dict[str, Category] = {
    "DL3007": Category.BASE_IMAGE,
    "DL3008": Category.DEPENDENCY,
    "DL3009": Category.DEPENDENCY,
    "DL3013": Category.DEPENDENCY,
    "DL3016": Category.DEPENDENCY,
    "DL3018": Category.DEPENDENCY,
    "DL3019": Category.DEPENDENCY,
    "DL3020": Category.BEST_PRACTICE,
    "DL3028": Category.DEPENDENCY,
    "DL3033": Category.DEPENDENCY,
    "DL3037": Category.DEPENDENCY,
    "DL3041": Category.DEPENDENCY,
    "DL3042": Category.DEPENDENCY,
    "DL4006": Category.REPRODUCIBILITY,
}


class HadolintUnavailableError(RuntimeError):
    """Raised when the `hadolint` binary cannot be found on PATH."""


def is_available() -> bool:
    return shutil.which("hadolint") is not None


def run(dockerfile_path: str, ignore_map: IgnoreMap | None = None) -> list[dict]:
    """Run Hadolint against `dockerfile_path` and return its flakiness-relevant findings.

    Each finding is a dict with keys rule_id, line_number, message, category, and
    flakiness_relevant. Findings matching a `# flakiscan-ignore` comment in `ignore_map`
    are filtered out before being returned, so a suppressed finding never leaves this
    adapter.

    Raises HadolintUnavailableError if the `hadolint` binary is not on PATH, or
    RuntimeError if Hadolint fails or its output cannot be parsed. This function never
    builds or runs the analyzed image -- it only reads the Dockerfile source.
    """
    if not is_available():
        raise HadolintUnavailableError("hadolint binary not found on PATH; install it (e.g. `brew install hadolint`)")

    proc = subprocess.run(
        ["hadolint", "--format", "json", dockerfile_path],
        capture_output=True,
        text=True,
    )

    # Hadolint exits non-zero when it reports findings; that is expected and not an error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"hadolint failed (exit {proc.returncode}): {proc.stderr.strip()}")

    try:
        raw_findings = json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse hadolint output: {exc}") from exc

    findings = []
    for item in raw_findings:
        code = item.get("code")
        category = RULE_CATEGORIES.get(code)
        if category is None:
            continue  # Not one of the rules this project tracks.

        findings.append(
            {
                "rule_id": code,
                "line_number": item.get("line"),
                "message": item.get("message", ""),
                "category": category,
                "flakiness_relevant": category != Category.BEST_PRACTICE,
            }
        )

    return filter_ignored(findings, ignore_map or {})
