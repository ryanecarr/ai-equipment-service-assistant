from typing import Literal, Optional

from pydantic import BaseModel

Category = Literal["Hydraulic", "Electrical", "Engine", "Structural", "Other"]
Priority = Literal["Low", "Medium", "High", "Critical"]
MatchStatus = Literal["matched", "needs_confirmation", "no_match"]


class MaintenanceAssessment(BaseModel):
    equipment_model: str
    equipment_id: Optional[str] = None
    customer: Optional[str] = None
    reported_issue: str
    category: Category
    priority: Priority
    recommendations: list[str]
    additional_information_needed: list[str]
    requires_technician_review: bool
    related_work_orders: list[str]
    grounding_used: bool
    comparison_notes: Optional[str] = None


class EquipmentSummary(BaseModel):
    equipment_id: str
    model: str
    customer: str
    location: Optional[str] = None


# Separate from MaintenanceAssessment, which is also the agent's output schema and what
# gets saved: these are the server's own matching results, so the agent can't set them
# and they don't end up in saved history.
class AssessmentResponse(MaintenanceAssessment):
    match_status: MatchStatus = "no_match"
    equipment_candidates: list[EquipmentSummary] = []
