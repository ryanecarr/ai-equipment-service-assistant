"""Evaluates one exact Foundry agent version against the cases in agents/<name>/evals/cases/.

    python -m scripts.evaluate_agent --version 5

Inputs:  the agent folder, a version number and the Foundry project endpoint
         (PROJECT_ENDPOINT, or local.settings.json).
Output:  a JSON report (--report) and a summary on stdout.
Exit:    0 if every blocking assertion passed, 1 if one failed, 2 if the run could not
         finish (an error, throttling past the retry limit, or out of time).

Each case is a request plus sample records. The message is built by the same code
/api/assess runs, the agent is called the same way the application calls it, and the
agent's first reply is scored as it arrived: no corrective retry, none of the
application's output corrections. Blocking assertions decide the exit code. Advisory
assertions and rubric scores are reported only.

Cases run one at a time because the agent shares the application's model capacity. Only
throttling is retried, never sooner than the service asks. Every HTTP request gets the
time the run has left at that moment as its timeout, so the run cannot outlast its limit.
"""

import argparse
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from openai import RateLimitError
from pydantic import ValidationError

import app.grounding.repository as grounding_repository
import app.history.repository as history_repository
from app.foundry.agent_reference import AgentReference
from app.foundry.client import _get_project_client, _run_response, _strip_markdown_fence, compose_message
from app.prompt_context import build_prompt_context
from app.schemas.history_models import SavedAssessmentRecord
from app.schemas.response_models import MaintenanceAssessment
from scripts.agent_definition import REPO_ROOT, AgentDefinitionError, load_agent_definition
from scripts.migrate_grounding_to_cosmos import load_source
from scripts.publish_agent import current_commit, default_agent_folder, default_project_endpoint

# The in-memory Cosmos stand-ins the unit tests use, so a case needs no database.
sys.path.insert(0, str(REPO_ROOT / "tests"))
from cosmos_fakes import FakeContainer, repository_for  # noqa: E402

MAX_ATTEMPTS_PER_CALL = 6
MAX_THROTTLE_DELAY_SECONDS = 90.0
# Used only when a throttled response carries no retry delay at all.
DEFAULT_THROTTLE_DELAY_SECONDS = 30.0
# Deleting an evaluation conversation gets at most this long, and less if less time is left.
CLEANUP_TIMEOUT_SECONDS = 10.0

_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s")


class RunIncomplete(Exception):
    """The run stopped before every case was scored."""


# --- cases ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    message: str
    known_work_order_ids: frozenset[str]
    blocking: dict[str, Any] = field(default_factory=dict)
    advisory: dict[str, Any] = field(default_factory=dict)
    rubric: tuple[str, ...] = ()


def _schema_errors(output: dict[str, Any], expected: Any = True) -> list[str]:
    """Every way the reply breaks the application's schema. `expected` is True, or
    {"except_null": [field, ...]} to accept a null in exactly those fields: a documented
    gap in the live agent version. A missing field, or any other error, still counts."""
    try:
        MaintenanceAssessment.model_validate(output)
    except ValidationError as exc:
        accepted_nulls = set(expected.get("except_null", ())) if isinstance(expected, dict) else set()
        return [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
            if not (len(error["loc"]) == 1 and error["loc"][0] in accepted_nulls and error["input"] is None)
        ]
    return []


def _list_entries(output: dict[str, Any]) -> list[str]:
    return list(output["recommendations"]) + list(output["additional_information_needed"])


