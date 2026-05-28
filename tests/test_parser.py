import pytest

from clio.parser.ast_nodes import (
    DatabaseSpec,
    FlowDecl,
    HttpServerSpec,
    McpToolImpl,
    PrimitiveType,
    ResourcesDecl,
    ShellImpl,
    SqlImpl,
    SseServerSpec,
    StdioServerSpec,
)
from clio.parser.parser import ParseError, parse


def test_parse_minimal_step():
    src = "STEP foo\n  MODE: exact\n"
    program = parse(src)
    assert len(program.decls) == 1
    step = program.decls[0]
    assert step.name == "foo"
    assert step.mode == "exact"
    assert step.line == 1


def test_parse_step_missing_mode_raises():
    src = "STEP foo\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "MODE" in str(exc.value)


def test_parse_step_with_unknown_mode_raises():
    src = "STEP foo\n  MODE: bogus\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "bogus" in str(exc.value)


def test_parse_step_with_takes_and_gives_primitives():
    src = (
        "STEP echo_str\n"
        "  TAKES: input: str\n"
        "  GIVES: output: str\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert step.name == "echo_str"
    assert len(step.takes) == 1
    assert step.takes[0].name == "input"
    assert step.takes[0].type.__class__.__name__ == "PrimitiveType"
    assert step.takes[0].type.name == "str"
    assert step.gives is not None
    assert step.gives.name == "output"
    assert step.gives.type.name == "str"


def test_parse_step_with_multiple_takes():
    src = (
        "STEP add\n"
        "  TAKES: a: int, b: int\n"
        "  GIVES: sum: int\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert [f.name for f in step.takes] == ["a", "b"]
    assert all(f.type.name == "int" for f in step.takes)


def test_parse_step_with_invalid_type_token_raises():
    # A colon is not a valid start of a type expression.
    src = "STEP foo\n  TAKES: x: :int\n  MODE: exact\n"
    with pytest.raises(ParseError):
        parse(src)


def test_parse_step_rejects_duplicate_mode():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  MODE: judgment\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_step_rejects_duplicate_takes():
    src = (
        "STEP foo\n"
        "  TAKES: a: int\n"
        "  TAKES: b: int\n"
        "  MODE:  exact\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_step_rejects_duplicate_gives():
    src = (
        "STEP foo\n"
        "  GIVES: a: int\n"
        "  GIVES: b: int\n"
        "  MODE:  exact\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_list_of_record():
    src = (
        "STEP load\n"
        "  GIVES: items: List<{name: str, age: int}>\n"
        "  MODE:  exact\n"
    )
    program = parse(src)
    step = program.decls[0]
    t = step.gives.type
    assert t.__class__.__name__ == "ListType"
    inner = t.inner
    assert inner.__class__.__name__ == "RecordType"
    assert [name for name, _ in inner.fields] == ["name", "age"]


def test_parse_enum_type():
    src = "STEP foo\n  TAKES: s: enum(low|mid|high)\n  MODE: exact\n"
    program = parse(src)
    step = program.decls[0]
    t = step.takes[0].type
    assert t.__class__.__name__ == "EnumType"
    assert t.values == ("low", "mid", "high")


def test_parse_dict_of_primitives():
    src = (
        "STEP load\n"
        "  GIVES: counts: Dict<str, int>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "DictType"
    assert t.key.__class__.__name__ == "PrimitiveType"
    assert t.key.name == "str"
    assert t.value.__class__.__name__ == "PrimitiveType"
    assert t.value.name == "int"


def test_parse_dict_of_contract_ref():
    src = (
        "CONTRACT r\n"
        "  SHAPE: {x: int}\n"
        "STEP s\n"
        "  GIVES: out: Dict<str, r>\n"
        "  MODE: judgment\n"
    )
    program = parse(src)
    step = next(d for d in program.decls if d.__class__.__name__ == "StepDecl")
    t = step.gives.type
    assert t.__class__.__name__ == "DictType"
    assert t.value.__class__.__name__ == "ContractRef"
    assert t.value.name == "r"


def test_parse_dict_nested_list_value():
    src = (
        "STEP load\n"
        "  GIVES: index: Dict<str, List<int>>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "DictType"
    assert t.value.__class__.__name__ == "ListType"
    assert t.value.inner.__class__.__name__ == "PrimitiveType"


def test_parse_dict_non_str_key_raises():
    src = (
        "STEP load\n"
        "  GIVES: counts: Dict<int, int>\n"
        "  MODE: exact\n"
    )
    with pytest.raises(ParseError, match="Dict.*key.*must be `str`"):
        parse(src)


def test_parse_dict_enum_key_raises():
    src = (
        "STEP load\n"
        "  GIVES: counts: Dict<enum(a|b), int>\n"
        "  MODE: exact\n"
    )
    with pytest.raises(ParseError, match="Dict.*key.*must be `str`"):
        parse(src)


def test_parse_optional_primitive():
    src = (
        "STEP load\n"
        "  GIVES: maybe_id: Optional<int>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "OptionalType"
    assert t.inner.__class__.__name__ == "PrimitiveType"
    assert t.inner.name == "int"


def test_parse_optional_contract_ref():
    src = (
        "CONTRACT r\n"
        "  SHAPE: {x: int}\n"
        "STEP s\n"
        "  GIVES: out: Optional<r>\n"
        "  MODE: judgment\n"
    )
    program = parse(src)
    step = next(d for d in program.decls if d.__class__.__name__ == "StepDecl")
    t = step.gives.type
    assert t.__class__.__name__ == "OptionalType"
    assert t.inner.__class__.__name__ == "ContractRef"


def test_parse_optional_list_nested():
    src = (
        "STEP load\n"
        "  GIVES: maybe_rows: Optional<List<int>>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "OptionalType"
    assert t.inner.__class__.__name__ == "ListType"


def test_parse_list_of_optional():
    src = (
        "STEP load\n"
        "  GIVES: items: List<Optional<int>>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "ListType"
    assert t.inner.__class__.__name__ == "OptionalType"


def test_parse_dict_optional_value():
    src = (
        "STEP load\n"
        "  GIVES: m: Dict<str, Optional<int>>\n"
        "  MODE: exact\n"
    )
    program = parse(src)
    t = program.decls[0].gives.type
    assert t.__class__.__name__ == "DictType"
    assert t.value.__class__.__name__ == "OptionalType"


def test_parse_str_min_constraint():
    src = "CONTRACT c\n  SHAPE: {s: str(min=1)}\n"
    program = parse(src)
    field_type = dict(program.decls[0].shape.fields)["s"]
    assert field_type.__class__.__name__ == "ConstrainedType"
    assert ("min", 1) in field_type.constraints


def test_parse_str_min_max_combined():
    src = "CONTRACT c\n  SHAPE: {s: str(min=1, max=200)}\n"
    program = parse(src)
    field_type = dict(program.decls[0].shape.fields)["s"]
    assert ("min", 1) in field_type.constraints
    assert ("max", 200) in field_type.constraints


def test_parse_int_min_max():
    src = "CONTRACT c\n  SHAPE: {age: int(min=0, max=120)}\n"
    program = parse(src)
    field_type = dict(program.decls[0].shape.fields)["age"]
    assert field_type.__class__.__name__ == "ConstrainedType"
    assert field_type.base.name == "int"
    assert ("min", 0) in field_type.constraints
    assert ("max", 120) in field_type.constraints


def test_parse_float_precision():
    src = "CONTRACT c\n  SHAPE: {price: float(precision=2)}\n"
    program = parse(src)
    field_type = dict(program.decls[0].shape.fields)["price"]
    assert field_type.base.name == "float"
    assert ("precision", 2) in field_type.constraints


def test_parse_float_min_max():
    src = "CONTRACT c\n  SHAPE: {ratio: float(min=0.0, max=1.0)}\n"
    program = parse(src)
    field_type = dict(program.decls[0].shape.fields)["ratio"]
    assert field_type.base.name == "float"
    constraints = dict(field_type.constraints)
    assert constraints["min"] == 0.0
    assert constraints["max"] == 1.0


def test_parse_bool_constraints_raises():
    src = "CONTRACT c\n  SHAPE: {x: bool(min=0)}\n"
    with pytest.raises(ParseError, match="constraints.*not supported.*bool"):
        parse(src)


def test_parse_str_precision_raises():
    src = "CONTRACT c\n  SHAPE: {s: str(precision=2)}\n"
    with pytest.raises(ParseError, match="precision.*float"):
        parse(src)


def test_parse_int_precision_raises():
    src = "CONTRACT c\n  SHAPE: {n: int(precision=2)}\n"
    with pytest.raises(ParseError, match="precision.*float"):
        parse(src)


def test_parse_duplicate_constraint_raises():
    """PR-C Gemini #3317312607 — duplicate constraints would silently
    overwrite during schema build; reject at parse time instead."""
    src = "CONTRACT c\n  SHAPE: {s: str(min=1, min=5)}\n"
    with pytest.raises(ParseError, match="duplicate constraint `min`"):
        parse(src)


def test_parse_unsatisfiable_min_gt_max_raises():
    """PR-C Gemini #3317312613 — min > max produces an unsatisfiable
    schema with no error; reject at parse time."""
    src = "CONTRACT c\n  SHAPE: {n: int(min=10, max=5)}\n"
    with pytest.raises(ParseError, match="unsatisfiable.*min.*10.*max.*5"):
        parse(src)


def test_parse_unsatisfiable_str_min_gt_max_raises():
    src = "CONTRACT c\n  SHAPE: {s: str(min=20, max=5)}\n"
    with pytest.raises(ParseError, match="unsatisfiable.*min.*20.*max.*5"):
        parse(src)


def test_parse_unbalanced_brace_raises():
    src = "STEP foo\n  GIVES: x: {name: str\n  MODE: exact\n"
    with pytest.raises(ParseError):
        parse(src)


def test_parse_contract_with_record_shape():
    src = (
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str, risk: enum(low|mid|high), reason: str}\n"
    )
    program = parse(src)
    assert len(program.decls) == 1
    c = program.decls[0]
    assert c.__class__.__name__ == "ContractDecl"
    assert c.name == "customer_risk"
    assert c.shape.__class__.__name__ == "RecordType"


def test_parse_step_referencing_contract():
    src = (
        "CONTRACT r\n"
        "  SHAPE: {x: int}\n"
        "STEP s\n"
        "  GIVES: out: List<r>\n"
        "  MODE:  judgment\n"
    )
    program = parse(src)
    step = next(d for d in program.decls if d.__class__.__name__ == "StepDecl")
    list_t = step.gives.type
    assert list_t.__class__.__name__ == "ListType"
    inner = list_t.inner
    assert inner.__class__.__name__ == "ContractRef"
    assert inner.name == "r"


def test_parse_str_with_max_constraint():
    src = "STEP foo\n  GIVES: r: str(max=300)\n  MODE: exact\n"
    program = parse(src)
    step = program.decls[0]
    t = step.gives.type
    assert t.__class__.__name__ == "ConstrainedType"
    assert t.base.__class__.__name__ == "PrimitiveType"
    assert t.base.name == "str"
    assert t.constraints == (("max", 300),)


def test_parse_minimal_flow_with_string_kwarg():
    src = (
        "STEP a\n  GIVES: x: str\n  MODE: exact\n"
        "STEP b\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW f\n"
        '  a(input="hi")\n'
        "    -> b(x)\n"
    )
    program = parse(src)
    flows = [d for d in program.decls if d.__class__.__name__ == "FlowDecl"]
    assert len(flows) == 1
    flow = flows[0]
    assert [c.name for c in flow.chain] == ["a", "b"]
    assert flow.chain[0].kwargs == (("input", "hi"),)
    k = dict(flow.chain[1].kwargs)
    assert "x" in k
    assert k["x"] == "@x"


def test_parse_flow_arrow_required_between_calls():
    src = (
        "STEP a\n  MODE: exact\n"
        "STEP b\n  MODE: exact\n"
        "FLOW f\n  a()\n    b()\n"
    )
    with pytest.raises(ParseError):
        parse(src)


def test_parse_contract_with_assert():
    src = (
        "CONTRACT r\n"
        "  SHAPE:  {x: int, name: str}\n"
        "  ASSERT: len(name) > 0\n"
    )
    program = parse(src)
    c = program.decls[0]
    assert c.assert_expr is not None


def test_parse_resources_block():
    src = (
        "STEP foo\n  MODE: exact\n"
        "RESOURCES\n  target: claude-cli\n  models: [haiku, sonnet]\n"
    )
    program = parse(src)
    res = [d for d in program.decls if d.__class__.__name__ == "ResourcesDecl"]
    assert len(res) == 1
    assert res[0].target == "claude-cli"
    assert res[0].models == ("haiku", "sonnet")


def test_parse_resources_unsupported_field_raises():
    src = (
        "STEP foo\n  MODE: exact\n"
        "RESOURCES\n  budget: 30\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "budget" in str(exc.value)
    assert "v0.1" in str(exc.value)


def test_parse_resources_target_python():
    """RESOURCES target: python is now accepted (was rejected pre-v0.6)."""
    src = "STEP foo\n  MODE: exact\nRESOURCES\n  target: python\n"
    program = parse(src)
    res = [d for d in program.decls if d.__class__.__name__ == "ResourcesDecl"]
    assert len(res) == 1
    assert res[0].target == "python"


def test_parse_resources_target_mcp_server():
    """RESOURCES target: mcp-server is now accepted (was rejected pre-v0.6)."""
    src = "STEP foo\n  MODE: exact\nRESOURCES\n  target: mcp-server\n"
    program = parse(src)
    res = [d for d in program.decls if d.__class__.__name__ == "ResourcesDecl"]
    assert len(res) == 1
    assert res[0].target == "mcp-server"


def test_parse_resources_target_langgraph():
    """RESOURCES target: langgraph is accepted since v0.7."""
    src = "STEP foo\n  MODE: exact\nRESOURCES\n  target: langgraph\n"
    program = parse(src)
    res = [d for d in program.decls if d.__class__.__name__ == "ResourcesDecl"]
    assert len(res) == 1
    assert res[0].target == "langgraph"


def test_parse_resources_target_claude_skill():
    """RESOURCES target: claude-skill is accepted since v0.15 (parser was
    out of sync with the --target CLI flag from v0.14 through early v0.15)."""
    src = "STEP foo\n  MODE: exact\nRESOURCES\n  target: claude-skill\n"
    program = parse(src)
    res = [d for d in program.decls if d.__class__.__name__ == "ResourcesDecl"]
    assert len(res) == 1
    assert res[0].target == "claude-skill"


def test_parse_resources_unknown_target_rejected():
    """Unknown targets get a clear, enumerated error listing all 5 valid ones."""
    src = "STEP foo\n  MODE: exact\nRESOURCES\n  target: rust\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    msg = str(exc.value)
    assert "rust" in msg
    for t in ("claude-cli", "python", "mcp-server", "langgraph", "claude-skill"):
        assert t in msg


def test_parse_step_with_cache_off():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n  CACHE: off\n"
    program = parse(src)
    step = program.decls[0]
    assert step.cache is not None
    assert step.cache.mode == "off"
    assert step.cache.ttl_seconds is None


def test_parse_step_with_cache_on():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n  CACHE: on\n"
    step = parse(src).decls[0]
    assert step.cache.mode == "on"
    assert step.cache.ttl_seconds is None


def test_parse_step_with_cache_ttl():
    cases = {"30s": 30, "5m": 300, "24h": 86400, "7d": 604800}
    for dur, expected in cases.items():
        src = f"STEP s\n  GIVES: r: str\n  MODE: judgment\n  CACHE: ttl({dur})\n"
        step = parse(src).decls[0]
        assert step.cache.mode == "ttl"
        assert step.cache.ttl_seconds == expected, dur


def test_parse_cache_on_exact_step_raises():
    src = "STEP s\n  GIVES: r: str\n  MODE: exact\n  CACHE: on\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    msg = str(exc.value)
    assert "CACHE" in msg
    assert "judgment" in msg


def test_parse_cache_duplicate_raises():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n  CACHE: on\n  CACHE: off\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_on_fail_retry_only():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n  ON_FAIL: retry(3)\n"
    step = parse(src).decls[0]
    assert step.on_fail is not None
    assert len(step.on_fail.strategies) == 1
    s = step.on_fail.strategies[0]
    assert s.kind == "retry"
    assert s.max_retries == 3


def test_parse_on_fail_chain():
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        '  ON_FAIL: retry(3) then escalate then abort("nope")\n'
    )
    step = parse(src).decls[0]
    kinds = [s.kind for s in step.on_fail.strategies]
    assert kinds == ["retry", "escalate", "abort"]
    assert step.on_fail.strategies[0].max_retries == 3
    assert step.on_fail.strategies[2].abort_message == "nope"


def test_parse_on_fail_fallback_clause_lexes():
    # Resolution + compat check is in slice G; in slice E the parser only stores the name.
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        "  ON_FAIL: retry(2) then fallback(other_step)\n"
    )
    step = parse(src).decls[0]
    fb = step.on_fail.strategies[1]
    assert fb.kind == "fallback"
    assert fb.fallback_step_name == "other_step"


def test_parse_on_fail_on_exact_step_raises():
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: exact\n  ON_FAIL: retry(3)\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "ON_FAIL" in str(exc.value)
    assert "judgment" in str(exc.value)


def test_parse_on_fail_duplicate_raises():
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        "  ON_FAIL: retry(2)\n"
        "  ON_FAIL: abort(\"x\")\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_lang_on_exact_step():
    src = "STEP foo\n  MODE: exact\n  LANG: python\n"
    step = parse(src).decls[0]
    assert step.lang == "python"


def test_parse_lang_omitted_defaults_to_none():
    src = "STEP foo\n  MODE: exact\n"
    step = parse(src).decls[0]
    assert step.lang is None


def test_parse_lang_accepts_all_documented_values():
    for lang in ("python", "rust", "go", "node", "bash", "auto"):
        src = f"STEP foo\n  MODE: exact\n  LANG: {lang}\n"
        step = parse(src).decls[0]
        assert step.lang == lang


def test_parse_lang_on_judgment_step_raises():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n  LANG: python\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "LANG" in str(exc.value)
    assert "exact" in str(exc.value)


def test_parse_lang_duplicate_raises():
    src = "STEP foo\n  MODE: exact\n  LANG: python\n  LANG: rust\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_lang_unknown_value_raises():
    # `cobol` is not a CLIO LANG. The lexer treats it as IDENT, which the
    # KEYWORD-expecting branch rejects; either way the parse must fail.
    src = "STEP foo\n  MODE: exact\n  LANG: cobol\n"
    with pytest.raises(ParseError):
        parse(src)


# --- impl: block parsing ---------------------------------------------------

def test_parse_impl_code_minimal():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: code\n"
    )
    step = parse(src).decls[0]
    assert step.impl is not None
    assert step.impl.__class__.__name__ == "CodeImpl"
    assert step.impl.lang is None


def test_parse_impl_code_with_lang():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: code\n"
        "    lang: rust\n"
    )
    step = parse(src).decls[0]
    assert step.impl.__class__.__name__ == "CodeImpl"
    assert step.impl.lang == "rust"


def test_parse_impl_rest_minimal():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://api.example.com/v1/items"\n'
    )
    step = parse(src).decls[0]
    assert step.impl.__class__.__name__ == "RestImpl"
    assert step.impl.method == "GET"
    assert step.impl.url == "https://api.example.com/v1/items"
    assert step.impl.response_path is None
    assert step.impl.timeout_seconds is None
    assert step.impl.retry is None
    assert step.impl.query is None
    assert step.impl.headers is None
    assert step.impl.body is None


def test_parse_impl_rest_full():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://api.example.com/v1/items"\n'
        '    response_path: "items[0].id"\n'
        "    timeout: 30s\n"
        "    retry: {attempts: 3}\n"
    )
    step = parse(src).decls[0]
    assert step.impl.method == "POST"
    assert step.impl.response_path == "items[0].id"
    assert step.impl.timeout_seconds == 30
    assert step.impl.retry is not None
    assert step.impl.retry.attempts == 3
    assert step.impl.retry.backoff == "exponential"
    assert step.impl.retry.base == 0.1
    assert step.impl.retry.cap == 30.0
    assert step.impl.retry.on == ("5xx", "429", "timeout")


def test_parse_impl_shell_minimal():
    src = (
        "STEP s\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "pdftotext ${file} -"\n'
    )
    step = parse(src).decls[0]
    assert step.impl.__class__.__name__ == "ShellImpl"
    assert step.impl.cmd == "pdftotext ${file} -"
    assert step.impl.timeout_seconds is None


def test_parse_impl_shell_with_timeout():
    src = (
        "STEP s\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "echo hi"\n'
        "    timeout: 5s\n"
    )
    step = parse(src).decls[0]
    assert step.impl.timeout_seconds == 5


def test_parse_impl_shell_missing_cmd_raises():
    src = (
        "STEP s\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        "    timeout: 5s\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "cmd" in str(exc.value)


def test_parse_impl_shell_unknown_field_raises():
    src = (
        "STEP s\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "echo"\n'
        '    stdin: "ignored"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "stdin" in str(exc.value)


def test_parse_impl_shell_with_parse_json():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: json\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert isinstance(step.impl, ShellImpl)
    assert step.impl.cmd == "cat ${file}"
    assert step.impl.parse == "json"


def test_parse_impl_shell_with_parse_none_explicit():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: none\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert step.impl.parse == "none"


def test_parse_impl_shell_parse_invalid_value_raises():
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: yaml\n"
    )
    with pytest.raises(ParseError, match="unknown impl.parse"):
        parse(src)


def test_parse_impl_on_judgment_step_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  impl:\n"
        "    mode: code\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "impl" in str(exc.value)
    assert "exact" in str(exc.value)


def test_parse_impl_duplicate_block_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: code\n"
        "  impl:\n"
        "    mode: code\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_impl_missing_mode_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    lang: python\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "mode" in str(exc.value).lower()


def test_parse_impl_unknown_mode_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: graphql\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "graphql" in str(exc.value) or "impl.mode" in str(exc.value)


def test_parse_impl_rest_missing_required_field_raises():
    # url is required for impl.mode: rest
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "url" in str(exc.value)


def test_parse_impl_rest_unknown_method_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: TRACE\n"
        '    url: "https://example.com"\n'
    )
    with pytest.raises(ParseError):
        parse(src)


# --- impl.rest extended fields (query/headers/body/retry) ---


def test_parse_impl_rest_query():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  TAKES: address: str\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/geocode"\n'
        '    query: {address: "${address}", limit: 10, key: "env:API_KEY"}\n'
    )
    step = parse(src).decls[0]
    assert step.impl.query == (
        ("address", "${address}"),
        ("limit", 10),
        ("key", "env:API_KEY"),
    )


def test_parse_impl_rest_headers():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        '    headers: {Authorization: "env:AUTH_HEADER", Accept: "application/json"}\n'
    )
    step = parse(src).decls[0]
    assert step.impl.headers == (
        ("Authorization", "env:AUTH_HEADER"),
        ("Accept", "application/json"),
    )


def test_parse_impl_rest_body_json_dict():
    from clio.parser.ast_nodes import JsonBody
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  TAKES: id: str\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/items"\n'
        '    body: {user_id: "${id}", limit: 100, active: true}\n'
    )
    step = parse(src).decls[0]
    assert isinstance(step.impl.body, JsonBody)
    assert step.impl.body.fields == (
        ("user_id", "${id}"),
        ("limit", 100),
        ("active", True),
    )


