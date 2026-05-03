"""Pydantic models generated from CLIO CONTRACT declarations."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CustomerRisk(BaseModel):
    """CONTRACT customer_risk."""
    client: str
    risk: Literal['low', 'mid', 'high']
    reason: str = Field(max_length=300)

    @field_validator('reason')
    @classmethod
    def _assert_customer_risk(cls, v):
        reason = v
        if not (len(reason) > 0):
            raise ValueError("ASSERT failed: " + '(len(reason) > 0)')
        return v

