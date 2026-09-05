"""Command-line entry point for flakiscan_validate.

Usage:
    python3 -m flakiscan_validate.cli original.Dockerfile modified.Dockerfile [--context DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys

from flakiscan_validate.builder import DockerDaemonUnavailableError, DockerUnavailableError
from flakiscan_validate.validator import validate


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "n/a"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _format_delta(delta_bytes: int | None) -> str:
    if delta_bytes is None:
        return "n/a"
    sign = "+" if delta_bytes >= 0 else "-"
    return f"{sign}{_format_size(abs(delta_bytes))}"


def _print_human(result) -> None:
    print(f"Outcome: {result.outcome.value}")
    print(
        f"  original: {'success' if result.original.success else 'FAILED'} "
        f"({result.original.duration_seconds:.1f}s, {_format_size(result.original.image_size_bytes)})"
    )
    print(
        f"  modified: {'success' if result.modified.success else 'FAILED'} "
        f"({result.modified.duration_seconds:.1f}s, {_format_size(result.modified.image_size_bytes)})"
    )
    if result.image_size_delta_bytes is not None:
        print(f"  image size change: {_format_delta(result.image_size_delta_bytes)}")

    failed = [(name, r) for name, r in (("original", result.original), ("modified", result.modified)) if not r.success]
    for name, r in failed:
        print(f"\n--- {name} build log (last 40 lines) ---")
        print("\n".join(r.log.splitlines()[-40:]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a modified Dockerfile by building it and comparing the outcome to the original.")
    parser.add_argument("original", help="Path to the original Dockerfile.")
    parser.add_argument("modified", help="Path to the modified Dockerfile.")
    parser.add_argument(
        "--context",
        help="Build context directory for both builds. Defaults to the original Dockerfile's own directory.",
    )
    parser.add_argument("--timeout", type=float, default=600, help="Per-build timeout in seconds (default: 600).")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON instead of a human-readable summary.")
    args = parser.parse_args(argv)

    try:
        result = validate(args.original, args.modified, context_dir=args.context, timeout_seconds=args.timeout)
    except (DockerUnavailableError, DockerDaemonUnavailableError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_human(result)

    return 0 if result.outcome.value in ("preserved", "improved") else 1


if __name__ == "__main__":
    sys.exit(main())
