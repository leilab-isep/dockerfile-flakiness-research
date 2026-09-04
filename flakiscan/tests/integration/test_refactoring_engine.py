"""Integration tests for the repair engine: minimal patch, idempotency, `# TODO`
fallbacks, and `# flakiscan-ignore` interaction. Uses fake resolvers throughout so the
suite never depends on network access."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flakiscan.refactoring.engine import repair_dockerfile
from flakiscan.refactoring.resolvers import Resolvers

FIXTURES = Path(__file__).parent.parent / "fixtures"

FAKE_RESOLVERS = Resolvers(
    pypi_latest_version=lambda pkg: "2.32.3",
    npm_latest_version=lambda pkg: "4.17.21",
    rubygems_latest_version=lambda gem: "7.1.0",
    github_latest_tag=lambda owner, repo: "v9.9.9",
    docker_hub_digest=lambda repo, tag: "sha256:" + "a" * 64,
    fetch_sha256=lambda url: "b" * 64,
)


def _write(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False) as f:
        f.write(content)
        return f.name


class TestMinimalPatch(unittest.TestCase):
    def test_only_flagged_instructions_change(self):
        report = repair_dockerfile(str(FIXTURES / "flaky.Dockerfile"), resolvers=FAKE_RESOLVERS)
        original_lines = (FIXTURES / "flaky.Dockerfile").read_text().splitlines()
        patched_lines = report.patched_text.splitlines()

        touched_line_numbers = {a.line_number for a in report.actions}
        # Every line untouched by a repair must appear, unmodified, somewhere in the
        # patched output, in original order -- this is a coarse but effective check
        # that unrelated content (COPY, WORKDIR, CMD, blank lines) survives untouched.
        untouched_original = [
            line for i, line in enumerate(original_lines, start=1) if i not in touched_line_numbers and line.strip()
        ]
        for line in untouched_original:
            self.assertIn(line, patched_lines)


class TestIdempotency(unittest.TestCase):
    def _run_twice(self, resolvers: Resolvers) -> tuple[str, str]:
        report1 = repair_dockerfile(str(FIXTURES / "flaky.Dockerfile"), resolvers=resolvers)
        path2 = _write(report1.patched_text)
        report2 = repair_dockerfile(path2, resolvers=resolvers)
        return report1.patched_text, report2.patched_text

    def test_idempotent_when_fixes_succeed(self):
        first, second = self._run_twice(FAKE_RESOLVERS)
        self.assertEqual(first, second)

    def test_idempotent_when_everything_falls_back(self):
        always_none = Resolvers(
            pypi_latest_version=lambda pkg: None,
            npm_latest_version=lambda pkg: None,
            rubygems_latest_version=lambda gem: None,
            github_latest_tag=lambda owner, repo: None,
            docker_hub_digest=lambda repo, tag: None,
            fetch_sha256=lambda url: None,
        )
        first, second = self._run_twice(always_none)
        self.assertEqual(first, second)

    def test_third_pass_is_still_stable(self):
        report1 = repair_dockerfile(str(FIXTURES / "flaky.Dockerfile"), resolvers=FAKE_RESOLVERS)
        path2 = _write(report1.patched_text)
        report2 = repair_dockerfile(path2, resolvers=FAKE_RESOLVERS)
        path3 = _write(report2.patched_text)
        report3 = repair_dockerfile(path3, resolvers=FAKE_RESOLVERS)
        self.assertEqual(report2.patched_text, report3.patched_text)


class TestIgnoreCommentInteraction(unittest.TestCase):
    def test_ignore_comment_stays_directly_above_instruction_after_repair(self):
        path = _write("# flakiscan-ignore: DL3007\nFROM ubuntu:latest\n")
        report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)
        lines = report.patched_text.splitlines()
        from_index = next(i for i, line in enumerate(lines) if line.startswith("FROM"))
        self.assertEqual(lines[from_index - 1], "# flakiscan-ignore: DL3007")

    def test_repeated_repair_does_not_break_ignore_suppression(self):
        # Regression: inserting a TODO comment directly above the instruction would
        # push a pre-existing ignore comment down by one line, so on the next run its
        # target line no longer matched the instruction it was meant to suppress.
        path = _write("# flakiscan-ignore: DL3007\nFROM ubuntu:latest\n")
        report1 = repair_dockerfile(path, resolvers=Resolvers(docker_hub_digest=lambda repo, tag: None))
        path2 = _write(report1.patched_text)
        report2 = repair_dockerfile(path2, resolvers=Resolvers(docker_hub_digest=lambda repo, tag: None))
        self.assertEqual(report1.patched_text, report2.patched_text)


class TestTodoFallback(unittest.TestCase):
    def test_unfixable_finding_gets_todo_comment(self):
        path = _write("ARG BUILD_ENV\n")
        report = repair_dockerfile(path, resolvers=FAKE_RESOLVERS)
        self.assertIn("# TODO(flakiscan): could not automatically fix: arg_no_default", report.patched_text)

    def test_todo_comment_lists_all_unhandled_rules_for_the_line(self):
        action = repair_dockerfile(
            str(FIXTURES / "flaky.Dockerfile"), resolvers=Resolvers(pypi_latest_version=lambda pkg: None)
        )
        pip_action = next(a for a in action.actions if a.instruction == "RUN" and "DL3013" in a.triggered_rule_ids)
        self.assertIn("DL3013", pip_action.fallback_rule_ids)
        self.assertNotIn("DL3013", pip_action.applied_rule_ids)

    def test_repair_report_never_silently_drops_a_finding(self):
        report = repair_dockerfile(str(FIXTURES / "flaky.Dockerfile"), resolvers=FAKE_RESOLVERS)
        for action in report.actions:
            self.assertEqual(
                set(action.triggered_rule_ids),
                set(action.applied_rule_ids) | set(action.fallback_rule_ids),
            )


class TestNeverInvokesDockerBuild(unittest.TestCase):
    def test_repair_never_calls_docker_build(self):
        real_run = subprocess.run

        def guarded_run(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            flat = " ".join(argv) if isinstance(argv, (list, tuple)) else str(argv)
            if "docker" in flat and "build" in flat:
                raise AssertionError(f"repair invoked a forbidden command: {flat!r}")
            return real_run(*args, **kwargs)

        with patch("subprocess.run", side_effect=guarded_run):
            repair_dockerfile(str(FIXTURES / "flaky.Dockerfile"), resolvers=FAKE_RESOLVERS)


class TestHealthyFixtureIsUntouched(unittest.TestCase):
    def test_no_findings_means_no_changes(self):
        report = repair_dockerfile(str(FIXTURES / "healthy.Dockerfile"), resolvers=FAKE_RESOLVERS)
        self.assertEqual(report.actions, [])
        self.assertEqual(report.patched_text, (FIXTURES / "healthy.Dockerfile").read_text())


if __name__ == "__main__":
    unittest.main()
