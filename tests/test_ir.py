import pytest

from clio.ir.builder import IRBuildError, build_ir
from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    DatabaseSpecIR,
    HttpServerSpecIR,
    McpToolImplIR,
    ShellImplIR,
    SqlImplIR,
    SseServerSpecIR,
    StdioServerSpecIR,
)
from clio.parser.ast_nodes import ListType, PrimitiveType
from clio.parser.parser import parse


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


def test_dict_str_to_primitive_to_json_schema():
    from clio.parser.ast_nodes import DictType, PrimitiveType
    t = DictType(key=PrimitiveType("str"), value=PrimitiveType("int"))
    assert type_to_json_schema(t) == {
        "type": "object",
        "additionalProperties": {"type": "integer"},
    }


def test_dict_str_to_contract_ref_to_json_schema():
    src = (
        "CONTRACT r\n"
        "  SHAPE: {x: int}\n"
        "STEP s\n"
        "  GIVES: out: Dict<str, r>\n"
        "  MODE:  judgment\n"
    )
    graph = build_ir(parse(src))
    step = graph.steps[0]
    assert step.gives.type.__class__.__name__ == "DictType"


def test_build_ir_dict_unresolved_ref_raises():
    import pytest
    src = (
        "STEP s\n"
        "  GIVES: out: Dict<str, missing>\n"
        "  MODE:  judgment\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "missing" in str(exc.value)


def test_optional_primitive_to_json_schema():
    from clio.parser.ast_nodes import OptionalType, PrimitiveType
    t = OptionalType(inner=PrimitiveType("int"))
    assert type_to_json_schema(t) == {
        "anyOf": [{"type": "integer"}, {"type": "null"}],
    }


def test_optional_contract_ref_to_json_schema():
    from clio.parser.ast_nodes import ContractRef, OptionalType
    t = OptionalType(inner=ContractRef(name="r", line=1, col=1))
    assert type_to_json_schema(t) == {
        "anyOf": [
            {"$ref": "../contracts/r.schema.json"},
            {"type": "null"},
        ],
    }


def test_build_ir_optional_unresolved_ref_raises():
    import pytest
    src = (
        "STEP s\n"
        "  GIVES: out: Optional<missing>\n"
        "  MODE:  judgment\n"
    )
    with pytest.raises(ValueError) as exc:
        build_ir(parse(src))
    assert "missing" in str(exc.value)


def test_str_min_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(base=PrimitiveType("str"), constraints=(("min", 1),))
    assert type_to_json_schema(t) == {"type": "string", "minLength": 1}


def test_str_min_max_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(
        base=PrimitiveType("str"),
        constraints=(("min", 1), ("max", 200)),
    )
    assert type_to_json_schema(t) == {
        "type": "string", "minLength": 1, "maxLength": 200,
    }


def test_int_min_max_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(
        base=PrimitiveType("int"),
        constraints=(("min", 0), ("max", 120)),
    )
    assert type_to_json_schema(t) == {
        "type": "integer", "minimum": 0, "maximum": 120,
    }


def test_float_min_max_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(
        base=PrimitiveType("float"),
        constraints=(("min", 0.0), ("max", 1.0)),
    )
    assert type_to_json_schema(t) == {
        "type": "number", "minimum": 0.0, "maximum": 1.0,
    }


def test_float_precision_to_json_schema():
    from clio.parser.ast_nodes import ConstrainedType, PrimitiveType
    t = ConstrainedType(
        base=PrimitiveType("float"),
        constraints=(("precision", 2),),
    )
    # precision=2 → multipleOf 0.01 (exact 2 decimal places)
    schema = type_to_json_schema(t)
    assert schema["type"] == "number"
    assert schema["multipleOf"] == 0.01


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
        "    retry: {attempts: 3}\n"
    )
    step = build_ir(parse(src)).steps[0]
    assert step.impl is not None
    assert step.impl.__class__.__name__ == "RestImplIR"
    assert step.impl.method == "GET"
    assert step.impl.url == "https://api.example.com/v1/items"
    assert step.impl.response_path == "items[0]"
    assert step.impl.timeout_seconds == 30
    assert step.impl.retry is not None
    assert step.impl.retry.attempts == 3
    assert step.impl.retry.backoff == "exponential"


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


