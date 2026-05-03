from dataclasses import dataclass

from clio.parser.ast_nodes import Field, TypeExpr


@dataclass(frozen=True)
class FieldIR:
    name: str
    type: TypeExpr            # AST type nodes are reused as IR for now


@dataclass(frozen=True)
class StepIR:
    name: str
    mode: str
    takes: tuple[FieldIR, ...]
    gives: FieldIR | None
    line: int


@dataclass(frozen=True)
class FlowGraph:
    steps: tuple[StepIR, ...]
