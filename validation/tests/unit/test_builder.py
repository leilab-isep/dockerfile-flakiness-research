"""Unit tests for the `docker build` wrapper, with `subprocess.run` and `shutil.which`
mocked out. See integration/test_builder.py for tests against the real Docker CLI."""

import subprocess
import unittest
from unittest.mock import MagicMock, patch

from flakiscan_validate import builder


def _fake_process(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def _dispatch_by_command(responses: dict[tuple, MagicMock], default: MagicMock | None = None):
    """subprocess.run side_effect that answers based on the command's first two argv
    tokens (e.g. ("docker", "build") vs ("docker", "image")), so a test can give
    `docker build`, `docker image inspect`, and `docker rmi` different fake results."""

    def side_effect(argv, **kwargs):
        return responses.get(tuple(argv[:2]), default or _fake_process(0))

    return side_effect


class TestIsAvailable(unittest.TestCase):
    def test_true_when_docker_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            self.assertTrue(builder.is_available())

    def test_false_when_docker_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(builder.is_available())


class TestBuild(unittest.TestCase):
    def test_raises_when_docker_missing(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaises(builder.DockerUnavailableError):
                builder.build("Dockerfile", ".")

    def test_successful_build(self):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_fake_process(0, stdout="Successfully built abc123")),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertTrue(result.success)
        self.assertFalse(result.timed_out)
        self.assertIn("Successfully built", result.log)

    def test_successful_build_with_non_utf8_bytes_in_log(self):
        # A build's log can legitimately contain bytes that are not valid UTF-8 (a
        # build tool's own terminal control sequences or locale-specific output).
        # subprocess.run(text=True) would raise UnicodeDecodeError decoding these
        # instead of ever returning a CompletedProcess -- build() must capture as
        # bytes and decode leniently itself instead of relying on text=True.
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_fake_process(0, stdout=b"Successfully built \xff\xfe garbled")),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertTrue(result.success)
        self.assertIn("Successfully built", result.log)

    def test_failed_build(self):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_fake_process(1, stderr="failed to solve: process exited 1")),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertFalse(result.success)
        self.assertIn("process exited 1", result.log)

    def test_daemon_unreachable_raises_instead_of_reporting_failure(self):
        stderr = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_fake_process(1, stderr=stderr)),
        ):
            with self.assertRaises(builder.DockerDaemonUnavailableError):
                builder.build("Dockerfile", ".")

    def test_timeout_is_reported_as_a_failed_build_not_an_exception(self):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="docker build", timeout=1, output="partial log"),
            ),
        ):
            result = builder.build("Dockerfile", ".", timeout_seconds=1, cleanup=False)

        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)

    def test_timeout_with_undecoded_bytes_output_does_not_raise(self):
        # subprocess.TimeoutExpired carries the raw, undecoded bytes captured before
        # the timeout even when the call used text=True -- this is what a real timeout
        # looks like, unlike the str output used in the test above.
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="docker build", timeout=1, output=b"partial stdout", stderr=b"partial stderr"),
            ),
        ):
            result = builder.build("Dockerfile", ".", timeout_seconds=1, cleanup=False)

        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.log, "partial stdoutpartial stderr")

    def test_successful_build_is_cleaned_up_by_default(self):
        run_mock = MagicMock(return_value=_fake_process(0))
        with patch("shutil.which", return_value="/usr/bin/docker"), patch("subprocess.run", run_mock):
            builder.build("Dockerfile", ".", tag="my-tag")

        rmi_calls = [c for c in run_mock.call_args_list if c.args[0][:2] == ["docker", "rmi"]]
        self.assertEqual(len(rmi_calls), 1)
        self.assertIn("my-tag", rmi_calls[0].args[0])

    def test_failed_build_is_not_cleaned_up(self):
        run_mock = MagicMock(return_value=_fake_process(1, stderr="some real build error"))
        with patch("shutil.which", return_value="/usr/bin/docker"), patch("subprocess.run", run_mock):
            builder.build("Dockerfile", ".")

        rmi_calls = [c for c in run_mock.call_args_list if c.args[0][:2] == ["docker", "rmi"]]
        self.assertEqual(rmi_calls, [])


class TestImageSize(unittest.TestCase):
    def test_successful_build_reports_image_size(self):
        responses = {
            ("docker", "build"): _fake_process(0, stdout="Successfully built abc123"),
            ("docker", "image"): _fake_process(0, stdout="4094717\n"),
        }
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", side_effect=_dispatch_by_command(responses)),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertEqual(result.image_size_bytes, 4094717)

    def test_failed_build_has_no_image_size(self):
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", return_value=_fake_process(1, stderr="some real build error")),
        ):
            result = builder.build("Dockerfile", ".")

        self.assertIsNone(result.image_size_bytes)

    def test_image_size_is_read_before_the_image_is_removed(self):
        responses = {
            ("docker", "build"): _fake_process(0),
            ("docker", "image"): _fake_process(0, stdout="1000\n"),
        }
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", side_effect=_dispatch_by_command(responses)) as run_mock,
        ):
            result = builder.build("Dockerfile", ".")  # cleanup=True by default

        self.assertEqual(result.image_size_bytes, 1000)
        rmi_calls = [c for c in run_mock.call_args_list if c.args[0][:2] == ["docker", "rmi"]]
        self.assertEqual(len(rmi_calls), 1)

    def test_image_size_is_none_when_inspect_fails(self):
        responses = {
            ("docker", "build"): _fake_process(0),
            ("docker", "image"): _fake_process(1, stderr="no such image"),
        }
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", side_effect=_dispatch_by_command(responses)),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertIsNone(result.image_size_bytes)

    def test_image_size_is_none_when_inspect_output_is_not_a_number(self):
        responses = {
            ("docker", "build"): _fake_process(0),
            ("docker", "image"): _fake_process(0, stdout="not-a-number\n"),
        }
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("subprocess.run", side_effect=_dispatch_by_command(responses)),
        ):
            result = builder.build("Dockerfile", ".", cleanup=False)

        self.assertIsNone(result.image_size_bytes)


if __name__ == "__main__":
    unittest.main()