# Each check reads the reply as a plain JSON object, so a reply that fails the schema in
# one field can still be checked on the others. A check that raises counts as failed.
CHECKS: dict[str, Callable[[Any, dict[str, Any], str, Case], bool]] = {
    "valid_schema": lambda expected, output, raw, case: not _schema_errors(output, expected),
    "grounding_used": lambda expected, output, raw, case: output["grounding_used"] is expected,
    "requires_technician_review": lambda expected, output, raw, case: output["requires_technician_review"] is expected,
    "category": lambda expected, output, raw, case: output["category"] == expected,
    "equipment_model_contains": lambda expected, output, raw, case: expected.lower() in output["equipment_model"].lower(),
    "cites": lambda expected, output, raw, case: set(expected) <= set(output["related_work_orders"]),
    # The work orders the application itself would accept for this request.
    "cites_only_known_work_orders": lambda expected, output, raw, case: (
        set(output["related_work_orders"]) <= case.known_work_order_ids
    ),
    "comparison_notes": lambda expected, output, raw, case: (
        (output["comparison_notes"] is None) == (expected == "absent")
    ),
    "plain_json": lambda expected, output, raw, case: raw.strip().startswith("{"),
    "plain_list_entries": lambda expected, output, raw, case: not any(
        _LIST_MARKER.match(entry) for entry in _list_entries(output)
    ),
}

# Applied to every case, and it cannot be switched off. A case can only narrow it to accept
# a null in named fields: valid_schema: {except_null: [equipment_model]}.
BLOCKING_FOR_EVERY_CASE = {"valid_schema": True}
# The application already corrects both of these, so they never block.
ADVISORY_FOR_EVERY_CASE = {"plain_json": True, "plain_list_entries": True}


def load_case(folder: Path) -> Case:
    inputs = json.loads((folder / "inputs.json").read_text(encoding="utf-8"))
    expect = yaml.safe_load((folder / "expect.yaml").read_text(encoding="utf-8")) or {}

    unknown_sections = set(expect) - {"blocking", "advisory", "rubric"}
    advisory = {**ADVISORY_FOR_EVERY_CASE, **(expect.get("advisory") or {})}
    blocking = {**BLOCKING_FOR_EVERY_CASE, **(expect.get("blocking") or {})}
    unknown_checks = (set(blocking) | set(advisory)) - set(CHECKS)
    if unknown_sections or unknown_checks:
        raise ValueError(f"{folder.name}/expect.yaml uses unknown names: {sorted(unknown_sections | unknown_checks)}")
    for section in (blocking, advisory):
        if section.get("comparison_notes", "absent") not in ("absent", "present"):
            raise ValueError(f"{folder.name}/expect.yaml: comparison_notes must be 'absent' or 'present'")
        schema = section.get("valid_schema", True)
        narrowed = isinstance(schema, dict) and set(schema) == {"except_null"} and isinstance(schema["except_null"], list)
        if schema is not True and not (narrowed and set(schema["except_null"]) <= set(MaintenanceAssessment.model_fields)):
            raise ValueError(
                f"{folder.name}/expect.yaml: valid_schema must be true, or except_null with a list of schema fields"
            )

    # Sample records behind the same lookups /api/assess uses.
    equipment_master, work_orders = load_source("seed")
    grounding_repository._repository = repository_for(equipment_master, work_orders)
    history = history_repository.SavedAssessmentRepository(FakeContainer("saved-assessments"))
    history_repository._repository = history
    try:
        for record in inputs.get("saved_assessments", []):
            history.add(SavedAssessmentRecord.model_validate(record))
        request = inputs["request"]
        context = build_prompt_context(request.get("equipment_id"), request["request_text"])
    finally:
        grounding_repository._repository = None
        history_repository._repository = None

    # Guards the case itself: if matching changes, the case must not silently test something else.
    if context.equipment_match.status != inputs["match_status"]:
        raise ValueError(
            f"{folder.name}: expected the request to be '{inputs['match_status']}', "
            f"but matching returned '{context.equipment_match.status}'"
        )

    return Case(
        name=folder.name,
        description=inputs["description"],
        message=compose_message(request["request_text"], context.combined_grounding),
        known_work_order_ids=frozenset(context.known_work_order_ids),
        blocking=blocking,
        advisory=advisory,
        rubric=tuple(expect.get("rubric") or ()),
    )


def load_cases(agent_folder: Path) -> list[Case]:
    folders = sorted(path for path in (agent_folder / "evals" / "cases").iterdir() if path.is_dir())
    if not folders:
        raise ValueError(f"No evaluation cases in {agent_folder / 'evals' / 'cases'}")
    return [load_case(folder) for folder in folders]


