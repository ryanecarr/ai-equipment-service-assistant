"""Which Foundry agent version this build of the application calls.

agent_reference.json sits beside function_app.py inside the deployed package. It is
generated, never committed: the release writes it for the version it just tested, and
scripts/publish_agent.py writes one for local runs. There is no fallback. Without a valid
file the application refuses to call the agent, because a call without an explicit version
resolves to the agent's latest version, which may be an untested candidate.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import AgentReferenceError

logger = logging.getLogger(__name__)

REFERENCE_PATH = Path(__file__).resolve().parents[2] / "agent_reference.json"

_USER_MESSAGE = "The diagnostic assistant is not configured correctly. Please contact support."


class AgentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project_endpoint: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    # Foundry numbers versions 1, 2, 3... A word such as "latest" is rejected here.
    agent_version: str = Field(pattern=r"^[1-9][0-9]*$")
    source_commit: str = Field(min_length=1)


def load_agent_reference(path: Path, project_endpoint: Optional[str]) -> AgentReference:
    """Reads and validates the file, and checks it was built for this app's Foundry project.
    Version numbers are per project, so a reference from another project would name the
    wrong definition."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AgentReferenceError(
            f"{path} is missing. The release workflow writes it into the deployment package; for a "
            "local run, create it with: python -m scripts.publish_agent --reference-only --version <n>",
            user_message=_USER_MESSAGE,
        ) from exc
    except (OSError, ValueError) as exc:
        raise AgentReferenceError(f"{path} could not be read as JSON: {exc}", user_message=_USER_MESSAGE) from exc

    try:
        reference = AgentReference.model_validate(data)
    except ValidationError as exc:
        raise AgentReferenceError(f"{path} is not a valid agent reference: {exc}", user_message=_USER_MESSAGE) from exc

    if not project_endpoint:
        raise AgentReferenceError("PROJECT_ENDPOINT is not set.", user_message=_USER_MESSAGE)
    if reference.project_endpoint.rstrip("/") != project_endpoint.rstrip("/"):
        raise AgentReferenceError(
            f"{path} was built for Foundry project {reference.project_endpoint}, but this application is "
            f"configured for {project_endpoint}.",
            user_message=_USER_MESSAGE,
        )
    return reference


# Read once per worker process. A failed read is not cached, so a fixed file is picked up.
_reference: Optional[AgentReference] = None


def get_agent_reference() -> AgentReference:
    global _reference
    if _reference is None:
        _reference = load_agent_reference(REFERENCE_PATH, os.environ.get("PROJECT_ENDPOINT"))
        logger.info(
            "Using Foundry agent %s version %s, built from commit %s",
            _reference.agent_name,
            _reference.agent_version,
            _reference.source_commit,
        )
    return _reference
