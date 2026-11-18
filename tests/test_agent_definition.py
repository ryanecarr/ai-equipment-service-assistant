"""The committed agent folders must be valid, and the loader must reject the mistakes
that would otherwise only surface in Foundry."""

from pathlib import Path

import pytest

from scripts.agent_definition import AGENTS_DIR, AgentDefinitionError, load_agent_definition

AGENT_FOLDERS = sorted(path for path in AGENTS_DIR.iterdir() if path.is_dir())

VALID_CONFIG = "name: demo-agent\nowner: someone\nmodel: gpt-5-mini\nreasoning_effort: low\ntools:\n  - type: web_search\n"


def _write_agent(tmp_path: Path, config: str = VALID_CONFIG, instructions: str = "Be helpful.\n") -> Path:
    folder = tmp_path / "demo-agent"
    folder.mkdir()
    (folder / "agent.yaml").write_text(config, encoding="utf-8")
    (folder / "instructions.md").write_text(instructions, encoding="utf-8")
    return folder


def test_there_is_at_least_one_agent():
    assert AGENT_FOLDERS


@pytest.mark.parametrize("folder", AGENT_FOLDERS, ids=lambda path: path.name)
def test_committed_agent_folder_is_valid(folder):
    definition = load_agent_definition(folder)

    assert definition.name == folder.name


def test_equipment_agent_model_settings_change_only_on_purpose():
    definition = load_agent_definition(AGENTS_DIR / "equipment-service-assistant")

    assert definition.model == "gpt-5-mini"
    assert definition.reasoning_effort == "low"
    assert definition.tools == [{"type": "web_search"}]


def test_only_the_final_newline_is_dropped_from_instructions(tmp_path):
    folder = _write_agent(tmp_path, instructions="  First line.\n\nLast line.  \n")

    assert load_agent_definition(folder).instructions == "  First line.\n\nLast line.  "


@pytest.mark.parametrize(
    "config, problem",
    [
        (VALID_CONFIG + "temperature: 0.2\n", "temperature"),
        (VALID_CONFIG.replace("reasoning_effort: low", "reasoning_effort: extreme"), "reasoning_effort"),
        (VALID_CONFIG.replace("model: gpt-5-mini\n", ""), "model"),
        (VALID_CONFIG.replace("  - type: web_search", "  - name: web_search"), "type"),
        (VALID_CONFIG.replace("name: demo-agent", "name: other-agent"), "other-agent"),
        (VALID_CONFIG + "instructions: inline\n", "instructions.md"),
        ("- just\n- a list\n", "mapping"),
        ("name: [unclosed\n", "not valid YAML"),
    ],
    ids=["unknown-key", "bad-effort", "missing-model", "tool-without-type", "name-differs-from-folder",
         "inline-instructions", "not-a-mapping", "broken-yaml"],
)
def test_invalid_config_is_rejected(tmp_path, config, problem):
    folder = _write_agent(tmp_path, config=config)

    with pytest.raises(AgentDefinitionError, match=problem):
        load_agent_definition(folder)


def test_blank_instructions_are_rejected(tmp_path):
    folder = _write_agent(tmp_path, instructions="\n")

    with pytest.raises(AgentDefinitionError, match="empty"):
        load_agent_definition(folder)


@pytest.mark.parametrize("missing", ["agent.yaml", "instructions.md"])
def test_missing_file_is_rejected(tmp_path, missing):
    folder = _write_agent(tmp_path)
    (folder / missing).unlink()

    with pytest.raises(AgentDefinitionError, match="missing"):
        load_agent_definition(folder)
