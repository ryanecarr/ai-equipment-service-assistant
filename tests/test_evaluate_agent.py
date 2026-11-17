"""scripts/evaluate_agent.py without Azure: the committed cases load and build real
messages, each assertion passes and fails as it should, and a run blocks, passes or
stops as incomplete for the right reasons."""

import json
from types import SimpleNamespace

import pytest
from openai import RateLimitError

from scripts import evaluate_agent
from scripts.agent_definition import AGENTS_DIR
from scripts.evaluate_agent import Case, RunIncomplete, load_cases, run_suite, score, with_throttle_retries

AGENT_FOLDER = AGENTS_DIR / "equipment-service-assistant"


def _reply(**overrides) -> str:
    fields = {
        "equipment_model": "CAT 320",
        "reported_issue": "Boom raises slowly",
        "category": "Hydraulic",
        "priority": "Medium",
        "recommendations": ["Inspect the main control valve."],
        "additional_information_needed": ["Any fault codes shown."],
        "requires_technician_review": True,
        "related_work_orders": ["WO-1"],
        "grounding_used": True,
        "comparison_notes": None,
    }
    return json.dumps({**fields, **overrides})


def _case(**overrides) -> Case:
    fields = dict(name="example", description="", message="request\n\n---\n\nrecords", known_work_order_ids=frozenset({"WO-1"}))
    return Case(**{**fields, **overrides})


def _throttled(retry_after=None, headers=None) -> RateLimitError:
    # A stand-in for the HTTP response: the error only reads these three attributes.
    headers = headers or ({"retry-after": str(retry_after)} if retry_after is not None else {})
    response = SimpleNamespace(status_code=429, headers=headers, request=None)
    return RateLimitError("throttled", response=response, body=None)


