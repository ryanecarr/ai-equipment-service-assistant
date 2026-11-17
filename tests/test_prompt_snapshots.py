"""Pins the complete message the agent receives, for a few representative requests.

Each test sends a request through the real /api/assess handler with seed data behind it,
captures what would be sent to the agent, and compares it with a file in tests/snapshots/.
Any change to a header, label or separator shows up here as a readable diff.

To accept an intended change, regenerate the files and review the diff:

    UPDATE_SNAPSHOTS=1 python -m pytest tests/test_prompt_snapshots.py
"""

import json
import os
from pathlib import Path

import azure.functions as func

import app.history.repository as history_repository_mod
import function_app
from app.foundry.client import compose_message
from app.schemas.history_models import SavedAssessmentRecord
from app.schemas.response_models import MaintenanceAssessment

SEED_DIR = Path(__file__).resolve().parent.parent / "seed-data"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
EQUIPMENT_MASTER = json.loads((SEED_DIR / "equipment_master.json").read_text(encoding="utf-8"))
WORK_ORDERS = json.loads((SEED_DIR / "work_orders.json").read_text(encoding="utf-8"))


def _assessment(**overrides) -> MaintenanceAssessment:
    fields = dict(
        equipment_model="CAT 320",
        equipment_id="CAT320-SE-1042",
        customer="Southeast Construction",
        reported_issue="Boom raises slowly under load",
        category="Hydraulic",
        priority="Medium",
        recommendations=["Inspect the main control valve for internal leakage."],
        additional_information_needed=[],
        requires_technician_review=True,
        related_work_orders=[],
        grounding_used=True,
    )
    return MaintenanceAssessment(**{**fields, **overrides})


def _message_sent_to_agent(monkeypatch, request_text: str) -> str:
    sent = {}

    def capture(text, grounding_block):
        sent["message"] = compose_message(text, grounding_block)
        return _assessment()

    monkeypatch.setattr(function_app, "get_assessment", capture)
    request = func.HttpRequest(
        method="POST",
        url="http://localhost/api/assess",
        headers={},
        params={},
        route_params={},
        body=json.dumps({"request_text": request_text}).encode(),
    )

    assert function_app.assess(request).status_code == 200
    return sent["message"]


def _assert_matches_snapshot(name: str, message: str) -> None:
    path = SNAPSHOT_DIR / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.write_text(message + "\n", encoding="utf-8")
    assert message + "\n" == path.read_text(encoding="utf-8")


def test_matched_machine_with_no_saved_history(monkeypatch, seed_grounding, saved_assessments):
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)

    message = _message_sent_to_agent(monkeypatch, "The boom on CAT320-SE-1042 is raising slowly again under load.")

    _assert_matches_snapshot("matched_no_history", message)


def test_request_that_matches_no_machine(monkeypatch, seed_grounding, saved_assessments):
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)

    message = _message_sent_to_agent(monkeypatch, "Our generator makes a grinding noise at startup.")

    _assert_matches_snapshot("no_match", message)


def test_matched_machine_with_saved_history(monkeypatch, seed_grounding, saved_assessments):
    # Two saved assessments: one associated with a work order, one not.
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)
    repository = history_repository_mod.get_saved_assessment_repository()
    repository.add(
        SavedAssessmentRecord(
            equipment_id="CAT320-SE-1042",
            saved_at="2026-06-10T14:05:00+00:00",
            saved_by="J. Alvarez",
            associated_work_order_id="WO-10234",
            assessment=_assessment(
                recommendations=[
                    "Inspect the main control valve for internal leakage.",
                    "Check hydraulic filter condition and fluid level.",
                ],
                related_work_orders=["WO-10234"],
            ),
        )
    )
    repository.add(
        SavedAssessmentRecord(
            equipment_id="CAT320-SE-1042",
            saved_at="2026-09-20T09:30:00+00:00",
            saved_by=None,
            associated_work_order_id=None,
            assessment=_assessment(
                reported_issue="Swing motor is noisy when the cab rotates left",
                priority="Low",
                recommendations=["Listen for the noise with the engine at idle and at full throttle."],
                grounding_used=False,
            ),
        )
    )

    message = _message_sent_to_agent(monkeypatch, "The boom on CAT320-SE-1042 is raising slowly again under load.")

    _assert_matches_snapshot("matched_with_history", message)
