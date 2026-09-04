"""Custom pattern-matching rules for flakiness risks that Hadolint and Parfum do not check.

Each rule is a small function over the parsed instruction list; `run` executes all of
them, filters out any finding suppressed by a `# flakiscan-ignore` comment, and returns
raw findings in the same shape as the other two adapters.
"""

from __future__ import annotations

import re

from flakiscan.detection.dockerfile_parser import Instruction
from flakiscan.detection.ignore_comments import IgnoreMap, filter_ignored
from flakiscan.schema import Category

_CHECKSUM_TOOLS = re.compile(r"\b(sha256sum|sha1sum|md5sum|gpg\s+--verify)\b")
# curl/wget must be the command immediately feeding the pipe, not merely present
# somewhere earlier in the RUN (e.g. as an apt-get package name).
_CURL_OR_WGET_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget)\b[^|&;]*\|\s*(sudo\s+)?(bash|sh|zsh|ash)\b"
)
_SINGLE_PIPE = re.compile(r"(?<!\|)\|(?!\|)")
_GIT_CLONE = re.compile(r"\bgit\s+clone\b")
_GIT_CHECKOUT = re.compile(r"\bgit\s+checkout\b")
_COMMAND_SEPARATORS = re.compile(r"&&|\|\||\||;")


def _from_image_and_alias(args: str) -> tuple[str, str | None]:
    """Split `FROM image[:tag][@digest] [AS alias]` into (image_ref, alias)."""
    parts = re.split(r"\s+AS\s+", args, maxsplit=1, flags=re.IGNORECASE)
    image_ref = parts[0].strip()
    alias = parts[1].strip() if len(parts) > 1 else None
    return image_ref, alias


def rule_implicit_and_explicit_latest(instructions: list[Instruction]) -> list[dict]:
    """Flag FROM instructions that resolve to the mutable `:latest` tag.

    A FROM instruction is exempt if it is digest-pinned (`@sha256:...`) or if it
    references an earlier build stage by alias (multi-stage builds), since that is not
    a registry image at all.
    """
    findings = []
    known_aliases: set[str] = set()

    for inst in instructions:
        if inst.instruction != "FROM":
            continue

        image_ref, alias = _from_image_and_alias(inst.args)
        if alias:
            known_aliases.add(alias.lower())

        if "@sha256:" in image_ref:
            continue
        if image_ref.lower() in known_aliases:
            continue  # References a previous build stage, not a registry image.

        # image[:tag] -- a ':' after the last '/' separates the tag from the name.
        name_part = image_ref.rsplit("/", 1)[-1]
        if ":" not in name_part:
            findings.append({
                "rule_id": "implicit_latest",
                "line_number": inst.line_number,
                "message": f"FROM '{image_ref}' has no tag and defaults to :latest.",
                "category": Category.BASE_IMAGE,
            })
        elif name_part.rsplit(":", 1)[-1] == "latest":
            findings.append({
                "rule_id": "explicit_latest",
                "line_number": inst.line_number,
                "message": f"FROM '{image_ref}' explicitly pins the mutable :latest tag.",
                "category": Category.BASE_IMAGE,
            })

    return findings


def rule_curl_pipe_shell(instructions: list[Instruction]) -> list[dict]:
    """Flag RUN instructions that pipe a remote download directly into a shell."""
    findings = []
    for inst in instructions:
        if inst.instruction != "RUN":
            continue
        if _CURL_OR_WGET_PIPE_TO_SHELL.search(inst.args):
            findings.append({
                "rule_id": "curl_pipe_shell",
                "line_number": inst.line_number,
                "message": "Remote script piped directly into a shell interpreter.",
                "category": Category.NETWORK,
            })
    return findings


def rule_add_remote_url(instructions: list[Instruction]) -> list[dict]:
    """Flag ADD instructions that fetch their source from an HTTP(S) URL."""
    findings = []
    for inst in instructions:
        if inst.instruction != "ADD":
            continue
        first_token = inst.args.split()[0] if inst.args.split() else ""
        if first_token.lower().startswith(("http://", "https://")):
            findings.append({
                "rule_id": "add_remote_url",
                "line_number": inst.line_number,
                "message": f"ADD fetches a remote URL ({first_token}); the resource may change or disappear.",
                "category": Category.NETWORK,
            })
    return findings


