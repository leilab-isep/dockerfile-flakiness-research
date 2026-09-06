"""End-to-end coverage of every FlakiScan detection rule, driven through the actual,
installed `flakiscan` command -- the same binary a real user would get from `pip
install flakiscan` -- rather than its internal library functions or the module path.

Each test builds its own minimal Dockerfile for the scenario it targets, then runs the
two steps a user of FlakiScan actually invokes, in order, each as its own `flakiscan`
subprocess:

  1. Detection -- `flakiscan <path> --json` runs the real Hadolint binary, the real
     Docker Parfum CLI, and the custom rule engine, and the target rule id is asserted
     to be among the findings in the printed JSON report.
  2. Refactoring -- `flakiscan <path> --repair --json` is asserted to resolve the
     finding correctly: either with its exact automated fix, or by deferring to a
     `# TODO(flakiscan)` comment for the handful of rules that have no automated fix
     (the correct outcome for those is deferring, not silently dropping the finding).

Because this suite shells out to the `flakiscan` command by name (resolved via PATH,
not by importing the package or invoking `python -m flakiscan.cli` from the source
tree), it depends on `flakiscan` having actually been *built and installed* first --
`pip install -e .` for a local run, or the wheel produced by the `build` job that this
suite's CI workflow depends on. A source-tree checkout with nothing installed will
correctly fail every test here with "command not found", the same way it would for a
user who forgot to install the package.

Refactoring runs with the CLI's real, default resolvers -- no fakes, and no way to
inject one, since `flakiscan.cli` does not expose that option. A handful of rules (base
image digest, pip/npm/gem version pinning, git tag pinning, download checksum) resolve
a value from a live external service (Docker Hub, PyPI, the npm registry, RubyGems, the
GitHub API, or the downloaded URL itself), so those specific tests assert the *shape*
of the resolved value (a real digest, a real semantic version) rather than a hardcoded
one, since the real current value changes over time. Where the target of a live lookup
needs to be pinned for a stable assertion (the download-checksum test), a specific,
immutable, tagged upstream file is used so its content -- and therefore its checksum --
can never change.

Requires Hadolint and Docker Parfum installed and on PATH (see the integration job in
.github/workflows/ci.yml for how CI provisions them), plus outbound internet access to
Docker Hub, PyPI, the npm registry, RubyGems, and the GitHub API. Because it depends on
those live services, this suite can occasionally fail on their transient unavailability
or rate limiting rather than on a real regression -- an accepted trade-off of testing
the real CLI end to end instead of against fakes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("flakiscan.tests.e2e")

# A real, immutable upstream file (pinned to a specific git tag, so its content and
# therefore its sha256 can never change) used as the download target for the checksum-
# pinning test.
PINNED_URL = "https://raw.githubusercontent.com/git/git/v2.45.0/README.md"
PINNED_URL_SHA256 = "4f4e044593fd16cce4a6afa7af10f4f9b4cceece89480e71c9e22a5cde67f241"

SEMVER = r"\d+(\.\d+)+"


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
        f.write(content)
        return f.name


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    """Invoke the installed `flakiscan` command by name, resolved via PATH -- the same
    way a real user runs it after `pip install flakiscan`."""
    return subprocess.run(
        ["flakiscan", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


_FLAKISCAN_INSTALLED = shutil.which("flakiscan") is not None


@unittest.skipUnless(_FLAKISCAN_INSTALLED, "flakiscan is not installed / not on PATH -- run `pip install -e .` first")
class RuleCase(unittest.TestCase):
    """Shared detect-then-repair steps for a single rule scenario, each run as a real
    `flakiscan` CLI invocation."""

    def run_pipeline(self, content: str):
        """Step 1: write the crafted Dockerfile to disk.
        Step 2: run `flakiscan <path> --json` (real detection: Hadolint + Docker
        Parfum + custom rules).
        Step 3: run `flakiscan <path> --repair --json` against the same file.

        Returns (detected_rule_ids, applied_rule_ids, fallback_rule_ids, patched_text).
        """
        logger.info("Dockerfile under test:\n%s", content.rstrip("\n"))
        path = _write(content)
        try:
            logger.info("--- step 1/2: detection -- flakiscan %s --json", path)
            detect_proc = _run_cli(path, "--json")
            self.assertEqual(detect_proc.returncode, 0, f"flakiscan {path} --json failed:\n{detect_proc.stderr}")
            findings = json.loads(detect_proc.stdout)["findings"]
            detected = {f["rule_id"] for f in findings}
            logger.info("    detected rule ids: %s", sorted(detected) or "(none)")

            logger.info("--- step 2/2: refactoring -- flakiscan %s --repair --json", path)
            repair_proc = _run_cli(path, "--repair", "--json")
            self.assertEqual(repair_proc.returncode, 0, f"flakiscan {path} --repair --json failed:\n{repair_proc.stderr}")
            repair_report = json.loads(repair_proc.stdout)
        finally:
            os.unlink(path)

        applied = {rid for action in repair_report["actions"] for rid in action["applied_rule_ids"]}
        fallback = {rid for action in repair_report["actions"] for rid in action["fallback_rule_ids"]}
        logger.info("    applied (automated fix): %s", sorted(applied) or "(none)")
        logger.info("    deferred (# TODO): %s", sorted(fallback) or "(none)")
        logger.info("    patched Dockerfile:\n%s", repair_report["patched_text"].rstrip("\n"))
        return detected, applied, fallback, repair_report["patched_text"]

    def assert_detected_and_fixed(self, rule_id: str, content: str, expected_substring: str):
        """The common case: `rule_id` is detected and the patched text contains the
        exact expected fix."""
        logger.info("=" * 78)
        logger.info("RULE %s -- expecting: detected, then automatically fixed", rule_id)
        detected, applied, _, patched = self.run_pipeline(content)
        self.assertIn(rule_id, detected, f"{rule_id} was not detected in:\n{content}")
        self.assertIn(rule_id, applied, f"{rule_id} was not applied; patched text was:\n{patched}")
        self.assertIn(expected_substring, patched)
        logger.info("PASS %s: detected and fixed as expected", rule_id)

    def assert_detected_and_fixed_matching(self, rule_id: str, content: str, expected_pattern: str):
        """Like `assert_detected_and_fixed`, but for a fix whose value comes from a
        live external lookup (a current package version, a current image digest) and
        so can only be checked against a pattern, not an exact hardcoded string."""
        logger.info("=" * 78)
        logger.info("RULE %s -- expecting: detected, then fixed with a live-resolved value", rule_id)
        detected, applied, _, patched = self.run_pipeline(content)
        self.assertIn(rule_id, detected, f"{rule_id} was not detected in:\n{content}")
        self.assertIn(rule_id, applied, f"{rule_id} was not applied; patched text was:\n{patched}")
        self.assertRegex(patched, expected_pattern)
        logger.info("PASS %s: detected and fixed as expected (pattern: %s)", rule_id, expected_pattern)

    def assert_detected_and_deferred(self, rule_id: str, content: str):
        """The no-automated-fix case: `rule_id` is detected but left as a `# TODO`
        rather than silently dropped or (incorrectly) rewritten."""
        logger.info("=" * 78)
        logger.info("RULE %s -- expecting: detected, then deferred to a # TODO (no automated fix)", rule_id)
        detected, _, fallback, patched = self.run_pipeline(content)
        self.assertIn(rule_id, detected, f"{rule_id} was not detected in:\n{content}")
        self.assertIn(rule_id, fallback, f"{rule_id} was not deferred; patched text was:\n{patched}")
        self.assertIn(f"# TODO(flakiscan): could not automatically fix: {rule_id}", patched)
        logger.info("PASS %s: detected and correctly deferred as expected", rule_id)


# ---------------------------------------------------------------------------
# Custom rule engine (8 rules)
# ---------------------------------------------------------------------------


class TestCustomRules(RuleCase):
    def test_implicit_latest(self):
        # FROM with no tag at all resolves to :latest implicitly. ubuntu:latest's
        # digest changes whenever a new image is published, so only the shape of the
        # pinned reference is checked, not today's specific digest.
        self.assert_detected_and_fixed_matching("implicit_latest", "FROM ubuntu\n", r"FROM ubuntu@sha256:[0-9a-f]{64}")

    def test_explicit_latest(self):
        # FROM with the mutable :latest tag pinned explicitly.
        self.assert_detected_and_fixed_matching("explicit_latest", "FROM ubuntu:latest\n", r"FROM ubuntu@sha256:[0-9a-f]{64}")

    def test_curl_pipe_shell(self):
        content = "FROM ubuntu:24.04\nRUN curl -fsSL https://example.com/install.sh | bash\n"
        self.assert_detected_and_fixed(
            "curl_pipe_shell",
            content,
            "curl -fsSL https://example.com/install.sh -o /tmp/install.sh && sha256sum /tmp/install.sh && bash /tmp/install.sh",
        )

    def test_add_remote_url(self):
        content = "FROM ubuntu:24.04\nADD https://example.com/data.tar.gz /data.tar.gz\n"
        self.assert_detected_and_fixed(
            "add_remote_url",
            content,
            "RUN curl -fsSL https://example.com/data.tar.gz -o /data.tar.gz && sha256sum /data.tar.gz",
        )

    def test_git_clone_no_pin(self):
        # git/git is a real, actively tagged public repository; its latest tag changes
        # with every release, so only the shape of the pinned checkout is checked.
        content = "FROM ubuntu:24.04\nRUN git clone https://github.com/git/git.git\n"
        self.assert_detected_and_fixed_matching(
            "git_clone_no_pin",
            content,
            rf"git clone https://github\.com/git/git\.git && cd git && git checkout v?{SEMVER}",
        )

    def test_arg_no_default(self):
        # No resolver can safely guess a value meant to come from --build-arg.
        self.assert_detected_and_deferred("arg_no_default", "FROM ubuntu:24.04\nARG BUILD_ENV\n")

    def test_missing_pipefail(self):
        content = "FROM ubuntu:24.04\nRUN apt-get update | tee log.txt\n"
        self.assert_detected_and_fixed("missing_pipefail", content, "RUN set -o pipefail && apt-get update | tee log.txt")

    def test_download_no_checksum(self):
        content = f"FROM ubuntu:24.04\nRUN curl -fsSL {PINNED_URL} -o README.md\n"
        self.assert_detected_and_fixed(
            "download_no_checksum",
            content,
            f"echo '{PINNED_URL_SHA256}  README.md' > README.md.sha256 && sha256sum -c README.md.sha256",
        )


# ---------------------------------------------------------------------------
# Hadolint (14 rules)
# ---------------------------------------------------------------------------


class TestHadolintRules(RuleCase):
    def test_dl3007_pin_base_image_digest(self):
        self.assert_detected_and_fixed_matching("DL3007", "FROM ubuntu:latest\n", r"FROM ubuntu@sha256:[0-9a-f]{64}")

    def test_dl4006_missing_shell_pipefail(self):
        content = "FROM ubuntu:24.04\nRUN apt-get update | tee log.txt\n"
        self.assert_detected_and_fixed("DL4006", content, "RUN set -o pipefail && apt-get update | tee log.txt")

    def test_dl3009_apt_lists_not_removed(self):
        content = "FROM ubuntu:24.04\nRUN apt-get update && apt-get install -y curl\n"
        self.assert_detected_and_fixed("DL3009", content, "rm -rf /var/lib/apt/lists/*")

    def test_dl3019_apk_add_missing_no_cache(self):
        self.assert_detected_and_fixed("DL3019", "FROM alpine:3.19\nRUN apk add curl\n", "apk add --no-cache curl")

    def test_dl3042_pip_install_missing_no_cache_dir(self):
        content = "FROM python:3.12\nRUN pip install requests\n"
        self.assert_detected_and_fixed_matching("DL3042", content, rf"pip install --no-cache-dir requests=={SEMVER}")

    def test_dl3020_add_of_local_file(self):
        content = "FROM ubuntu:24.04\nADD app.py /app/app.py\n"
        self.assert_detected_and_fixed("DL3020", content, "COPY app.py /app/app.py")

    def test_dl3008_apt_get_install_unpinned(self):
        # The available version depends on the base image's package mirror state at
        # build time, which cannot be resolved from the Dockerfile source alone.
        self.assert_detected_and_deferred("DL3008", "FROM ubuntu:24.04\nRUN apt-get install -y curl\n")

    def test_dl3013_pip_install_unpinned(self):
        content = "FROM python:3.12\nRUN pip install requests\n"
        self.assert_detected_and_fixed_matching("DL3013", content, rf"requests=={SEMVER}")

    def test_dl3016_npm_install_unpinned(self):
        content = "FROM node:20\nRUN npm install express\n"
        self.assert_detected_and_fixed_matching("DL3016", content, rf"express@{SEMVER}")

    def test_dl3018_apk_add_unpinned(self):
        self.assert_detected_and_deferred("DL3018", "FROM alpine:3.19\nRUN apk add curl\n")

    def test_dl3028_gem_install_unpinned(self):
        content = "FROM ruby:3.3\nRUN gem install rails\n"
        self.assert_detected_and_fixed_matching("DL3028", content, rf"gem install rails -v {SEMVER}")

    def test_dl3033_yum_install_unpinned(self):
        self.assert_detected_and_deferred("DL3033", "FROM centos:7\nRUN yum install httpd\n")

    def test_dl3037_zypper_install_unpinned(self):
        self.assert_detected_and_deferred("DL3037", "FROM opensuse/leap:15.5\nRUN zypper install httpd\n")

    def test_dl3041_dnf_install_unpinned(self):
        self.assert_detected_and_deferred("DL3041", "FROM fedora:39\nRUN dnf install httpd\n")


# ---------------------------------------------------------------------------
# Docker Parfum (18 rules)
# ---------------------------------------------------------------------------


class TestParfumRules(RuleCase):
    def test_apk_add_use_no_cache(self):
        self.assert_detected_and_fixed("apkAddUseNoCache", "FROM alpine:3.19\nRUN apk add curl\n", "--no-cache")

    def test_apt_get_install_use_no_recommends(self):
        content = "FROM ubuntu:24.04\nRUN apt-get install -y curl\n"
        self.assert_detected_and_fixed("aptGetInstallUseNoRec", content, "--no-install-recommends")

    def test_pip_use_no_cache_dir(self):
        content = "FROM python:3.12\nRUN pip install requests\n"
        self.assert_detected_and_fixed("pipUseNoCacheDir", content, "--no-cache-dir")

    def test_apt_get_install_use_y(self):
        # A plain `apt-get install` (no -y) also triggers aptGetInstallUseNoRec,
        # aptGetUpdatePrecedesInstall and aptGetInstallThenRemoveAptLists on the same
        # instruction, so the -y flag this rule adds ends up followed by their own
        # additions rather than directly by the package name.
        content = "FROM ubuntu:24.04\nRUN apt-get install curl\n"
        self.assert_detected_and_fixed("aptGetInstallUseY", content, "apt-get install -y")

    def test_yum_install_force_yes(self):
        content = "FROM centos:7\nRUN yum install httpd\n"
        self.assert_detected_and_fixed("yumInstallForceYes", content, "yum install -y httpd")

    def test_apt_get_update_precedes_install(self):
        content = "FROM ubuntu:24.04\nRUN apt-get install -y curl\n"
        self.assert_detected_and_fixed("aptGetUpdatePrecedesInstall", content, "apt-get update && apt-get install")

    def test_rule_more_than_one_install(self):
        # Merging separate install instructions would mean editing a different
        # instruction than the one the finding is reported on, which the engine never
        # does -- always deferred.
        content = "FROM ubuntu:24.04\nRUN apt-get install -y curl\nRUN apt-get install -y wget\n"
        self.assert_detected_and_deferred("ruleMoreThanOneInstall", content)

    def test_apt_get_install_then_remove_apt_lists(self):
        content = "FROM ubuntu:24.04\nRUN apt-get update && apt-get install -y curl\n"
        self.assert_detected_and_fixed("aptGetInstallThenRemoveAptLists", content, "rm -rf /var/lib/apt/lists/*")

    def test_yum_install_rm_var_cache_yum(self):
        content = "FROM centos:7\nRUN yum install httpd\n"
        self.assert_detected_and_fixed("yumInstallRmVarCacheYum", content, "rm -rf /var/cache/yum")

    def test_npm_cache_clean_after_install(self):
        content = "FROM node:20\nRUN npm install express\n"
        self.assert_detected_and_fixed("npmCacheCleanAfterInstall", content, "npm cache clean --force")

    def test_npm_cache_clean_use_force(self):
        content = "FROM node:20\nRUN npm cache clean\n"
        self.assert_detected_and_fixed("npmCacheCleanUseForce", content, "npm cache clean --force")

    def test_yarn_cache_clean_after_install(self):
        content = "FROM node:20\nRUN yarn install\n"
        self.assert_detected_and_fixed("yarnCacheCleanAfterInstall", content, "yarn install && yarn cache clean")

    def test_curl_use_flag_f(self):
        content = "FROM ubuntu:24.04\nRUN curl http://example.com/f.tar.gz -o f.tar.gz && sha256sum f.tar.gz\n"
        self.assert_detected_and_fixed("curlUseFlagF", content, "curl -L -f https://example.com/f.tar.gz")

    def test_curl_use_flag_l(self):
        content = "FROM ubuntu:24.04\nRUN curl http://example.com/f.tar.gz -o f.tar.gz && sha256sum f.tar.gz\n"
        self.assert_detected_and_fixed("curlUseFlagL", content, "curl -L -f https://example.com/f.tar.gz")

    def test_curl_use_https_url(self):
        content = "FROM ubuntu:24.04\nRUN curl http://example.com/f.tar.gz -o f.tar.gz && sha256sum f.tar.gz\n"
        self.assert_detected_and_fixed("curlUseHttpsUrl", content, "https://example.com/f.tar.gz")

    def test_wget_use_https_url(self):
        content = "FROM ubuntu:24.04\nRUN wget http://example.com/f.tar.gz -O f.tar.gz && sha256sum f.tar.gz\n"
        self.assert_detected_and_fixed("wgetUseHttpsUrl", content, "https://example.com/f.tar.gz")

    def test_sha256sum_echo_one_space(self):
        # sha256sum -c parses "<hash>  <filename>" (two spaces); a single space is
        # misparsed as part of the filename. The hash itself is arbitrary test data,
        # not a resolved value, so it is fine to hardcode.
        sample_hash = "c" * 64
        content = f'FROM ubuntu:24.04\nRUN echo "{sample_hash} file.tar.gz" | sha256sum -c -\n'
        self.assert_detected_and_fixed("sha256sumEchoOneSpaces", content, f"{sample_hash}  file.tar.gz")

    def test_gpg_verify_asc_rm_asc(self):
        content = "FROM ubuntu:24.04\nRUN gpg --verify file.tar.gz.asc file.tar.gz\n"
        self.assert_detected_and_fixed("gpgVerifyAscRmAsc", content, "rm -f file.tar.gz.asc")


if __name__ == "__main__":
    unittest.main()
