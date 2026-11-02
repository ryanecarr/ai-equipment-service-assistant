"""Holds the matcher to the labelled requests in matching_eval_cases.json. To change a
result here, change the case's expected outcome deliberately: the point of this suite is
that a shift in matching behavior shows up as a reviewable diff."""

import pytest

from scripts.evaluate_matching import load_catalog, load_cases, outcome_for

CATALOG = load_catalog()
CASES = load_cases()

MIN_LINKED_CORRECTLY = 25
MAX_MISSED = 2


@pytest.mark.parametrize("case", CASES, ids=[case["text"][:60] for case in CASES])
def test_labelled_request_has_its_expected_outcome(case):
    assert outcome_for(CATALOG, case["text"], case["machine"]) == case["outcome"]


def test_no_labelled_request_is_expected_to_link_the_wrong_machine():
    assert not [case for case in CASES if case["outcome"] == "wrong_link"]


def test_overall_score_stays_within_its_limits():
    outcomes = [outcome_for(CATALOG, case["text"], case["machine"]) for case in CASES]

    assert outcomes.count("wrong_link") == 0
    assert outcomes.count("linked") >= MIN_LINKED_CORRECTLY
    assert outcomes.count("missed") <= MAX_MISSED


def test_labels_refer_to_machines_in_the_catalog():
    known = {record["equipment_id"] for record in CATALOG}

    assert {case["machine"] for case in CASES if case["machine"]} <= known
