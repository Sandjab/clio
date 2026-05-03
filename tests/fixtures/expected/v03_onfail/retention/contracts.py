"""Pydantic models generated from CLIO CONTRACT declarations."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CustomerRisk(BaseModel):
    """CONTRACT customer_risk."""
    client: str
    risk: Literal['low', 'mid', 'high']
    reason: str

