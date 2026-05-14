"""Type-utility helpers shared by emitter modules.

Originally lived in `_python_helpers.py`; extracted in v0.14 because
3 emitters now consume the same shape-rendering and naming logic
(python, mcp-server, claude-skill).

The CLAUDE.md rule "emitters never import from each other" continues
to hold: `_shared_utils.py` is a utility module, not an emitter. Both
emitter helper modules (`_python_helpers.py`, `_mcp_helpers.py`,
`_claude_skill_helpers.py`) import from here.
"""
from __future__ import annotations

import keyword

from clio.ir.graph import (
    ContractIR,
    FlowGraph,
    StepIR,
)
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)

_PYTHON_PRIMITIVES = {"int": "int", "float": "float", "str": "str", "bool": "bool"}

_MODEL_ID_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}


def _to_class_name(name: str) -> str:
    """customer_risk -> CustomerRisk."""
    return "".join(part.capitalize() for part in name.split("_"))


def _to_field_name(name: str) -> str:
    """Identity for valid identifiers; suffixes a `_` for Python keywords."""
    if keyword.iskeyword(name):
        return f"{name}_"
    return name


def _render_system_prompt(step: StepIR) -> list[str]:
    """Render the `_SYSTEM_PROMPT = (...)` block for a judgment step.

    Always emits the strict JSON-only directive. When the step declares
    DESCRIPTION or STRATEGIES (v0.15), appends them as a labelled section
    so the model can use them as judgment context without being misled
    about the output contract. Output is byte-identical to the pre-v0.15
    emitter when neither field is set.

    Shared by the python and mcp-server targets — both emit the same
    `_SYSTEM_PROMPT` module constant, so the v0.15 enrichment applies
    uniformly. Was previously python-only; mcp-server compiled the
    legacy literal, silently dropping DESCRIPTION/STRATEGIES."""
    legacy = [
        "_SYSTEM_PROMPT = (",
        "    'You are a strict JSON-only API. Output exactly one JSON document matching '",
        "    'the requested schema, with no prose, no markdown code fences, no commentary, '",
        "    'and no leading or trailing whitespace beyond the JSON itself.'",
        ")",
    ]
    if not step.description and not step.strategies:
        return legacy
    extras: list[str] = []
    if step.description:
        extras.append("Step intent: " + step.description.replace("\n", " "))
    if step.strategies:
        extras.append("Heuristics:\n" + step.strategies)
    suffix = "\n\n" + "\n\n".join(extras)
    return [*legacy[:-1], f"    {suffix!r}", legacy[-1]]


def _safe_package_name(graph: FlowGraph, default: str) -> str:
    """Return a Python-importable package name derived from `graph.flow.name`.

    CLIO identifiers accept Python reserved/soft keywords (`class`, `match`, …),
    but a package literally named `class` produces `from class.flow import …`,
    which is a SyntaxError. Suffix `_` for the rare collisions; default when
    no FLOW is declared (e.g. CONTRACT-only sources)."""
    if graph.flow is None:
        return default
    name = graph.flow.name
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        return f"{name}_"
    return name


def _model_id(short_name: str) -> str:
    return _MODEL_ID_MAP.get(short_name, short_name)


def _uses_contract_refs(step: StepIR) -> bool:
    """True iff the step's TAKES or GIVES type tree references any ContractRef.
    Determines whether the emitted step module needs `from .. import contracts`
    — otherwise the `contracts.Foo` qualifier in the type annotation is an
    unresolved name (harmless under `from __future__ import annotations` but
    ugly and breaks `typing.get_type_hints`)."""
    def walk(t: TypeExpr) -> bool:
        if isinstance(t, ContractRef):
            return True
        if isinstance(t, ListType):
            return walk(t.inner)
        if isinstance(t, RecordType):
            return any(walk(ty) for _, ty in t.fields)
        if isinstance(t, ConstrainedType):
            return walk(t.base)
        return False

    if step.gives is not None and walk(step.gives.type):
        return True
    return any(walk(f.type) for f in step.takes)


def _render_type_short(t: TypeExpr) -> str:
    """Human-readable type rendering for docstrings."""
    if isinstance(t, PrimitiveType):
        return t.name
    if isinstance(t, ListType):
        return f"List<{_render_type_short(t.inner)}>"
    if isinstance(t, EnumType):
        return f"enum({'|'.join(t.values)})"
    if isinstance(t, ConstrainedType):
        cs = ", ".join(f"{k}={v}" for k, v in t.constraints)
        return f"{_render_type_short(t.base)}({cs})"
    if isinstance(t, ContractRef):
        return t.name
    if isinstance(t, RecordType):
        return "{" + ", ".join(f"{n}: {_render_type_short(ty)}" for n, ty in t.fields) + "}"
    return type(t).__name__


