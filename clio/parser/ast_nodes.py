from dataclasses import dataclass


@dataclass(frozen=True)
class StepDecl:
    name: str
    mode: str               # "exact" or "judgment"
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    decls: tuple[StepDecl, ...]
