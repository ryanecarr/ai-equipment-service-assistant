"""Rules for linking free text to a machine, and how /assess and /equipment expose them.

The matcher tests run on the real Cat dataset (the one production uses), and the wrong-link
cases below were all live behavior before this logic required two signals.
"""

import json

import azure.functions as func
import pytest

import app.grounding.lookup as lookup
import app.grounding.repository as repository_mod
import app.history.lookup as history_mod
import function_app
from app.errors import GroundingDataUnavailableError
from app.grounding.matching import _MAX_CANDIDATES, match_equipment
from app.schemas.history_models import SaveAssessmentRequest
from app.schemas.response_models import AssessmentResponse, MaintenanceAssessment
from scripts.migrate_grounding_to_cosmos import load_source

EQUIPMENT_MASTER, WORK_ORDERS = load_source("expanded")
RECORDS = list(EQUIPMENT_MASTER.values())
SERIAL = EQUIPMENT_MASTER["CAT320-SE-1042"]["serial_number"]


def _record(equipment_id, model, customer, serial_number=None):
    return {
        "equipment_id": equipment_id,
        "model": model,
        "customer": customer,
        "serial_number": serial_number,
        "hours": 1,
        "last_pm_date": "2026-01-01",
    }


@pytest.mark.parametrize(
    "text",
    [
        "A CAT 320 at Southeast Construction has a slow boom.",
        "A cat 320 at SOUTHEAST CONSTRUCTION has a slow boom.",
        "CAT320 at Southeast Construction has a slow boom.",
        "cat-320 at Southeast Construction has a slow boom.",
        "Caterpillar 320 at Southeast Construction has a slow boom.",
        "320 excavator at Southeast Construction has a slow boom.",
        "CAT 320 at Southeast Construction Inc has a slow boom.",
        "Southeast Construction's CAT 320 has a slow boom.",
        "A CAT  320\nat Southeast   Construction has a slow boom.",
    ],
)
def test_model_and_customer_together_link_despite_formatting(text):
    match = match_equipment(RECORDS, text)

    assert match.status == "matched"
    assert match.record["equipment_id"] == "CAT320-SE-1042"


@pytest.mark.parametrize(
    "text",
    [
        "XQ200 at Redclay Utilities won't start.",
        "XQ 200 at Redclay Utilities won't start.",
        "XQ-200 at Redclay Utilities won't start.",
        "CATERPILLAR XQ200 at Redclay Utilities won't start.",
    ],
)
def test_letter_codes_do_not_need_the_brand(text):
    match = match_equipment(RECORDS, text)

    assert match.status == "matched"
    assert match.record["equipment_id"] == "CATXQ200-RU-6045"


@pytest.mark.parametrize(
    "text",
    [
        "CAT320-SE-1042 has a slow boom.",
        "cat320-se-1042 has a slow boom.",
        "Machine (CAT320-SE-1042): slow boom.",
        f"Serial {SERIAL} has a slow boom.",
        f"serial {SERIAL.lower()} has a slow boom.",
        "CAT320-SE-1042 at Northridge Excavation has a slow boom.",  # identifier outranks the rest
    ],
)
def test_an_identifier_alone_is_enough(text):
    match = match_equipment(RECORDS, text)

    assert match.status == "matched"
    assert match.record["equipment_id"] == "CAT320-SE-1042"


@pytest.mark.parametrize(
    "text",
    [
        # The first two were wrong links in production: Southeast Construction has one
        # machine, so its name alone picked it.
        "A Komatsu PC200 at Southeast Construction has a track issue.",
        "A rented skid steer at Southeast Construction lost hydraulic pressure.",
        # CAT D6 is on one machine, owned by someone else.
        "A CAT D6 at Acme Paving has a track issue.",
        "CAT 336 hydraulic leak.",
        "Northridge Excavation called about a machine.",
        "CAT320-SE-1042-B has a slow boom.",  # a longer identifier, but it does say CAT 320
    ],
)
def test_a_single_signal_never_links_a_machine(text):
    match = match_equipment(RECORDS, text)

    assert match.record is None
    assert match.status == "needs_confirmation"
    assert match.candidates


