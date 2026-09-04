"""Unit tests for the Hadolint adapter's own logic (parsing, filtering, error
handling), with `subprocess.run` and `shutil.which` mocked out. These do not require
the real `hadolint` binary -- see integration/test_hadolint_adapter.py for tests
against the actual tool."""

import json
import unittest
from unittest.mock import MagicMock, patch

from flakiscan.detection import hadolint_adapter


def _fake_completed_process(stdout: str, returncode: int = 1, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


class TestIsAvailable(unittest.TestCase):
    def test_true_when_binary_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/hadolint"):
            self.assertTrue(hadolint_adapter.is_available())

    def test_false_when_binary_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(hadolint_adapter.is_available())


class TestRun(unittest.TestCase):
    def test_raises_when_unavailable(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(hadolint_adapter.HadolintUnavailableError):
                hadolint_adapter.run("Dockerfile")

    def test_parses_and_filters_known_rules(self):
        stdout = json.dumps(
            [
                {"code": "DL3007", "line": 1, "message": "latest tag"},
                {"code": "DL9999", "line": 5, "message": "not a tracked rule"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process(stdout)),
        ):
            findings = hadolint_adapter.run("Dockerfile")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "DL3007")
        self.assertEqual(findings[0]["line_number"], 1)
        self.assertTrue(findings[0]["flakiness_relevant"])

    def test_dl3020_is_marked_not_flakiness_relevant(self):
        stdout = json.dumps([{"code": "DL3020", "line": 3, "message": "use COPY"}])
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process(stdout)),
        ):
            findings = hadolint_adapter.run("Dockerfile")

        self.assertFalse(findings[0]["flakiness_relevant"])

    def test_respects_ignore_map(self):
        stdout = json.dumps([{"code": "DL3007", "line": 1, "message": "latest tag"}])
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process(stdout)),
        ):
            findings = hadolint_adapter.run("Dockerfile", ignore_map={1: None})

        self.assertEqual(findings, [])

    def test_empty_stdout_yields_no_findings(self):
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process("")),
        ):
            self.assertEqual(hadolint_adapter.run("Dockerfile"), [])

    def test_raises_runtime_error_on_unexpected_exit_code(self):
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process("", returncode=2, stderr="boom")),
        ):
            with self.assertRaisesRegex(RuntimeError, "hadolint failed"):
                hadolint_adapter.run("Dockerfile")

    def test_raises_runtime_error_on_malformed_json(self):
        with (
            patch("shutil.which", return_value="/usr/bin/hadolint"),
            patch("subprocess.run", return_value=_fake_completed_process("not json")),
        ):
            with self.assertRaisesRegex(RuntimeError, "could not parse hadolint output"):
                hadolint_adapter.run("Dockerfile")


if __name__ == "__main__":
    unittest.main()
