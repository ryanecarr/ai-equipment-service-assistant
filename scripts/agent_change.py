"""Decides whether a release evaluates its agent version once or three times.

    gh release list --limit 100 --json tagName,publishedAt,isDraft > releases.json
    python -m scripts.agent_change --releases releases.json --output agent-change.json

Inputs:  this repository's GitHub Releases as JSON, and the git checkout it runs in.
Output:  a JSON file: extra_evaluations (true or false), release (the tag compared with,
         or null), changed (the files that differ), reason and summary.
Exit:    0 with an answer, 1 if the comparison could not be made.

The agent definition is every agents/<name>/agent.yaml and instructions.md. It is compared
with the newest GitHub Release, which is the last commit a release deployed. It is not
compared with the previous push: an instruction change that merged but was never deployed
keeps counting as a change in every later release, however it was started. Without any
GitHub Release there is nothing to compare with, and the answer is three.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.agent_definition import REPO_ROOT

# The tags the Release workflow makes: release-<run number>.<attempt>.
_RELEASE_TAG = re.compile(r"^release-(\d+)\.(\d+)$")
_DEFINITION_FILE = re.compile(r"^agents/[^/]+/(agent\.yaml|instructions\.md)$")


def newest_release(releases: list[dict[str, Any]]) -> Optional[str]:
    """The tag of the release that was published last, or None if there is none."""

    def order(release: dict[str, Any]) -> tuple:
        run_number, attempt = _RELEASE_TAG.match(release["tagName"]).groups()
        return datetime.fromisoformat(release["publishedAt"]), int(run_number), int(attempt)

    published = [r for r in releases if _RELEASE_TAG.match(r["tagName"]) and not r.get("isDraft")]
    return max(published, key=order)["tagName"] if published else None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def fetch_tag(repo: Path, tag: str) -> str:
    """Fetches the released commit by its tag and returns its ID. A release checkout holds
    one commit, so the released one is usually missing. Only a checkout that is already
    shallow is fetched with a depth: in a full clone a depth would cut its history short.
    No local tag or branch is written."""
    depth = ["--depth", "1"] if _git(repo, "rev-parse", "--is-shallow-repository").strip() == "true" else []
    _git(repo, "fetch", "--no-tags", *depth, "origin", f"refs/tags/{tag}")
    return _git(repo, "rev-parse", "FETCH_HEAD^{commit}").strip()


def definition_files(repo: Path, ref: str) -> dict[str, str]:
    """The agent definition at a commit, as {path: git blob ID}."""
    files = {}
    for entry in _git(repo, "ls-tree", "-r", "-z", ref, "--", "agents").split("\0"):
        if entry:
            details, path = entry.split("\t", 1)
            if _DEFINITION_FILE.match(path):
                files[path] = details.split()[2]
    return files


def changed_definition_files(repo: Path, base: str, head: str = "HEAD") -> list[str]:
    """Definition files added, removed or with different content between two commits."""
    before, after = definition_files(repo, base), definition_files(repo, head)
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def decide(
    repo: Path,
    releases: list[dict[str, Any]],
    requested: bool = False,
    fetch: Callable[[Path, str], str] = fetch_tag,
) -> dict[str, Any]:
    """`fetch(repo, tag)` makes the released commit available and returns a name for it."""
    tag = newest_release(releases)
    if tag is None:
        changed: list[str] = []
        reason = "there is no GitHub Release to compare with"
    else:
        changed = changed_definition_files(repo, fetch(repo, tag))
        if changed:
            reason = f"{', '.join(changed)} changed since {tag}, the last release"
        elif requested:
            reason = f"unchanged since {tag}, the last release, but this run asked for the extra evaluations"
        else:
            reason = f"unchanged since {tag}, the last release"
    extra = tag is None or bool(changed) or requested
    count = "three times, five minutes apart, and reviews the runs" if extra else "once"
    return {
        "extra_evaluations": extra,
        "release": tag,
        "changed": changed,
        "reason": reason,
        "summary": f"Agent definition: {reason}. This release evaluates the agent version {count}.",
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--releases", type=Path, required=True, help="output of gh release list --json tagName,publishedAt,isDraft")
    parser.add_argument("--output", type=Path, required=True, help="where to write the answer, as JSON")
    parser.add_argument("--requested", action="store_true", help="run the extra evaluations even if nothing changed")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="the git checkout to compare (default: this repository)")
    args = parser.parse_args(argv)

    try:
        answer = decide(args.repo, json.loads(args.releases.read_text(encoding="utf-8")), args.requested)
    except subprocess.CalledProcessError as exc:
        print(f"error: {' '.join(exc.cmd)} failed: {exc.stderr.strip()}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc!r}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
    print(answer["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
