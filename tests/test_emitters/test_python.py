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
    step_path = tmp_path / "retention" / "steps" / "load_customers.py"
    mod = _load_module("v03_load_customers_test", step_path)
    with pytest.raises(NotImplementedError):
        mod.load_customers(file="x.csv")


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
    mod = _load_module("v03_no_takes_test", step_path)
    with pytest.raises(NotImplementedError):
        mod.foo()


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
    import sys, json
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
    import sys, json
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
    assert "url='https://api.example.com/geocode'" in body
    assert "timeout=30" in body


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
    assert "_url = 'https://api.example.com/geo/${country}?q=${address}'" in body
    assert "_url = _url.replace('${address}', str(address))" in body
    assert "_url = _url.replace('${country}', str(country))" in body
    assert "url=_url" in body
    assert "_ = address" not in body
    assert "_ = country" not in body


def test_emit_rest_step_templated_parses_as_python(tmp_path):
    import ast
    PythonEmitter().emit(build_ir(parse(_REST_TEMPLATED_SRC)), tmp_path)
    body = (tmp_path / "geo" / "steps" / "geocode.py").read_text()
    ast.parse(body)


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
    import runpy
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

    step_path = tmp_path / "pipe" / "steps" / "extract_pdf.py"
    ns = runpy.run_path(str(step_path))
    out = ns["extract_pdf"](file="report.pdf")
    assert out == "extracted text"
    assert captured["argv"] == ["pdftotext", "report.pdf", "-"]
    assert captured["timeout"] == 60
    assert captured["check"] is True
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_emit_rest_step_templated_runtime_substitution(tmp_path, monkeypatch):
    import runpy
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

    step_path = tmp_path / "geo" / "steps" / "geocode.py"
    ns = runpy.run_path(str(step_path))
    ns["geocode"](address="123 Main St", country="US")
    assert captured["url"] == "https://api.example.com/geo/US?q=123 Main St"
    assert captured["method"] == "GET"


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