def test_contradictory_model_and_customer_offers_both_without_linking():
    match = match_equipment(RECORDS, "A CAT D6 at Southeast Construction has a track issue.")

    assert match.record is None
    assert {c["equipment_id"] for c in match.candidates} == {"CATD6-SH-4559", "CAT320-SE-1042"}


@pytest.mark.parametrize(
    "text",
    [
        "Completely unrelated text.",
        "The CAT 3200 is leaking.",  # longer model
        "The Komatsu PC200 is leaking.",  # not in the Cat dataset
        "Pressure reads 320 psi after 140 hours on the boom.",  # bare numbers
        "A CAT 33 issue.",  # shorter model
    ],
)
def test_no_evidence_is_no_match(text):
    match = match_equipment(RECORDS, text)

    assert match.status == "no_match"
    assert match.record is None
    assert match.candidates == []


def test_bare_number_needs_a_machine_word_even_with_the_right_customer():
    record = _record("CAT140-X", "CAT 140", "Acme Grading")

    assert match_equipment([record], "Acme Grading reports 140 psi on the boom.").record is None
    assert match_equipment([record], "Acme Grading's 140 grader is down.").record == record


def test_model_numbers_get_no_typo_tolerance():
    # One character from a real, different machine; matching it would be a wrong link.
    records = [_record("CAT320-AP-1", "CAT 320", "Acme Paving"), _record("CAT330-AP-2", "CAT 330", "Acme Paving")]

    assert match_equipment(records, "A CAT 320 at Acme Paving is down.").record == records[0]
    assert match_equipment(records, "A CAT 321 at Acme Paving is down.").record is None


def test_two_machines_with_the_same_model_and_customer_need_confirmation():
    records = [_record("CAT320-AP-1", "CAT 320", "Acme Paving"), _record("CAT320-AP-2", "CAT 320", "Acme Paving")]

    match = match_equipment(records, "A CAT 320 at Acme Paving is down.")

    assert match.status == "needs_confirmation"
    assert [c["equipment_id"] for c in match.candidates] == ["CAT320-AP-1", "CAT320-AP-2"]


def test_two_identifiers_in_one_request_need_confirmation():
    match = match_equipment(RECORDS, "CAT320-SE-1042 and CAT320-NR-2207 both have slow booms.")

    assert match.status == "needs_confirmation"
    assert {c["equipment_id"] for c in match.candidates} == {"CAT320-SE-1042", "CAT320-NR-2207"}


def test_legal_suffix_on_the_stored_name_is_optional_in_the_text():
    record = _record("CAT320-AP-1", "CAT 320", "Acme Paving LLC")

    assert match_equipment([record], "A CAT 320 at Acme Paving is down.").record == record
    assert match_equipment([record], "A CAT 320 at Acme Paving, LLC is down.").record == record


def test_candidates_are_capped_and_in_a_stable_order():
    records = [_record(f"M-{i}", f"CAT {i}", "Acme Paving") for i in range(9)]

    match = match_equipment(list(reversed(records)), "Acme Paving called.")

    assert len(match.candidates) == _MAX_CANDIDATES
    assert match.candidates == sorted(match.candidates, key=lambda c: c["model"])


@pytest.mark.parametrize(
    "typed",
    [
        "Southeast Constrution",  # missing letter
        "Southeast Construcstion",  # extra letter
        "Southeast Constriction",  # wrong letter
        "Southeast Constructoin",  # swapped letters
        "Sotheast Constrution",  # one typo in each word
    ],
)
def test_one_typo_per_word_in_a_customer_name_still_links_with_the_model(typed):
    match = match_equipment(RECORDS, f"A CAT 320 at {typed} has a slow boom.")

    assert match.status == "matched"
    assert match.record["equipment_id"] == "CAT320-SE-1042"