def test_parse_impl_rest_body_raw_string():
    from clio.parser.ast_nodes import RawBody
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  TAKES: msg: str\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/echo"\n'
        '    body: "raw text ${msg}"\n'
    )
    step = parse(src).decls[0]
    assert isinstance(step.impl.body, RawBody)
    assert step.impl.body.template == "raw text ${msg}"


def test_parse_impl_rest_body_file():
    from clio.parser.ast_nodes import FileBody
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/upload"\n'
        '    body: "@./payload.json"\n'
    )
    step = parse(src).decls[0]
    assert isinstance(step.impl.body, FileBody)
    assert step.impl.body.path == "./payload.json"


def test_parse_impl_rest_body_form():
    from clio.parser.ast_nodes import FormBody
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  TAKES: name: str\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/login"\n'
        '    body: {form: {user: "${name}", remember: "true"}}\n'
    )
    step = parse(src).decls[0]
    assert isinstance(step.impl.body, FormBody)
    assert step.impl.body.fields == (("user", "${name}"), ("remember", "true"))


def test_parse_impl_rest_body_multipart():
    from clio.parser.ast_nodes import MultipartBody
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/upload"\n'
        '    body: {multipart: {label: "doc", file: "@./upload.pdf"}}\n'
    )
    step = parse(src).decls[0]
    assert isinstance(step.impl.body, MultipartBody)
    assert step.impl.body.fields == (("label", "doc"), ("file", "@./upload.pdf"))


