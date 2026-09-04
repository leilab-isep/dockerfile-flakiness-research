"""One positive and one negative case per custom rule."""

import unittest

from flakiscan.detection import custom_rules
from flakiscan.detection.dockerfile_parser import Instruction


def inst(instruction: str, args: str, line: int = 1) -> Instruction:
    return Instruction(line_number=line, instruction=instruction, args=args, raw=f"{instruction} {args}")


def rule_ids(findings: list[dict]) -> set[str]:
    return {f["rule_id"] for f in findings}


class TestImplicitAndExplicitLatest(unittest.TestCase):
    def test_no_tag_is_implicit_latest(self):
        findings = custom_rules.rule_implicit_and_explicit_latest([inst("FROM", "ubuntu")])
        self.assertEqual(rule_ids(findings), {"implicit_latest"})

    def test_explicit_latest_tag(self):
        findings = custom_rules.rule_implicit_and_explicit_latest([inst("FROM", "ubuntu:latest")])
        self.assertEqual(rule_ids(findings), {"explicit_latest"})

    def test_pinned_version_is_clean(self):
        findings = custom_rules.rule_implicit_and_explicit_latest([inst("FROM", "ubuntu:22.04")])
        self.assertEqual(findings, [])

    def test_digest_pinned_is_clean(self):
        findings = custom_rules.rule_implicit_and_explicit_latest(
            [inst("FROM", "ubuntu@sha256:" + "a" * 64)]
        )
        self.assertEqual(findings, [])

    def test_multi_stage_alias_reference_is_clean(self):
        instructions = [
            inst("FROM", "golang:1.21 AS builder", line=1),
            inst("FROM", "builder", line=5),
        ]
        findings = custom_rules.rule_implicit_and_explicit_latest(instructions)
        self.assertEqual(findings, [])


class TestCurlPipeShell(unittest.TestCase):
    def test_curl_pipe_bash_flagged(self):
        findings = custom_rules.rule_curl_pipe_shell([inst("RUN", "curl -sL https://x.io/i.sh | bash")])
        self.assertEqual(rule_ids(findings), {"curl_pipe_shell"})

    def test_plain_curl_download_not_flagged(self):
        findings = custom_rules.rule_curl_pipe_shell([inst("RUN", "curl -o file.tar.gz https://x.io/file.tar.gz")])
        self.assertEqual(findings, [])

    def test_curl_as_apt_package_with_unrelated_pipe_not_flagged(self):
        findings = custom_rules.rule_curl_pipe_shell(
            [inst("RUN", "apt-get install -y curl && echo hi | bash")]
        )
        self.assertEqual(findings, [])


class TestAddRemoteUrl(unittest.TestCase):
    def test_http_source_flagged(self):
        findings = custom_rules.rule_add_remote_url([inst("ADD", "https://x.io/data.tar.gz /data.tar.gz")])
        self.assertEqual(rule_ids(findings), {"add_remote_url"})

    def test_local_source_not_flagged(self):
        findings = custom_rules.rule_add_remote_url([inst("ADD", "./local.tar.gz /data.tar.gz")])
        self.assertEqual(findings, [])


class TestGitCloneNoPin(unittest.TestCase):
    def test_clone_without_checkout_flagged(self):
        findings = custom_rules.rule_git_clone_no_pin([inst("RUN", "git clone https://x.io/repo.git")])
        self.assertEqual(rule_ids(findings), {"git_clone_no_pin"})

    def test_clone_with_checkout_not_flagged(self):
        findings = custom_rules.rule_git_clone_no_pin(
            [inst("RUN", "git clone https://x.io/repo.git && cd repo && git checkout abc123")]
        )
        self.assertEqual(findings, [])


class TestArgNoDefault(unittest.TestCase):
    def test_arg_without_default_flagged(self):
        findings = custom_rules.rule_arg_no_default([inst("ARG", "BUILD_ENV")])
        self.assertEqual(rule_ids(findings), {"arg_no_default"})

    def test_arg_with_default_not_flagged(self):
        findings = custom_rules.rule_arg_no_default([inst("ARG", "BUILD_ENV=production")])
        self.assertEqual(findings, [])


class TestMissingPipefail(unittest.TestCase):
    def test_pipe_without_pipefail_flagged(self):
        findings = custom_rules.rule_missing_pipefail([inst("RUN", "cat file | grep foo")])
        self.assertEqual(rule_ids(findings), {"missing_pipefail"})

    def test_pipe_with_preceding_shell_pipefail_not_flagged(self):
        instructions = [
            inst("SHELL", '["/bin/bash", "-o", "pipefail", "-c"]', line=1),
            inst("RUN", "cat file | grep foo", line=2),
        ]
        findings = custom_rules.rule_missing_pipefail(instructions)
        self.assertEqual(findings, [])

    def test_pipe_with_inline_set_pipefail_not_flagged(self):
        findings = custom_rules.rule_missing_pipefail(
            [inst("RUN", "set -o pipefail && cat file | grep foo")]
        )
        self.assertEqual(findings, [])

    def test_no_pipe_not_flagged(self):
        findings = custom_rules.rule_missing_pipefail([inst("RUN", "apt-get install -y curl")])
        self.assertEqual(findings, [])


class TestDownloadNoChecksum(unittest.TestCase):
    def test_wget_without_checksum_flagged(self):
        findings = custom_rules.rule_download_no_checksum([inst("RUN", "wget https://x.io/file.tar.gz")])
        self.assertEqual(rule_ids(findings), {"download_no_checksum"})

    def test_wget_with_checksum_not_flagged(self):
        findings = custom_rules.rule_download_no_checksum(
            [inst("RUN", "wget https://x.io/file.tar.gz && sha256sum -c file.tar.gz.sha256")]
        )
        self.assertEqual(findings, [])

    def test_curl_pipe_shell_not_double_counted(self):
        findings = custom_rules.rule_download_no_checksum([inst("RUN", "curl https://x.io/i.sh | bash")])
        self.assertEqual(findings, [])

    def test_curl_as_apt_package_name_not_flagged(self):
        findings = custom_rules.rule_download_no_checksum(
            [inst("RUN", "apt-get update && apt-get install -y curl")]
        )
        self.assertEqual(findings, [])


class TestRunAllRules(unittest.TestCase):
    def test_run_marks_every_finding_flakiness_relevant(self):
        instructions = [inst("FROM", "ubuntu"), inst("ARG", "FOO")]
        findings = custom_rules.run(instructions)
        self.assertTrue(all(f["flakiness_relevant"] for f in findings))
        self.assertEqual(rule_ids(findings), {"implicit_latest", "arg_no_default"})

    def test_run_respects_scoped_ignore_map(self):
        instructions = [inst("FROM", "ubuntu", line=1), inst("ARG", "FOO", line=2)]
        findings = custom_rules.run(instructions, ignore_map={1: {"implicit_latest"}})
        self.assertEqual(rule_ids(findings), {"arg_no_default"})

    def test_run_respects_bare_ignore_map(self):
        instructions = [inst("FROM", "ubuntu", line=1), inst("ARG", "FOO", line=2)]
        findings = custom_rules.run(instructions, ignore_map={1: None})
        self.assertEqual(rule_ids(findings), {"arg_no_default"})


if __name__ == "__main__":
    unittest.main()