# --- scoring --------------------------------------------------------------------------------


def _assert_all(expectations: dict[str, Any], output: dict[str, Any], raw: str, case: Case) -> list[dict]:
    results = []
    for name, expected in expectations.items():
        try:
            passed = bool(CHECKS[name](expected, output, raw, case))
        except Exception:  # a missing field, or one of the wrong type
            passed = False
        result = {"check": name, "expected": expected, "passed": passed}
        if name == "valid_schema" and not passed:
            result["detail"] = "; ".join(_schema_errors(output, expected))[:500]
        results.append(result)
    return results


def score(case: Case, raw: str) -> dict[str, list[dict]]:
    """Scores one raw reply. A reply that is not a JSON object fails the case outright,
    because nothing else about it can be checked."""
    try:
        # The same fence-stripping the application applies before it validates.
        output = json.loads(_strip_markdown_fence(raw))
        if not isinstance(output, dict):
            raise ValueError("the reply is JSON but not an object")
    except ValueError as exc:  # includes json.JSONDecodeError
        failed = {"check": "valid_json", "expected": True, "passed": False, "detail": str(exc)[:500]}
        return {"blocking": [failed], "advisory": []}

    return {
        "blocking": [{"check": "valid_json", "expected": True, "passed": True}]
        + _assert_all(case.blocking, output, raw, case),
        "advisory": _assert_all(case.advisory, output, raw, case),
    }


# --- calling Foundry ------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _delay_seconds(name: str, value: str) -> Optional[float]:
    """One retry header as seconds from now, or None if it cannot be read."""
    try:
        seconds = float(value) * (0.001 if name == "retry-after-ms" else 1.0)
    except (TypeError, ValueError):
        if name != "retry-after":
            return None
        try:  # Retry-After may also be an HTTP date (RFC 9110, section 10.2.3).
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        seconds = (moment - _utc_now()).total_seconds()
    return max(seconds, 0.0) if math.isfinite(seconds) else None


def _retry_after_seconds(exc: RateLimitError) -> Optional[float]:
    """The wait the service asked for, or None if it sent no retry delay. A delay that was
    sent but cannot be read ends the run: guessing could retry sooner than asked."""
    headers = getattr(exc.response, "headers", None) or {}
    sent = {name: headers[name] for name in ("retry-after-ms", "retry-after") if name in headers}
    for name, value in sent.items():
        seconds = _delay_seconds(name, value)
        if seconds is not None:
            return seconds
    if sent:
        raise RunIncomplete(f"throttled, and the service's retry delay could not be read: {sent}") from exc
    return None


