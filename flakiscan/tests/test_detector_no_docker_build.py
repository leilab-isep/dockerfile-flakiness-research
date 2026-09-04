"""Detection must never invoke `docker build`, and must not require a Docker daemon."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from flakiscan.detection.detector import detect

FIXTURES = Path(__file__).parent / "fixtures"

_real_run = subprocess.run


def _guarded_run(*args, **kwargs):
    argv = args[0] if args else kwargs.get("args")
    flat = " ".join(argv) if isinstance(argv, (list, tuple)) else str(argv)
    if "docker" in flat and "build" in flat:
        raise AssertionError(f"Detection Component invoked a forbidden command: {flat!r}")
    return _real_run(*args, **kwargs)


class TestDetectionNeverBuildsDocker(unittest.TestCase):
    def test_detect_never_calls_docker_build(self):
        with patch("subprocess.run", side_effect=_guarded_run):
            result = detect(str(FIXTURES / "flaky.Dockerfile"))
        self.assertIsInstance(result.findings, list)


if __name__ == "__main__":
    unittest.main()
