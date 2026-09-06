"""Unit tests for each repair function, using fake resolvers so no test touches the
network. A resolver that would only be called on a network-independent path raises if
invoked, to catch accidental network dependencies."""

import unittest

from flakiscan.refactoring import rules
from flakiscan.refactoring.resolvers import Resolvers


def _unused(*args, **kwargs):
    raise AssertionError("this test's fixture should not need to resolve a value")


NO_NETWORK = Resolvers(
    pypi_latest_version=_unused,
    npm_latest_version=_unused,
    rubygems_latest_version=_unused,
    github_latest_tag=_unused,
    docker_hub_digest=_unused,
    fetch_sha256=_unused,
)


class TestPinBaseImageDigest(unittest.TestCase):
    def test_pins_to_resolved_digest(self):
        resolvers = Resolvers(docker_hub_digest=lambda repo, tag: "sha256:" + "a" * 64)
        result = rules.repair_pin_base_image_digest("FROM ubuntu:latest", {"explicit_latest"}, resolvers)
        self.assertEqual(result.text, "FROM ubuntu@sha256:" + "a" * 64)
        self.assertEqual(result.handled_rule_ids, {"explicit_latest"})

    def test_leaves_digest_pinned_image_untouched(self):
        text = "FROM ubuntu@sha256:" + "b" * 64
        result = rules.repair_pin_base_image_digest(text, {"explicit_latest"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())

    def test_leaves_non_docker_hub_image_untouched(self):
        result = rules.repair_pin_base_image_digest("FROM ghcr.io/example/app:latest", {"explicit_latest"}, NO_NETWORK)
        self.assertEqual(result.handled_rule_ids, set())

    def test_falls_back_when_digest_unresolvable(self):
        resolvers = Resolvers(docker_hub_digest=lambda repo, tag: None)
        result = rules.repair_pin_base_image_digest("FROM ubuntu:latest", {"explicit_latest"}, resolvers)
        self.assertEqual(result.handled_rule_ids, set())
        self.assertEqual(result.text, "FROM ubuntu:latest")


class TestAddPipefail(unittest.TestCase):
    def test_prepends_pipefail(self):
        result = rules.repair_add_pipefail("RUN cat f | grep x", {"missing_pipefail"}, NO_NETWORK)
        self.assertEqual(result.text, "RUN set -o pipefail && cat f | grep x")
        self.assertEqual(result.handled_rule_ids, {"missing_pipefail"})

    def test_idempotent_when_already_present(self):
        text = "RUN set -o pipefail && cat f | grep x"
        result = rules.repair_add_pipefail(text, {"missing_pipefail"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestRestructureCurlPipeShell(unittest.TestCase):
    def test_restructures_into_download_then_execute(self):
        result = rules.repair_restructure_curl_pipe_shell("RUN curl -sL https://example.com/install.sh | bash", {"curl_pipe_shell"}, NO_NETWORK)
        self.assertNotIn("|", result.text)
        self.assertIn("sha256sum", result.text)
        self.assertIn("bash /tmp/install.sh", result.text)
        self.assertEqual(result.handled_rule_ids, {"curl_pipe_shell"})

    def test_does_not_introduce_an_unprotected_pipe(self):
        result = rules.repair_restructure_curl_pipe_shell("RUN wget https://example.com/x.sh -O- | sh", {"curl_pipe_shell"}, NO_NETWORK)
        self.assertNotIn("|", result.text)


class TestAptGetCleanup(unittest.TestCase):
    def test_appends_cleanup(self):
        result = rules.repair_apt_get_cleanup("RUN apt-get install -y curl", {"DL3009"}, NO_NETWORK)
        self.assertTrue(result.text.endswith("&& rm -rf /var/lib/apt/lists/*"))
        self.assertEqual(result.handled_rule_ids, {"DL3009"})

    def test_leaves_existing_cleanup_untouched(self):
        text = "RUN apt-get install -y curl && rm -rf /var/lib/apt/lists/*"
        result = rules.repair_apt_get_cleanup(text, {"DL3009"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestAddNoCacheFlag(unittest.TestCase):
    def test_adds_apt_no_install_recommends(self):
        result = rules.repair_add_no_cache_flag("RUN apt-get install -y curl", {"aptGetInstallUseNoRec"}, NO_NETWORK)
        self.assertIn("--no-install-recommends", result.text)
        self.assertEqual(result.handled_rule_ids, {"aptGetInstallUseNoRec"})

    def test_adds_apk_no_cache(self):
        result = rules.repair_add_no_cache_flag("RUN apk add curl", {"DL3019", "apkAddUseNoCache"}, NO_NETWORK)
        self.assertIn("--no-cache", result.text)
        self.assertEqual(result.handled_rule_ids, {"DL3019", "apkAddUseNoCache"})

    def test_adds_pip_no_cache_dir(self):
        result = rules.repair_add_no_cache_flag("RUN pip install requests", {"pipUseNoCacheDir"}, NO_NETWORK)
        self.assertIn("--no-cache-dir", result.text)


class TestRequireYesFlag(unittest.TestCase):
    def test_adds_apt_y_flag(self):
        result = rules.repair_require_yes_flag("RUN apt-get install curl", {"aptGetInstallUseY"}, NO_NETWORK)
        self.assertIn(" -y ", result.text + " ")
        self.assertEqual(result.handled_rule_ids, {"aptGetInstallUseY"})

    def test_does_not_duplicate_existing_yes_flag(self):
        text = "RUN apt-get install -y curl"
        result = rules.repair_require_yes_flag(text, {"aptGetInstallUseY"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestFuseAptUpdate(unittest.TestCase):
    def test_prepends_apt_get_update(self):
        result = rules.repair_fuse_apt_update("RUN apt-get install -y curl", {"aptGetUpdatePrecedesInstall"}, NO_NETWORK)
        self.assertTrue(result.text.startswith("RUN apt-get update && apt-get install"))

    def test_leaves_existing_update_untouched(self):
        text = "RUN apt-get update && apt-get install -y curl"
        result = rules.repair_fuse_apt_update(text, {"aptGetUpdatePrecedesInstall"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestMergeDuplicateInstalls(unittest.TestCase):
    def test_always_defers(self):
        text = "RUN apt-get install -y curl"
        result = rules.repair_merge_duplicate_installs(text, {"ruleMoreThanOneInstall"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestAddCacheCleanup(unittest.TestCase):
    def test_appends_yum_cache_cleanup(self):
        result = rules.repair_add_cache_cleanup("RUN yum install -y curl", {"yumInstallRmVarCacheYum"}, NO_NETWORK)
        self.assertIn("/var/cache/yum", result.text)

    def test_appends_npm_cache_clean_after_install(self):
        result = rules.repair_add_cache_cleanup("RUN npm install", {"npmCacheCleanAfterInstall"}, NO_NETWORK)
        self.assertIn("npm cache clean --force", result.text)

    def test_adds_force_to_existing_npm_cache_clean(self):
        result = rules.repair_add_cache_cleanup("RUN npm install && npm cache clean", {"npmCacheCleanUseForce"}, NO_NETWORK)
        self.assertIn("npm cache clean --force", result.text)

    def test_appends_yarn_cache_clean(self):
        result = rules.repair_add_cache_cleanup("RUN yarn install", {"yarnCacheCleanAfterInstall"}, NO_NETWORK)
        self.assertIn("yarn cache clean", result.text)


class TestHardenCurlWget(unittest.TestCase):
    def test_adds_flags_and_upgrades_scheme_for_standalone_curl(self):
        result = rules.repair_harden_curl_wget("RUN curl http://example.com/f", {"curlUseFlagF", "curlUseFlagL", "curlUseHttpsUrl"}, NO_NETWORK)
        self.assertIn("https://example.com/f", result.text)
        self.assertTrue(rules._has_short_flag(result.text, "f"))
        self.assertTrue(rules._has_short_flag(result.text, "L"))
        self.assertEqual(result.handled_rule_ids, {"curlUseFlagF", "curlUseFlagL", "curlUseHttpsUrl"})

    def test_counts_already_satisfied_flags_as_handled(self):
        # -fsSL already covers curlUseFlagF/curlUseFlagL -- both must be reported as
        # handled even though this function makes no further edit for them.
        result = rules.repair_harden_curl_wget("RUN curl -fsSL https://example.com/f -o /tmp/f", {"curlUseFlagF", "curlUseFlagL"}, NO_NETWORK)
        self.assertEqual(result.handled_rule_ids, {"curlUseFlagF", "curlUseFlagL"})
        self.assertEqual(result.text, "RUN curl -fsSL https://example.com/f -o /tmp/f")

    def test_upgrades_wget_scheme(self):
        result = rules.repair_harden_curl_wget("RUN wget http://example.com/f", {"wgetUseHttpsUrl"}, NO_NETWORK)
        self.assertIn("https://example.com/f", result.text)

    def test_first_command_in_chain_is_recognised(self):
        # Regression: a curl/wget command with nothing before it in the RUN instruction
        # must still be recognised, not only ones after a `&&`.
        result = rules.repair_harden_curl_wget("RUN wget http://example.com/f", {"wgetUseHttpsUrl"}, NO_NETWORK)
        self.assertEqual(result.handled_rule_ids, {"wgetUseHttpsUrl"})


class TestFixChecksumSignature(unittest.TestCase):
    def test_expands_single_space_to_two(self):
        # sha256sum -c parses "<hash>  <filename>" (two spaces); a single space is
        # misparsed as part of the filename, which is what this rule flags.
        digest = "a" * 64
        result = rules.repair_fix_checksum_signature(f"RUN echo '{digest} file.tar.gz' | sha256sum -c", {"sha256sumEchoOneSpaces"}, NO_NETWORK)
        self.assertIn(f"{digest}  file.tar.gz", result.text)
        self.assertEqual(result.handled_rule_ids, {"sha256sumEchoOneSpaces"})

    def test_leaves_already_correct_double_space_untouched(self):
        digest = "a" * 64
        result = rules.repair_fix_checksum_signature(f"RUN echo '{digest}  file.tar.gz' | sha256sum -c", {"sha256sumEchoOneSpaces"}, NO_NETWORK)
        self.assertEqual(result.handled_rule_ids, set())

    def test_appends_asc_removal(self):
        result = rules.repair_fix_checksum_signature("RUN gpg --verify file.tar.gz.asc file.tar.gz", {"gpgVerifyAscRmAsc"}, NO_NETWORK)
        self.assertIn("rm -f file.tar.gz.asc", result.text)


class TestArgNoDefault(unittest.TestCase):
    def test_always_defers(self):
        text = "ARG BUILD_ENV"
        result = rules.repair_arg_no_default(text, {"arg_no_default"}, NO_NETWORK)
        self.assertEqual(result.text, text)
        self.assertEqual(result.handled_rule_ids, set())


class TestPinGitClone(unittest.TestCase):
    def test_pins_to_resolved_tag(self):
        resolvers = Resolvers(github_latest_tag=lambda owner, repo: "v1.2.3")
        result = rules.repair_pin_git_clone("RUN git clone https://github.com/example/repo.git", {"git_clone_no_pin"}, resolvers)
        self.assertIn("git checkout v1.2.3", result.text)
        self.assertEqual(result.handled_rule_ids, {"git_clone_no_pin"})

    def test_leaves_already_pinned_clone_untouched(self):
        text = "RUN git clone https://github.com/example/repo.git && cd repo && git checkout abc123"
        result = rules.repair_pin_git_clone(text, {"git_clone_no_pin"}, NO_NETWORK)
        self.assertEqual(result.text, text)

    def test_falls_back_for_non_github_url(self):
        result = rules.repair_pin_git_clone("RUN git clone https://gitlab.com/example/repo.git", {"git_clone_no_pin"}, NO_NETWORK)
        self.assertEqual(result.handled_rule_ids, set())

    def test_falls_back_when_tag_unresolvable(self):
        resolvers = Resolvers(github_latest_tag=lambda owner, repo: None)
        result = rules.repair_pin_git_clone("RUN git clone https://github.com/example/repo.git", {"git_clone_no_pin"}, resolvers)
        self.assertEqual(result.handled_rule_ids, set())


class TestReplaceAdd(unittest.TestCase):
    def test_remote_url_becomes_run_curl(self):
        result = rules.repair_replace_add("ADD https://example.com/data.tar.gz /data.tar.gz", {"add_remote_url"}, NO_NETWORK)
        self.assertTrue(result.text.startswith("RUN curl"))
        self.assertIn("sha256sum", result.text)
        self.assertEqual(result.handled_rule_ids, {"add_remote_url"})

    def test_local_file_becomes_copy(self):
        result = rules.repair_replace_add("ADD app.tar.gz /app.tar.gz", {"DL3020"}, NO_NETWORK)
        self.assertEqual(result.text, "COPY app.tar.gz /app.tar.gz")
        self.assertEqual(result.handled_rule_ids, {"DL3020"})


class TestPinPackageVersion(unittest.TestCase):
    def test_pins_pip_package(self):
        resolvers = Resolvers(pypi_latest_version=lambda pkg: "2.32.3")
        result = rules.repair_pin_package_version("RUN pip install requests", {"DL3013"}, resolvers)
        self.assertEqual(result.text, "RUN pip install requests==2.32.3")
        self.assertEqual(result.handled_rule_ids, {"DL3013"})

    def test_pins_npm_package(self):
        resolvers = Resolvers(npm_latest_version=lambda pkg: "4.17.21")
        result = rules.repair_pin_package_version("RUN npm install lodash", {"DL3016"}, resolvers)
        self.assertEqual(result.text, "RUN npm install lodash@4.17.21")

    def test_pins_gem_package(self):
        resolvers = Resolvers(rubygems_latest_version=lambda gem: "7.1.0")
        result = rules.repair_pin_package_version("RUN gem install rails", {"DL3028"}, resolvers)
        self.assertEqual(result.text, "RUN gem install rails -v 7.1.0")

    def test_pins_npm_package_even_after_a_cache_clean_suffix_was_already_appended(self):
        # Regression: repair_add_cache_cleanup runs earlier in SUB_REPAIRS and appends
        # "&& npm cache clean --force" to this same instruction whenever
        # npmCacheCleanAfterInstall also fires (true for almost every bare `npm
        # install`), which it does. A naive tail.split() would treat "&&" as part of
        # the package list and bail out as ambiguous.
        resolvers = Resolvers(npm_latest_version=lambda pkg: "4.17.21")
        result = rules.repair_pin_package_version("RUN npm install lodash && npm cache clean --force", {"DL3016"}, resolvers)
        self.assertEqual(result.text, "RUN npm install lodash@4.17.21 && npm cache clean --force")
        self.assertEqual(result.handled_rule_ids, {"DL3016"})

    def test_apt_apk_yum_zypper_dnf_always_defer(self):
        for rule_id, command in [
            ("DL3008", "RUN apt-get install -y curl"),
            ("DL3018", "RUN apk add curl"),
            ("DL3033", "RUN yum install -y curl"),
            ("DL3037", "RUN zypper install curl"),
            ("DL3041", "RUN dnf install -y curl"),
        ]:
            with self.subTest(rule_id=rule_id):
                result = rules.repair_pin_package_version(command, {rule_id}, NO_NETWORK)
                self.assertEqual(result.handled_rule_ids, set())

    def test_multiple_packages_are_too_ambiguous_to_pin(self):
        resolvers = Resolvers(pypi_latest_version=_unused)
        result = rules.repair_pin_package_version("RUN pip install requests flask", {"DL3013"}, resolvers)
        self.assertEqual(result.handled_rule_ids, set())

    def test_falls_back_when_version_unresolvable(self):
        resolvers = Resolvers(pypi_latest_version=lambda pkg: None)
        result = rules.repair_pin_package_version("RUN pip install requests", {"DL3013"}, resolvers)
        self.assertEqual(result.handled_rule_ids, set())


class TestAddDownloadChecksum(unittest.TestCase):
    def test_adds_checksum_verification_without_a_pipe(self):
        resolvers = Resolvers(fetch_sha256=lambda url: "c" * 64)
        result = rules.repair_add_download_checksum(
            "RUN wget https://example.com/archive.tar.gz -O archive.tar.gz",
            {"download_no_checksum"},
            resolvers,
        )
        self.assertIn("c" * 64, result.text)
        self.assertNotIn("|", result.text)
        self.assertIn("sha256sum -c", result.text)
        self.assertEqual(result.handled_rule_ids, {"download_no_checksum"})

    def test_falls_back_when_checksum_unresolvable(self):
        resolvers = Resolvers(fetch_sha256=lambda url: None)
        result = rules.repair_add_download_checksum(
            "RUN wget https://example.com/archive.tar.gz -O archive.tar.gz",
            {"download_no_checksum"},
            resolvers,
        )
        self.assertEqual(result.handled_rule_ids, set())


if __name__ == "__main__":
    unittest.main()
