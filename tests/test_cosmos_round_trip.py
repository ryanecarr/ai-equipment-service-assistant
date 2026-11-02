"""The full path grounding data takes, using the real records in seed-data/: source JSON,
through the migration's document transforms, into a fake container that adds Cosmos's
system properties, and back out through the runtime repository.

The result has to be what the source records produce directly, which is what keeps a
change to the transforms or the repository from quietly altering the agent's prompt.
"""

import json
from pathlib import Path

import azure.functions as func
import pytest

import app.grounding.lookup as lookup
import app.grounding.repository as repository_mod
import app.history.lookup as history_mod
import function_app
from app.grounding.lookup import format_grounding_block
from app.schemas.response_models import MaintenanceAssessment

SEED_DIR = Path(__file__).resolve().parent.parent / "seed-data"
EQUIPMENT_MASTER = json.loads((SEED_DIR / "equipment_master.json").read_text(encoding="utf-8"))
WORK_ORDERS = json.loads((SEED_DIR / "work_orders.json").read_text(encoding="utf-8"))

# Neither ascending nor descending by date, unlike the seed data, so that no date sort
# in either direction can reproduce this order by accident.
MULTI_WORK_ORDERS = {
    "CAT320-SE-1042": [
        {"work_order_id": "WO-2", "date": "2026-03-01", "reported_issue": "b", "resolution": "r2", "technician_notes": "n2"},
        {"work_order_id": "WO-3", "date": "2026-09-01", "reported_issue": "c", "resolution": "r3", "technician_notes": "n3"},
        {"work_order_id": "WO-1", "date": "2026-01-01", "reported_issue": "a", "resolution": "r1", "technician_notes": "n1"},
    ]
}

ALL_EQUIPMENT_IDS = sorted(EQUIPMENT_MASTER)


@pytest.fixture(params=[WORK_ORDERS, MULTI_WORK_ORDERS], ids=["seed-work-orders", "multi-work-orders"])
def work_orders(request, seed_grounding):
    seed_grounding(EQUIPMENT_MASTER, request.param)
    return request.param


@pytest.mark.parametrize("equipment_id", ALL_EQUIPMENT_IDS)
def test_records_come_back_exactly_as_the_source_has_them(work_orders, equipment_id):
    repository = repository_mod.get_equipment_repository()

    assert repository.get_equipment(equipment_id) == EQUIPMENT_MASTER[equipment_id]
    assert repository.get_work_orders(equipment_id) == work_orders.get(equipment_id, [])


@pytest.mark.parametrize("equipment_id", ALL_EQUIPMENT_IDS)
def test_grounding_block_is_what_the_source_records_produce(work_orders, equipment_id):
    record, block = lookup.find_grounding(equipment_id, "any text")

    assert record == EQUIPMENT_MASTER[equipment_id]
    assert block == format_grounding_block(EQUIPMENT_MASTER[equipment_id], work_orders.get(equipment_id, []))


def test_grounding_text_sent_to_the_agent_is_pinned(seed_grounding):
    # Literal on purpose. The tests above compare against format_grounding_block itself,
    # so they can't notice a change to the wording the agent receives.
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)

    _, block = lookup.find_grounding("CAT320-SE-1042", "any text")

    assert block == (
        "KNOWN PRIOR RECORDS (from internal systems, for reference only — not manufacturer documentation):\n"
        "Equipment: CAT 320 (CAT320-SE-1042), Customer: Southeast Construction, Hours: 4312, Last PM: 2026-09-02\n"
        "Prior service history:\n"
        '- 2026-06-14 [WO-10234]: Reported "Boom raises slowly under load" — Resolution: Replaced main control '
        "valve O-rings; hydraulic response restored. Notes: No fault codes present at time of service. "
        "Recommend monitoring filter condition.\n"
        '- 2026-09-02 [WO-10590]: Reported "Routine preventive maintenance" — Resolution: Hydraulic fluid and '
        "filter replaced; system pressure tested within spec. Notes: No anomalies observed."
    )


def test_unknown_equipment_gets_the_no_records_message(work_orders):
    assert lookup.find_grounding("NOT-A-REAL-ID", "any text") == (None, lookup._NO_RECORDS_MESSAGE)


def test_work_orders_keep_source_order_rather_than_date_order(seed_grounding):
    seed_grounding(EQUIPMENT_MASTER, MULTI_WORK_ORDERS)
    _, block = lookup.find_grounding("CAT320-SE-1042", "any text")

    assert block.index("WO-2") < block.index("WO-3") < block.index("WO-1")


def test_real_seed_work_orders_stay_oldest_first(seed_grounding):
    # CAT320-SE-1042's source array runs oldest to newest, so a "most recent first"
    # ORDER BY would visibly reverse this payload.
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)
    _, block = lookup.find_grounding("CAT320-SE-1042", "any text")

    assert block.index("WO-10234") < block.index("WO-10590")


@pytest.mark.parametrize("equipment_id", ALL_EQUIPMENT_IDS)
def test_validation_helpers_reflect_the_source(work_orders, equipment_id):
    assert lookup.equipment_exists(equipment_id) is True
    assert lookup.get_known_work_order_ids(equipment_id) == {
        wo["work_order_id"] for wo in work_orders.get(equipment_id, [])
    }


def test_validation_helpers_for_unknown_equipment(work_orders):
    assert lookup.equipment_exists("NOT-A-REAL-ID") is False
    assert lookup.get_known_work_order_ids("NOT-A-REAL-ID") == set()


def test_foundry_payload_is_built_from_the_source_records(monkeypatch, seed_grounding):
    seed_grounding(EQUIPMENT_MASTER, WORK_ORDERS)
    monkeypatch.setattr(history_mod, "recent_saved_assessments", lambda equipment_id, limit: ([], 0))
    payloads = []

    def fake_get_assessment(request_text, grounding_block):
        payloads.append(grounding_block)
        return MaintenanceAssessment(
            equipment_model="CAT 320",
            reported_issue="x",
            category="Hydraulic",
            priority="Medium",
            recommendations=[],
            additional_information_needed=[],
            requires_technician_review=False,
            # One real id and one the agent invented, to exercise the scrub.
            related_work_orders=["WO-10234", "WO-INVENTED"],
            grounding_used=True,
        )

    monkeypatch.setattr(function_app, "get_assessment", fake_get_assessment)
    response = function_app.assess(
        func.HttpRequest(
            method="POST",
            url="http://localhost/api/assess",
            headers={},
            params={},
            route_params={},
            body=json.dumps({"request_text": "Boom raises slowly.", "equipment_id": "CAT320-SE-1042"}).encode(),
        )
    )

    expected_block = format_grounding_block(EQUIPMENT_MASTER["CAT320-SE-1042"], WORK_ORDERS["CAT320-SE-1042"])
    assert payloads[0] == f"{expected_block}\n\n{history_mod._NO_HISTORY_MESSAGE}"
    assert json.loads(response.get_body())["related_work_orders"] == ["WO-10234"]
