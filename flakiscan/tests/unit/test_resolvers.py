"""Resolver unit tests. All network access is mocked -- these must never depend on
having a working internet connection."""

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from flakiscan.refactoring import resolvers


def _fake_response(body: bytes, headers: dict[str, str] | None = None):
    response = MagicMock()
    response.read.return_value = body
    response.headers = headers or {}
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class TestPypiLatestVersion(unittest.TestCase):
    def test_returns_version_from_info(self):
        body = json.dumps({"info": {"version": "2.32.3"}}).encode()
        with patch("urllib.request.urlopen", return_value=_fake_response(body)):
            self.assertEqual(resolvers.pypi_latest_version("requests"), "2.32.3")

    def test_returns_none_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            self.assertIsNone(resolvers.pypi_latest_version("requests"))

    def test_returns_none_on_malformed_json(self):
        with patch("urllib.request.urlopen", return_value=_fake_response(b"not json")):
            self.assertIsNone(resolvers.pypi_latest_version("requests"))


class TestNpmLatestVersion(unittest.TestCase):
    def test_returns_dist_tag_latest(self):
        body = json.dumps({"dist-tags": {"latest": "4.17.21"}}).encode()
        with patch("urllib.request.urlopen", return_value=_fake_response(body)):
            self.assertEqual(resolvers.npm_latest_version("lodash"), "4.17.21")


class TestRubygemsLatestVersion(unittest.TestCase):
    def test_returns_version(self):
        body = json.dumps({"version": "1.2.3"}).encode()
        with patch("urllib.request.urlopen", return_value=_fake_response(body)):
            self.assertEqual(resolvers.rubygems_latest_version("rails"), "1.2.3")

    def test_returns_none_for_unknown_gem(self):
        body = json.dumps({"version": "unknown"}).encode()
        with patch("urllib.request.urlopen", return_value=_fake_response(body)):
            self.assertIsNone(resolvers.rubygems_latest_version("not-a-real-gem"))


class TestGithubLatestTag(unittest.TestCase):
    def test_returns_first_tag_name(self):
        body = json.dumps([{"name": "v2.45.0"}, {"name": "v2.44.0"}]).encode()
        with patch("urllib.request.urlopen", return_value=_fake_response(body)):
            self.assertEqual(resolvers.github_latest_tag("git", "git"), "v2.45.0")

    def test_returns_none_when_no_tags(self):
        with patch("urllib.request.urlopen", return_value=_fake_response(b"[]")):
            self.assertIsNone(resolvers.github_latest_tag("owner", "repo"))


class TestDockerHubDigest(unittest.TestCase):
    def test_returns_digest_header(self):
        token_response = _fake_response(json.dumps({"token": "abc"}).encode())
        manifest_response = _fake_response(b"{}", headers={"Docker-Content-Digest": "sha256:" + "a" * 64})
        with patch("urllib.request.urlopen", side_effect=[token_response, manifest_response]):
            digest = resolvers.docker_hub_digest("library/ubuntu", "latest")
        self.assertEqual(digest, "sha256:" + "a" * 64)

    def test_returns_none_when_token_request_fails(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            self.assertIsNone(resolvers.docker_hub_digest("library/ubuntu", "latest"))

    def test_returns_none_when_no_token_in_response(self):
        with patch("urllib.request.urlopen", return_value=_fake_response(b"{}")):
            self.assertIsNone(resolvers.docker_hub_digest("library/ubuntu", "latest"))


class TestFetchSha256(unittest.TestCase):
    def test_returns_hex_digest_of_content(self):
        import hashlib
        content = b"hello world"
        expected = hashlib.sha256(content).hexdigest()

        response = MagicMock()
        response.read.side_effect = [content, b""]
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(resolvers.fetch_sha256("https://example.com/file"), expected)

    def test_returns_none_when_oversized(self):
        response = MagicMock()
        # Two chunks over the limit, then EOF -- the function should bail before reading past it.
        oversized_chunk = b"x" * (resolvers._MAX_DOWNLOAD_BYTES + 1)
        response.read.side_effect = [oversized_chunk, b""]
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=response):
            self.assertIsNone(resolvers.fetch_sha256("https://example.com/huge-file"))

    def test_returns_none_on_error(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
            self.assertIsNone(resolvers.fetch_sha256("https://example.com/file"))


if __name__ == "__main__":
    unittest.main()
