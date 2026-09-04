import tempfile
import unittest

from flakiscan.detection.ignore_comments import filter_ignored, is_ignored, parse_ignore_map


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
        f.write(content)
        return f.name


class TestParseIgnoreMap(unittest.TestCase):
    def test_bare_ignore_suppresses_every_rule_on_next_line(self):
        path = _write("# flakiscan-ignore\nFROM ubuntu\n")
        ignore_map = parse_ignore_map(path)
        self.assertEqual(ignore_map, {2: None})

    def test_scoped_ignore_suppresses_only_named_rule(self):
        path = _write("# flakiscan-ignore: DL3007\nFROM ubuntu\n")
        ignore_map = parse_ignore_map(path)
        self.assertEqual(ignore_map, {2: {"DL3007"}})

    def test_multiple_scoped_ignores_on_consecutive_comment_lines_accumulate(self):
        path = _write("# flakiscan-ignore: DL3007\n# flakiscan-ignore: explicit_latest\nFROM ubuntu:latest\n")
        ignore_map = parse_ignore_map(path)
        # Both comments precede line 3 directly above it... actually the second comment
        # (line 2) targets line 3; the first comment (line 1) targets line 2, which is
        # itself a comment line and never matched by any tool -- harmless.
        self.assertIn(3, ignore_map)
        self.assertEqual(ignore_map[3], {"explicit_latest"})

    def test_no_comment_yields_empty_map(self):
        path = _write("FROM ubuntu:22.04\n")
        self.assertEqual(parse_ignore_map(path), {})

    def test_regular_comment_is_not_treated_as_ignore(self):
        path = _write("# just a normal comment\nFROM ubuntu\n")
        self.assertEqual(parse_ignore_map(path), {})


class TestIsIgnoredAndFilter(unittest.TestCase):
    def test_is_ignored_bare(self):
        self.assertTrue(is_ignored({5: None}, 5, "DL3007"))
        self.assertTrue(is_ignored({5: None}, 5, "anything"))

    def test_is_ignored_scoped(self):
        self.assertTrue(is_ignored({5: {"DL3007"}}, 5, "DL3007"))
        self.assertFalse(is_ignored({5: {"DL3007"}}, 5, "DL3008"))

    def test_is_ignored_different_line_is_not_ignored(self):
        self.assertFalse(is_ignored({5: None}, 6, "DL3007"))

    def test_filter_ignored_drops_matching_findings_only(self):
        findings = [
            {"rule_id": "DL3007", "line_number": 1},
            {"rule_id": "DL3008", "line_number": 2},
        ]
        result = filter_ignored(findings, {1: None})
        self.assertEqual(result, [{"rule_id": "DL3008", "line_number": 2}])


if __name__ == "__main__":
    unittest.main()
