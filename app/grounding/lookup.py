from typing import Any, Optional

from app.grounding.matching import EquipmentMatch, match_equipment
from app.grounding.repository import get_equipment_repository
from app.schemas.response_models import EquipmentSummary

_NO_RECORDS_MESSAGE = (
    "KNOWN PRIOR RECORDS: none found for this equipment/customer in internal systems."
)


def resolve_equipment(equipment_id: Optional[str], request_text: str) -> EquipmentMatch:
    repository = get_equipment_repository()
    if equipment_id:
        record = repository.get_equipment(equipment_id)
        return EquipmentMatch("matched", record=record) if record else EquipmentMatch("no_match")
    return match_equipment(repository.list_equipment(), request_text)


def list_equipment_summaries() -> list[EquipmentSummary]:
    records = sorted(
        get_equipment_repository().list_equipment(),
        key=lambda r: (r["customer"], r["model"], r["equipment_id"]),
    )
    return [to_equipment_summary(record) for record in records]


def to_equipment_summary(record: dict[str, Any]) -> EquipmentSummary:
    return EquipmentSummary(
        equipment_id=record["equipment_id"],
        model=record["model"],
        customer=record["customer"],
        location=record.get("location"),
    )


def equipment_exists(equipment_id: str) -> bool:
    return get_equipment_repository().get_equipment(equipment_id) is not None


def get_known_work_order_ids(equipment_id: str) -> set[str]:
    return {wo["work_order_id"] for wo in get_equipment_repository().get_work_orders(equipment_id)}


def format_grounding_block(record: dict[str, Any], prior_work_orders: list[dict[str, Any]]) -> str:
    lines = [
        "KNOWN PRIOR RECORDS (from internal systems, for reference only — not manufacturer documentation):",
        f"Equipment: {record['model']} ({record['equipment_id']}), Customer: {record['customer']}, "
        f"Hours: {record['hours']}, Last PM: {record['last_pm_date']}",
    ]

    if prior_work_orders:
        lines.append("Prior service history:")
        for wo in prior_work_orders:
            lines.append(
                f"- {wo['date']} [{wo['work_order_id']}]: Reported \"{wo['reported_issue']}\" — "
                f"Resolution: {wo['resolution']}. Notes: {wo['technician_notes']}"
            )
    else:
        lines.append("Prior service history: none on file for this equipment.")

    return "\n".join(lines)


def find_grounding(equipment_id: Optional[str], request_text: str) -> tuple[Optional[dict[str, Any]], str]:
    """Look up the matching equipment record and format it into a labeled grounding
    block for the agent. Returns (matched_record_or_None, formatted_block) — the caller
    can read customer/equipment_id deterministically from the record rather than trusting
    the agent to echo them back correctly."""
    return build_grounding(resolve_equipment(equipment_id, request_text))


def build_grounding(match: EquipmentMatch) -> tuple[Optional[dict[str, Any]], str]:
    # Candidates stay out of the prompt on purpose: they're unconfirmed, so the agent
    # gets the same no-records message as any other request without a linked machine.
    if match.record is None:
        return None, _NO_RECORDS_MESSAGE

    work_orders = get_equipment_repository().get_work_orders(match.record["equipment_id"])
    return match.record, format_grounding_block(match.record, work_orders)
