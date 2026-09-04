"""Command-line entry point for FlakiScan.

Usage:
    python3 -m flakiscan.cli path/to/Dockerfile [--json]
    python3 -m flakiscan.cli path/to/Dockerfile --repair [--in-place] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys

from flakiscan.detection.detector import detect
from flakiscan.refactoring.engine import repair_dockerfile
from flakiscan.scoring.classifier import classify, flakiness_score


def analyze(dockerfile_path: str) -> dict:
    """Run detection and scoring on `dockerfile_path` and return a JSON-serializable report.

    The report contains the Dockerfile path, its overall flakiness_score, the full list
    of findings, a summary count by category, any tool-availability warnings, and the
    analysis duration in seconds.
    """
    result = detect(dockerfile_path)
    classify(result.findings)
    score = flakiness_score(result.findings)

    by_category: dict[str, int] = {}
    for finding in result.findings:
        by_category[finding.category.value] = by_category.get(finding.category.value, 0) + 1

    return {
        "dockerfile": dockerfile_path,
        "flakiness_score": score,
        "findings": [f.to_dict() for f in result.findings],
        "summary": {
            "total_findings": len(result.findings),
            "by_category": by_category,
        },
        "warnings": result.warnings,
        "duration_seconds": round(result.duration_seconds, 3),
    }


def _print_human(report: dict) -> None:
    print(f"FlakiScan report for {report['dockerfile']}")
    print(f"  flakiness_score: {report['flakiness_score']}")
    print(f"  findings: {report['summary']['total_findings']}")
    for category, count in sorted(report["summary"]["by_category"].items()):
        print(f"    {category}: {count}")
    if report["warnings"]:
        print("  warnings:")
        for warning in report["warnings"]:
            print(f"    - {warning}")
    print()
    for finding in report["findings"]:
        print(
            f"  [{finding['severity']:<7}] {finding['rule_id']:<32} "
            f"L{finding['line_number']:<4} ({finding['tool_source']}, {finding['category']}) "
            f"{finding['message']}"
        )


def _print_repair_summary(report) -> None:
    for action in report.actions:
        if action.applied_rule_ids:
            print(
                f"  L{action.line_number} ({action.instruction}): fixed {', '.join(action.applied_rule_ids)}",
                file=sys.stderr,
            )
        if action.fallback_rule_ids:
            print(
                f"  L{action.line_number} ({action.instruction}): left as TODO: {', '.join(action.fallback_rule_ids)}",
                file=sys.stderr,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a Dockerfile for build-flakiness risks.")
    parser.add_argument("dockerfile", help="Path to the Dockerfile to analyze.")
    parser.add_argument("--json", action="store_true", help="Print the raw JSON report instead of the human-readable summary.")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Attempt to automatically fix findings and print the patched Dockerfile.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="With --repair, overwrite the input file instead of printing to stdout.",
    )
    args = parser.parse_args(argv)

    if args.repair:
        report = repair_dockerfile(args.dockerfile)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        elif args.in_place:
            with open(args.dockerfile, "w", encoding="utf-8") as f:
                f.write(report.patched_text)
            _print_repair_summary(report)
        else:
            print(report.patched_text, end="")
            _print_repair_summary(report)
        return 0

    report = analyze(args.dockerfile)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