def test_build_ir_propagates_parse_json_to_shell_impl():
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
    step = build_ir(parse(src)).steps[0]
    assert isinstance(step.impl, ShellImplIR)
    assert step.impl.argv == ("cat", "${file}")
    assert step.impl.parse == "json"


def test_build_ir_default_parse_is_none():
    src = (
        "STEP extract_pdf\n"
        "  TAKES: file: str\n"
        "  GIVES: text: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd:  "pdftotext ${file} -"\n'
    )
    step = build_ir(parse(src)).steps[0]
    assert step.impl.parse == "none"


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


def test_ir_rejects_parallel_multi_step_body():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP a\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "STEP b\n  TAKES: y: str\n  GIVES: z: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         a(x=item)\n"
        "           -> b(y)\n"
    )
    with pytest.raises(ValueError, match="must contain exactly one step or sub-flow call"):
        build_ir(parse(src))


def test_ir_rejects_parallel_with_nested_for_each_body():
    src = (
        "STEP load\n  GIVES: matrix: List<List<str>>\n  MODE: exact\n"
        "STEP inner\n  TAKES: cell: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH row IN matrix PARALLEL AS rows:\n"
        "         FOR EACH cell IN row:\n"
        "           inner(cell=cell)\n"
    )
    with pytest.raises(ValueError, match="cannot contain nested FOR EACH"):
        build_ir(parse(src))


def test_ir_rejects_parallel_body_step_without_gives():
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP sink\n  TAKES: x: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         sink(x=item)\n"
    )
    with pytest.raises(ValueError, match="must have a GIVES"):
        build_ir(parse(src))


