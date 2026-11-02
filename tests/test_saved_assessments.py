"""Saved assessments in Cosmos DB: the repository, the size limits on saves, and how
/assess and /assessments behave on top of them. Runs against a fake container."""

import json
import logging
from unittest.mock import MagicMock

import azure.functions as func
import pytest
from azure.core.exceptions import ServiceRequestError
from azure.cosmos.exceptions import CosmosHttpResponseError

import app.cosmos as cosmos_mod
import app.grounding.repository as repository_mod
import app.history.lookup as history_mod
import app.history.repository as history_repository_mod
import function_app
from app.errors import GroundingDataUnavailableError, InvalidGroundingRecordError
from app.history.lookup import build_history_block, list_saved_assessments, recent_saved_assessments, save_assessment
from app.schemas.history_models import SaveAssessmentRequest, _MAX_ASSESSMENT_BYTES
from app.schemas.response_models import MaintenanceAssessment

EQUIPMENT_ID = "CAT-A"
EQUIPMENT = {
    EQUIPMENT_ID: {
        "equipment_id": EQUIPMENT_ID,
        "model": "CAT 320",
        "customer": "Southeast Construction",
        "hours": 100,
        "last_pm_date": "2026-01-01",
    },
    "CAT-B": {
        "equipment_id": "CAT-B",
        "model": "CAT 336",
        "customer": "Northridge Excavation",
        "hours": 200,
        "last_pm_date": "2026-01-02",
    },
}


def _assessment(**overrides) -> MaintenanceAssessment:
    fields = dict(
        equipment_model="CAT 320",
        equipment_id=EQUIPMENT_ID,
        reported_issue="Boom is slow",
        category="Hydraulic",
        priority="Medium",
        recommendations=["Check the filter"],
        additional_information_needed=[],
        requires_technician_review=False,
        related_work_orders=[],
        grounding_used=True,
    )
    return MaintenanceAssessment(**{**fields, **overrides})


def _document(equipment_id=EQUIPMENT_ID, saved_at="2026-01-01T00:00:00+00:00", document_id="doc-1", **overrides):
    return {
        "id": document_id,
        "equipment_id": equipment_id,
        "saved_at": saved_at,
        "saved_by": "tech",
        "associated_work_order_id": None,
        "assessment": _assessment(equipment_id=equipment_id).model_dump(),
        **overrides,
    }


def _seed(container, count, equipment_id=EQUIPMENT_ID):
    for i in range(count):
        container._store(_document(equipment_id, f"2026-01-{i + 1:02d}T00:00:00+00:00", f"doc-{equipment_id}-{i}"))


def _request(method, url, body=None, params=None):
    return func.HttpRequest(
        method=method,
        url=url,
        headers={},
        params=params or {},
        route_params={},
        body=json.dumps(body).encode() if body is not None else b"",
    )


def _post_save(body):
    return function_app.assessments(_request("POST", "http://localhost/api/assessments", body))


def _get_saved(equipment_id):
    return function_app.assessments(
        _request("GET", "http://localhost/api/assessments", params={"equipment_id": equipment_id})
    )


def test_each_save_is_a_new_document_created_for_that_machine(saved_assessments):
    record = save_assessment(EQUIPMENT_ID, _assessment(), "tech", None)

    (name, call), = saved_assessments.calls
    assert name == "create_item"
    assert call["body"]["equipment_id"] == EQUIPMENT_ID
    assert call["body"]["id"]
    assert call["body"]["saved_at"] == record.saved_at
    assert "_rid" not in call["body"]


def test_saves_for_one_machine_never_replace_each_other(saved_assessments):
    # The old store rewrote a whole file per machine, so two saves at once could lose one.
    for technician in ("first", "second", "third"):
        save_assessment(EQUIPMENT_ID, _assessment(), technician, None)

    assert saved_assessments.document_count == 3
    assert saved_assessments.call_names() == ["create_item"] * 3
    assert {r.saved_by for r in list_saved_assessments(EQUIPMENT_ID)} == {"first", "second", "third"}


def test_listing_is_newest_first_and_only_that_machine(saved_assessments):
    _seed(saved_assessments, 3)
    _seed(saved_assessments, 2, "CAT-B")

    records = list_saved_assessments(EQUIPMENT_ID)

    assert [r.saved_at[:10] for r in records] == ["2026-01-03", "2026-01-02", "2026-01-01"]
    assert {r.equipment_id for r in records} == {EQUIPMENT_ID}


