"""Regression tests for function_app.assess()'s anti-hallucination field scrubbing and
recommendation-formatting cleanup.

Seeds the repository with a fake container (same pattern as test_grounding_lookup.py),
monkeypatches app.history.lookup.recent_saved_assessments, and monkeypatches
function_app.get_assessment so no Azure/Foundry access is needed.
"""

import json

import azure.functions as func

import app.history.lookup as history_mod
import function_app
from app.schemas.response_models import MaintenanceAssessment


def _request(body: dict) -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/assess",
        headers={},
        params={},
        route_params={},
        body=json.dumps(body).encode(),
    )


def test_unmatched_equipment_forces_grounding_used_false_and_clears_comparison_notes(monkeypatch, seed_grounding):
    # No equipment_id, and request text that matches nothing — grounding must fail.
    seed_grounding({})
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))

    def fake_get_assessment(request_text, grounding_block):
        # A schema-valid but dishonest agent response: claims grounding and a history
        # comparison despite no grounding/history ever being supplied in the prompt.
        return MaintenanceAssessment(
            equipment_model="CAT 320",
            reported_issue="x",
            category="Hydraulic",
            priority="Medium",
            recommendations=[],
            additional_information_needed=[],
            requires_technician_review=False,
            related_work_orders=[],
            grounding_used=True,
            comparison_notes="Same failure as the previously saved assessment.",
        )

    monkeypatch.setattr(function_app, "get_assessment", fake_get_assessment)

    resp = function_app.assess(_request({"request_text": "Something broke."}))
    data = json.loads(resp.get_body())

    assert data["equipment_id"] is None
    assert data["customer"] is None
    assert data["grounding_used"] is False
    assert data["comparison_notes"] is None


def test_matched_equipment_without_saved_history_clears_fabricated_comparison_notes(monkeypatch, seed_grounding):
    seed_grounding(
        {
            "CAT-A": {
                "equipment_id": "CAT-A",
                "model": "CAT 320",
                "customer": "Southeast Construction",
                "hours": 100,
                "last_pm_date": "2026-01-01",
            },
        }
    )
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))

    def fake_get_assessment(request_text, grounding_block):
        return MaintenanceAssessment(
            equipment_model="CAT 320",
            reported_issue="x",
            category="Hydraulic",
            priority="Medium",
            recommendations=[],
            additional_information_needed=[],
            requires_technician_review=False,
            related_work_orders=[],
            grounding_used=True,
            comparison_notes="This matches a previously saved assessment.",
        )

    monkeypatch.setattr(function_app, "get_assessment", fake_get_assessment)

    resp = function_app.assess(_request({"request_text": "CAT 320 issue", "equipment_id": "CAT-A"}))
    data = json.loads(resp.get_body())

    assert data["equipment_id"] == "CAT-A"
    # Real equipment grounding did occur — must not be forced off just because
    # there's no saved history for it.
    assert data["grounding_used"] is True
    # But no saved history was actually supplied, so the agent's comparison claim
    # must be cleared regardless of what it said.
    assert data["comparison_notes"] is None


def test_leading_bullet_markers_are_stripped_from_recommendations(monkeypatch, seed_grounding):
    # The UI renders each recommendations entry inside its own <li>; an agent-supplied
    # leading "-"/"*"/"•" produces a doubled bullet on screen. function_app.assess()
    # must strip these regardless of whether the agent followed the no-bullets
    # instruction, since free-text generation isn't guaranteed to comply every time.
    # Regression: a bare "-" with no following whitespace is a negative number's sign
    # (e.g. "-10°C"), not a bullet, and must survive untouched.
    seed_grounding({})
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))

    def fake_get_assessment(request_text, grounding_block):
        return MaintenanceAssessment(
            equipment_model="CAT 320",
            reported_issue="x",
            category="Hydraulic",
            priority="Medium",
            recommendations=[
                "- Check hydraulic fluid level",
                "* Inspect filter for debris",
                "Already plain text",
                "-10°C ambient temperature may affect sensor readings",
            ],
            additional_information_needed=[],
            requires_technician_review=False,
            related_work_orders=[],
            grounding_used=False,
        )

    monkeypatch.setattr(function_app, "get_assessment", fake_get_assessment)

    resp = function_app.assess(_request({"request_text": "Something broke."}))
    data = json.loads(resp.get_body())

    assert data["recommendations"] == [
        "Check hydraulic fluid level",
        "Inspect filter for debris",
        "Already plain text",
        # No whitespace between "-" and "10" — this is a negative number's sign, not a
        # bullet marker, and must be left untouched.
        "-10°C ambient temperature may affect sensor readings",
    ]
