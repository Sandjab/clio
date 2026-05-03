from clio.parser.parser import parse
from clio.ir.builder import build_ir
from clio.ir.contracts import type_to_json_schema


def test_build_ir_from_minimal_step():
    program = parse("STEP foo\n  MODE: exact\n")
    graph = build_ir(program)
    assert len(graph.steps) == 1
    assert graph.steps[0].name == "foo"
    assert graph.steps[0].takes == ()
    assert graph.steps[0].gives is None


def test_build_ir_with_primitive_takes_gives():
    src = "STEP echo_str\n  TAKES: input: str\n  GIVES: output: str\n  MODE: exact\n"
    graph = build_ir(parse(src))
    step = graph.steps[0]
    assert [f.name for f in step.takes] == ["input"]
    assert step.gives.name == "output"


def test_primitive_type_to_json_schema():
    from clio.parser.ast_nodes import PrimitiveType
    assert type_to_json_schema(PrimitiveType("str")) == {"type": "string"}
    assert type_to_json_schema(PrimitiveType("int")) == {"type": "integer"}
    assert type_to_json_schema(PrimitiveType("bool")) == {"type": "boolean"}
    assert type_to_json_schema(PrimitiveType("float")) == {"type": "number"}


def test_list_of_record_to_json_schema():
    from clio.parser.ast_nodes import ListType, PrimitiveType, RecordType
    t = ListType(
        inner=RecordType(
            fields=(("name", PrimitiveType("str")), ("age", PrimitiveType("int"))),
        ),
    )
    schema = type_to_json_schema(t)
    assert schema == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
            "additionalProperties": False,
        },
    }


def test_enum_to_json_schema():
    from clio.parser.ast_nodes import EnumType
    schema = type_to_json_schema(EnumType(values=("low", "mid", "high")))
    assert schema == {"enum": ["low", "mid", "high"]}


def test_build_ir_with_contract_and_ref():
    src = (
        "CONTRACT r\n"
        "  SHAPE: {x: int}\n"
        "STEP s\n"
        "  GIVES: out: List<r>\n"
        "  MODE:  judgment\n"
    )
    graph = build_ir(parse(src))
    assert len(graph.contracts) == 1
    assert graph.contracts[0].name == "r"
    assert graph.contracts[0].json_schema == {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }


def test_build_ir_unresolved_contract_ref_raises():
    import pytest
    src = (
        "STEP s\n"
        "  GIVES: out: List<missing>\n"
        "  MODE:  judgment\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "missing" in str(exc.value)


def test_constrained_str_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(base=PrimitiveType("str"), constraints=(("max", 300),))
    assert type_to_json_schema(t) == {"type": "string", "maxLength": 300}
