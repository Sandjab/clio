"""Module-level helpers for the Python emitter.

These functions are pure (no I/O, no global state). The emitter class lives
in `clio/emitters/python.py` and imports from this module.

Split out per CLAUDE.md scope-discipline rule (~300-line limit per file).
"""

import keyword

from clio.ir.graph import (
    ApiInvokeIR,
    CallIR,
    CliInvokeIR,
    ContractIR,
    FileBodyIR,
    ForEachIR,
    FormBodyIR,
    HttpServerSpecIR,
    InvokeIR,
    JsonBodyIR,
    McpServerSpecIR,
    McpToolImplIR,
    MultipartBodyIR,
    RawBodyIR,
    RestImplIR,
    ShellImplIR,
    SseServerSpecIR,
    StdioServerSpecIR,
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
    if kind == "bool_and":
        return f"({_ast_to_python(node['left'])} and {_ast_to_python(node['right'])})"
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


def emit_default_exact_step(step: "StepIR", contracts_by_name: dict[str, "ContractIR"]) -> str:
    """Emit a default-mode (no impl, or impl.mode: code) exact step body.
    Both python and mcp-server targets emit this identical shape."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )
    contracts_import = (
        "from .. import contracts\n" if _uses_contract_refs(step) else ""
    )
    return (
        f'"""STEP {step.name} (exact)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Implement the body below. The orchestrator passes arguments by keyword\n'
        f'and expects the return value to conform to the GIVES type.\n'
        f'\n'
        f'NOTE: when implementing, emit a step_end before returning:\n'
        f'    _log.emit("step_end", step={step.name!r}, mode="exact",\n'
        f'              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import time\n\n'
        f'from ..clio_runtime import logging as _log\n'
        f'{contracts_import}'
        f'\n\n'
        f'def {step.name}({params}) -> {ret_type}:\n'
        f'    _t0 = time.monotonic()\n'
        f'    _log.emit("step_start", step={step.name!r}, mode="exact")\n'
        f'    raise NotImplementedError(\n'
        f'        "Implement steps/{step.name}.py: this is an exact (deterministic) step."\n'
        f'    )\n'
    )


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
        "    nonlocal _last_usage",
        "    try:",
        f"        client = anthropic.Anthropic({client_args})",
        "        msg = client.messages.create(",
        *create_args,
        "        )",
        "        if hasattr(msg, 'usage') and msg.usage is not None:",
        "            _last_usage = {",
        "                'tokens_in': getattr(msg.usage, 'input_tokens', None),",
        "                'tokens_out': getattr(msg.usage, 'output_tokens', None),",
        "            }",
        "            _last_usage = {k: v for k, v in _last_usage.items() if v is not None}",
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
        "    nonlocal _last_usage",
        "    try:",
        f"        client = openai.OpenAI({client_args})",
        "        msg = client.chat.completions.create(",
        *create_args,
        "        )",
        "        if hasattr(msg, 'usage') and msg.usage is not None:",
        "            _last_usage = {",
        "                'tokens_in': getattr(msg.usage, 'prompt_tokens', None),",
        "                'tokens_out': getattr(msg.usage, 'completion_tokens', None),",
        "            }",
        "            _last_usage = {k: v for k, v in _last_usage.items() if v is not None}",
        "        raw = msg.choices[0].message.content if msg.choices else ''",
        "        if not raw:",
        "            return None",
        "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
        f"        return {result_class}(json.loads(cleaned))",
        "    except Exception:",
        "        return None",
    ]

    return extra_imports, attempt_block, needs_os


