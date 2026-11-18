"""The rules for reviewing a candidate agent version that was evaluated more than once.

Reads evaluation reports written by scripts/evaluate_agent.py: the candidate's, and the
last release's, which are the baselines. Pure functions: nothing is run and nothing is
called. scripts/extra_evaluations.py makes the runs and uses these rules.

A blocking assertion that failed in any report stops the release, so the rules below are
about advisory assertions and rubric lines, which never stop one. They produce a
recommendation for the reviewer who approves the deployment. Each check gets a class from
the baselines:

  known gap      failed in at least one baseline that scored it
  baseline pass  passed in every baseline that scored it, and at least one did
  no baseline    no baseline scored it

"Scored" means a report holds a pass or a fail for the check. A case that did not run and
a rubric line the grader could not score are no evidence either way. A new failure is a
baseline-pass check that fails in a candidate report. It counts wherever it was scored,
including in a run that later ended incomplete.

  Finding in the candidate's reports             Recommendation
  A blocking assertion failed                    reject
  A new failure in two or more reports           reject
  A new failure in exactly one report            a tie-break run decides (below)
  A check with no baseline failed                none
  Fewer than three evaluations finished          none
  A known gap failed                             listed; changes nothing
  None of the above                              approve

  Tie-break run                                              Recommendation
  The disputed check failed again                            reject
  Another new failure appeared                               reject
  A finished run scored the disputed check and it passed     cleared
  Anything else                                              none
"""

import html
import json
from typing import Any, Optional

Report = dict[str, Any]

COMPLETE = ("passed", "failed")
RUNS_NEEDED = 3
# GitHub shows at most 1 MiB of summary for one step.
MAX_SUMMARY_CHARACTERS = 900_000


def _label(case: str, check: dict[str, Any]) -> str:
    return f"{case} / {check['check']} = {json.dumps(check['expected'], sort_keys=True)}"


def blocking_failures(report: Report) -> list[str]:
    """Every blocking assertion that was scored and failed, whatever the run's result: a
    run that ends incomplete after a failure was scored still holds that failure."""
    failures = []
    for case in report["cases"]:
        for check in case.get("blocking", []):
            if not check["passed"]:
                detail = f" ({check['detail']})" if check.get("detail") else ""
                failures.append(_label(case["name"], check) + detail)
    return failures


def results(report: Report, sections: tuple[str, ...] = ("blocking", "advisory", "rubric")) -> dict[str, Optional[bool]]:
    """{check: True, False or None} for the cases that ran. None is a rubric line the grader
    left unscored. An assertion is named without its section, so one that moved between
    blocking and advisory keeps its baseline."""
    found: dict[str, Optional[bool]] = {}
    for case in report["cases"]:
        for section in sections:
            for check in case.get(section, []):
                if section == "rubric":
                    found[f"{case['name']} / rubric: {check['criterion']}"] = check["met"]
                else:
                    found[_label(case["name"], check)] = check["passed"]
    return found


def classify(check: str, baselines: dict[str, dict[str, Optional[bool]]]) -> str:
    scored = [table[check] for table in baselines.values() if table.get(check) is not None]
    if not scored:
        return "no baseline"
    return "baseline pass" if all(scored) else "known gap"


def _state(table: dict[str, Optional[bool]], check: str) -> str:
    if check not in table:
        return "not run"
    return {True: "pass", False: "FAIL", None: "not scored"}[table[check]]


