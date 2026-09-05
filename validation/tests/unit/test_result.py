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
        result = BuildResult(success=True, log="ok", duration_seconds=1.5, timed_out=False, image_size_bytes=1000)
        self.assertEqual(
            result.to_dict(),
            {"success": True, "log": "ok", "duration_seconds": 1.5, "timed_out": False, "image_size_bytes": 1000},
        )

    def test_validation_result_to_dict_includes_size_delta(self):
        original = BuildResult(success=True, log="a", duration_seconds=1.0, image_size_bytes=1000)
        modified = BuildResult(success=False, log="b", duration_seconds=2.0)
        result = ValidationResult(outcome=Outcome.REGRESSED, original=original, modified=modified)

        as_dict = result.to_dict()
        self.assertEqual(as_dict["outcome"], "regressed")
        self.assertEqual(as_dict["original"]["success"], True)
        self.assertEqual(as_dict["modified"]["success"], False)
        self.assertIsNone(as_dict["image_size_delta_bytes"])


class TestImageSizeDelta(unittest.TestCase):
    def test_delta_is_modified_minus_original(self):
        original = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1000)
        modified = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1500)
        result = ValidationResult(outcome=Outcome.PRESERVED, original=original, modified=modified)

        self.assertEqual(result.image_size_delta_bytes, 500)

    def test_delta_can_be_negative(self):
        original = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1500)
        modified = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1000)
        result = ValidationResult(outcome=Outcome.PRESERVED, original=original, modified=modified)

        self.assertEqual(result.image_size_delta_bytes, -500)

    def test_delta_is_none_when_original_has_no_size(self):
        original = BuildResult(success=False, log="", duration_seconds=1.0)
        modified = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1000)
        result = ValidationResult(outcome=Outcome.IMPROVED, original=original, modified=modified)

        self.assertIsNone(result.image_size_delta_bytes)

    def test_delta_is_none_when_modified_has_no_size(self):
        original = BuildResult(success=True, log="", duration_seconds=1.0, image_size_bytes=1000)
        modified = BuildResult(success=False, log="", duration_seconds=1.0)
        result = ValidationResult(outcome=Outcome.REGRESSED, original=original, modified=modified)

        self.assertIsNone(result.image_size_delta_bytes)


if __name__ == "__main__":
    unittest.main()