def emit_contracts(graph) -> str:
    """Emit contracts.py for the given FlowGraph. Used by both python and mcp-server targets."""
    if not graph.contracts:
        return '"""No contracts declared in this flow."""\n'

    lines: list[str] = [
        '"""Pydantic models generated from CLIO CONTRACT declarations."""',
        "from typing import Literal",
        "",
        "from pydantic import BaseModel, Field, field_validator",
        "",
        "",
    ]

    for c in graph.contracts:
        class_name = _to_class_name(c.name)
        shape = _shape_from_schema(c.json_schema)
        for fname, _ in shape:
            if fname in _PYDANTIC_RESERVED_FIELDS:
                raise ValueError(
                    f"CONTRACT {c.name!r} field {fname!r} collides with a "
                    f"Pydantic v2 reserved attribute and cannot be emitted "
                    f"to the python target; rename the field in your .clio source"
                )
        lines.append(f"class {class_name}(BaseModel):")
        lines.append(f'    """CONTRACT {c.name}."""')

        for fname, fschema in shape:
            lines.append(f"    {_field_from_schema(fname, fschema)}")

        if c.assert_json_ast is not None:
            idents = _collect_idents(c.assert_json_ast)
            if len(idents) > 1:
                raise ValueError(
                    f"CONTRACT {c.name!r} ASSERT references multi-field "
                    f"({sorted(idents)}); the python target only supports "
                    f"single-field ASSERTs in v0.3"
                )
            target_field = _first_ident(c.assert_json_ast)
            expr = _ast_to_python(c.assert_json_ast)
            lines += [
                "",
                f'    @field_validator({target_field!r})',
                "    @classmethod",
                f"    def _assert_{c.name}(cls, v):",
                f"        {target_field} = v",
                f"        if not {expr}:",
                f'            raise ValueError("ASSERT failed: " + {expr!r})',
                "        return v",
            ]
        lines.append("")

    return "\n".join(lines) + "\n"


