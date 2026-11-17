"""Builds the deployment package: one zip holding the application code, its installed
dependencies and agent_reference.json.

    pip install -r requirements.txt --target .python_packages/lib/site-packages
    python -m scripts.build_package --output package.zip

Inputs:  the repository checkout, with agent_reference.json in its root and the
         dependencies installed as above, on Linux with the Function App's Python version;
         and the Foundry project endpoint (PROJECT_ENDPOINT).
Output:  the zip. The same file is deployed, attached to the GitHub Release, and
         redeployed by a rollback, so it is never rebuilt.

It refuses to build without a valid agent reference for this project and this commit.
The application code is every tracked file that .funcignore does not exclude.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Optional

from app.errors import AgentReferenceError
from app.foundry.agent_reference import AgentReference, load_agent_reference

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPENDENCIES = Path(".python_packages") / "lib" / "site-packages"


class PackageError(Exception):
    pass


def _git(root: Path, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        raise PackageError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line]


def application_files(root: Path) -> list[str]:
    """Tracked files, minus the ones .funcignore excludes. Git reads .funcignore itself,
    so the patterns mean exactly what they mean in a .gitignore."""
    tracked = _git(root, "ls-files")
    excluded = set(_git(root, "ls-files", "--cached", "--ignored", "--exclude-from=.funcignore"))
    # .funcignore itself configures the build and is not part of the application.
    return sorted(path for path in tracked if path not in excluded and path != ".funcignore")


def check_reference(root: Path, project_endpoint: Optional[str]) -> AgentReference:
    """The reference must be valid, for this Foundry project, and made from this commit."""
    reference = load_agent_reference(root / "agent_reference.json", project_endpoint)
    (head,) = _git(root, "rev-parse", "HEAD")
    if reference.source_commit != head:
        raise PackageError(
            f"agent_reference.json was made from commit {reference.source_commit}, "
            f"but this checkout is {head}. The package must hold the reference made for its own commit."
        )
    return reference


def build_package(root: Path, output: Path, project_endpoint: Optional[str]) -> AgentReference:
    reference = check_reference(root, project_endpoint)

    dependencies = sorted(
        path.relative_to(root).as_posix() for path in (root / DEPENDENCIES).rglob("*") if path.is_file()
    )
    if not dependencies:
        raise PackageError(
            f"{DEPENDENCIES} is empty. Install the dependencies first: "
            f"pip install -r requirements.txt --target {DEPENDENCIES}"
        )

    files = application_files(root)
    for required in ("function_app.py", "host.json"):
        if required not in files:
            raise PackageError(f"{required} is not in the package. Check .funcignore.")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name in [*files, "agent_reference.json", *dependencies]:
            package.write(root / name, arcname=name)
    return reference


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output", type=Path, default=Path("package.zip"))
    parser.add_argument("--project-endpoint", default=os.environ.get("PROJECT_ENDPOINT"), help="default: PROJECT_ENDPOINT")
    args = parser.parse_args(argv)

    try:
        reference = build_package(REPO_ROOT, args.output, args.project_endpoint)
    except (PackageError, AgentReferenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"Built {args.output}: {args.output.stat().st_size:,} bytes, sha256 {digest}")
    print(f"It calls agent {reference.agent_name} version {reference.agent_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
