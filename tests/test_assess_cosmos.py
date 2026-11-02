"""/assess and /assessments behavior when Cosmos DB fails or returns bad records — above
all, that a database problem is never turned into an ungrounded agent call."""

import json
import logging

import azure.functions as func
import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from azure.cosmos.exceptions import CosmosHttpResponseError

import app.grounding.lookup as grounding_mod
import app.grounding.repository as repository_mod
import app.history.lookup as history_mod
import function_app
from app.errors import FoundryCallError, GroundingDataUnavailableError
from app.grounding.repository import EquipmentRepository
from app.schemas.response_models import MaintenanceAssessment
from cosmos_fakes import FakeContainer

EQUIPMENT_DOC = {
    "id": "CAT-A",
    "equipment_id": "CAT-A",
    "model": "CAT 320",
    "customer": "Southeast Construction",
    "hours": 100,
    "last_pm_date": "2026-01-01",
}
WORK_ORDER_DOC = {
    "id": "WO-1",
    "equipment_id": "CAT-A",
    "source_order": 0,
    "work_order_id": "WO-1",
    "date": "2026-01-01",
    "reported_issue": "x",
    "resolution": "y",
    "technician_notes": "z",
}


def _assessment(**overrides) -> MaintenanceAssessment:
    fields = dict(
        equipment_model="CAT 320",
        reported_issue="x",
        category="Hydraulic",
        priority="Medium",
        recommendations=[],
        additional_information_needed=[],
        requires_technician_review=False,
        related_work_orders=[],
        grounding_used=True,
    )
    return MaintenanceAssessment(**{**fields, **overrides})


def _request(method: str, url: str, body=None, params=None) -> func.HttpRequest:
    return func.HttpRequest(
        method=method,
        url=url,
        headers={},
        params=params or {},
        route_params={},
        body=json.dumps(body).encode() if body is not None else b"",
    )


def _post_assess(body: dict) -> func.HttpResponse:
    return function_app.assess(_request("POST", "http://localhost/api/assess", body))


class FakeAgent:
    def __init__(self, assessment=None, error=None):
        self.assessment = assessment or _assessment()
        self.error = error
        self.grounding_blocks = []

    def __call__(self, request_text, grounding_block):
        self.grounding_blocks.append(grounding_block)
        if self.error is not None:
            raise self.error
        return self.assessment


@pytest.fixture
def containers(monkeypatch):
    equipment = FakeContainer("equipment", documents=(EQUIPMENT_DOC,))
    work_orders = FakeContainer("work-orders", documents=(WORK_ORDER_DOC,))
    monkeypatch.setattr(repository_mod, "_repository", EquipmentRepository(equipment, work_orders))
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))
    return equipment, work_orders


@pytest.fixture
def agent(monkeypatch):
    fake = FakeAgent()
    monkeypatch.setattr(function_app, "get_assessment", fake)
    return fake


def _error(response: func.HttpResponse) -> dict:
    return json.loads(response.get_body())["error"]


@pytest.mark.parametrize(
    "failure",
    [
        CosmosHttpResponseError(status_code=503, message="internal cosmos detail"),
        CosmosHttpResponseError(status_code=403, message="principal f879 denied"),
        ClientAuthenticationError("managed identity endpoint failed"),
        ServiceRequestError("dns failure"),
    ],
    ids=["unavailable", "forbidden", "token-acquisition", "unreachable"],
)
def test_equipment_read_failure_returns_503_and_never_calls_agent(containers, agent, failure):
    equipment, _ = containers
    equipment.read_item_error = failure

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 503
    assert response.mimetype == "application/json"
    assert _error(response)["code"] == "grounding_data_unavailable"
    assert "cosmos detail" not in response.get_body().decode()
    assert "f879" not in response.get_body().decode()
    assert agent.grounding_blocks == []


def test_free_text_scan_failure_returns_503_and_never_calls_agent(containers, agent):
    equipment, _ = containers
    equipment.query_error = CosmosHttpResponseError(status_code=503, message="down")

    response = _post_assess({"request_text": "A CAT 320 is broken."})

    assert response.status_code == 503
    assert agent.grounding_blocks == []


