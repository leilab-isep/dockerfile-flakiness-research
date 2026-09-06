"""Repair functions for detected findings, grouped into small composable units.

Multiple findings can target the same instruction (an `apt-get install` missing both
`-y` and `--no-install-recommends`, for example). Rather than one function per rule
independently rewriting the instruction from scratch -- which would silently drop
whichever fix ran first -- every function below takes the instruction's *current* text
(already possibly modified by an earlier function) and returns the next version, so
fixes compose instead of clobbering each other. `SUB_REPAIRS` lists them in application
order, each paired with the set of rule ids it knows how to address.

Coverage note: a few rules from different tools flag the identical underlying
condition on the same instruction (Hadolint's DL3007 and the custom `explicit_latest`
rule both fire on `FROM image:latest`; DL3009 and `aptGetInstallThenRemoveAptLists`
both fire on a missing apt list cleanup). Fixing the condition resolves every rule that
flags it, so each function below claims all of them, not just the rule it is primarily
built around.

A rule with no automated fix (for example `arg_no_default`, whose correct value
depends on an external `--build-arg` this engine has no way to know) is included here
as a function that always defers, so the omission is a documented decision rather than
a silent gap -- the caller treats any rule id no function claims as unhandled and
inserts a `# TODO` comment for it.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from flakiscan.refactoring.resolvers import Resolvers
from flakiscan.refactoring.result import SubRepairResult

RepairFunc = Callable[[str, set[str], Resolvers], SubRepairResult]

_SEGMENT_SPLIT_RE = re.compile(r"(&&|\|\||\||;)")
_LEADING_INSTRUCTION_RE = re.compile(r"^\s*(RUN)\s+", re.IGNORECASE)


def _split_commands(text: str) -> list[str]:
    """Split a RUN instruction's text into alternating command/separator segments,
    with the leading `RUN` keyword kept as its own segment.

    Splitting the whole instruction text directly would leave `RUN` glued to the first
    command (e.g. "RUN curl ..."), so a check for "is this segment's first word curl"
    would never match the first command in the chain -- only ones after a `&&`/`;`/`|`.
    """
    match = _LEADING_INSTRUCTION_RE.match(text)
    if not match:
        return _SEGMENT_SPLIT_RE.split(text)
    return [match.group(0)] + _SEGMENT_SPLIT_RE.split(text[match.end() :])


def _no_fix(text: str) -> SubRepairResult:
    return SubRepairResult(text=text)


def _has_short_flag(segment: str, letter: str) -> bool:
    """True if `segment` contains a short-option cluster (e.g. `-fsSL`) with `letter`."""
    return bool(re.search(rf"(?<!\S)-[A-Za-z]*{re.escape(letter)}[A-Za-z]*(?!\S)", segment))


# ---------------------------------------------------------------------------
# Pin base image to a digest
# ---------------------------------------------------------------------------

_FROM_RE = re.compile(r"^(FROM\s+)(\S+)(.*)$", re.IGNORECASE | re.DOTALL)


def _split_image_ref(image_ref: str) -> tuple[str, str]:
    """Split `name[:tag]` into (name, tag), defaulting the tag to 'latest'."""
    last_slash = image_ref.rfind("/")
    colon = image_ref.find(":", last_slash + 1)
    if colon == -1:
        return image_ref, "latest"
    return image_ref[:colon], image_ref[colon + 1 :]


def _docker_hub_repository(name: str) -> str | None:
    """Return the Docker Hub API repository path for `name`, or None if `name` points
    at a different registry (has an explicit host component)."""
    first_segment = name.split("/", 1)[0]
    if "." in first_segment or ":" in first_segment or first_segment == "localhost":
        return None
    return name if "/" in name else f"library/{name}"


def repair_pin_base_image_digest(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    match = _FROM_RE.match(text)
    if not match:
        return _no_fix(text)

    prefix, image_ref, suffix = match.groups()
    if "@sha256:" in image_ref:
        return _no_fix(text)

    name, tag = _split_image_ref(image_ref)
    repository = _docker_hub_repository(name)
    if repository is None:
        return _no_fix(text)  # A non-Docker-Hub registry; no generic anonymous-pull API to query.

    digest = resolvers.docker_hub_digest(repository, tag)
    if not digest:
        return _no_fix(text)

    return SubRepairResult(
        text=f"{prefix}{name}@{digest}{suffix}",
        handled_rule_ids=set(rule_ids),
        rationale=[f"Pinned base image to {name}@{digest} (resolved from Docker Hub, tag '{tag}')."],
    )


# ---------------------------------------------------------------------------
# Add pipefail
# ---------------------------------------------------------------------------

_RUN_RE = re.compile(r"^(RUN\s+)(.*)$", re.IGNORECASE | re.DOTALL)


def repair_add_pipefail(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    """Make a RUN instruction's pipe failure-safe by explicitly invoking bash for it.

    `set -o pipefail` is a bash/zsh/ksh feature -- it does not exist in the POSIX `sh`
    (dash, on Debian-family images) that a RUN instruction uses by default whenever the
    Dockerfile has no preceding SHELL instruction, which is the common case this rule
    exists to fix in the first place. Prepending `set -o pipefail &&` to the RUN's
    existing shell-form text as a plain string therefore *breaks a build that
    previously succeeded*: dash rejects the option and the whole instruction fails
    before any of the real command runs. Switching this one instruction to the JSON
    exec form, explicitly naming bash as the interpreter, sidesteps the question of
    what the ambient default shell is -- bash is present on the large majority of
    real-world base images (Debian/Ubuntu family in particular) -- without depending on
    a separate SHELL instruction persisting correctly across the rest of the file.
    """
    match = _RUN_RE.match(text)
    if not match or "pipefail" in match.group(2):
        return _no_fix(text)

    prefix, body = match.groups()
    exec_form = json.dumps(["/bin/bash", "-o", "pipefail", "-c", body])
    return SubRepairResult(
        text=f"{prefix}{exec_form}",
        handled_rule_ids=set(rule_ids),
        rationale=[
            "Switched to the exec form, explicitly running this instruction with `bash -o pipefail -c` "
            "so a failure earlier in the pipe is not silently ignored -- appending `set -o pipefail` as "
            "plain text would break on the POSIX `sh` a RUN instruction uses by default."
        ],
    )


# ---------------------------------------------------------------------------
# Restructure curl|bash into download-then-execute
# ---------------------------------------------------------------------------

_CURL_WGET_PIPE_SHELL_RE = re.compile(r"(curl|wget)\b([^|&;]*)\|\s*(?:sudo\s+)?(bash|sh|zsh|ash)\b")
_URL_RE = re.compile(r"https?://\S+")


def repair_restructure_curl_pipe_shell(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    match = _CURL_WGET_PIPE_SHELL_RE.search(text)
    if not match:
        return _no_fix(text)

    tool, args, shell = match.groups()
    url_match = _URL_RE.search(args)
    if not url_match:
        return _no_fix(text)
    url = url_match.group(0)

    script_name = url.rsplit("/", 1)[-1] or "script.sh"
    if "." not in script_name:
        script_name += ".sh"
    dest = f"/tmp/{script_name}"

    download_cmd = f"curl -fsSL {url} -o {dest}" if tool == "curl" else f"wget -q {url} -O {dest}"
    replacement = f"{download_cmd} && sha256sum {dest} && {shell} {dest}"

    return SubRepairResult(
        text=text[: match.start()] + replacement + text[match.end() :],
        handled_rule_ids=set(rule_ids),
        rationale=[
            f"Restructured the piped-to-shell download into download-then-execute; "
            f"the printed sha256sum makes it visible in build logs if {url} ever changes."
        ],
    )


# ---------------------------------------------------------------------------
# Add apt-get list cleanup
# ---------------------------------------------------------------------------

_APT_GET_INSTALL_RE = re.compile(r"apt-get\s+install\b[^&;]*")
_APT_LIST_CLEANUP_RE = re.compile(r"rm\s+-rf\s+/var/lib/apt/lists")


def repair_apt_get_cleanup(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    if not _APT_GET_INSTALL_RE.search(text) or _APT_LIST_CLEANUP_RE.search(text):
        return _no_fix(text)

    return SubRepairResult(
        text=f"{text.rstrip()} && rm -rf /var/lib/apt/lists/*",
        handled_rule_ids=set(rule_ids),
        rationale=["Appended apt list cleanup so a stale package index is not left in the image layer."],
    )


# ---------------------------------------------------------------------------
# Add no-cache / no-install-recommends flags
# ---------------------------------------------------------------------------

_APT_INSTALL_ANCHOR = re.compile(r"apt-get\s+install\b")
_APK_ADD_ANCHOR = re.compile(r"apk\s+add\b")
_PIP_INSTALL_ANCHOR = re.compile(r"pip3?\s+install\b")
_YUM_INSTALL_ANCHOR = re.compile(r"yum\s+install\b")


def _insert_after(text: str, anchor: re.Pattern[str], token: str) -> str | None:
    match = anchor.search(text)
    if not match:
        return None
    return f"{text[:match.end()]} {token}{text[match.end():]}"


def repair_add_no_cache_flag(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    handled: set[str] = set()
    rationale: list[str] = []

    apk_rules = rule_ids & {"DL3019", "apkAddUseNoCache"}
    if apk_rules and "--no-cache" not in text and _APK_ADD_ANCHOR.search(text):
        updated = _insert_after(text, _APK_ADD_ANCHOR, "--no-cache")
        if updated:
            text = updated
            handled |= apk_rules
            rationale.append("Added --no-cache to apk add so no package index is cached in the image.")

    if "aptGetInstallUseNoRec" in rule_ids and "--no-install-recommends" not in text and _APT_INSTALL_ANCHOR.search(text):
        updated = _insert_after(text, _APT_INSTALL_ANCHOR, "--no-install-recommends")
        if updated:
            text = updated
            handled.add("aptGetInstallUseNoRec")
            rationale.append("Added --no-install-recommends to apt-get install to reduce installed surface area.")

    pip_rules = rule_ids & {"DL3042", "pipUseNoCacheDir"}
    if pip_rules and "--no-cache-dir" not in text and _PIP_INSTALL_ANCHOR.search(text):
        updated = _insert_after(text, _PIP_INSTALL_ANCHOR, "--no-cache-dir")
        if updated:
            text = updated
            handled |= pip_rules
            rationale.append("Added --no-cache-dir to pip install to avoid caching packages in the image layer.")

    return SubRepairResult(text=text, handled_rule_ids=handled, rationale=rationale)


# ---------------------------------------------------------------------------
# Require -y / non-interactive flag
# ---------------------------------------------------------------------------


def repair_require_yes_flag(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    handled: set[str] = set()
    rationale: list[str] = []

    if (
        "aptGetInstallUseY" in rule_ids
        and _APT_INSTALL_ANCHOR.search(text)
        and not re.search(r"apt-get\s+install\b[^&;]*(-y\b|--yes\b|--assume-yes\b)", text)
    ):
        updated = _insert_after(text, _APT_INSTALL_ANCHOR, "-y")
        if updated:
            text = updated
            handled.add("aptGetInstallUseY")
            rationale.append("Added -y to apt-get install so the build cannot hang on an interactive prompt.")

    if "yumInstallForceYes" in rule_ids and _YUM_INSTALL_ANCHOR.search(text) and not re.search(r"yum\s+install\b[^&;]*(-y\b|--assumeyes\b)", text):
        updated = _insert_after(text, _YUM_INSTALL_ANCHOR, "-y")
        if updated:
            text = updated
            handled.add("yumInstallForceYes")
            rationale.append("Added -y to yum install so the build cannot hang on an interactive prompt.")

    return SubRepairResult(text=text, handled_rule_ids=handled, rationale=rationale)


# ---------------------------------------------------------------------------
# Fuse a missing apt-get update into the install
# ---------------------------------------------------------------------------


def repair_fuse_apt_update(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    if "aptGetUpdatePrecedesInstall" not in rule_ids:
        return _no_fix(text)
    match = _APT_INSTALL_ANCHOR.search(text)
    if not match or re.search(r"apt-get\s+update\b", text):
        return _no_fix(text)

    return SubRepairResult(
        text=f"{text[:match.start()]}apt-get update && {text[match.start():]}",
        handled_rule_ids={"aptGetUpdatePrecedesInstall"},
        rationale=[
            "Prepended 'apt-get update' to this RUN so the install uses a fresh package "
            "index instead of one that may be stale or missing from an earlier layer. "
            "If a separate 'apt-get update' RUN instruction already exists elsewhere in "
            "the file, it is left in place -- merging across instructions is out of "
            "scope for a fix that only touches the flagged instruction."
        ],
    )


# ---------------------------------------------------------------------------
# Merge duplicate install commands -- always deferred
# ---------------------------------------------------------------------------


def repair_merge_duplicate_installs(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    # Safely merging install commands means removing or rewriting a *different*
    # instruction than the one being repaired, which this engine never does -- each
    # repair only touches the single instruction its finding is reported on. Always
    # deferred to a TODO comment naming the rule, rather than attempting a cross-
    # instruction edit that could reorder side effects.
    return _no_fix(text)


# ---------------------------------------------------------------------------
# Add package-manager cache cleanup
# ---------------------------------------------------------------------------

_NPM_CACHE_CLEAN_RE = re.compile(r"npm\s+cache\s+clean\b[^&;]*")
_SHELL_CONDITIONAL_RE = re.compile(r"\b(if|case)\b")


def repair_add_cache_cleanup(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    """Append the missing cache-cleanup command for the package manager(s) this
    instruction's findings identify.

    Skips `npmCacheCleanAfterInstall`/`yarnCacheCleanAfterInstall` when the instruction
    contains a shell `if`/`case` -- a real-world pattern this project's own corpus
    validation caught a regression from: a conditional installer script (`if [ -f
    package-lock.json ]; then npm ci; elif [ -f yarn.lock ]; then yarn install; ...
    fi`) only actually runs *one* of npm/yarn/pnpm depending on which lockfile is
    present, but appending `&& yarn cache clean` after the whole if/fi block runs it
    unconditionally regardless of which branch executed -- breaking a build that
    previously succeeded whenever yarn was not the branch taken (and so was never
    installed in the image at all). `yumInstallRmVarCacheYum`'s `rm -rf` is left
    unguarded since removing a directory that may already be empty is harmless even
    when appended after a branch that never ran.
    """
    handled: set[str] = set()
    rationale: list[str] = []
    conditional = bool(_SHELL_CONDITIONAL_RE.search(text))

    if "yumInstallRmVarCacheYum" in rule_ids and _YUM_INSTALL_ANCHOR.search(text) and "/var/cache/yum" not in text:
        text = f"{text.rstrip()} && rm -rf /var/cache/yum"
        handled.add("yumInstallRmVarCacheYum")
        rationale.append("Appended yum cache cleanup so a stale package cache is not left in the image.")

    if "npmCacheCleanUseForce" in rule_ids:
        clean_match = _NPM_CACHE_CLEAN_RE.search(text)
        if clean_match and "--force" not in clean_match.group(0):
            text = f"{text[:clean_match.end()]} --force{text[clean_match.end():]}"
            handled.add("npmCacheCleanUseForce")
            rationale.append("Added --force to npm cache clean, which npm otherwise ignores.")

    if "npmCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"npm\s+install\b", text) and not _NPM_CACHE_CLEAN_RE.search(text):
        text = f"{text.rstrip()} && npm cache clean --force"
        handled.add("npmCacheCleanAfterInstall")
        rationale.append("Appended npm cache clean after install to avoid caching packages in the image layer.")

    if "yarnCacheCleanAfterInstall" in rule_ids and not conditional and re.search(r"yarn\s+install\b", text) and "yarn cache clean" not in text:
        text = f"{text.rstrip()} && yarn cache clean"
        handled.add("yarnCacheCleanAfterInstall")
        rationale.append("Appended yarn cache clean after install to avoid caching packages in the image layer.")

    return SubRepairResult(text=text, handled_rule_ids=handled, rationale=rationale)


# ---------------------------------------------------------------------------
# Harden curl/wget flags and URLs
# ---------------------------------------------------------------------------


def repair_harden_curl_wget(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    """Add missing -f/-L flags to curl and upgrade http:// to https:// for curl/wget.

    A rule id is marked handled whenever its condition holds in the *final* text, not
    only when this function itself made the change -- an earlier repair in the
    pipeline (restructuring a piped-to-shell download, for example) can already add
    `-fsSL`, and that must still count as satisfying curlUseFlagF/curlUseFlagL rather
    than being reported as unfixed.
    """
    segments = _split_commands(text)
    handled: set[str] = set()

    for i, segment in enumerate(segments):
        if segment in ("&&", "||", "|", ";"):
            continue
        first_word = segment.strip().split(" ", 1)[0] if segment.strip() else ""
        updated = segment

        if first_word == "curl":
            if "curlUseHttpsUrl" in rule_ids:
                if "http://" in updated:
                    updated = updated.replace("http://", "https://")
                handled.add("curlUseHttpsUrl")
            if "curlUseFlagF" in rule_ids:
                if "--fail" not in updated and not _has_short_flag(updated, "f"):
                    updated = updated.replace("curl", "curl -f", 1)
                handled.add("curlUseFlagF")
            if "curlUseFlagL" in rule_ids:
                if "--location" not in updated and not _has_short_flag(updated, "L"):
                    updated = updated.replace("curl", "curl -L", 1)
                handled.add("curlUseFlagL")
        elif first_word == "wget" and "wgetUseHttpsUrl" in rule_ids:
            if "http://" in updated:
                updated = updated.replace("http://", "https://")
            handled.add("wgetUseHttpsUrl")

        segments[i] = updated

    if not handled:
        return _no_fix(text)

    rationale = []
    if handled & {"curlUseFlagF", "curlUseFlagL", "curlUseHttpsUrl"}:
        rationale.append("Hardened curl: added missing -f/-L flags and/or upgraded http:// to https://.")
    if "wgetUseHttpsUrl" in handled:
        rationale.append("Upgraded wget URL from http:// to https://.")

    return SubRepairResult(text="".join(segments), handled_rule_ids=handled, rationale=rationale)


# ---------------------------------------------------------------------------
# Fix checksum/signature verification
# ---------------------------------------------------------------------------

# `sha256sum -c` parses its input as "<hash>  <filename>" (two spaces, or one space
# plus a leading "*" for binary mode) -- a single space between hash and filename is
# misparsed as part of the filename. The lookahead excludes binary-mode "*" and text
# that already has a second whitespace character so an already-correct line is left untouched.
_SHA256_ONE_SPACE_RE = re.compile(r"([a-fA-F0-9]{32,128}) (?![\s*])(\S+)")
_GPG_VERIFY_ASC_RE = re.compile(r"gpg\s+--verify\s+(\S+\.asc)")


def repair_fix_checksum_signature(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    handled: set[str] = set()
    rationale: list[str] = []

    if "sha256sumEchoOneSpaces" in rule_ids and _SHA256_ONE_SPACE_RE.search(text):
        text = _SHA256_ONE_SPACE_RE.sub(r"\1  \2", text)
        handled.add("sha256sumEchoOneSpaces")
        rationale.append("Expanded the single space between hash and filename to the two spaces `sha256sum -c` expects.")

    if "gpgVerifyAscRmAsc" in rule_ids:
        match = _GPG_VERIFY_ASC_RE.search(text)
        if match and "rm" not in text[match.end() :]:
            sig_file = match.group(1)
            text = f"{text.rstrip()} && rm -f {sig_file}"
            handled.add("gpgVerifyAscRmAsc")
            rationale.append(f"Appended removal of {sig_file} after successful verification.")

    return SubRepairResult(text=text, handled_rule_ids=handled, rationale=rationale)


# ---------------------------------------------------------------------------
# ARG with no default -- no automated fix
# ---------------------------------------------------------------------------


def repair_arg_no_default(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    # The build's behavior depends on an externally supplied --build-arg value that
    # this engine has no way to infer safely, so this always defers to a TODO comment.
    return _no_fix(text)


# ---------------------------------------------------------------------------
# Pin git clone to a tag
# ---------------------------------------------------------------------------

_GIT_CLONE_GITHUB_RE = re.compile(r"git\s+clone\s+(?:\S+\s+)*?https://github\.com/([^/\s]+)/([^/\s.]+?)(?:\.git)?\b")
_GIT_CHECKOUT_RE = re.compile(r"\bgit\s+checkout\b")


def repair_pin_git_clone(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    if "git_clone_no_pin" not in rule_ids or _GIT_CHECKOUT_RE.search(text):
        return _no_fix(text)

    match = _GIT_CLONE_GITHUB_RE.search(text)
    if not match:
        return _no_fix(text)  # Not a github.com URL; no generic tag API to query.

    owner, repo = match.groups()
    tag = resolvers.github_latest_tag(owner, repo)
    if not tag:
        return _no_fix(text)

    # Appended at the end and re-entering the clone directory, rather than inserted
    # immediately after the clone: simpler and correct as long as no later command in
    # the same RUN changes into a different directory.
    return SubRepairResult(
        text=f"{text.rstrip()} && cd {repo} && git checkout {tag}",
        handled_rule_ids={"git_clone_no_pin"},
        rationale=[f"Pinned {owner}/{repo} to tag '{tag}' (the repository's latest tag via the GitHub API)."],
    )


# ---------------------------------------------------------------------------
# Replace ADD of a URL or local file
# ---------------------------------------------------------------------------

_ADD_RE = re.compile(r"^ADD\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)


def repair_replace_add(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    match = _ADD_RE.match(text.strip())
    if not match:
        return _no_fix(text)

    src, dest = match.groups()
    is_remote = src.lower().startswith(("http://", "https://"))

    if is_remote and "add_remote_url" in rule_ids:
        return SubRepairResult(
            text=f"RUN curl -fsSL {src} -o {dest} && sha256sum {dest}",
            handled_rule_ids=rule_ids & {"add_remote_url", "DL3020"},
            rationale=[
                "Replaced ADD of a remote URL with an explicit RUN curl download; the "
                "printed sha256sum makes the fetched content's hash visible in build logs."
            ],
        )

    if not is_remote and "DL3020" in rule_ids:
        return SubRepairResult(
            text=f"COPY {src} {dest}",
            handled_rule_ids={"DL3020"},
            rationale=["Replaced ADD of a local file with COPY, which has simpler, more predictable semantics."],
        )

    return _no_fix(text)


# ---------------------------------------------------------------------------
# Pin an unversioned package install
# ---------------------------------------------------------------------------

_BARE_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _unversioned_packages(tokens: list[str]) -> list[str]:
    """Return bare package-name tokens from `tokens` (flags already filtered out by the
    caller), or an empty list if any token is a path, URL, extras spec, or already-
    versioned package -- those are left for a TODO rather than guessed at."""
    packages = []
    for token in tokens:
        if not _BARE_PACKAGE_RE.match(token):
            return []
        packages.append(token)
    return packages


_NPM_INSTALL_ANCHOR = re.compile(r"npm\s+install\b")
_GEM_INSTALL_ANCHOR = re.compile(r"gem\s+install\b")


def _pin_single_package(text: str, anchor: re.Pattern[str], separator_fmt: str, resolve: Callable[[str], str | None]) -> tuple[str, str, str] | None:
    """Find exactly one unversioned package after `anchor` in `text`, resolve its
    current version, and rewrite it in place. Returns (new_text, package, version), or
    None if there isn't exactly one resolvable bare package name to pin.

    Only the segment up to the next `&&`/`||`/`|`/`;` is considered the package list --
    an earlier sub-repair in the pipeline (e.g. appending `&& npm cache clean --force`
    to the same instruction) would otherwise be tokenized right along with it and make
    an otherwise-unambiguous single package look like multiple, unpinnable ones.
    """
    match = anchor.search(text)
    if not match:
        return None
    tail = text[match.end() :]
    segments = _SEGMENT_SPLIT_RE.split(tail, maxsplit=1)
    command_tail, rest = segments[0], "".join(segments[1:])

    tokens = [t for t in command_tail.split() if not t.startswith("-")]
    packages = _unversioned_packages(tokens)
    if len(packages) != 1:
        return None  # No package, or more than one -- too ambiguous to pin automatically.

    package = packages[0]
    version = resolve(package)
    if not version:
        return None

    pinned = separator_fmt.format(name=package, version=version)
    new_command_tail = command_tail.replace(package, pinned, 1)
    return text[: match.end()] + new_command_tail + rest, package, version


def repair_pin_package_version(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    if "DL3013" in rule_ids:
        result = _pin_single_package(text, _PIP_INSTALL_ANCHOR, "{name}=={version}", resolvers.pypi_latest_version)
        if result:
            new_text, package, version = result
            return SubRepairResult(
                text=new_text,
                handled_rule_ids={"DL3013"},
                rationale=[f"Pinned {package} to =={version} (current release on PyPI)."],
            )

    if "DL3016" in rule_ids:
        result = _pin_single_package(text, _NPM_INSTALL_ANCHOR, "{name}@{version}", resolvers.npm_latest_version)
        if result:
            new_text, package, version = result
            return SubRepairResult(
                text=new_text,
                handled_rule_ids={"DL3016"},
                rationale=[f"Pinned {package} to @{version} (current version on the npm registry)."],
            )

    if "DL3028" in rule_ids:
        result = _pin_single_package(text, _GEM_INSTALL_ANCHOR, "{name} -v {version}", resolvers.rubygems_latest_version)
        if result:
            new_text, package, version = result
            return SubRepairResult(
                text=new_text,
                handled_rule_ids={"DL3028"},
                rationale=[f"Pinned {package} to version {version} (current release on RubyGems, via gem's -v flag)."],
            )

    # DL3008 (apt-get), DL3018 (apk), DL3033 (yum), DL3037 (zypper), DL3041 (dnf): the
    # "currently available version" depends on the base image's specific OS release and
    # configured package mirrors, which cannot be determined from Dockerfile source
    # alone, so these always defer to a TODO comment.
    return _no_fix(text)


# ---------------------------------------------------------------------------
# Add a checksum after a file download
# ---------------------------------------------------------------------------


def repair_add_download_checksum(text: str, rule_ids: set[str], resolvers: Resolvers) -> SubRepairResult:
    if "download_no_checksum" not in rule_ids:
        return _no_fix(text)

    segments = _split_commands(text)
    for i, segment in enumerate(segments):
        stripped = segment.strip()
        first_word = stripped.split(" ", 1)[0] if stripped else ""
        if first_word not in ("curl", "wget"):
            continue

        url_match = _URL_RE.search(stripped)
        if not url_match:
            continue
        url = url_match.group(0)

        dest_match = re.search(r"(?:-o|-O|--output)\s+(\S+)", stripped)
        dest = dest_match.group(1) if dest_match else url.rsplit("/", 1)[-1]

        checksum = resolvers.fetch_sha256(url)
        if not checksum:
            return _no_fix(text)

        # Written to a file and checked with a second command rather than piped
        # (echo ... | sha256sum -c) so this fix does not itself introduce a new shell
        # pipe that would need its own pipefail handling.
        checksum_file = f"{dest}.sha256"
        segments[i] = f"{segment.rstrip()} && echo '{checksum}  {dest}' > {checksum_file} " f"&& sha256sum -c {checksum_file}"
        return SubRepairResult(
            text="".join(segments),
            handled_rule_ids={"download_no_checksum"},
            rationale=[f"Downloaded {url} at repair time and pinned its sha256 ({checksum[:12]}...) for verification on every build."],
        )

    return _no_fix(text)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SUB_REPAIRS: list[tuple[frozenset[str], RepairFunc]] = [
    (frozenset({"DL3007", "implicit_latest", "explicit_latest"}), repair_pin_base_image_digest),
    (frozenset({"DL4006", "missing_pipefail"}), repair_add_pipefail),
    (frozenset({"curl_pipe_shell"}), repair_restructure_curl_pipe_shell),
    (frozenset({"DL3009", "aptGetInstallThenRemoveAptLists"}), repair_apt_get_cleanup),
    (
        frozenset({"DL3019", "apkAddUseNoCache", "aptGetInstallUseNoRec", "DL3042", "pipUseNoCacheDir"}),
        repair_add_no_cache_flag,
    ),
    (frozenset({"aptGetInstallUseY", "yumInstallForceYes"}), repair_require_yes_flag),
    (frozenset({"aptGetUpdatePrecedesInstall"}), repair_fuse_apt_update),
    (frozenset({"ruleMoreThanOneInstall"}), repair_merge_duplicate_installs),
    (
        frozenset(
            {
                "yumInstallRmVarCacheYum",
                "npmCacheCleanAfterInstall",
                "npmCacheCleanUseForce",
                "yarnCacheCleanAfterInstall",
            }
        ),
        repair_add_cache_cleanup,
    ),
    (
        frozenset({"curlUseFlagF", "curlUseFlagL", "curlUseHttpsUrl", "wgetUseHttpsUrl"}),
        repair_harden_curl_wget,
    ),
    (frozenset({"sha256sumEchoOneSpaces", "gpgVerifyAscRmAsc"}), repair_fix_checksum_signature),
    (frozenset({"arg_no_default"}), repair_arg_no_default),
    (frozenset({"git_clone_no_pin"}), repair_pin_git_clone),
    (frozenset({"add_remote_url", "DL3020"}), repair_replace_add),
    (
        frozenset({"DL3008", "DL3013", "DL3016", "DL3018", "DL3028", "DL3033", "DL3037", "DL3041"}),
        repair_pin_package_version,
    ),
    (frozenset({"download_no_checksum"}), repair_add_download_checksum),
]
