from pydantic import BaseModel, ConfigDict, Field


# Checks only the fields the grounding block and /assess actually read. extra="allow"
# keeps the rest of the source record (manufacturer, serial_number, location, ...)
# valid without this schema having to track every business field.
class EquipmentRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    equipment_id: str = Field(..., min_length=1)
    # min_length: an empty model/customer would match every request in the free-text
    # scoring in app/grounding/lookup.py, since \b\b matches at any word boundary.
    model: str = Field(..., min_length=1)
    customer: str = Field(..., min_length=1)
    hours: int
    last_pm_date: str


class WorkOrderRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    work_order_id: str = Field(..., min_length=1)
    date: str
    reported_issue: str
    resolution: str
    technician_notes: str