def test_ir_rejects_parallel_collector_shadowing_state_field():
    """The collector must not collide with a state field already populated
    upstream in the FLOW chain (the GIVES name of a prior step in this case)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS items:\n"  # 'items' shadows the upstream GIVES
        "         process(x=item)\n"
    )
    with pytest.raises(ValueError, match="shadows existing state field"):
        build_ir(parse(src))


def test_ir_rejects_nested_parallel():
    """Two nested PARALLEL blocks (transitive) are rejected in v1."""
    from clio.ir.graph import CallIR, FieldIR, FlowGraph, FlowIR, ForEachIR, StepIR
    from clio.parser.ast_nodes import PrimitiveType

    inner = ForEachIR(
        loop_var="y",
        collection="ys",
        body=(CallIR(step_name="leaf", kwargs=(("y", "@y"),), line=1),),
        line=1,
        parallel=True,
        collector="leaf_results",
    )
    outer = ForEachIR(
        loop_var="x",
        collection="xs",
        body=(inner,),  # inner is also PARALLEL — should be rejected
        line=1,
        parallel=True,
        collector="outer_results",
    )
    flow = FlowIR(name="pipe", chain=(outer,), rescues=(), line=1)
    leaf = StepIR(
        name="leaf",
        takes=(FieldIR(name="y", type=PrimitiveType("str")),),
        gives=FieldIR(name="r", type=PrimitiveType("str")),
        mode="exact",
        impl=None, invoke=None, cache=None, on_fail=None, lang=None, line=1,
    )
    graph = FlowGraph(steps=(leaf,), contracts=(), flow=flow)

    from clio.ir.builder import _validate_parallel_for_each
    with pytest.raises(ValueError, match="nested inside another PARALLEL"):
        _validate_parallel_for_each(graph)


def test_ir_accepts_parallel_inside_sequential_foreach():
    """A PARALLEL block inside a *sequential* FOR EACH is allowed (the outer
    is not parallel, so there's no nested-parallel issue)."""
    src = (
        "STEP load_outer\n  GIVES: groups: List<str>\n  MODE: exact\n"
        "STEP load_inner\n  TAKES: g: str\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load_outer()\n"
        "    -> FOR EACH g IN groups:\n"
        "         load_inner(g=g)\n"
        "           -> FOR EACH item IN items PARALLEL AS results:\n"
        "                process(x=item)\n"
    )
    # Should NOT raise — outer is sequential, inner is parallel.
    g = build_ir(parse(src))
    outer = g.flow.chain[1]
    assert outer.parallel is False
    inner = outer.body[1]
    assert inner.parallel is True


# --- impl.mode: mcp_tool IR build + cross-validation (v0.10) ----------------


_MCP_VALID_SRC = (
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
    "RESOURCES\n"
    "  target: python\n"
    "  mcp_servers:\n"
    "    docs:\n"
    "      transport: stdio\n"
    '      command:   "mcp-server-docs"\n'
)


def test_ir_build_mcp_tool_basic():
    g = build_ir(parse(_MCP_VALID_SRC))
    step = g.steps[0]
    assert isinstance(step.impl, McpToolImplIR)
    assert step.impl.server == "docs"
    assert step.impl.tool == "search"
    assert step.impl.parse == "json"
    assert g.resources is not None
    assert len(g.resources.mcp_servers) == 1
    assert isinstance(g.resources.mcp_servers[0], StdioServerSpecIR)


def test_ir_build_mcp_tool_unknown_server_rejected():
    src = _MCP_VALID_SRC.replace("server:  docs", "server:  ghost")
    with pytest.raises(IRBuildError, match="ghost.*not declared"):
        build_ir(parse(src))


def test_ir_build_mcp_tool_parse_text_with_non_str_gives_rejected():
    src = (
        "STEP search\n"
        "  TAKES: query: str\n"
        "  GIVES: r: int\n"   # non-str — incompatible with parse: text
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    mcp_tool\n"
        "    server:  docs\n"
        "    tool:    search\n"
        "    parse:   text\n"
        "FLOW f\n"
        '  search(query="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  mcp_servers:\n"
        "    docs:\n"
        '      command: "x"\n'
    )
    with pytest.raises(IRBuildError, match="parse: text requires GIVES of type 'str'"):
        build_ir(parse(src))


def test_ir_build_mcp_tool_dead_server_warns(capsys):
    src = (
        _MCP_VALID_SRC
        + "    unused:\n"
        + "      transport: sse\n"
        + '      url:       "https://example.com/mcp"\n'
    )
    g = build_ir(parse(src))
    captured = capsys.readouterr()
    assert "unused is declared but never referenced" in captured.err
    # Build still succeeds — the server spec is in the IR even if dead.
    assert {s.name for s in g.resources.mcp_servers} == {"docs", "unused"}


def test_ir_build_mcp_tool_sse_and_http_specs():
    src = (
        _MCP_VALID_SRC
        + "    remote_sse:\n"
        + "      transport: sse\n"
        + '      url:       "https://api.example.com/sse"\n'
        + "    remote_http:\n"
        + "      transport: http\n"
        + '      url:       "https://api.example.com/http"\n'
    )
    # Must not warn-as-error; we capture stderr but build_ir succeeds.
    g = build_ir(parse(src))
    by_name = {s.name: s for s in g.resources.mcp_servers}
    assert isinstance(by_name["remote_sse"], SseServerSpecIR)
    assert isinstance(by_name["remote_http"], HttpServerSpecIR)


# --- impl.mode: sql IR build + cross-validation (v0.11) ---------------------


_SQL_VALID_SRC = (
    "STEP get_orders\n"
    "  TAKES: email: str\n"
    "  GIVES: orders: List<{id: int, status: str}>\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:  sql\n"
    "    db:    crm\n"
    '    query: "SELECT id, status FROM orders WHERE email = :email"\n'
    "FLOW f\n"
    '  get_orders(email="x@y")\n'
    "RESOURCES\n"
    "  target: python\n"
    "  databases:\n"
    "    crm:\n"
    "      driver: sqlite\n"
    '      url:    ":memory:"\n'
)


def test_ir_build_sql_basic():
    g = build_ir(parse(_SQL_VALID_SRC))
    step = g.steps[0]
    assert isinstance(step.impl, SqlImplIR)
    assert step.impl.db == "crm"
    assert ":email" in step.impl.query
    assert g.resources is not None
    assert len(g.resources.databases) == 1
    db = g.resources.databases[0]
    assert isinstance(db, DatabaseSpecIR)
    assert db.driver == "sqlite"


def test_ir_build_sql_unknown_db_rejected():
    src = _SQL_VALID_SRC.replace("db:    crm", "db:    ghost")
    with pytest.raises(IRBuildError, match="ghost.*not declared"):
        build_ir(parse(src))


def test_ir_build_sql_missing_gives_rejected():
    src = (
        "STEP touch\n"
        "  TAKES: email: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:  sql\n"
        "    db:    crm\n"
        '    query: "INSERT INTO log (email) VALUES (:email)"\n'
        "FLOW f\n"
        '  touch(email="x")\n'
        "RESOURCES\n"
        "  target: python\n"
        "  databases:\n"
        "    crm:\n"
        "      driver: sqlite\n"
        '      url:    ":memory:"\n'
    )
    with pytest.raises(IRBuildError, match="impl.sql requires a GIVES"):
        build_ir(parse(src))


def test_ir_build_sql_dead_db_warns(capsys):
    src = (
        _SQL_VALID_SRC
        + "    unused:\n"
        + "      driver: postgres\n"
        + '      url:    "env:UNUSED_PG_URL"\n'
    )
    g = build_ir(parse(src))
    captured = capsys.readouterr()
    assert "unused is declared but never referenced" in captured.err
    assert {d.name for d in g.resources.databases} == {"crm", "unused"}


def test_ir_build_sql_record_gives_shape_accepted():
    """A single-record GIVES is also valid (one row expected at runtime)."""
    src = _SQL_VALID_SRC.replace(
        "  GIVES: orders: List<{id: int, status: str}>",
        "  GIVES: order: {id: int, status: str}",
    )
    g = build_ir(parse(src))
    step = g.steps[0]
    assert isinstance(step.impl, SqlImplIR)


def test_ir_build_sql_primitive_gives_shape_accepted():
    src = _SQL_VALID_SRC.replace(
        "  GIVES: orders: List<{id: int, status: str}>",
        "  GIVES: count: int",
    ).replace(
        '"SELECT id, status FROM orders WHERE email = :email"',
        '"SELECT COUNT(*) FROM orders WHERE email = :email"',
    )
    g = build_ir(parse(src))
    step = g.steps[0]
    assert isinstance(step.impl, SqlImplIR)


# -- multi-FLOW (v0.15) --


def test_build_ir_multiple_flows_without_selector_leaves_main_none():
    """v0.17: multi-FLOW sources no longer require --flow at build_ir.
    `flow` (the main) stays None; per-target emitters that need a main
    (python, langgraph, claude-skill, claude-cli) raise in their own
    emit pass. `flows` and `exposed_flow_names` are still populated so
    targets like mcp-server can emit every FLOW as a tool."""
    src = (
        "STEP foo\n"
        "  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    g = build_ir(parse(src))
    assert g.flow is None
    assert {f.name for f in g.flows} == {"alpha", "beta"}


def test_build_ir_multi_flow_select_by_name():
    src = (
        "STEP foo\n"
        "  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW beta\n  foo(x=\"b\")\n"
    )
    g = build_ir(parse(src), flow_name="beta")
    assert g.flow is not None
    assert g.flow.name == "beta"


def test_build_ir_unknown_flow_name_raises():
    src = (
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
    )
    with pytest.raises(IRBuildError) as exc:
        build_ir(parse(src), flow_name="missing")
    assert "not found" in str(exc.value)
    assert "alpha" in str(exc.value)


def test_build_ir_duplicate_flow_name_rejected():
    src = (
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
        "FLOW alpha\n  foo(x=\"b\")\n"
    )
    with pytest.raises(IRBuildError) as exc:
        build_ir(parse(src))
    assert "duplicate FLOW name" in str(exc.value)


def test_build_ir_single_flow_ignores_flow_name_when_matching():
    src = (
        "STEP foo\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n"
        "FLOW alpha\n  foo(x=\"a\")\n"
    )
    # Backwards compat: a flow_name that matches the single flow is fine.
    g = build_ir(parse(src), flow_name="alpha")
    assert g.flow.name == "alpha"


# -- TEST block (v0.15) --


def test_ir_test_references_unknown_flow_raises():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n  FLOW: zzz\n  EXPECTS:\n    rows: not_empty\n"
    )
    with pytest.raises(IRBuildError) as exc:
        build_ir(parse(src))
    assert "unknown" in str(exc.value)
    assert "zzz" in str(exc.value)


def test_ir_duplicate_test_name_rejected():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n  FLOW: p\n  EXPECTS:\n    a: not_empty\n"
        "TEST t1:\n  FLOW: p\n  EXPECTS:\n    b: not_empty\n"
    )
    with pytest.raises(IRBuildError) as exc:
        build_ir(parse(src))
    assert "duplicate TEST" in str(exc.value)


