"""scripts/extra_evaluations.py and scripts/review_evaluations.py without Azure: how many
runs are made, when a run is retried, what stops a release, and what the review recommends.
Every evaluation here is a stand-in that writes a report; nothing calls Foundry."""

import json
import sys

import pytest

from scripts import evaluate_agent, extra_evaluations
from scripts.extra_evaluations import SECONDS_BETWEEN_RUNS, run_extra_evaluations
from scripts.review_evaluations import classify, render, review

CANDIDATE = {
    "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
    "agent_name": "equipment-service-assistant",
    "agent_version": "9",
    "source_commit": "abc1234",
}
RUBRIC = "Does not imply the pump was replaced."
CATEGORY = 'grounded / category = "Hydraulic"'
MODEL = 'grounded / equipment_model_contains = "CAT 320"'
RUBRIC_LINE = f"injection / rubric: {RUBRIC}"


def _check(name, passed, expected=True):
    return {"check": name, "expected": expected, "passed": passed}


def _report(result=None, schema=True, category=True, model=True, rubric=True, stops_after=None, reply="{}", **identity):
    """A report of two cases. `stops_after` names the last case that ran before the run
    ended incomplete; the flags set single checks."""
    cases = [
        {
            "name": "grounded",
            "raw_output": reply,
            "blocking": [_check("valid_json", True), _check("valid_schema", schema)],
            "advisory": [_check("category", category, "Hydraulic"), _check("equipment_model_contains", model, "CAT 320")],
        },
        {
            "name": "injection",
            "raw_output": reply,
            "blocking": [_check("valid_json", True), _check("valid_schema", True)],
            "advisory": [],
            "rubric": [{"criterion": RUBRIC, "met": rubric, "reason": "as read"}],
        },
    ]
    names = [case["name"] for case in cases]
    if stops_after is not None:
        ran = names.index(stops_after) + 1
        cases = cases[:ran] + [{"name": name, "error": "not run: Connection error."} for name in names[ran:]]
        result = "incomplete"
    return {
        **CANDIDATE,
        **identity,
        "started_at": "2026-11-18T14:37:21.000000+00:00",
        "duration_seconds": 167.3,
        "result": result or ("passed" if schema else "failed"),
        "incomplete_reason": "injection: Connection error." if result == "incomplete" else None,
        "cases": cases,
    }


# The live version: it passes everything except the model name, a known gap.
BASELINES = {"release-2.1 eval-report.json": _report(model=False, agent_version="7")}
PASSED = _report()
INCOMPLETE = _report(stops_after="grounded")


# --- the review rules -------------------------------------------------------------------------


def test_a_check_is_classed_by_the_baselines_that_scored_it():
    baselines = {"A": {"passes": True, "fails": False, "mixed": True}, "B": {"mixed": False, "unscored": None}}

    assert classify("passes", baselines) == "baseline pass"  # B did not run it, which is no evidence
    assert classify("fails", baselines) == "known gap"
    assert classify("mixed", baselines) == "known gap"
    assert classify("unscored", baselines) == "no baseline"
    assert classify("new", baselines) == "no baseline"


def test_three_clean_runs_are_recommended_for_approval():
    result = review(BASELINES, {"run-1": PASSED, "run-2": PASSED, "run-3": PASSED})

    assert (result["verdict"], result["reasons"]) == ("approve", [])


def test_a_known_gap_never_changes_the_recommendation():
    failing = _report(model=False)

    result = review(BASELINES, {"run-1": failing, "run-2": failing, "run-3": PASSED})

    assert result["verdict"] == "approve"
    (gap,) = result["known_gaps"]
    assert gap["check"] == MODEL
    assert gap["baselines"] == {"release-2.1 eval-report.json": "FAIL"}
    assert gap["reports"] == {"run-1": "FAIL", "run-2": "FAIL", "run-3": "pass"}


def test_a_new_failure_in_two_reports_is_rejected():
    result = review(BASELINES, {"run-1": PASSED, "run-2": _report(category=False), "run-3": _report(category=False)})

    assert result["verdict"] == "reject"
    assert result["reasons"] == [f"New failure in 2 reports (run-2, run-3): {CATEGORY}"]


def test_a_new_failure_seen_once_needs_a_tie_break():
    result = review(BASELINES, {"run-1": PASSED, "run-2": _report(rubric=False), "run-3": PASSED})

    assert result["verdict"] == "tie-break"
    assert result["disputed"] == [{"check": RUBRIC_LINE, "failed_in": "run-2", "cleared_by": None}]


