from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MaintenanceRequest(BaseModel):
    request_text: str = Field(..., min_length=1)
    equipment_id: Optional[str] = None

    @field_validator("request_text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("request_text must not be blank")
        return v