def test_a_misspelled_customer_alone_only_offers_candidates():
    match = match_equipment(RECORDS, "Southeast Constrution called about a machine.")

    assert match.record is None
    assert [c["equipment_id"] for c in match.candidates] == ["CAT320-SE-1042"]


@pytest.mark.parametrize(
    "customer",
    [
        "Southridge Excavation",  # two letters from Northridge, and a different first letter
        "Northbridge Excavating",
        "Northwood Excavation",
        "Routheast Construction",  # one letter from Southeast, but the first letter stays exact
    ],
)
def test_similar_company_names_are_not_treated_as_typos(customer):
    records = [_record("CAT320-NR-1", "CAT 320", "Northridge Excavation"), _record("CAT320-SE-1", "CAT 320", "Southeast Construction")]

    assert match_equipment(records, f"A CAT 320 at {customer} is down.").record is None


def test_an_exact_customer_name_beats_a_near_match():
    # One letter apart, so "Acme Paving" is also a near match for the other customer.
    records = [_record("CAT320-AP-1", "CAT 320", "Acme Pacing"), _record("CAT320-AP-2", "CAT 320", "Acme Paving")]

    assert match_equipment(records, "A CAT 320 at Acme Paving is down.").record == records[1]


def test_short_words_and_words_with_digits_get_no_typo_tolerance():
    short_word = _record("CAT320-BH-1", "CAT 320", "Blue Heron Sitework")
    with_digits = _record("CAT330-R1-1", "CAT 330", "Route66 Paving")

    assert match_equipment([short_word], "A CAT 320 at Blur Heron Sitework is down.").record is None
    assert match_equipment([with_digits], "A CAT 330 at Route67 Paving is down.").record is None


def test_one_word_customer_names_get_no_typo_tolerance():
    record = _record("CAT320-AC-1", "CAT 320", "Ironbark")

    assert match_equipment([record], "A CAT 320 at Ironbrak is down.").record is None


def test_every_real_machine_is_reachable_by_its_model_and_customer():
    for record in RECORDS:
        match = match_equipment(RECORDS, f"The {record['model']} at {record['customer']} is down.")
        assert match.status == "matched", record["equipment_id"]
        assert match.record == record


class FakeAgent:
    def __init__(self):
        self.grounding_blocks = []

    def __call__(self, request_text, grounding_block):
        self.grounding_blocks.append(grounding_block)
        return MaintenanceAssessment(
            equipment_model="x",
            reported_issue="x",
            category="Other",
            priority="Low",
            recommendations=[],
            additional_information_needed=[],
            requires_technician_review=False,
            related_work_orders=[],
            grounding_used=True,
        )


@pytest.fixture
def agent(monkeypatch, seed_grounding):
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))
    fake = FakeAgent()
    monkeypatch.setattr(function_app, "get_assessment", fake)
    return fake


def _post_assess(body: dict) -> dict:
    response = function_app.assess(
        func.HttpRequest(
            method="POST",
            url="http://localhost/api/assess",
            headers={},
            params={},
            route_params={},
            body=json.dumps(body).encode(),
        )
    )
    assert response.status_code == 200
    return json.loads(response.get_body())


def _get_equipment(method: str = "GET") -> func.HttpResponse:
    return function_app.equipment(
        func.HttpRequest(method=method, url="http://localhost/api/equipment", headers={}, params={}, route_params={}, body=b"")
    )


def test_assess_reports_a_confirmed_match(agent):
    data = _post_assess({"request_text": "A CAT 320 at Southeast Construction is down."})

    assert data["match_status"] == "matched"
    assert data["equipment_id"] == "CAT320-SE-1042"
    assert data["equipment_candidates"] == []