def test_a_new_failure_counts_in_a_run_that_ended_incomplete():
    runs = {
        "run-1": PASSED,
        "run-2": _report(category=False, stops_after="grounded"),
        "run-2-retry": PASSED,
        "run-3": _report(category=False),
    }

    result = review(BASELINES, runs)

    assert result["verdict"] == "reject"
    assert result["new_failures"] == [{"check": CATEGORY, "failed_in": ["run-2", "run-3"]}]


@pytest.mark.parametrize(
    "tie_break, verdict, reason",
    [
        ({"tie-break": PASSED}, "approve", None),
        ({"tie-break": _report(category=False)}, "reject", "New failure in 2 reports (run-2, tie-break)"),
        # Failed again before the run ended incomplete: that is still a second failure.
        ({"tie-break": _report(category=False, stops_after="grounded")}, "reject", "New failure in 2 reports"),
        ({"tie-break": _report(rubric=False)}, "reject", "New failure in the tie-break (tie-break)"),
        # Passed, but in a run that did not finish, so it does not clear the failure.
        ({"tie-break": INCOMPLETE}, "none", "The tie-break did not settle"),
        ({}, "none", "The tie-break did not settle"),
        ({"tie-break": INCOMPLETE, "tie-break-retry": PASSED}, "approve", None),
    ],
)
def test_the_tie_break_decides_a_new_failure_seen_once(tie_break, verdict, reason):
    runs = {"run-1": PASSED, "run-2": _report(category=False), "run-3": PASSED}

    result = review(BASELINES, runs, tie_break)

    assert result["verdict"] == verdict
    if reason is None:
        assert result["reasons"] == []
        assert result["disputed"][0]["cleared_by"] == list(tie_break)[-1]
    else:
        assert reason in result["reasons"][0]


def test_an_unscored_rubric_line_does_not_clear_a_tie_break_and_is_listed_for_reading():
    runs = {"run-1": PASSED, "run-2": _report(rubric=False), "run-3": PASSED}

    result = review(BASELINES, runs, {"tie-break": _report(rubric=None)})

    assert result["verdict"] == "none"
    assert result["to_read"] == [{"report": "tie-break", "check": RUBRIC_LINE, "class": "baseline pass"}]


def test_an_approval_that_depends_on_an_unscored_rubric_line_says_so():
    runs = {"run-1": PASSED, "run-2": _report(rubric=None, reply='{"note": "the reply to read"}'), "run-3": PASSED}

    result = review(BASELINES, runs)
    text = render(result, BASELINES, runs)

    assert result["verdict"] == "approve"
    assert "This holds only if every rubric line" in text and "(1 to read)" in text
    assert f"- run-2, baseline pass: {RUBRIC_LINE}" in text
    assert "the reply to read" in text
    warning = extra_evaluations._annotation({"stopped": None, "review": result})
    assert warning.startswith("::warning title=Agent evaluation review::Recommendation: approve, but only if")


def test_a_failing_check_without_a_baseline_gets_no_recommendation():
    result = review({}, {"run-1": PASSED, "run-2": _report(category=False), "run-3": PASSED})

    assert result["verdict"] == "none"
    assert result["reasons"] == [f"No baseline scored a check that failed in run-2: {CATEGORY}"]


def test_fewer_than_three_finished_runs_get_no_recommendation():
    result = review(BASELINES, {"run-1": PASSED, "run-2": INCOMPLETE, "run-2-retry": INCOMPLETE})

    assert (result["verdict"], result["reasons"]) == ("none", ["Only 1 of the 3 evaluations finished"])


def test_a_blocking_failure_is_rejected_even_in_a_run_that_ended_incomplete():
    runs = {"run-1": PASSED, "run-2": _report(schema=False, stops_after="grounded")}

    result = review(BASELINES, runs)

    assert result["verdict"] == "reject"
    assert result["reasons"][0] == "A blocking assertion failed in run-2: grounded / valid_schema = true"


def test_an_assertion_keeps_its_baseline_when_it_moves_between_sections():
    # The live version passed it as a blocking assertion; the candidate has it as advisory.
    baseline = _report(agent_version="7")
    baseline["cases"][0]["blocking"].append(baseline["cases"][0]["advisory"].pop(0))

    result = review({"A": baseline}, {"run-1": PASSED, "run-2": _report(category=False), "run-3": _report(category=False)})

    assert result["new_failures"] == [{"check": CATEGORY, "failed_in": ["run-2", "run-3"]}]


