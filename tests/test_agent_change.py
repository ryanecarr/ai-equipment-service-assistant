"""scripts/agent_change.py against small git repositories: which changes make a release
evaluate its agent version three times, and that a change which merged but was never
deployed keeps counting. No network and no GitHub."""

import json
import subprocess

import pytest

from scripts import agent_change

AGENT = "agents/equipment-service-assistant"
RELEASED = [{"tagName": "release-1.1", "publishedAt": "2026-11-17T22:59:10Z", "isDraft": False}]


def _git(repo, *args):
    command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args]
    return subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, files, message="change"):
    """Writes each file, or deletes it when its content is None, and commits."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def origin(tmp_path):
    """A repository shaped like this one, whose first commit was deployed as release-1.1."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        {
            f"{AGENT}/agent.yaml": "model: gpt-5-mini\n",
            f"{AGENT}/instructions.md": "Rules:\n- Be careful.\n",
            f"{AGENT}/evals/cases/grounded/expect.yaml": "blocking: {}\n",
            "app/client.py": "",
            "README.md": "",
        },
        "released",
    )
    _git(repo, "tag", "release-1.1")
    return repo


def _decide(repo, releases=RELEASED, requested=False):
    # The repository under test is the remote itself, so its tags are already there.
    return agent_change.decide(repo, releases, requested, fetch=lambda repo, tag: f"refs/tags/{tag}")


def test_the_newest_release_is_the_one_published_last():
    releases = [
        {"tagName": "release-4.1", "publishedAt": "2026-11-18T10:00:00Z", "isDraft": False},
        # A run with a lower number that was approved later is still the last deployment.
        {"tagName": "release-3.2", "publishedAt": "2026-11-18T11:00:00Z", "isDraft": False},
        {"tagName": "release-5.1", "publishedAt": None, "isDraft": True},
        {"tagName": "v9.0", "publishedAt": "2026-11-19T00:00:00Z", "isDraft": False},
    ]

    assert agent_change.newest_release(releases) == "release-3.2"
    assert agent_change.newest_release([]) is None


def test_an_unchanged_definition_gets_one_evaluation(origin):
    answer = _decide(origin)

    assert answer["extra_evaluations"] is False
    assert (answer["release"], answer["changed"]) == ("release-1.1", [])
    assert "evaluates the agent version once" in answer["summary"]


@pytest.mark.parametrize(
    "files, changed",
    [
        ({f"{AGENT}/instructions.md": "Rules:\n- Be more careful.\n"}, [f"{AGENT}/instructions.md"]),
        ({f"{AGENT}/agent.yaml": "model: gpt-5\n"}, [f"{AGENT}/agent.yaml"]),
        ({f"{AGENT}/instructions.md": None}, [f"{AGENT}/instructions.md"]),
        ({"agents/second/agent.yaml": "model: gpt-5-mini\n"}, ["agents/second/agent.yaml"]),
    ],
)
def test_a_changed_definition_gets_the_extra_evaluations(origin, files, changed):
    _commit(origin, files)

    answer = _decide(origin)

    assert answer["extra_evaluations"] is True
    assert answer["changed"] == changed
    assert "three times" in answer["summary"]


def test_changes_outside_the_definition_get_one_evaluation(origin):
    _commit(
        origin,
        {
            "app/client.py": "changed = True\n",
            "README.md": "changed\n",
            f"{AGENT}/evals/cases/grounded/expect.yaml": "blocking: {valid_schema: true}\n",
            # Only agents/<name>/agent.yaml and instructions.md are the definition.
            f"{AGENT}/evals/cases/grounded/instructions.md": "a note\n",
        },
    )

    assert _decide(origin)["extra_evaluations"] is False


def test_an_undeployed_instruction_change_still_counts_after_later_pushes(origin):
    _commit(origin, {f"{AGENT}/instructions.md": "Rules:\n- Be more careful.\n"}, "instruction change")
    # The instruction change's own release never deployed. Then came unrelated pushes.
    _commit(origin, {"app/client.py": "changed = True\n"}, "application change")
    _commit(origin, {"README.md": "changed\n"}, "documentation change")

    assert _decide(origin)["changed"] == [f"{AGENT}/instructions.md"]
    assert _decide(origin)["extra_evaluations"] is True

    # Once a release deploys it, the next release compares with that one.
    _git(origin, "tag", "release-2.1")
    deployed = [*RELEASED, {"tagName": "release-2.1", "publishedAt": "2026-11-18T01:19:38Z", "isDraft": False}]
    assert _decide(origin, deployed)["extra_evaluations"] is False


def test_a_change_undone_before_any_release_is_not_a_change(origin):
    _commit(origin, {f"{AGENT}/instructions.md": "Rules:\n- Be more careful.\n"})
    _commit(origin, {f"{AGENT}/instructions.md": "Rules:\n- Be careful.\n"})

    assert _decide(origin)["extra_evaluations"] is False


def test_without_a_release_the_extra_evaluations_run(origin):
    answer = _decide(origin, releases=[])

    assert answer["extra_evaluations"] is True
    assert answer["release"] is None


def test_a_run_can_ask_for_the_extra_evaluations_but_never_remove_them(origin):
    assert _decide(origin, requested=True)["extra_evaluations"] is True

    _commit(origin, {f"{AGENT}/agent.yaml": "model: gpt-5\n"})
    assert _decide(origin, requested=False)["extra_evaluations"] is True


@pytest.fixture
def checkout(origin, tmp_path):
    """What the release job has: one commit of main, several commits after the release."""
    _commit(origin, {f"{AGENT}/instructions.md": "Rules:\n- Be more careful.\n"}, "instruction change")
    _commit(origin, {"app/client.py": "changed = True\n"}, "application change")
    clone = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", "--depth", "1", origin.as_uri(), str(clone))
    return clone


def test_the_released_commit_is_fetched_into_a_one_commit_checkout(checkout, origin, tmp_path):
    released = _git(origin, "rev-parse", "release-1.1")
    with pytest.raises(subprocess.CalledProcessError):
        agent_change.definition_files(checkout, released)
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps(RELEASED), encoding="utf-8")
    output = tmp_path / "agent-change.json"

    exit_code = agent_change.main(["--releases", str(releases), "--output", str(output), "--repo", str(checkout)])

    assert exit_code == 0
    answer = json.loads(output.read_text(encoding="utf-8"))
    assert answer["extra_evaluations"] is True
    assert answer["changed"] == [f"{AGENT}/instructions.md"]


def test_the_fetch_leaves_a_full_clone_full_and_writes_no_tag(origin, tmp_path):
    clone = tmp_path / "full"
    _git(tmp_path, "clone", "-q", "--no-tags", origin.as_uri(), str(clone))
    _commit(clone, {f"{AGENT}/agent.yaml": "model: gpt-5\n"})

    answer = agent_change.decide(clone, RELEASED)

    assert answer["changed"] == [f"{AGENT}/agent.yaml"]
    # A depth here would have cut the clone's history at the released commit.
    assert _git(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert _git(clone, "tag", "--list") == ""


def test_a_comparison_that_cannot_be_made_fails_and_gives_no_answer(checkout, tmp_path, capsys):
    releases = tmp_path / "releases.json"
    releases.write_text(json.dumps([{"tagName": "release-9.1", "publishedAt": "2026-11-19T00:00:00Z"}]), encoding="utf-8")
    output = tmp_path / "agent-change.json"

    exit_code = agent_change.main(["--releases", str(releases), "--output", str(output), "--repo", str(checkout)])

    assert exit_code == 1
    assert not output.exists()
    assert "release-9.1" in capsys.readouterr().err
