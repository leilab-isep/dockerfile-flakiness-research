"""Findings from the three detection sources merge into one deduplicated list."""

import unittest

from flakiscan.detection.uniformise import uniformise
from flakiscan.schema import Category, ToolSource


def raw(rule_id: str, line: int, category=Category.DEPENDENCY, flakiness_relevant=True) -> dict:
    return {
        "rule_id": rule_id,
        "line_number": line,
        "message": "msg",
        "category": category,
        "flakiness_relevant": flakiness_relevant,
    }


class TestUniformise(unittest.TestCase):
    def test_merges_three_sources(self):
        findings = uniformise(
            hadolint_findings=[raw("DL3007", 1, Category.BASE_IMAGE)],
            parfum_findings=[raw("curlUseFlagF", 3, Category.NETWORK)],
            custom_findings=[raw("arg_no_default", 5, Category.ENVIRONMENT)],
        )
        self.assertEqual(len(findings), 3)
        sources = {f.tool_source for f in findings}
        self.assertEqual(sources, {ToolSource.HADOLINT, ToolSource.PARFUM, ToolSource.CUSTOM})

    def test_dedupes_same_rule_id_and_line_across_sources(self):
        # Two tools happening to report the same (rule_id, line_number) pair.
        findings = uniformise(
            hadolint_findings=[raw("DL3007", 1, Category.BASE_IMAGE)],
            parfum_findings=[raw("DL3007", 1, Category.BASE_IMAGE)],
            custom_findings=[],
        )
        self.assertEqual(len(findings), 1)

    def test_no_duplicate_rule_line_pairs_in_output(self):
        findings = uniformise(
            hadolint_findings=[raw("DL3007", 1), raw("DL3008", 2)],
            parfum_findings=[raw("DL3008", 2), raw("curlUseFlagF", 3)],
            custom_findings=[raw("arg_no_default", 5)],
        )
        keys = [(f.rule_id, f.line_number) for f in findings]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(findings), 4)


if __name__ == "__main__":
    unittest.main()