def test_parse_impl_rest_body_form_with_at_prefix_rejected():
    # @./file is only allowed inside multipart bodies, not form-urlencoded.
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/x"\n'
        '    body: {form: {file: "@./pdf"}}\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "multipart" in str(exc.value)


def test_parse_impl_rest_body_on_get_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        '    body: "data"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "GET" in str(exc.value)


def test_parse_impl_rest_retries_scalar_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        "    retries: 3\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "retry: {attempts" in str(exc.value)


def test_parse_impl_rest_retry_full():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        "    retry: {attempts: 5, backoff: constant, base: 0.5, cap: 10, on: [\"5xx\", \"timeout\"]}\n"
    )
    step = parse(src).decls[0]
    r = step.impl.retry
    assert r.attempts == 5
    assert r.backoff == "constant"
    assert r.base == 0.5
    assert r.cap == 10.0
    assert r.on == ("5xx", "timeout")


def test_parse_impl_rest_retry_attempts_zero_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        "    retry: {attempts: 0}\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "positive" in str(exc.value)


def test_parse_impl_rest_retry_unknown_backoff_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        "    retry: {attempts: 3, backoff: linear}\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "exponential" in str(exc.value)


def test_parse_impl_rest_retry_on_unknown_token_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/x"\n'
        "    retry: {attempts: 3, on: [\"4xx\"]}\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "4xx" in str(exc.value) or "unknown" in str(exc.value).lower()


