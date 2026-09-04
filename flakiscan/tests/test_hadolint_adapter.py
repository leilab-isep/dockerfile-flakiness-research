import unittest
from pathlib import Path

from flakiscan.detection import hadolint_adapter
from flakiscan.schema import Category

FIXTURES = Path(__file__).parent / "fixtures"


@unittest.skipUnless(hadolint_adapter.is_available(), "hadolint binary not installed")
class TestHadolintAdapter(unittest.TestCase):
    def test_flaky_fixture_flags_latest_tag_and_unpinned_installs(self):
        findings = hadolint_adapter.run(str(FIXTURES / "flaky.Dockerfile"))
        rule_ids = {f["rule_id"] for f in findings}

        self.assertIn("DL3007", rule_ids)  # FROM ubuntu:latest
        self.assertIn("DL3008", rule_ids)  # unpinned apt-get install

        for f in findings:
            self.assertIn(f["rule_id"], hadolint_adapter.RULE_CATEGORIES)
            self.assertIsInstance(f["category"], Category)
            self.assertIsInstance(f["line_number"], int)

    def test_only_flakiness_relevant_rules_are_kept(self):
        findings = hadolint_adapter.run(str(FIXTURES / "flaky.Dockerfile"))
        for f in findings:
            self.assertIn(f["rule_id"], hadolint_adapter.RULE_CATEGORIES)

    def test_healthy_fixture_has_no_dl3007(self):
        findings = hadolint_adapter.run(str(FIXTURES / "healthy.Dockerfile"))
        rule_ids = {f["rule_id"] for f in findings}
        self.assertNotIn("DL3007", rule_ids)

    def test_scoped_ignore_comment_suppresses_only_named_rule(self):
        ignore_map = {2: {"DL3007"}}
        findings = hadolint_adapter.run(str(FIXTURES / "ignored.Dockerfile"), ignore_map)
        rule_ids = {f["rule_id"] for f in findings}
        self.assertNotIn("DL3007", rule_ids)

    def test_bare_ignore_comment_suppresses_every_rule_on_the_line(self):
        ignore_map = {5: None}
        findings = hadolint_adapter.run(str(FIXTURES / "ignored.Dockerfile"), ignore_map)
        for f in findings:
            self.assertNotEqual(f["line_number"], 5)


if __name__ == "__main__":
    unittest.main()