def _type_to_python(t: TypeExpr, contracts: dict[str, ContractIR]) -> str:
    if isinstance(t, PrimitiveType):
        return _PYTHON_PRIMITIVES[t.name]
    if isinstance(t, ListType):
        return f"list[{_type_to_python(t.inner, contracts)}]"
    if isinstance(t, EnumType):
        values = ", ".join(repr(v) for v in t.values)
        return f"Literal[{values}]"
    if isinstance(t, ConstrainedType):
        return _type_to_python(t.base, contracts)
    if isinstance(t, ContractRef):
        # Step modules import `from .. import contracts`, so qualify the ref —
        # an unqualified name breaks typing.get_type_hints under `from __future__
        # import annotations`.
        return f"contracts.{_to_class_name(t.name)}"
    if isinstance(t, RecordType):
        # Anonymous nested records: typed as `dict` for v0.3.
        # The contract's BaseModel handles structured validation.
        return "dict"
    raise ValueError(f"unhandled type for Python emit: {type(t).__name__}")


def _json_type_to_python(schema: dict) -> str:
    if "$ref" in schema:
        ref = schema["$ref"]
        name = ref.rsplit("/", 1)[-1]
        return _to_class_name(name)
    if "enum" in schema:
        values = ", ".join(repr(v) for v in schema["enum"])
        return f"Literal[{values}]"
    t = schema.get("type")
    if t == "string":
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        return f"list[{_json_type_to_python(schema.get('items', {}))}]"
    if t == "object":
        return "dict"
    return "object"


def _shape_from_schema(schema: dict) -> list[tuple[str, dict]]:
    """Return [(field_name, field_subschema), ...] preserving declaration order."""
    return list(schema.get("properties", {}).items())


def _field_from_schema(name: str, schema: dict) -> str:
    py_name = _to_field_name(name)
    py_type = _json_type_to_python(schema)
    # Build a list of Field(...) kwargs so we can compose alias + max_length
    # uniformly. Pydantic v2 needs `alias=` (and `validation_alias=`) so the
    # original CLIO field name still parses from JSON input when py_name was
    # renamed to avoid a Python-keyword collision.
    field_kwargs: list[str] = []
    if py_name != name:
        field_kwargs.append(f"alias={name!r}")
        field_kwargs.append(f"validation_alias={name!r}")
    if schema.get("type") == "string" and "maxLength" in schema:
        field_kwargs.append(f"max_length={schema['maxLength']}")
    if field_kwargs:
        return f"{py_name}: {py_type} = Field({', '.join(field_kwargs)})"
    return f"{py_name}: {py_type}"


# ---------------------------------------------------------------------------
# Chain helpers used by every emitter that walks a FlowIR.chain (python,
# mcp-server, langgraph). Live here — and NOT inside an emitter helper
# module — so that emitter helpers do not need to import from each other
# (CLAUDE.md: "Emitters never import from each other").

def _has_parallel(chain) -> bool:
    """Return True if any ForEachIR in the chain (or nested) has parallel=True.
    Used by emitters to decide whether to emit `import concurrent.futures` /
    `import asyncio` at module top of the emitted flow.py."""
    from clio.ir.graph import (  # avoid top-level circular import
        ForEachIR,
        IfBlockIR,
        MatchBlockIR,
        WhileBlockIR,
    )
    for elem in chain:
        if isinstance(elem, ForEachIR):
            if elem.parallel:
                return True
            if _has_parallel(elem.body):
                return True
        elif isinstance(elem, IfBlockIR):
            if _has_parallel(elem.then_body) or _has_parallel(elem.else_body):
                return True
        elif isinstance(elem, MatchBlockIR):
            for arm in elem.cases:
                if _has_parallel(arm.body):
                    return True
        elif isinstance(elem, WhileBlockIR):
            if _has_parallel(elem.body):
                return True
    return False


def _python_condition_expr(condition, scope_local: set[str]) -> str:
    """Render an IF/WHILE condition as a Python boolean expression.

    Leaf comparisons (`ConditionIR`) read the contract field via attribute
    access (Pydantic models); the base is a bare name when the state field
    is in `scope_local` (e.g. inside a FOR EACH body), otherwise it's
    `state[<name>]`. `BoolOpIR` nodes render as `(<left>) and/or (<right>)`
    — the parentheses are unconditional so the emitted Python preserves
    the IR's precedence regardless of nesting."""
    from clio.ir.graph import BoolOpIR  # avoid top-level circular import

    if isinstance(condition, BoolOpIR):
        left = _python_condition_expr(condition.left, scope_local)
        right = _python_condition_expr(condition.right, scope_local)
        return f"({left}) {condition.op} ({right})"
    base = (
        condition.step_name
        if condition.step_name in scope_local
        else f"state[{condition.step_name!r}]"
    )
    access = f"{base}.{condition.field}"
    if condition.literal_kind == "int":
        lit = str(condition.literal_value)
    elif condition.literal_kind == "float":
        lit = repr(condition.literal_value)
    elif condition.literal_kind == "bool":
        lit = "True" if condition.literal_value else "False"
    else:
        # str | ident — both rendered as Python string literals
        lit = repr(condition.literal_value)
    return f"{access} {condition.op} {lit}"
