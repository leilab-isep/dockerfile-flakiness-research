"""Runs `docker build` against a single Dockerfile and reports whether it succeeded."""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid

from flakiscan_validate.result import BuildResult

_DAEMON_UNREACHABLE_MARKERS = (
    "cannot connect to the docker daemon",
    "docker daemon is not running",
    "error during connect",
)


class DockerUnavailableError(RuntimeError):
    """Raised when the `docker` binary is not on PATH."""


class DockerDaemonUnavailableError(RuntimeError):
    """Raised when `docker build` could not reach a running daemon.

    This is distinct from a build failing because of the Dockerfile's own content:
    it means no conclusion about buildability can be drawn at all, so it must not be
    misreported as a build failure.
    """


def is_available() -> bool:
    """True if the `docker` binary is on PATH. Does not check that the daemon is
    reachable -- use `build` and catch DockerDaemonUnavailableError for that."""
    return shutil.which("docker") is not None


def _looks_like_daemon_unreachable(log: str) -> bool:
    lowered = log.lower()
    return any(marker in lowered for marker in _DAEMON_UNREACHABLE_MARKERS)


def _decode(value: str | bytes | None) -> str:
    """Normalize captured subprocess output to str.

    `subprocess.TimeoutExpired.stdout`/`.stderr` carry the raw, undecoded bytes
    collected before the timeout even when the call used `text=True` -- only a
    completed `communicate()` decodes them. Without this, concatenating a decoded
    empty string with an undecoded bytes value raises TypeError.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _image_size_bytes(tag: str) -> int | None:
    """Return the built image's size in bytes, or None if it cannot be determined."""
    proc = subprocess.run(
        ["docker", "image", "inspect", tag, "--format", "{{.Size}}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def build(
    dockerfile_path: str,
    context_dir: str,
    tag: str | None = None,
    timeout_seconds: float = 600,
    cleanup: bool = True,
) -> BuildResult:
    """Run `docker build -f dockerfile_path context_dir` and report the outcome.

    `tag` defaults to a randomly generated name; pass one explicitly to keep the built
    image afterward (with `cleanup=False`). On success, the returned BuildResult's
    `image_size_bytes` is read before the image is removed, so it is available even
    with the default `cleanup=True`. Raises DockerUnavailableError if the `docker`
    binary is not on PATH, or DockerDaemonUnavailableError if the build could not
    reach a running Docker daemon -- neither is treated as a build failure, since both
    mean no conclusion about the Dockerfile itself can be drawn.
    """
    if not is_available():
        raise DockerUnavailableError("docker binary not found on PATH; install Docker to use flakiscan_validate")

    image_tag = tag or f"flakiscan-validate-{uuid.uuid4().hex[:12]}"
    start = time.monotonic()
    timed_out = False

    try:
        proc = subprocess.run(
            ["docker", "build", "-f", dockerfile_path, "-t", image_tag, context_dir],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        log = proc.stdout + proc.stderr
        success = proc.returncode == 0
    except subprocess.TimeoutExpired as exc:
        log = _decode(exc.stdout) + _decode(exc.stderr)
        success = False
        timed_out = True

    duration = time.monotonic() - start

    if not success and _looks_like_daemon_unreachable(log):
        raise DockerDaemonUnavailableError(f"docker build could not reach a running daemon: {log.strip()[-500:]}")

    image_size = _image_size_bytes(image_tag) if success else None

    if success and cleanup:
        subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True, text=True)

    return BuildResult(
        success=success,
        log=log,
        duration_seconds=duration,
        timed_out=timed_out,
        image_size_bytes=image_size,
    )
