"""The scripts the release workflow runs, without Azure: creating an agent version from the
committed files, building the deployment package, and the smoke test's verdicts."""

import json
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

from app.errors import AgentReferenceError
from app.foundry.agent_reference import load_agent_reference
from scripts import build_package, publish_agent, smoke_test
from scripts.agent_definition import AGENTS_DIR, REPO_ROOT, load_agent_definition

PROJECT = "https://example.services.ai.azure.com/api/projects/demo"
AGENT_FOLDER = AGENTS_DIR / "equipment-service-assistant"


# --- creating an agent version --------------------------------------------------------------


class FakeProject:
    """Records what create_version was asked for and returns the next version number."""

    def __init__(self, version="6"):
        self.calls = []
        self.agents = SimpleNamespace(create_version=self._create_version)
        self._version = version

    def _create_version(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(version=self._version)


def test_a_version_is_created_from_exactly_what_the_agent_folder_holds():
    project = FakeProject()
    definition = load_agent_definition(AGENT_FOLDER)

    version = publish_agent.create_version(project, definition, "abc1234")

    assert version == "6"
    (call,) = project.calls
    assert call["agent_name"] == "equipment-service-assistant"
    assert call["metadata"] == {"source_commit": "abc1234"}
    # The whole definition Foundry receives. Live version 5 has exactly these fields.
    assert call["definition"].as_dict() == {
        "kind": "prompt",
        "model": "gpt-5-mini",
        "reasoning": {"effort": "low"},
        "tools": [{"type": "web_search"}],
        "instructions": (AGENT_FOLDER / "instructions.md").read_text(encoding="utf-8").removesuffix("\n"),
    }


def test_create_writes_a_reference_to_the_version_foundry_returned(monkeypatch, tmp_path):
    project = FakeProject(version=7)
    endpoints = []
    monkeypatch.setattr(publish_agent, "DefaultAzureCredential", lambda: "credential")
    monkeypatch.setattr(
        publish_agent, "AIProjectClient", lambda endpoint, credential: endpoints.append(endpoint) or project
    )
    output = tmp_path / "agent_reference.json"

    exit_code = publish_agent.main(
        ["--create", "--project-endpoint", PROJECT, "--commit", "abc1234", "--output", str(output)]
    )

    assert exit_code == 0
    assert endpoints == [PROJECT]
    assert len(project.calls) == 1
    reference = load_agent_reference(output, PROJECT)
    assert (reference.agent_version, reference.source_commit) == ("7", "abc1234")


@pytest.mark.parametrize(
    "arguments",
    [
        [],  # no mode
        ["--create", "--reference-only", "--version", "5"],  # both modes
        ["--create", "--version", "5"],  # Foundry numbers a new version itself
    ],
)
def test_the_two_modes_cannot_be_mixed(monkeypatch, arguments):
    monkeypatch.setattr(publish_agent, "AIProjectClient", lambda **kwargs: pytest.fail("must not reach Foundry"))

    with pytest.raises(SystemExit):
        publish_agent.main([*arguments, "--project-endpoint", PROJECT])


# --- building the package -------------------------------------------------------------------


def test_the_real_package_holds_the_application_and_nothing_else():
    files = build_package.application_files(REPO_ROOT)

    assert {"function_app.py", "host.json", "requirements.txt", "app/foundry/client.py"} <= set(files)
    for name in files:
        assert name in ("function_app.py", "host.json", "requirements.txt") or name.startswith("app/"), name


@pytest.fixture
def checkout(tmp_path):
    """A small git repository shaped like this one, with dependencies installed and a
    reference made from its own commit."""

    def git(*args):
        command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args]
        return subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    tracked = {
        "function_app.py": "",
        "host.json": "{}",
        "app/client.py": "",
        "tests/test_client.py": "",
        "scripts/tool.py": "",
        "README.md": "",
        ".funcignore": "tests/\nscripts/\nREADME.md\n.git*\n",
        ".gitignore": "agent_reference.json\n.python_packages/\n",
    }
    for name, content in tracked.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "application")

    dependency = tmp_path / ".python_packages" / "lib" / "site-packages" / "pydantic" / "__init__.py"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("", encoding="utf-8")
    publish_agent.write_reference(
        tmp_path / "agent_reference.json", PROJECT, "equipment-service-assistant", "6", git("rev-parse", "HEAD")
    )
    return tmp_path


