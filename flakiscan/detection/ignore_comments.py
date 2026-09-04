"""`# flakiscan-ignore` suppression comments.

Each of the three detection sources recognises a `# flakiscan-ignore: <rule_id>`
comment (or a bare `# flakiscan-ignore`, suppressing every rule) placed on the line
immediately preceding the affected instruction -- the same suppression syntax applies
uniformly regardless of which source would otherwise report the finding. The comment
must precede the instruction, since a Dockerfile only recognises `#` as a comment at
the start of a line.

Limitation: for an instruction split across multiple physical lines with a trailing
backslash, this only suppresses a finding reported on the line directly below the
comment -- if a tool reports a finding on a later continuation line, the ignore comment
must be placed directly above *that* line, not above the instruction's first line.
"""

from __future__ import annotations

import re

_IGNORE_RE = re.compile(r"^\s*#\s*flakiscan-ignore\s*(?::\s*([A-Za-z0-9_]+))?\s*$")

# None means "ignore every rule on this line" (bare `# flakiscan-ignore`).
IgnoreMap = dict[int, "set[str] | None"]


def parse_ignore_map(dockerfile_path: str) -> IgnoreMap:
    """Scan `dockerfile_path` for flakiscan-ignore comments and return a map from the
    suppressed line number (1-indexed, the line *after* the comment) to either a set of
    ignored rule ids, or None if every rule is suppressed on that line."""
    with open(dockerfile_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    ignore_map: IgnoreMap = {}
    for i, line in enumerate(lines):
        match = _IGNORE_RE.match(line.rstrip("\n"))
        if not match:
            continue

        target_line = i + 2  # 1-indexed line number of the line below this comment
        rule_id = match.group(1)
        if rule_id is None:
            ignore_map[target_line] = None
        else:
            existing = ignore_map.get(target_line, set())
            if existing is not None:
                ignore_map[target_line] = existing | {rule_id}

    return ignore_map


def is_ignored(ignore_map: IgnoreMap, line_number: int | None, rule_id: str) -> bool:
    if line_number is None or line_number not in ignore_map:
        return False
    ignored_rules = ignore_map[line_number]
    return ignored_rules is None or rule_id in ignored_rules


def filter_ignored(findings: list[dict], ignore_map: IgnoreMap) -> list[dict]:
    """Drop any raw finding suppressed by a matching flakiscan-ignore comment."""
    return [f for f in findings if not is_ignored(ignore_map, f["line_number"], f["rule_id"])]
