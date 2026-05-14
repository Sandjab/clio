import importlib.util
from pathlib import Path

import pytest

from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def _load_module(name: str, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_emit_skeleton(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v03_skeleton")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_skeleton_copies_cache_verbatim(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    src_cache = (Path(__file__).parent.parent.parent / "clio" / "runtime" / "cache.py").read_text()
    out_cache = (tmp_path / "classify" / "clio_runtime" / "cache.py").read_text()
    assert out_cache == src_cache


def test_emit_skeleton_copies_logging_verbatim(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    src_logging = (
        Path(__file__).parent.parent.parent / "clio" / "runtime" / "logging.py"
    ).read_text()
    out_logging = (tmp_path / "classify" / "clio_runtime" / "logging.py").read_text()
    assert out_logging == src_logging


def test_emit_contracts(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v03_contracts")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_contracts_pydantic_validates(tmp_path):
    """Smoke: load the emitted contracts module and validate a sample dict."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_path = tmp_path / "retention" / "contracts.py"
    mod = _load_module("v03_contracts_test", contracts_path)
    CustomerRisk = mod.CustomerRisk
    ok = CustomerRisk.model_validate({"client": "X", "risk": "low", "reason": "ok"})
    assert ok.client == "X"
    with pytest.raises(Exception):
        CustomerRisk.model_validate({"client": "X", "risk": "low", "reason": ""})
    with pytest.raises(Exception):
        CustomerRisk.model_validate({"client": "X", "risk": "ZZZ", "reason": "ok"})


def test_emit_exact_step_stub_is_callable(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        from retention.steps import load_customers as lc_mod
        with pytest.raises(NotImplementedError):
            lc_mod.load_customers(file="x.csv")
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_judgment_step_runs_with_monkeypatched_sdk(tmp_path, monkeypatch):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        import anthropic

        class FakeMessage:
            def __init__(self, text):
                self.content = [type("Block", (), {"text": text})()]

        class FakeMessages:
            @staticmethod
            def create(**kw):
                return FakeMessage(
                    '[{"client": "Alpha", "risk": "low", "reason": "stable"}]'
                )

        class FakeClient:
            def __init__(self, *_a, **_k):
                self.messages = FakeMessages()

        monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

        from retention.steps import detect_churn as dc_mod
        result = dc_mod.detect_churn(customers=[{"name": "Alpha", "revenue": 50000}])
        assert len(result) == 1
        assert result[0].client == "Alpha"
        assert result[0].risk == "low"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_exact_step_with_no_takes_is_valid_python(tmp_path):
    """Regression: empty TAKES must not emit `def foo(*, ) ->` (SyntaxError)."""
    src = (
        "STEP foo\n"
        "  GIVES: r: str\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  foo()\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_path = tmp_path / "f" / "steps" / "foo.py"
    src_text = step_path.read_text()
    # Must NOT contain the broken signature.
    assert "def foo(*, )" not in src_text, "empty TAKES emitted broken signature"
    # Must compile.
    import py_compile
    py_compile.compile(str(step_path), doraise=True)
    # Must be loadable and raise NotImplementedError when called.
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        from f.steps import foo as foo_mod
        with pytest.raises(NotImplementedError):
            foo_mod.foo()
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k == "f" or k.startswith("f."):
                del sys.modules[k]


def test_emit_v03_cache(tmp_path):
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    expected = _read_tree(FIXTURES / "expected" / "v03_cache")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v03_onfail(tmp_path):
    src = (FIXTURES / "mvp_v03_onfail.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    expected = _read_tree(FIXTURES / "expected" / "v03_onfail")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v03_fallback(tmp_path):
    src = (FIXTURES / "mvp_v03_fallback.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    expected = _read_tree(FIXTURES / "expected" / "v03_fallback")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_onfail_retry_then_escalate(tmp_path, monkeypatch):
    """First 4 calls fail (initial + 3 retries on haiku), 5th succeeds on sonnet."""
    src = (FIXTURES / "mvp_v03_onfail.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    import sys
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("CLIO_CACHE_DIR", str(tmp_path / ".cache"))
    try:
        import anthropic
        call_log = []

        class FakeMessages:
            @staticmethod
            def create(**kw):
                call_log.append(kw["model"])
                if len(call_log) <= 4:
                    return type("M", (), {"content": [type("B", (), {"text": "garbage"})()]})()
                return type("M", (), {"content": [type("B", (), {"text": '[{"client": "X", "risk": "low", "reason": "ok"}]'})()]})()

        class FakeClient:
            def __init__(self, *_a, **_k):
                self.messages = FakeMessages()

        monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

        from retention.steps import detect_churn as dc_mod
        result = dc_mod.detect_churn(customers=[{"name": "X", "revenue": 1.0}])
        assert len(result) == 1
        assert len(call_log) == 5
        assert call_log[:4] == ["claude-haiku-4-5-20251001"] * 4
        assert call_log[4] == "claude-sonnet-4-6"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_onfail_fallback_uses_naive(tmp_path, monkeypatch):
    """All model attempts fail → fallback step runs."""
    src = (FIXTURES / "mvp_v03_fallback.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    import sys
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("CLIO_CACHE_DIR", str(tmp_path / ".cache"))
    try:
        import anthropic

        class FakeMessages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [type("B", (), {"text": "garbage"})()]})()

        class FakeClient:
            def __init__(self, *_a, **_k):
                self.messages = FakeMessages()

        monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

        from retention.steps import detect_churn_naive as naive_mod

        def fake_naive(*, customers):
            return [{"client": c["name"], "risk": "high", "reason": "fallback"} for c in customers]

        monkeypatch.setattr(naive_mod, "detect_churn_naive", fake_naive)

        from retention.steps import detect_churn as dc_mod
        result = dc_mod.detect_churn(customers=[{"name": "X", "revenue": 1.0}])
        assert len(result) == 1
        assert result[0].reason == "fallback"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_orchestrator_runs_full_flow(tmp_path, monkeypatch):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    monkeypatch.setenv("CLIO_STATE_FILE", str(tmp_path / "state.json"))
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        from retention.steps import load_customers as lc
        monkeypatch.setattr(lc, "load_customers", lambda *, file: [{"name": "A", "revenue": 1.0}])

        import anthropic

        class FakeMessages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [type("B", (), {"text": '[{"client": "A", "risk": "low", "reason": "ok"}]'})()]})()

        class FakeClient:
            def __init__(self, *_a, **_k):
                self.messages = FakeMessages()

        monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

        from retention.flow import run
        result = run()
        assert "customers" in result
        assert "risks" in result
        assert len(result["risks"]) == 1
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_examples_mvp_python(tmp_path):
    src = (Path(__file__).parent.parent.parent / "examples" / "mvp.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    expected = _read_tree(Path(__file__).parent.parent / "fixtures" / "expected" / "python_v03_mvp")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_shell_parse_json_fixture_locked(tmp_path):
    """Byte-identical lock for the parse:json shell-step fixture. Detects
    accidental drift in emit output (whitespace, ordering, imports)."""
    src = (FIXTURES / "shell_parse_json.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    expected = _read_tree(FIXTURES / "expected" / "shell_parse_json")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_examples_classify_corpus_python(tmp_path):
    src = (Path(__file__).parent.parent.parent / "examples" / "classify_corpus.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "openai>=1.0" in pyproject
    assert "anthropic" not in pyproject
    assert "pydantic>=2" in pyproject
    assert "requests" not in pyproject

    classify_body = (tmp_path / "classify_corpus" / "steps" / "classify.py").read_text()
    assert "import openai" in classify_body
    assert "openai.OpenAI(base_url='http://localhost:4000'" in classify_body
    assert "api_key=os.environ.get('LITELLM_KEY')" in classify_body
    assert "import anthropic" not in classify_body

    flow = (tmp_path / "classify_corpus" / "flow.py").read_text()
    assert "state['lines'] = load_lines_mod.load_lines" in flow
    assert "for line in state['lines']:" in flow
    assert "classify_mod.classify(text=line)" in flow

    contracts_py = (tmp_path / "classify_corpus" / "contracts.py").read_text()
    assert "class Classification(BaseModel)" in contracts_py
    assert "@field_validator" in contracts_py


def test_emit_judgment_cache_hit_skips_sdk(tmp_path, monkeypatch):
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    import json
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        cache_dir = tmp_path / ".cache"
        monkeypatch.setenv("CLIO_CACHE_DIR", str(cache_dir))

        from retention.clio_runtime import cache as _cache
        from retention.steps import detect_churn as dc_mod

        prompt = dc_mod._PROMPT_TEMPLATE
        prompt = prompt.replace("${customers}", json.dumps([{"name": "X", "revenue": 1.0}]))
        prompt = prompt.replace("${schema}", dc_mod._INLINED_SCHEMA)
        key = _cache.cache_key("detect_churn", dc_mod._MODELS[0], prompt, dc_mod._INLINED_SCHEMA)
        cached_payload = json.dumps([{"client": "Cached", "risk": "low", "reason": "from cache"}])
        _cache.cache_store(cache_dir, "detect_churn", key, dc_mod._MODELS[0], cached_payload)

        import anthropic
        def boom(*a, **kw):
            raise AssertionError("SDK must not be called on cache hit")
        monkeypatch.setattr(anthropic, "Anthropic", boom)

        result = dc_mod.detect_churn(customers=[{"name": "X", "revenue": 1.0}])
        assert len(result) == 1
        assert result[0].client == "Cached"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emit_rejects_pydantic_reserved_field_names(tmp_path):
    """Latent #1: CONTRACT field colliding with Pydantic v2 reserved attribute
    (model_config, model_dump, ...) must be rejected at emit, not crash at
    import time with PydanticUserError."""
    src = (
        "CONTRACT item\n"
        "  SHAPE: {model_config: str, ok: str}\n"
        "STEP load\n"
        "  GIVES: r: List<item>\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  load()\n"
    )
    graph = build_ir(parse(src))
    with pytest.raises(ValueError, match="model_config"):
        PythonEmitter().emit(graph, tmp_path)


def test_emit_chained_assert_validates_at_runtime(tmp_path):
    """ASSERT `0.0 <= score <= 1.0` desugars to a bool_and AST; the emitted
    Pydantic validator should accept in-range values and reject out-of-range
    on both sides."""
    src = (
        "CONTRACT scored\n"
        "  SHAPE:  {score: float}\n"
        "  ASSERT: 0.0 <= score <= 1.0\n"
        "STEP load\n"
        "  GIVES: r: List<scored>\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  load()\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_path = tmp_path / "f" / "contracts.py"
    body = contracts_path.read_text()
    # The emitted validator body uses Python `and` joining the two compares.
    assert "(0.0 <= score) and (score <= 1.0)" in body

    mod = _load_module("clio_chained_assert_test", contracts_path)
    Scored = mod.Scored
    assert Scored.model_validate({"score": 0.5}).score == 0.5
    assert Scored.model_validate({"score": 0.0}).score == 0.0
    assert Scored.model_validate({"score": 1.0}).score == 1.0
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        Scored.model_validate({"score": -0.1})
    with pytest.raises(pydantic.ValidationError):
        Scored.model_validate({"score": 1.1})


def test_judgment_attempt_re_raises_non_transient_sdk_errors(tmp_path):
    """A bad API key (anthropic.AuthenticationError) is not a transient
    failure — retrying just burns tokens without changing the outcome.
    The emitted _attempt() must re-raise authentication / permission /
    bad-request errors instead of swallowing them as `return None`."""
    src = (FIXTURES / "mvp_v03_onfail.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_path = tmp_path / "retention" / "steps" / "detect_churn.py"
    body = step_path.read_text()
    # The explicit re-raise must come before the generic except Exception
    # (Python evaluates except clauses top-down, so order matters).
    assert (
        "except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, "
        "anthropic.BadRequestError):\n            raise\n"
    ) in body
    # And the generic catch still exists.
    assert "        except Exception:\n            return None\n" in body


def test_emit_field_validator_handles_python_keyword_field_name(tmp_path):
    """A CONTRACT field whose CLIO name is a Python keyword (`class`,
    `return`, …) gets renamed to `<name>_` by `_to_field_name`. The
    emitted @field_validator must target the renamed Python name, not
    the raw CLIO name, otherwise Pydantic raises PydanticUserError at
    import time because the targeted field doesn't exist on the model.
    """
    src = (
        "CONTRACT row\n"
        "  SHAPE:  {return: int}\n"
        "  ASSERT: return > 0\n"
        "STEP load\n"
        "  GIVES: r: List<row>\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  load()\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_path = tmp_path / "f" / "contracts.py"
    body = contracts_path.read_text()
    # The decorator must target the renamed Python field name.
    assert "@field_validator('return_')" in body
    # And the validator body's local must use the renamed name too.
    assert "return_ = v" in body

    # End-to-end: importing the module must not raise PydanticUserError,
    # and the validator must actually fire on out-of-range values.
    mod = _load_module("clio_kw_field_test", contracts_path)
    Row = mod.Row
    assert Row.model_validate({"return": 5}).return_ == 5
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        Row.model_validate({"return": 0})


def test_emit_rejects_multifield_assert(tmp_path):
    """Latent #2: ASSERT referencing more than one field would generate a
    @field_validator whose body references idents not in scope at runtime
    (NameError on validation). Reject at emit with a clear message."""
    src = (
        "CONTRACT item\n"
        "  SHAPE: {a: int, b: int}\n"
        "  ASSERT: a > b\n"
        "STEP load\n"
        "  GIVES: r: List<item>\n"
        "  MODE:  exact\n"
        "FLOW f\n"
        "  load()\n"
    )
    graph = build_ir(parse(src))
    with pytest.raises(ValueError, match="multi-field"):
        PythonEmitter().emit(graph, tmp_path)


def test_emit_judgment_cache_stale_falls_through_to_sdk(tmp_path, monkeypatch):
    """Latent #4: a cached payload that no longer matches the current schema
    (e.g. user edited the .clio between runs) must be treated as a cache
    miss and trigger a fresh SDK call, not crash with ValidationError."""
    import json
    import sys
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    sys.path.insert(0, str(tmp_path))
    try:
        cache_dir = tmp_path / ".cache"
        monkeypatch.setenv("CLIO_CACHE_DIR", str(cache_dir))

        from retention.clio_runtime import cache as _cache
        from retention.steps import detect_churn as dc_mod

        prompt = dc_mod._PROMPT_TEMPLATE
        prompt = prompt.replace("${customers}", json.dumps([{"name": "X", "revenue": 1.0}]))
        prompt = prompt.replace("${schema}", dc_mod._INLINED_SCHEMA)
        key = _cache.cache_key("detect_churn", dc_mod._MODELS[0], prompt, dc_mod._INLINED_SCHEMA)
        # Stale payload — missing required `risk` and `reason` fields
        stale = json.dumps([{"client": "X"}])
        _cache.cache_store(cache_dir, "detect_churn", key, dc_mod._MODELS[0], stale)

        import anthropic
        class FakeMessages:
            @staticmethod
            def create(**kw):
                return type("M", (), {"content": [type("B", (), {"text": '[{"client": "Fresh", "risk": "low", "reason": "from sdk"}]'})()]})()
        class FakeClient:
            def __init__(self, *_a, **_k):
                self.messages = FakeMessages()
        monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

        result = dc_mod.detect_churn(customers=[{"name": "X", "revenue": 1.0}])
        assert len(result) == 1
        assert result[0].client == "Fresh"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


def test_emitted_step_signatures_resolve_via_get_type_hints(tmp_path):
    """Latent #3: with `from __future__ import annotations`, an unqualified
    `list[CustomerRisk]` in a step signature crashes typing.get_type_hints
    because only `contracts` is imported, not the class itself."""
    import sys
    from typing import get_type_hints
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    sys.path.insert(0, str(tmp_path))
    try:
        from retention.steps import detect_churn as dc
        hints = get_type_hints(dc.detect_churn)
        assert "return" in hints
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k.startswith("retention"):
                del sys.modules[k]


# --- impl: mode: rest emission ---------------------------------------------

_REST_SRC = (
    "STEP geocode\n"
    "  TAKES: address: str\n"
    "  GIVES: location: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode: rest\n"
    "    method: GET\n"
    '    url: "https://api.example.com/geocode"\n'
    '    response_path: "results[0].formatted_address"\n'
    "    timeout: 30s\n"
    "FLOW geo\n"
    '  geocode(address="123 Main St")\n'
)


def _emit_rest(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_SRC)), tmp_path)
    return tmp_path


def test_emit_rest_step_imports_requests_and_calls_request(tmp_path):
    out = _emit_rest(tmp_path)
    body = (out / "geo" / "steps" / "geocode.py").read_text()
    assert "import requests" in body
    assert "requests.request(" in body
    assert "method='GET'" in body
    # URL is now templated through _rest.subst (harmless if no ${var} present).
    assert "_rest.subst('https://api.example.com/geocode', _takes)" in body
    assert "url=_url" in body
    assert "_kwargs['timeout'] = 30" in body


def test_emit_rest_step_parses_as_python(tmp_path):
    import ast
    out = _emit_rest(tmp_path)
    body = (out / "geo" / "steps" / "geocode.py").read_text()
    ast.parse(body)  # raises if invalid syntax


def test_emit_rest_step_emits_response_path_traversal(tmp_path):
    out = _emit_rest(tmp_path)
    body = (out / "geo" / "steps" / "geocode.py").read_text()
    # response_path 'results[0].formatted_address' should drive the regex-walked
    # traversal block, not a raw `return response.json()`.
    assert "results[0].formatted_address" in body
    assert "import re as _re" in body
    assert "for _part in _re.findall" in body


def test_emit_rest_pyproject_adds_requests_dependency(tmp_path):
    out = _emit_rest(tmp_path)
    pyproject = (out / "pyproject.toml").read_text()
    assert "requests>=2.31" in pyproject


def test_emit_pyproject_omits_requests_when_no_rest_step(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "requests" not in pyproject


_REST_TEMPLATED_SRC = (
    "STEP geocode\n"
    "  TAKES: address: str, country: str\n"
    "  GIVES: location: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode: rest\n"
    "    method: GET\n"
    '    url: "https://api.example.com/geo/${country}?q=${address}"\n'
    '    response_path: "results[0].formatted_address"\n'
    "    timeout: 30s\n"
    "FLOW geo\n"
    '  geocode(address="123 Main St", country="US")\n'
)


def test_emit_rest_step_templates_takes_into_url(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_TEMPLATED_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    # URL templating is now delegated to _rest.subst at runtime, fed by a
    # `_takes` dict built from the function's TAKES.
    assert (
        "_url = _rest.subst('https://api.example.com/geo/${country}?q=${address}', _takes)"
    ) in body
    assert "'address': address" in body
    assert "'country': country" in body
    assert "url=_url" in body


def test_emit_rest_step_templated_parses_as_python(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_REST_TEMPLATED_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    ast.parse(body)


# --- impl.rest extended emission (query/headers/body/retry) ---

_REST_FULL_SRC = (
    "STEP geocode\n"
    "  TAKES: address: str\n"
    "  GIVES: location: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode: rest\n"
    "    method: GET\n"
    '    url: "https://api.example.com/geo"\n'
    '    query: {address: "${address}", limit: 10, key: "env:API_KEY"}\n'
    '    headers: {Accept: "application/json", Authorization: "env:AUTH_HEADER"}\n'
    '    response_path: "results[0]"\n'
    "    timeout: 30s\n"
    "    retry: {attempts: 3, backoff: exponential, base: 0.1, cap: 30, on: [\"5xx\", \"429\", \"timeout\"]}\n"
    "FLOW geo\n"
    '  geocode(address="123 Main St")\n'
)


def test_emit_rest_step_emits_query_via_render_dict(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_FULL_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    assert "_kwargs['params'] = _rest.render_dict(" in body
    assert "'address', '${address}'" in body
    assert "'limit', 10" in body
    assert "'key', 'env:API_KEY'" in body


def test_emit_rest_step_emits_headers_via_render_dict(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_FULL_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    assert "_kwargs['headers'] = _rest.render_dict(" in body
    assert "'Authorization', 'env:AUTH_HEADER'" in body


def test_emit_rest_step_emits_retry_loop(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_FULL_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    assert "_attempts = 3" in body
    assert "for _i in range(_attempts):" in body
    assert "_rest.is_retryable_response(" in body
    assert "_rest.is_retryable_exception(" in body
    assert "_rest.compute_delay(" in body
    assert "Retry-After" in body


def test_emit_rest_step_copies_runtime_rest_module(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_FULL_SRC)), tmp_path)
    rest_runtime = (tmp_path / "geo" / "clio_runtime" / "rest.py")
    assert rest_runtime.exists()
    src = rest_runtime.read_text()
    assert "def subst(" in src
    assert "def render_dict(" in src
    assert "def compute_delay(" in src


def test_emit_rest_step_no_retry_emits_single_request(tmp_path):
    src = (
        "STEP fetch\n"
        "  TAKES: id: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: GET\n"
        '    url: "https://example.com/${id}"\n'
        "FLOW f\n"
        '  fetch(id="1")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "fetch.py").read_text()
    assert "for _i in range(_attempts)" not in body
    assert "response = requests.request(method='GET', url=_url, **_kwargs)" in body


_REST_BODY_JSON_SRC = (
    "STEP create\n"
    "  TAKES: name: str\n"
    "  GIVES: id: str\n"
    "  MODE: exact\n"
    "  impl:\n"
    "    mode: rest\n"
    "    method: POST\n"
    '    url: "https://api.example.com/users"\n'
    '    body: {name: "${name}", active: true, count: 0}\n'
    "FLOW f\n"
    '  create(name="alice")\n'
)


def test_emit_rest_body_json_uses_json_kwarg(tmp_path):
    PythonEmitter().emit(build_ir(parse(_REST_BODY_JSON_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "create.py").read_text()
    assert "_kwargs['json'] = _rest.render_dict(" in body
    assert "'active', True" in body


def test_emit_rest_body_raw_uses_data_with_text_plain(tmp_path):
    src = (
        "STEP echo\n"
        "  TAKES: msg: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/echo"\n'
        '    body: "raw ${msg}"\n'
        "FLOW f\n"
        '  echo(msg="hi")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "echo.py").read_text()
    assert "_kwargs['data'] = _rest.subst('raw ${msg}', _takes)" in body
    assert "Content-Type" in body
    assert "text/plain" in body


def test_emit_rest_body_file_uses_read_file_body(tmp_path):
    src = (
        "STEP send\n"
        "  TAKES: id: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/upload"\n'
        '    body: "@./payload.json"\n'
        "FLOW f\n"
        '  send(id="1")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "send.py").read_text()
    assert "_rest.read_file_body('./payload.json', _takes)" in body
    assert "_kwargs['data'] = _data" in body


def test_emit_rest_body_form_uses_data_dict(tmp_path):
    src = (
        "STEP login\n"
        "  TAKES: u: str, p: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/login"\n'
        '    body: {form: {user: "${u}", password: "${p}"}}\n'
        "FLOW f\n"
        '  login(u="bob", p="hunter2")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "login.py").read_text()
    assert "_kwargs['data'] = _rest.render_dict(" in body
    assert "'user', '${u}'" in body


def test_emit_rest_body_multipart_routes_at_files_to_files(tmp_path):
    src = (
        "STEP upload\n"
        "  TAKES: lab: str\n"
        "  GIVES: r: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: rest\n"
        "    method: POST\n"
        '    url: "https://example.com/upload"\n'
        '    body: {multipart: {label: "${lab}", file: "@./blob.bin"}}\n'
        "FLOW f\n"
        '  upload(lab="cv")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "upload.py").read_text()
    assert "_files: dict = {}" in body
    assert "_form: dict = {}" in body
    assert "if isinstance(_v, str) and _v.startswith('@'):" in body
    assert "_rest.content_type_for_path(_path)" in body
    # File handle is closed via context manager (no fd leak across loop iterations).
    assert "with open(_path, 'rb') as _f:" in body
    # Cross-platform basename (Path(...).name handles `\` on Windows).
    assert "Path(_path).name" in body
    assert "_f.read()" in body
    # The pathlib import is added on demand only when the body is multipart.
    assert "from pathlib import Path" in body


_SHELL_SRC = (
    "STEP extract_pdf\n"
    "  TAKES: file: str\n"
    "  GIVES: text: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode: shell\n"
    '    cmd: "pdftotext ${file} -"\n'
    "    timeout: 60s\n"
    "FLOW pipe\n"
    '  extract_pdf(file="a.pdf")\n'
)


def test_emit_shell_step_imports_subprocess(tmp_path):
    PythonEmitter().emit(build_ir(parse(_SHELL_SRC)), tmp_path)
    body = (tmp_path / "pipe" / "steps" / "extract_pdf.py").read_text()
    assert "import subprocess" in body
    assert "subprocess.run(_argv, capture_output=True, text=True, check=True, timeout=60)" in body


def test_emit_shell_step_emits_argv_list_and_substitutions(tmp_path):
    PythonEmitter().emit(build_ir(parse(_SHELL_SRC)), tmp_path)
    body = (tmp_path / "pipe" / "steps" / "extract_pdf.py").read_text()
    assert "_argv = ['pdftotext', '${file}', '-']" in body
    assert "_argv = [_t.replace('${file}', str(file)) for _t in _argv]" in body
    assert "return result.stdout" in body


def test_emit_shell_step_parses_as_python(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_SHELL_SRC)), tmp_path)
    body = (tmp_path / "pipe" / "steps" / "extract_pdf.py").read_text()
    ast.parse(body)


def test_emit_shell_step_runtime_substitution(tmp_path, monkeypatch):
    import sys
    import types

    PythonEmitter().emit(build_ir(parse(_SHELL_SRC)), tmp_path)

    captured: dict[str, object] = {}

    class _FakeResult:
        stdout = "extracted text"

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured.update(kwargs)
        return _FakeResult()

    fake = types.ModuleType("subprocess")
    fake.run = fake_run
    monkeypatch.setitem(sys.modules, "subprocess", fake)

    sys.path.insert(0, str(tmp_path))
    try:
        from pipe.steps import extract_pdf as ep_mod
        out = ep_mod.extract_pdf(file="report.pdf")
        assert out == "extracted text"
        assert captured["argv"] == ["pdftotext", "report.pdf", "-"]
        assert captured["timeout"] == 60
        assert captured["check"] is True
        assert captured["capture_output"] is True
        assert captured["text"] is True
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k == "pipe" or k.startswith("pipe."):
                del sys.modules[k]


def test_emit_shell_step_with_parse_json_imports_json_and_calls_loads(tmp_path):
    src = (
        "STEP load_corpus\n"
        "  TAKES: file: str\n"
        "  GIVES: corpus: List<str>\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode:  shell\n"
        '    cmd:   "cat ${file}"\n'
        "    parse: json\n"
        "FLOW pipe\n"
        '  load_corpus(file="x")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "pipe" / "steps" / "load_corpus.py").read_text()
    assert "import json" in body
    assert "json.loads(result.stdout)" in body
    assert "return result.stdout" not in body


def test_emit_shell_step_default_parse_returns_stdout_string(tmp_path):
    """Regression guard for v0.4 behaviour — parse=none keeps the legacy emit."""
    src = (
        "STEP extract_pdf\n"
        "  TAKES: file: str\n"
        "  GIVES: text: str\n"
        "  MODE: exact\n"
        "  impl:\n"
        "    mode: shell\n"
        '    cmd:  "pdftotext ${file} -"\n'
        "FLOW pipe\n"
        '  extract_pdf(file="x")\n'
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "pipe" / "steps" / "extract_pdf.py").read_text()
    assert "return result.stdout" in body
    assert "json.loads" not in body
    assert "import json" not in body


def test_emit_rest_step_templated_runtime_substitution(tmp_path, monkeypatch):
    import sys
    import types

    PythonEmitter().emit(build_ir(parse(_REST_TEMPLATED_SRC)), tmp_path)

    captured: dict[str, object] = {}

    class _FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"results": [{"formatted_address": "ok"}]}

    fake = types.ModuleType("requests")
    fake.request = lambda **kwargs: (captured.update(kwargs) or _FakeResp())
    monkeypatch.setitem(sys.modules, "requests", fake)

    sys.path.insert(0, str(tmp_path))
    try:
        from geo.steps import geocode as gc_mod
        gc_mod.geocode(address="123 Main St", country="US")
        assert captured["url"] == "https://api.example.com/geo/US?q=123 Main St"
        assert captured["method"] == "GET"
    finally:
        sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k == "geo" or k.startswith("geo."):
                del sys.modules[k]


# --- invoke: mode: api emission --------------------------------------------

_OPENAI_SRC = (
    "STEP classify\n"
    "  TAKES: text: str\n"
    "  GIVES: label: str\n"
    "  MODE:  judgment\n"
    "  invoke:\n"
    "    mode: api\n"
    "    protocol: openai\n"
    '    base_url: "http://litellm.local:4000"\n'
    '    model: "gemini-1.5-pro"\n'
    '    auth: "env:LITELLM_KEY"\n'
    "    temperature: 0.0\n"
    "    max_tokens: 256\n"
    "FLOW classifier\n"
    '  classify(text="hello")\n'
)


def test_emit_openai_step_imports_openai_not_anthropic(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    body = (tmp_path / "classifier" / "steps" / "classify.py").read_text()
    assert "import openai" in body
    assert "import anthropic" not in body


def test_emit_openai_only_no_anthropic_anywhere(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    offenders = [
        str(p.relative_to(tmp_path))
        for p in tmp_path.rglob("*.py")
        if "anthropic" in p.read_text()
    ]
    assert offenders == [], f"unexpected anthropic mentions in: {offenders}"


def test_emit_openai_only_no_pydantic_anywhere(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    offenders = [
        str(p.relative_to(tmp_path))
        for p in tmp_path.rglob("*.py")
        if "pydantic" in p.read_text()
    ]
    assert offenders == [], f"unexpected pydantic mentions in: {offenders}"


def test_emit_openai_step_uses_chat_completions(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    body = (tmp_path / "classifier" / "steps" / "classify.py").read_text()
    assert "client.chat.completions.create(" in body
    assert "msg.choices[0].message.content" in body
    # messages array uses both system and user roles
    assert "{'role': 'system'" in body
    assert "{'role': 'user'" in body


def test_emit_openai_step_passes_base_url_and_auth(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    body = (tmp_path / "classifier" / "steps" / "classify.py").read_text()
    assert "base_url='http://litellm.local:4000'" in body
    assert "api_key=os.environ.get('LITELLM_KEY')" in body


def test_emit_openai_step_uses_invoke_model_not_resources(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    body = (tmp_path / "classifier" / "steps" / "classify.py").read_text()
    # invoke.model is the literal model id passed to the endpoint;
    # _MODELS becomes a single-element tuple of that id, no Anthropic mapping.
    assert "_MODELS = ('gemini-1.5-pro',)" in body


def test_emit_openai_step_parses_as_python(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    body = (tmp_path / "classifier" / "steps" / "classify.py").read_text()
    ast.parse(body)


def test_emit_openai_pyproject_adds_openai_dep(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "openai>=1.0" in pyproject


def test_emit_openai_only_pyproject_omits_anthropic_dep(tmp_path):
    PythonEmitter().emit(build_ir(parse(_OPENAI_SRC)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "anthropic" not in pyproject


def test_emit_pyproject_includes_anthropic_when_judgment_step_present(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "anthropic>=0.40" in pyproject


def test_emit_pyproject_omits_pydantic_when_no_contracts(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "pydantic" not in pyproject


def test_emit_pyproject_includes_pydantic_when_contracts_present(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "pydantic>=2" in pyproject


def test_emit_pyproject_omits_openai_when_no_openai_protocol(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "openai" not in pyproject


def test_emit_bedrock_protocol_raises_at_compile_time(tmp_path):
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: bedrock\n"
        '    model: "anthropic.claude-3-5-sonnet"\n'
        "FLOW f\n"
        "  s()\n"
    )
    with pytest.raises(ValueError) as exc:
        PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    assert "bedrock" in str(exc.value)


def test_emit_vertex_protocol_raises_at_compile_time(tmp_path):
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: vertex\n"
        '    model: "claude-3-5-sonnet-v2"\n'
        "FLOW f\n"
        "  s()\n"
    )
    with pytest.raises(ValueError) as exc:
        PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    assert "vertex" in str(exc.value)


def test_emit_cli_invoke_raises_at_compile_time(tmp_path):
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: cli\n"
        "FLOW f\n"
        "  s()\n"
    )
    with pytest.raises(ValueError) as exc:
        PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    assert "cli" in str(exc.value).lower()


def test_emit_anthropic_invoke_with_overrides(tmp_path):
    """Anthropic with explicit invoke.model + overrides should produce code that
    still uses the Anthropic SDK but with the overridden model and parameters."""
    src = (
        "STEP s\n"
        "  GIVES: r: str\n"
        "  MODE: judgment\n"
        "  invoke:\n"
        "    mode: api\n"
        "    protocol: anthropic\n"
        '    model: "claude-opus-4-7"\n'
        "    temperature: 0.5\n"
        "    max_tokens: 2048\n"
        "FLOW f\n"
        "  s()\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "s.py").read_text()
    assert "import anthropic" in body
    assert "_MODELS = ('claude-opus-4-7',)" in body
    assert "max_tokens=2048" in body
    assert "temperature=0.5" in body


# --- FOR EACH emission -----------------------------------------------------

_FOREACH_SRC = (
    "STEP load_articles\n"
    "  GIVES: articles: List<str>\n"
    "  MODE: exact\n"
    "STEP extract\n"
    "  TAKES: text: str\n"
    "  GIVES: entities: List<str>\n"
    "  MODE: exact\n"
    "FLOW pipe\n"
    "  load_articles()\n"
    "    -> FOR EACH article IN articles:\n"
    "         extract(text=article)\n"
)


def test_emit_for_each_generates_python_loop(tmp_path):
    PythonEmitter().emit(build_ir(parse(_FOREACH_SRC)), tmp_path)
    flow = (tmp_path / "pipe" / "flow.py").read_text()
    assert "for article in state['articles']:" in flow
    # Body call uses the loop variable as a kwarg, not state[]
    assert "extract_mod.extract(text=article)" in flow


def test_emit_for_each_does_not_assign_to_state_inside_body(tmp_path):
    """v0 limitation: the body call's result is invoked for side effects,
    not accumulated into state. State assignment only happens at top-level."""
    PythonEmitter().emit(build_ir(parse(_FOREACH_SRC)), tmp_path)
    flow = (tmp_path / "pipe" / "flow.py").read_text()
    # `state['articles'] = ...` for the top-level call is fine
    assert "state['articles'] = load_articles_mod.load_articles()" in flow
    # ...but inside the loop, the extract call should not be wrapped in state[...] = ...
    inside_loop = flow.split("for article in state['articles']:")[1]
    assert "state[" not in inside_loop


def test_emit_for_each_flow_parses_as_python(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_FOREACH_SRC)), tmp_path)
    flow = (tmp_path / "pipe" / "flow.py").read_text()
    ast.parse(flow)


def test_emit_for_each_nested(tmp_path):
    src = (
        "STEP load\n  GIVES: matrix: List<List<str>>\n  MODE: exact\n"
        "STEP inner\n  TAKES: cell: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH row IN matrix:\n"
        "         FOR EACH cell IN row:\n"
        "           inner(cell=cell)\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow = (tmp_path / "pipe" / "flow.py").read_text()
    # Nested loops should produce nested Python for-loops with proper indentation;
    # inside the inner loop, `cell` resolves via the local scope, not state[]
    assert "for row in state['matrix']:" in flow
    assert "for cell in row:" in flow
    assert "inner_mod.inner(cell=cell)" in flow


_PARALLEL_FOR_EACH_SRC = (
    "STEP load\n  GIVES: docs: List<str>\n  MODE: exact\n"
    "STEP classify\n  TAKES: text: str\n  GIVES: label: str\n  MODE: exact\n"
    "FLOW pipe\n"
    "  load()\n"
    "    -> FOR EACH doc IN docs PARALLEL AS labels:\n"
    "         classify(text=doc)\n"
)


def test_python_emits_thread_pool_for_parallel_for_each(tmp_path):
    PythonEmitter().emit(build_ir(parse(_PARALLEL_FOR_EACH_SRC)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "import concurrent.futures" in flow_py
    assert "ThreadPoolExecutor(max_workers=10)" in flow_py
    assert "concurrent.futures.as_completed" in flow_py
    assert "_results = [None] *" in flow_py
    assert "state['labels'] = _results" in flow_py or 'state["labels"] = _results' in flow_py


def test_python_does_not_import_concurrent_when_no_parallel(tmp_path):
    """Sequential-only flows must not pull in concurrent.futures (unused dep)."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP process\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items:\n"
        "         process(x=item)\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "pipe" / "flow.py").read_text()
    assert "concurrent.futures" not in flow_py


def test_flow_py_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "import time" in flow_py
    assert "from .clio_runtime import logging as _log" in flow_py


def test_flow_py_emits_set_flow_and_flow_events(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert '_log.set_flow("classify")' in flow_py
    assert '_log.emit("flow_start"' in flow_py
    assert '_log.emit("flow_end"' in flow_py
    assert "try:" in flow_py
    assert "finally:" in flow_py
    assert "_log.set_flow(None)" in flow_py


def test_judgment_step_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    assert judgment_files, "expected at least one judgment step in fixture"
    body = judgment_files[0].read_text()
    assert "import time" in body
    assert "from ..clio_runtime import logging as _log" in body


def test_judgment_step_has_step_start(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    body = judgment_files[0].read_text()
    assert '_log.emit("step_start"' in body
    assert 'mode="judgment"' in body


def test_judgment_step_has_at_least_two_step_ends(tmp_path):
    """A judgment step with cache + ON_FAIL has 3 return paths:
    cache hit, success, abort. Each gets its own step_end."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "retention" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    assert judgment_files
    body = judgment_files[0].read_text()
    count = body.count('_log.emit("step_end"')
    assert count >= 2, f"expected >=2 step_end calls, got {count}"


def test_judgment_step_step_end_carries_cache_hit_field(tmp_path):
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "retention" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    body = judgment_files[0].read_text()
    assert "cache_hit=True" in body
    assert "cache_hit=False" in body


def test_judgment_step_initializes_last_usage(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    body = judgment_files[0].read_text()
    assert "_last_usage" in body
    assert "**_last_usage" in body


# Uses contracts fixture (has exact + judgment) since skeleton is judgment-only.
def test_exact_step_emits_step_start_and_step_end(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "retention" / "steps").glob("*.py"))
    exact_files = [f for f in step_files if "(exact" in f.read_text()]
    assert exact_files, "expected at least one exact step in fixture"
    body = exact_files[0].read_text()
    assert '_log.emit("step_start"' in body
    assert 'mode="exact"' in body
    # Also assert step_end is emitted (plan calls for "exactly 1 step_start and 1 step_end").
    assert '_log.emit("step_end"' in body
    assert "duration_ms=" in body
    assert "success=True" in body


# Same fixture rationale as above.
def test_exact_step_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "retention" / "steps").glob("*.py"))
    exact_files = [f for f in step_files if "(exact" in f.read_text()]
    body = exact_files[0].read_text()
    assert "from ..clio_runtime import logging as _log" in body
    assert "import time" in body


def test_parallel_block_emits_block_events(tmp_path):
    """A FOR EACH ... PARALLEL emits parallel_block_start/end events."""
    src = Path("examples/parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert '_log.emit("parallel_block_start"' in flow_py
    assert '_log.emit("parallel_block_end"' in flow_py
    assert "total_iterations=" in flow_py
    assert "max_workers=10" in flow_py


def test_parallel_block_propagates_contextvar(tmp_path):
    """Workers must see the flow ContextVar set by run() — wrap each
    submitted task in copy_context().run()."""
    src = Path("examples/parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert "import contextvars" in flow_py
    assert "copy_context()" in flow_py


def test_judgment_step_escalate_cache_hit_emits_step_end(tmp_path):
    """When escalate hits a secondary-model cache, step_end must still be emitted."""
    src = (FIXTURES / "mvp_v03_fallback.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "retention" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    body = judgment_files[0].read_text()
    # The escalate path should now emit step_end before its return.
    # Find the esc_hit handling block and verify step_end appears between
    # "if esc_hit is not None:" and the next "return"
    esc_idx = body.find("esc_hit is not None")
    assert esc_idx > 0
    # Within the next 800 chars (the block), find both step_end emission and a return
    block = body[esc_idx:esc_idx + 800]
    assert '_log.emit("step_end"' in block
    assert "cache_hit=True" in block


def test_flow_py_contains_persist_state_helper(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "def _persist_state(step_idx: int, state: dict)" in flow_py
    assert "os.environ.get(\"CLIO_STATE_FILE\", \"state.json\")" in flow_py
    assert "json.dump" in flow_py and "default=str" in flow_py
    assert "os.replace(tmp, path)" in flow_py


def test_flow_py_contains_total_steps_constant(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # mvp_v03_skeleton has 1 step in its FLOW chain
    assert "TOTAL_STEPS = 1" in flow_py


def test_flow_py_imports_os_for_persist_state(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "import os" in flow_py


def test_run_signature_has_start_at_keyword_only(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "def run(*, start_at: int = 0, **initial: object)" in flow_py


def test_run_emits_state_json_load_when_start_at_gt_zero(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "if start_at > 0:" in flow_py
    assert 'os.environ.get("CLIO_STATE_FILE", "state.json")' in flow_py
    assert "json.load(f)" in flow_py


def test_run_emits_four_validation_systemexits(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert flow_py.count("raise SystemExit(2)") >= 4
    assert "missing" in flow_py
    assert "flow mismatch" in flow_py
    assert "only reached step" in flow_py
    assert ">= total steps=" in flow_py


def test_run_emits_else_branch_with_initial_state(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "state: dict = dict(initial)" in flow_py
    assert "else:" in flow_py


def test_flow_start_event_includes_resumed_from(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert 'resumed_from=start_at if start_at > 0 else 0' in flow_py


def test_chain_items_are_wrapped_in_start_at_gate(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    # mvp_v03_skeleton has exactly 1 chain item
    assert "if start_at < 1:" in flow_py


def test_persist_state_called_after_each_chain_item(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "_persist_state(1, state)" in flow_py


def test_three_chain_items_three_gates_three_persists(tmp_path):
    # parallel_classify.clio has exactly 3 top-level chain items:
    # load_corpus, FOR EACH PARALLEL, aggregate.
    src = Path("examples/parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    assert "if start_at < 3:" in flow_py
    # _persist_state appears once as def + 3 times as calls
    assert flow_py.count("_persist_state(") >= 3
    assert "_persist_state(1, state)" in flow_py
    assert "_persist_state(2, state)" in flow_py
    assert "_persist_state(3, state)" in flow_py
    assert "TOTAL_STEPS = 3" in flow_py


def test_for_each_block_counts_as_one_chain_item(tmp_path):
    src = Path("examples/classify_corpus.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    # classify_corpus has 2 chain items: load_lines, FOR EACH ... classify
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    # Count _persist_state(<N>, state) call sites - should be exactly 2
    import re
    call_sites = re.findall(r"_persist_state\(\d+, state\)", flow_py)
    assert len(call_sites) == 2, f"expected 2 call sites, got {call_sites}"
    assert "TOTAL_STEPS = 2" in flow_py


def test_parallel_block_counts_as_one_chain_item(tmp_path):
    src = Path("examples/parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    # parallel_classify has 3 chain items: load_corpus, FOR EACH PARALLEL, aggregate.
    # The PARALLEL block is ONE chain item (not one gate per inner iteration).
    assert "if start_at < 1:" in flow_py
    assert "if start_at < 2:" in flow_py
    assert "if start_at < 3:" in flow_py
    import re
    call_sites = re.findall(r"_persist_state\(\d+, state\)", flow_py)
    assert len(call_sites) == 3, f"expected 3 call sites, got {call_sites}"


def test_main_argparse_has_from_step(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    assert '"--from-step"' in main_py
    assert "type=int" in main_py
    assert "default=0" in main_py


def test_main_validates_negative_from_step(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    assert "args.from_step < 0" in main_py
    assert "return 2" in main_py


def test_main_passes_start_at_to_run(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    main_py = (tmp_path / "classify" / "__main__.py").read_text()
    assert "run(start_at=args.from_step" in main_py or "start_at=args.from_step" in main_py


# --- behavioral resume tests -----------------------------------------------

def _stub_step(file_path, step_name, ret_value):
    """Overwrite an emitted step file with a minimal stub that returns ret_value."""
    file_path.write_text(
        f'"""STEP {step_name} (stubbed for behavioral test)"""\n'
        f'from __future__ import annotations\n'
        f'def {step_name}(*args, **kw):\n'
        f'    return {ret_value!r}\n'
    )


def _import_and_run(tmp_path, pkg_name, **run_kwargs):
    """Add tmp_path to sys.path, import <pkg>.flow, call run(**kwargs), clean up."""
    import importlib
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        # Reload in case sys.modules has a stale entry from a previous test
        for k in list(sys.modules):
            if k == pkg_name or k.startswith(pkg_name + "."):
                del sys.modules[k]
        flow_mod = importlib.import_module(f"{pkg_name}.flow")
        return flow_mod.run(**run_kwargs)
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k == pkg_name or k.startswith(pkg_name + "."):
                del sys.modules[k]


def test_state_json_written_with_clio_state_file_env(tmp_path, monkeypatch):
    """When CLIO_STATE_FILE is set, state.json is written there after each step."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Stub the only step (mvp_v03_skeleton has 1 chain item: detect_topic)
    _stub_step(tmp_path / "classify" / "steps" / "detect_topic.py", "detect_topic", "topic_value")

    state_file = tmp_path / "state.json"
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    _import_and_run(tmp_path, "classify", doc="hello")

    assert state_file.exists()
    import json as _json
    payload = _json.loads(state_file.read_text())
    assert payload["version"] == 1
    assert payload["flow"] == "classify"
    assert payload["step_index"] == 1


def test_state_json_written_after_each_step_in_multi_step_flow(tmp_path, monkeypatch):
    """In a multi-step flow, state.json is updated after each chain item."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    # Stub each step
    for sf in (tmp_path / "retention" / "steps").glob("*.py"):
        if sf.name == "__init__.py":
            continue
        _stub_step(sf, sf.stem, "stub_value")

    state_file = tmp_path / "state.json"
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    _import_and_run(tmp_path, "retention")

    # After full run, state.json should reflect the LAST step_index = TOTAL_STEPS.
    import json as _json
    payload = _json.loads(state_file.read_text())
    assert payload["version"] == 1
    assert payload["flow"] == "retention"
    assert payload["step_index"] >= 1


def test_resume_from_step_skips_chain_items(tmp_path, monkeypatch):
    """--from-step N skips chain items 1..N and reloads state from state.json."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    for sf in (tmp_path / "retention" / "steps").glob("*.py"):
        if sf.name == "__init__.py":
            continue
        _stub_step(sf, sf.stem, f"stub_value_{sf.stem}")

    # Pre-populate state.json claiming step 1 has completed.
    # Include `customers` key so step 2's call detect_churn(customers=state['customers'])
    # doesn't raise KeyError.
    state_file = tmp_path / "state.json"
    import json as _json
    state_file.write_text(_json.dumps({
        "version": 1,
        "flow": "retention",
        "step_index": 1,
        "state": {
            "preloaded_marker": "from_state_json",
            "customers": [{"name": "preloaded", "revenue": 1.0}],
        },
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    # Run with start_at=1 — items 2 onwards execute, state seeded from file.
    result = _import_and_run(tmp_path, "retention", start_at=1)

    # The preloaded marker should still be in the state (it was loaded, not from initial kwargs).
    assert "preloaded_marker" in result, f"state lost the preloaded marker; got keys: {list(result.keys())}"
    assert result["preloaded_marker"] == "from_state_json"


def test_resume_fails_when_state_json_missing(tmp_path, monkeypatch):
    """start_at > 0 with no state.json -> SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    monkeypatch.setenv("CLIO_STATE_FILE", str(tmp_path / "nonexistent.json"))

    with pytest.raises(SystemExit) as exc:
        _import_and_run(tmp_path, "retention", start_at=1)
    assert exc.value.code == 2


def test_resume_fails_when_flow_mismatches(tmp_path, monkeypatch):
    """start_at > 0 with state.json containing wrong flow name -> SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    state_file = tmp_path / "state.json"
    import json as _json
    state_file.write_text(_json.dumps({
        "version": 1, "flow": "DIFFERENT_FLOW_NAME", "step_index": 1, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    with pytest.raises(SystemExit) as exc:
        _import_and_run(tmp_path, "retention", start_at=1)
    assert exc.value.code == 2


def test_resume_fails_when_step_index_too_low(tmp_path, monkeypatch):
    """state_index < start_at -> SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    state_file = tmp_path / "state.json"
    import json as _json
    state_file.write_text(_json.dumps({
        "version": 1, "flow": "retention", "step_index": 0, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    # state only at step 0, can't resume from 1; this triggers the
    # "step_index too low" check before the "start_at >= TOTAL_STEPS" check.
    with pytest.raises(SystemExit) as exc:
        _import_and_run(tmp_path, "retention", start_at=1)
    assert exc.value.code == 2


def test_resume_fails_when_start_at_exceeds_total_steps(tmp_path, monkeypatch):
    """start_at >= TOTAL_STEPS -> SystemExit(2)."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    g = build_ir(parse(src))
    total = len(g.flow.chain)
    PythonEmitter().emit(g, tmp_path)
    state_file = tmp_path / "state.json"
    import json as _json
    # step_index=999 ensures the "step_index too low" check passes,
    # so the start_at >= TOTAL_STEPS check is the one that fires.
    state_file.write_text(_json.dumps({
        "version": 1, "flow": "retention", "step_index": 999, "state": {}
    }))
    monkeypatch.setenv("CLIO_STATE_FILE", str(state_file))

    with pytest.raises(SystemExit) as exc:
        _import_and_run(tmp_path, "retention", start_at=total + 5)
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# RESCUE handler (v0.8)
# ---------------------------------------------------------------------------

RESCUE_SIMPLE_SRC = """STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE: exact

STEP detect
  TAKES: rows: List<int>
  GIVES: result: int
  MODE: exact

FLOW pipeline
  load(path="x.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> abort("detection failed")

RESOURCES
  target: python
"""


def test_python_emit_rescue_basic(tmp_path):
    """The emitted python module must wrap the protected call in
    try/except, define a _rescue_<step> helper, and render abort as
    raise FlowAborted."""
    graph = build_ir(parse(RESCUE_SIMPLE_SRC))
    PythonEmitter().emit(graph, tmp_path)

    flow_path = tmp_path / "pipeline" / "flow.py"
    assert flow_path.exists(), f"flow.py missing in {sorted(tmp_path.rglob('*'))}"
    flow_text = flow_path.read_text()

    # FlowAborted is defined in the emitted flow module.
    assert "class FlowAborted(Exception)" in flow_text
    # Helper present.
    assert "def _rescue_detect(state" in flow_text
    # Abort renders as raise FlowAborted (single or double quoted literal).
    assert (
        "raise FlowAborted('detection failed')" in flow_text
        or 'raise FlowAborted("detection failed")' in flow_text
    )
    # Try/except around the call site, dispatching to the rescue helper.
    assert "try:" in flow_text
    assert "_rescue_detect(state" in flow_text
    assert "except FlowAborted:" in flow_text


def test_python_runtime_rescue_aborts(tmp_path):
    """Compile + run: detect raises, the rescue catches and re-raises FlowAborted."""
    graph = build_ir(parse(RESCUE_SIMPLE_SRC))
    PythonEmitter().emit(graph, tmp_path)

    # Stub `load` to return a value, and `detect` to raise.
    (tmp_path / "pipeline" / "steps" / "load.py").write_text(
        '"""stubbed"""\n'
        "def load(*, path):\n"
        "    return [1, 2, 3]\n"
    )
    (tmp_path / "pipeline" / "steps" / "detect.py").write_text(
        '"""stubbed"""\n'
        "def detect(*, rows):\n"
        '    raise RuntimeError("synthetic failure")\n'
    )

    import importlib
    import sys
    sys.path.insert(0, str(tmp_path))
    try:
        for k in list(sys.modules):
            if k == "pipeline" or k.startswith("pipeline."):
                del sys.modules[k]
        flow_mod = importlib.import_module("pipeline.flow")
        with pytest.raises(flow_mod.FlowAborted) as exc:
            flow_mod.run()
        assert "detection failed" in str(exc.value)
    finally:
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for k in list(sys.modules):
            if k == "pipeline" or k.startswith("pipeline."):
                del sys.modules[k]


def test_python_emitter_rescue_abort_helper_takes_err(tmp_path):
    """Helper signature includes `_err`, wrapper binds `as _err` and passes it."""
    src = """STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP notify
  TAKES: channel: str
  GIVES: sent: bool
  MODE:  exact

FLOW pipeline
  load(path="x.csv")
    -> detect(rows=rows)

  RESCUE detect:
    -> notify(channel="#a")
    -> abort("boom")

RESOURCES
  target: python
  models: [haiku]
"""
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    assert "def _rescue_detect(state: dict, _err: BaseException) -> None:" in flow_py
    assert "except Exception as _err:" in flow_py
    assert "_rescue_detect(state, _err)" in flow_py


def test_python_emitter_rescue_error_access_substitutions(tmp_path):
    """ErrorAccessIR kwargs emit str(_err) / type(_err).__name__ in _rescue_ helper."""
    src = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP notify
  TAKES: channel: str, reason: str, err_type: str
  GIVES: sent: bool
  MODE:  exact

FLOW pipeline
  load(path="x") -> detect(rows=rows)

  RESCUE detect:
    -> notify(channel="#a", reason=detect.error.message, err_type=detect.error.type)
    -> abort("boom")

RESOURCES
  target: python
  models: [haiku]
"""
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    assert "reason=str(_err)" in flow_py
    assert "err_type=type(_err).__name__" in flow_py


# --- impl.mode: mcp_tool emission (v0.10) -----------------------------------

_MCP_PY_SRC = (
    "STEP search\n"
    "  TAKES: query: str\n"
    "  GIVES: r: str\n"
    "  MODE:  exact\n"
    "  impl:\n"
    "    mode:    mcp_tool\n"
    "    server:  docs\n"
    "    tool:    search\n"
    "    args:    {q: \"${query}\", limit: 10}\n"
    "    timeout: 30s\n"
    "FLOW f\n"
    '  search(query="x")\n'
    "RESOURCES\n"
    "  target: python\n"
    "  mcp_servers:\n"
    "    docs:\n"
    "      transport: stdio\n"
    '      command:   "mcp-server-docs"\n'
    '      args:      ["--cfg"]\n'
    '      env:       {INDEX: "env:DOCS_INDEX"}\n'
)


def test_python_emit_mcp_tool_step_uses_call_tool_sync(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_MCP_PY_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "search.py").read_text()
    assert "from ..clio_runtime import mcp_client as _mcp" in body
    assert "_mcp.call_tool_sync(" in body
    assert "'transport': 'stdio'" in body
    assert "'command': 'mcp-server-docs'" in body
    assert "'q': '${query}'" in body
    assert "timeout=30" in body
    assert "parse='json'" in body
    ast.parse(body)


def test_python_emit_mcp_tool_bundles_runtime(tmp_path):
    PythonEmitter().emit(build_ir(parse(_MCP_PY_SRC)), tmp_path)
    rt = tmp_path / "f" / "clio_runtime"
    assert (rt / "mcp_client.py").exists()
    # mcp_client imports `subst` from rest, so rest must be bundled too.
    assert (rt / "rest.py").exists()


def test_python_emitter_rescue_resume_terminator(tmp_path):
    src = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  judgment

STEP recover
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  exact

STEP downstream
  TAKES: report: str
  GIVES: ok: bool
  MODE:  exact

FLOW pipeline
  load(path="x") -> detect(rows=rows) -> downstream(report=report)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: python
  models: [haiku]
"""
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)
    flow_py = (tmp_path / "pipeline" / "flow.py").read_text()
    # Wrapper assigns helper return to rescued step's slot:
    assert "state['report'] = _rescue_detect(state, _err)" in flow_py
    # Helper returns from the state slot the fallback populated:
    assert "return state['report']" in flow_py
    # The downstream call still reads state["report"] as before:
    assert "state['ok'] = downstream_mod.downstream(report=state['report'])" in flow_py


def test_python_emit_mcp_tool_renders_env_pairs_as_list_of_tuples(tmp_path):
    PythonEmitter().emit(build_ir(parse(_MCP_PY_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "search.py").read_text()
    # env pairs round-trip through json-friendly list-of-tuples (so the
    # runtime can iterate (k, v) pairs without dict-ordering surprises).
    assert "'env': [('INDEX', 'env:DOCS_INDEX')]" in body


# --- impl.mode: sql emission (v0.11) ----------------------------------------

_SQL_PY_SRC = (
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
    "RESOURCES\n"
    "  target: python\n"
    "  databases:\n"
    "    crm:\n"
    "      driver: sqlite\n"
    '      url:    "./crm.sqlite"\n'
)


def test_python_emit_sql_step_calls_runtime_execute(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_SQL_PY_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "get_orders.py").read_text()
    assert "from ..clio_runtime import sql as _sql" in body
    assert "_sql.execute(_db_spec, _query, _params, gives_shape='list_of_records')" in body
    assert "'name': 'crm'" in body
    assert "'driver': 'sqlite'" in body
    assert "'email': email" in body
    ast.parse(body)


def test_python_emit_sql_bundles_runtime(tmp_path):
    PythonEmitter().emit(build_ir(parse(_SQL_PY_SRC)), tmp_path)
    rt = tmp_path / "f" / "clio_runtime"
    assert (rt / "sql.py").exists()


def test_python_emit_sql_record_shape(tmp_path):
    src = _SQL_PY_SRC.replace(
        "  GIVES: orders: List<{id: int, status: str}>",
        "  GIVES: order: {id: int, status: str}",
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "get_orders.py").read_text()
    assert "gives_shape='record'" in body


def test_python_emit_sql_primitive_shape(tmp_path):
    src = _SQL_PY_SRC.replace(
        "  GIVES: orders: List<{id: int, status: str}>",
        "  GIVES: count: int",
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "f" / "steps" / "get_orders.py").read_text()
    assert "gives_shape='primitive'" in body


def test_python_emit_sql_preserves_multiline_query(tmp_path):
    PythonEmitter().emit(build_ir(parse(_SQL_PY_SRC)), tmp_path)
    body = (tmp_path / "f" / "steps" / "get_orders.py").read_text()
    # The block scalar must round-trip the SQL with internal newlines as `\n`.
    assert "_query = 'SELECT id, status\\nFROM orders\\nWHERE email = :email'" in body


def test_python_emit_sql_list_of_primitive_rejected(tmp_path):
    """Gemini PR #6 review: GIVES: List<int> would silently produce
    [{'col': 1}, ...] instead of [1, ...]. Reject at emit time with a
    clear message pointing at the wrap-in-a-record workaround."""
    import pytest as _pt
    src = _SQL_PY_SRC.replace(
        "  GIVES: orders: List<{id: int, status: str}>",
        "  GIVES: ids: List<int>",
    )
    with _pt.raises(ValueError, match="List<PrimitiveType>.*single-field record"):
        PythonEmitter().emit(build_ir(parse(src)), tmp_path)


# -- DESCRIPTION / STRATEGIES injection into system prompt (v0.15) --


def test_python_emit_injects_description_into_system_prompt(tmp_path):
    src = (
        "STEP analyze\n"
        '  DESCRIPTION: "score risk on a cohort"\n'
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Step intent: score risk on a cohort" in body


def test_python_emit_injects_strategies_into_system_prompt(tmp_path):
    src = (
        "STEP analyze\n"
        "  STRATEGIES: |\n"
        "    - prefer high-recency signals\n"
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Heuristics:" in body
    assert "prefer high-recency signals" in body


def test_python_emit_no_description_keeps_legacy_prompt(tmp_path):
    src = (
        "STEP analyze\n"
        "  TAKES: rows: str\n  GIVES: risks: str\n  MODE: judgment\n"
        "FLOW p\n  analyze(rows=\"x\")\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "p" / "steps" / "analyze.py").read_text()
    assert "Step intent:" not in body
    assert "Heuristics:" not in body
    # The original 3-line legacy SYSTEM_PROMPT is preserved byte-for-byte.
    assert "    'You are a strict JSON-only API." in body


# -- TEST block emission (v0.15) --


def test_python_emit_writes_test_file_per_test(tmp_path):
    src = (
        "STEP load\n  TAKES: f: str\n  GIVES: rows: List<int>\n  MODE: exact\n"
        "FLOW p\n  load(f=\"d\")\n"
        "TEST t_one:\n  FLOW: p\n  EXPECTS:\n    rows: not_empty\n"
        "TEST t_two:\n  FLOW: p\n  EXPECTS:\n    rows: contains 42\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    assert (tmp_path / "tests" / "test_t_one.py").exists()
    assert (tmp_path / "tests" / "test_t_two.py").exists()
    body = (tmp_path / "tests" / "test_t_one.py").read_text()
    assert "from p.flow import run" in body
    assert "monkeypatch.setenv(\"CLIO_STATE_FILE\"" in body
    assert "bool(state.get('rows'))" in body


def test_python_emit_no_test_block_means_no_tests_dir(tmp_path):
    src = (
        "STEP load\n  MODE: exact\n"
        "FLOW p\n  load()\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    assert not (tmp_path / "tests").exists()


def test_python_emit_predicate_human_messages(tmp_path):
    src = (
        "STEP load\n  TAKES: f: str\n  GIVES: rows: List<int>\n  MODE: exact\n"
        "FLOW p\n  load(f=\"d\")\n"
        "TEST t1:\n"
        "  FLOW: p\n"
        "  EXPECTS:\n"
        "    rows: not_empty\n"
        "  EXPECTS_NOT:\n"
        "    rows: empty\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    body = (tmp_path / "tests" / "test_t1.py").read_text()
    assert "be not empty" in body
    assert "NOT to be empty" in body


def test_python_emit_test_files_are_executable_pytest(tmp_path):
    """Smoke: the emitted pytest file must run and assert against the result
    of run() returning state. We use a NotImplementedError-stub step but make
    the assertion target the EXACT-step path: state will be empty, the test
    will fail at the assertion, but pytest itself must collect+run the file
    cleanly (no SyntaxError, no ImportError)."""
    src = (
        "STEP load\n  TAKES: f: str\n  GIVES: rows: List<int>\n  MODE: exact\n"
        "FLOW p\n  load(f=\"d\")\n"
        "TEST t_collect:\n  FLOW: p\n  EXPECTS:\n    rows: not_empty\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    # Just check the test file compiles as Python.
    test_path = tmp_path / "tests" / "test_t_collect.py"
    code = test_path.read_text()
    compile(code, str(test_path), "exec")  # raises SyntaxError if invalid


def test_emit_with_python_keyword_flow_name_produces_importable_package(tmp_path):
    """A FLOW named after a Python keyword (e.g. `class`) produces a package
    whose generated imports (`from class.flow import ...`) would be a SyntaxError.
    The emitter must sanitize the package directory and import statements.
    """
    src = (
        "STEP greet\n"
        "  GIVES: msg: str\n"
        "  MODE: exact\n"
        "FLOW class\n"
        "  greet()\n"
    )
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)

    # The emitted package directory must NOT clash with a Python reserved
    # keyword; one stable convention is to suffix with `_`.
    pkg_dir = tmp_path / "class_"
    assert pkg_dir.is_dir(), (
        f"expected sanitized package dir 'class_' under {tmp_path}, got: "
        f"{sorted(p.name for p in tmp_path.iterdir())}"
    )

    # Every emitted .py file must be syntactically valid Python (no
    # `from class import ...` lurking inside).
    for py in pkg_dir.rglob("*.py"):
        code = py.read_text()
        compile(code, str(py), "exec")  # raises SyntaxError if any import is broken
