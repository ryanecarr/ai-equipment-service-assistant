"""Tests for app/grounding/repository.py against a fake container — no Azure access."""

import logging
from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError
from azure.cosmos.exceptions import CosmosHttpResponseError, CosmosResourceNotFoundError

import app.cosmos as cosmos_mod
import app.grounding.repository as repository_mod
from app.errors import GroundingDataUnavailableError, InvalidGroundingRecordError
from app.grounding.repository import EquipmentRepository, get_equipment_repository, is_valid_document_id
from cosmos_fakes import FakeContainer

EQUIPMENT_DOC = {
    "id": "CAT-A",
    "equipment_id": "CAT-A",
    "model": "CAT 320",
    "manufacturer": "Caterpillar",
    "customer": "Southeast Construction",
    "hours": 100,
    "last_pm_date": "2026-01-01",
}


def _work_order_doc(equipment_id: str, work_order_id: str, source_order: int) -> dict:
    return {
        "id": work_order_id,
        "equipment_id": equipment_id,
        "source_order": source_order,
        "work_order_id": work_order_id,
        "date": "2026-01-01",
        "reported_issue": "x",
        "resolution": "y",
        "technician_notes": "z",
    }


@pytest.fixture
def equipment():
    return FakeContainer("equipment", documents=(EQUIPMENT_DOC,))


@pytest.fixture
def work_orders():
    return FakeContainer(
        "work-orders",
        documents=(
            # Deliberately stored out of order, and not in date order either way.
            _work_order_doc("CAT-A", "WO-2", 1),
            _work_order_doc("CAT-A", "WO-3", 2),
            _work_order_doc("CAT-A", "WO-1", 0),
            _work_order_doc("CAT-B", "WO-9", 0),
        ),
    )


@pytest.fixture
def repository(equipment, work_orders):
    return EquipmentRepository(equipment, work_orders)


SERVICE_FAILURES = [
    pytest.param(CosmosHttpResponseError(status_code=503, message="unavailable"), id="service-unavailable"),
    pytest.param(CosmosHttpResponseError(status_code=401, message="unauthorized"), id="unauthorized"),
    pytest.param(CosmosHttpResponseError(status_code=403, message="forbidden"), id="forbidden"),
    pytest.param(CosmosHttpResponseError(status_code=429, message="throttled"), id="throttled-after-sdk-retries"),
    pytest.param(ServiceRequestError("connection refused"), id="unreachable"),
    pytest.param(ClientAuthenticationError("no credential"), id="token-acquisition"),
]


def test_get_equipment_is_a_point_read_and_never_queries(repository, equipment):
    repository.get_equipment("CAT-A")

    assert equipment.call_names() == ["read_item"]
    assert equipment.calls[0][1] == {"item": "CAT-A", "partition_key": "CAT-A"}


def test_get_equipment_returns_only_source_fields(repository):
    record = repository.get_equipment("CAT-A")

    expected = {k: v for k, v in EQUIPMENT_DOC.items() if k != "id"}
    assert record == expected


def test_get_equipment_missing_returns_none(repository):
    assert repository.get_equipment("NOT-A-REAL-ID") is None


def test_404_with_a_sub_status_is_not_treated_as_missing_equipment(repository, equipment):
    # 1003 = owner resource does not exist. Reporting this as "no such equipment" would
    # send the agent an ungrounded request as if the record simply weren't on file.
    equipment.read_item_error = CosmosResourceNotFoundError(status_code=404, message="gone", sub_status=1003)

    with pytest.raises(GroundingDataUnavailableError):
        repository.get_equipment("CAT-A")


@pytest.mark.parametrize(
    "unsafe_id",
    ["", ".", "..", "../work-orders", "..%2f", "a/b", "a\\b", "a?b", "a#b", "a b", "x" * 256, "CAT-A\n"],
)
def test_ids_unsafe_for_the_request_url_never_reach_the_sdk(repository, equipment, unsafe_id):
    # read_item formats the id into the URL unescaped, so these must simply be "not found".
    assert repository.get_equipment(unsafe_id) is None
    assert equipment.calls == []


