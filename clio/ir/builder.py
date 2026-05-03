from clio.ir.graph import FlowGraph, StepIR
from clio.parser.ast_nodes import Program, StepDecl


def build_ir(program: Program) -> FlowGraph:
    steps = tuple(_build_step(d) for d in program.decls if isinstance(d, StepDecl))
    return FlowGraph(steps=steps)


def _build_step(decl: StepDecl) -> StepIR:
    return StepIR(name=decl.name, mode=decl.mode, line=decl.line)
