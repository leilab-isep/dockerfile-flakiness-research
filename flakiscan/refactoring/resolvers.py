"""Network lookups used by repair rules that need an externally resolved value: a
package's current version, a base image's digest, or a repository's latest tag.

Every function here returns None on any failure (network error, timeout, unexpected
response shape, unknown value) rather than raising, so callers can uniformly fall back
to inserting a `# TODO` comment instead of guessing at a value. They are bundled into a
`Resolvers` object so tests can substitute fakes and never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

_TIMEOUT_SECONDS = 5.0
_MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict | list | None:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def pypi_latest_version(package: str) -> str | None:
    data = _get_json(f"https://pypi.org/pypi/{package}/json")
    if not isinstance(data, dict):
        return None
    return data.get("info", {}).get("version")


def npm_latest_version(package: str) -> str | None:
    data = _get_json(f"https://registry.npmjs.org/{package}")
    if not isinstance(data, dict):
        return None
    return data.get("dist-tags", {}).get("latest")


def rubygems_latest_version(gem: str) -> str | None:
    data = _get_json(f"https://rubygems.org/api/v1/versions/{gem}/latest.json")
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    return version if version and version != "unknown" else None


def github_latest_tag(owner: str, repo: str) -> str | None:
    data = _get_json(
        f"https://api.github.com/repos/{owner}/{repo}/tags",
        headers={"Accept": "application/vnd.github+json"},
    )
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    return first.get("name") if isinstance(first, dict) else None


def fetch_sha256(url: str) -> str | None:
    """Download `url` and return its sha256 hex digest.

    Returns None if the URL cannot be fetched, or if the response exceeds
    `_MAX_DOWNLOAD_BYTES` -- this function is used to pin a checksum for a file the
    Dockerfile already downloads, not as a general-purpose downloader, so it refuses to
    fetch anything unexpectedly large.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "flakiscan-refactoring-engine"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            digest = hashlib.sha256()
            total_bytes = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > _MAX_DOWNLOAD_BYTES:
                    return None
                digest.update(chunk)
            return digest.hexdigest()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


_DOCKER_HUB_AUTH_URL = "https://auth.docker.io/token"
_DOCKER_HUB_REGISTRY_URL = "https://registry-1.docker.io"
_MANIFEST_ACCEPT_HEADER = (
    "application/vnd.docker.distribution.manifest.v2+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.oci.image.index.v1+json"
)


def docker_hub_digest(repository: str, tag: str) -> str | None:
    """Resolve the current manifest digest for a Docker Hub `repository:tag`.

    `repository` must already be in Docker Hub's normalized form, e.g. "library/ubuntu"
    for an official image or "someuser/someapp" for a user image. Only Docker Hub is
    supported; other registries have no standard anonymous-pull token endpoint.
    """
    token_data = _get_json(f"{_DOCKER_HUB_AUTH_URL}?service=registry.docker.io&scope=repository:{repository}:pull")
    if not isinstance(token_data, dict) or not token_data.get("token"):
        return None

    request = urllib.request.Request(
        f"{_DOCKER_HUB_REGISTRY_URL}/v2/{repository}/manifests/{tag}",
        headers={
            "Authorization": f"Bearer {token_data['token']}",
            "Accept": _MANIFEST_ACCEPT_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.headers.get("Docker-Content-Digest")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


@dataclass
class Resolvers:
    """Bundle of injectable network lookups. Substitute fakes in tests to avoid
    depending on network access or third-party service availability."""

    pypi_latest_version: Callable[[str], str | None] = pypi_latest_version
    npm_latest_version: Callable[[str], str | None] = npm_latest_version
    rubygems_latest_version: Callable[[str], str | None] = rubygems_latest_version
    github_latest_tag: Callable[[str, str], str | None] = github_latest_tag
    docker_hub_digest: Callable[[str, str], str | None] = docker_hub_digest
    fetch_sha256: Callable[[str], str | None] = fetch_sha256


DEFAULT_RESOLVERS = Resolvers()