def test_ir_test_appears_in_graph_tests_tuple():
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
        "TEST t1:\n  FLOW: p\n  EXPECTS:\n    rows: not_empty\n"
    )
    g = build_ir(parse(src))
    assert len(g.tests) == 1
    assert g.tests[0].name == "t1"
    assert g.tests[0].flow_name == "p"


# --- Issue #19: first-step identifier kwargs as FLOW inputs ----------------

def test_first_step_identifier_kwarg_auto_promoted_to_flow_input():
    """Issue #19: the FIRST step's identifier kwargs that don't match an
    upstream produced field are auto-promoted to FLOW-level inputs (passed
    via run(**initial) at runtime). Without this, `load_article(file=file)`
    crashes with 'state reference not produced by any previous step'."""
    src = (
        "STEP load_article\n"
        "  TAKES: file: str\n"
        "  GIVES: article: str\n"
        "  MODE:  exact\n"
        "FLOW p\n"
        "  load_article(file=file)\n"
    )
    # Must build without raising — the `file` identifier on the first step
    # is recognized as an external FLOW input.
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert graph.flow.name == "p"


def test_first_step_identifier_kwarg_threaded_to_downstream_step():
    """Issue #19: a downstream step can reference the same name and the
    builder still type-checks it correctly."""
    src = (
        "STEP load_article\n"
        "  TAKES: file: str\n"
        "  GIVES: article: str\n"
        "  MODE:  exact\n"
        "STEP echo_path\n"
        "  TAKES: file: str\n"
        "  GIVES: copy: str\n"
        "  MODE:  exact\n"
        "FLOW p\n"
        "  load_article(file=file)\n"
        "    -> echo_path(file=file)\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None


def test_non_first_step_unknown_identifier_still_rejected():
    """Issue #19 regression guard: only the first step gets auto-promotion.
    A later step referencing an undefined identifier must still raise."""
    src = (
        "STEP load\n"
        "  GIVES: data: str\n"
        "  MODE:  exact\n"
        "STEP next\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "FLOW p\n"
        "  load()\n"
        "    -> next(x=undefined_name)\n"
    )
    with pytest.raises(IRBuildError, match="not produced by any previous step"):
        build_ir(parse(src))


def test_test_with_kwargs_match_first_step_takes():
    """Issue #19: TEST WITH-kwargs become run(**initial) values at runtime;
    they must therefore match a FLOW input — i.e. an identifier kwarg on
    the first step. This test compiles and runs through to graph.tests."""
    src = (
        "STEP load_article\n"
        "  TAKES: file: str\n"
        "  GIVES: article: str\n"
        "  MODE:  exact\n"
        "FLOW p\n"
        "  load_article(file=file)\n"
        "TEST t:\n"
        '  FLOW: p\n'
        "  WITH:\n"
        '    file: "data/article.txt"\n'
        "  EXPECTS:\n"
        "    article: not_empty\n"
    )
    graph = build_ir(parse(src))
    assert len(graph.tests) == 1
    assert graph.tests[0].with_kwargs == (("file", "data/article.txt"),)


# ---------------------------------------------------------------------------
# v0.16 — TEST WITH / EXPECTS type-checked against declared FLOW signature
# ---------------------------------------------------------------------------

def test_test_with_unknown_kwarg_against_declared_takes_rejected():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "TEST t:\n"
        "  FLOW: p\n"
        "  WITH:\n"
        '    not_there: "oops"\n'
        "  EXPECTS:\n"
        "    y: not_empty\n"
    )
    with pytest.raises(IRBuildError, match="not_there"):
        build_ir(parse(src))


