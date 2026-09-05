"""Data types for a build attempt and the outcome of comparing two of them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    """How a modified Dockerfile's buildability compares to the original's.

    PRESERVED: both built successfully -- the modification is safe to accept.
    REGRESSED: the original built but the modified version does not -- the
        modification introduced a new failure and should be rejected.
    IMPROVED: the original failed but the modified version builds -- accepted, but not
        attributable to a specific intended fix without more context than a plain
        build comparison provides.
    PRE_EXISTING_FAILURE: both failed -- the failure predates the modification and
        should be excluded from any accuracy count based on this comparison.
    """

    PRESERVED = "preserved"
    REGRESSED = "regressed"
    IMPROVED = "improved"
    PRE_EXISTING_FAILURE = "pre_existing_failure"


def classify(original_succeeded: bool, modified_succeeded: bool) -> Outcome:
    """Return the Outcome for a given pair of build results."""
    if original_succeeded and modified_succeeded:
        return Outcome.PRESERVED
    if original_succeeded and not modified_succeeded:
        return Outcome.REGRESSED
    if not original_succeeded and modified_succeeded:
        return Outcome.IMPROVED
    return Outcome.PRE_EXISTING_FAILURE


@dataclass
class BuildResult:
    """The outcome of a single `docker build` attempt."""

    success: bool
    log: str
    duration_seconds: float
    timed_out: bool = False

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "log": self.log,
            "duration_seconds": self.duration_seconds,
            "timed_out": self.timed_out,
        }


@dataclass
class ValidationResult:
    """The result of validating one modified Dockerfile against its original."""

    outcome: Outcome
    original: BuildResult
    modified: BuildResult

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "original": self.original.to_dict(),
            "modified": self.modified.to_dict(),
        }
