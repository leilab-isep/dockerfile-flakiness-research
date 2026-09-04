"""Unit tests for the repair engine's own orchestration logic (grouping findings by
instruction, applying sub-repairs, inserting fallback comments), with `detect` mocked
out so these do not require Hadolint or Docker Parfum. See
integration/test_refactoring_engine.py for tests against the real detection pipeline."""

import tempfile
import unittest
from unittest.mock import patch

from flakiscan.detection.detector import DetectionResult
from flakiscan.refactoring.engine import repair_dockerfile
from flakiscan.refactoring.resolvers import Resolvers
from flakiscan.schema import Category, Finding, ToolSource

FAKE_RESOLVERS = Resolvers(docker_hub_digest=lambda repo, tag: "sha256:" + "a" * 64)


def _finding(rule_id: str, line: int, category=Category.BASE_IMAGE) -> Finding:
    return Finding(
        rule_id=rule_id,
        tool_source=ToolSource.CUSTOM,
        category=category,
        line_number=line,
        message="msg",
        flakiness_relevant=True,
    )


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
        f.write(content)
        return f.name


class TestRepairDockerfile(unittest.TestCase):
    def test_applies_a_fix_for_a_single_finding(self):
        path = _write("FROM ubuntu:latest\n")
        result = DetectionResult(findings=[_finding("explicit_latest", 1)])

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(len(report.actions), 1)
        self.assertEqual(report.actions[0].applied_rule_ids, ["explicit_latest"])
        self.assertIn("@sha256:", report.patched_text)

    def test_groups_multiple_findings_on_the_same_instruction(self):
        path = _write("RUN apt-get install curl\n")
        result = DetectionResult(
            findings=[
                _finding("aptGetInstallUseNoRec", 1, Category.DEPENDENCY),
                _finding("aptGetInstallUseY", 1, Category.DEPENDENCY),
            ]
        )

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(len(report.actions), 1)
        self.assertEqual(set(report.actions[0].applied_rule_ids), {"aptGetInstallUseNoRec", "aptGetInstallUseY"})

    def test_finding_on_a_continuation_line_maps_to_the_owning_instruction(self):
        path = _write("RUN apt-get update && \\\n    apt-get install -y curl\n")
        # Hadolint-style: the finding is reported on the continuation line, not the
        # instruction's first line.
        result = DetectionResult(findings=[_finding("aptGetInstallUseNoRec", 2, Category.DEPENDENCY)])

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(len(report.actions), 1)
        self.assertEqual(report.actions[0].line_number, 1)
        self.assertIn("--no-install-recommends", report.patched_text)

    def test_finding_with_no_owning_instruction_is_silently_skipped(self):
        path = _write("# just a comment\nFROM ubuntu:22.04\n")
        result = DetectionResult(findings=[_finding("explicit_latest", 1)])  # line 1 is a comment, not an instruction

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(report.actions, [])
        self.assertEqual(report.patched_text, "# just a comment\nFROM ubuntu:22.04\n")

    def test_unfixable_finding_produces_a_todo_comment(self):
        path = _write("ARG BUILD_ENV\n")
        result = DetectionResult(findings=[_finding("arg_no_default", 1, Category.ENVIRONMENT)])

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(report.actions[0].fallback_rule_ids, ["arg_no_default"])
        self.assertTrue(report.patched_text.startswith("# TODO(flakiscan)"))

    def test_no_findings_means_no_actions_and_unchanged_text(self):
        path = _write("FROM ubuntu:22.04\n")
        result = DetectionResult(findings=[])

        with patch("flakiscan.refactoring.engine.detect", return_value=result):
            report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)

        self.assertEqual(report.actions, [])
        self.assertEqual(report.patched_text, "FROM ubuntu:22.04\n")


if __name__ == "__main__":
    unittest.main()
