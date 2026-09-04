"""Unit tests for the CLI's argument handling and output formatting, with `detect` and
`repair_dockerfile` mocked out so these do not depend on Hadolint, Docker Parfum, or
network access."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from flakiscan import cli
from flakiscan.detection.detector import DetectionResult
from flakiscan.refactoring.result import RepairAction, RepairReport
from flakiscan.schema import Category, Finding, ToolSource


def _fake_finding() -> Finding:
    return Finding(
        rule_id="DL3007",
        tool_source=ToolSource.HADOLINT,
        category=Category.BASE_IMAGE,
        line_number=1,
        message="latest tag",
        flakiness_relevant=True,
    )


class TestAnalyze(unittest.TestCase):
    def test_report_has_expected_shape(self):
        result = DetectionResult(findings=[_fake_finding()], warnings=["hadolint missing"], duration_seconds=0.5)
        with patch("flakiscan.cli.detect", return_value=result):
            report = cli.analyze("Dockerfile")

        self.assertEqual(report["dockerfile"], "Dockerfile")
        self.assertEqual(report["flakiness_score"], 3)
        self.assertEqual(report["summary"]["total_findings"], 1)
        self.assertEqual(report["summary"]["by_category"], {"base_image": 1})
        self.assertEqual(report["warnings"], ["hadolint missing"])
        self.assertEqual(report["duration_seconds"], 0.5)


class TestMainAnalyzeMode(unittest.TestCase):
    def test_human_readable_output(self):
        result = DetectionResult(findings=[_fake_finding()])
        stdout = io.StringIO()
        with patch("flakiscan.cli.detect", return_value=result), redirect_stdout(stdout):
            exit_code = cli.main(["Dockerfile"])

        self.assertEqual(exit_code, 0)
        self.assertIn("flakiness_score:", stdout.getvalue())
        self.assertIn("DL3007", stdout.getvalue())

    def test_json_output_is_valid_json(self):
        result = DetectionResult(findings=[_fake_finding()])
        stdout = io.StringIO()
        with patch("flakiscan.cli.detect", return_value=result), redirect_stdout(stdout):
            cli.main(["Dockerfile", "--json"])

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["findings"][0]["rule_id"], "DL3007")


class TestMainRepairMode(unittest.TestCase):
    def _fake_report(self) -> RepairReport:
        action = RepairAction(
            line_number=1,
            end_line_number=1,
            instruction="FROM",
            triggered_rule_ids=["explicit_latest"],
            applied_rule_ids=[],
            fallback_rule_ids=["explicit_latest"],
            rationale=[],
            original_text="FROM ubuntu:latest",
            new_text="# TODO(flakiscan): could not automatically fix: explicit_latest\nFROM ubuntu:latest",
        )
        return RepairReport(dockerfile="Dockerfile", actions=[action], patched_text="patched content\n")

    def test_prints_patched_text_to_stdout_and_summary_to_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("flakiscan.cli.repair_dockerfile", return_value=self._fake_report()),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main(["Dockerfile", "--repair"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "patched content\n")
        self.assertIn("explicit_latest", stderr.getvalue())

    def test_json_mode_includes_patched_text(self):
        stdout = io.StringIO()
        with (
            patch("flakiscan.cli.repair_dockerfile", return_value=self._fake_report()),
            redirect_stdout(stdout),
        ):
            cli.main(["Dockerfile", "--repair", "--json"])

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["patched_text"], "patched content\n")
        self.assertEqual(parsed["actions"][0]["instruction"], "FROM")

    def test_in_place_writes_file_and_prints_summary_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
            f.write("FROM ubuntu:latest\n")
            path = f.name

        try:
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch("flakiscan.cli.repair_dockerfile", return_value=self._fake_report()),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                cli.main([path, "--repair", "--in-place"])

            with open(path) as f:
                self.assertEqual(f.read(), "patched content\n")
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("explicit_latest", stderr.getvalue())
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