def test_queries_are_parameterized_and_scoped_to_one_partition(saved_assessments):
    list_saved_assessments(EQUIPMENT_ID)

    (_, call), = saved_assessments.calls
    assert call["partition_key"] == EQUIPMENT_ID
    assert {"name": "@equipment_id", "value": EQUIPMENT_ID} in call["parameters"]
    assert EQUIPMENT_ID not in call["query"]
    assert not call["enable_cross_partition_query"]


@pytest.mark.parametrize("unsafe_id", ["", "../x", "a/b", "x" * 256])
def test_unsafe_ids_list_nothing_without_a_query(saved_assessments, unsafe_id):
    assert list_saved_assessments(unsafe_id) == []
    assert saved_assessments.calls == []


def test_stored_metadata_does_not_reach_the_record(saved_assessments):
    _seed(saved_assessments, 1)

    assert set(list_saved_assessments(EQUIPMENT_ID)[0].model_dump()) == {
        "equipment_id",
        "saved_at",
        "saved_by",
        "associated_work_order_id",
        "assessment",
    }


def test_recent_returns_only_the_limit_and_the_true_total(saved_assessments):
    _seed(saved_assessments, 8)

    records, total = recent_saved_assessments(EQUIPMENT_ID, 5)

    assert [r.saved_at[:10] for r in records] == [f"2026-01-0{d}" for d in (8, 7, 6, 5, 4)]
    assert total == 8


def test_a_short_history_needs_no_count_query(saved_assessments):
    _seed(saved_assessments, 2)

    records, total = recent_saved_assessments(EQUIPMENT_ID, 5)

    assert (len(records), total) == (2, 2)
    assert len(saved_assessments.calls) == 1


def test_the_prompt_reads_only_the_newest_records(saved_assessments):
    _seed(saved_assessments, 8)

    block, has_history = build_history_block(EQUIPMENT_ID)

    assert has_history is True
    assert "Showing the 5 most recent of 8 saved assessments." in block
    assert "2026-01-08" in block
    assert "2026-01-03" not in block
    query = saved_assessments.calls[0][1]
    assert "TOP @limit" in query["query"]
    assert {"name": "@limit", "value": 5} in query["parameters"]


def test_no_history_keeps_the_existing_message(saved_assessments):
    assert build_history_block(EQUIPMENT_ID) == (history_mod._NO_HISTORY_MESSAGE, False)


def test_malformed_stored_record_is_controlled_and_names_no_contents(saved_assessments):
    saved_assessments._store(_document(assessment={"reported_issue": "SENSITIVE-VALUE"}))

    with pytest.raises(InvalidGroundingRecordError) as exc_info:
        list_saved_assessments(EQUIPMENT_ID)

    assert "SENSITIVE-VALUE" not in exc_info.value.detail
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "failure",
    [CosmosHttpResponseError(status_code=403, message="denied"), ServiceRequestError("unreachable")],
    ids=["forbidden", "unreachable"],
)
def test_store_failures_are_controlled(saved_assessments, failure):
    saved_assessments.query_error = failure
    with pytest.raises(GroundingDataUnavailableError):
        list_saved_assessments(EQUIPMENT_ID)
    with pytest.raises(GroundingDataUnavailableError):
        history_repository_mod.get_saved_assessment_repository().count(EQUIPMENT_ID)

    saved_assessments.create_error = failure
    with pytest.raises(GroundingDataUnavailableError):
        save_assessment(EQUIPMENT_ID, _assessment(), "tech", None)


def test_write_charge_is_logged_without_contents(saved_assessments, caplog):
    with caplog.at_level(logging.INFO, logger=cosmos_mod.__name__):
        save_assessment(EQUIPMENT_ID, _assessment(reported_issue="SENSITIVE-VALUE"), "tech", None)

    assert "saved assessment write request charge: 9.0 RU" in caplog.text
    assert "SENSITIVE-VALUE" not in caplog.text


def test_save_endpoint_persists_and_the_record_can_be_read_back(saved_assessments, seed_grounding):
    seed_grounding(EQUIPMENT)

    created = _post_save({"assessment": _assessment().model_dump(), "saved_by": "Jamie R."})
    listed = _get_saved(EQUIPMENT_ID)

    assert created.status_code == 201
    assert listed.status_code == 200
    assert [r["saved_by"] for r in json.loads(listed.get_body())] == ["Jamie R."]
    assert "id" not in json.loads(created.get_body())


