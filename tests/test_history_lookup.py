"""Regression tests for app/history/lookup.py's prompt-budget enforcement.

build_history_block() calls recent_saved_assessments() as a bare module-level name,
so monkeypatching it redirects the read without needing any Azure access.
"""

import app.history.lookup as history_mod
from app.schemas.history_models import SavedAssessmentRecord
from app.schemas.response_models import MaintenanceAssessment


def _make_record(saved_at, reported_issue="issue", recommendations=None, saved_by="tech"):
    recommendations = recommendations if recommendations is not None else ["rec"]
    assessment = MaintenanceAssessment(
        equipment_model="CAT 320",
        equipment_id="CAT320-SE-1042",
        customer="Southeast Construction",
        reported_issue=reported_issue,
        category="Hydraulic",
        priority="Medium",
        recommendations=recommendations,
        additional_information_needed=[],
        requires_technician_review=False,
        related_work_orders=[],
        grounding_used=False,
    )
    return SavedAssessmentRecord(
        equipment_id="CAT320-SE-1042",
        saved_at=saved_at,
        saved_by=saved_by,
        associated_work_order_id=None,
        assessment=assessment,
    )


def _stub_history(monkeypatch, records):
    monkeypatch.setattr(
        history_mod, "recent_saved_assessments", lambda equipment_id, limit: (records[:limit], len(records))
    )


def test_pathologically_long_record_is_bounded(monkeypatch):
    # A single schema-valid record with an enormous reported_issue and a large
    # number of very long recommendations must not blow up the formatted block —
    # per-field truncation must bound it even though the record-count and
    # recommendation-count caps don't apply (there's only one record here, and
    # it's the first 3 recommendations that are unbounded in length).
    huge_issue = "x" * 2_000_000
    huge_recs = ["y" * 100_000 for _ in range(50)]
    record = _make_record("2026-01-01T00:00:00+00:00", reported_issue=huge_issue, recommendations=huge_recs)
    _stub_history(monkeypatch, [record])

    block, has_history = history_mod.build_history_block("CAT320-SE-1042")

    assert has_history is True
    assert len(block) < 5000
    assert "...(truncated)" in block


def test_total_history_budget_caps_many_records(monkeypatch):
    # Even with each individual record kept small, enough saved records must
    # still be capped by the overall prompt budget, not just by the
    # _MAX_HISTORY_RECORDS count cap (which only bounds how many records are
    # considered, not how large the resulting text is).
    records = [
        _make_record(f"2026-01-{5 - i:02d}T00:00:00+00:00", reported_issue="y" * 250)
        for i in range(5)
    ]
    _stub_history(monkeypatch, records)
    monkeypatch.setattr(history_mod, "_MAX_TOTAL_HISTORY_CHARS", 600)

    block, has_history = history_mod.build_history_block("CAT320-SE-1042")

    assert has_history is True
    assert len(block) < 1000
    assert "omitted to stay within the prompt budget" in block
    # the most recent record is always included even under a tight budget
    assert "2026-01-05T00:00:00+00:00" in block
    assert "2026-01-01T00:00:00+00:00" not in block


def test_no_saved_history_reports_false(monkeypatch):
    _stub_history(monkeypatch, [])

    block, has_history = history_mod.build_history_block("CAT320-SE-1042")

    assert has_history is False
    assert block == history_mod._NO_HISTORY_MESSAGE


def test_no_equipment_id_reports_false():
    block, has_history = history_mod.build_history_block(None)

    assert has_history is False
    assert block == history_mod._NO_HISTORY_MESSAGE


def test_block_frames_saved_assessments_as_unverified_ai_guidance(monkeypatch):
    # Regression for a reviewed defect: the agent treated a saved assessment's
    # association with a work order as if that work order's documented resolution
    # covered the assessment's recommendations. The prompt block itself must make
    # clear, both in its header and next to each work-order association, that saved
    # assessments are prior AI output with unknown completion/outcome — not evidence
    # that any recommended diagnostics or repairs were actually performed.
    record = _make_record("2026-01-01T00:00:00+00:00")
    record.associated_work_order_id = "WO-11078"
    _stub_history(monkeypatch, [record])

    block, has_history = history_mod.build_history_block("CAT320-SE-1042")

    assert has_history is True
    assert "AI-generated" in block
    assert "not verified outcomes" in block or "NOT verified outcomes" in block
    assert "WO-11078 (association only" in block