def test_real_equipment_ids_are_valid_document_ids():
    assert is_valid_document_id("CAT336-RP-3310")
    assert is_valid_document_id("WO-10234")


def test_work_orders_filtered_to_one_equipment_in_source_order(repository):
    orders = repository.get_work_orders("CAT-A")

    assert [o["work_order_id"] for o in orders] == ["WO-1", "WO-2", "WO-3"]


def test_work_order_query_is_parameterized_and_scoped_to_one_partition(repository, work_orders):
    repository.get_work_orders("CAT-A")

    (name, call), = work_orders.calls
    assert name == "query_items"
    assert call["partition_key"] == "CAT-A"
    assert call["parameters"] == [{"name": "@equipment_id", "value": "CAT-A"}]
    assert "CAT-A" not in call["query"]
    assert not call["enable_cross_partition_query"]


def test_work_orders_return_only_source_fields(repository):
    order = repository.get_work_orders("CAT-B")[0]

    assert set(order) == {"work_order_id", "date", "reported_issue", "resolution", "technician_notes"}


def test_no_work_orders_returns_empty_list(repository):
    assert repository.get_work_orders("DEERE-A") == []


def test_list_equipment_is_the_only_cross_partition_read(repository, equipment):
    records = repository.list_equipment()

    assert [r["equipment_id"] for r in records] == ["CAT-A"]
    assert "id" not in records[0] and "_etag" not in records[0]
    assert equipment.calls[0][1]["enable_cross_partition_query"] is True


@pytest.mark.parametrize("bad_field", ["model", "customer", "hours", "last_pm_date", "equipment_id"])
def test_equipment_missing_a_required_field_is_rejected(work_orders, bad_field):
    doc = {k: v for k, v in EQUIPMENT_DOC.items() if k != bad_field}
    doc.setdefault("equipment_id", "CAT-A")  # the fake needs it to store the partition
    equipment = FakeContainer("equipment", documents=(doc,))
    if bad_field == "equipment_id":
        for stored in equipment._documents.values():
            del stored["equipment_id"]

    with pytest.raises(InvalidGroundingRecordError):
        EquipmentRepository(equipment, work_orders).get_equipment("CAT-A")


def test_empty_model_is_rejected_because_it_would_match_every_request(work_orders):
    equipment = FakeContainer("equipment", documents=({**EQUIPMENT_DOC, "model": ""},))

    with pytest.raises(InvalidGroundingRecordError):
        EquipmentRepository(equipment, work_orders).list_equipment()


def test_malformed_work_order_is_rejected(equipment):
    doc = _work_order_doc("CAT-A", "WO-1", 0)
    del doc["resolution"]
    work_orders = FakeContainer("work-orders", documents=(doc,))

    with pytest.raises(InvalidGroundingRecordError):
        EquipmentRepository(equipment, work_orders).get_work_orders("CAT-A")


def test_validation_failure_names_fields_but_not_their_contents(work_orders):
    doc = {**EQUIPMENT_DOC, "hours": "SENSITIVE-VALUE", "customer": ""}
    equipment = FakeContainer("equipment", documents=(doc,))

    with pytest.raises(InvalidGroundingRecordError) as exc_info:
        EquipmentRepository(equipment, work_orders).get_equipment("CAT-A")

    assert "hours" in exc_info.value.detail and "customer" in exc_info.value.detail
    assert "SENSITIVE-VALUE" not in exc_info.value.detail
    # A chained pydantic ValidationError would put the value back in the logged traceback.
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.parametrize("failure", SERVICE_FAILURES)
def test_point_read_failures_become_a_controlled_error(repository, equipment, failure):
    equipment.read_item_error = failure

    with pytest.raises(GroundingDataUnavailableError) as exc_info:
        repository.get_equipment("CAT-A")

    assert exc_info.value.status_code == 503
    assert exc_info.value.user_message == cosmos_mod._UNAVAILABLE_USER_MESSAGE


