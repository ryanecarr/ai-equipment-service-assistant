"""Evaluates a candidate agent version twice more and reviews the three runs.

    python -m scripts.extra_evaluations --reference agent_reference.json \
        --first-report eval-report.json --baselines eval-baselines --output-dir eval-extra

For the release workflow, when the agent definition changed (scripts/agent_change.py). It
creates nothing and deploys nothing: it only runs scripts.evaluate_agent.

Inputs:  the candidate's agent reference, the report of its first evaluation, the last
         release's reports (--baselines, a folder that may be missing), and Azure sign-in.
Output:  --output-dir holds one report for every attempt, named eval-report-<attempt>.json,
         plus eval-review.md and eval-review.json. --summary appends the review to a file.
Exit:    0 if the release may be offered for approval, whatever the review recommends;
         1 if a blocking assertion failed in any attempt;
         2 if an evaluation could not finish in two attempts, or a report is not the
         candidate's.

Every run evaluates the version, commit and project of the first report. Runs are made
one at a time with a pause before each, because they share the application's model
capacity. A run that ends incomplete is tried once more, under its own report name, so
no report is ever overwritten. A blocking failure ends everything at once: it is never
retried away, even if that run went on to end incomplete. The review rules, and the
tie-break run they can ask for, are in scripts/review_evaluations.py.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.agent_definition import REPO_ROOT
from scripts.review_evaluations import COMPLETE, Report, blocking_failures, render, review

SECONDS_BETWEEN_RUNS = 300
IDENTITY = ("project_endpoint", "agent_name", "agent_version", "source_commit")


class Stop(Exception):
    """The release cannot go on. Carries the exit code."""

    def __init__(self, exit_code: int, reason: str):
        super().__init__(reason)
        self.exit_code = exit_code


def _read(path: Path) -> Optional[Report]:
    """The report at `path`, or None if the evaluation ended without writing a usable one."""
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) and "cases" in report and "result" in report else None


def run_extra_evaluations(
    first: Report,
    baselines: dict[str, Report],
    evaluate: Callable[[Path], int],
    output_dir: Path,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Makes the runs and returns {"exit_code", "stopped", "review", "runs", "tie_break",
    "attempts"}. `evaluate(report_path)` runs one evaluation of the candidate, writes its
    report to that path and returns the evaluator's exit code."""
    runs: dict[str, Report] = {"run-1": first}
    tie_break: Optional[dict[str, Report]] = None
    attempts: list[dict[str, Any]] = []

    def attempt(name: str, into: dict[str, Report]) -> Optional[Report]:
        path = output_dir / f"eval-report-{name}.json"
        if path.exists():
            raise Stop(2, f"{path} already exists, and a report is never overwritten")
        print(f"\n{name}: waiting {SECONDS_BETWEEN_RUNS} seconds since the last run, then evaluating", flush=True)
        sleep(SECONDS_BETWEEN_RUNS)
        exit_code = evaluate(path)
        report = _read(path)
        attempts.append({"name": name, "exit_code": exit_code, "result": report["result"] if report else "no report"})
        if report is None:
            print(f"{name}: ended with exit code {exit_code} and no report", flush=True)
            return None
        different = [key for key in IDENTITY if report.get(key) != first[key]]
        if different:
            raise Stop(2, f"{path.name} is not a report of this candidate: {', '.join(different)} differ")
        into[name] = report
        failures = blocking_failures(report)
        if failures:
            raise Stop(1, f"A blocking assertion failed in {name}: {'; '.join(failures)}")
        return report

    def finished(report: Optional[Report]) -> bool:
        return report is not None and report["result"] in COMPLETE

    try:
        failures = blocking_failures(first)
        if failures:
            raise Stop(1, f"A blocking assertion failed in run-1: {'; '.join(failures)}")
        if first["result"] not in COMPLETE:
            raise Stop(2, "The first evaluation did not finish")
        output_dir.mkdir(parents=True, exist_ok=True)

        for name in ("run-2", "run-3"):
            if not finished(attempt(name, runs)) and not finished(attempt(f"{name}-retry", runs)):
                raise Stop(2, f"{name} did not finish in two attempts")

        if review(baselines, runs)["verdict"] == "tie-break":
            tie_break = {}
            for name in ("tie-break", "tie-break-retry"):
                report = attempt(name, tie_break)
                # Settled either way, or a finished run that could not settle it: no further run helps.
                if review(baselines, runs, tie_break)["verdict"] in ("reject", "approve") or finished(report):
                    break
            else:
                raise Stop(2, "The tie-break run did not finish in two attempts")
        exit_code, stopped = 0, None
    except Stop as stop:
        exit_code, stopped = stop.exit_code, str(stop)

    return {
        "exit_code": exit_code,
        "stopped": stopped,
        "review": review(baselines, runs, tie_break),
        "runs": runs,
        "tie_break": tie_break,
        "attempts": attempts,
    }