def test_a_save_appears_in_the_next_assessment_prompt(saved_assessments, seed_grounding, monkeypatch):
    seed_grounding(EQUIPMENT)
    _post_save({"assessment": _assessment(reported_issue="Saved symptom").model_dump()})
    prompts = []

    def fake_get_assessment(request_text, grounding_block):
        prompts.append(grounding_block)
        return _assessment(equipment_id=None)

    monkeypatch.setattr(function_app, "get_assessment", fake_get_assessment)
    response = function_app.assess(
        _request("POST", "http://localhost/api/assess", {"request_text": "Slow.", "equipment_id": EQUIPMENT_ID})
    )

    assert response.status_code == 200
    assert 'reported_issue="Saved symptom"' in prompts[0]


def test_history_failure_never_reaches_the_agent(saved_assessments, seed_grounding, monkeypatch):
    seed_grounding(EQUIPMENT)
    saved_assessments.query_error = CosmosHttpResponseError(status_code=503, message="down")
    monkeypatch.setattr(function_app, "get_assessment", lambda *_: pytest.fail("agent must not be called"))

    response = function_app.assess(
        _request("POST", "http://localhost/api/assess", {"request_text": "Slow.", "equipment_id": EQUIPMENT_ID})
    )

    assert response.status_code == 503
    assert json.loads(response.get_body())["error"]["code"] == "grounding_data_unavailable"


def test_failed_save_reports_an_error_and_stores_nothing(saved_assessments, seed_grounding):
    seed_grounding(EQUIPMENT)
    saved_assessments.create_error = CosmosHttpResponseError(status_code=403, message="principal f879 denied")

    response = _post_save({"assessment": _assessment().model_dump()})

    assert response.status_code == 503
    assert "f879" not in response.get_body().decode()
    assert saved_assessments.document_count == 0


def test_saving_for_unknown_equipment_writes_nothing(saved_assessments, seed_grounding):
    seed_grounding(EQUIPMENT)

    response = _post_save({"assessment": _assessment(equipment_id="NOT-REAL").model_dump()})

    assert response.status_code == 400
    assert saved_assessments.calls == []


def test_oversized_assessment_is_rejected_before_anything_is_written(saved_assessments, seed_grounding):
    # The size of a real test record that once sat in the old store, read on every /assess.
    seed_grounding(EQUIPMENT)
    huge = _assessment(reported_issue="x" * 50_000, recommendations=["y" * 10_000] * 20)

    response = _post_save({"assessment": huge.model_dump()})

    assert response.status_code == 400
    assert json.loads(response.get_body())["error"]["code"] == "invalid_request"
    assert saved_assessments.calls == []


def test_size_limit_boundary_and_a_realistic_record():
    base = len(_assessment(reported_issue="").model_dump_json().encode("utf-8"))

    SaveAssessmentRequest(assessment=_assessment(reported_issue="x" * (_MAX_ASSESSMENT_BYTES - base)))
    with pytest.raises(ValueError, match="limit"):
        SaveAssessmentRequest(assessment=_assessment(reported_issue="x" * (_MAX_ASSESSMENT_BYTES - base + 1)))

    assert base + 5_000 < _MAX_ASSESSMENT_BYTES


def test_size_limit_counts_bytes_not_characters():
    base = len(_assessment(reported_issue="").model_dump_json().encode("utf-8"))

    with pytest.raises(ValueError, match="limit"):
        SaveAssessmentRequest(assessment=_assessment(reported_issue="€" * (_MAX_ASSESSMENT_BYTES - base)))


def test_saved_by_length_is_limited():
    SaveAssessmentRequest(assessment=_assessment(), saved_by="x" * 100)
    with pytest.raises(ValueError):
        SaveAssessmentRequest(assessment=_assessment(), saved_by="x" * 101)


@pytest.fixture
def fake_sdk(monkeypatch):
    client = MagicMock()
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(cosmos_mod, "CosmosClient", client_cls)
    monkeypatch.setattr(cosmos_mod, "DefaultAzureCredential", MagicMock())
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.com:443/")
    monkeypatch.delenv("COSMOS_SAVED_ASSESSMENTS_CONTAINER", raising=False)
    return client_cls, client


def test_every_repository_shares_one_client(fake_sdk):
    client_cls, _ = fake_sdk

    repository_mod.get_equipment_repository()
    history_repository_mod.get_saved_assessment_repository()

    client_cls.assert_called_once()


def test_default_container_name(fake_sdk):
    _, client = fake_sdk

    history_repository_mod.get_saved_assessment_repository()

    client.get_database_client().get_container_client.assert_called_once_with("saved-assessments")


def test_container_name_comes_from_configuration(fake_sdk, monkeypatch):
    _, client = fake_sdk
    monkeypatch.setenv("COSMOS_SAVED_ASSESSMENTS_CONTAINER", "other-assessments")

    history_repository_mod.get_saved_assessment_repository()

    client.get_database_client().get_container_client.assert_called_once_with("other-assessments")