def test_parse_impl_rest_body_form_and_multipart_combined_rejected():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/x"\n'
        "    body: {form: {a: \"1\"}, multipart: {b: \"2\"}}\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "form" in str(exc.value).lower() and "multipart" in str(exc.value).lower()


def test_parse_impl_unknown_field_for_code_mode_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: code\n"
        '    url: "irrelevant"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "url" in str(exc.value)
    assert "code" in str(exc.value)


# --- invoke: block parsing -------------------------------------------------

def test_parse_invoke_cli_minimal():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
    )
    step = parse(src).decls[0]
    assert step.invoke is not None
    assert step.invoke.__class__.__name__ == "CliInvoke"
    assert step.invoke.cli is None
    assert step.invoke.model is None


def test_parse_invoke_cli_with_fields():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "    cli: claude\n"
        "    model: opus\n"
        "    output_format: json\n"
        "    max_turns: 5\n"
    )
    step = parse(src).decls[0]
    assert step.invoke.cli == "claude"
    assert step.invoke.model == "opus"
    assert step.invoke.output_format == "json"
    assert step.invoke.max_turns == 5


def test_parse_invoke_api_minimal():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: anthropic\n"
        '    model: "claude-opus-4-7"\n'
    )
    step = parse(src).decls[0]
    assert step.invoke.__class__.__name__ == "ApiInvoke"
    assert step.invoke.protocol == "anthropic"
    assert step.invoke.model == "claude-opus-4-7"
    assert step.invoke.base_url is None
    assert step.invoke.auth is None