@pytest.mark.parametrize("failure", SERVICE_FAILURES)
def test_query_failures_during_iteration_become_a_controlled_error(repository, work_orders, equipment, failure):
    work_orders.query_error = failure
    equipment.query_error = failure

    with pytest.raises(GroundingDataUnavailableError):
        repository.get_work_orders("CAT-A")
    with pytest.raises(GroundingDataUnavailableError):
        repository.list_equipment()


def test_failure_detail_keeps_status_for_diagnosis_but_user_message_does_not(repository, equipment):
    equipment.read_item_error = CosmosHttpResponseError(status_code=403, message="principal abc denied")

    with pytest.raises(GroundingDataUnavailableError) as exc_info:
        repository.get_equipment("CAT-A")

    assert "status=403" in exc_info.value.detail
    assert "403" not in exc_info.value.user_message
    assert "principal" not in exc_info.value.user_message


def test_request_charge_is_logged_without_record_contents(repository, caplog):
    with caplog.at_level(logging.INFO, logger=cosmos_mod.__name__):
        repository.get_equipment("CAT-A")
        repository.get_work_orders("CAT-A")

    assert "equipment read request charge: 1.0 RU" in caplog.text
    assert "work order query request charge: 2.9 RU" in caplog.text
    assert "Southeast Construction" not in caplog.text


@pytest.fixture
def fake_sdk(monkeypatch):
    client = MagicMock()
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(cosmos_mod, "CosmosClient", client_cls)
    monkeypatch.setattr(cosmos_mod, "DefaultAzureCredential", MagicMock())
    monkeypatch.setenv("COSMOS_ENDPOINT", "https://example.documents.azure.com:443/")
    for name in ("COSMOS_DATABASE_NAME", "COSMOS_EQUIPMENT_CONTAINER", "COSMOS_WORK_ORDERS_CONTAINER"):
        monkeypatch.delenv(name, raising=False)
    repository_mod._repository = None
    return client_cls, client


def test_client_is_built_once_and_reused(fake_sdk):
    client_cls, _ = fake_sdk

    assert get_equipment_repository() is get_equipment_repository()
    client_cls.assert_called_once()


def test_default_resource_names(fake_sdk):
    _, client = fake_sdk
    get_equipment_repository()

    client.get_database_client.assert_called_once_with("equipment-service-db")
    container_names = [c.args[0] for c in client.get_database_client().get_container_client.call_args_list]
    assert container_names == ["equipment", "work-orders"]


def test_resource_names_come_from_configuration(fake_sdk, monkeypatch):
    _, client = fake_sdk
    monkeypatch.setenv("COSMOS_DATABASE_NAME", "other-db")
    monkeypatch.setenv("COSMOS_EQUIPMENT_CONTAINER", "other-equipment")
    monkeypatch.setenv("COSMOS_WORK_ORDERS_CONTAINER", "other-orders")
    get_equipment_repository()

    client.get_database_client.assert_called_once_with("other-db")
    container_names = [c.args[0] for c in client.get_database_client().get_container_client.call_args_list]
    assert container_names == ["other-equipment", "other-orders"]


def test_missing_container_at_startup_is_a_service_error_not_missing_equipment(fake_sdk):
    _, client = fake_sdk
    missing = client.get_database_client().get_container_client()
    missing.read.side_effect = CosmosResourceNotFoundError(status_code=404, message="no such container")

    with pytest.raises(GroundingDataUnavailableError):
        get_equipment_repository()


def test_failed_client_construction_is_controlled_and_not_cached(fake_sdk):
    client_cls, _ = fake_sdk
    client_cls.side_effect = ServiceRequestError("account unreachable")

    with pytest.raises(GroundingDataUnavailableError):
        get_equipment_repository()

    client_cls.side_effect = None
    assert get_equipment_repository() is not None
    assert client_cls.call_count == 2