# --- the runs ---------------------------------------------------------------------------------


class Evaluations:
    """Stands in for scripts.evaluate_agent. Each call writes the next planned report and
    returns the exit code the evaluator would; None is a run that died without a report.
    A call beyond the plan fails the test."""

    def __init__(self, *planned):
        self.planned = list(planned)
        self.written = []

    def __call__(self, path):
        self.written.append(path.name)
        report = self.planned.pop(0)
        if report is None:
            return 1
        path.write_text(json.dumps(report), encoding="utf-8")
        return {"passed": 0, "failed": 1, "incomplete": 2}[report["result"]]


def _run(tmp_path, *planned, first=PASSED, baselines=BASELINES):
    evaluations, sleeps = Evaluations(*planned), []
    outcome = run_extra_evaluations(first, baselines, evaluations, tmp_path / "eval-extra", sleep=sleeps.append)
    assert evaluations.planned == [], "a planned run was not made"
    assert sleeps == [SECONDS_BETWEEN_RUNS] * len(evaluations.written), "every run waits first"
    saved = sorted(path.name for path in (tmp_path / "eval-extra").glob("*.json"))
    assert saved == sorted(name for name, report in zip(evaluations.written, planned) if report is not None)
    return outcome, evaluations.written


def test_two_more_runs_five_minutes_apart_each_with_its_own_report(tmp_path):
    outcome, written = _run(tmp_path, PASSED, PASSED)

    assert SECONDS_BETWEEN_RUNS == 300
    assert written == ["eval-report-run-2.json", "eval-report-run-3.json"]
    assert (outcome["exit_code"], outcome["stopped"], outcome["review"]["verdict"]) == (0, None, "approve")
    assert list(outcome["runs"]) == ["run-1", "run-2", "run-3"]


def test_a_run_that_ends_incomplete_is_tried_once_more_and_both_reports_are_kept(tmp_path):
    outcome, written = _run(tmp_path, INCOMPLETE, PASSED, PASSED)

    assert written == ["eval-report-run-2.json", "eval-report-run-2-retry.json", "eval-report-run-3.json"]
    assert (outcome["exit_code"], outcome["review"]["verdict"]) == (0, "approve")
    assert outcome["runs"]["run-2"]["result"] == "incomplete"


def test_a_run_that_ends_incomplete_twice_stops_the_release(tmp_path):
    outcome, written = _run(tmp_path, PASSED, INCOMPLETE, INCOMPLETE)

    assert written == ["eval-report-run-2.json", "eval-report-run-3.json", "eval-report-run-3-retry.json"]
    assert (outcome["exit_code"], outcome["stopped"]) == (2, "run-3 did not finish in two attempts")
    assert outcome["review"]["verdict"] == "none"


def test_a_run_that_writes_no_report_counts_as_incomplete(tmp_path):
    outcome, written = _run(tmp_path, None, None)

    assert written == ["eval-report-run-2.json", "eval-report-run-2-retry.json"]
    assert (outcome["exit_code"], outcome["stopped"]) == (2, "run-2 did not finish in two attempts")
    assert [attempt["result"] for attempt in outcome["attempts"]] == ["no report", "no report"]


def test_a_blocking_failure_stops_the_release_without_another_run(tmp_path):
    outcome, written = _run(tmp_path, _report(schema=False))

    assert written == ["eval-report-run-2.json"]
    assert outcome["exit_code"] == 1
    assert outcome["stopped"] == "A blocking assertion failed in run-2: grounded / valid_schema = true"


def test_a_blocking_failure_in_a_run_that_ended_incomplete_is_not_retried_away(tmp_path):
    outcome, written = _run(tmp_path, PASSED, _report(schema=False, stops_after="grounded"))

    # No run-3-retry: a clean retry must not hide the failure that was scored.
    assert written == ["eval-report-run-2.json", "eval-report-run-3.json"]
    assert outcome["exit_code"] == 1
    assert outcome["review"]["verdict"] == "reject"
    assert "run-3: grounded / valid_schema" in outcome["stopped"]
    saved = json.loads((tmp_path / "eval-extra" / "eval-report-run-3.json").read_text(encoding="utf-8"))
    assert saved["result"] == "incomplete"
    assert saved["cases"][0]["blocking"][1]["passed"] is False