def test_work_order_failure_returns_503_and_never_calls_agent(containers, agent):
    _, work_orders = containers
    work_orders.query_error = CosmosHttpResponseError(status_code=429, message="throttled")

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 503
    assert agent.grounding_blocks == []


def test_failure_on_the_known_work_order_lookup_still_uses_the_error_envelope(monkeypatch, containers, agent):
    # This lookup used to run after the try block, which was harmless against an
    # in-memory dict. A Cosmos failure there would escape as an unhandled exception,
    # having already spent an agent call.
    def failing_lookup(equipment_id):
        raise GroundingDataUnavailableError("query failed", user_message="unavailable")

    monkeypatch.setattr(function_app, "get_known_work_order_ids", failing_lookup)

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 503
    assert _error(response)["code"] == "grounding_data_unavailable"
    assert agent.grounding_blocks == []


def test_invalid_record_is_a_controlled_error_that_keeps_contents_out_of_logs(containers, agent, caplog):
    equipment, _ = containers
    equipment._store({**EQUIPMENT_DOC, "hours": "SENSITIVE-VALUE"})

    with caplog.at_level(logging.DEBUG):
        response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 500
    assert _error(response)["code"] == "invalid_grounding_record"
    assert "SENSITIVE-VALUE" not in response.get_body().decode()
    assert "SENSITIVE-VALUE" not in caplog.text
    assert "Southeast Construction" not in caplog.text
    assert agent.grounding_blocks == []


def test_unknown_equipment_keeps_the_existing_no_records_behavior(containers, agent):
    response = _post_assess({"request_text": "Broken.", "equipment_id": "NOT-A-REAL-ID"})
    data = json.loads(response.get_body())

    assert response.status_code == 200
    assert data["equipment_id"] is None
    assert data["grounding_used"] is False
    assert data["related_work_orders"] == []
    assert agent.grounding_blocks[0].startswith(grounding_mod._NO_RECORDS_MESSAGE)


def test_equipment_with_no_work_orders_uses_the_existing_empty_history_text(containers, agent):
    _, work_orders = containers
    work_orders._documents.clear()

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 200
    assert "Prior service history: none on file for this equipment." in agent.grounding_blocks[0]


def test_agent_failure_is_unchanged(monkeypatch, containers):
    agent = FakeAgent(error=FoundryCallError("boom", user_message="The diagnostic assistant could not be reached."))
    monkeypatch.setattr(function_app, "get_assessment", agent)

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert response.status_code == 502
    assert _error(response)["code"] == "upstream_agent_error"


def test_invented_work_orders_are_still_scrubbed(monkeypatch, containers):
    agent = FakeAgent(assessment=_assessment(related_work_orders=["WO-1", "WO-INVENTED"]))
    monkeypatch.setattr(function_app, "get_assessment", agent)

    response = _post_assess({"request_text": "Broken.", "equipment_id": "CAT-A"})

    assert json.loads(response.get_body())["related_work_orders"] == ["WO-1"]


def _save_body(equipment_id="CAT-A", related=("WO-1",)) -> dict:
    return {"assessment": _assessment(equipment_id=equipment_id, related_work_orders=list(related)).model_dump()}


def _post_save(body: dict) -> func.HttpResponse:
    return function_app.assessments(_request("POST", "http://localhost/api/assessments", body))


def test_save_rejects_unknown_equipment(containers):
    response = _post_save(_save_body(equipment_id="NOT-A-REAL-ID", related=()))

    assert response.status_code == 400
    assert "Unknown equipment_id" in _error(response)["message"]


def test_save_rejects_invented_work_order(containers):
    response = _post_save(_save_body(related=("WO-INVENTED",)))

    assert response.status_code == 400
    assert "WO-INVENTED" in _error(response)["message"]


def test_save_is_refused_when_cosmos_cannot_confirm_the_equipment(containers):
    equipment, _ = containers
    equipment.read_item_error = CosmosHttpResponseError(status_code=503, message="down")

    response = _post_save(_save_body())

    assert response.status_code == 503
