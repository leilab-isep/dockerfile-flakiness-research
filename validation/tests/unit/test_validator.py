"""Unit tests for validator orchestration, with `builder.build` mocked out."""

import unittest
from unittest.mock import patch

from flakiscan_validate.result import BuildResult, Outcome
from flakiscan_validate.validator import validate


def _result(success: bool) -> BuildResult:
    return BuildResult(success=success, log="log", duration_seconds=0.1)


class TestValidate(unittest.TestCase):
    def test_both_succeed_yields_preserved(self):
        with patch("flakiscan_validate.validator.builder.build", side_effect=[_result(True), _result(True)]):
            result = validate("original.Dockerfile", "modified.Dockerfile", context_dir=".")

        self.assertEqual(result.outcome, Outcome.PRESERVED)

    def test_regression_is_detected(self):
        with patch("flakiscan_validate.validator.builder.build", side_effect=[_result(True), _result(False)]):
            result = validate("original.Dockerfile", "modified.Dockerfile", context_dir=".")

        self.assertEqual(result.outcome, Outcome.REGRESSED)

    def test_defaults_context_to_original_dockerfiles_directory(self):
        with patch("flakiscan_validate.validator.builder.build", side_effect=[_result(True), _result(True)]) as build_mock:
            validate("/some/dir/original.Dockerfile", "/other/dir/modified.Dockerfile")

        for call in build_mock.call_args_list:
            self.assertEqual(call.args[1], "/some/dir")

    def test_explicit_context_overrides_default(self):
        with patch("flakiscan_validate.validator.builder.build", side_effect=[_result(True), _result(True)]) as build_mock:
            validate("original.Dockerfile", "modified.Dockerfile", context_dir="/explicit")

        for call in build_mock.call_args_list:
            self.assertEqual(call.args[1], "/explicit")


if __name__ == "__main__":
    unittest.main()
