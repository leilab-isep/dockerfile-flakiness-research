#!/usr/bin/env python3
"""Retrieve Dockerfiles from the repositories listed in repositories.json.

Each repository is shallow-cloned to a temporary directory, every Dockerfile
it contains is copied into Dockerfiles/<owner>/<repo>/<original path>, and the
clone is discarded. A manifest recording the source repository, commit, and
original path of every collected file is written alongside the output so
each Dockerfile stays traceable back to where it came from.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

DOCKERFILE_NAME_RE = re.compile(r"^Dockerfile(\.[A-Za-z0-9_.\-]+)?$")
DEFAULT_EXCLUDE_TOKENS = ("dockerignore", "template")


@dataclass
class ManifestEntry:
    owner: str
    repo: str
    url: str
    commit: str
    source_path: str
    dataset_path: str
    size_bytes: int


def find_dockerfiles(root: Path, exclude_tokens: tuple[str, ...]) -> list[Path]:
    matches = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if not DOCKERFILE_NAME_RE.match(path.name):
            continue
        relative = path.relative_to(root).as_posix().lower()
        if any(token in relative for token in exclude_tokens):
            continue
        matches.append(path)
    return sorted(matches)


def clone_repo(url: str, dest: Path) -> str:
    subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def fetch_repo(entry: dict, output_dir: Path, exclude_tokens: tuple[str, ...]) -> tuple[list[ManifestEntry], str | None]:
    owner, repo, url = entry["owner"], entry["repo"], entry["url"]
    repo_prefix = f"{owner}__{repo}__"
    for stale in output_dir.glob(f"{repo_prefix}*"):
        stale.unlink()

    with tempfile.TemporaryDirectory(prefix=f"{owner}__{repo}_") as tmp:
        clone_dir = Path(tmp)
        try:
            commit = clone_repo(url, clone_dir)
        except subprocess.CalledProcessError as exc:
            return [], exc.stderr.strip().splitlines()[-1] if exc.stderr else str(exc)

        dockerfiles = find_dockerfiles(clone_dir, exclude_tokens)
        manifest_entries = []
        for source in dockerfiles:
            relative = source.relative_to(clone_dir)
            destination = output_dir / (repo_prefix + relative.as_posix().replace("/", "__"))
            shutil.copy2(source, destination)
            manifest_entries.append(
                ManifestEntry(
                    owner=owner,
                    repo=repo,
                    url=url,
                    commit=commit,
                    source_path=relative.as_posix(),
                    dataset_path=destination.name,
                    size_bytes=destination.stat().st_size,
                )
            )
        return manifest_entries, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repos-file",
        type=Path,
        default=Path(__file__).parent / "repositories.json",
        help="JSON file listing repositories to fetch (default: dataset/repositories.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "Dockerfiles",
        help="Directory to write collected Dockerfiles into (default: dataset/Dockerfiles)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated owner/repo pairs to fetch, for testing a subset (default: all)",
    )
    parser.add_argument(
        "--include-templates",
        action="store_true",
        help="Do not skip files whose path contains 'template' (off by default: these are usually not valid, buildable Dockerfiles)",
    )
    args = parser.parse_args()

    repositories = json.loads(args.repos_file.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        repositories = [r for r in repositories if f"{r['owner']}/{r['repo']}" in wanted]

    exclude_tokens = ("dockerignore",) if args.include_templates else DEFAULT_EXCLUDE_TOKENS

    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[ManifestEntry] = []
    failures: dict[str, str] = {}
    for entry in repositories:
        label = f"{entry['owner']}/{entry['repo']}"
        print(f"Fetching {label} ...", file=sys.stderr)
        entries, error = fetch_repo(entry, args.output_dir, exclude_tokens)
        if error:
            print(f"  FAILED: {error}", file=sys.stderr)
            failures[label] = error
            continue
        print(f"  {len(entries)} Dockerfile(s)", file=sys.stderr)
        manifest.extend(entries)

    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps([asdict(e) for e in manifest], indent=2) + "\n")

    print(f"\nCollected {len(manifest)} Dockerfiles from {len(repositories) - len(failures)}/{len(repositories)} repositories.")
    print(f"Manifest written to {manifest_path}")
    if failures:
        print(f"\n{len(failures)} repositories failed:", file=sys.stderr)
        for label, error in failures.items():
            print(f"  {label}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