def review(
    baselines: dict[str, Report], runs: dict[str, Report], tie_break: Optional[dict[str, Report]] = None
) -> dict[str, Any]:
    """Applies the rules in this module's description. Every argument maps a report's name
    to the report. `runs` holds the three evaluations and their retries. `tie_break` holds
    the tie-break run and its retry: None until a tie-break was started, and empty if it
    wrote no report.

    The verdict is "reject", "none", "approve", or "tie-break" when a tie-break run is
    needed before anything can be recommended."""
    tie_reports = tie_break or {}
    reports = {**runs, **tie_reports}
    baseline_results = {name: results(report) for name, report in baselines.items()}
    shown = {name: results(report) for name, report in reports.items()}
    reviewed = {name: results(report, ("advisory", "rubric")) for name, report in reports.items()}
    checks = sorted({check for table in (*baseline_results.values(), *shown.values()) for check in table})
    classes = {check: classify(check, baseline_results) for check in checks}

    failed = {check: [name for name, table in reviewed.items() if table.get(check) is False] for check in checks}
    new_failures = {check: names for check, names in failed.items() if names and classes[check] == "baseline pass"}

    reject = [
        f"A blocking assertion failed in {name}: {failure}"
        for name, report in reports.items()
        for failure in blocking_failures(report)
    ]
    disputed = []
    for check, names in new_failures.items():
        if len(names) >= 2:
            reject.append(f"New failure in {len(names)} reports ({', '.join(names)}): {check}")
        elif names[0] in tie_reports:
            reject.append(f"New failure in the tie-break ({names[0]}): {check}")
        else:
            cleared_by = next(
                (
                    name
                    for name, report in tie_reports.items()
                    if report["result"] in COMPLETE and reviewed[name].get(check) is True
                ),
                None,
            )
            disputed.append({"check": check, "failed_in": names[0], "cleared_by": cleared_by})

    undecided = []
    finished = [name for name, report in runs.items() if report["result"] in COMPLETE]
    if len(finished) < RUNS_NEEDED:
        undecided.append(f"Only {len(finished)} of the {RUNS_NEEDED} evaluations finished")
    undecided += [
        f"No baseline scored a check that failed in {', '.join(names)}: {check}"
        for check, names in failed.items()
        if names and classes[check] == "no baseline"
    ]
    unsettled = [entry for entry in disputed if entry["cleared_by"] is None]
    needs_tie_break = bool(unsettled) and tie_break is None and not reject and not undecided
    for entry in unsettled:
        if needs_tie_break:
            undecided.append(f"New failure seen once, in {entry['failed_in']}; a tie-break run decides: {entry['check']}")
        elif tie_break is None:
            undecided.append(f"New failure seen once, in {entry['failed_in']}, and not put to a tie-break: {entry['check']}")
        else:
            undecided.append(f"The tie-break did not settle a new failure seen once, in {entry['failed_in']}: {entry['check']}")

    if reject:
        verdict = "reject"
    elif needs_tie_break:
        verdict = "tie-break"
    else:
        verdict = "none" if undecided else "approve"

    return {
        "verdict": verdict,
        "reasons": reject + undecided,
        "new_failures": [{"check": check, "failed_in": names} for check, names in new_failures.items()],
        "disputed": disputed,
        "known_gaps": [
            {
                "check": check,
                "baselines": {name: _state(table, check) for name, table in baseline_results.items()},
                "reports": {name: _state(table, check) for name, table in shown.items()},
            }
            for check in checks
            if classes[check] == "known gap"
        ],
        # Rubric lines the grader could not score. The rules count them once a person has read the reply.
        "to_read": [
            {"report": name, "check": check, "class": classes[check]}
            for name, table in reviewed.items()
            for check, value in table.items()
            if value is None
        ],
        "classes": {name: sum(1 for value in classes.values() if value == name) for name in ("baseline pass", "known gap", "no baseline")},
    }


# --- the summary a reviewer reads --------------------------------------------------------------


def _cell(text: Any) -> str:
    return html.escape(str(text)).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows] + [""]


def _replies(reports: dict[str, Report]) -> list[str]:
    lines = ["### Replies", "", "Every reply as it arrived, with the grader's reading of each rubric line.", ""]
    for name, report in reports.items():
        for case in report["cases"]:
            title = f"{_cell(name)} / {_cell(case['name'])}"
            if "raw_output" not in case:
                lines += [f"{title}: not run ({_cell(case.get('error', ''))})", ""]
                continue
            lines += [f"<details><summary>{title}</summary>", "", f"<pre>{html.escape(case['raw_output'])}</pre>", ""]
            for line in case.get("rubric", []):
                state = {True: "met", False: "NOT MET", None: "not scored"}[line["met"]]
                lines.append(f"- {state}: {_cell(line['criterion'])} ({_cell(line.get('reason', ''))})")
            lines += ["", "</details>", ""]
    return lines


_HEADLINES = {
    "approve": "Recommendation: approve.",
    "reject": "Recommendation: reject.",
    "none": "No recommendation.",
    "tie-break": "No recommendation yet: a tie-break run is needed.",
}


