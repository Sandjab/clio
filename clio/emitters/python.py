"""Emitter for `target: python`.

Produces a runnable Python package (Anthropic SDK + Pydantic v2) from a
target-independent IR. Reuses `clio/runtime/cache.py` verbatim under the
emitted package's `clio_runtime/`.
"""

import keyword
from pathlib import Path

from clio.emitters.base import BaseEmitter
from clio.ir.graph import ContractIR, FieldIR, FlowGraph, StepIR
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
        return _to_class_name(t.name)
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

        (output_dir / "pyproject.toml").write_text(self._pyproject(pkg_name))
        (output_dir / "README.md").write_text(self._readme(pkg_name, graph))

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
            lines.append(f"class {class_name}(BaseModel):")
            lines.append(f'    """CONTRACT {c.name}."""')

            for fname, fschema in shape:
                lines.append(f"    {_field_from_schema(fname, fschema)}")

            if c.assert_json_ast is not None:
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
            f'\n\n'
            f'def {step.name}({params}) -> {ret_type}:\n'
            f'    raise NotImplementedError(\n'
            f'        "Implement steps/{step.name}.py: this is an exact (deterministic) step."\n'
            f'    )\n'
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

        models = (
            graph.resources.models
            if graph.resources is not None and graph.resources.models
            else ("haiku",)
        )
        primary = _model_id(models[0])

        prompt_template = _render_prompt(step)
        result_class = _gives_validator_expr(step.gives)

        sub_lines = [
            f"    prompt = prompt.replace('${{{t.name}}}', json.dumps({_to_field_name(t.name)}))"
            for t in step.takes
        ]
        sub_lines.append("    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)")

        body = [
            f'"""STEP {step.name} (judgment).',
            f'',
            f'Auto-generated. Do not edit; regenerate via `clio compile`.',
            f'"""',
            "from __future__ import annotations",
            "",
            "import json",
            "import sys",
            "",
            "from anthropic import Anthropic",
            "",
            "from .. import contracts",
            "",
            "",
            f"_PROMPT_TEMPLATE = {prompt_template!r}",
            f"_INLINED_SCHEMA = {inlined_json!r}",
            f"_PRIMARY_MODEL = {primary!r}",
            "",
            "",
            "def _attempt(model, prompt):",
            "    \"\"\"Single attempt: SDK call → markdown strip → Pydantic validation.\"\"\"",
            "    try:",
            "        client = Anthropic()",
            "        msg = client.messages.create(",
            "            model=model,",
            "            max_tokens=4096,",
            "            messages=[{'role': 'user', 'content': prompt}],",
            "        )",
            "        raw = msg.content[0].text if msg.content else ''",
            "        if not raw:",
            "            return None",
            "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
            f"        return {result_class}(json.loads(cleaned))",
            "    except Exception:",
            "        return None",
            "",
            "",
            f"def {step.name}({params}) -> {ret_type}:",
            "    prompt = _PROMPT_TEMPLATE",
            *sub_lines,
            "",
            "    response = _attempt(_PRIMARY_MODEL, prompt)",
            "    if response is None:",
            f"        print('[clio] step {step.name}: ON_FAIL strategies exhausted', file=sys.stderr)",
            "        raise SystemExit(1)",
            "    return response",
            "",
        ]
        return "\n".join(body)

    @staticmethod
    def _package_name(graph: FlowGraph) -> str:
        if graph.flow is None:
            return "clio_flow"
        return graph.flow.name

    @staticmethod
    def _pyproject(pkg_name: str) -> str:
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
            '    "anthropic>=0.40",\n'
            '    "pydantic>=2",\n'
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
