from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.schemas.response_models import MaintenanceAssessment

# Well above a real assessment (about 5 KB) and far below Cosmos's 2 MB item limit. Set
# here rather than on MaintenanceAssessment, which is also the agent's output schema.
_MAX_ASSESSMENT_BYTES = 32_000


class SaveAssessmentRequest(BaseModel):
    assessment: MaintenanceAssessment
    saved_by: Optional[str] = Field(default=None, max_length=100)
    associated_work_order_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_assessment_size(self) -> "SaveAssessmentRequest":
        size = len(self.assessment.model_dump_json().encode("utf-8"))
        if size > _MAX_ASSESSMENT_BYTES:
            raise ValueError(f"assessment is {size} bytes; the limit is {_MAX_ASSESSMENT_BYTES}.")
        return self

    @model_validator(mode="after")
    def validate_work_order_membership(self) -> "SaveAssessmentRequest":
        if (
            self.associated_work_order_id is not None
            and self.associated_work_order_id not in self.assessment.related_work_orders
        ):
            raise ValueError(
                f"associated_work_order_id '{self.associated_work_order_id}' must be one of "
                "this assessment's related_work_orders; the server will not invent a new "
                "work-order link."
            )
        return self


class SavedAssessmentRecord(BaseModel):
    equipment_id: str
    saved_at: str
    saved_by: Optional[str] = None
    associated_work_order_id: Optional[str] = None
    assessment: MaintenanceAssessment