def render(
    result: dict[str, Any],
    baselines: dict[str, Report],
    runs: dict[str, Report],
    tie_break: Optional[dict[str, Report]] = None,
    stopped: Optional[str] = None,
    no_report: tuple[str, ...] = (),
) -> str:
    """The review as Markdown. `stopped` is why the release stopped, if it did. `no_report`
    names the attempts that ended without writing a report."""
    reports = {**runs, **(tie_break or {})}
    first = next(iter(runs.values()))
    to_read = [entry for entry in result["to_read"] if entry["class"] != "known gap"]

    lines = ["## Agent evaluation review", "", f"**{_HEADLINES[result['verdict']]}**", ""]
    if stopped:
        lines += [f"**The release stops here:** {_cell(stopped)}", ""]
    lines += [f"- {_cell(reason)}" for reason in result["reasons"]]
    if result["verdict"] == "approve":
        lines.append(
            f"- No blocking assertion failed, and no check the last release passes failed in the candidate's "
            f"{len(reports)} reports."
        )
        if to_read:
            lines.append(
                "- This holds only if every rubric line under \"Rubric lines to judge by reading\" is met "
                f"({len(to_read)} to read). One that is not met is a new failure."
            )
    lines += [
        "",
        "Blocking assertions stop a release. Advisory assertions and rubric lines never do: this recommendation is "
        "for the reviewer who approves Deploy. The rules are in `README.md`, \"Agent evaluation\".",
        "",
        f"Candidate: `{_cell(first['agent_name'])}` version **{_cell(first['agent_version'])}** from commit "
        f"`{_cell(first['source_commit'])}`, in `{_cell(first['project_endpoint'])}`.",
        "",
    ]
    if baselines:
        described = [
            f"`{_cell(name)}` (version {_cell(report['agent_version'])}, {_cell(report['result'])})"
            for name, report in baselines.items()
        ]
        lines += [f"Baselines, the last release's reports: {', '.join(described)}.", ""]
    else:
        lines += ["Baselines: none. Every check that fails has no baseline.", ""]
    counts = result["classes"]
    lines += [
        f"Checks: {counts['baseline pass']} that the last release passes, {counts['known gap']} known gaps, "
        f"{counts['no baseline']} with no baseline.",
        "",
    ]

    rows = []
    for name, report in reports.items():
        ran = sum(1 for case in report["cases"] if "raw_output" in case)
        result_text = report["result"] + (f": {report['incomplete_reason']}" if report.get("incomplete_reason") else "")
        rows.append(
            [name, report["started_at"][:19].replace("T", " "), f"{report['duration_seconds']:.0f}", result_text,
             f"{ran} of {len(report['cases'])}", len(blocking_failures(report))]
        )
    lines += _table(["Report", "Started (UTC)", "Seconds", "Result", "Cases run", "Blocking failures"], rows)
    lines += [f"{_cell(name)} ended without writing a report." for name in no_report] + ([""] if no_report else [])

    if result["new_failures"]:
        lines += ["### New failures", "", "Checks the last release passes that failed in the candidate.", ""]
        cleared = {entry["check"]: entry["cleared_by"] for entry in result["disputed"]}
        rows = [
            [entry["check"], ", ".join(entry["failed_in"]), f"cleared by {cleared[entry['check']]}" if cleared.get(entry["check"]) else ""]
            for entry in result["new_failures"]
        ]
        lines += _table(["Check", "Failed in", "Tie-break"], rows)

    if result["known_gaps"]:
        lines += [
            "### Known gaps",
            "",
            "Checks that failed in a baseline. They never change the recommendation. If this change is meant to "
            "close one, it should pass in every report: that is the reviewer's call, because the workflow cannot "
            "know what a change is for.",
            "",
        ]
        names = [*baselines, *reports]
        rows = [
            [gap["check"], *gap["baselines"].values(), *gap["reports"].values()] for gap in result["known_gaps"]
        ]
        lines += _table(["Check", *names], rows)

    if result["to_read"]:
        lines += [
            "### Rubric lines to judge by reading",
            "",
            "The grader could not score these. Read the reply under \"Replies\", for the same report and case, and "
            "decide whether the line is met.",
            "",
        ]
        lines += [f"- {_cell(entry['report'])}, {_cell(entry['class'])}: {_cell(entry['check'])}" for entry in result["to_read"]]
        lines.append("")

    body = "\n".join(lines)
    replies = "\n".join(_replies(reports))
    if len(body) + len(replies) > MAX_SUMMARY_CHARACTERS:
        replies = "### Replies\n\nToo long to show here. Every reply is in the report files of this run's artifacts.\n"
    return body + "\n" + replies