def with_throttle_retries(
    call: Callable[[Callable[[], float]], Any],
    deadline: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Makes one call, retrying only on throttling. `call` receives time_left(), which it
    asks before each HTTP request for that request's timeout; time_left() raises
    RunIncomplete once the deadline has passed. It never retries sooner than the service
    asks: a wait longer than this run allows ends the run instead."""

    def time_left() -> float:
        seconds = deadline - clock()
        if seconds <= 0:
            raise RunIncomplete("the time limit was reached")
        return seconds

    for attempt in range(1, MAX_ATTEMPTS_PER_CALL + 1):
        time_left()
        try:
            return call(time_left)
        except RateLimitError as exc:
            asked = _retry_after_seconds(exc)
            delay = DEFAULT_THROTTLE_DELAY_SECONDS if asked is None else asked
            if attempt == MAX_ATTEMPTS_PER_CALL:
                raise RunIncomplete(f"still throttled after {MAX_ATTEMPTS_PER_CALL} attempts") from exc
            if delay > MAX_THROTTLE_DELAY_SECONDS:
                raise RunIncomplete(
                    f"throttled, and the service asked for a {delay:.0f}s wait; "
                    f"this run waits at most {MAX_THROTTLE_DELAY_SECONDS:.0f}s"
                ) from exc
            if clock() + delay >= deadline:
                raise RunIncomplete("throttled, and waiting would pass the time limit") from exc
            source = "the service gave no delay" if asked is None else "as the service asked"
            print(f"  throttled; waiting {delay:.0f}s, {source} (attempt {attempt} of {MAX_ATTEMPTS_PER_CALL})", flush=True)
            sleep(delay)
    raise AssertionError("unreachable")


def bounded(openai_client, timeout_seconds: float):
    """The same client with a request timeout and no retries of its own. The SDK would
    otherwise retry twice by itself, hiding throttling from the limits above."""
    return openai_client.with_options(timeout=timeout_seconds, max_retries=0)


def call_agent(openai_client, reference: AgentReference, message: str, time_left: Callable[[], float]) -> str:
    """One conversation, one response, as app/foundry/client.py does it, without the retry.
    Each of the three requests gets the time left when it starts as its timeout."""
    conversation = bounded(openai_client, time_left()).conversations.create(
        items=[{"type": "message", "role": "user", "content": message}]
    )
    try:
        return _run_response(bounded(openai_client, time_left()), conversation.id, reference)
    finally:
        # The reply is already in hand and a leftover sample conversation is harmless, so
        # cleanup never fails the call, and is skipped when the run is out of time.
        try:
            cleanup_timeout = min(time_left(), CLEANUP_TIMEOUT_SECONDS)
            bounded(openai_client, cleanup_timeout).conversations.delete(conversation_id=conversation.id)
        except Exception as exc:
            print(f"  did not delete conversation {conversation.id}: {exc}", flush=True)


_GRADER_PROMPT = """You are checking an AI assistant's reply against a list of criteria.

The message the assistant received:
<message>
{message}
</message>

The assistant's reply:
<reply>
{reply}
</reply>

Criteria:
{criteria}

Return only a JSON array with one object per criterion, in order:
[{{"met": true or false, "reason": "one short sentence"}}]"""


def grade(openai_client, model: str, case: Case, raw: str, time_left: Callable[[], float]) -> list[dict]:
    """Experimental. One direct model call judges the reply against the case's rubric."""
    criteria = "\n".join(f"{number}. {text}" for number, text in enumerate(case.rubric, start=1))
    prompt = _GRADER_PROMPT.format(message=case.message, reply=raw, criteria=criteria)
    response = bounded(openai_client, time_left()).responses.create(model=model, input=prompt)
    verdicts = json.loads(_strip_markdown_fence(response.output_text))
    if len(verdicts) != len(case.rubric):
        raise ValueError(f"grader returned {len(verdicts)} verdicts for {len(case.rubric)} criteria")
    return [
        {"criterion": criterion, "met": bool(verdict["met"]), "reason": str(verdict.get("reason", ""))}
        for criterion, verdict in zip(case.rubric, verdicts)
    ]


# --- the run --------------------------------------------------------------------------------


def run_suite(
    cases: list[Case],
    ask_agent: Callable[[str, Callable[[], float]], str],
    ask_grader: Optional[Callable[[Case, str, Callable[[], float]], list[dict]]],
    max_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Runs every case in order. Returns {"result": passed | failed | incomplete, "cases": [...]}.

    ask_agent(message, time_left) and ask_grader(case, raw, time_left) ask time_left() for
    the timeout of each HTTP request they make. The time limit is checked before every call
    and again once a case is done, so a run that went over is never reported as passed."""
    deadline = clock() + max_seconds
    time_limit = f"time limit of {max_seconds / 60:.0f} minutes reached"
    results: list[dict[str, Any]] = []
    incomplete: Optional[str] = None

    for case in cases:
        entry: dict[str, Any] = {"name": case.name, "description": case.description, "message": case.message}
        results.append(entry)
        if incomplete is None and clock() >= deadline:
            incomplete = time_limit
        if incomplete is not None:
            entry["error"] = f"not run: {incomplete}"
            continue

        print(f"{case.name}", flush=True)
        try:
            raw = with_throttle_retries(lambda time_left: ask_agent(case.message, time_left), deadline, sleep, clock)
        except Exception as exc:
            incomplete = f"{case.name}: {exc}"
            entry["error"] = str(exc)
            continue
        entry["raw_output"] = raw
        entry.update(score(case, raw))

        if case.rubric and ask_grader is not None:
            try:
                entry["rubric"] = with_throttle_retries(
                    lambda time_left: ask_grader(case, raw, time_left), deadline, sleep, clock
                )
            except Exception as exc:  # advisory, so a grader problem never fails the run
                entry["rubric"] = [{"criterion": c, "met": None, "reason": f"not scored: {exc}"} for c in case.rubric]

        if clock() > deadline:
            incomplete = f"{time_limit} during {case.name}"

    if incomplete is not None:
        result = "incomplete"
    elif all(check["passed"] for entry in results for check in entry["blocking"]):
        result = "passed"
    else:
        result = "failed"
    return {"result": result, "incomplete_reason": incomplete, "cases": results}


def print_summary(report: dict[str, Any]) -> None:
    print()
    for entry in report["cases"]:
        if "error" in entry:
            print(f"ERROR  {entry['name']}: {entry['error']}")
            continue
        failed = [c for c in entry["blocking"] if not c["passed"]]
        print(f"{'FAIL' if failed else 'PASS'}   {entry['name']}")
        for check in failed:
            print(f"         blocking  {check['check']}: expected {check['expected']}  {check.get('detail', '')}".rstrip())
        for check in entry["advisory"]:
            if not check["passed"]:
                print(f"         advisory  {check['check']}: expected {check['expected']}")
        for verdict in entry.get("rubric", []):
            if verdict["met"] is not True:
                label = "not met" if verdict["met"] is False else "not scored"
                print(f"         rubric    {label}: {verdict['criterion']}  ({verdict['reason']})")
    print(
        f"\nAgent {report['agent_name']} version {report['agent_version']}: {report['result'].upper()} "
        f"in {report['duration_seconds']:.0f}s"
    )
    if report["incomplete_reason"]:
        print(f"Incomplete: {report['incomplete_reason']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", required=True, help="the exact agent version to evaluate, for example 5")
    parser.add_argument("--agent", type=Path, help="agent folder (default: the only folder in agents/)")
    parser.add_argument("--project-endpoint", help="default: PROJECT_ENDPOINT, then local.settings.json")
    parser.add_argument("--commit", help="source commit to record (default: the current git commit)")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "eval-report.json")
    parser.add_argument("--max-minutes", type=float, default=30.0, help="stop the run after this long")
    parser.add_argument("--no-rubric", action="store_true", help="skip the experimental rubric grader")
    args = parser.parse_args(argv)

    project_endpoint = args.project_endpoint or default_project_endpoint()
    if not project_endpoint:
        parser.error("no project endpoint: set PROJECT_ENDPOINT, fill in local.settings.json, or pass --project-endpoint")

    try:
        agent_folder = args.agent or default_agent_folder()
        definition = load_agent_definition(agent_folder)
        cases = load_cases(agent_folder)
        reference = AgentReference(
            project_endpoint=project_endpoint,
            agent_name=definition.name,
            agent_version=args.version,
            source_commit=args.commit or current_commit(),
        )
    except (AgentDefinitionError, ValidationError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    os.environ["PROJECT_ENDPOINT"] = project_endpoint
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    print(f"Evaluating {reference.agent_name} version {reference.agent_version}: {len(cases)} cases\n", flush=True)

    with _get_project_client().get_openai_client() as openai_client:
        report = run_suite(
            cases,
            ask_agent=lambda message, time_left: call_agent(openai_client, reference, message, time_left),
            ask_grader=None
            if args.no_rubric
            else lambda case, raw, time_left: grade(openai_client, definition.model, case, raw, time_left),
            max_seconds=args.max_minutes * 60,
        )

    report = {
        **reference.model_dump(),
        "started_at": started_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started, 1),
        **report,
    }
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(report)
    print(f"Report: {args.report}")
    return {"passed": 0, "failed": 1, "incomplete": 2}[report["result"]]


if __name__ == "__main__":
    sys.exit(main())
