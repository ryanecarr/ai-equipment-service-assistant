"""Tests for scripts/migrate_grounding_to_cosmos.py against fake containers — no Azure access."""

import json
from pathlib import Path

import pytest
from azure.cosmos.exceptions import CosmosHttpResponseError

from cosmos_fakes import FakeContainer
from scripts.migrate_grounding_to_cosmos import build_documents, run, verify

SEED_DIR = Path(__file__).resolve().parent.parent / "seed-data"
EQUIPMENT_MASTER = json.loads((SEED_DIR / "equipment_master.json").read_text(encoding="utf-8"))
WORK_ORDERS = json.loads((SEED_DIR / "work_orders.json").read_text(encoding="utf-8"))

EQUIPMENT = {
    "equipment_id": "CAT-A",
    "model": "CAT 320",
    "manufacturer": "Caterpillar",
    "customer": "SE",
    "hours": 1,
    "last_pm_date": "2026-01-01",
}
ORDER = {"work_order_id": "WO-1", "date": "2026-01-01", "reported_issue": "x", "resolution": "y", "technician_notes": "z"}


@pytest.fixture
def equipment():
    return FakeContainer("equipment")


@pytest.fixture
def work_orders():
    return FakeContainer("work-orders")


def _write_calls(*containers) -> list[str]:
    return [n for c in containers for n in c.call_names() if n not in ("read", "read_item", "query_items")]


def test_equipment_document_adds_only_id():
    document = build_documents({"CAT-A": EQUIPMENT}, {}).equipment[0]

    assert document == {**EQUIPMENT, "id": "CAT-A"}


def test_work_order_document_adds_id_parent_and_order():
    document = build_documents({"CAT-A": EQUIPMENT}, {"CAT-A": [ORDER]}).work_orders[0]

    assert document == {**ORDER, "id": "WO-1", "equipment_id": "CAT-A", "source_order": 0}


def test_work_orders_record_their_position_in_the_source_array():
    orders = [ORDER, {**ORDER, "work_order_id": "WO-2"}, {**ORDER, "work_order_id": "WO-3"}]

    documents = build_documents({"CAT-A": EQUIPMENT}, {"CAT-A": orders}).work_orders

    assert [d["source_order"] for d in documents] == [0, 1, 2]


def test_real_seed_data_builds_cleanly():
    plan = build_documents(EQUIPMENT_MASTER, WORK_ORDERS)

    assert plan.failures == []
    assert len(plan.equipment) == 3
    assert len(plan.work_orders) == 3


@pytest.mark.parametrize(
    "equipment_master, orders, expected",
    [
        ({"CAT-B": EQUIPMENT}, {}, "key differs"),
        ({"CAT-A": {**EQUIPMENT, "hours": "lots"}}, {}, "hours"),
        ({"CAT-A": {k: v for k, v in EQUIPMENT.items() if k != "customer"}}, {}, "customer"),
        ({"a/b": {**EQUIPMENT, "equipment_id": "a/b"}}, {}, "document id"),
        ({"CAT-A": EQUIPMENT}, {"GHOST": [ORDER]}, "no importable equipment"),
        ({"CAT-A": EQUIPMENT}, {"CAT-A": [{**ORDER, "work_order_id": "WO 1"}]}, "document id"),
        ({"CAT-A": EQUIPMENT}, {"CAT-A": [ORDER, ORDER]}, "duplicate"),
        ({"CAT-A": EQUIPMENT}, {"CAT-A": [{k: v for k, v in ORDER.items() if k != "resolution"}]}, "resolution"),
    ],
    ids=["key-mismatch", "bad-type", "missing-field", "unsafe-id", "orphan", "unsafe-wo-id", "duplicate-wo", "missing-wo-field"],
)
def test_invalid_source_records_are_reported_not_imported(equipment_master, orders, expected):
    plan = build_documents(equipment_master, orders)

    assert len(plan.failures) >= 1
    assert expected in plan.failures[-1]
    assert not any(d["id"] in ("a/b", "WO 1") for d in plan.equipment + plan.work_orders)


def test_validation_failure_reports_field_names_not_values():
    plan = build_documents({"CAT-A": {**EQUIPMENT, "hours": "SENSITIVE-VALUE"}}, {})

    assert "SENSITIVE-VALUE" not in plan.failures[0]


def test_dry_run_writes_nothing(equipment, work_orders, capsys):
    exit_code = run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders)

    assert exit_code == 0
    assert equipment.document_count == 0 and work_orders.document_count == 0
    assert _write_calls(equipment, work_orders) == []
    assert "would import 3" in capsys.readouterr().out


