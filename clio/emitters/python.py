"""Emitter for `target: python`.

Produces a runnable Python package (Anthropic SDK + Pydantic v2) from a
target-independent IR. Reuses `clio/runtime/cache.py` verbatim under the
emitted package's `clio_runtime/`.
"""

import keyword
from pathlib import Path

from clio.emitters.base import BaseEmitter
from clio.ir.graph import (
    ApiInvokeIR,
    CliInvokeIR,
    ContractIR,
    FieldIR,
    FlowGraph,
    InvokeIR,
    RestImplIR,
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


class PythonEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        pkg_name = self._package_name(graph)
        pkg_dir = output_dir / pkg_name
        steps_dir = pkg_dir / "steps"
        runtime_dir = pkg_dir / "clio_runtime"

        for d in (pkg_dir, steps_dir, runtime_dir):
            d.mkdir(parents=True, exist_ok=True)

        (pkg_dir / "__init__.py").write_text("")
        (steps_dir / "__init__.py").write_text("")
        (runtime_dir / "__init__.py").write_text("")

        (pkg_dir / "contracts.py").write_text(self._emit_contracts(graph))

        contracts_by_name = {c.name: c for c in graph.contracts}
        for step in graph.steps:
            if step.mode == "exact":
                body = self._emit_exact_step(step, contracts_by_name)
            else:
                body = self._emit_judgment_step(step, graph, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)

        needs_requests = any(
            isinstance(s.impl, RestImplIR) for s in graph.steps
        )
        needs_openai = any(
            isinstance(s.invoke, ApiInvokeIR) and s.invoke.protocol == "openai"
            for s in graph.steps
        )
        (output_dir / "pyproject.toml").write_text(
            self._pyproject(
                pkg_name,
                needs_requests=needs_requests,
                needs_openai=needs_openai,
            )
        )
        (output_dir / "README.md").write_text(self._readme(pkg_name, graph))

        (pkg_dir / "flow.py").write_text(self._emit_flow(graph))
        (pkg_dir / "__main__.py").write_text(self._emit_main(pkg_name))

        from clio import runtime as src_pkg
        src = Path(src_pkg.__file__).parent / "cache.py"
        (runtime_dir / "cache.py").write_text(src.read_text())

    def _emit_contracts(self, graph: FlowGraph) -> str:
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

    def _emit_exact_step(self, step: StepIR, contracts_by_name: dict[str, "ContractIR"]) -> str:
        if isinstance(step.impl, RestImplIR):
            return self._emit_rest_step(step, contracts_by_name, step.impl)

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

        return (
            f'"""STEP {step.name} (exact)\n'
            f'TAKES:\n'
            f'    {takes_doc}\n'
            f'GIVES:\n'
            f'    {gives_doc}\n\n'
            f'Implement the body below. The orchestrator passes arguments by keyword\n'
            f'and expects the return value to conform to the GIVES type.\n'
            f'"""\n'
            f'from __future__ import annotations\n'
            f'\n\n'
            f'def {step.name}({params}) -> {ret_type}:\n'
            f'    raise NotImplementedError(\n'
            f'        "Implement steps/{step.name}.py: this is an exact (deterministic) step."\n'
            f'    )\n'
        )

    def _emit_rest_step(
        self,
        step: StepIR,
        contracts_by_name: dict[str, "ContractIR"],
        impl: RestImplIR,
    ) -> str:
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

        # The function body is generated; TAKES are accepted in the signature
        # for orchestrator parity but not yet wired into the request (no
        # templating in this version — see LANGUAGE_SPEC v0.2).
        unused_takes_lines = [f"    _ = {_to_field_name(t.name)}" for t in step.takes]

        timeout_arg = (
            f"timeout={impl.timeout_seconds}"
            if impl.timeout_seconds is not None else "timeout=None"
        )

        if impl.response_path is not None:
            traversal_block = (
                f"    _path = {impl.response_path!r}\n"
                f"    _data = response.json()\n"
                f"    for _part in _re.findall(r'[^.\\[\\]]+|\\[\\d+\\]', _path):\n"
                f"        if _part.startswith('['):\n"
                f"            _data = _data[int(_part[1:-1])]\n"
                f"        else:\n"
                f"            _data = _data[_part]\n"
                f"    return _data\n"
            )
            extra_imports = "import re as _re\n"
        else:
            traversal_block = "    return response.json()\n"
            extra_imports = ""

        retries_note = (
            f"# retries={impl.retries} requested but not implemented in v0.2; "
            "wire ON_FAIL on rest steps in a future slice.\n"
            if impl.retries is not None else ""
        )

        body_unused = ("\n".join(unused_takes_lines) + "\n") if unused_takes_lines else ""

        return (
            f'"""STEP {step.name} (exact, impl: rest)\n'
            f'TAKES:\n'
            f'    {takes_doc}\n'
            f'GIVES:\n'
            f'    {gives_doc}\n\n'
            f'Auto-generated from `impl: mode: rest`. The HTTP request is hardcoded\n'
            f'from the .clio source; templating of TAKES into the URL/headers/body\n'
            f'is not yet supported (v0.2 limitation).\n'
            f'"""\n'
            f'from __future__ import annotations\n\n'
            f'import requests\n'
            f'{extra_imports}\n\n'
            f'{retries_note}'
            f'def {step.name}({params}) -> {ret_type}:\n'
            f'{body_unused}'
            f'    response = requests.request(\n'
            f'        method={impl.method!r},\n'
            f'        url={impl.url!r},\n'
            f'        {timeout_arg},\n'
            f'    )\n'
            f'    response.raise_for_status()\n'
            f'{traversal_block}'
        )

    def _emit_judgment_step(
        self,
        step: StepIR,
        graph: FlowGraph,
        contracts_by_name: dict[str, "ContractIR"],
    ) -> str:
        from clio.emitters.claude_cli import _inline_schema, _render_prompt
        import json as _json

        params = _step_signature(step, contracts_by_name)
        ret_type = (
            _type_to_python(step.gives.type, contracts_by_name)
            if step.gives is not None else "None"
        )

        inlined = (
            _inline_schema(step.gives.type, contracts_by_name)
            if step.gives is not None else {}
        )
        inlined_json = _json.dumps(inlined, separators=(",", ":"))

        # invoke.model overrides RESOURCES.models when set; otherwise the
        # RESOURCES.models list drives the escalate chain as before.
        invoke = step.invoke
        if isinstance(invoke, ApiInvokeIR):
            models = (invoke.model,)
            models_full = (invoke.model,)  # invoke.model is the raw provider ID
        else:
            models = (
                graph.resources.models
                if graph.resources is not None and graph.resources.models
                else ("haiku",)
            )
            models_full = tuple(_model_id(m) for m in models)
        models_array_repr = (
            "(" + ", ".join(repr(m) for m in models_full) + ",)"
            if len(models_full) == 1
            else "(" + ", ".join(repr(m) for m in models_full) + ")"
        )

        strategies = step.on_fail.strategies if step.on_fail is not None else ()
        has_fallback = any(s.kind == "fallback" for s in strategies)
        terminal_abort = bool(strategies) and strategies[-1].kind == "abort"

        prompt_template = _render_prompt(step)
        result_class = _gives_validator_expr(step.gives)

        cache_active = step.cache is not None and step.cache.mode != "off"
        ttl_repr = (
            "None" if step.cache is None or step.cache.mode == "on"
            else str(step.cache.ttl_seconds)
        )

        sub_lines = [
            f"    prompt = prompt.replace('${{{t.name}}}', json.dumps({_to_field_name(t.name)}))"
            for t in step.takes
        ]
        sub_lines.append("    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)")

        provider_imports, attempt_lines, attempt_needs_os = _emit_attempt_block(
            invoke, result_class,
        )

        header = [
            f'"""STEP {step.name} (judgment).',
            f'',
            f'Auto-generated. Do not edit; regenerate via `clio compile`.',
            f'"""',
            "from __future__ import annotations",
            "",
            "import json",
            "import sys",
        ]
        if cache_active or attempt_needs_os:
            header += ["import os"]
        if cache_active:
            header += ["from pathlib import Path"]
        header += [
            "",
        ] + provider_imports + [
            "",
        ]
        if cache_active:
            header += ["from ..clio_runtime import cache as _cache", ""]
        header += [
            "from .. import contracts",
            "",
            "",
            f"_PROMPT_TEMPLATE = {prompt_template!r}",
            f"_INLINED_SCHEMA = {inlined_json!r}",
            "_SYSTEM_PROMPT = (",
            "    'You are a strict JSON-only API. Output exactly one JSON document matching '",
            "    'the requested schema, with no prose, no markdown code fences, no commentary, '",
            "    'and no leading or trailing whitespace beyond the JSON itself.'",
            ")",
            f"_MODELS = {models_array_repr}",
            "",
            "",
        ] + attempt_lines + [
            "",
            "",
        ]

        if cache_active:
            header += [
                "def _serialize(response):",
                '    """Re-serialize a validated response for cache storage."""',
                "    if isinstance(response, list):",
                "        return json.dumps([(item.model_dump() if hasattr(item, 'model_dump') else item) for item in response])",
                "    if hasattr(response, 'model_dump'):",
                "        return json.dumps(response.model_dump())",
                "    return json.dumps(response)",
                "",
                "",
            ]

        body = list(header)
        body.append(f"def {step.name}({params}) -> {ret_type}:")
        body.append("    prompt = _PROMPT_TEMPLATE")
        body += sub_lines
        body.append("")

        chain_lines: list[str] = [
            "    model_idx = 0",
        ]
        if has_fallback:
            chain_lines.append("    fallback_used = False")
        chain_lines += [
            "    response = None",
            "",
        ]

        if cache_active:
            chain_lines += [
                "    cache_dir = Path(os.environ.get('CLIO_CACHE_DIR', '.cache'))",
                f"    primary_key = _cache.cache_key('{step.name}', _MODELS[0], prompt, _INLINED_SCHEMA)",
                f"    hit = _cache.cache_lookup(cache_dir, '{step.name}', primary_key, {ttl_repr})",
                "    if hit is not None:",
                "        try:",
                f"            return {result_class}(json.loads(hit))",
                "        except Exception:",
                "            pass  # stale cache (schema changed): fall through to a fresh call",
                "",
            ]

        chain_lines += [
            "    response = _attempt(_MODELS[model_idx], prompt)",
            "",
        ]

        for s in strategies:
            if s.kind == "retry":
                n = s.max_retries
                chain_lines += [
                    "    if response is None:",
                    f"        for _ in range({n}):",
                    "            response = _attempt(_MODELS[model_idx], prompt)",
                    "            if response is not None:",
                    "                break",
                    "",
                ]
            elif s.kind == "escalate":
                if cache_active:
                    chain_lines += [
                        "    if response is None and model_idx < len(_MODELS) - 1:",
                        "        model_idx += 1",
                        f"        esc_key = _cache.cache_key('{step.name}', _MODELS[model_idx], prompt, _INLINED_SCHEMA)",
                        f"        esc_hit = _cache.cache_lookup(cache_dir, '{step.name}', esc_key, {ttl_repr})",
                        "        if esc_hit is not None:",
                        "            try:",
                        f"                return {result_class}(json.loads(esc_hit))",
                        "            except Exception:",
                        "                pass  # stale escalate cache: fall through",
                        "        response = _attempt(_MODELS[model_idx], prompt)",
                        "        if response is not None:",
                        f"            _cache.cache_store(cache_dir, '{step.name}', esc_key, _MODELS[model_idx], _serialize(response))",
                        "",
                    ]
                else:
                    chain_lines += [
                        "    if response is None and model_idx < len(_MODELS) - 1:",
                        "        model_idx += 1",
                        "        response = _attempt(_MODELS[model_idx], prompt)",
                        "",
                    ]
            elif s.kind == "fallback":
                fb_name = s.fallback_step.name
                kw_str = ", ".join(f"{t.name}={_to_field_name(t.name)}" for t in step.takes)
                chain_lines += [
                    "    if response is None:",
                    f"        from . import {fb_name} as _{fb_name}_mod",
                    f"        fb_response = _{fb_name}_mod.{fb_name}({kw_str})",
                    f"        response = {result_class}(fb_response if not isinstance(fb_response, str) else json.loads(fb_response))",
                    "        fallback_used = True",
                    "",
                ]
            elif s.kind == "abort":
                msg = s.abort_message or ""
                full_msg = f"[clio] step {step.name}: {msg}"
                chain_lines += [
                    "    if response is None:",
                    f"        print({full_msg!r}, file=sys.stderr)",
                    "        raise SystemExit(1)",
                    "",
                ]

        if not terminal_abort:
            chain_lines += [
                "    if response is None:",
                f"        print('[clio] step {step.name}: ON_FAIL strategies exhausted', file=sys.stderr)",
                "        raise SystemExit(1)",
                "",
            ]

        if cache_active:
            gate_terms = ["model_idx == 0", "response is not None"]
            if has_fallback:
                gate_terms.append("not fallback_used")
            chain_lines += [
                f"    if {' and '.join(gate_terms)}:",
                f"        _cache.cache_store(cache_dir, '{step.name}', primary_key, _MODELS[0], _serialize(response))",
                "",
            ]

        chain_lines.append("    return response")

        body += chain_lines
        body.append("")
        return "\n".join(body)

    def _emit_flow(self, graph: FlowGraph) -> str:
        if graph.flow is None:
            return '"""No FLOW declared."""\n\ndef run(**kwargs):\n    return {}\n'

        chain_lines: list[str] = []
        imported_steps: list[str] = []
        for call in graph.flow.chain:
            step = next(s for s in graph.steps if s.name == call.step_name)
            if step.name not in imported_steps:
                imported_steps.append(step.name)
            kw_parts = []
            for name, value in call.kwargs:
                if isinstance(value, str) and value.startswith("@"):
                    kw_parts.append(f"{name}=state[{value[1:]!r}]")
                else:
                    kw_parts.append(f"{name}={value!r}")
            kwargs_str = ", ".join(kw_parts)
            out_name = step.gives.name if step.gives is not None else "_result"
            chain_lines.append(
                f"    state[{out_name!r}] = {step.name}_mod.{step.name}({kwargs_str})"
            )

        imports = "\n".join(f"from .steps import {n} as {n}_mod" for n in imported_steps)

        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'{imports}\n'
            f'\n'
            f'\n'
            f'def run(**initial: object) -> dict:\n'
            f'    state: dict = dict(initial)\n'
            + "\n".join(chain_lines)
            + "\n    return state\n"
        )

    def _emit_main(self, pkg_name: str) -> str:
        return (
            f'"""CLI entry point: `python -m {pkg_name}`."""\n'
            f'import argparse\n'
            f'import json\n'
            f'import sys\n'
            f'\n'
            f'from .flow import run\n'
            f'\n'
            f'\n'
            f'def main(argv: list[str] | None = None) -> int:\n'
            f'    parser = argparse.ArgumentParser(prog="{pkg_name}")\n'
            f'    parser.add_argument("--kwargs", default="{{}}", help="JSON dict of initial flow kwargs")\n'
            f'    args = parser.parse_args(argv)\n'
            f'    initial = json.loads(args.kwargs)\n'
            f'    result = run(**initial)\n'
            f'    json.dump(result, sys.stdout, indent=2, default=str)\n'
            f'    sys.stdout.write("\\n")\n'
            f'    return 0\n'
            f'\n'
            f'\n'
            f'if __name__ == "__main__":\n'
            f'    raise SystemExit(main())\n'
        )

    @staticmethod
    def _package_name(graph: FlowGraph) -> str:
        if graph.flow is None:
            return "clio_flow"
        return graph.flow.name

    @staticmethod
    def _pyproject(
        pkg_name: str,
        *,
        needs_requests: bool = False,
        needs_openai: bool = False,
    ) -> str:
        deps = [
            '    "anthropic>=0.40",',
            '    "pydantic>=2",',
        ]
        if needs_requests:
            deps.append('    "requests>=2.31",')
        if needs_openai:
            deps.append('    "openai>=1.0",')
        deps_block = "\n".join(deps)
        return (
            "[build-system]\n"
            'requires = ["setuptools>=70"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            f'name = "{pkg_name}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            "dependencies = [\n"
            f"{deps_block}\n"
            "]\n"
            "\n"
            "[project.scripts]\n"
            f'{pkg_name} = "{pkg_name}.__main__:main"\n'
            "\n"
            "[tool.setuptools.packages.find]\n"
            f'include = ["{pkg_name}*"]\n'
        )

    @staticmethod
    def _readme(pkg_name: str, graph: FlowGraph) -> str:
        flow_name = graph.flow.name if graph.flow else "(no flow)"
        return (
            f"# {pkg_name}\n\n"
            f"Generated by CLIO from a `.clio` source. Implements FLOW `{flow_name}`.\n\n"
            "## Install\n\n"
            "```bash\n"
            "pip install -e .\n"
            "```\n\n"
            "## Run\n\n"
            "```bash\n"
            f"python -m {pkg_name}\n"
            "```\n\n"
            "Or programmatically:\n\n"
            "```python\n"
            f"from {pkg_name}.flow import run\n"
            "result = run()\n"
            "```\n"
        )
