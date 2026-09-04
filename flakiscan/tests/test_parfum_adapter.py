import unittest
from pathlib import Path

from flakiscan.detection import parfum_adapter
from flakiscan.schema import Category

FIXTURES = Path(__file__).parent / "fixtures"


@unittest.skipUnless(parfum_adapter.is_available(), "node / @tdurieux/docker-parfum not installed")
class TestParfumAdapter(unittest.TestCase):
    def test_flaky_fixture_flags_curl_and_apt_smells(self):
        findings = parfum_adapter.run(str(FIXTURES / "flaky.Dockerfile"))
        rule_ids = {f["rule_id"] for f in findings}

        self.assertIn("curlUseHttpsUrl", rule_ids)
        self.assertIn("aptGetInstallThenRemoveAptLists", rule_ids)

        for f in findings:
            self.assertIn(f["rule_id"], parfum_adapter.RULE_CATEGORIES)
            self.assertIsInstance(f["category"], Category)
            self.assertIsInstance(f["line_number"], int)

    def test_healthy_fixture_has_fewer_findings_than_flaky(self):
        flaky = parfum_adapter.run(str(FIXTURES / "flaky.Dockerfile"))
        healthy = parfum_adapter.run(str(FIXTURES / "healthy.Dockerfile"))
        self.assertLess(len(healthy), len(flaky))

    def test_bare_ignore_comment_suppresses_every_rule_on_the_line(self):
        without_ignore = parfum_adapter.run(str(FIXTURES / "ignored.Dockerfile"))
        self.assertTrue(any(f["line_number"] == 5 for f in without_ignore))

        with_ignore = parfum_adapter.run(str(FIXTURES / "ignored.Dockerfile"), {5: None})
        self.assertFalse(any(f["line_number"] == 5 for f in with_ignore))


if __name__ == "__main__":
    unittest.main()