class Clock:
    """Time that only moves when something sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


# --- the committed cases --------------------------------------------------------------------


def test_the_committed_cases_load_and_build_messages_with_the_application_code():
    cases = {case.name: case for case in load_cases(AGENT_FOLDER)}

    assert sorted(cases) == [
        "fake_record_injection",
        "grounded",
        "needs_confirmation",
        "no_matching_records",
        "other_machine_work_order",
        "request_contradicts_records",
        "saved_history_unverified",
    ]
    assert cases["grounded"].known_work_order_ids == {"WO-10234", "WO-10590"}
    assert "[WO-10234]" in cases["grounded"].message
    assert cases["needs_confirmation"].message.endswith(
        "KNOWN PRIOR RECORDS: none found for this equipment/customer in internal systems.\n\n"
        "PREVIOUSLY SAVED ASSESSMENTS: none found for this equipment in internal systems."
    )
    assert "associated with work order WO-10590" in cases["saved_history_unverified"].message
    # The made-up records sit in the request text; the real block after the separator says none.
    request_text, real_records = cases["fake_record_injection"].message.split("\n\n---\n\n")
    assert "WO-99999" in request_text and "WO-99999" not in real_records
    assert cases["fake_record_injection"].known_work_order_ids == frozenset()
    # Every case blocks on the schema. One case accepts a null in one named field.
    assert all("valid_schema" in case.blocking for case in cases.values())
    assert cases["grounded"].blocking["valid_schema"] is True
    assert cases["no_matching_records"].blocking["valid_schema"] == {"except_null": ["equipment_model"]}
    assert cases["no_matching_records"].advisory["valid_schema"] is True


def test_the_committed_exception_still_rejects_a_reply_the_application_cannot_accept():
    case = {case.name: case for case in load_cases(AGENT_FOLDER)}["no_matching_records"]

    result = score(case, json.dumps({"grounding_used": False, "related_work_orders": [], "comparison_notes": None}))

    schema = next(check for check in result["blocking"] if check["check"] == "valid_schema")
    assert schema["passed"] is False
    assert "reported_issue: Field required" in schema["detail"]


def _write_case(tmp_path, inputs, expect):
    folder = tmp_path / "agent" / "evals" / "cases" / "one"
    folder.mkdir(parents=True)
    (folder / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    (folder / "expect.yaml").write_text(expect, encoding="utf-8")
    return tmp_path / "agent"


def test_a_case_whose_request_no_longer_matches_as_intended_is_rejected(tmp_path):
    inputs = {"description": "", "request": {"request_text": "Our generator is noisy."}, "match_status": "matched"}

    with pytest.raises(ValueError, match="expected the request to be 'matched', but matching returned 'no_match'"):
        load_cases(_write_case(tmp_path, inputs, "blocking:\n  grounding_used: true\n"))


def test_an_unknown_assertion_name_is_rejected(tmp_path):
    inputs = {"description": "", "request": {"request_text": "Our generator is noisy."}, "match_status": "no_match"}

    with pytest.raises(ValueError, match="grounding_usd"):
        load_cases(_write_case(tmp_path, inputs, "blocking:\n  grounding_usd: false\n"))


NO_MATCH_INPUTS = {"description": "", "request": {"request_text": "Our generator is noisy."}, "match_status": "no_match"}


def test_listing_the_schema_check_as_advisory_does_not_stop_it_blocking(tmp_path):
    (case,) = load_cases(_write_case(tmp_path, NO_MATCH_INPUTS, "advisory:\n  valid_schema: true\n"))

    assert case.blocking["valid_schema"] is True


@pytest.mark.parametrize(
    "expect",
    [
        "blocking:\n  valid_schema: false\n",
        "blocking:\n  valid_schema:\n    except_null: [not_a_field]\n",
        "blocking:\n  valid_schema:\n    except_missing: [equipment_model]\n",
        "blocking:\n  valid_schema:\n    except_null: equipment_model\n",
    ],
)
def test_the_schema_check_cannot_be_switched_off_or_widened(tmp_path, expect):
    with pytest.raises(ValueError, match="valid_schema must be true, or except_null"):
        load_cases(_write_case(tmp_path, NO_MATCH_INPUTS, expect))


# --- assertions -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check, expected, passing, failing",
    [
        ("grounding_used", True, {}, {"grounding_used": False}),
        ("requires_technician_review", True, {}, {"requires_technician_review": False}),
        ("category", "Hydraulic", {}, {"category": "Engine"}),
        ("equipment_model_contains", "cat 320", {"equipment_model": "Caterpillar CAT 320"}, {"equipment_model": "PC210"}),
        ("cites", ["WO-1"], {}, {"related_work_orders": []}),
        ("cites_only_known_work_orders", True, {"related_work_orders": []}, {"related_work_orders": ["WO-1", "WO-9"]}),
        ("comparison_notes", "absent", {}, {"comparison_notes": "Similar to a saved report."}),
        ("comparison_notes", "present", {"comparison_notes": "Similar to a saved report."}, {}),
        ("plain_list_entries", True, {}, {"recommendations": ["- Inspect the valve."]}),
        ("plain_list_entries", True, {}, {"additional_information_needed": ["1. Fault codes."]}),
    ],
)
def test_each_assertion_passes_and_fails(check, expected, passing, failing):
    case = _case(blocking={check: expected})

    assert score(case, _reply(**passing))["blocking"][1] == {"check": check, "expected": expected, "passed": True}
    assert score(case, _reply(**failing))["blocking"][1] == {"check": check, "expected": expected, "passed": False}


def test_a_fenced_reply_is_a_valid_schema_but_not_plain_json():
    case = _case(advisory={"plain_json": True})

    result = score(case, f"```json\n{_reply()}\n```")

    assert result["blocking"] == [{"check": "valid_json", "expected": True, "passed": True}]
    assert result["advisory"] == [{"check": "plain_json", "expected": True, "passed": False}]


@pytest.mark.parametrize("raw", ["I could not find that machine.", '["Hydraulic"]'])
def test_a_reply_that_is_not_a_json_object_fails_the_case(raw):
    result = score(_case(blocking={"valid_schema": True, "grounding_used": True}), raw)

    assert [(check["check"], check["passed"]) for check in result["blocking"]] == [("valid_json", False)]


NULL_MODEL_ACCEPTED = {"valid_schema": {"except_null": ["equipment_model"]}}


def test_a_case_can_accept_a_null_in_one_named_field_and_the_strict_check_still_reports_it():
    # What agent version 5 sometimes returns when the request names no model.
    case = _case(blocking={**NULL_MODEL_ACCEPTED, "grounding_used": True}, advisory={"valid_schema": True})

    result = score(case, _reply(equipment_model=None))

    assert [(check["check"], check["passed"]) for check in result["blocking"]] == [
        ("valid_json", True),
        ("valid_schema", True),
        ("grounding_used", True),
    ]
    assert result["advisory"][0]["passed"] is False
    assert "equipment_model" in result["advisory"][0]["detail"]


def _without(field: str) -> str:
    reply = json.loads(_reply())
    del reply[field]
    return json.dumps(reply)


@pytest.mark.parametrize(
    "raw, named_in_detail",
    [
        (_without("equipment_model"), "equipment_model"),  # missing is not null
        (_reply(equipment_model=7), "equipment_model"),
        (_reply(equipment_model=None, category="Plumbing"), "category"),
        (_reply(equipment_model=None, recommendations=None), "recommendations"),
        (_without("priority"), "priority"),
    ],
)
def test_every_other_schema_error_still_blocks_that_case(raw, named_in_detail):
    result = score(_case(blocking=NULL_MODEL_ACCEPTED), raw)

    assert result["blocking"][1]["passed"] is False
    assert named_in_detail in result["blocking"][1]["detail"]


def test_the_accepted_null_is_left_out_of_the_failure_detail():
    result = score(_case(blocking=NULL_MODEL_ACCEPTED), _reply(equipment_model=None, category="Plumbing"))

    assert result["blocking"][1]["detail"].startswith("category: ")
    assert "equipment_model" not in result["blocking"][1]["detail"]


def test_a_missing_or_mistyped_field_fails_its_assertion_without_stopping_the_run():
    case = _case(blocking={"valid_schema": True, "cites_only_known_work_orders": True, "category": "Hydraulic"})

    result = score(case, json.dumps({"category": "Hydraulic", "related_work_orders": None}))

    assert [(check["check"], check["passed"]) for check in result["blocking"]] == [
        ("valid_json", True),
        ("valid_schema", False),
        ("cites_only_known_work_orders", False),
        ("category", True),
    ]


# --- the run --------------------------------------------------------------------------------


def test_a_run_passes_when_only_advisory_assertions_fail():
    case = _case(blocking={"grounding_used": True}, advisory={"category": "Engine"})

    report = run_suite([case], ask_agent=lambda message, timeout: _reply(), ask_grader=None, max_seconds=60)

    assert report["result"] == "passed"
    assert report["cases"][0]["raw_output"] == _reply()
    assert report["cases"][0]["advisory"] == [{"check": "category", "expected": "Engine", "passed": False}]


def test_a_run_fails_when_a_blocking_assertion_fails_and_still_runs_the_other_cases():
    first = _case(name="first", blocking={"grounding_used": False})
    second = _case(name="second", blocking={"grounding_used": True})

    report = run_suite([first, second], ask_agent=lambda message, timeout: _reply(), ask_grader=None, max_seconds=60)

    assert report["result"] == "failed"
    assert [entry["name"] for entry in report["cases"] if "raw_output" in entry] == ["first", "second"]


def test_the_agent_receives_the_case_message_and_rubric_scores_never_block():
    case = _case(rubric=("Does not invent a work order.",))
    sent = []

    def ask_agent(message, timeout):
        sent.append(message)
        return _reply()

    def ask_grader(graded_case, raw, timeout):
        return [{"criterion": graded_case.rubric[0], "met": False, "reason": "It did."}]

    report = run_suite([case], ask_agent, ask_grader, max_seconds=60)

    assert sent == [case.message]
    assert report["result"] == "passed"
    assert report["cases"][0]["rubric"][0]["met"] is False


def test_a_grader_error_is_recorded_as_not_scored():
    case = _case(rubric=("Does not invent a work order.",))

    def ask_grader(graded_case, raw, timeout):
        raise ValueError("grader returned prose")

    report = run_suite([case], lambda message, timeout: _reply(), ask_grader, max_seconds=60)

    assert report["result"] == "passed"
    assert report["cases"][0]["rubric"] == [
        {"criterion": "Does not invent a work order.", "met": None, "reason": "not scored: grader returned prose"}
    ]


def test_an_agent_error_makes_the_run_incomplete_and_skips_the_rest():
    def ask_agent(message, timeout):
        raise RuntimeError("404 version not found")

    report = run_suite([_case(name="first"), _case(name="second")], ask_agent, None, max_seconds=60)

    assert report["result"] == "incomplete"
    assert report["cases"][0]["error"] == "404 version not found"
    assert report["cases"][1]["error"].startswith("not run: first: 404")


def test_the_time_limit_stops_the_run():
    clock = Clock()

    def ask_agent(message, timeout):
        clock.now += 40
        return _reply()

    cases = [_case(name="first"), _case(name="second"), _case(name="third")]
    report = run_suite(cases, ask_agent, None, max_seconds=60, sleep=clock.sleep, clock=clock)

    assert report["result"] == "incomplete"
    assert ["raw_output" in entry for entry in report["cases"]] == [True, True, False]
    assert report["incomplete_reason"] == "time limit of 1 minutes reached during second"


def test_a_case_that_finishes_after_the_time_limit_is_never_reported_as_passed():
    clock = Clock()

    def ask_agent(message, timeout):
        clock.now += 120
        return _reply()

    report = run_suite([_case()], ask_agent, None, max_seconds=60, sleep=clock.sleep, clock=clock)

    assert report["result"] == "incomplete"
    assert report["incomplete_reason"] == "time limit of 1 minutes reached during example"
    assert report["cases"][0]["raw_output"] == _reply()


def test_the_grader_is_not_started_after_the_time_limit():
    clock = Clock()
    graded = []

    def ask_agent(message, timeout):
        clock.now += 61
        return _reply()

    def ask_grader(graded_case, raw, timeout):
        graded.append(graded_case.name)
        return []

    case = _case(rubric=("Does not invent a work order.",))
    report = run_suite([case], ask_agent, ask_grader, max_seconds=60, sleep=clock.sleep, clock=clock)

    assert graded == []
    assert report["result"] == "incomplete"
    assert report["cases"][0]["rubric"][0]["reason"] == "not scored: the time limit was reached"


def test_each_call_is_given_the_time_the_run_has_left():
    clock = Clock()
    timeouts = []

    def ask_agent(message, timeout):
        timeouts.append(timeout)
        clock.now += 10
        return _reply()

    def ask_grader(graded_case, raw, timeout):
        timeouts.append(timeout)
        clock.now += 5
        return [{"criterion": graded_case.rubric[0], "met": True, "reason": ""}]

    cases = [_case(name="first", rubric=("One.",)), _case(name="second", rubric=("One.",))]
    report = run_suite(cases, ask_agent, ask_grader, max_seconds=60, sleep=clock.sleep, clock=clock)

    assert report["result"] == "passed"
    assert timeouts == [60, 50, 45, 35]


def test_the_sdk_gets_the_timeout_and_is_told_not_to_retry_by_itself():
    options = []
    client = SimpleNamespace(with_options=lambda **kwargs: options.append(kwargs) or "bounded client")

    assert evaluate_agent.bounded(client, 42.5) == "bounded client"
    assert options == [{"timeout": 42.5, "max_retries": 0}]


# --- throttling -----------------------------------------------------------------------------


def test_throttling_waits_as_long_as_the_service_asks_then_retries():
    clock = Clock()
    outcomes = [_throttled(retry_after=7), _throttled(retry_after=90), _throttled(), "reply"]

    def call(timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert with_throttle_retries(call, deadline=1000, sleep=clock.sleep, clock=clock) == "reply"
    assert clock.sleeps == [7, 90, evaluate_agent.DEFAULT_THROTTLE_DELAY_SECONDS]


def test_a_delay_given_in_milliseconds_is_honored_and_an_unreadable_one_uses_the_default():
    clock = Clock()
    outcomes = [
        _throttled(headers={"retry-after-ms": "2500", "retry-after": "3"}),
        _throttled(headers={"retry-after": "Wed, 18 Nov 2026 07:28:00 GMT"}),
        "reply",
    ]

    def call(timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert with_throttle_retries(call, deadline=1000, sleep=clock.sleep, clock=clock) == "reply"
    assert clock.sleeps == [2.5, evaluate_agent.DEFAULT_THROTTLE_DELAY_SECONDS]


def test_a_wait_longer_than_the_run_allows_ends_the_run_instead_of_retrying_early():
    clock = Clock()
    calls = []

    def call(timeout):
        calls.append(1)
        raise _throttled(retry_after=500)

    with pytest.raises(RunIncomplete, match="asked for a 500s wait; this run waits at most 90s"):
        with_throttle_retries(call, deadline=1000, sleep=clock.sleep, clock=clock)

    assert len(calls) == 1
    assert clock.sleeps == []


def test_throttling_gives_up_after_the_attempt_limit():
    clock = Clock()
    calls = []

    def call(timeout):
        calls.append(1)
        raise _throttled(retry_after=1)

    with pytest.raises(RunIncomplete, match="still throttled after 6 attempts"):
        with_throttle_retries(call, deadline=1000, sleep=clock.sleep, clock=clock)

    assert len(calls) == evaluate_agent.MAX_ATTEMPTS_PER_CALL


def test_throttling_does_not_wait_past_the_time_limit():
    clock = Clock()

    def call(timeout):
        raise _throttled(retry_after=60)

    with pytest.raises(RunIncomplete, match="time limit"):
        with_throttle_retries(call, deadline=30, sleep=clock.sleep, clock=clock)

    assert clock.sleeps == []


def test_only_throttling_is_retried():
    calls = []

    def call(timeout):
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        with_throttle_retries(call, deadline=1000, sleep=Clock().sleep, clock=Clock())

    assert len(calls) == 1
