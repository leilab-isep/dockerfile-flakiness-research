"""Orchestrates repairing a Dockerfile: detect findings, fix what can be fixed, and
leave a `# TODO` comment for anything that can't."""

from __future__ import annotations

import re

from flakiscan.detection.detector import detect
from flakiscan.detection.dockerfile_parser import Instruction, parse_dockerfile
from flakiscan.refactoring.resolvers import DEFAULT_RESOLVERS, Resolvers
from flakiscan.refactoring.result import RepairAction, RepairReport
from flakiscan.refactoring.rules import SUB_REPAIRS
from flakiscan.schema import Finding


def _owning_instruction(instructions: list[Instruction], line_number: int) -> Instruction | None:
    for inst in instructions:
        end = inst.end_line_number if inst.end_line_number is not None else inst.line_number
        if inst.line_number <= line_number <= end:
            return inst
    return None


def _group_findings_by_instruction(findings: list[Finding], instructions: list[Instruction]) -> dict[int, tuple[Instruction, list[Finding]]]:
    groups: dict[int, tuple[Instruction, list[Finding]]] = {}
    for finding in findings:
        inst = _owning_instruction(instructions, finding.line_number)
        if inst is None:
            continue  # No instruction claims this line; nothing to rewrite.
        groups.setdefault(inst.line_number, (inst, []))[1].append(finding)
    return groups


def _apply_sub_repairs(text: str, triggered: set[str], resolvers: Resolvers) -> tuple[str, set[str], list[str]]:
    handled: set[str] = set()
    rationale: list[str] = []

    for rule_ids, repair_fn in SUB_REPAIRS:
        relevant = triggered & rule_ids
        if not relevant:
            continue
        result = repair_fn(text, relevant, resolvers)
        text = result.text
        handled |= result.handled_rule_ids
        rationale += result.rationale

    return text, handled, rationale


_FALLBACK_COMMENT_RE = re.compile(r"^\s*#\s*TODO\(flakiscan\):")
_IGNORE_COMMENT_RE = re.compile(r"^\s*#\s*flakiscan-ignore\b")


def _fallback_comment(unhandled: set[str]) -> str:
    rule_list = ", ".join(sorted(unhandled))
    return f"# TODO(flakiscan): could not automatically fix: {rule_list}"


def _find_prefix(original_lines: list[str], instruction_start_line: int) -> tuple[int, str | None]:
    """Look above `instruction_start_line` for a `# flakiscan-ignore` comment and any
    `# TODO(flakiscan)` comment(s) from a previous repair run.

    Returns the 1-indexed line to start replacing from, and the exact text of the
    ignore comment if one is present (None otherwise). The ignore comment must stay on
    the line *directly* above the instruction for its own suppression logic to keep
    matching it -- so when a TODO comment also needs to be (re)written, it goes above
    the ignore comment rather than between it and the instruction. Without this, a
    repair run would push the ignore comment down by inserting a TODO line right above
    the instruction, silently breaking which finding the ignore comment suppresses.
    """
    line_index = instruction_start_line - 1  # 0-indexed line of the instruction itself
    ignore_comment = None
    if line_index > 0 and _IGNORE_COMMENT_RE.match(original_lines[line_index - 1]):
        ignore_comment = original_lines[line_index - 1].rstrip("\n")
        line_index -= 1
    while line_index > 0 and _FALLBACK_COMMENT_RE.match(original_lines[line_index - 1]):
        line_index -= 1
    return line_index + 1, ignore_comment


def repair_dockerfile(dockerfile_path: str, resolvers: Resolvers = DEFAULT_RESOLVERS) -> RepairReport:
    """Detect and repair flakiness findings in `dockerfile_path`.

    Returns a RepairReport describing what changed, without writing anything to disk --
    write `report.patched_text` to a file yourself if you want to keep it. Every
    instruction with at least one finding is reconsidered; a finding this engine has no
    automated fix for (or could not resolve a required external value for) gets a
    `# TODO` comment listing the unresolved rule ids, rather than being silently
    dropped from the output.

    Raises OSError if `dockerfile_path` cannot be read. Network-dependent rules (base
    image digest, package version, git tag, and download-checksum resolution) fail
    gracefully to the TODO-comment fallback rather than raising.
    """
    with open(dockerfile_path, "r", encoding="utf-8") as f:
        original_lines = f.readlines()

    instructions = parse_dockerfile(dockerfile_path)
    detection_result = detect(dockerfile_path)
    groups = _group_findings_by_instruction(detection_result.findings, instructions)

    actions: list[RepairAction] = []
    patched_lines = list(original_lines)
    line_offset = 0

    for start_line in sorted(groups):
        inst, findings = groups[start_line]
        end_line = inst.end_line_number if inst.end_line_number is not None else inst.line_number
        triggered_rule_ids = sorted({f.rule_id for f in findings})

        original_block = original_lines[start_line - 1 : end_line]
        original_text = "".join(original_block).rstrip("\n")

        new_text, handled, rationale = _apply_sub_repairs(original_text, set(triggered_rule_ids), resolvers)
        unhandled = set(triggered_rule_ids) - handled

        actions.append(
            RepairAction(
                line_number=start_line,
                end_line_number=end_line,
                instruction=inst.instruction,
                triggered_rule_ids=triggered_rule_ids,
                applied_rule_ids=sorted(handled),
                fallback_rule_ids=sorted(unhandled),
                rationale=rationale,
                original_text=original_text,
                new_text=_fallback_comment(unhandled) + "\n" + new_text if unhandled else new_text,
            )
        )

        patch_start_line, ignore_comment = _find_prefix(original_lines, start_line)
        output_lines = []
        if unhandled:
            output_lines.append(_fallback_comment(unhandled))
        if ignore_comment is not None:
            output_lines.append(ignore_comment)
        output_lines.extend(new_text.split("\n"))

        replacement_lines = [line + "\n" for line in output_lines]
        patch_start = patch_start_line - 1 + line_offset
        patch_end = end_line + line_offset
        patched_lines[patch_start:patch_end] = replacement_lines
        line_offset += len(replacement_lines) - (end_line - patch_start_line + 1)

    return RepairReport(
        dockerfile=dockerfile_path,
        actions=actions,
        patched_text="".join(patched_lines),
    )
