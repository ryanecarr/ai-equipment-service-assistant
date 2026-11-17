"""Loads and validates an agent folder: agents/<name>/agent.yaml and instructions.md.

Input: the folder path. Output: an AgentDefinition, or an AgentDefinitionError that says
what is wrong. Makes no network calls.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


class AgentDefinitionError(ValueError):
    pass


class AgentDefinition(BaseModel):
    # Unknown keys are rejected so a misspelled setting fails here, not silently in Foundry.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    owner: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: Literal["minimal", "low", "medium", "high"]
    tools: list[dict[str, Any]] = []
    instructions: str

    @field_validator("tools")
    @classmethod
    def each_tool_names_its_type(cls, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for tool in tools:
            if not isinstance(tool.get("type"), str) or not tool["type"]:
                raise ValueError(f"every tool needs a 'type', got {tool!r}")
        return tools

    @field_validator("instructions")
    @classmethod
    def instructions_are_not_blank(cls, instructions: str) -> str:
        if not instructions.strip():
            raise ValueError("instructions.md is empty")
        return instructions


def load_agent_definition(folder: Path) -> AgentDefinition:
    folder = Path(folder)
    config_path = folder / "agent.yaml"
    instructions_path = folder / "instructions.md"

    for path in (config_path, instructions_path):
        if not path.is_file():
            raise AgentDefinitionError(f"{path} is missing")

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AgentDefinitionError(f"{config_path} is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise AgentDefinitionError(f"{config_path} must be a mapping of settings")
    if "instructions" in config:
        raise AgentDefinitionError(f"{config_path} must not set 'instructions'; they live in instructions.md")

    # The file ends with one newline, as text files do. That newline is not part of the
    # instructions; everything else is sent to Foundry exactly as written.
    instructions = instructions_path.read_text(encoding="utf-8").removesuffix("\n")

    try:
        definition = AgentDefinition(**config, instructions=instructions)
    except ValidationError as exc:
        raise AgentDefinitionError(f"{folder} is not a valid agent definition: {exc}") from exc

    if definition.name != folder.name:
        raise AgentDefinitionError(
            f"{config_path} names the agent '{definition.name}' but its folder is '{folder.name}'"
        )
    return definition
