"""Module-level helpers for the Python emitter.

These functions are pure (no I/O, no global state). The emitter class lives
in `clio/emitters/python.py` and imports from this module.

Split out per CLAUDE.md scope-discipline rule (~300-line limit per file).
"""

import keyword

from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    ContractIR,
    InvokeIR,
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


# Pydantic v2 attributes that raise PydanticUserError/ValueError when used as
# field names (verified against pydantic 2.x). The shadowing-only `model_*`
# names are not blocked, just warned about, so we let them through.
_PYDANTIC_RESERVED_FIELDS = frozenset({
    "model_config",
    "model_dump",
    "model_dump_json",
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
})


_MODEL_ID_MAP = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
}


def _model_id(short_name: str) -> str:
    return _MODEL_ID_MAP.get(short_name, short_name)


def _gives_validator_expr(gives) -> str:
    """Python expression that, given a parsed-JSON value, returns the validated GIVES."""
    if gives is None:
        return "(lambda raw: raw)"
    t = gives.type
    if isinstance(t, ContractRef):
        return f"contracts.{_to_class_name(t.name)}.model_validate"
    if isinstance(t, ListType) and isinstance(t.inner, ContractRef):
        cls = _to_class_name(t.inner.name)
        return f"(lambda raw: [contracts.{cls}.model_validate(item) for item in raw])"
    return "(lambda raw: raw)"


def _to_class_name(name: str) -> str:
    """customer_risk -> CustomerRisk."""
    return "".join(part.capitalize() for part in name.split("_"))


def _to_field_name(name: str) -> str:
    """Identity for valid identifiers; suffixes a `_` for Python keywords."""
    if keyword.iskeyword(name):
        return f"{name}_"
    return name


def _type_to_python(t: TypeExpr, contracts: dict[str, "ContractIR"]) -> str:
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


def _ast_to_python(node: dict) -> str:
    """Render a clio assert AST node as a Python expression string.

    The AST shape comes from `clio.parser.expressions.expr_to_json_ast`:
    nodes have a `kind` field, and `compare`/`call` carry their operator/func
    in dedicated fields rather than overloading `kind`.
    """
    kind = node.get("kind")
    if kind == "ident":
        return node["name"]
    if kind == "int":
        return repr(node["value"])
    if kind == "float":
        return repr(node["value"])
    if kind == "str":
        return repr(node["value"])
    if kind == "call":
        func = node["func"]
        if func != "len":
            raise ValueError(f"unsupported function in assert AST: {func!r}")
        return f"len({_ast_to_python(node['args'][0])})"
    if kind == "compare":
        op = node["op"]
        return f"({_ast_to_python(node['left'])} {op} {_ast_to_python(node['right'])})"
    raise ValueError(f"unhandled assert AST kind: {kind!r}")


def _shape_from_schema(schema: dict) -> list[tuple[str, dict]]:
    """Return [(field_name, field_subschema), ...] preserving declaration order."""
    return list(schema.get("properties", {}).items())


def _field_from_schema(name: str, schema: dict) -> str:
    py_name = _to_field_name(name)
    py_type = _json_type_to_python(schema)
    if schema.get("type") == "string" and "maxLength" in schema:
        return f"{py_name}: {py_type} = Field(max_length={schema['maxLength']})"
    return f"{py_name}: {py_type}"


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


def _step_signature(step: StepIR, contracts_by_name: dict[str, "ContractIR"]) -> str:
    """Return the parameter list portion of a `def name(...)` signature.
    Empty TAKES → empty (no `*, ` orphan); else → keyword-only args."""
    if not step.takes:
        return ""
    args = ", ".join(
        f"{_to_field_name(t.name)}: {_type_to_python(t.type, contracts_by_name)}"
        for t in step.takes
    )
    return f"*, {args}"


def _first_ident(assert_ast: dict) -> str:
    kind = assert_ast.get("kind")
    if kind == "ident":
        return assert_ast["name"]
    for key in ("args", "left", "right"):
        sub = assert_ast.get(key)
        if isinstance(sub, dict):
            try:
                return _first_ident(sub)
            except KeyError:
                continue
        if isinstance(sub, list):
            for item in sub:
                if isinstance(item, dict):
                    try:
                        return _first_ident(item)
                    except KeyError:
                        continue
    raise KeyError("no ident found")


def _collect_idents(assert_ast: dict) -> set[str]:
    """Walk the assert AST and return every distinct ident name."""
    if assert_ast.get("kind") == "ident":
        return {assert_ast["name"]}
    out: set[str] = set()
    for key in ("args", "left", "right"):
        sub = assert_ast.get(key)
        if isinstance(sub, dict):
            out |= _collect_idents(sub)
        elif isinstance(sub, list):
            for item in sub:
                if isinstance(item, dict):
                    out |= _collect_idents(item)
    return out


