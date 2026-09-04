"""Data types describing the outcome of repairing one Dockerfile instruction."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SubRepairResult:
    """Result of one repair function attempting to fix some of an instruction's findings.

    `text` is the instruction text after this function ran -- unchanged from its input
    if the function found nothing it could fix. `handled_rule_ids` lists which of the
    rule ids it was asked to address were actually fixed; any requested rule id not
    listed here is left for the caller to report as unfixed.
    """

    text: str
    handled_rule_ids: set[str] = field(default_factory=set)
    rationale: list[str] = field(default_factory=list)


@dataclass
class RepairAction:
    """Everything that happened when repairing one instruction."""

    line_number: int
    end_line_number: int
    instruction: str
    triggered_rule_ids: list[str]
    applied_rule_ids: list[str]
    fallback_rule_ids: list[str]
    rationale: list[str]
    original_text: str
    new_text: str

    @property
    def changed(self) -> bool:
        return self.new_text != self.original_text

    def to_dict(self) -> dict:
        return {
            "line_number": self.line_number,
            "end_line_number": self.end_line_number,
            "instruction": self.instruction,
            "triggered_rule_ids": self.triggered_rule_ids,
            "applied_rule_ids": self.applied_rule_ids,
            "fallback_rule_ids": self.fallback_rule_ids,
            "rationale": self.rationale,
            "changed": self.changed,
        }


@dataclass
class RepairReport:
    """The result of repairing a whole Dockerfile."""

    dockerfile: str
    actions: list[RepairAction]
    patched_text: str

    def to_dict(self) -> dict:
        return {
            "dockerfile": self.dockerfile,
            "actions": [a.to_dict() for a in self.actions],
            "patched_text": self.patched_text,
        }
