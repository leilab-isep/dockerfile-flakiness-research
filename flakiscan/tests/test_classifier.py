"""Every finding gets exactly one severity/weight, and info-level findings don't
contribute to flakiness_score."""

import unittest

from flakiscan.scoring.classifier import classify, flakiness_score
from flakiscan.schema import Category, Finding, Severity, ToolSource


def finding(rule_id, category, flakiness_relevant=True) -> Finding:
    return Finding(
        rule_id=rule_id,
        tool_source=ToolSource.HADOLINT,
        category=category,
        line_number=1,
        message="msg",
        flakiness_relevant=flakiness_relevant,
    )


class TestClassifier(unittest.TestCase):
    def test_every_finding_gets_severity_and_weight(self):
        findings = [
            finding("DL3007", Category.BASE_IMAGE),
            finding("DL3008", Category.DEPENDENCY),
            finding("curlUseFlagF", Category.NETWORK),
            finding("arg_no_default", Category.ENVIRONMENT),
            finding("DL4006", Category.REPRODUCIBILITY),
        ]
        classify(findings)
        for f in findings:
            self.assertIsNotNone(f.severity)
            self.assertIsNotNone(f.weight)

    def test_error_categories_get_weight_3(self):
        findings = [
            finding("DL3007", Category.BASE_IMAGE),
            finding("DL3008", Category.DEPENDENCY),
            finding("curlUseFlagF", Category.NETWORK),
        ]
        classify(findings)
        self.assertTrue(all(f.severity == Severity.ERROR and f.weight == 3 for f in findings))

    def test_environment_gets_warning_weight_1(self):
        findings = [finding("arg_no_default", Category.ENVIRONMENT)]
        classify(findings)
        self.assertEqual(findings[0].severity, Severity.WARNING)
        self.assertEqual(findings[0].weight, 1)

    def test_dl3020_best_practice_is_info_and_excluded_from_score(self):
        findings = [
            finding("DL3020", Category.BEST_PRACTICE, flakiness_relevant=False),
            finding("DL3008", Category.DEPENDENCY, flakiness_relevant=True),
        ]
        classify(findings)
        dl3020 = next(f for f in findings if f.rule_id == "DL3020")
        self.assertEqual(dl3020.severity, Severity.INFO)
        self.assertEqual(dl3020.weight, 0)

        score = flakiness_score(findings)
        self.assertEqual(score, 3)  # only DL3008's weight counts

    def test_flakiness_score_sums_only_relevant_findings(self):
        findings = [
            finding("DL3008", Category.DEPENDENCY, flakiness_relevant=True),
            finding("curlUseFlagF", Category.NETWORK, flakiness_relevant=True),
            finding("DL3020", Category.BEST_PRACTICE, flakiness_relevant=False),
        ]
        classify(findings)
        self.assertEqual(flakiness_score(findings), 6)


if __name__ == "__main__":
    unittest.main()
