"""Regression tests for app/grounding/lookup.py's equipment-matching logic.

These tests seed the repository with a fake container (see conftest.py), so no Azure
credentials or network access are needed.
"""

import pytest

import app.grounding.lookup as lookup


@pytest.fixture(autouse=True)
def seed_equipment_master(seed_grounding):
    equipment_master = {
        "CAT-A": {
            "equipment_id": "CAT-A",
            "model": "CAT 320",
            "customer": "Southeast Construction",
            "hours": 100,
            "last_pm_date": "2026-01-01",
        },
        "CAT-B": {
            "equipment_id": "CAT-B",
            "model": "CAT 320",
            "customer": "Northridge Excavation",
            "hours": 200,
            "last_pm_date": "2026-01-02",
        },
        "DEERE-A": {
            "equipment_id": "DEERE-A",
            "model": "John Deere 350G",
            "customer": "Southeast Construction",
            "hours": 300,
            "last_pm_date": "2026-01-03",
        },
        "KOMATSU-A": {
            "equipment_id": "KOMATSU-A",
            "model": "Komatsu PC200",
            "customer": "Riverside Paving",
            "hours": 400,
            "last_pm_date": "2026-01-04",
        },
    }
    work_orders = {
        "CAT-A": [{"work_order_id": "WO-1", "date": "2026-01-01", "reported_issue": "x",
                    "resolution": "y", "technician_notes": "z"}],
        "CAT-B": [],
        "DEERE-A": [],
        "KOMATSU-A": [],
    }
    seed_grounding(equipment_master, work_orders)


def test_tied_model_match_returns_no_match():
    # "CAT 320" matches both CAT-A and CAT-B's model; neither customer is mentioned,
    # so both tie at score 1 — ambiguous, must not silently pick either one.
    record, block = lookup.find_grounding(None, "The CAT 320 has slow hydraulics.")
    assert record is None
    assert block == lookup._NO_RECORDS_MESSAGE


def test_tied_customer_match_returns_no_match():
    # "Southeast Construction" matches both CAT-A and DEERE-A's customer; no model
    # keyword is mentioned, so both tie at score 1 — ambiguous.
    record, block = lookup.find_grounding(None, "Southeast Construction reported an issue.")
    assert record is None
    assert block == lookup._NO_RECORDS_MESSAGE


def test_unique_highest_scoring_match_still_resolves():
    # CAT 320 + Southeast Construction uniquely identifies CAT-A (score 2) over
    # CAT-B (model only, score 1) and DEERE-A (customer only, score 1) — the fix
    # must not break the normal, unambiguous case.
    record, block = lookup.find_grounding(
        None, "A CAT 320 excavator issue. Customer is Southeast Construction."
    )
    assert record is not None
    assert record["equipment_id"] == "CAT-A"
    assert "WO-1" in block


def test_explicit_equipment_id_takes_precedence_over_ambiguous_text():
    # An explicit equipment_id resolves directly and never goes through the
    # scoring/tie logic, even if the free text alone would be ambiguous.
    record, _ = lookup.find_grounding("CAT-B", "The CAT 320 has slow hydraulics.")
    assert record is not None
    assert record["equipment_id"] == "CAT-B"


def test_no_keywords_match_anything_returns_no_match():
    record, block = lookup.find_grounding(None, "Completely unrelated text with no matches.")
    assert record is None
    assert block == lookup._NO_RECORDS_MESSAGE


def test_longer_model_identifier_does_not_falsely_match_shorter_known_model():
    # Plain substring matching would treat "PC200" as present inside "PC2000" and
    # incorrectly attach KOMATSU-A's history to a different, longer model number
    # entirely. Word-boundary matching must reject this.
    record, block = lookup.find_grounding(None, "The Komatsu PC2000 bucket is leaking.")
    assert record is None
    assert block == lookup._NO_RECORDS_MESSAGE


def test_exact_model_boundary_still_matches():
    # Regression check: the word-boundary fix must not break matching the real,
    # exact model name.
    record, block = lookup.find_grounding(
        None, "The Komatsu PC200 bucket is leaking. Customer is Riverside Paving."
    )
    assert record is not None
    assert record["equipment_id"] == "KOMATSU-A"


def test_equipment_exists():
    assert lookup.equipment_exists("CAT-A") is True
    assert lookup.equipment_exists("NOT-A-REAL-ID") is False


def test_get_known_work_order_ids():
    assert lookup.get_known_work_order_ids("CAT-A") == {"WO-1"}
    assert lookup.get_known_work_order_ids("CAT-B") == set()
    assert lookup.get_known_work_order_ids("NOT-A-REAL-ID") == set()