def test_parse_invoke_api_full():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: openai\n"
        '    model: "gemini-1.5-pro"\n'
        '    base_url: "http://litellm.local:4000"\n'
        '    auth: "env:LITELLM_KEY"\n'
        "    temperature: 0.0\n"
        "    max_tokens: 1024\n"
        "    timeout: 60s\n"
        "    retries: 3\n"
    )
    step = parse(src).decls[0]
    assert step.invoke.protocol == "openai"
    assert step.invoke.model == "gemini-1.5-pro"
    assert step.invoke.base_url == "http://litellm.local:4000"
    assert step.invoke.auth == "env:LITELLM_KEY"
    assert step.invoke.temperature == 0.0
    assert step.invoke.max_tokens == 1024
    assert step.invoke.timeout_seconds == 60
    assert step.invoke.retries == 3


def test_parse_invoke_on_exact_step_raises():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  invoke:\n"
        "    mode: cli\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "invoke" in str(exc.value)
    assert "judgment" in str(exc.value)


def test_parse_invoke_duplicate_block_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: anthropic\n"
        '    model: "x"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate" in str(exc.value).lower()


def test_parse_invoke_missing_mode_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    cli: claude\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "mode" in str(exc.value).lower()


def test_parse_invoke_unknown_mode_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: spawn\n"
    )
    with pytest.raises(ParseError):
        parse(src)


def test_parse_invoke_api_missing_protocol_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        '    model: "claude-opus-4-7"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "protocol" in str(exc.value)


def test_parse_invoke_api_missing_model_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: anthropic\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "model" in str(exc.value)


def test_parse_invoke_api_unknown_protocol_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: cohere\n"
        '    model: "command-r"\n'
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "cohere" in str(exc.value) or "protocol" in str(exc.value)


def test_parse_invoke_api_unknown_field_raises():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: anthropic\n"
        '    model: "x"\n'
        "    speed: 5\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "speed" in str(exc.value)


# --- FOR EACH parsing ------------------------------------------------------

_FOREACH_SRC = (
    "STEP load\n"
    "  GIVES: items: List<str>\n"
    "  MODE: exact\n"
    "STEP process\n"
    "  TAKES: x: str\n"
    "  GIVES: r: str\n"
    "  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH item IN items:\n"
    "         process(x=item)\n"
)


def test_parse_for_each_basic():
    flow = next(d for d in parse(_FOREACH_SRC).decls if d.__class__.__name__ == "FlowDecl")
    assert [type(c).__name__ for c in flow.chain] == ["StepCall", "ForEachBlock"]
    fe = flow.chain[1]
    assert fe.loop_var == "item"
    assert fe.collection == "items"
    assert len(fe.body) == 1
    assert fe.body[0].__class__.__name__ == "StepCall"
    assert fe.body[0].name == "process"


def test_parse_for_each_kwarg_binding_uses_state_ref_format():
    # `process(x=item)` should produce kwargs (("x", "@item"),) — same convention
    # as the shorthand `step(name)` form.
    flow = next(d for d in parse(_FOREACH_SRC).decls if d.__class__.__name__ == "FlowDecl")
    body_call = flow.chain[1].body[0]
    assert body_call.kwargs == (("x", "@item"),)


def test_parse_for_each_with_arrow_chain_in_body():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP a\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "STEP b\n  TAKES: y: str\n  GIVES: z: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         a(x=item)\n"
        "           -> b(y)\n"
    )
    flow = next(d for d in parse(src).decls if d.__class__.__name__ == "FlowDecl")
    fe = flow.chain[1]
    assert len(fe.body) == 2
    assert [c.name for c in fe.body] == ["a", "b"]


def test_for_each_block_defaults_are_sequential():
    """Sequential FOR EACH must build with parallel=False, collector=None."""
    flow = next(d for d in parse(_FOREACH_SRC).decls if d.__class__.__name__ == "FlowDecl")
    fe = flow.chain[1]
    assert fe.parallel is False
    assert fe.collector is None


def test_parse_for_each_nested():
    src = (
        "STEP load\n  GIVES: matrix: List<str>\n  MODE: exact\n"
        "STEP inner\n  TAKES: cell: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH row IN matrix:\n"
        "         FOR EACH cell IN row:\n"
        "           inner(cell=cell)\n"
    )
    flow = next(d for d in parse(src).decls if d.__class__.__name__ == "FlowDecl")
    outer = flow.chain[1]
    assert outer.__class__.__name__ == "ForEachBlock"
    assert outer.body[0].__class__.__name__ == "ForEachBlock"
    inner = outer.body[0]
    assert inner.loop_var == "cell"
    assert inner.collection == "row"


_FOREACH_PARALLEL_SRC = (
    "STEP load\n"
    "  GIVES: items: List<str>\n"
    "  MODE: exact\n"
    "STEP process\n"
    "  TAKES: x: str\n"
    "  GIVES: r: str\n"
    "  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH item IN items PARALLEL AS results:\n"
    "         process(x=item)\n"
)


def test_parse_for_each_parallel_with_as():
    flow = next(d for d in parse(_FOREACH_PARALLEL_SRC).decls if d.__class__.__name__ == "FlowDecl")
    fe = flow.chain[1]
    assert fe.parallel is True
    assert fe.collector == "results"
    assert fe.loop_var == "item"
    assert fe.collection == "items"


def test_parse_for_each_parallel_without_as_fails():
    bad = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(Exception, match="PARALLEL requires an AS"):
        parse(bad)


def test_parse_for_each_as_without_parallel_fails():
    bad = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items AS results:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(Exception, match="AS binding is only valid with PARALLEL"):
        parse(bad)


# --- impl.mode: mcp_tool + RESOURCES.mcp_servers (v0.10) ---------------------

