#!/usr/bin/env python3
"""Run the validation component against the corpus in Dockerfiles/manifest.json.

For each Dockerfile listed in the manifest, this:
  1. Re-clones the Dockerfile's source repository (shallow, once per repo, reused
     across all Dockerfiles from that repo) so the build context matches what the
     original repository actually provides -- the flattened Dockerfiles/ directory
     has no COPY/ADD sources, so validating directly against it would fail almost
     every build for reasons unrelated to flakiness.
  2. Runs flakiscan's refactoring engine on the original Dockerfile to produce a
     patched version.
  3. Builds both the original and the patched Dockerfile with flakiscan_validate and
     records the outcome (preserved/regressed/improved/pre_existing_failure) plus the
     image size delta.

Results are written incrementally to --output as a JSON array, so a long run can be
interrupted without losing progress already made. After every Dockerfile, `docker
system prune -af` reclaims BuildKit's cache and any dangling images left behind by that
Dockerfile's builds -- without it, disk usage climbs across the whole run regardless of
how small this script's own output is, and eventually exhausts a constrained disk (a
GitHub Actions runner's, in particular).

Requires Docker running locally. Neither flakiscan nor flakiscan_validate need to be
installed -- this script adds both package roots to sys.path itself.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for path in (REPO_ROOT, REPO_ROOT / "validation"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from flakiscan.refactoring.engine import repair_dockerfile
from flakiscan_validate import builder
from flakiscan_validate.validator import validate
from flakiscan_validate.builder import DockerDaemonUnavailableError, DockerUnavailableError

MAX_LOG_CHARS = 4000


def prune_docker() -> None:
    """Reclaim disk used by Docker's build cache and dangling images/containers.

    `builder.build()` already removes each build's own tagged image after inspecting
    its size, but that only drops the final tag -- BuildKit's layer cache and any
    dangling intermediate-stage images from multi-stage builds are not freed by that
    and accumulate across every build in the run. Over a long run (or a repo with many
    large, multi-stage Dockerfiles) that accumulation -- not this script's own small
    JSON/text output -- is what exhausts disk. Failures here are logged and swallowed
    rather than raised, since a failed prune should not abort an otherwise-successful
    validation run.
    """
    proc = subprocess.run(["docker", "system", "prune", "-af"], capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"warning: docker system prune failed: {proc.stderr.strip()}", file=sys.stderr)


def clone_repo(url: str, dest: Path) -> str | None:
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    rev = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    return rev.stdout.strip()


def truncate_log(log: str) -> str:
    if len(log) <= MAX_LOG_CHARS:
        return log
    return "...(truncated)...\n" + log[-MAX_LOG_CHARS:]


def process_entry(entry: dict, repo_dir: Path, commit: str, timeout: float, files_dir: Path | None) -> dict:
    base = {
        "owner": entry["owner"],
        "repo": entry["repo"],
        "url": entry["url"],
        "commit": commit,
        "source_path": entry["source_path"],
    }
    original_path = repo_dir / entry["source_path"]
    if not original_path.is_file():
        return {**base, "error": "source file no longer present at this path in the repo"}

    saved_files_dir = files_dir / entry["dataset_path"] if files_dir else None
    if saved_files_dir:
        saved_files_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original_path, saved_files_dir / "original.Dockerfile")

    try:
        report = repair_dockerfile(str(original_path))
    except Exception as exc:  # detection tooling (Hadolint/Parfum) can crash on unusual syntax
        return {**base, "error": f"repair_dockerfile failed: {exc!r}"}

    if saved_files_dir:
        (saved_files_dir / "patched.Dockerfile").write_text(report.patched_text)

    patched_path = original_path.with_name(original_path.name + ".flakiscan-patched")
    try:
        patched_path.write_text(report.patched_text)
        result = validate(str(original_path), str(patched_path), timeout_seconds=timeout)
    except (DockerUnavailableError, DockerDaemonUnavailableError):
        raise
    except Exception as exc:
        return {**base, "error": f"validate failed: {exc!r}"}
    finally:
        patched_path.unlink(missing_ok=True)

    applied = sorted({rid for action in report.actions for rid in action.applied_rule_ids})
    fallback = sorted({rid for action in report.actions for rid in action.fallback_rule_ids})

    return {
        **base,
        "outcome": result.outcome.value,
        "image_size_delta_bytes": result.image_size_delta_bytes,
        "applied_rule_ids": applied,
        "fallback_rule_ids": fallback,
        "original": {
            "success": result.original.success,
            "duration_seconds": round(result.original.duration_seconds, 2),
            "timed_out": result.original.timed_out,
            "image_size_bytes": result.original.image_size_bytes,
            "log": truncate_log(result.original.log),
        },
        "modified": {
            "success": result.modified.success,
            "duration_seconds": round(result.modified.duration_seconds, 2),
            "timed_out": result.modified.timed_out,
            "image_size_bytes": result.modified.image_size_bytes,
            "log": truncate_log(result.modified.log),
        },
    }


class RepoPool:
    """Clones each unique repository once, up front, and hands out its directory to
    every Dockerfile that belongs to it.

    A repo's clone is removed as soon as the last Dockerfile referencing it has been
    processed, so disk usage stays bounded by the concurrently in-flight repos rather
    than by the whole corpus -- this matters for repos like fluent/fluentd-docker-image
    (124 Dockerfiles) where cloning once and fanning out the individual builds is what
    keeps the whole run inside a practical time and disk budget.
    """

    def __init__(self, manifest: list[dict], workers: int):
        self._dirs: dict[tuple[str, str], Path] = {}
        self._commits: dict[tuple[str, str], str | None] = {}
        self._remaining: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._tmp_root = Path(tempfile.mkdtemp(prefix="flakiscan-validate-"))

        urls: dict[tuple[str, str], str] = {}
        for entry in manifest:
            key = (entry["owner"], entry["repo"])
            urls[key] = entry["url"]
            self._remaining[key] = self._remaining.get(key, 0) + 1

        def clone_one(key: tuple[str, str]) -> None:
            owner, repo = key
            dest = self._tmp_root / f"{owner}__{repo}"
            self._dirs[key] = dest
            self._commits[key] = clone_repo(urls[key], dest)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(clone_one, urls.keys()))

    def get(self, owner: str, repo: str) -> tuple[Path, str | None]:
        key = (owner, repo)
        return self._dirs[key], self._commits[key]

    def release(self, owner: str, repo: str) -> None:
        key = (owner, repo)
        with self._lock:
            self._remaining[key] -= 1
            done = self._remaining[key] == 0
        if done:
            shutil.rmtree(self._dirs[key], ignore_errors=True)

    def close(self) -> None:
        shutil.rmtree(self._tmp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).parent / "Dockerfiles" / "manifest.json",
        help="Manifest produced by fetch_dockerfiles.py (default: dataset/Dockerfiles/manifest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "validation_results.json",
        help="Where to write the JSON results (default: dataset/validation_results.json)",
    )
    parser.add_argument("--timeout", type=float, default=300, help="Per-build timeout in seconds (default: 300)")
    parser.add_argument("--only", help="Comma-separated owner/repo pairs to run, for testing a subset (default: all)")
    parser.add_argument(
        "--shard",
        help="Process only shard N of M, as 'N/M' (1-indexed, e.g. '2/4'), for splitting a large repo's "
        "Dockerfiles across multiple CI jobs. Applied after --only; deterministic across runs since the "
        "matching manifest entries are sorted first, then striped so adjacent files land in different "
        "shards rather than clustering the slowest ones together.",
    )
    parser.add_argument("--limit", type=int, help="Stop after this many Dockerfiles total (default: no limit)")
    parser.add_argument("--workers", type=int, default=4, help="Number of Dockerfiles to build concurrently (default: 4)")
    parser.add_argument(
        "--files-dir",
        type=Path,
        default=Path(__file__).parent / "validation_files",
        help="Where to save each Dockerfile's original and patched text, one subdirectory per manifest entry "
        "(default: dataset/validation_files)",
    )
    parser.add_argument("--no-save-files", action="store_true", help="Don't save original/patched Dockerfile text")
    args = parser.parse_args()
    files_dir = None if args.no_save_files else args.files_dir

    if not builder.is_available():
        print("docker binary not found on PATH; install Docker to run validation", file=sys.stderr)
        return 2

    manifest = json.loads(args.manifest.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        manifest = [e for e in manifest if f"{e['owner']}/{e['repo']}" in wanted]
    if args.shard:
        shard_index, shard_count = (int(x) for x in args.shard.split("/"))
        if not (1 <= shard_index <= shard_count):
            parser.error(f"--shard {args.shard!r}: N must be between 1 and M")
        manifest.sort(key=lambda e: (e["owner"], e["repo"], e["source_path"]))
        manifest = manifest[shard_index - 1 :: shard_count]
    if args.limit:
        manifest = manifest[: args.limit]

    if not manifest:
        print("No manifest entries matched; nothing to do.", file=sys.stderr)
        return 0

    results: list[dict] = []
    lock = threading.Lock()

    def flush() -> None:
        tmp_output = args.output.with_suffix(".json.tmp")
        tmp_output.write_text(json.dumps(results, indent=2) + "\n")
        tmp_output.replace(args.output)

    total = len(manifest)
    done = 0
    fatal_error: Exception | None = None

    print(f"Cloning {len({(e['owner'], e['repo']) for e in manifest})} repositories...", file=sys.stderr)
    pool = RepoPool(manifest, args.workers)

    def run_one(entry: dict) -> dict:
        owner, repo = entry["owner"], entry["repo"]
        repo_dir, commit = pool.get(owner, repo)
        try:
            if commit is None:
                return {
                    "owner": owner,
                    "repo": repo,
                    "url": entry["url"],
                    "commit": None,
                    "source_path": entry["source_path"],
                    "error": "git clone failed",
                }
            return process_entry(entry, repo_dir, commit, args.timeout, files_dir)
        finally:
            pool.release(owner, repo)
            prune_docker()

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(run_one, entry): entry for entry in manifest}
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    result = future.result()
                except (DockerUnavailableError, DockerDaemonUnavailableError) as exc:
                    fatal_error = exc
                    for f in futures:
                        f.cancel()
                    break
                with lock:
                    results.append(result)
                    done += 1
                    flush()
                print(f"[{done}/{total}] {entry['owner']}/{entry['repo']}/{entry['source_path']}", file=sys.stderr)
    finally:
        pool.close()

    if fatal_error is not None:
        print(f"\nAborted: Docker became unavailable mid-run: {fatal_error}", file=sys.stderr)
        print(f"Partial results ({len(results)} entries) written to {args.output}", file=sys.stderr)
        return 2

    errors = [r for r in results if "error" in r]
    outcomes: dict[str, int] = {}
    for r in results:
        if "outcome" in r:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1

    print(f"\n{len(results)} Dockerfiles processed, {len(errors)} error(s).")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome}: {count}")
    print(f"Results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
