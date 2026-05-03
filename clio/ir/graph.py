from dataclasses import dataclass


@dataclass(frozen=True)
class StepIR:
    name: str
    mode: str
    line: int


@dataclass(frozen=True)
class FlowGraph:
    steps: tuple[StepIR, ...]