def emit_rest_step(
    step: StepIR,
    contracts_by_name: dict[str, ContractIR],
    impl: RestImplIR,
) -> str:
    """Emit a REST-impl exact step. Shared by python and mcp-server targets."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )

    takes_dict_lines = (
        ["    _takes = {"]
        + [f"        {t.name!r}: {_to_field_name(t.name)}," for t in step.takes]
        + ["    }"]
        if step.takes else ["    _takes: dict = {}"]
    )

    url_line = f"    _url = _rest.subst({impl.url!r}, _takes)"

    kwargs_lines: list[str] = ["    _kwargs: dict = {}"]
    if impl.query is not None:
        kwargs_lines.append(
            f"    _kwargs['params'] = _rest.render_dict({tuple(impl.query)!r}, _takes)"
        )

    headers_initialized = False
    if impl.headers is not None:
        kwargs_lines.append(
            f"    _kwargs['headers'] = _rest.render_dict({tuple(impl.headers)!r}, _takes)"
        )
        headers_initialized = True

    def _ensure_headers() -> str:
        nonlocal headers_initialized
        if headers_initialized:
            return ""
        headers_initialized = True
        return "    _kwargs.setdefault('headers', {})\n"

    if impl.body is not None:
        if isinstance(impl.body, JsonBodyIR):
            kwargs_lines.append(
                f"    _kwargs['json'] = _rest.render_dict({tuple(impl.body.fields)!r}, _takes)"
            )
        elif isinstance(impl.body, RawBodyIR):
            kwargs_lines.append(
                f"    _kwargs['data'] = _rest.subst({impl.body.template!r}, _takes)"
            )
            kwargs_lines.append(_ensure_headers().rstrip("\n") or "")
            kwargs_lines.append(
                "    _kwargs['headers'].setdefault('Content-Type', 'text/plain')"
            )
        elif isinstance(impl.body, FileBodyIR):
            kwargs_lines.append(
                f"    _data, _ct = _rest.read_file_body({impl.body.path!r}, _takes)"
            )
            kwargs_lines.append("    _kwargs['data'] = _data")
            kwargs_lines.append(_ensure_headers().rstrip("\n") or "")
            kwargs_lines.append("    _kwargs['headers'].setdefault('Content-Type', _ct)")
        elif isinstance(impl.body, FormBodyIR):
            kwargs_lines.append(
                f"    _kwargs['data'] = _rest.render_dict({tuple(impl.body.fields)!r}, _takes)"
            )
        elif isinstance(impl.body, MultipartBodyIR):
            kwargs_lines.append("    _form: dict = {}")
            kwargs_lines.append("    _files: dict = {}")
            kwargs_lines.append(
                f"    for _k, _v in {tuple(impl.body.fields)!r}:"
            )
            kwargs_lines.append("        if isinstance(_v, str) and _v.startswith('@'):")
            kwargs_lines.append("            _path = _v[1:]")
            kwargs_lines.append("            with open(_path, 'rb') as _f:")
            kwargs_lines.append(
                "                _files[_k] = ("
                "Path(_path).name, "
                "_f.read(), "
                "_rest.content_type_for_path(_path))"
            )
            kwargs_lines.append("        else:")
            kwargs_lines.append(
                "            _form[_k] = _rest.subst(_v, _takes) if isinstance(_v, str) else _v"
            )
            kwargs_lines.append("    if _form:")
            kwargs_lines.append("        _kwargs['data'] = _form")
            kwargs_lines.append("    if _files:")
            kwargs_lines.append("        _kwargs['files'] = _files")

    if impl.timeout_seconds is not None:
        kwargs_lines.append(f"    _kwargs['timeout'] = {impl.timeout_seconds}")
    else:
        kwargs_lines.append("    _kwargs['timeout'] = None")

    # Retry block (or single-shot)
    if impl.retry is not None:
        request_block_lines = [
            f"    _attempts = {impl.retry.attempts}",
            f"    _retry_on = {tuple(impl.retry.on)!r}",
            f"    _backoff = {impl.retry.backoff!r}",
            f"    _base = {impl.retry.base}",
            f"    _cap = {impl.retry.cap}",
            "    response = None",
            "    for _i in range(_attempts):",
            "        try:",
            f"            response = requests.request(method={impl.method!r}, url=_url, **_kwargs)",
            "        except Exception as _e:",
            "            if _rest.is_retryable_exception(_e, _retry_on) and _i + 1 < _attempts:",
            "                time.sleep(_rest.compute_delay(_i + 1, _base, _cap, _backoff))",
            "                continue",
            "            raise",
            "        if (_rest.is_retryable_response(response.status_code, _retry_on)",
            "                and _i + 1 < _attempts):",
            "            _ra = _rest.parse_retry_after(response.headers.get('Retry-After'))",
            "            time.sleep(_ra if _ra is not None else _rest.compute_delay(_i + 1, _base, _cap, _backoff))",
            "            continue",
            "        break",
            "    assert response is not None",
        ]
    else:
        request_block_lines = [
            f"    response = requests.request(method={impl.method!r}, url=_url, **_kwargs)",
        ]

    if impl.response_path is not None:
        traversal_block_lines = [
            f"    _path = {impl.response_path!r}",
            "    _data = response.json()",
            "    for _part in _re.findall(r'[^.\\[\\]]+|\\[\\d+\\]', _path):",
            "        if _part.startswith('['):",
            "            _data = _data[int(_part[1:-1])]",
            "        else:",
            "            _data = _data[_part]",
            f'    _log.emit("step_end", step={step.name!r}, mode="exact",',
            "              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)",
            "    return _data",
        ]
        extra_imports = "import re as _re\n"
    else:
        traversal_block_lines = [
            f'    _log.emit("step_end", step={step.name!r}, mode="exact",',
            "              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)",
            "    return response.json()",
        ]
        extra_imports = ""

    if isinstance(impl.body, MultipartBodyIR):
        extra_imports += "from pathlib import Path\n"

    contracts_import = (
        "from .. import contracts\n" if _uses_contract_refs(step) else ""
    )

    body_lines = (
        [
            f'def {step.name}({params}) -> {ret_type}:',
            '    _t0 = time.monotonic()',
            f'    _log.emit("step_start", step={step.name!r}, mode="exact")',
        ]
        + takes_dict_lines
        + [url_line]
        + [ln for ln in kwargs_lines if ln]
        + request_block_lines
        + ["    response.raise_for_status()"]
        + traversal_block_lines
    )

    return (
        f'"""STEP {step.name} (exact, impl: rest)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Auto-generated from `impl: mode: rest`. URL, query, headers, and body\n'
        f'string values support ${{var}} substitution from TAKES and full-value\n'
        f'env:NAME resolution from os.environ. See LANGUAGE_SPEC.md §impl.mode: rest.\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import time\n'
        f'import requests\n'
        f'{extra_imports}\n'
        f'from ..clio_runtime import logging as _log\n'
        f'from ..clio_runtime import rest as _rest\n'
        f'{contracts_import}'
        f'\n\n'
        + "\n".join(body_lines) + "\n"
    )