_MCP_FLOW_PROLOG = (
    "STEP search\n"
    "  TAKES: query: str\n"
    "  GIVES: r: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:    mcp_tool\n"
    "    server:  docs\n"
    "    tool:    search\n"
    "    args:    {q: \"${query}\"}\n"
    "FLOW f\n"
    '  search(query="x")\n'
)


def test_parse_mcp_tool_impl_basic():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + '      command: "mcp-server-docs"\n'
    )
    prog = parse(src)
    step = next(d for d in prog.decls if hasattr(d, "name") and d.name == "search")
    assert isinstance(step.impl, McpToolImpl)
    assert step.impl.server == "docs"
    assert step.impl.tool == "search"
    assert step.impl.args == (("q", "${query}"),)
    assert step.impl.timeout_seconds == 60   # default
    assert step.impl.parse == "json"          # default


def test_parse_mcp_tool_impl_with_timeout_and_parse():
    src = (
        "STEP search\n"
        "  TAKES: query: str\n"
        "  GIVES: r: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    mcp_tool\n"
        "    server:  docs\n"
        "    tool:    search\n"
        "    timeout: 30s\n"
        "    parse:   text\n"
        "FLOW f\n"
        '  search(query="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  mcp_servers:\n"
        "    docs:\n"
        '      command: "mcp-server-docs"\n'
    )
    prog = parse(src)
    step = next(d for d in prog.decls if hasattr(d, "name") and d.name == "search")
    assert step.impl.timeout_seconds == 30
    assert step.impl.parse == "text"


def test_parse_mcp_tool_impl_retry_rejected():
    src = (
        "STEP search\n"
        "  TAKES: query: str\n"
        "  GIVES: r: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    mcp_tool\n"
        "    server:  docs\n"
        "    tool:    search\n"
        "    retry:   {attempts: 3}\n"
        "FLOW f\n"
        '  search(query="x")\n'
    )
    with pytest.raises(ParseError, match="impl.mcp_tool does not support 'retry:'"):
        parse(src)


def test_parse_mcp_tool_impl_unknown_field_rejected():
    src = _MCP_FLOW_PROLOG.replace(
        '    args:    {q: "${query}"}\n',
        '    args:    {q: "${query}"}\n    bogus: 1\n',
    )
    with pytest.raises(ParseError, match="unknown field 'bogus'"):
        parse(src)


def test_parse_mcp_tool_impl_missing_server_rejected():
    src = (
        "STEP search\n"
        "  TAKES: query: str\n"
        "  GIVES: r: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    mcp_tool\n"
        "    tool:    search\n"
        "FLOW f\n"
        '  search(query="x")\n'
    )
    with pytest.raises(ParseError, match="missing required field 'server'"):
        parse(src)


def test_parse_mcp_tool_impl_invalid_parse_value_rejected():
    src = _MCP_FLOW_PROLOG.replace(
        '    args:    {q: "${query}"}\n',
        '    args:    {q: "${query}"}\n    parse:   xml\n',
    )
    with pytest.raises(ParseError, match="parse must be 'json' or 'text'"):
        parse(src)


def test_parse_mcp_servers_stdio_full():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: stdio\n"
        + '      command:   "mcp-server-docs"\n'
        + '      args:      ["--cfg", "x.json"]\n'
        + '      env:       {INDEX: "env:DOCS_INDEX"}\n'
    )
    prog = parse(src)
    res = next(d for d in prog.decls if isinstance(d, ResourcesDecl))
    assert len(res.mcp_servers) == 1
    spec = res.mcp_servers[0]
    assert isinstance(spec, StdioServerSpec)
    assert spec.name == "docs"
    assert spec.command == "mcp-server-docs"
    assert spec.args == ("--cfg", "x.json")
    assert spec.env == (("INDEX", "env:DOCS_INDEX"),)


def test_parse_mcp_servers_sse_full():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: sse\n"
        + '      url:       "https://api.example.com/mcp"\n'
        + '      headers:   {Authorization: "env:TOKEN"}\n'
    )
    prog = parse(src)
    res = next(d for d in prog.decls if isinstance(d, ResourcesDecl))
    spec = res.mcp_servers[0]
    assert isinstance(spec, SseServerSpec)
    assert spec.url == "https://api.example.com/mcp"
    assert spec.headers == (("Authorization", "env:TOKEN"),)


def test_parse_mcp_servers_http_localhost_allowed():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: http\n"
        + '      url:       "http://localhost:8765/mcp"\n'
    )
    prog = parse(src)
    res = next(d for d in prog.decls if isinstance(d, ResourcesDecl))
    assert isinstance(res.mcp_servers[0], HttpServerSpec)


def test_parse_mcp_servers_stdio_with_url_rejected():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: stdio\n"
        + '      command:   "mcp-server-docs"\n'
        + '      url:       "https://example.com"\n'
    )
    with pytest.raises(ParseError, match="transport: stdio.*declares 'url'"):
        parse(src)


def test_parse_mcp_servers_sse_with_command_rejected():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: sse\n"
        + '      url:       "https://example.com"\n'
        + '      command:   "bogus"\n'
    )
    with pytest.raises(ParseError, match="transport: sse.*declares 'command'"):
        parse(src)


def test_parse_mcp_servers_unknown_transport_rejected():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: ftp\n"
        + '      command:   "x"\n'
    )
    with pytest.raises(ParseError, match="transport must be one of"):
        parse(src)


def test_parse_mcp_servers_duplicate_name_rejected():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + '      command: "x"\n'
        + "    docs:\n"
        + '      command: "y"\n'
    )
    with pytest.raises(ParseError, match="duplicate server name"):
        parse(src)


def test_parse_mcp_servers_url_must_be_https_or_localhost():
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: sse\n"
        + '      url:       "http://example.com/mcp"\n'
    )
    with pytest.raises(ParseError, match="url must be https"):
        parse(src)


def test_parse_mcp_servers_url_rejects_localhost_lookalike_ssrf():
    # `startswith("http://localhost")` would let through `localhost.attacker.com`.
    # urlparse + hostname check correctly rejects it.
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: sse\n"
        + '      url:       "http://localhost.attacker.com/mcp"\n'
    )
    with pytest.raises(ParseError, match="url must be https"):
        parse(src)


def test_parse_mcp_servers_url_rejects_127_lookalike_ssrf():
    # Same family — `127.0.0.1.nip.io` resolves elsewhere but startswith would
    # have accepted it.
    src = (
        _MCP_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  mcp_servers:\n"
        + "    docs:\n"
        + "      transport: sse\n"
        + '      url:       "http://127.0.0.1.nip.io/mcp"\n'
    )
    with pytest.raises(ParseError, match="url must be https"):
        parse(src)


