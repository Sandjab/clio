"""Smoke tests for clio.emitters._shared_utils.

These guard the public surface; behavioural coverage stays in
test_python.py and test_mcp_server.py via the existing emitter suites.
"""

import importlib
import inspect
from pathlib import Path

import pytest

from clio.emitters._shared_utils import (
    _field_from_schema,
    _json_type_to_python,
    _model_id,
    _render_type_short,
    _shape_from_schema,
    _to_class_name,
    _to_field_name,
    _to_go_field_name,
    _type_to_go,
    _type_to_python,
    _uses_contract_refs,
)
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    EnumType,
    ListType,
    PrimitiveType,
    RecordType,
)


def test_to_class_name_basic():
    assert _to_class_name("customer_order") == "CustomerOrder"
    assert _to_class_name("foo") == "Foo"
    assert _to_class_name("a_b_c") == "ABC"


def test_to_field_name_basic():
    # Non-keywords pass through unchanged.
    assert _to_field_name("score") == "score"
    assert _to_field_name("customer_id") == "customer_id"
    # Python keywords get a trailing underscore.
    assert _to_field_name("class") == "class_"
    assert _to_field_name("return") == "return_"


def test_to_go_field_name_basic():
    # Snake_case → UpperCamelCase
    assert _to_go_field_name("customer_id") == "CustomerId"
    assert _to_go_field_name("a_b_c") == "ABC"
    # Hyphens are normalised to underscores before splitting.
    assert _to_go_field_name("x-y") == "XY"
    assert _to_go_field_name("a_b-c") == "ABC"
    # Single character stays correctly cased.
    assert _to_go_field_name("a") == "A"
    # Already-camel input keeps trailing casing intact (.capitalize would lowercase it).
    assert _to_go_field_name("fooBar") == "FooBar"


def test_model_id_smoke():
    out = _model_id("sonnet")
    assert isinstance(out, str) and len(out) > 0
    # Unknown name passes through as-is.
    assert _model_id("my-custom-model") == "my-custom-model"


def test_render_type_short_primitive():
    t = PrimitiveType(name="str")
    assert _render_type_short(t) == "str"


def test_render_type_short_list():
    t = ListType(inner=PrimitiveType(name="int"))
    assert _render_type_short(t) == "List<int>"


def test_json_type_to_python_primitives():
    assert _json_type_to_python({"type": "string"}) == "str"
    assert _json_type_to_python({"type": "integer"}) == "int"
    assert _json_type_to_python({"type": "boolean"}) == "bool"
    assert _json_type_to_python({"type": "number"}) == "float"
    assert _json_type_to_python({"type": "object"}) == "dict"


def test_json_type_to_python_ref():
    assert _json_type_to_python({"$ref": "#/$defs/customer_risk"}) == "CustomerRisk"


def test_shape_from_schema_preserves_order():
    schema = {"properties": {"b": {"type": "string"}, "a": {"type": "integer"}}}
    result = _shape_from_schema(schema)
    assert result == [("b", {"type": "string"}), ("a", {"type": "integer"})]


def test_shape_from_schema_empty():
    assert _shape_from_schema({}) == []


def test_field_from_schema_simple():
    result = _field_from_schema("score", {"type": "integer"})
    assert result == "score: int"


def test_field_from_schema_keyword_name():
    # Renamed field carries an alias back to the original CLIO name so the
    # LLM-emitted JSON (which uses the source name `class`) still parses.
    result = _field_from_schema("class", {"type": "string"})
    assert result == "class_: str = Field(alias='class', validation_alias='class')"


def test_field_from_schema_max_length():
    result = _field_from_schema("name", {"type": "string", "maxLength": 100})
    assert result == "name: str = Field(max_length=100)"


def test_type_to_python_primitive():
    """_type_to_python maps simple primitives to Python type strings."""
    assert _type_to_python(PrimitiveType("int"), {}) == "int"
    assert _type_to_python(PrimitiveType("str"), {}) == "str"


def test_uses_contract_refs_false_when_no_takes():
    """A step with no TAKES/GIVES doesn't use contract refs."""

    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    FIXTURES = Path(__file__).parent.parent / "fixtures"
    graph = build_ir(parse((FIXTURES / "mvp_phase1.clio").read_text()))
    step = graph.steps[0]
    assert isinstance(_uses_contract_refs(step), bool)


def test_type_to_go_primitives():
    assert _type_to_go(PrimitiveType(name="str"), {}) == "string"
    assert _type_to_go(PrimitiveType(name="int"), {}) == "int64"
    assert _type_to_go(PrimitiveType(name="float"), {}) == "float64"
    assert _type_to_go(PrimitiveType(name="bool"), {}) == "bool"
    assert _type_to_go(PrimitiveType(name="any"), {}) == "any"


def test_type_to_go_list_of_primitives():
    t = ListType(inner=PrimitiveType(name="str"))
    assert _type_to_go(t, {}) == "[]string"


def test_type_to_go_list_of_records():
    t = ListType(inner=RecordType(fields=(
        ("name", PrimitiveType(name="str")),
        ("revenue", PrimitiveType(name="float")),
    )))
    out = _type_to_go(t, {})
    assert out.startswith("[]struct ")
    assert 'Name string `json:"name"`' in out
    assert 'Revenue float64 `json:"revenue"`' in out
    # Go struct fields are separated by `;`, never `,` — guard against
    # regression to the comma-separator bug.
    assert "; " in out
    assert ", Revenue" not in out


def test_type_to_go_contract_ref():
    from clio.ir.graph import ContractIR
    contracts = {"customer_risk": ContractIR(name="customer_risk", json_schema={}, assert_json_ast=None, line=0)}
    t = ContractRef(name="customer_risk", line=0, col=0)
    assert _type_to_go(t, contracts) == "CustomerRisk"


def test_type_to_go_enum():
    t = EnumType(values=("low", "mid", "high"))
    # enums render as `string` with a documented constant set elsewhere
    assert _type_to_go(t, {}) == "string"


def test_type_to_go_constrained_unwraps():
    t = ConstrainedType(base=PrimitiveType(name="str"), constraints=(("max", 300),))
    assert _type_to_go(t, {}) == "string"


@pytest.mark.parametrize(
    "emitter_module,emitter_class",
    [
        ("clio.emitters.claude_cli", "ClaudeCLIEmitter"),
        ("clio.emitters.python", "PythonEmitter"),
        ("clio.emitters.mcp_server", "MCPServerEmitter"),
        ("clio.emitters.langgraph", "LangGraphEmitter"),
        ("clio.emitters.claude_skill", "ClaudeSkillEmitter"),
    ],
)
def test_all_emitters_accept_source_path_kwarg(tmp_path, emitter_module, emitter_class):
    """Every emitter must accept a keyword-only `source_path: Path | None = None`
    so callers (notably `_cmd_compile`) can plumb the source path uniformly
    without per-emitter branching."""
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    mod = importlib.import_module(emitter_module)
    cls = getattr(mod, emitter_class)
    sig = inspect.signature(cls.emit)
    assert "source_path" in sig.parameters, f"{emitter_class}.emit missing source_path"
    param = sig.parameters["source_path"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is None

    # Smoke: emitter must run with source_path=None without error
    src = "STEP foo\n  MODE: exact\n  LANG: python\nFLOW f\n  foo()\n"
    program = parse(src)
    graph = build_ir(program)
    out = tmp_path / emitter_class
    cls().emit(graph, out, source_path=None)
    assert out.exists()
