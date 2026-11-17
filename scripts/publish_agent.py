"""Writes agent_reference.json, the file that tells the application which Foundry agent
version to call.

    python -m scripts.publish_agent --reference-only --version 5    # local runs
    python -m scripts.publish_agent --create                        # the release workflow

Inputs:  the agent folder, the Foundry project endpoint (PROJECT_ENDPOINT, or
         local.settings.json) and the source commit. --reference-only also takes a version.
Output:  agent_reference.json in the repository root, or the path given with --output.

--reference-only points at a version that already exists and creates nothing in Foundry.

--create makes a new, immutable agent version from agent.yaml and instructions.md and
points the reference at it. It runs in the release workflow, never by hand. Creating a
version does not change what the deployed application calls: that only changes when a
package holding the new reference is deployed.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.identity import DefaultAzureCredential
from pydantic import ValidationError

from app.foundry.agent_reference import REFERENCE_PATH, AgentReference
from scripts.agent_definition import (
    AGENTS_DIR,
    REPO_ROOT,
    AgentDefinition,
    AgentDefinitionError,
    load_agent_definition,
)


def default_agent_folder() -> Path:
    folders = sorted(path for path in AGENTS_DIR.iterdir() if path.is_dir())
    if len(folders) != 1:
        raise SystemExit(f"Expected one folder in {AGENTS_DIR}, found {len(folders)}. Pass --agent <folder>.")
    return folders[0]


def default_project_endpoint() -> Optional[str]:
    if os.environ.get("PROJECT_ENDPOINT"):
        return os.environ["PROJECT_ENDPOINT"]
    local_settings = REPO_ROOT / "local.settings.json"
    if local_settings.is_file():
        return json.loads(local_settings.read_text(encoding="utf-8")).get("Values", {}).get("PROJECT_ENDPOINT")
    return None


def current_commit() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def create_version(project, definition: AgentDefinition, source_commit: str) -> str:
    """Creates one agent version in Foundry from the definition and returns its number.
    Foundry numbers versions itself; every call makes a new one."""
    created = project.agents.create_version(
        agent_name=definition.name,
        definition=PromptAgentDefinition(
            model=definition.model,
            instructions=definition.instructions,
            reasoning={"effort": definition.reasoning_effort},
            tools=definition.tools,
        ),
        # Shown beside the version in Foundry, so a version can be traced back to its commit.
        metadata={"source_commit": source_commit},
    )
    return str(created.version)


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--reference-only", action="store_true", help="point at an existing version; create nothing")
    mode.add_argument("--create", action="store_true", help="create a new version in Foundry (release workflow only)")
    parser.add_argument("--version", help="with --reference-only: the existing version, for example 5")
    parser.add_argument("--agent", type=Path, help="agent folder (default: the only folder in agents/)")
    parser.add_argument("--project-endpoint", help="default: PROJECT_ENDPOINT, then local.settings.json")
    parser.add_argument("--commit", help="source commit to record (default: the current git commit)")
    parser.add_argument("--output", type=Path, default=REFERENCE_PATH)
    args = parser.parse_args(argv)

    if args.reference_only and not args.version:
        parser.error("--reference-only needs --version <n>")
    if args.create and args.version:
        parser.error("--create takes no --version: Foundry numbers the new version")

    project_endpoint = args.project_endpoint or default_project_endpoint()
    if not project_endpoint:
        parser.error("no project endpoint: set PROJECT_ENDPOINT, fill in local.settings.json, or pass --project-endpoint")

    source_commit = args.commit or current_commit()
    try:
        definition = load_agent_definition(args.agent or default_agent_folder())
        version = args.version
        if args.create:
            project = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
            version = create_version(project, definition, source_commit)
            print(f"Created agent {definition.name} version {version} from commit {source_commit}")
        reference = write_reference(args.output, project_endpoint, definition.name, version, source_commit)
    except (AgentDefinitionError, ValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output}: agent {reference.agent_name} version {reference.agent_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
