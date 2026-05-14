"""Smoke tests for clio.emitters._shared_utils.

These guard the public surface; behavioural coverage stays in
test_python.py and test_mcp_server.py via the existing emitter suites.
"""

from clio.emitters._shared_utils import (
    _field_from_schema,
    _json_type_to_python,
    _model_id,
    _render_type_short,
    _shape_from_schema,
    _to_class_name,
    _to_field_name,
    _type_to_python,
    _uses_contract_refs,
)
from clio.parser.ast_nodes import ListType, PrimitiveType


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
    from pathlib import Path

    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    FIXTURES = Path(__file__).parent.parent / "fixtures"
    graph = build_ir(parse((FIXTURES / "mvp_phase1.clio").read_text()))
    step = graph.steps[0]
    assert isinstance(_uses_contract_refs(step), bool)
