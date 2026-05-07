import pytest

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


def test_flow_typecheck_passes_for_compatible_chain():
    src = (
        "STEP a\n  TAKES: file: str\n  GIVES: items: List<{n: str}>\n  MODE: exact\n"
        "STEP b\n  TAKES: items: List<{n: str}>\n  GIVES: out: str\n  MODE: exact\n"
        "FLOW f\n"
        '  a(file="x.csv")\n'
        "    -> b(items)\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert [c.step_name for c in graph.flow.chain] == ["a", "b"]


def test_flow_typecheck_fails_for_incompatible_chain():
    import pytest
    src = (
        "STEP a\n  GIVES: items: List<{n: str}>\n  MODE: exact\n"
        "STEP b\n  TAKES: items: List<{n: int}>\n  GIVES: out: str\n  MODE: exact\n"
        "FLOW f\n  a()\n    -> b(items)\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    msg = str(exc.value)
    assert "type mismatch" in msg
    assert "items" in msg


def test_build_ir_carries_resources():
    src = (
        "STEP foo\n  MODE: exact\n"
        "RESOURCES\n  target: claude-cli\n  models: [haiku]\n"
    )
    graph = build_ir(parse(src))
    assert graph.resources is not None
    assert graph.resources.target == "claude-cli"
    assert graph.resources.models == ("haiku",)


def test_build_ir_carries_cache_ttl():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  CACHE: ttl(24h)\n"
    )
    graph = build_ir(parse(src))
    step = graph.steps[0]
    assert step.cache is not None
    assert step.cache.mode == "ttl"
    assert step.cache.ttl_seconds == 86400


def test_build_ir_no_cache_means_none():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
    step = build_ir(parse(src)).steps[0]
    assert step.cache is None


def test_build_ir_carries_on_fail():
    src = (
        "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
        '  ON_FAIL: retry(3) then escalate then abort("done")\n'
    )
    graph = build_ir(parse(src))
    of = graph.steps[0].on_fail
    assert of is not None
    kinds = [s.kind for s in of.strategies]
    assert kinds == ["retry", "escalate", "abort"]
    assert of.strategies[0].max_retries == 3
    assert of.strategies[2].abort_message == "done"


def test_build_ir_resolves_fallback_step():
    src = (
        "STEP main\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(naive)\n"
        "STEP naive\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
    )
    graph = build_ir(parse(src))
    main_step = next(s for s in graph.steps if s.name == "main")
    fb = main_step.on_fail.strategies[0]
    assert fb.kind == "fallback"
    assert fb.fallback_step is not None
    assert fb.fallback_step.name == "naive"


def test_build_ir_fallback_unknown_step_raises():
    src = (
        "STEP main\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(missing)\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "missing" in str(exc.value)


def test_build_ir_fallback_takes_mismatch_raises():
    src = (
        "STEP main\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(naive)\n"
        "STEP naive\n"
        "  TAKES: x: str\n"          # different type
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
    )
    import pytest as _pt
    with _pt.raises(ValueError) as exc:
        build_ir(parse(src))
    msg = str(exc.value)
    assert "incompatible" in msg.lower()
    assert "TAKES" in msg


def test_build_ir_fallback_gives_mismatch_raises():
    src = (
        "STEP main\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(naive)\n"
        "STEP naive\n"
        "  TAKES: x: int\n"
        "  GIVES: y: int\n"          # different type
        "  MODE:  exact\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    msg = str(exc.value)
    assert "incompatible" in msg.lower()
    assert "GIVES" in msg


def test_build_ir_fallback_cycle_raises():
    src = (
        "STEP a\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(b)\n"
        "STEP b\n"
        "  TAKES: x: int\n"
        "  GIVES: y: str\n"
        "  MODE:  judgment\n"
        "  ON_FAIL: fallback(a)\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "cycle" in str(exc.value).lower()


def test_build_ir_propagates_lang_field():
    src = (
        "STEP a\n  MODE: exact\n  LANG: rust\n"
        "STEP b\n  MODE: exact\n"
        "STEP c\n  GIVES: r: str\n  MODE: judgment\n"
    )
    graph = build_ir(parse(src))
    by_name = {s.name: s for s in graph.steps}
    assert by_name["a"].lang == "rust"
    assert by_name["b"].lang is None
    assert by_name["c"].lang is None


def test_build_ir_propagates_impl_rest():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://api.example.com/v1/items"\n'
        '    response_path: "items[0]"\n'
        "    timeout: 30s\n"
        "    retries: 3\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.impl is not None
    assert step.impl.__class__.__name__ == "RestImplIR"
    assert step.impl.method == "GET"
    assert step.impl.url == "https://api.example.com/v1/items"
    assert step.impl.response_path == "items[0]"
    assert step.impl.timeout_seconds == 30
    assert step.impl.retries == 3


def test_build_ir_propagates_impl_shell_shlex_split():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "pdftotext ${file} -"\n'
        "    timeout: 60s\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.impl.__class__.__name__ == "ShellImplIR"
    assert step.impl.argv == ("pdftotext", "${file}", "-")
    assert step.impl.timeout_seconds == 60


def test_build_ir_impl_shell_preserves_quoted_argv_token():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: "/bin/echo \'hello world\'"\n'
    )
    step = build_ir(parse(src)).steps[0]
    # shlex preserves the quoted argument as a single token
    assert step.impl.argv == ("/bin/echo", "hello world")


def test_build_ir_impl_shell_empty_cmd_raises():
    import pytest
    from clio.ir.builder import IRBuildError
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd: ""\n'
    )
    with pytest.raises(IRBuildError) as exc:
        build_ir(parse(src))
    assert "at least one token" in str(exc.value)


def test_build_ir_propagates_impl_code():
    src = (
        "STEP foo\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: code\n"
        "    lang: python\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.impl.__class__.__name__ == "CodeImplIR"
    assert step.impl.lang == "python"


def test_build_ir_impl_omitted_is_none():
    src = "STEP foo\n  MODE: exact\n"
    step = build_ir(parse(src)).steps[0]
    assert step.impl is None


def test_build_ir_propagates_invoke_api():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: openai\n"
        '    model: "gemini-1.5-pro"\n'
        '    base_url: "http://litellm:4000"\n'
        '    auth: "env:LITELLM_KEY"\n'
        "    max_tokens: 1024\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.invoke is not None
    assert step.invoke.__class__.__name__ == "ApiInvokeIR"
    assert step.invoke.protocol == "openai"
    assert step.invoke.model == "gemini-1.5-pro"
    assert step.invoke.base_url == "http://litellm:4000"
    assert step.invoke.auth == "env:LITELLM_KEY"
    assert step.invoke.max_tokens == 1024


def test_build_ir_propagates_invoke_cli():
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "    cli: claude\n"
        "    model: opus\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.invoke.__class__.__name__ == "CliInvokeIR"
    assert step.invoke.cli == "claude"
    assert step.invoke.model == "opus"


def test_build_ir_invoke_omitted_is_none():
    src = "STEP s\n  GIVES: r: str\n  MODE: judgment\n"
    step = build_ir(parse(src)).steps[0]
    assert step.invoke is None


def test_build_ir_for_each_basic():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    flow = build_ir(parse(src)).flow
    assert [type(c).__name__ for c in flow.chain] == ["CallIR", "ForEachIR"]
    fe = flow.chain[1]
    assert fe.loop_var == "item"
    assert fe.collection == "items"
    assert [type(b).__name__ for b in fe.body] == ["CallIR"]


def test_build_ir_for_each_unknown_collection_raises():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN nonexistent:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "nonexistent" in str(exc.value)


def test_build_ir_for_each_non_list_collection_raises():
    src = (
        "STEP load\n  GIVES: count: int\n  MODE: exact\n"
        "STEP process\n  TAKES: x: int\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN count:\n"
        "         process(x=item)\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "List" in str(exc.value)


def test_build_ir_for_each_loop_var_in_kwarg_resolution():
    """Inside the FOR EACH body, the loop_var should resolve as a state-like
    reference for kwargs that bind to it."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    flow = build_ir(parse(src)).flow
    body_call = flow.chain[1].body[0]
    assert body_call.kwargs == (("x", "@item"),)


def test_ir_propagates_parallel_for_each_fields():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         process(x=item)\n"
    )
    g = build_ir(parse(src))
    fe = next(elem for elem in g.flow.chain if elem.__class__.__name__ == "ForEachIR")
    assert fe.parallel is True
    assert fe.collector == "results"


def test_ir_sequential_for_each_defaults_unchanged():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    g = build_ir(parse(src))
    fe = next(elem for elem in g.flow.chain if elem.__class__.__name__ == "ForEachIR")
    assert fe.parallel is False
    assert fe.collector is None