def test_assess_keeps_candidates_out_of_the_answer_and_the_prompt(agent):
    data = _post_assess({"request_text": "A Komatsu PC200 at Southeast Construction has a track issue."})

    assert data["match_status"] == "needs_confirmation"
    assert data["equipment_id"] is None
    assert data["customer"] is None
    assert data["grounding_used"] is False
    assert [c["equipment_id"] for c in data["equipment_candidates"]] == ["CAT320-SE-1042"]
    assert agent.grounding_blocks[0].startswith(lookup._NO_RECORDS_MESSAGE)
    assert "CAT320-SE-1042" not in agent.grounding_blocks[0]


def test_candidates_expose_only_the_summary_fields(agent):
    data = _post_assess({"request_text": "Southeast Construction called."})

    assert set(data["equipment_candidates"][0]) == {"equipment_id", "model", "customer", "location"}


def test_assess_reports_no_match(agent):
    data = _post_assess({"request_text": "Something unrelated."})

    assert data["match_status"] == "no_match"
    assert data["equipment_candidates"] == []


def test_unknown_explicit_id_is_no_match_without_a_catalog_scan(agent):
    data = _post_assess({"request_text": "A CAT 320 at Southeast Construction.", "equipment_id": "NOT-REAL"})

    assert data["match_status"] == "no_match"
    assert "query_items" not in repository_mod._repository._equipment.call_names()


def test_explicit_id_is_exact_and_ignores_contradicting_text(agent):
    data = _post_assess({"request_text": "A Komatsu at Northridge Excavation.", "equipment_id": "CAT320-SE-1042"})

    assert data["match_status"] == "matched"
    assert data["equipment_id"] == "CAT320-SE-1042"


def test_matching_fields_are_not_saved_with_an_assessment():
    response = AssessmentResponse(
        equipment_model="x",
        equipment_id="CAT320-SE-1042",
        reported_issue="x",
        category="Other",
        priority="Low",
        recommendations=[],
        additional_information_needed=[],
        requires_technician_review=False,
        related_work_orders=[],
        grounding_used=True,
        match_status="matched",
    )

    # The UI posts the whole /assess response back when saving.
    saved = SaveAssessmentRequest.model_validate({"assessment": response.model_dump()})

    assert "match_status" not in saved.model_dump()["assessment"]
    assert "equipment_candidates" not in saved.model_dump()["assessment"]


def test_the_agent_cannot_set_matching_fields():
    parsed = MaintenanceAssessment.model_validate(
        {
            "equipment_model": "x",
            "reported_issue": "x",
            "category": "Other",
            "priority": "Low",
            "recommendations": [],
            "additional_information_needed": [],
            "requires_technician_review": False,
            "related_work_orders": [],
            "grounding_used": True,
            "match_status": "matched",
        }
    )

    assert "match_status" not in parsed.model_dump()


def test_equipment_list_is_sorted_summaries_without_serial_numbers(agent):
    response = _get_equipment()
    summaries = json.loads(response.get_body())

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert len(summaries) == len(RECORDS)
    assert set(summaries[0]) == {"equipment_id", "model", "customer", "location"}
    keys = [(s["customer"], s["model"], s["equipment_id"]) for s in summaries]
    assert keys == sorted(keys)
    assert "serial_number" not in response.get_body().decode()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_equipment_list_rejects_other_methods(method):
    response = _get_equipment(method)

    assert response.status_code == 405
    assert json.loads(response.get_body())["error"]["code"] == "method_not_allowed"


def test_equipment_list_store_failure_uses_the_error_envelope(monkeypatch):
    def failing():
        raise GroundingDataUnavailableError("down", user_message="Equipment records are unavailable.")

    monkeypatch.setattr(function_app, "list_equipment_summaries", failing)
    response = _get_equipment()

    assert response.status_code == 503
    assert json.loads(response.get_body())["error"]["code"] == "grounding_data_unavailable"


def test_equipment_list_unexpected_failure_is_a_generic_500(monkeypatch):
    def failing():
        raise RuntimeError("internal detail")

    monkeypatch.setattr(function_app, "list_equipment_summaries", failing)
    response = _get_equipment()

    assert response.status_code == 500
    assert "internal detail" not in response.get_body().decode()