def test_test_expects_unknown_root_against_declared_gives_rejected():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
        "\n"
        "TEST t:\n"
        "  FLOW: p\n"
        "  WITH:\n"
        '    x: "hi"\n'
        "  EXPECTS:\n"
        "    not_there: not_empty\n"
    )
    with pytest.raises(IRBuildError, match="not_there"):
        build_ir(parse(src))


def test_test_without_flow_signature_skips_type_check_v0_15_backcompat():
    """v0.15 behaviour preserved when FLOW does not declare TAKES/GIVES."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  s(x=x)\n"
        "\n"
        "TEST t:\n"
        "  FLOW: p\n"
        "  WITH:\n"
        '    anything: "goes"\n'
        "  EXPECTS:\n"
        "    y: not_empty\n"
    )
    build_ir(parse(src))    # no exception expected


# ---------------------------------------------------------------------------
# v0.16 — FLOW.TAKES seeded into chain scope (closes #21)
# ---------------------------------------------------------------------------

def test_flow_with_declared_takes_compiles_when_chain_starts_with_for_each():
    """Closes #21 — top-level FOR EACH over an external input now compiles
    when FLOW.TAKES declares the input."""
    src = (
        "STEP classify\n"
        "  TAKES: item:  str\n"
        "  GIVES: label: str\n"
        "  MODE:  judgment\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: items: List<str>\n"
        "  FOR EACH item IN items PARALLEL AS labels:\n"
        "    classify(item=item)\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert len(graph.flow.takes) == 1
    assert graph.flow.takes[0].name == "items"
    assert isinstance(graph.flow.takes[0].type, ListType)
    assert isinstance(graph.flow.takes[0].type.inner, PrimitiveType)
    assert graph.flow.takes[0].type.inner.name == "str"


def test_flow_with_declared_takes_disables_autopromote():
    """When FLOW.TAKES is declared, a first-step identifier kwarg whose
    referent is not in TAKES must be rejected — no implicit auto-promote."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: a: str\n"
        "  s(x=x)\n"
    )
    with pytest.raises(IRBuildError, match="state reference 'x' not produced"):
        build_ir(parse(src))


