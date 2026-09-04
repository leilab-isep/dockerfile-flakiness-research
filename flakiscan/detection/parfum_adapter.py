"""Adapter for Docker Parfum, a Dockerfile smell detector.

Parfum detects a broad set of Dockerfile smells; this adapter surfaces only the subset
relevant to build flakiness (see RULE_CATEGORIES). Parfum has no JSON output mode on its
CLI, so this adapter drives its Node.js library directly through `parfum_runner.js` and
parses the JSON that script prints.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from flakiscan.detection.ignore_comments import IgnoreMap, filter_ignored
from flakiscan.schema import Category

_RUNNER_SCRIPT = Path(__file__).with_name("parfum_runner.js")

# Parfum rules this project reports on, and the flakiness category each belongs to.
RULE_CATEGORIES: dict[str, Category] = {
    "aptGetInstallUseNoRec": Category.DEPENDENCY,
    "aptGetInstallThenRemoveAptLists": Category.DEPENDENCY,
    "aptGetUpdatePrecedesInstall": Category.DEPENDENCY,
    "aptGetInstallUseY": Category.DEPENDENCY,
    "apkAddUseNoCache": Category.DEPENDENCY,
    "yumInstallRmVarCacheYum": Category.DEPENDENCY,
    "yumInstallForceYes": Category.DEPENDENCY,
    "pipUseNoCacheDir": Category.DEPENDENCY,
    "npmCacheCleanAfterInstall": Category.DEPENDENCY,
    "npmCacheCleanUseForce": Category.DEPENDENCY,
    "yarnCacheCleanAfterInstall": Category.DEPENDENCY,
    "ruleMoreThanOneInstall": Category.DEPENDENCY,
    "curlUseFlagF": Category.NETWORK,
    "curlUseFlagL": Category.NETWORK,
    "curlUseHttpsUrl": Category.NETWORK,
    "wgetUseHttpsUrl": Category.NETWORK,
    "sha256sumEchoOneSpaces": Category.REPRODUCIBILITY,
    "gpgVerifyAscRmAsc": Category.REPRODUCIBILITY,
}


class ParfumUnavailableError(RuntimeError):
    """Raised when `node` or the `@tdurieux/docker-parfum` package is not available."""


def is_available() -> bool:
    if shutil.which("node") is None:
        return False
    return _resolve_module_path() is not None


def _global_node_root() -> str | None:
    try:
        proc = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _resolve_module_path() -> str | None:
    """Return a NODE_PATH entry that lets `parfum_runner.js` resolve
    `@tdurieux/docker-parfum`, wherever npm installed it globally."""
    root = _global_node_root()
    if root and (Path(root) / "@tdurieux" / "docker-parfum").exists():
        return root
    return None


def run(dockerfile_path: str, ignore_map: IgnoreMap | None = None) -> list[dict]:
    """Run Docker Parfum against `dockerfile_path` and return its flakiness-relevant findings.

    Each finding is a dict with keys rule_id, line_number, message, category, and
    flakiness_relevant. Findings matching a `# flakiscan-ignore` comment in `ignore_map`
    are filtered out before being returned.

    Raises ParfumUnavailableError if Node.js or the docker-parfum package is not
    available, or RuntimeError if the underlying tool fails or its output cannot be
    parsed. This function never builds or runs the analyzed image -- Parfum only parses
    the Dockerfile's syntax tree.
    """
    node_path = _resolve_module_path()
    if shutil.which("node") is None or node_path is None:
        raise ParfumUnavailableError(
            "node or @tdurieux/docker-parfum not found; "
            "install with `npm install -g @tdurieux/docker-parfum`"
        )

    env = os.environ.copy()
    env["NODE_PATH"] = node_path
    proc = subprocess.run(
        ["node", str(_RUNNER_SCRIPT), dockerfile_path],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker-parfum failed: {proc.stderr.strip()}")

    try:
        raw_findings = json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"could not parse docker-parfum output: {exc}") from exc

    findings = []
    for item in raw_findings:
        rule_id = item.get("rule_id")
        category = RULE_CATEGORIES.get(rule_id)
        if category is None:
            continue  # Not one of the rules this project tracks.

        findings.append({
            "rule_id": rule_id,
            "line_number": item.get("line_number"),
            "message": item.get("message", ""),
            "category": category,
            "flakiness_relevant": True,
        })

    return filter_ignored(findings, ignore_map or {})
