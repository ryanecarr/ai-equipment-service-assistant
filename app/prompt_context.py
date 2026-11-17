"""Everything /api/assess looks up before it calls the agent, in one place, so the
evaluation suite builds its messages with exactly the code the endpoint runs."""

from dataclasses import dataclass
from typing import Any, Optional

from app.grounding.lookup import build_grounding, get_known_work_order_ids, resolve_equipment
from app.grounding.matching import EquipmentMatch
from app.history.lookup import build_history_block


@dataclass(frozen=True)
class PromptContext:
    equipment_match: EquipmentMatch
    matched_record: Optional[dict[str, Any]]
    # The grounding block and the saved-history block, as sent after the "---" separator.
    combined_grounding: str
    has_saved_history: bool
    known_work_order_ids: set[str]


def build_prompt_context(equipment_id: Optional[str], request_text: str) -> PromptContext:
    equipment_match = resolve_equipment(equipment_id, request_text)
    matched_record, grounding_block = build_grounding(equipment_match)
    history_equipment_id = matched_record["equipment_id"] if matched_record else None
    history_block, has_saved_history = build_history_block(history_equipment_id)

    # Fetched before the agent call, not at the scrubbing step afterwards: with Cosmos
    # this is a real query that can fail, and failing here costs no agent call.
    known_work_order_ids = get_known_work_order_ids(history_equipment_id) if matched_record else set()

    return PromptContext(
        equipment_match=equipment_match,
        matched_record=matched_record,
        combined_grounding=f"{grounding_block}\n\n{history_block}",
        has_saved_history=has_saved_history,
        known_work_order_ids=known_work_order_ids,
    )
