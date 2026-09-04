"""Merge findings from Hadolint, Parfum, and the custom rule engine into one list.

Each source emits findings in its own raw dict format. This is the only module allowed
to construct `Finding` objects: it converts each source's raw output into the shared
schema and removes duplicates.
"""

from __future__ import annotations

from flakiscan.schema import Finding, ToolSource


def uniformise(
    hadolint_findings: list[dict],
    parfum_findings: list[dict],
    custom_findings: list[dict],
) -> list[Finding]:
    """Combine raw findings from all three sources into deduplicated Finding objects.

    Findings are deduplicated by (rule_id, line_number): if two sources report the same
    rule on the same line, only the first occurrence (in hadolint, parfum, custom order)
    is kept.
    """
    sources = (
        (hadolint_findings, ToolSource.HADOLINT),
        (parfum_findings, ToolSource.PARFUM),
        (custom_findings, ToolSource.CUSTOM),
    )

    seen: set[tuple[str, int | None]] = set()
    findings: list[Finding] = []

    for raw_findings, tool_source in sources:
        for raw in raw_findings:
            key = (raw["rule_id"], raw["line_number"])
            if key in seen:
                continue
            seen.add(key)

            findings.append(Finding(
                rule_id=raw["rule_id"],
                tool_source=tool_source,
                category=raw["category"],
                line_number=raw["line_number"],
                message=raw["message"],
                flakiness_relevant=raw["flakiness_relevant"],
            ))

    return findings
