"""Minimal Dockerfile parser used by the custom rule engine.

Hadolint and Parfum parse the Dockerfile themselves; this parser exists only so the
custom rule engine can reason about instructions and line numbers without shelling out
to either tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Instruction:
    line_number: int  # 1-indexed, the line the instruction *starts* on
    instruction: str  # e.g. "FROM", "RUN" (always upper-case)
    args: str
    raw: str
    end_line_number: int | None = None  # 1-indexed, last physical line (backslash continuations)

    def __post_init__(self) -> None:
        if self.end_line_number is None:
            self.end_line_number = self.line_number


_INSTRUCTION_RE = re.compile(r"^([A-Za-z]+)(?:\s+(.*))?$", re.DOTALL)

VALID_INSTRUCTIONS = {
    "FROM",
    "RUN",
    "CMD",
    "LABEL",
    "EXPOSE",
    "ENV",
    "ADD",
    "COPY",
    "ENTRYPOINT",
    "VOLUME",
    "USER",
    "WORKDIR",
    "ARG",
    "ONBUILD",
    "STOPSIGNAL",
    "HEALTHCHECK",
    "SHELL",
}


def parse_dockerfile(path: str) -> list[Instruction]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return _parse_lines(lines)


def _parse_lines(lines: list[str]) -> list[Instruction]:
    instructions: list[Instruction] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")

        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        start_line_number = i + 1
        full_line = line
        while full_line.rstrip().endswith("\\") and i + 1 < len(lines):
            full_line = full_line.rstrip()[:-1]
            i += 1
            full_line += " " + lines[i].rstrip("\n").lstrip()
        end_line_number = i + 1

        instruction = _parse_instruction(full_line, start_line_number, end_line_number)
        if instruction:
            instructions.append(instruction)

        i += 1

    return instructions


def _parse_instruction(line: str, line_number: int, end_line_number: int) -> Instruction | None:
    line = line.strip()
    if not line:
        return None

    match = _INSTRUCTION_RE.match(line)
    if not match:
        return None

    instruction = match.group(1).upper()
    if instruction not in VALID_INSTRUCTIONS:
        return None

    args = (match.group(2) or "").strip()
    return Instruction(
        line_number=line_number,
        instruction=instruction,
        args=args,
        raw=line,
        end_line_number=end_line_number,
    )