def test_parse_mcp_tool_args_with_nested_dict_and_list():
    src = (
        "STEP query\n"
        "  TAKES: q: str\n"
        "  GIVES: r: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    mcp_tool\n"
        "    server:  db\n"
        "    tool:    select\n"
        "    args:    {filters: {kind: \"${q}\", limit: 10}, ids: [1, 2, 3]}\n"
        "FLOW f\n"
        '  query(q="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  mcp_servers:\n"
        "    db:\n"
        '      command: "mcp-server-db"\n'
    )
    prog = parse(src)
    step = next(d for d in prog.decls if hasattr(d, "name") and d.name == "query")
    args = dict(step.impl.args)
    assert args["filters"] == {"kind": "${q}", "limit": 10}
    assert args["ids"] == [1, 2, 3]


# --- impl.mode: sql + RESOURCES.databases (v0.11) ---------------------------

_SQL_FLOW_PROLOG = (
    "STEP get_orders\n"
    "  TAKES: email: str\n"
    "  GIVES: orders: List<{id: int, status: str}>\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:  sql\n"
    "    db:    crm\n"
    "    query: |\n"
    "      SELECT id, status\n"
    "      FROM orders\n"
    "      WHERE email = :email\n"
    "FLOW f\n"
    '  get_orders(email="x@y")\n'
)


def test_parse_sql_impl_basic():
    src = (
        _SQL_FLOW_PROLOG
        + "RESOURCES\n"
        + "  target: python\n"
        + "  databases:\n"
        + "    crm:\n"
        + "      driver: sqlite\n"
        + '      url:    "./crm.sqlite"\n'
    )
    prog = parse(src)
    step = next(d for d in prog.decls if hasattr(d, "name") and d.name == "get_orders")
    assert isinstance(step.impl, SqlImpl)
    assert step.impl.db == "crm"
    assert "SELECT id, status" in step.impl.query
    assert "WHERE email = :email" in step.impl.query


def test_parse_sql_impl_retry_rejected():
    src = (
        "STEP get_orders\n"
        "  TAKES: email: str\n"
        "  GIVES: orders: List<{id: int, status: str}>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "    retry: {attempts: 3}\n"
        "FLOW f\n"
        '  get_orders(email="x")\n'
    )
    with pytest.raises(ParseError, match="impl.sql does not support 'retry:'"):
        parse(src)


def test_parse_sql_impl_env_in_query_rejected():
    src = (
        "STEP get_orders\n"
        "  TAKES: email: str\n"
        "  GIVES: orders: List<{id: int}>\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT * FROM t WHERE token = env:TOKEN"\n'
        "FLOW f\n"
        '  get_orders(email="x")\n'
    )
    with pytest.raises(ParseError, match="may not contain 'env:NAME'"):
        parse(src)


def test_parse_sql_impl_missing_db_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
    )
    with pytest.raises(ParseError, match="missing required field 'db'"):
        parse(src)


def test_parse_sql_impl_missing_query_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        "FLOW f\n"
        '  s(email="x")\n'
    )
    with pytest.raises(ParseError, match="missing required field 'query'"):
        parse(src)


def test_parse_sql_impl_unknown_field_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    sql\n"
        "    db:      crm\n"
        '    query:   "SELECT 1"\n'
        "    timeout: 30s\n"
        "FLOW f\n"
        '  s(email="x")\n'
    )
    with pytest.raises(ParseError, match="unknown field 'timeout' for impl.mode: sql"):
        parse(src)


def test_parse_databases_block_three_drivers():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url:    ":memory:"\n'
        "    pg:\n"
        "      driver: postgres\n"
        '      url:    "env:PG_URL"\n'
        "    legacy:\n"
        "      driver: mysql\n"
        '      url:    "mysql://user:pass@h:3306/db"\n'
    )
    prog = parse(src)
    res = next(d for d in prog.decls if isinstance(d, ResourcesDecl))
    by_name = {d.name: d for d in res.databases}
    assert isinstance(by_name["crm"], DatabaseSpec)
    assert by_name["crm"].driver == "sqlite"
    assert by_name["crm"].url == ":memory:"
    assert by_name["pg"].driver == "postgres"
    assert by_name["pg"].url == "env:PG_URL"
    assert by_name["legacy"].driver == "mysql"


def test_parse_databases_duplicate_name_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url:    ":memory:"\n'
        "    crm:\n"
        "      driver: postgres\n"
        '      url:    "postgresql://h/db"\n'
    )
    with pytest.raises(ParseError, match="duplicate database name 'crm'"):
        parse(src)


def test_parse_databases_unknown_driver_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: duckdb\n"
        '      url:    ":memory:"\n'
    )
    with pytest.raises(ParseError, match=r"driver must be one of"):
        parse(src)


def test_parse_databases_missing_url_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
    )
    with pytest.raises(ParseError, match="missing required field 'url'"):
        parse(src)


def test_parse_databases_unknown_field_rejected():
    src = (
        "STEP s\n"
        "  TAKES: email: str\n"
        "  GIVES: x: int\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "SELECT 1"\n'
        "FLOW f\n"
        '  s(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url:    ":memory:"\n'
        "      port:   5432\n"
    )
    with pytest.raises(ParseError, match=r"unknown field 'port'"):
        parse(src)


def test_parse_error_three_segment_middle_not_error():
    """3-segment dotted kwarg with middle != 'error' must raise ParseError."""
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE: exact

STEP b
  TAKES: foo: int
  GIVES: bar: int
  MODE: exact

FLOW f
  a(x=1)

  RESCUE a:
    -> b(foo=a.report.client)
    -> abort("nope")

RESOURCES
  target: python
  models: [haiku]
"""
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "unknown 2-segment kwarg value 'a.report'" in str(exc.value)
    assert "<step>.error.<message|type>" in str(exc.value)


@pytest.mark.parametrize(
    "src_body, expected_msg_fragment",
    [
        ("    -> RESUME(foo)",                 "expected DOT"),
        ('    -> RESUME("literal")',           "RESUME requires '<step>.<field>'"),
        ("    -> RESUME()",                    "RESUME requires '<step>.<field>'"),
        ("    -> RESUME(a.b.c)",               "RESUME accepts exactly '<step>.<field>'"),
        ("    -> RESUME(a.b, c.d)",            "RESUME takes a single '<step>.<field>'"),
    ],
)
def test_parse_error_malformed_resume(src_body, expected_msg_fragment):
    src = f"""
STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE: judgment

FLOW pipeline
  detect(rows=rows)

  RESCUE detect:
{src_body}

RESOURCES
  target: python
  models: [haiku]
