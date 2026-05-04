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

