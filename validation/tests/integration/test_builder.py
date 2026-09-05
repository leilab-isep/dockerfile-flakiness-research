"""Integration tests against the real `docker` CLI and a real daemon. Skipped
automatically if either is unavailable."""

import unittest
from pathlib import Path

from flakiscan_validate import builder

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _daemon_available() -> bool:
    if not builder.is_available():
        return False
    try:
        builder.build(str(FIXTURES / "healthy.Dockerfile"), str(FIXTURES), cleanup=True)
        return True
    except builder.DockerDaemonUnavailableError:
        return False


_DAEMON_AVAILABLE = _daemon_available()


@unittest.skipUnless(_DAEMON_AVAILABLE, "docker daemon not available")
class TestBuild(unittest.TestCase):
    def test_healthy_dockerfile_builds_successfully(self):
        result = builder.build(str(FIXTURES / "healthy.Dockerfile"), str(FIXTURES))
        self.assertTrue(result.success)
        self.assertFalse(result.timed_out)

    def test_broken_dockerfile_fails(self):
        result = builder.build(str(FIXTURES / "broken.Dockerfile"), str(FIXTURES))
        self.assertFalse(result.success)
        self.assertIn("this-command-does-not-exist", result.log)

    def test_built_image_is_removed_after_success(self):
        import subprocess

        tag = "flakiscan-validate-test-cleanup"
        builder.build(str(FIXTURES / "healthy.Dockerfile"), str(FIXTURES), tag=tag, cleanup=True)

        inspect = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
        self.assertNotEqual(inspect.returncode, 0, "image should have been removed after a successful build")


if __name__ == "__main__":
    unittest.main()