def emit_shell_step(
    step: StepIR,
    contracts_by_name: dict[str, ContractIR],
    impl: ShellImplIR,
) -> str:
    """Emit a shell-impl exact step. Shared by python and mcp-server targets."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )

    argv_repr = "[" + ", ".join(repr(t) for t in impl.argv) + "]"
    sub_lines = [
        f"    _argv = [_t.replace('${{{t.name}}}', str({_to_field_name(t.name)})) for _t in _argv]"
        for t in step.takes
    ]
    sub_block = ("\n".join(sub_lines) + "\n") if sub_lines else ""

    timeout_arg = (
        f"timeout={impl.timeout_seconds}"
        if impl.timeout_seconds is not None else "timeout=None"
    )

    if impl.parse == "json":
        json_import = "import json\n"
        return_line = "    return json.loads(result.stdout)\n"
    else:
        json_import = ""
        return_line = "    return result.stdout\n"

    contracts_import = (
        "from .. import contracts\n" if _uses_contract_refs(step) else ""
    )

    return (
        f'"""STEP {step.name} (exact, impl: shell)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Auto-generated from `impl: mode: shell`. Argv-style invocation —\n'
        f'no shell pipes/redirections (subprocess.run is called with shell=False).\n'
        f'TAKES are substituted into argv tokens via ${{var}} placeholders.\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import subprocess\n'
        f'{json_import}'
        f'import time\n\n'
        f'from ..clio_runtime import logging as _log\n'
        f'{contracts_import}'
        f'\n\n'
        f'def {step.name}({params}) -> {ret_type}:\n'
        f'    _t0 = time.monotonic()\n'
        f'    _log.emit("step_start", step={step.name!r}, mode="exact")\n'
        f'    _argv = {argv_repr}\n'
        f'{sub_block}'
        f'    result = subprocess.run(_argv, capture_output=True, text=True, check=True, {timeout_arg})\n'
        f'    _log.emit("step_end", step={step.name!r}, mode="exact",\n'
        f'              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)\n'
        f'{return_line}'
    )


def _server_spec_dict_repr(spec: McpServerSpecIR) -> str:
    """Render an `McpServerSpecIR` as a Python dict literal usable in
    emitted code. The runtime helpers (`clio_runtime.mcp_client`) read
    this dict shape (with keys: name, transport, command, args, env, url,
    headers) and resolve `env:NAME` values at runtime."""
    if isinstance(spec, StdioServerSpecIR):
        return (
            "{"
            f"'name': {spec.name!r}, "
            f"'transport': 'stdio', "
            f"'command': {spec.command!r}, "
            f"'args': {list(spec.args)!r}, "
            f"'env': {list(spec.env)!r}"
            "}"
        )
    if isinstance(spec, SseServerSpecIR):
        return (
            "{"
            f"'name': {spec.name!r}, "
            f"'transport': 'sse', "
            f"'url': {spec.url!r}, "
            f"'headers': {list(spec.headers)!r}"
            "}"
        )
    if isinstance(spec, HttpServerSpecIR):
        return (
            "{"
            f"'name': {spec.name!r}, "
            f"'transport': 'http', "
            f"'url': {spec.url!r}, "
            f"'headers': {list(spec.headers)!r}"
            "}"
        )
    raise ValueError(f"unknown McpServerSpecIR subtype: {type(spec).__name__}")


def emit_mcp_tool_step(
    step: StepIR,
    contracts_by_name: dict[str, ContractIR],
    impl: McpToolImplIR,
    server_spec: McpServerSpecIR,
    *,
    async_call: bool = False,
) -> str:
    """Emit a mcp_tool-impl exact step. Shared by python (sync) and
    mcp-server (async) targets. claude-cli has its own emitter (per-step
    bootstrap script) — see `_claude_cli_helpers.py`."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )

    takes_dict_lines = (
        ["    _takes = {"]
        + [f"        {t.name!r}: {_to_field_name(t.name)}," for t in step.takes]
        + ["    }"]
        if step.takes else ["    _takes: dict = {}"]
    )

    server_spec_line = f"    _server = {_server_spec_dict_repr(server_spec)}"
    args_repr = "{" + ", ".join(f"{k!r}: {v!r}" for k, v in impl.args) + "}"
    args_line = f"    _args = {args_repr}"

    call_kwargs = (
        f"_server, {impl.tool!r}, _args, _takes, "
        f"timeout={impl.timeout_seconds}, parse={impl.parse!r}"
    )
    if async_call:
        call_line = f"    _result = await _mcp.call_tool_async({call_kwargs})"
        def_line = f"async def {step.name}({params}) -> {ret_type}:"
    else:
        call_line = f"    _result = _mcp.call_tool_sync({call_kwargs})"
        def_line = f"def {step.name}({params}) -> {ret_type}:"

    contracts_import = (
        "from .. import contracts\n" if _uses_contract_refs(step) else ""
    )

    body_lines = [
        def_line,
        '    _t0 = time.monotonic()',
        f'    _log.emit("step_start", step={step.name!r}, mode="exact")',
        *takes_dict_lines,
        server_spec_line,
        args_line,
        call_line,
        f'    _log.emit("step_end", step={step.name!r}, mode="exact",',
        "              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)",
        "    return _result",
    ]

    return (
        f'"""STEP {step.name} (exact, impl: mcp_tool)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Auto-generated from `impl: mode: mcp_tool`. Calls the MCP server\n'
        f'declared in RESOURCES.mcp_servers.{impl.server!r} (transport: '
        f'{server_spec.__class__.__name__.replace("ServerSpecIR", "").lower()}).\n'
        f'See LANGUAGE_SPEC.md §impl.mode: mcp_tool.\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import time\n\n'
        f'from ..clio_runtime import logging as _log\n'
        f'from ..clio_runtime import mcp_client as _mcp\n'
        f'{contracts_import}'
        f'\n\n'
        + "\n".join(body_lines) + "\n"
    )