def test_apply_imports_all_records_and_verifies(equipment, work_orders, capsys):
    exit_code = run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders, apply=True)

    assert exit_code == 0
    assert equipment.document_count == 3
    assert work_orders.document_count == 3
    output = capsys.readouterr().out
    assert "equipment: imported 3, skipped 0, failed 0" in output
    assert "work orders: imported 3, skipped 0, failed 0" in output
    assert "Verification: passed" in output


def test_second_run_skips_everything_and_creates_no_duplicates(equipment, work_orders, capsys):
    run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders, apply=True)
    capsys.readouterr()
    equipment.calls.clear()
    work_orders.calls.clear()

    exit_code = run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders, apply=True)

    assert exit_code == 0
    assert equipment.document_count == 3 and work_orders.document_count == 3
    assert _write_calls(equipment, work_orders) == []
    output = capsys.readouterr().out
    assert "equipment: imported 0, skipped 3, failed 0" in output
    assert "work orders: imported 0, skipped 3, failed 0" in output


def test_changed_source_record_is_updated_in_place(equipment, work_orders, capsys):
    run({"CAT-A": EQUIPMENT}, {}, equipment, work_orders, apply=True)
    capsys.readouterr()

    exit_code = run({"CAT-A": {**EQUIPMENT, "hours": 2}}, {}, equipment, work_orders, apply=True)

    assert exit_code == 0
    assert equipment.document_count == 1
    assert "equipment: imported 1, skipped 0, failed 0" in capsys.readouterr().out


def test_invalid_records_do_not_stop_the_rest_but_fail_the_run(equipment, work_orders, capsys):
    source = {"CAT-A": EQUIPMENT, "BAD": {**EQUIPMENT, "equipment_id": "OTHER"}}

    exit_code = run(source, {}, equipment, work_orders, apply=True)

    assert exit_code == 1
    assert equipment.document_count == 1
    assert "FAILED equipment 'BAD'" in capsys.readouterr().err


def test_write_failure_is_counted_and_fails_the_run(equipment, work_orders, capsys):
    equipment.upsert_error = CosmosHttpResponseError(status_code=503, message="down")

    exit_code = run({"CAT-A": EQUIPMENT}, {}, equipment, work_orders, apply=True)

    assert exit_code == 1
    assert "equipment: imported 0, skipped 0, failed 1" in capsys.readouterr().out


@pytest.mark.parametrize("wrong", ["/equipmentId", "/id"])
def test_wrong_partition_key_aborts_before_any_read_or_write(work_orders, wrong):
    equipment = FakeContainer("equipment", partition_key_path=wrong)

    with pytest.raises(SystemExit) as exc_info:
        run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders, apply=True)

    assert wrong in str(exc_info.value)
    assert equipment.call_names() == ["read"]
    assert work_orders.calls == []


def test_verify_only_reports_a_missing_import_and_writes_nothing(equipment, work_orders, capsys):
    exit_code = run(EQUIPMENT_MASTER, WORK_ORDERS, equipment, work_orders, verify_only=True)

    assert exit_code == 1
    assert _write_calls(equipment, work_orders) == []
    assert "MISMATCH" in capsys.readouterr().err


def test_verify_detects_value_drift_extra_documents_and_lost_ordering(equipment, work_orders, capsys):
    orders = {"CAT-A": [ORDER, {**ORDER, "work_order_id": "WO-2"}]}
    run({"CAT-A": EQUIPMENT}, orders, equipment, work_orders, apply=True)
    assert verify({"CAT-A": EQUIPMENT}, orders, equipment, work_orders) == []

    equipment._store({"id": "CAT-A", **EQUIPMENT, "hours": 999})
    assert any("record differs" in p for p in verify({"CAT-A": EQUIPMENT}, orders, equipment, work_orders))

    equipment._store({"id": "CAT-A", **EQUIPMENT})
    swapped = {"CAT-A": list(reversed(orders["CAT-A"]))}
    assert any("work orders differ" in p for p in verify({"CAT-A": EQUIPMENT}, swapped, equipment, work_orders))

    equipment._store({"id": "EXTRA", **EQUIPMENT, "equipment_id": "EXTRA"})
    problems = verify({"CAT-A": EQUIPMENT}, orders, equipment, work_orders)
    assert any("2 document(s), source has 1" in p for p in problems)


def test_script_contains_no_delete_or_container_management_calls():
    source = (Path(__file__).resolve().parent.parent / "scripts" / "migrate_grounding_to_cosmos.py").read_text()

    for forbidden in ("delete_item", "delete_container", "create_container", "create_database"):
        assert forbidden not in source
