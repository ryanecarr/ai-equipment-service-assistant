"""Guards on seed-data/expansion/: it must stay additive and Caterpillar-only, must not
change how existing requests resolve, and must not grow the prompt without limit — each
work order on a machine is another line in every agent call for that machine."""

import json
from pathlib import Path

import pytest

import app.grounding.lookup as lookup
import scripts.migrate_grounding_to_cosmos as migration
from app.grounding.matching import match_equipment
from cosmos_fakes import FakeContainer
from scripts.migrate_grounding_to_cosmos import build_documents, load_source, run

SEED_DIR = Path(__file__).resolve().parent.parent / "seed-data"
EQUIPMENT_MASTER = json.loads((SEED_DIR / "equipment_master.json").read_text(encoding="utf-8"))
WORK_ORDERS = json.loads((SEED_DIR / "work_orders.json").read_text(encoding="utf-8"))
EXTRA_EQUIPMENT = json.loads((SEED_DIR / "expansion" / "equipment_master.json").read_text(encoding="utf-8"))
EXTRA_WORK_ORDERS = json.loads((SEED_DIR / "expansion" / "work_orders.json").read_text(encoding="utf-8"))

MAX_WORK_ORDERS_PER_EQUIPMENT = 8
MAX_GROUNDING_BLOCK_CHARS = 2500
FREE_TEXT_REQUESTS = [
    "A CAT 320 excavator issue. Customer is Southeast Construction.",
    "The CAT 320 has slow hydraulics.",
    "Southeast Construction reported an issue.",
    "The Komatsu PC200 at Gulf Harbor Marine is leaking.",
    "The Komatsu PC200 is leaking.",
    "The Komatsu PC2000 is leaking.",
    "Gulf Harbor Marine called about a track issue.",
    "Completely unrelated text.",
]
GENERATOR_MODELS = {"CAT C9", "CAT C15", "CAT C18", "CAT XQ60", "CAT XQ200", "CAT XQ330", "CAT 3512"}


def test_expanded_source_is_the_original_data_plus_the_expansion():
    equipment, work_orders = load_source("expanded")

    assert len(equipment) == 48
    assert {k: equipment[k] for k in EQUIPMENT_MASTER} == EQUIPMENT_MASTER
    assert {k: work_orders[k] for k in WORK_ORDERS} == WORK_ORDERS


def test_expanded_data_builds_cleanly_and_imports_idempotently(capsys):
    equipment_master, work_orders = load_source("expanded")
    plan = build_documents(equipment_master, work_orders)
    assert plan.failures == []

    equipment, orders = FakeContainer("equipment"), FakeContainer("work-orders")
    assert run(equipment_master, work_orders, equipment, orders, apply=True) == 0
    assert equipment.document_count == 48
    assert orders.document_count == len(plan.work_orders)

    assert run(equipment_master, work_orders, equipment, orders, apply=True) == 0
    assert "equipment: imported 0, skipped 48, failed 0" in capsys.readouterr().out


def test_all_source_data_is_caterpillar():
    equipment, _ = load_source("expanded")

    assert {record["manufacturer"] for record in equipment.values()} == {"Caterpillar"}
    assert all(record["model"].startswith("CAT ") for record in equipment.values())


def test_expansion_covers_generators_as_well_as_construction_equipment():
    generators = [r for r in EXTRA_EQUIPMENT.values() if r["model"] in GENERATOR_MODELS]

    assert len(generators) >= 10
    assert len(EXTRA_EQUIPMENT) - len(generators) >= 30


def test_generator_history_does_not_describe_machine_parts_a_generator_lacks():
    for equipment_id, record in EXTRA_EQUIPMENT.items():
        if record["model"] not in GENERATOR_MODELS:
            continue
        for order in EXTRA_WORK_ORDERS.get(equipment_id, []):
            text = f"{order['reported_issue']} {order['resolution']}".lower()
            assert not any(part in text for part in ("track", "boom", "bucket", "blade")), equipment_id


@pytest.mark.parametrize("request_text", FREE_TEXT_REQUESTS)
def test_existing_free_text_requests_resolve_the_same_with_the_expansion_present(request_text):
    before = match_equipment(list(EQUIPMENT_MASTER.values()), request_text)
    after = match_equipment(list({**EQUIPMENT_MASTER, **EXTRA_EQUIPMENT}.values()), request_text)

    assert after.status == before.status
    assert after.record == before.record


def test_history_per_equipment_stays_within_the_prompt_budget():
    assert max(len(orders) for orders in EXTRA_WORK_ORDERS.values()) <= MAX_WORK_ORDERS_PER_EQUIPMENT

    for equipment_id, record in EXTRA_EQUIPMENT.items():
        block = lookup.format_grounding_block(record, EXTRA_WORK_ORDERS.get(equipment_id, []))
        assert len(block) <= MAX_GROUNDING_BLOCK_CHARS, equipment_id


def test_some_equipment_has_no_history():
    assert set(EXTRA_EQUIPMENT) - set(EXTRA_WORK_ORDERS)


def test_work_orders_only_reference_expansion_equipment_and_run_oldest_first():
    assert set(EXTRA_WORK_ORDERS) <= set(EXTRA_EQUIPMENT)
    for orders in EXTRA_WORK_ORDERS.values():
        dates = [order["date"] for order in orders]
        assert dates == sorted(dates)


def test_work_order_ids_are_unique_across_both_datasets():
    ids = [o["work_order_id"] for orders in {**WORK_ORDERS, **EXTRA_WORK_ORDERS}.values() for o in orders]

    assert len(ids) == len(set(ids))
