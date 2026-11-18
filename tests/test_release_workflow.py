"""The wiring of the extra evaluations in .github/workflows/release.yml: the parts the
scripts' own tests cannot see. The workflow is read as data; nothing is run."""

import yaml

from scripts.agent_definition import REPO_ROOT

WORKFLOW = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
JOBS = WORKFLOW["jobs"]
STEPS = JOBS["candidate"]["steps"]
NAMES = [step.get("name") for step in STEPS]

COMPARE = "Compare the agent definition with the last release"
FIRST = "Evaluate that version"
EXTRA = "Evaluate that version twice more and review the three runs"


def _step(name, steps=STEPS):
    return next(step for step in steps if step.get("name") == name)


def test_the_comparison_runs_before_an_agent_version_is_created():
    assert NAMES.index(COMPARE) < NAMES.index("Create the agent version")


def test_every_release_keeps_its_first_evaluation():
    first = _step(FIRST)

    assert "if" not in first
    assert first["run"] == (
        'python -m scripts.evaluate_agent --version "$AGENT_VERSION" --commit "$GITHUB_SHA" --report eval-report.json'
    )


def test_the_extra_evaluations_run_unless_the_comparison_said_no():
    extra = _step(EXTRA)

    # "Not false", so a comparison that left no answer cannot skip them.
    assert extra["if"] == "steps.change.outputs.extra != 'false'"
    assert NAMES.index(FIRST) < NAMES.index(EXTRA) < NAMES.index("Hand the agent reference to the package job")
    assert "--reference agent_reference.json --first-report eval-report.json" in extra["run"]


def test_a_manual_run_can_only_add_the_extra_evaluations():
    # PyYAML reads the key "on" as the boolean True.
    inputs = WORKFLOW[True]["workflow_dispatch"]["inputs"]

    assert list(inputs) == ["extra_evaluations"]
    assert inputs["extra_evaluations"]["default"] is False
    assert 'if [ "$REQUESTED" = "true" ]; then arguments+=(--requested); fi' in _step(COMPARE)["run"]


def test_every_report_is_kept_even_when_the_extra_step_fails():
    keep = _step("Keep the extra reports and the review")

    assert keep["if"] == "always() && steps.extra.outcome != 'skipped'"
    assert keep["with"] == {"name": "eval-reports-extra", "path": "eval-extra/"}
    assert NAMES.index(EXTRA) < NAMES.index("Keep the extra reports and the review")


def test_the_deployment_keeps_the_extra_reports_in_the_github_release():
    deploy = JOBS["deploy"]["steps"]
    download = next(step for step in deploy if step.get("with", {}).get("name") == "eval-reports-extra")

    assert JOBS["candidate"]["outputs"]["extra_evaluations"] == "${{ steps.change.outputs.extra }}"
    assert download["if"] == "needs.candidate.outputs.extra_evaluations != 'false'"
    assert download["with"]["path"] == "eval-extra"
    publish = _step("Publish the GitHub Release", deploy)["run"]
    assert 'for file in eval-extra/*; do\n  if [ -f "$file" ]; then assets+=("$file"); fi\ndone' in publish
    assert 'gh release create "$tag" "${assets[@]}"' in publish