def _emit_attempt_block(
    invoke: InvokeIR | None, result_class: str, default_max_tokens: int = 4096,
) -> tuple[list[str], list[str], bool]:
    """Build the (extra_imports, attempt_function_lines, needs_os) tuple
    for the SDK call inside a judgment step. Routes by invoke type and
    invoke.protocol; defaults to Anthropic when invoke is None.

    Raises ValueError at compile time for protocols/modes the python emitter
    does not yet support (bedrock, vertex, cli).
    """
    if invoke is None:
        return _attempt_anthropic_block(None, result_class, default_max_tokens)

    if isinstance(invoke, ApiInvokeIR):
        if invoke.protocol == "anthropic":
            return _attempt_anthropic_block(invoke, result_class, default_max_tokens)
        if invoke.protocol == "openai":
            return _attempt_openai_block(invoke, result_class, default_max_tokens)
        if invoke.protocol in ("bedrock", "vertex"):
            raise ValueError(
                f"invoke.protocol {invoke.protocol!r} is not yet supported by the "
                "python emitter; only 'anthropic' and 'openai' are implemented in v0.2"
            )
        raise ValueError(f"unknown invoke.protocol {invoke.protocol!r}")

    if isinstance(invoke, CliInvokeIR):
        raise ValueError(
            "invoke.mode: cli is not supported by the python emitter; "
            "use --target claude-cli for CLI invocation, or switch to invoke.mode: api"
        )

    raise ValueError(f"unknown invoke type: {type(invoke).__name__}")


def _attempt_anthropic_block(
    invoke: ApiInvokeIR | None, result_class: str, default_max_tokens: int,
) -> tuple[list[str], list[str], bool]:
    """Anthropic SDK attempt block. Behavior is identical to the v0.1 emitter
    when invoke is None; with invoke set, applies base_url/auth/temperature/
    max_tokens overrides."""
    extra_imports = ["import anthropic"]
    needs_os = False

    client_args_parts: list[str] = []
    if invoke is not None and invoke.base_url:
        client_args_parts.append(f"base_url={invoke.base_url!r}")
    if invoke is not None and invoke.auth and invoke.auth.startswith("env:"):
        env_var = invoke.auth[4:]
        client_args_parts.append(f"api_key=os.environ.get({env_var!r})")
        needs_os = True
    client_args = ", ".join(client_args_parts)

    max_tokens = (
        invoke.max_tokens
        if invoke is not None and invoke.max_tokens is not None
        else default_max_tokens
    )

    create_args = [
        "            model=model,",
        f"            max_tokens={max_tokens},",
    ]
    if invoke is not None and invoke.temperature is not None:
        create_args.append(f"            temperature={invoke.temperature},")
    create_args += [
        "            system=_SYSTEM_PROMPT,",
        "            messages=[{'role': 'user', 'content': prompt}],",
    ]

    attempt_block = [
        "def _attempt(model, prompt):",
        '    """Single attempt: SDK call → markdown strip → Pydantic validation."""',
        "    try:",
        f"        client = anthropic.Anthropic({client_args})",
        "        msg = client.messages.create(",
    ] + create_args + [
        "        )",
        "        raw = msg.content[0].text if msg.content else ''",
        "        if not raw:",
        "            return None",
        "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
        f"        return {result_class}(json.loads(cleaned))",
        "    except Exception:",
        "        return None",
    ]

    return extra_imports, attempt_block, needs_os


def _attempt_openai_block(
    invoke: ApiInvokeIR, result_class: str, default_max_tokens: int,
) -> tuple[list[str], list[str], bool]:
    """OpenAI SDK attempt block (chat.completions API). Compatible with LiteLLM,
    OpenRouter, Ollama, vLLM, Together, etc. via base_url."""
    extra_imports = ["import openai"]
    needs_os = False

    client_args_parts: list[str] = []
    if invoke.base_url:
        client_args_parts.append(f"base_url={invoke.base_url!r}")
    if invoke.auth and invoke.auth.startswith("env:"):
        env_var = invoke.auth[4:]
        client_args_parts.append(f"api_key=os.environ.get({env_var!r})")
        needs_os = True
    elif invoke.auth == "none":
        # Local servers (Ollama default, vLLM no-auth) accept any non-empty key.
        client_args_parts.append("api_key='not-needed'")
    client_args = ", ".join(client_args_parts)

    max_tokens = invoke.max_tokens if invoke.max_tokens is not None else default_max_tokens

    create_args = [
        "            model=model,",
        f"            max_tokens={max_tokens},",
    ]
    if invoke.temperature is not None:
        create_args.append(f"            temperature={invoke.temperature},")
    create_args += [
        "            messages=[",
        "                {'role': 'system', 'content': _SYSTEM_PROMPT},",
        "                {'role': 'user', 'content': prompt},",
        "            ],",
    ]

    attempt_block = [
        "def _attempt(model, prompt):",
        '    """Single attempt: SDK call → markdown strip → Pydantic validation."""',
        "    try:",
        f"        client = openai.OpenAI({client_args})",
        "        msg = client.chat.completions.create(",
    ] + create_args + [
        "        )",
        "        raw = msg.choices[0].message.content if msg.choices else ''",
        "        if not raw:",
        "            return None",
        "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
        f"        return {result_class}(json.loads(cleaned))",
        "    except Exception:",
        "        return None",
    ]

    return extra_imports, attempt_block, needs_os
