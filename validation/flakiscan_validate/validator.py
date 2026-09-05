"""Compares an original and a modified Dockerfile by building both."""

from __future__ import annotations

from pathlib import Path

from flakiscan_validate import builder
from flakiscan_validate.result import ValidationResult, classify


def validate(
    original_dockerfile: str,
    modified_dockerfile: str,
    context_dir: str | None = None,
    timeout_seconds: float = 600,
) -> ValidationResult:
    """Build `original_dockerfile` and `modified_dockerfile` and classify the result.

    Both are built against the same `context_dir` (so COPY/ADD sources resolve
    identically for each), which defaults to the original Dockerfile's own directory.
    Neither build's image is kept afterward.

    Raises DockerUnavailableError or DockerDaemonUnavailableError (see `builder`) if
    Docker itself is not usable -- these propagate rather than being folded into the
    result, since they mean no conclusion about buildability was reached at all.
    """
    resolved_context = context_dir or str(Path(original_dockerfile).parent)

    original_result = builder.build(original_dockerfile, resolved_context, timeout_seconds=timeout_seconds)
    modified_result = builder.build(modified_dockerfile, resolved_context, timeout_seconds=timeout_seconds)

    outcome = classify(original_result.success, modified_result.success)
    return ValidationResult(outcome=outcome, original=original_result, modified=modified_result)