def test_the_package_holds_the_code_its_dependencies_and_the_reference(checkout, tmp_path_factory):
    output = tmp_path_factory.mktemp("out") / "package.zip"

    reference = build_package.build_package(checkout, output, PROJECT)

    assert reference.agent_version == "6"
    with zipfile.ZipFile(output) as package:
        assert sorted(package.namelist()) == [
            ".python_packages/lib/site-packages/pydantic/__init__.py",
            "agent_reference.json",
            "app/client.py",
            "function_app.py",
            "host.json",
        ]
        assert json.loads(package.read("agent_reference.json"))["agent_version"] == "6"


def test_no_package_without_a_reference(checkout, tmp_path_factory):
    (checkout / "agent_reference.json").unlink()

    with pytest.raises(AgentReferenceError, match="is missing"):
        build_package.build_package(checkout, tmp_path_factory.mktemp("out") / "package.zip", PROJECT)


def test_no_package_with_a_reference_for_another_project(checkout, tmp_path_factory):
    with pytest.raises(AgentReferenceError, match="was built for Foundry project"):
        build_package.build_package(
            checkout,
            tmp_path_factory.mktemp("out") / "package.zip",
            "https://other.services.ai.azure.com/api/projects/other",
        )


def test_no_package_with_a_reference_made_from_another_commit(checkout, tmp_path_factory):
    publish_agent.write_reference(
        checkout / "agent_reference.json", PROJECT, "equipment-service-assistant", "6", "0" * 40
    )

    with pytest.raises(build_package.PackageError, match="was made from commit 0000"):
        build_package.build_package(checkout, tmp_path_factory.mktemp("out") / "package.zip", PROJECT)


def test_no_package_without_installed_dependencies(checkout, tmp_path_factory):
    (checkout / ".python_packages" / "lib" / "site-packages" / "pydantic" / "__init__.py").unlink()

    with pytest.raises(build_package.PackageError, match="is empty"):
        build_package.build_package(checkout, tmp_path_factory.mktemp("out") / "package.zip", PROJECT)


# --- the smoke test -------------------------------------------------------------------------

GOOD = json.dumps({"match_status": "matched", "recommendations": ["Inspect the main control valve."]})


@pytest.mark.parametrize(
    "status, body, problem",
    [
        (200, GOOD, None),
        (500, '{"error": {"code": "agent_reference_invalid"}}', "HTTP 500"),
        (0, "no response: timed out", "HTTP 0"),
        (200, "<html>Service unavailable</html>", "not JSON"),
        (200, json.dumps({"match_status": "no_match", "recommendations": ["x"]}), "was not matched"),
        (200, json.dumps({"match_status": "matched", "recommendations": []}), "no recommendations"),
    ],
)
def test_the_smoke_test_verdict(status, body, problem):
    verdict = smoke_test.problem_with(status, body)

    assert (verdict is None) if problem is None else (problem in verdict)


def test_the_smoke_test_waits_for_the_application_to_come_back():
    responses = [(503, "restarting"), (0, "no response"), (200, GOOD)]
    sleeps = []

    assert smoke_test.run(lambda: responses.pop(0), sleep=sleeps.append) is True
    assert sleeps == [smoke_test.SECONDS_BETWEEN_ATTEMPTS] * 2


def test_the_smoke_test_gives_up_after_its_attempts():
    calls = []

    assert smoke_test.run(lambda: calls.append(1) or (500, "broken"), sleep=lambda seconds: None) is False
    assert len(calls) == smoke_test.ATTEMPTS
