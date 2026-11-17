"""app/foundry/client.py against a fake OpenAI client: what is sent to Foundry, the
validate-and-retry-once loop, and the refusal to call the agent without an explicit version.
No Azure access."""

import json
from types import SimpleNamespace

import pytest

import app.foundry.agent_reference as agent_reference_mod
import app.foundry.client as client_mod
from app.errors import AgentReferenceError, AgentResponseValidationError, FoundryCallError
from app.foundry.agent_reference import AgentReference, load_agent_reference
from scripts import publish_agent

PROJECT = "https://example.services.ai.azure.com/api/projects/demo"
REFERENCE = AgentReference(
    project_endpoint=PROJECT, agent_name="equipment-service-assistant", agent_version="5", source_commit="abc1234"
)
VALID_OUTPUT = json.dumps(
    {
        "equipment_model": "CAT 320",
        "equipment_id": None,
        "customer": None,
        "reported_issue": "Boom raises slowly",
        "category": "Hydraulic",
        "priority": "Medium",
        "recommendations": ["Inspect the main control valve."],
        "additional_information_needed": [],
        "requires_technician_review": True,
        "related_work_orders": [],
        "grounding_used": False,
        "comparison_notes": None,
    }
)


class FakeOpenAI:
    """Records every call and replays the queued response texts in order."""

    def __init__(self, outputs, status="completed"):
        self._outputs = list(outputs)
        self._status = status
        self.created_conversations = []
        self.added_items = []
        self.response_calls = []
        self.deleted_conversations = []
        self.conversations = SimpleNamespace(
            create=self._create_conversation,
            delete=lambda conversation_id: self.deleted_conversations.append(conversation_id),
            items=SimpleNamespace(create=self._add_items),
        )
        self.responses = SimpleNamespace(create=self._create_response)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def _create_conversation(self, items):
        self.created_conversations.append(items)
        return SimpleNamespace(id="conv_1")

    def _add_items(self, conversation_id, items):
        self.added_items.append((conversation_id, items))

    def _create_response(self, **kwargs):
        self.response_calls.append(kwargs)
        return SimpleNamespace(status=self._status, output_text=self._outputs.pop(0), error="boom")


@pytest.fixture
def fake_openai(monkeypatch):
    def install(outputs, status="completed"):
        fake = FakeOpenAI(outputs, status)
        monkeypatch.setattr(client_mod, "_get_project_client", lambda: SimpleNamespace(get_openai_client=lambda: fake))
        agent_reference_mod._reference = REFERENCE
        return fake

    return install


# --- what is sent ---------------------------------------------------------------------------


def test_the_agent_is_called_with_its_explicit_version(fake_openai):
    fake = fake_openai([VALID_OUTPUT])

    client_mod.get_assessment("Boom is slow.", "KNOWN PRIOR RECORDS: none")

    assert fake.response_calls == [
        {
            "conversation": "conv_1",
            "extra_body": {
                "agent_reference": {"name": "equipment-service-assistant", "version": "5", "type": "agent_reference"}
            },
            "input": "",
        }
    ]


def test_the_composed_message_is_the_only_conversation_item(fake_openai):
    fake = fake_openai([VALID_OUTPUT])

    client_mod.get_assessment("Boom is slow.", "KNOWN PRIOR RECORDS: none")

    assert fake.created_conversations == [
        [{"type": "message", "role": "user", "content": "Boom is slow.\n\n---\n\nKNOWN PRIOR RECORDS: none"}]
    ]
    assert fake.deleted_conversations == ["conv_1"]


# --- validate and retry once ----------------------------------------------------------------


def test_a_fenced_response_is_accepted_without_a_retry(fake_openai):
    fake = fake_openai([f"```json\n{VALID_OUTPUT}\n```"])

    assessment = client_mod.get_assessment("Boom is slow.", "none")

    assert assessment.category == "Hydraulic"
    assert len(fake.response_calls) == 1


def test_an_invalid_response_is_retried_once_in_the_same_conversation_and_version(fake_openai):
    fake = fake_openai(["not json", VALID_OUTPUT])

    assessment = client_mod.get_assessment("Boom is slow.", "none")

    assert assessment.priority == "Medium"
    assert len(fake.response_calls) == 2
    assert fake.response_calls[1]["extra_body"]["agent_reference"]["version"] == "5"
    (conversation_id, items), = fake.added_items
    assert conversation_id == "conv_1"
    assert items[0]["content"].startswith("Your previous response did not match the required JSON schema: ")
    assert items[0]["content"].endswith("Return ONLY a valid JSON object matching the schema, with no markdown.")
    assert fake.deleted_conversations == ["conv_1"]