def test_a_report_for_another_candidate_stops_the_release(tmp_path):
    outcome, _ = _run(tmp_path, _report(agent_version="8"))

    assert outcome["exit_code"] == 2
    assert outcome["stopped"] == "eval-report-run-2.json is not a report of this candidate: agent_version differ"
    assert list(outcome["runs"]) == ["run-1"]


def test_an_existing_report_is_never_overwritten(tmp_path):
    existing = tmp_path / "eval-extra" / "eval-report-run-2.json"
    existing.parent.mkdir()
    existing.write_text("kept", encoding="utf-8")
    evaluations = Evaluations()

    outcome = run_extra_evaluations(PASSED, BASELINES, evaluations, tmp_path / "eval-extra", sleep=lambda seconds: None)

    assert outcome["exit_code"] == 2
    assert evaluations.written == []
    assert existing.read_text(encoding="utf-8") == "kept"


@pytest.mark.parametrize(
    "first, exit_code",
    [(_report(schema=False), 1), (_report(schema=False, stops_after="grounded"), 1), (INCOMPLETE, 2)],
)
def test_no_extra_run_is_made_after_a_first_evaluation_that_did_not_pass(tmp_path, first, exit_code):
    outcome, written = _run(tmp_path, first=first)

    assert written == []
    assert outcome["exit_code"] == exit_code


def test_advisory_and_rubric_findings_never_stop_the_release(tmp_path):
    outcome, written = _run(tmp_path, _report(category=False, rubric=False), _report(category=False, model=False))

    assert len(written) == 2  # already rejected, so no tie-break run is made
    assert outcome["exit_code"] == 0
    assert outcome["review"]["verdict"] == "reject"


def test_a_new_failure_seen_once_gets_a_tie_break_run(tmp_path):
    outcome, written = _run(tmp_path, _report(category=False), PASSED, PASSED)

    assert written == ["eval-report-run-2.json", "eval-report-run-3.json", "eval-report-tie-break.json"]
    assert (outcome["exit_code"], outcome["review"]["verdict"]) == (0, "approve")
    assert outcome["review"]["disputed"] == [{"check": CATEGORY, "failed_in": "run-2", "cleared_by": "tie-break"}]


def test_a_tie_break_that_fails_again_is_rejected_without_a_retry(tmp_path):
    outcome, written = _run(tmp_path, _report(category=False), PASSED, _report(category=False, stops_after="grounded"))

    assert written[-1] == "eval-report-tie-break.json"
    assert (outcome["exit_code"], outcome["review"]["verdict"]) == (0, "reject")


def test_a_tie_break_that_cannot_finish_twice_stops_the_release(tmp_path):
    outcome, written = _run(tmp_path, _report(category=False), PASSED, INCOMPLETE, None)

    assert written[-2:] == ["eval-report-tie-break.json", "eval-report-tie-break-retry.json"]
    assert (outcome["exit_code"], outcome["stopped"]) == (2, "The tie-break run did not finish in two attempts")
    assert outcome["review"]["verdict"] == "none"


def test_a_finished_tie_break_that_could_not_score_the_line_is_not_run_again(tmp_path):
    outcome, written = _run(tmp_path, _report(rubric=False), PASSED, _report(rubric=None))

    assert written[-1] == "eval-report-tie-break.json"
    assert (outcome["exit_code"], outcome["review"]["verdict"]) == (0, "none")


def test_no_slot_ever_gets_more_than_two_attempts(tmp_path):
    outcome, written = _run(tmp_path, INCOMPLETE, _report(category=False), INCOMPLETE, PASSED, INCOMPLETE, PASSED)

    assert written == [
        "eval-report-run-2.json",
        "eval-report-run-2-retry.json",
        "eval-report-run-3.json",
        "eval-report-run-3-retry.json",
        "eval-report-tie-break.json",
        "eval-report-tie-break-retry.json",
    ]
    assert (outcome["exit_code"], outcome["review"]["verdict"]) == (0, "approve")


# --- the review a reviewer reads, and the command line -----------------------------------------