def rule_git_clone_no_pin(instructions: list[Instruction]) -> list[dict]:
    """Flag `git clone` calls not followed, in the same RUN instruction, by a checkout
    to a specific ref. Without a pinned ref, the build depends on whatever commit
    happens to be at the tip of the default branch at build time."""
    findings = []
    for inst in instructions:
        if inst.instruction != "RUN":
            continue
        if _GIT_CLONE.search(inst.args) and not _GIT_CHECKOUT.search(inst.args):
            findings.append({
                "rule_id": "git_clone_no_pin",
                "line_number": inst.line_number,
                "message": "git clone is not followed by a git checkout to a pinned ref; build depends on the current HEAD.",
                "category": Category.NETWORK,
            })
    return findings


def rule_arg_no_default(instructions: list[Instruction]) -> list[dict]:
    """Flag ARG instructions with no default value, since the build's behavior then
    depends on whether and how `--build-arg` is passed at build time."""
    findings = []
    for inst in instructions:
        if inst.instruction != "ARG":
            continue
        if "=" not in inst.args:
            findings.append({
                "rule_id": "arg_no_default",
                "line_number": inst.line_number,
                "message": f"ARG '{inst.args}' has no default value; build is not reproducible without --build-arg.",
                "category": Category.ENVIRONMENT,
            })
    return findings


def rule_missing_pipefail(instructions: list[Instruction]) -> list[dict]:
    """Flag RUN instructions with a shell pipe that isn't protected by `pipefail`.

    Without `pipefail`, a shell pipeline's exit status is that of its last command, so
    a failure earlier in the pipe (e.g. a download that returns an error page) is
    silently ignored. A preceding `SHELL` instruction that sets `-o pipefail` (or
    `set -o pipefail` inside the RUN's own command chain) satisfies this for every
    following RUN.
    """
    findings = []
    pipefail_shell_active = False

    for inst in instructions:
        if inst.instruction == "SHELL" and "pipefail" in inst.args:
            pipefail_shell_active = True
            continue

        if inst.instruction != "RUN":
            continue
        if not _SINGLE_PIPE.search(inst.args):
            continue
        if pipefail_shell_active or "set -o pipefail" in inst.args:
            continue

        findings.append({
            "rule_id": "missing_pipefail",
            "line_number": inst.line_number,
            "message": "RUN contains a shell pipe without 'set -o pipefail'; a failure in an earlier stage of the pipe is silently ignored.",
            "category": Category.REPRODUCIBILITY,
        })

    return findings


def _is_download_command(segment: str) -> bool:
    """True if `segment` (one &&/;/|-separated shell command) actually invokes curl or
    wget as a command, as opposed to merely naming a package (e.g. `apt-get install
    curl`)."""
    first_word = segment.strip().split(" ", 1)[0] if segment.strip() else ""
    return first_word in ("curl", "wget")


def rule_download_no_checksum(instructions: list[Instruction]) -> list[dict]:
    """Flag curl/wget downloads with no following checksum verification step.

    Applies to file downloads only; a curl/wget piped straight into a shell is handled
    by rule_curl_pipe_shell instead, to avoid reporting the same line twice.
    """
    findings = []
    for inst in instructions:
        if inst.instruction != "RUN":
            continue

        segments = _COMMAND_SEPARATORS.split(inst.args)
        if not any(_is_download_command(s) for s in segments):
            continue
        if _CURL_OR_WGET_PIPE_TO_SHELL.search(inst.args):
            continue  # curl_pipe_shell's territory.
        if _CHECKSUM_TOOLS.search(inst.args):
            continue

        findings.append({
            "rule_id": "download_no_checksum",
            "line_number": inst.line_number,
            "message": "File download is not followed by a checksum verification step.",
            "category": Category.NETWORK,
        })
    return findings


_ALL_RULES = (
    rule_implicit_and_explicit_latest,
    rule_curl_pipe_shell,
    rule_add_remote_url,
    rule_git_clone_no_pin,
    rule_arg_no_default,
    rule_missing_pipefail,
    rule_download_no_checksum,
)


def run(instructions: list[Instruction], ignore_map: IgnoreMap | None = None) -> list[dict]:
    """Run all custom rules and return their findings.

    Every finding from this engine is flakiness_relevant, since it has no equivalent to
    Hadolint's purely stylistic rules. Findings matching a `# flakiscan-ignore` comment
    in `ignore_map` are filtered out before being returned.
    """
    findings = []
    for rule_fn in _ALL_RULES:
        for finding in rule_fn(instructions):
            finding["flakiness_relevant"] = True
            findings.append(finding)
    return filter_ignored(findings, ignore_map or {})