def test_flow_without_takes_keeps_autopromote_v0_15_behaviour():
    """Backward-compat: no FLOW.TAKES → the first-step StepCall auto-promote
    from PR #20 still fires, so this compiles (x is promoted)."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  s(x=x)\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert graph.flow.takes == ()    # nothing declared


def test_flow_with_declared_takes_rejects_top_level_for_each_over_undeclared():
    """Top-level FOR EACH over an identifier that is not in FLOW.TAKES is
    still rejected — the #21 error pattern stays for genuinely undefined names."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: a: List<str>\n"
        "  FOR EACH x IN items:\n"
        "    s(x=x)\n"
    )
    with pytest.raises(IRBuildError, match="FOR EACH iterates over 'items'"):
        build_ir(parse(src))


def test_flow_duplicate_takes_field_rejected_at_ir_build():
    """The parser does not deduplicate fields inside a single TAKES line
    (parse_field_list accepts a, b, a). The IR builder rejects."""
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW pipeline\n"
        "  TAKES: a: str, b: int, a: float\n"
        "  s(x=\"hi\")\n"
    )
    with pytest.raises(IRBuildError, match="duplicate TAKES field"):
        build_ir(parse(src))


def test_flow_gives_coverage_compiles_when_field_matches_last_step():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  s(x=x)\n"
    )
    graph = build_ir(parse(src))
    assert graph.flow is not None
    assert len(graph.flow.gives) == 1
    assert graph.flow.gives[0].name == "y"


def test_flow_gives_rejects_missing_field():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: not_there: str\n"
        "  s(x=x)\n"
    )
    with pytest.raises(IRBuildError, match="GIVES field 'not_there' but no step in the chain produces it"):
        build_ir(parse(src))


def test_flow_gives_rejects_type_mismatch():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: y: int\n"
        "  s(x=x)\n"
    )
    with pytest.raises(IRBuildError, match="GIVES field 'y'.*but the chain produces"):
        build_ir(parse(src))


def test_flow_gives_allows_subset_coverage():
    """The chain can produce more fields than FLOW.GIVES declares —
    only the declared subset is exposed externally."""
    src = (
        "STEP s1\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "STEP s2\n"
        "  TAKES: y: str\n"
        "  GIVES: z: int\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  TAKES: x: str\n"
        "  GIVES: z: int\n"
        "  s1(x=x) -> s2(y=y)\n"
    )
    graph = build_ir(parse(src))
    assert {f.name for f in graph.flow.gives} == {"z"}


