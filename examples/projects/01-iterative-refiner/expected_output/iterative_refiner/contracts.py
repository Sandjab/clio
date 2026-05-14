"""Pydantic models generated from CLIO CONTRACT declarations."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SummaryJudgment(BaseModel):
    """CONTRACT summary_judgment."""
    score: float
    missing_points: list[str]
    verdict: Literal['accept', 'refine']

    @field_validator('score')
    @classmethod
    def _assert_summary_judgment(cls, v):
        score = v
        if not ((0.0 <= score) and (score <= 1.0)):
            raise ValueError("ASSERT failed: " + '((0.0 <= score) and (score <= 1.0))')
        return v

class FinalSummary(BaseModel):
    """CONTRACT final_summary."""
    text: str = Field(max_length=4000)
    iterations: int
    final_score: float

    @field_validator('iterations')
    @classmethod
    def _assert_final_summary(cls, v):
        iterations = v
        if not (iterations >= 1):
            raise ValueError("ASSERT failed: " + '(iterations >= 1)')
        return v