def test_the_review_shows_what_a_reviewer_needs_and_escapes_the_replies():
    runs = {
        "run-1": _report(reply='{"note": "<script>alert(1)</script>"}'),
        "run-2": _report(model=False, rubric=None, schema=False, stops_after="injection"),
    }

    text = render(review(BASELINES, runs), BASELINES, runs, stopped="A blocking assertion failed in run-2", no_report=("run-3",))

    assert "**Recommendation: reject.**" in text
    assert "**The release stops here:** A blocking assertion failed in run-2" in text
    assert "version **9** from commit `abc1234`" in text
    assert "`release-2.1 eval-report.json` (version 7, passed)" in text
    assert "| run-2 | 2026-11-18 14:37:21 | 167 | incomplete: injection: Connection error. | 2 of 2 | 1 |" in text
    assert "run-3 ended without writing a report." in text
    assert "| grounded / equipment_model_contains = &quot;CAT 320&quot; | FAIL | pass | FAIL |" in text
    assert "### Rubric lines to judge by reading" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>" not in text


class _NoFoundry:
    """Stands in for the Foundry project client. The suite is replaced too, so nothing is called."""

    def get_openai_client(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *error):
        return False


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """The files the release job has when the extra step starts. Each command the runner
    would start as a process is recorded and handed to the real scripts.evaluate_agent
    command line, with Foundry and the suite replaced, so the report is the one that
    command writes from the arguments it was given."""
    (tmp_path / "agent_reference.json").write_text(json.dumps(CANDIDATE), encoding="utf-8")
    (tmp_path / "eval-report.json").write_text(json.dumps(PASSED), encoding="utf-8")
    (tmp_path / "eval-baselines").mkdir()
    (tmp_path / "eval-baselines" / "eval-report.json").write_text(json.dumps(BASELINES["release-2.1 eval-report.json"]), encoding="utf-8")
    commands = []
    suite = {"result": "passed", "incomplete_reason": None, "cases": PASSED["cases"]}
    monkeypatch.setattr(evaluate_agent, "_get_project_client", _NoFoundry)
    monkeypatch.setattr(evaluate_agent, "run_suite", lambda *args, **kwargs: suite)
    monkeypatch.setenv("PROJECT_ENDPOINT", "")  # the evaluator sets it; this puts it back afterwards

    def fake_run(command, cwd):
        commands.append(command)
        assert command[:3] == [sys.executable, "-m", "scripts.evaluate_agent"]
        return type("Completed", (), {"returncode": evaluate_agent.main(command[3:])})()

    monkeypatch.setattr(extra_evaluations.subprocess, "run", fake_run)
    monkeypatch.setattr(extra_evaluations, "SECONDS_BETWEEN_RUNS", 0)
    monkeypatch.chdir(tmp_path)
    return commands


ARGUMENTS = ["--reference", "agent_reference.json", "--first-report", "eval-report.json", "--baselines", "eval-baselines"]
ARGUMENTS += ["--release", "release-2.1", "--output-dir", "eval-extra", "--summary", "summary.md"]


def test_the_command_evaluates_the_same_version_commit_and_project_every_time(workspace, tmp_path, capsys):
    assert extra_evaluations.main(ARGUMENTS) == 0

    assert len(workspace) == 2
    for command in workspace:
        for flag, key in (("--version", "agent_version"), ("--commit", "source_commit"), ("--project-endpoint", "project_endpoint")):
            assert command[command.index(flag) + 1] == CANDIDATE[key]
    # Written by the evaluator's own command line from those arguments.
    for name in ("eval-report-run-2.json", "eval-report-run-3.json"):
        written = json.loads((tmp_path / "eval-extra" / name).read_text(encoding="utf-8"))
        assert {key: written[key] for key in CANDIDATE} == CANDIDATE
    record = json.loads((tmp_path / "eval-extra" / "eval-review.json").read_text(encoding="utf-8"))
    assert (record["verdict"], record["exit_code"], record["agent_version"]) == ("approve", 0, "9")
    assert record["baselines"] == ["release-2.1 eval-report.json"]
    assert [attempt["name"] for attempt in record["attempts"]] == ["run-1", "run-2", "run-3"]
    review_text = (tmp_path / "eval-extra" / "eval-review.md").read_text(encoding="utf-8")
    assert (tmp_path / "summary.md").read_text(encoding="utf-8") == review_text + "\n"
    assert "::notice title=Agent evaluation review::Recommendation: approve." in capsys.readouterr().out


def test_the_command_refuses_a_first_report_of_another_candidate(workspace, tmp_path, capsys):
    (tmp_path / "eval-report.json").write_text(json.dumps(_report(agent_version="8")), encoding="utf-8")

    assert extra_evaluations.main(ARGUMENTS) == 2

    assert workspace == []
    assert "agent_version differ" in capsys.readouterr().out
