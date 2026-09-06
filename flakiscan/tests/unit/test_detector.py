"""Unit tests for the detection orchestrator's own logic (merging, warnings), with the
Hadolint and Parfum adapters mocked out. See integration/test_detector_no_docker_build.py
for a test against the real adapters."""

import unittest
from pathlib import Path
from unittest.mock import patch

from flakiscan.detection import detector
from flakiscan.schema import Category

FIXTURE = str(Path(__file__).parent.parent / "fixtures" / "flaky.Dockerfile")


def _raw(rule_id: str, line: int, category=Category.DEPENDENCY) -> dict:
    return {
        "rule_id": rule_id,
        "line_number": line,
        "message": "msg",
        "category": category,
        "flakiness_relevant": True,
    }


class TestDetect(unittest.TestCase):
    def test_merges_findings_from_all_available_sources(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=True),
            patch("flakiscan.detection.hadolint_adapter.run", return_value=[_raw("DL3007", 1)]),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=True),
            patch("flakiscan.detection.parfum_adapter.run", return_value=[_raw("curlUseFlagF", 3)]),
        ):
            result = detector.detect(FIXTURE)

        rule_ids = {f.rule_id for f in result.findings}
        self.assertIn("DL3007", rule_ids)
        self.assertIn("curlUseFlagF", rule_ids)
        self.assertEqual(result.warnings, [])

    def test_records_a_warning_when_hadolint_unavailable(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=False),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=True),
            patch("flakiscan.detection.parfum_adapter.run", return_value=[]),
        ):
            result = detector.detect(FIXTURE)

        self.assertEqual(len(result.warnings), 1)
        self.assertIn("hadolint", result.warnings[0])

    def test_records_a_warning_when_parfum_unavailable(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=True),
            patch("flakiscan.detection.hadolint_adapter.run", return_value=[]),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=False),
        ):
            result = detector.detect(FIXTURE)

        self.assertEqual(len(result.warnings), 1)
        self.assertIn("docker-parfum", result.warnings[0])

    def test_still_returns_custom_rule_findings_when_both_tools_unavailable(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=False),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=False),
        ):
            result = detector.detect(FIXTURE)

        self.assertEqual(len(result.warnings), 2)
        custom_findings = [f for f in result.findings if f.tool_source.value == "custom"]
        self.assertTrue(custom_findings)

    def test_still_returns_hadolint_findings_when_parfum_crashes(self):
        # A Parfum crash (its underlying parser cannot handle every real-world
        # Dockerfile, e.g. Windows/PowerShell RUN commands) must not take down
        # Hadolint's or the custom engine's otherwise-valid findings for the same file.
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=True),
            patch("flakiscan.detection.hadolint_adapter.run", return_value=[_raw("DL3007", 1)]),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=True),
            patch("flakiscan.detection.parfum_adapter.run", side_effect=RuntimeError("docker-parfum failed: boom")),
        ):
            result = detector.detect(FIXTURE)

        rule_ids = {f.rule_id for f in result.findings}
        self.assertIn("DL3007", rule_ids)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("docker-parfum", result.warnings[0])
        self.assertIn("boom", result.warnings[0])

    def test_still_returns_parfum_findings_when_hadolint_crashes(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=True),
            patch("flakiscan.detection.hadolint_adapter.run", side_effect=RuntimeError("hadolint exploded")),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=True),
            patch("flakiscan.detection.parfum_adapter.run", return_value=[_raw("curlUseFlagF", 3)]),
        ):
            result = detector.detect(FIXTURE)

        rule_ids = {f.rule_id for f in result.findings}
        self.assertIn("curlUseFlagF", rule_ids)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("hadolint", result.warnings[0])

    def test_duration_is_recorded(self):
        with (
            patch("flakiscan.detection.hadolint_adapter.is_available", return_value=False),
            patch("flakiscan.detection.parfum_adapter.is_available", return_value=False),
        ):
            result = detector.detect(FIXTURE)

        self.assertGreaterEqual(result.duration_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
