"""Writes agent_reference.json, the file that tells the application which Foundry agent
version to call.

    python -m scripts.publish_agent --reference-only --version 5

Inputs:  the agent folder (for the agent's name), a version number, the Foundry project
         endpoint (PROJECT_ENDPOINT, or local.settings.json) and the source commit.
Output:  agent_reference.json in the repository root, or the path given with --output.

--reference-only points at a version that already exists and creates nothing in Foundry.
Use it for local runs. Creating a new version is added with the release workflow.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.foundry.agent_reference import REFERENCE_PATH, AgentReference
from scripts.agent_definition import AGENTS_DIR, REPO_ROOT, AgentDefinitionError, load_agent_definition


def _default_agent_folder() -> Path:
    folders = sorted(path for path in AGENTS_DIR.iterdir() if path.is_dir())
    if len(folders) != 1:
        raise SystemExit(f"Expected one folder in {AGENTS_DIR}, found {len(folders)}. Pass --agent <folder>.")
    return folders[0]


def _default_project_endpoint() -> Optional[str]:
    if os.environ.get("PROJECT_ENDPOINT"):
        return os.environ["PROJECT_ENDPOINT"]
    local_settings = REPO_ROOT / "local.settings.json"
    if local_settings.is_file():
        return json.loads(local_settings.read_text(encoding="utf-8")).get("Values", {}).get("PROJECT_ENDPOINT")
    return None


def _current_commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def write_reference(
    output: Path, project_endpoint: str, agent_name: str, agent_version: str, source_commit: str
) -> AgentReference:
    reference = AgentReference(
        project_endpoint=project_endpoint,
        agent_name=agent_name,
        agent_version=agent_version,
        source_commit=source_commit,
    )
    output.write_text(json.dumps(reference.model_dump(), indent=2) + "\n", encoding="utf-8")
    return reference


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference-only", action="store_true", help="point at an existing version; create nothing")
    parser.add_argument("--version", help="the existing agent version to reference, for example 5")
    parser.add_argument("--agent", type=Path, help="agent folder (default: the only folder in agents/)")
    parser.add_argument("--project-endpoint", help="default: PROJECT_ENDPOINT, then local.settings.json")
    parser.add_argument("--commit", help="source commit to record (default: the current git commit)")
    parser.add_argument("--output", type=Path, default=REFERENCE_PATH)
    args = parser.parse_args(argv)

    if not args.reference_only or not args.version:
        parser.error("pass --reference-only --version <n>. Creating a version is done by the release workflow.")

    project_endpoint = args.project_endpoint or _default_project_endpoint()
    if not project_endpoint:
        parser.error("no project endpoint: set PROJECT_ENDPOINT, fill in local.settings.json, or pass --project-endpoint")

    try:
        definition = load_agent_definition(args.agent or _default_agent_folder())
        reference = write_reference(
            args.output, project_endpoint, definition.name, args.version, args.commit or _current_commit()
        )
    except (AgentDefinitionError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output}: agent {reference.agent_name} version {reference.agent_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
