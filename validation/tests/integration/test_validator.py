"""End-to-end integration tests: real `docker build` calls for every classification
in the outcome table. Skipped automatically if Docker is unavailable."""

import unittest
from pathlib import Path

from flakiscan_validate import builder
from flakiscan_validate.result import Outcome
from flakiscan_validate.validator import validate

FIXTURES = Path(__file__).parent.parent / "fixtures"
HEALTHY = str(FIXTURES / "healthy.Dockerfile")
BROKEN = str(FIXTURES / "broken.Dockerfile")
BIGGER = str(FIXTURES / "bigger.Dockerfile")  # adds a ~1MB file on top of the same base as HEALTHY


def _daemon_available() -> bool:
    if not builder.is_available():
        return False
    try:
        builder.build(HEALTHY, str(FIXTURES), cleanup=True)
        return True
    except builder.DockerDaemonUnavailableError:
        return False


_DAEMON_AVAILABLE = _daemon_available()


@unittest.skipUnless(_DAEMON_AVAILABLE, "docker daemon not available")
class TestValidateOutcomeTable(unittest.TestCase):
    def test_both_healthy_is_preserved(self):
        result = validate(HEALTHY, HEALTHY)
        self.assertEqual(result.outcome, Outcome.PRESERVED)

    def test_healthy_then_broken_is_regressed(self):
        result = validate(HEALTHY, BROKEN)
        self.assertEqual(result.outcome, Outcome.REGRESSED)

    def test_broken_then_healthy_is_improved(self):
        result = validate(BROKEN, HEALTHY)
        self.assertEqual(result.outcome, Outcome.IMPROVED)

    def test_both_broken_is_pre_existing_failure(self):
        result = validate(BROKEN, BROKEN)
        self.assertEqual(result.outcome, Outcome.PRE_EXISTING_FAILURE)


@unittest.skipUnless(_DAEMON_AVAILABLE, "docker daemon not available")
class TestImageSizeComparison(unittest.TestCase):
    def test_size_delta_reflects_a_real_size_increase(self):
        result = validate(HEALTHY, BIGGER)
        self.assertEqual(result.outcome, Outcome.PRESERVED)
        self.assertIsNotNone(result.image_size_delta_bytes)
        # BIGGER writes an extra ~1MB file on top of the same base image.
        self.assertGreater(result.image_size_delta_bytes, 900_000)

    def test_size_delta_is_none_when_a_build_fails(self):
        result = validate(HEALTHY, BROKEN)
        self.assertIsNone(result.image_size_delta_bytes)


if __name__ == "__main__":
    unittest.main()
