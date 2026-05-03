import pytest
from clio.parser.parser import parse, ParseError


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
    step = [d for d in program.decls if d.__class__.__name__ == "StepDecl"][0]
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