def test_two_invalid_responses_raise_and_still_delete_the_conversation(fake_openai):
    fake = fake_openai(["not json", '{"still": "wrong"}'])

    with pytest.raises(AgentResponseValidationError):
        client_mod.get_assessment("Boom is slow.", "none")

    assert len(fake.response_calls) == 2
    assert fake.deleted_conversations == ["conv_1"]


def test_an_incomplete_response_is_an_upstream_error(fake_openai):
    fake = fake_openai([VALID_OUTPUT], status="failed")

    with pytest.raises(FoundryCallError, match="failed"):
        client_mod.get_assessment("Boom is slow.", "none")

    assert fake.deleted_conversations == ["conv_1"]


# --- the agent reference --------------------------------------------------------------------


def _write(tmp_path, **overrides):
    path = tmp_path / "agent_reference.json"
    path.write_text(json.dumps({**REFERENCE.model_dump(), **overrides}), encoding="utf-8")
    return path


def test_a_valid_reference_loads(tmp_path):
    assert load_agent_reference(_write(tmp_path), PROJECT + "/") == REFERENCE


def test_no_reference_file_means_no_agent_call(monkeypatch, tmp_path, fake_openai):
    fake = fake_openai([VALID_OUTPUT])
    agent_reference_mod._reference = None
    monkeypatch.setattr(agent_reference_mod, "REFERENCE_PATH", tmp_path / "agent_reference.json")
    monkeypatch.setenv("PROJECT_ENDPOINT", PROJECT)

    with pytest.raises(AgentReferenceError, match="is missing"):
        client_mod.get_assessment("Boom is slow.", "none")

    assert fake.created_conversations == []
    assert fake.response_calls == []


@pytest.mark.parametrize("version", ["latest", "@latest", "", "0", "5.1", None])
def test_a_reference_must_name_a_numbered_version(tmp_path, version):
    with pytest.raises(AgentReferenceError, match="agent_version"):
        load_agent_reference(_write(tmp_path, agent_version=version), PROJECT)


def test_a_reference_missing_a_field_is_rejected(tmp_path):
    path = tmp_path / "agent_reference.json"
    path.write_text(json.dumps({"agent_name": "equipment-service-assistant", "agent_version": "5"}), encoding="utf-8")

    with pytest.raises(AgentReferenceError, match="project_endpoint"):
        load_agent_reference(path, PROJECT)


def test_a_reference_that_is_not_json_is_rejected(tmp_path):
    path = tmp_path / "agent_reference.json"
    path.write_text("version: 5", encoding="utf-8")

    with pytest.raises(AgentReferenceError, match="could not be read as JSON"):
        load_agent_reference(path, PROJECT)


def test_a_reference_built_for_another_project_is_rejected(tmp_path):
    with pytest.raises(AgentReferenceError, match="was built for Foundry project"):
        load_agent_reference(_write(tmp_path), "https://other.services.ai.azure.com/api/projects/other")


def test_the_app_setting_must_be_present_to_check_the_project(tmp_path):
    with pytest.raises(AgentReferenceError, match="PROJECT_ENDPOINT is not set"):
        load_agent_reference(_write(tmp_path), None)


def test_a_missing_reference_becomes_a_clear_http_error(monkeypatch, tmp_path, seed_grounding, saved_assessments):
    import azure.functions as func

    import function_app

    seed_grounding({}, {})
    monkeypatch.setattr(agent_reference_mod, "REFERENCE_PATH", tmp_path / "agent_reference.json")
    monkeypatch.setenv("PROJECT_ENDPOINT", PROJECT)
    request = func.HttpRequest(
        method="POST",
        url="http://localhost/api/assess",
        headers={},
        params={},
        route_params={},
        body=json.dumps({"request_text": "The boom is slow."}).encode(),
    )

    response = function_app.assess(request)

    assert response.status_code == 500
    assert json.loads(response.get_body())["error"]["code"] == "agent_reference_invalid"


# --- writing a reference for local runs -----------------------------------------------------


def test_reference_only_writes_a_file_the_app_accepts(tmp_path):
    output = tmp_path / "agent_reference.json"

    exit_code = publish_agent.main(
        ["--reference-only", "--version", "5", "--project-endpoint", PROJECT, "--commit", "abc1234", "--output", str(output)]
    )

    assert exit_code == 0
    assert load_agent_reference(output, PROJECT) == REFERENCE


def test_reference_only_refuses_latest(tmp_path, capsys):
    output = tmp_path / "agent_reference.json"

    exit_code = publish_agent.main(
        ["--reference-only", "--version", "latest", "--project-endpoint", PROJECT, "--output", str(output)]
    )

    assert exit_code == 1
    assert not output.exists()
    assert "agent_version" in capsys.readouterr().err


def test_a_version_is_required():
    with pytest.raises(SystemExit):
        publish_agent.main(["--reference-only"])
