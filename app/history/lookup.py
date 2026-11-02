from datetime import datetime, timezone
from typing import Optional

from app.grounding.repository import is_valid_document_id
from app.history.repository import get_saved_assessment_repository
from app.schemas.history_models import SavedAssessmentRecord
from app.schemas.response_models import MaintenanceAssessment

_NO_HISTORY_MESSAGE = (
    "PREVIOUSLY SAVED ASSESSMENTS: none found for this equipment in internal systems."
)

# Bounds on how much saved history gets folded into the agent prompt. Without a
# cap, enough saves for one piece of equipment would grow the prompt unboundedly
# — raising latency/cost before eventually exceeding the model's context limit.
# Capping *record count* and *recommendation count* alone isn't enough: nothing
# else bounds the length of any individual string field (reported_issue, a single
# recommendation, saved_by), so one schema-valid record with a pathologically
# long string could still blow the budget on its own. Per-field truncation closes
# that gap; _MAX_TOTAL_HISTORY_CHARS is a second, independent ceiling on the
# fully-formatted block as a whole, enforced after per-field truncation.
_MAX_HISTORY_RECORDS = 5
_MAX_RECOMMENDATIONS_PER_RECORD = 3
_MAX_RECOMMENDATION_CHARS = 200
_MAX_REPORTED_ISSUE_CHARS = 300
_MAX_SAVED_BY_CHARS = 100
_MAX_TOTAL_HISTORY_CHARS = 4000

def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...(truncated)"


def list_saved_assessments(equipment_id: str) -> list[SavedAssessmentRecord]:
    # equipment_id comes straight from the query string on GET /api/assessments.
    if not is_valid_document_id(equipment_id):
        return []
    return get_saved_assessment_repository().list(equipment_id)


def recent_saved_assessments(equipment_id: str, limit: int) -> tuple[list[SavedAssessmentRecord], int]:
    """Returns the newest records, plus how many exist in total. The prompt only uses the
    newest few, so this stays cheap however long a machine's history grows."""
    repository = get_saved_assessment_repository()
    records = repository.list(equipment_id, limit=limit)
    total = repository.count(equipment_id) if len(records) == limit else len(records)
    return records, total


def save_assessment(
    equipment_id: str,
    assessment: MaintenanceAssessment,
    saved_by: Optional[str],
    associated_work_order_id: Optional[str],
) -> SavedAssessmentRecord:
    new_record = SavedAssessmentRecord(
        equipment_id=equipment_id,
        saved_at=datetime.now(timezone.utc).isoformat(),
        saved_by=saved_by,
        associated_work_order_id=associated_work_order_id,
        assessment=assessment,
    )
    get_saved_assessment_repository().add(new_record)
    return new_record


def build_history_block(equipment_id: Optional[str]) -> tuple[str, bool]:
    """Format saved history into a labeled prompt block. Returns (block, has_history) —
    the caller can read has_history deterministically rather than string-matching the
    block or trusting the agent to correctly report whether history was supplied."""
    if equipment_id is None:
        return _NO_HISTORY_MESSAGE, False

    shown, total = recent_saved_assessments(equipment_id, _MAX_HISTORY_RECORDS)
    if not shown:
        return _NO_HISTORY_MESSAGE, False

    lines = [
        "PREVIOUSLY SAVED ASSESSMENTS (prior AI-generated analysis and recommendations that a "
        "technician chose to save for this exact equipment — NOT verified outcomes. Saving one, "
        "or associating it with a work order below, does not confirm its recommendations were "
        "performed, its diagnosis was confirmed, or the reported symptom was resolved. Any work "
        "order's own documented resolution/notes, supplied separately under KNOWN PRIOR RECORDS, "
        "is the only authoritative record of what was actually done):",
    ]
    if total > len(shown):
        lines.append(f"Showing the {len(shown)} most recent of {total} saved assessments.")

    running_chars = sum(len(line) + 1 for line in lines)
    included = 0

    for rec in shown:
        a = rec.assessment
        saved_by = _truncate(rec.saved_by or "unspecified technician", _MAX_SAVED_BY_CHARS)
        wo_note = (
            f", associated with work order {rec.associated_work_order_id} (association only — "
            "not confirmation that the work order addressed this)"
            if rec.associated_work_order_id
            else ""
        )
        recs = [_truncate(r, _MAX_RECOMMENDATION_CHARS) for r in a.recommendations[:_MAX_RECOMMENDATIONS_PER_RECORD]]
        omitted_recs = len(a.recommendations) - len(recs)
        recs_text = str(recs) + (f" (+{omitted_recs} more)" if omitted_recs > 0 else "")
        reported_issue = _truncate(a.reported_issue, _MAX_REPORTED_ISSUE_CHARS)
        line = (
            f"- Saved {rec.saved_at} by {saved_by}{wo_note}: category={a.category}, "
            f"priority={a.priority}, reported_issue=\"{reported_issue}\", "
            f"recommendations={recs_text}"
        )

        # Per-field truncation above bounds any single record to a predictable size, but
        # this is still enforced independently: stop adding records once the formatted
        # block itself hits the overall budget, rather than trusting the per-field caps
        # to add up correctly. Always include at least the most recent record.
        if included > 0 and running_chars + len(line) + 1 > _MAX_TOTAL_HISTORY_CHARS:
            remaining = len(shown) - included
            lines.append(f"({remaining} additional saved assessment(s) omitted to stay within the prompt budget.)")
            break

        lines.append(line)
        running_chars += len(line) + 1
        included += 1

    return "\n".join(lines), True
