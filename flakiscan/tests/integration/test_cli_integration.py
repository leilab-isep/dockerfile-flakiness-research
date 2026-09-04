"""End-to-end: parse -> detect (3 sources) -> uniformise -> classify -> flakiness_score."""

import json
import unittest
from pathlib import Path

from flakiscan.cli import analyze

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestEndToEnd(unittest.TestCase):
    def test_flaky_fixture_scores_higher_than_healthy(self):
        flaky_report = analyze(str(FIXTURES / "flaky.Dockerfile"))
        healthy_report = analyze(str(FIXTURES / "healthy.Dockerfile"))

        self.assertGreater(flaky_report["flakiness_score"], healthy_report["flakiness_score"])
        self.assertGreater(flaky_report["summary"]["total_findings"], 0)

    def test_report_is_json_serializable(self):
        report = analyze(str(FIXTURES / "flaky.Dockerfile"))
        json.dumps(report)  # must not raise

    def test_every_finding_has_non_null_category_after_scoring(self):
        report = analyze(str(FIXTURES / "flaky.Dockerfile"))
        for finding in report["findings"]:
            self.assertIsNotNone(finding["category"])
            self.assertIsNotNone(finding["severity"])

    def test_flakiscan_ignore_comments_suppress_findings_end_to_end(self):
        report = analyze(str(FIXTURES / "ignored.Dockerfile"))

        # Bare ignore on line 4 must suppress every source's finding on line 5.
        self.assertFalse(any(f["line_number"] == 5 for f in report["findings"]))

        # Scoped ignore on line 1 (DL3007 only) must not touch the custom engine's
        # explicit_latest finding on the same line 2.
        rule_ids_at_line_2 = {f["rule_id"] for f in report["findings"] if f["line_number"] == 2}
        self.assertNotIn("DL3007", rule_ids_at_line_2)
        self.assertIn("explicit_latest", rule_ids_at_line_2)


if __name__ == "__main__":
    unittest.main()
