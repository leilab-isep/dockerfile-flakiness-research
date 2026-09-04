"""Assigns severity and weight to findings, and aggregates a Dockerfile's flakiness score.

Categories are graded by how likely a finding is to make a build's outcome depend on
*when* it runs rather than only on the Dockerfile's contents:

- `error` (weight 3): dependency, network, security, base_image, and reproducibility
  findings. Each describes a condition that can differ between two builds of the same
  Dockerfile run at different times -- a package version becoming unavailable, a
  registry going down, a credential expiring, a base image tag being retagged, or a
  shell pipeline silently swallowing an upstream failure.
- `warning` (weight 1): environment findings. These often indicate a real but lower-
  probability risk, or one that also depends on factors outside the Dockerfile (such as
  the build host's own environment).
- `info` (weight 0): best-practice findings that are useful maintainability feedback
  but do not, on their own, indicate a build whose outcome can change between runs.
"""

from __future__ import annotations

from flakiscan.schema import Category, Finding, Severity

_ERROR_CATEGORIES = {
    Category.DEPENDENCY,
    Category.NETWORK,
    Category.SECURITY,
    Category.BASE_IMAGE,
    Category.REPRODUCIBILITY,
}
_WARNING_CATEGORIES = {Category.ENVIRONMENT}
_INFO_CATEGORIES = {Category.BEST_PRACTICE}


def classify(findings: list[Finding]) -> None:
    """Assign a severity and weight to each finding, in place.

    Raises ValueError if a finding's category is not one this function knows how to
    grade.
    """
    for finding in findings:
        if finding.category in _ERROR_CATEGORIES:
            finding.apply_severity(Severity.ERROR)
        elif finding.category in _WARNING_CATEGORIES:
            finding.apply_severity(Severity.WARNING)
        elif finding.category in _INFO_CATEGORIES:
            finding.apply_severity(Severity.INFO)
        else:
            raise ValueError(f"Unclassifiable category: {finding.category!r}")


def flakiness_score(findings: list[Finding]) -> int:
    """Return the total weight of findings that contribute to flakiness risk.

    Findings must already be classified (see `classify`). Findings with
    flakiness_relevant=False -- reported for informational purposes only -- are
    excluded regardless of their weight.
    """
    return sum(f.weight or 0 for f in findings if f.flakiness_relevant)