"""
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert expected_msg_fragment in str(exc.value)


# -- DESCRIPTION / STRATEGIES (v0.15) --


def test_parse_step_with_description_quoted_string():
    src = (
        "STEP foo\n"
        '  DESCRIPTION: "one-line intent"\n'
        "  TAKES: x: str\n  GIVES: y: str\n  MODE: judgment\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert step.description == "one-line intent"
    assert step.strategies is None


def test_parse_step_with_description_and_strategies_block():
    src = (
        "STEP foo\n"
        "  DESCRIPTION: |\n"
        "    First line.\n"
        "    Second line.\n"
        "  STRATEGIES: |\n"
        "    - prefer A\n"
        "    - fallback to B\n"
        "  TAKES: x: str\n  GIVES: y: str\n  MODE: judgment\n"
    )
    program = parse(src)
    step = program.decls[0]
    assert "First line" in step.description
    assert "Second line" in step.description
    assert "prefer A" in step.strategies
    assert "fallback to B" in step.strategies


def test_parse_step_duplicate_description_raises():
    src = (
        "STEP foo\n"
        '  DESCRIPTION: "a"\n'
        '  DESCRIPTION: "b"\n'
        "  MODE: exact\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate DESCRIPTION" in str(exc.value)


def test_parse_step_description_rejects_non_text():
    src = "STEP foo\n  DESCRIPTION: 42\n  MODE: exact\n"
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "quoted string or `|` block scalar" in str(exc.value)


# -- TEST block (v0.15) --


def test_parse_test_minimal():
    src = (
        "STEP load\n  TAKES: f: str\n  GIVES: rows: List<int>\n  MODE: exact\n"
        "FLOW p\n  load(f=\"d\")\n"
        "TEST t1:\n"
        "  FLOW: p\n"
        "  EXPECTS:\n"
        "    rows: not_empty\n"
    )
    program = parse(src)
    test = next(d for d in program.decls if type(d).__name__ == "TestDecl")
    assert test.name == "t1"
    assert test.flow_name == "p"
    assert test.expects[0][0] == "rows"
    assert test.expects[0][1].kind == "not_empty"


def test_parse_test_all_predicate_kinds():
    src = (
        "STEP load\n  TAKES: f: str\n  GIVES: rows: List<int>\n  MODE: exact\n"
        "FLOW p\n  load(f=\"d\")\n"
        "TEST t1:\n"
        "  FLOW: p\n"
        "  WITH:\n"
        "    f: \"x\"\n"
        "  EXPECTS:\n"
        '    a: == "hi"\n'
        "    b: != 0\n"
        "    c: > 3\n"
        "    d: <= 10.5\n"
        "    e: contains 1\n"
        "    f: empty\n"
        "    g: not_empty\n"
    )
    program = parse(src)
    test = next(d for d in program.decls if type(d).__name__ == "TestDecl")
    kinds = [p.kind for _, p in test.expects]
    assert kinds == ["eq", "ne", "gt", "le", "contains", "empty", "not_empty"]


def test_parse_test_requires_at_least_one_expects():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n"
        "  FLOW: p\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "at least one EXPECTS" in str(exc.value)


def test_parse_test_missing_flow_field_raises():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n"
        "  EXPECTS:\n"
        "    x: not_empty\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "missing required FLOW" in str(exc.value)


def test_parse_test_duplicate_section_rejected():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n"
        "  FLOW: p\n"
        "  EXPECTS:\n"
        "    x: not_empty\n"
        "  EXPECTS:\n"
        "    y: not_empty\n"
    )
    with pytest.raises(ParseError) as exc:
        parse(src)
    assert "duplicate EXPECTS" in str(exc.value)


# ---------------------------------------------------------------------------
# v0.16 — FLOW.TAKES / FLOW.GIVES parser tests
# ---------------------------------------------------------------------------

def test_flow_takes_single_field_parses():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  TAKES: x: str
  s(x=x)
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert len(flow.takes) == 1
    assert flow.takes[0].name == "x"
    assert isinstance(flow.takes[0].type, PrimitiveType) and flow.takes[0].type.name == "str"
    assert flow.gives == ()


def test_flow_gives_multi_field_parses():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  GIVES: a: str, b: int
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert len(flow.gives) == 2
    assert {f.name for f in flow.gives} == {"a", "b"}
    assert flow.takes == ()


def test_flow_takes_and_gives_parse_in_either_order():
    src_a = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  GIVES: y: str
  s(x=x)
"""
    src_b = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  GIVES: y: str
  TAKES: x: str
  s(x=x)
"""
    for src in (src_a, src_b):
        program = parse(src)
        flow = next(d for d in program.decls if isinstance(d, FlowDecl))
        assert len(flow.takes) == 1 and len(flow.gives) == 1


def test_flow_duplicate_takes_rejected():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW p
  TAKES: x: str
  TAKES: y: int
  s(x=x)
"""
    with pytest.raises(ParseError, match="duplicate TAKES"):
        parse(src)


def test_flow_without_takes_gives_still_parses_backcompat():
    """v0.15 form — no FLOW signature, behaviour unchanged."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.takes == ()
    assert flow.gives == ()


# v0.17.x — FLOW.DESCRIPTION (mirror of STEP.DESCRIPTION from v0.15).
# The motivation is the claude-skill emitter: its SKILL.md frontmatter
# `description:` field is what the host LLM uses to auto-trigger the
# skill. Without an explicit FLOW description, the emitter today defaults
# to "Execute flow <name>" and prints a runtime warning that auto-trigger
# will be weak. These tests cover the parser's acceptance of the field
# (quoted string OR `|` block scalar), its placement (anywhere among
# TAKES / GIVES, before the chain), and the duplicate-field rejection.


def test_flow_description_quoted_string_parses():
    """`DESCRIPTION: "..."` on a FLOW lands on FlowDecl.description."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  DESCRIPTION: "Refine a draft into a final version."
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.description == "Refine a draft into a final version."


def test_flow_description_block_scalar_parses():
    """Multi-line `DESCRIPTION: |` block scalars are accepted, same as STEP."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  DESCRIPTION: |
    Refine a draft into a final version.
    Use this skill when the user wants polished prose.
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.description is not None
    assert flow.description.startswith("Refine a draft")
    assert "polished prose" in flow.description


def test_flow_description_with_takes_gives_in_any_order():
    """DESCRIPTION sits next to TAKES / GIVES in the FLOW header — any order."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  GIVES: y: str
  DESCRIPTION: "Polish a draft."
  TAKES: x: str
  s(x=x)
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.description == "Polish a draft."
    assert len(flow.takes) == 1 and flow.takes[0].name == "x"
    assert len(flow.gives) == 1 and flow.gives[0].name == "y"


def test_flow_duplicate_description_rejected():
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  DESCRIPTION: "First."
  DESCRIPTION: "Second."
  s(x="hi")
"""
    with pytest.raises(ParseError, match="duplicate DESCRIPTION"):
        parse(src)


def test_flow_description_absent_defaults_to_none():
    """Backcompat: a FLOW without DESCRIPTION leaves `description=None`."""
    src = """STEP s
  TAKES: x: str
  GIVES: y: str
  MODE:  exact

FLOW pipeline
  s(x="hi")
"""
    program = parse(src)
    flow = next(d for d in program.decls if isinstance(d, FlowDecl))
    assert flow.description is None
