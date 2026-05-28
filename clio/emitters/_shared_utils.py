"""Type-utility helpers shared by emitter modules.

Originally lived in `_python_helpers.py`; extracted in v0.14 because
now 4 emitters consume the same shape-rendering and naming logic
(python, mcp-server, claude-skill, go).

The CLAUDE.md rule "emitters never import from each other" continues
to hold: `_shared_utils.py` is a utility module, not an emitter. Both
emitter helper modules (`_python_helpers.py`, `_mcp_helpers.py`,
`_claude_skill_helpers.py`, `_go_helpers.py`) import from here.
"""
from __future__ import annotations

import keyword

from clio.ir.graph import (
    BoolOpIR,
    ConditionIR,
    ContractIR,
    FlowGraph,
    StepIR,
)
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    DictType,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)

_PYTHON_PRIMITIVES = {"int": "int", "float": "float", "str": "str", "bool": "bool"}

_GO_PRIMITIVES = {
    "str": "string",
    "int": "int64",
    "float": "float64",
    "bool": "bool",
    "any": "any",
}

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
    which is a SyntaxError. Suffix `_` for the rare collisions; fall back to
    `default` when no FLOW is selected (e.g. CONTRACT-only sources, or a
    multi-FLOW source compiled without `--flow`). The default is also
    keyword-sanitized so callers that derive it from a FLOW name (e.g.
    mcp_server's "first declared exposed FLOW" fallback) need not double-check."""
    name = graph.flow.name if graph.flow is not None else default
    if keyword.iskeyword(name) or keyword.issoftkeyword(name):
        return f"{name}_"
    return name


def _model_id(short_name: str) -> str:
    return _MODEL_ID_MAP.get(short_name, short_name)


# Lambda inlined into every emitted prompt-substitution `json.dumps(...)`
# call. Pydantic v2 instances are not natively JSON-serializable, so we
# pass a `default` handler that walks any nested `BaseModel` via
# `.model_dump()`. Handles `ContractRef`, `List<ContractRef>`, deeply
# nested structures, and anonymous records containing contracts uniformly
# at runtime — no compile-time type walking needed.
_PROMPT_SUBST_DEFAULT = (
    "lambda o: o.model_dump() if hasattr(o, 'model_dump') else str(o)"
)


def _prompt_subst_expr(name: str) -> str:
    """Render the `json.dumps(<name>, default=...)` expression used to
    substitute a TAKES value into a judgment step's prompt template.

    The runtime `default=` handler covers every Pydantic-bearing shape
    uniformly, including nested ones (`List<List<ContractRef>>`,
    anonymous records containing contracts), without compile-time
    type analysis."""
    py_name = _to_field_name(name)
    return f"json.dumps({py_name}, default={_PROMPT_SUBST_DEFAULT})"


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
        if isinstance(t, DictType):
            return walk(t.key) or walk(t.value)
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
    if isinstance(t, DictType):
        return f"Dict<{_render_type_short(t.key)}, {_render_type_short(t.value)}>"
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
    if isinstance(t, DictType):
        return (
            f"dict[{_type_to_python(t.key, contracts)}, "
            f"{_type_to_python(t.value, contracts)}]"
        )
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


def _json_type_to_go(schema: dict) -> str:
    """Render a JSON Schema subschema dict as a Go type expression.

    Mirror of `_json_type_to_python` for the Go target. Used by
    `render_contracts_go` to convert CONTRACT property schemas — which are
    plain dicts, not TypeExpr nodes — into Go field types.

    $ref resolves to an UpperCamelCase struct name (mirrors ContractRef
    handling in `_type_to_go`). enum → string (same as TypeExpr EnumType).
    Unrecognised shapes → any."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _to_class_name(name)
    if "enum" in schema:
        return "string"
    t = schema.get("type")
    if t == "string":
        return "string"
    if t == "integer":
        return "int64"
    if t == "number":
        return "float64"
    if t == "boolean":
        return "bool"
    if t == "array":
        return f"[]{_json_type_to_go(schema.get('items', {}))}"
    if t == "object":
        # Dict<str, V> shapes carry a typed `additionalProperties` subschema;
        # recurse so Go gets `map[string]<V>` instead of `map[string]any`.
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            return f"map[string]{_json_type_to_go(ap)}"
        return "map[string]any"
    return "any"


def _collect_contract_refs(step: StepIR) -> set[str]:
    """Return the set of CONTRACT names referenced in step's TAKES or GIVES.

    Mirrors the walker in `_uses_contract_refs` but collects names instead
    of returning a bool. Used by `render_contracts_go` to decide which
    contracts to emit struct definitions for."""
    refs: set[str] = set()

    def walk(t: TypeExpr) -> None:
        if isinstance(t, ContractRef):
            refs.add(t.name)
        elif isinstance(t, ListType):
            walk(t.inner)
        elif isinstance(t, DictType):
            walk(t.key)
            walk(t.value)
        elif isinstance(t, RecordType):
            for _, ty in t.fields:
                walk(ty)
        elif isinstance(t, ConstrainedType):
            walk(t.base)

    if step.gives is not None:
        walk(step.gives.type)
    for field in step.takes:
        walk(field.type)
    return refs


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
        # Dict<str, V> shapes carry a typed `additionalProperties` subschema;
        # recurse so Pydantic gets `dict[str, V]` instead of bare `dict`.
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            return f"dict[str, {_json_type_to_python(ap)}]"
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
    if isinstance(condition, BoolOpIR):
        left = _python_condition_expr(condition.left, scope_local)
        right = _python_condition_expr(condition.right, scope_local)
        return f"({left}) {condition.op} ({right})"
    base = (
        condition.step_name
        if condition.step_name in scope_local
        else f"state[{condition.step_name!r}]"
    )
    # The CONTRACT field on the Pydantic model has been renamed if its CLIO
    # name is a Python keyword (`class` → `class_`, `return` → `return_`, …),
    # so attribute access here must follow the same rename — otherwise the
    # emitted Python is a SyntaxError on hard keywords or an AttributeError
    # on softer ones.
    access = f"{base}.{_to_field_name(condition.field)}"
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


def _go_condition_expr(
    condition: ConditionIR | BoolOpIR,
    scope_local: set[str],
    state_field_to_step: dict[str, StepIR],
) -> str:
    """Render a CLIO IF/WHILE condition as a Go boolean expression.

    Mirrors `_python_condition_expr` but produces Go syntax.  The key
    difference: state values are `any` (= `interface{}`), so reading a
    contract field requires a type assertion.

    `ConditionIR.step_name` is the *state field name* (the GIVES name of
    whatever step produced it — not the step's own name).  The type
    assertion form is:

        state["<state_field>"].(steps.<StepClassName>Out).<GoField>

    where `<StepClassName>` is the UpperCamelCase rendering of the *step*
    that GIVES into `<state_field>`.  `state_field_to_step` supplies this
    mapping (built by the caller).

    When `state_field` is in `scope_local` (loop variable inside FOR EACH
    body) the local identifier is used directly without `state[...]`:

        <state_field>.(steps.<StepClassName>Out).<GoField>

    `BoolOpIR` nodes render as `(<left>) &&/|| (<right>)` — unconditional
    parens preserve IR precedence regardless of nesting depth."""
    if isinstance(condition, BoolOpIR):
        left = _go_condition_expr(condition.left, scope_local, state_field_to_step)
        right = _go_condition_expr(condition.right, scope_local, state_field_to_step)
        go_op = "&&" if condition.op == "and" else "||"
        return f"({left}) {go_op} ({right})"

    # Leaf: ConditionIR
    # condition.step_name is the state-dict key (GIVES field name of the
    # step that produced it), not the step's own name.
    state_field = condition.step_name
    step = state_field_to_step.get(state_field)
    if step is not None:
        cls = _to_class_name(step.name)
        type_assert = f"(steps.{cls}Out)"
    else:
        # Fallback: unknown state field — use `any`.  Should not happen after
        # IR validation, but guards against future call-site bugs.
        type_assert = "(any)"
    if state_field in scope_local:
        base = f"{state_field}.{type_assert}"
    else:
        base = f'state["{state_field}"].{type_assert}'
    access = f"{base}.{_to_go_field_name(condition.field)}"

    # Render the RHS literal in Go syntax.
    if condition.literal_kind == "int":
        lit = f"int64({condition.literal_value})"
    elif condition.literal_kind == "float":
        lit = f"float64({condition.literal_value})"
    elif condition.literal_kind == "bool":
        lit = "true" if condition.literal_value else "false"
    elif condition.literal_kind == "ident":
        # Enum ident rendered as a Go string constant (unquoted idents are
        # enum values in CLIO; at runtime they compare against string fields).
        lit = f'"{condition.literal_value}"'
    else:
        # str — Go interpreted string literal: double-quote with backslash escapes.
        escaped = str(condition.literal_value).replace("\\", "\\\\").replace('"', '\\"')
        lit = f'"{escaped}"'
    return f"{access} {condition.op} {lit}"


def _to_go_field_name(name: str) -> str:
    """CLIO field name → Go exported identifier (UpperCamelCase).

    Mirrors `_to_field_name` (which targets Python snake_case). Go exports
    require capitalised first letter to be visible across packages. Splits on
    both `_` and `-` separators — CLIO identifiers may use either — then
    capitalises each part and joins without separator."""
    parts = [p for p in name.replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _type_to_go(
    t: TypeExpr, contracts: dict[str, ContractIR], *, qualifier: str = ""
) -> str:
    """Render a CLIO TypeExpr as a Go type expression.

    Used both inline (struct field types) and standalone (variable types).
    Mirrors `_type_to_python` for the python target. RecordType emits an
    anonymous Go struct with json struct tags so that `encoding/json` round-trips
    field names without manual mapping. EnumType emits `string` — the schema-level
    constraint enforces the value set at Validate() time; generating named Go
    enum types is deferred to a future refactor.

    `qualifier`, when non-empty, prefixes ContractRef names with the given
    package qualifier (e.g. `qualifier="contracts"` → `contracts.CustomerRisk`).
    Step files that live in a separate `steps/` package need this to reference
    types from the `contracts/` package; contracts.go itself uses bare names
    (same package) and must leave `qualifier` at its default empty string."""
    if isinstance(t, ConstrainedType):
        return _type_to_go(t.base, contracts, qualifier=qualifier)
    if isinstance(t, PrimitiveType):
        return _GO_PRIMITIVES[t.name]
    if isinstance(t, EnumType):
        # v0.20.0: enums render as plain `string`; the schema-level enum
        # constraint enforces the value set at Validate() time. Typed Go
        # enum types are deferred to a future refactor.
        return "string"
    if isinstance(t, ListType):
        return f"[]{_type_to_go(t.inner, contracts, qualifier=qualifier)}"
    if isinstance(t, DictType):
        # v0.21: Dict keys are always `str` (enforced by the parser), so the
        # Go type is `map[string]<V>`. Re-render the key for forward-compat
        # in case future versions relax the constraint.
        return (
            f"map[{_type_to_go(t.key, contracts, qualifier=qualifier)}]"
            f"{_type_to_go(t.value, contracts, qualifier=qualifier)}"
        )
    if isinstance(t, RecordType):
        fields = "; ".join(
            f'{_to_go_field_name(fname)} {_type_to_go(ftype, contracts, qualifier=qualifier)} '
            f'`json:"{fname}"`'
            for fname, ftype in t.fields
        )
        return f"struct {{ {fields} }}"
    if isinstance(t, ContractRef):
        cls = _to_class_name(t.name)
        if qualifier:
            return f"{qualifier}.{cls}"
        return cls
    raise ValueError(f"unsupported TypeExpr for Go target: {type(t).__name__}")
