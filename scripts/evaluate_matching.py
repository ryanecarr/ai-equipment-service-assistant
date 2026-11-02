"""Scores the free-text matcher against labelled requests. No Azure access.

    python -m scripts.evaluate_matching

Each case in tests/matching_eval_cases.json names the machine the request is really
about, or null if it isn't in the catalog. The outcome is worked out from that, so a
wrong link can't be labelled away. Exits non-zero if anything was linked wrongly.
"""

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from app.grounding.matching import match_equipment
from scripts.migrate_grounding_to_cosmos import load_source

_CASES_PATH = Path(__file__).resolve().parent.parent / "tests" / "matching_eval_cases.json"


def load_cases() -> list[dict[str, Any]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def load_catalog() -> list[dict[str, Any]]:
    equipment_master, _ = load_source("expanded")
    return list(equipment_master.values())


def outcome_for(records: list[dict[str, Any]], text: str, machine: Optional[str]) -> str:
    match = match_equipment(records, text)

    if match.record is not None:
        return "linked" if match.record["equipment_id"] == machine else "wrong_link"
    if machine is None:
        return "correctly_unlinked"
    return "confirmable" if machine in {c["equipment_id"] for c in match.candidates} else "missed"


def main() -> int:
    records = load_catalog()
    cases = load_cases()
    counts: Counter[str] = Counter()

    for case in cases:
        outcome = outcome_for(records, case["text"], case["machine"])
        counts[outcome] += 1
        if outcome in ("wrong_link", "missed"):
            print(f"{outcome.upper():10} {case['text']}")

    real_machine = sum(1 for case in cases if case["machine"] is not None)
    print(f"\n{len(cases)} requests against {len(records)} machines")
    print(f"  Wrong machine linked:      {counts['wrong_link']}")
    print(f"  Linked correctly:          {counts['linked']} of {real_machine} about a real machine")
    print(f"  Offered for confirmation:  {counts['confirmable']}")
    print(f"  Missed:                    {counts['missed']}")
    print(f"  Correctly left unlinked:   {counts['correctly_unlinked']} of {len(cases) - real_machine} with no such machine")
    return 1 if counts["wrong_link"] else 0


if __name__ == "__main__":
    sys.exit(main())