def emit_parallel_for_each_python(
    elem: ForEachIR,
    steps_by_name: dict,
    indent: str,
) -> str:
    """Emit a ThreadPoolExecutor block for a parallel FOR EACH (python target).

    The body is guaranteed (by IR validation) to be a single CallIR with a
    GIVES. Default cap is 10. Failure semantics: ThreadPoolExecutor's `with`
    exit cancels queued futures; in-flight tasks finish; the first
    `_fut.result()` to raise propagates.

    Each task is wrapped in contextvars.copy_context().run(...) so the
    _current_flow ContextVar set by run() propagates into worker threads.
    Without this wrapping, in-block step events would lack the 'flow' field
    because ThreadPoolExecutor workers don't inherit the parent's
    ContextVar copy by default.

    The block is bracketed by parallel_block_start/parallel_block_end events;
    the end event is emitted in a finally clause and reports duration_ms +
    success.
    """
    inner = elem.body[0]
    assert isinstance(inner, CallIR)  # IR builder enforces
    step = steps_by_name[inner.step_name]

    # Render kwargs using the @-prefix disambiguation. Loop var is in scope.
    scope_local = {elem.loop_var}
    kw_parts: list[str] = []
    for name, value in inner.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref in scope_local:
                kw_parts.append(f"{name}={ref}")
            else:
                kw_parts.append(f"{name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{name}={value!r}")
    kwargs_str = ", ".join(kw_parts)

    # The collection always lives in state for a parallel FOR EACH.
    items_lookup = f"state[{elem.collection!r}]"
    step_call = f"{step.name}_mod.{step.name}"

    return (
        f"{indent}_items = {items_lookup}\n"
        f"{indent}_results = [None] * len(_items)\n"
        f'{indent}_log.emit("parallel_block_start", step={step.name!r}, '
        f"collector={elem.collector!r}, total_iterations=len(_items), max_workers=10)\n"
        f"{indent}_pblock_t0 = time.monotonic()\n"
        f"{indent}_pblock_success = False\n"
        f"{indent}try:\n"
        f"{indent}    def _task({elem.loop_var}):\n"
        f"{indent}        return {step_call}({kwargs_str})\n"
        f"{indent}    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as _ex:\n"
        f"{indent}        _futures = {{_ex.submit(contextvars.copy_context().run, _task, {elem.loop_var}): _i "
        f"for _i, {elem.loop_var} in enumerate(_items)}}\n"
        f"{indent}        for _fut in concurrent.futures.as_completed(_futures):\n"
        f"{indent}            _idx = _futures[_fut]\n"
        f"{indent}            _results[_idx] = _fut.result()\n"
        f"{indent}    state[{elem.collector!r}] = _results\n"
        f"{indent}    _pblock_success = True\n"
        f"{indent}finally:\n"
        f'{indent}    _log.emit("parallel_block_end", step={step.name!r}, '
        f"collector={elem.collector!r}, total_iterations=len(_items), "
        f"duration_ms=int((time.monotonic() - _pblock_t0) * 1000), success=_pblock_success)"
    )


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
    """Render an IF/WHILE ConditionIR as a Python boolean expression.

    Reads the contract field via attribute access (Pydantic models): if the
    state field is in `scope_local` (e.g. inside a FOR EACH body) it's a bare
    name, otherwise it's `state[<name>]`."""
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
