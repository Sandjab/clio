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
    assert step.impl.retries is None


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
        "    retries: 3\n"
    )
    step = parse(src).decls[0]
    assert step.impl.method == "POST"
    assert step.impl.response_path == "items[0].id"
    assert step.impl.timeout_seconds == 30
    assert step.impl.retries == 3


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
