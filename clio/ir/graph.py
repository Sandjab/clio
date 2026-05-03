from dataclasses import dataclass

from clio.parser.ast_nodes import TypeExpr


@dataclass(frozen=True)
class FieldIR:
    name: str
    type: TypeExpr


@dataclass(frozen=True)
class StepIR:
    name: str
    mode: str
    takes: tuple[FieldIR, ...]
    gives: FieldIR | None
    line: int


@dataclass(frozen=True)
class ContractIR:
    name: str
    json_schema: dict
    line: int


@dataclass(frozen=True)
class CallIR:
    step_name: str
    kwargs: tuple[tuple[str, object], ...]
    line: int


@dataclass(frozen=True)
class FlowIR:
    name: str
    chain: tuple[CallIR, ...]
    line: int


@dataclass(frozen=True)
class FlowGraph:
    steps: tuple[StepIR, ...]
    contracts: tuple[ContractIR, ...] = ()
    flow: FlowIR | None = None
