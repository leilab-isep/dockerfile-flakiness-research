"""Common finding schema shared by every FlakiScan component.

Every detection source (Hadolint, Parfum, the custom rule engine) emits findings in its
own native format. `detection/uniformise.py` is the only place allowed to construct a
Finding directly from adapter output; every other component (scoring, the CLI) consumes
Finding objects only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolSource(str, Enum):
    HADOLINT = "hadolint"
    PARFUM = "parfum"
    CUSTOM = "custom"


class Category(str, Enum):
    """The kind of flakiness risk a finding represents.

    BEST_PRACTICE is for findings that are useful style/maintainability feedback but are
    not evidence of a build that could behave differently between runs; such findings
    are reported but excluded from flakiness_score.
    """

    DEPENDENCY = "dependency"
    NETWORK = "network"
    ENVIRONMENT = "environment"
    BASE_IMAGE = "base_image"
    SECURITY = "security"
    REPRODUCIBILITY = "reproducibility"
    BEST_PRACTICE = "best_practice"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Numeric contribution of each severity level to a Dockerfile's flakiness_score.
SEVERITY_WEIGHT = {
    Severity.ERROR: 3,
    Severity.WARNING: 1,
    Severity.INFO: 0,
}


@dataclass
class Finding:
    """A single normalized detection result, independent of which tool produced it."""

    rule_id: str
    tool_source: ToolSource
    category: Category
    line_number: int
    message: str
    flakiness_relevant: bool
    severity: Severity | None = None
    weight: int | None = None
    snippet: str | None = field(default=None, compare=False)

    def apply_severity(self, severity: Severity) -> None:
        self.severity = severity
        self.weight = SEVERITY_WEIGHT[severity]

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "tool_source": self.tool_source.value,
            "category": self.category.value,
            "severity": self.severity.value if self.severity else None,
            "weight": self.weight,
            "line_number": self.line_number,
            "message": self.message,
            "flakiness_relevant": self.flakiness_relevant,
        }