def test_flow_duplicate_gives_field_rejected_at_ir_build():
    src = (
        "STEP s\n"
        "  TAKES: x: str\n"
        "  GIVES: y: str\n"
        "  MODE:  exact\n"
        "\n"
        "FLOW p\n"
        "  GIVES: a: str, b: int, a: float\n"
        "  s(x=\"hi\")\n"
    )
    with pytest.raises(IRBuildError, match="duplicate GIVES field"):
        build_ir(parse(src))


def test_flowcallir_is_distinct_from_callir():
    from clio.ir.graph import CallIR, FlowCallIR
    fc = FlowCallIR(flow_name="enrich", kwargs=(("a", "@art"),), line=10)
    sc = CallIR(step_name="enrich", kwargs=(("a", "@art"),), line=10)
    assert type(fc) is not type(sc)
    assert fc.flow_name == "enrich"
    assert fc.kwargs == (("a", "@art"),)
    assert fc.line == 10


def test_extract_flow_signatures_skips_unsigned_flows():
    from clio.ir.builder import _extract_flow_signatures
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW with_sig\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW no_sig\n  s(x=\"hi\")\n"
    )
    prog = parse(src)
    flow_decls = [d for d in prog.decls if type(d).__name__ == "FlowDecl"]
    sigs = _extract_flow_signatures(flow_decls)
    assert "with_sig" in sigs
    assert "no_sig" not in sigs
    sig = sigs["with_sig"]
    assert [f.name for f in sig.takes] == ["x"]
    assert [f.name for f in sig.gives] == ["y"]


def test_step_flow_name_collision_rejected():
    from clio.ir.builder import IRBuildError, build_ir
    from clio.parser.parser import parse
    src = (
        "STEP enrich\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW enrich\n  TAKES: x: str\n  GIVES: y: str\n  enrich(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src))
    msg = str(ei.value)
    assert "collision" in msg.lower() or "shadow" in msg.lower()
    assert "enrich" in msg


def test_subflow_call_returns_flowcallir():
    from clio.ir.builder import build_ir
    from clio.ir.graph import FlowCallIR
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW inner\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n"
    )
    g = build_ir(parse(src), flow_name="outer")
    outer = g.flow
    assert outer is not None
    item = outer.chain[0]
    assert isinstance(item, FlowCallIR)
    assert item.flow_name == "inner"


def test_call_to_unsigned_flow_rejected():
    from clio.ir.builder import IRBuildError, build_ir
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW inner\n  s(x=\"hi\")\n\n"
        "FLOW outer\n  TAKES: x: str\n  GIVES: y: str\n  inner(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="outer")
    msg = str(ei.value)
    assert "inner" in msg
    assert "signature" in msg.lower() or "TAKES" in msg


def test_subflow_self_recursion_rejected():
    from clio.ir.builder import IRBuildError, build_ir
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="a")
    assert "recursion" in str(ei.value).lower() or "cycle" in str(ei.value).lower()


def test_subflow_mutual_recursion_rejected():
    from clio.ir.builder import IRBuildError, build_ir
    from clio.parser.parser import parse
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  b(x=x)\n\n"
        "FLOW b\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    import pytest as _pt
    with _pt.raises(IRBuildError) as ei:
        build_ir(parse(src), flow_name="a")
    assert "cycle" in str(ei.value).lower()


def test_graph_exposes_all_flows():
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse
    # v0.18: exposure is now an explicit marker (EXPOSE FLOW), no longer
    # derived by the sibling-call heuristic. `a` is called by `b`; only
    # `b` is marked EXPOSE here, so only `b` is in exposed_flow_names.
    src = (
        "STEP s\n  TAKES: x: str\n  GIVES: y: str\n  MODE: exact\n\n"
        "FLOW a\n  TAKES: x: str\n  GIVES: y: str\n  s(x=x)\n\n"
        "EXPOSE FLOW b\n  TAKES: x: str\n  GIVES: y: str\n  a(x=x)\n"
    )
    g = build_ir(parse(src), flow_name="b")
    assert g.flow is not None and g.flow.name == "b"
    names = {f.name for f in g.flows}
    assert names == {"a", "b"}
    # `a` is not marked EXPOSE, so it is NOT in exposed_flow_names.
    assert "a" not in g.exposed_flow_names
    assert "b" in g.exposed_flow_names
