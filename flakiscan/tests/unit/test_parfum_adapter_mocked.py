"""Unit tests for the Docker Parfum adapter's own logic (parsing, filtering, error
handling), with `subprocess.run`, `shutil.which`, and the filesystem check for the
installed package mocked out. These do not require Node.js or docker-parfum to be
installed -- see integration/test_parfum_adapter.py for tests against the real tool."""

import json
import unittest
from unittest.mock import MagicMock, patch

from flakiscan.detection import parfum_adapter


def _fake_process(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def _run_side_effect(npm_root_stdout: str, node_stdout: str, node_returncode: int = 0):
    def side_effect(argv, **kwargs):
        if argv[:2] == ["npm", "root"]:
            return _fake_process(npm_root_stdout)
        return _fake_process(node_stdout, returncode=node_returncode)

    return side_effect


class TestIsAvailable(unittest.TestCase):
    def test_false_when_node_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(parfum_adapter.is_available())

    def test_false_when_package_not_installed(self):
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("subprocess.run", return_value=_fake_process("/nonexistent/node_modules")),
        ):
            self.assertFalse(parfum_adapter.is_available())

    def test_true_when_node_and_package_present(self):
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("subprocess.run", return_value=_fake_process("/usr/lib/node_modules")),
            patch("pathlib.Path.exists", return_value=True),
        ):
            self.assertTrue(parfum_adapter.is_available())


class TestRun(unittest.TestCase):
    def test_raises_when_unavailable(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(parfum_adapter.ParfumUnavailableError):
                parfum_adapter.run("Dockerfile")

    def test_parses_and_filters_known_rules(self):
        node_stdout = json.dumps(
            [
                {"rule_id": "curlUseFlagF", "line_number": 3, "message": "add -f"},
                {"rule_id": "notARealRule", "line_number": 9, "message": "untracked"},
            ]
        )
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_run_side_effect("/root", node_stdout)),
        ):
            findings = parfum_adapter.run("Dockerfile")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule_id"], "curlUseFlagF")
        self.assertTrue(findings[0]["flakiness_relevant"])

    def test_respects_ignore_map(self):
        node_stdout = json.dumps([{"rule_id": "curlUseFlagF", "line_number": 3, "message": "add -f"}])
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_run_side_effect("/root", node_stdout)),
        ):
            findings = parfum_adapter.run("Dockerfile", ignore_map={3: None})

        self.assertEqual(findings, [])

    def test_raises_runtime_error_on_nonzero_exit(self):
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_run_side_effect("/root", "", node_returncode=1)),
        ):
            with self.assertRaisesRegex(RuntimeError, "docker-parfum failed"):
                parfum_adapter.run("Dockerfile")

    def test_raises_runtime_error_on_malformed_json(self):
        with (
            patch("shutil.which", return_value="/usr/bin/node"),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=_run_side_effect("/root", "not json")),
        ):
            with self.assertRaisesRegex(RuntimeError, "could not parse docker-parfum output"):
                parfum_adapter.run("Dockerfile")


if __name__ == "__main__":
    unittest.main()