def _annotation(outcome: dict[str, Any]) -> str:
    """One line GitHub shows at the top of the run."""
    verdict, reasons = outcome["review"]["verdict"], outcome["review"]["reasons"]
    to_read = [entry for entry in outcome["review"]["to_read"] if entry["class"] != "known gap"]
    if outcome["stopped"]:
        line = f"::error title=Agent evaluation::{outcome['stopped']}"
    elif verdict == "approve" and to_read:
        line = (
            "::warning title=Agent evaluation review::Recommendation: approve, but only if the rubric lines the "
            f"grader could not score are met ({len(to_read)} to read). Read the review in this run's summary."
        )
    elif verdict == "approve":
        line = "::notice title=Agent evaluation review::Recommendation: approve. Read the review in this run's summary."
    else:
        headline = "Recommendation: reject." if verdict == "reject" else "No recommendation."
        line = f"::warning title=Agent evaluation review::{headline} {reasons[0]} Read the review in this run's summary."
    return line.replace("\r", " ").replace("\n", " ")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference", type=Path, required=True, help="the candidate's agent_reference.json")
    parser.add_argument("--first-report", type=Path, required=True, help="the report of the candidate's first evaluation")
    parser.add_argument("--baselines", type=Path, required=True, help="folder of the last release's eval-report*.json files")
    parser.add_argument("--release", default="", help="the release the baselines came from, to name them in the review")
    parser.add_argument("--output-dir", type=Path, required=True, help="where the reports and the review go")
    parser.add_argument("--summary", type=Path, help="a file to append the review to, for example $GITHUB_STEP_SUMMARY")
    args = parser.parse_args(argv)

    try:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        first = json.loads(args.first_report.read_text(encoding="utf-8"))
        different = [key for key in IDENTITY if first.get(key) != reference[key]]
        if different:
            raise ValueError(f"{args.first_report} is not a report of the candidate in {args.reference}: {', '.join(different)} differ")
        baseline_files = sorted(args.baselines.glob("eval-report*.json")) if args.baselines.is_dir() else []
        baselines = {f"{args.release} {path.name}".strip(): json.loads(path.read_text(encoding="utf-8")) for path in baseline_files}
    except (OSError, ValueError, KeyError) as exc:
        print(f"::error title=Agent evaluation::{exc}")
        return 2

    def evaluate(report_path: Path) -> int:
        command = [sys.executable, "-m", "scripts.evaluate_agent", "--report", str(report_path.resolve())]
        command += ["--version", first["agent_version"], "--commit", first["source_commit"]]
        command += ["--project-endpoint", first["project_endpoint"]]
        return subprocess.run(command, cwd=REPO_ROOT).returncode

    outcome = run_extra_evaluations(first, baselines, evaluate, args.output_dir)

    no_report = tuple(entry["name"] for entry in outcome["attempts"] if entry["result"] == "no report")
    markdown = render(outcome["review"], baselines, outcome["runs"], outcome["tie_break"], outcome["stopped"], no_report)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "eval-review.md").write_text(markdown, encoding="utf-8")
    record = {
        **{key: first[key] for key in IDENTITY},
        "baselines": list(baselines),
        "exit_code": outcome["exit_code"],
        "stopped": outcome["stopped"],
        "attempts": [{"name": "run-1", "exit_code": 0, "result": first["result"]}, *outcome["attempts"]],
        **outcome["review"],
    }
    (args.output_dir / "eval-review.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(markdown + "\n")

    print(f"\nReview: {outcome['review']['verdict']}")
    for reason in outcome["review"]["reasons"]:
        print(f"  {reason}")
    print(_annotation(outcome))
    return outcome["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
