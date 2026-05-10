import json

import pytest

from clio.runtime import substitute as substitute_mod
from clio.runtime import validate as validate_mod


def test_validate_accepts_valid_instance(tmp_path):
    schema_path = tmp_path / "s.json"
    schema_path.write_text(json.dumps({
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }))
    validate_mod.validate(schema_path, {"x": 1})


def test_validate_rejects_invalid_instance(tmp_path):
    import jsonschema
    schema_path = tmp_path / "s.json"
    schema_path.write_text(json.dumps({
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }))
    with pytest.raises(jsonschema.ValidationError):
        validate_mod.validate(schema_path, {"x": "nope"})


def test_validate_accepts_when_assert_ast_holds(tmp_path):
    schema_path = tmp_path / "s.json"
    schema_path.write_text(json.dumps({
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "x-clio-assert": {
            "kind": "compare",
            "op": ">",
            "left": {"kind": "call", "func": "len", "args": [{"kind": "ident", "name": "reason"}]},
            "right": {"kind": "int", "value": 0},
        },
    }))
    validate_mod.validate(schema_path, {"reason": "ok"})


def test_validate_rejects_when_assert_ast_fails(tmp_path):
    schema_path = tmp_path / "s.json"
    schema_path.write_text(json.dumps({
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "x-clio-assert": {
            "kind": "compare",
            "op": ">",
            "left": {"kind": "call", "func": "len", "args": [{"kind": "ident", "name": "reason"}]},
            "right": {"kind": "int", "value": 0},
        },
    }))
    with pytest.raises(AssertionError):
        validate_mod.validate(schema_path, {"reason": ""})


def test_validate_per_item_assert_via_ref(tmp_path):
    contract_schema = {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "x-clio-assert": {
            "kind": "compare",
            "op": ">",
            "left": {"kind": "call", "func": "len", "args": [{"kind": "ident", "name": "reason"}]},
            "right": {"kind": "int", "value": 0},
        },
    }
    (tmp_path / "contracts").mkdir()
    contract_path = tmp_path / "contracts" / "r.schema.json"
    contract_path.write_text(json.dumps(contract_schema))

    step_schema = {"type": "array", "items": {"$ref": "../contracts/r.schema.json"}}
    step_path = tmp_path / "steps" / "s.schema.json"
    step_path.parent.mkdir()
    step_path.write_text(json.dumps(step_schema))

    validate_mod.validate(step_path, [{"reason": "ok"}, {"reason": "yes"}])
    with pytest.raises(AssertionError):
        validate_mod.validate(step_path, [{"reason": ""}])


def test_substitute_renders_state_value():
    result = substitute_mod.render('echo: ${name}\n', {"name": "Alice"})
    assert result == 'echo: "Alice"\n'


def test_substitute_keeps_schema_placeholder():
    result = substitute_mod.render('schema: ${schema}\n', {})
    assert result == 'schema: ${schema}\n'


def test_substitute_missing_key_raises():
    with pytest.raises(KeyError):
        substitute_mod.render('${missing}', {})
