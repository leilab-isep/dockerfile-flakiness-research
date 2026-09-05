import unittest

from flakiscan_validate.result import BuildResult, Outcome, ValidationResult, classify


class TestClassify(unittest.TestCase):
    def test_both_succeed_is_preserved(self):
        self.assertEqual(classify(True, True), Outcome.PRESERVED)

    def test_original_succeeds_modified_fails_is_regressed(self):
        self.assertEqual(classify(True, False), Outcome.REGRESSED)

    def test_original_fails_modified_succeeds_is_improved(self):
        self.assertEqual(classify(False, True), Outcome.IMPROVED)

    def test_both_fail_is_pre_existing_failure(self):
        self.assertEqual(classify(False, False), Outcome.PRE_EXISTING_FAILURE)


class TestToDict(unittest.TestCase):
    def test_build_result_to_dict(self):
        result = BuildResult(success=True, log="ok", duration_seconds=1.5, timed_out=False)
        self.assertEqual(
            result.to_dict(),
            {"success": True, "log": "ok", "duration_seconds": 1.5, "timed_out": False},
        )

    def test_validation_result_to_dict(self):
        original = BuildResult(success=True, log="a", duration_seconds=1.0)
        modified = BuildResult(success=False, log="b", duration_seconds=2.0)
        result = ValidationResult(outcome=Outcome.REGRESSED, original=original, modified=modified)

        as_dict = result.to_dict()
        self.assertEqual(as_dict["outcome"], "regressed")
        self.assertEqual(as_dict["original"]["success"], True)
        self.assertEqual(as_dict["modified"]["success"], False)


if __name__ == "__main__":
    unittest.main()
