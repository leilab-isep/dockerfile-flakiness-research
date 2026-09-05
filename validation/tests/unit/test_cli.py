"""Unit tests for the CLI's argument handling and output formatting, with `validate`
mocked out."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from flakiscan_validate import cli
from flakiscan_validate.builder import DockerUnavailableError
from flakiscan_validate.result import BuildResult, Outcome, ValidationResult


def _result(outcome: Outcome, original_success: bool, modified_success: bool) -> ValidationResult:
    return ValidationResult(
        outcome=outcome,
        original=BuildResult(success=original_success, log="original log", duration_seconds=1.0),
        modified=BuildResult(success=modified_success, log="modified log", duration_seconds=1.0),
    )


class TestMain(unittest.TestCase):
    def test_preserved_exits_zero_and_prints_summary(self):
        stdout = io.StringIO()
        with patch("flakiscan_validate.cli.validate", return_value=_result(Outcome.PRESERVED, True, True)):
            with redirect_stdout(stdout):
                exit_code = cli.main(["orig.Dockerfile", "mod.Dockerfile"])

        self.assertEqual(exit_code, 0)
        self.assertIn("preserved", stdout.getvalue())

    def test_regressed_exits_nonzero_and_prints_failure_log(self):
        stdout = io.StringIO()
        with patch("flakiscan_validate.cli.validate", return_value=_result(Outcome.REGRESSED, True, False)):
            with redirect_stdout(stdout):
                exit_code = cli.main(["orig.Dockerfile", "mod.Dockerfile"])

        self.assertEqual(exit_code, 1)
        self.assertIn("regressed", stdout.getvalue())
        self.assertIn("modified log", stdout.getvalue())

    def test_improved_exits_zero(self):
        with redirect_stdout(io.StringIO()):
            with patch("flakiscan_validate.cli.validate", return_value=_result(Outcome.IMPROVED, False, True)):
                exit_code = cli.main(["orig.Dockerfile", "mod.Dockerfile"])
        self.assertEqual(exit_code, 0)

    def test_pre_existing_failure_exits_nonzero(self):
        with redirect_stdout(io.StringIO()):
            with patch(
                "flakiscan_validate.cli.validate", return_value=_result(Outcome.PRE_EXISTING_FAILURE, False, False)
            ):
                exit_code = cli.main(["orig.Dockerfile", "mod.Dockerfile"])
        self.assertEqual(exit_code, 1)

    def test_json_output(self):
        stdout = io.StringIO()
        with patch("flakiscan_validate.cli.validate", return_value=_result(Outcome.PRESERVED, True, True)):
            with redirect_stdout(stdout):
                cli.main(["orig.Dockerfile", "mod.Dockerfile", "--json"])

        parsed = json.loads(stdout.getvalue())
        self.assertEqual(parsed["outcome"], "preserved")

    def test_docker_unavailable_prints_error_and_exits_2(self):
        stderr = io.StringIO()
        with patch("flakiscan_validate.cli.validate", side_effect=DockerUnavailableError("docker not found")):
            with patch("sys.stderr", stderr):
                exit_code = cli.main(["orig.Dockerfile", "mod.Dockerfile"])

        self.assertEqual(exit_code, 2)
        self.assertIn("docker not found", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
