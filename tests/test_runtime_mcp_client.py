"""Unit tests for `clio.runtime.mcp_client`.

These tests focus on the pure helpers (templating + env resolution) and
on the lazy-import behaviour. The async client lifecycle (subprocess
boot, JSON-RPC handshake) requires a real `mcp` SDK install + a live
server, so it is exercised manually with the `examples/mcp_tool.clio`
sample, not in the unit-test suite.
"""
import os

import pytest

from clio.runtime.mcp_client import _resolve_env, render_args


def test_render_args_substitutes_string_leaves():
    out = render_args({"q": "${name}", "limit": 10}, {"name": "alice"})
    assert out == {"q": "alice", "limit": 10}


def test_render_args_walks_nested_dicts_and_lists():
    args = {"filters": {"kind": "${k}", "rank": 5}, "ids": [1, "${tag}", 3]}
    out = render_args(args, {"k": "doc", "tag": "x"})
    assert out == {"filters": {"kind": "doc", "rank": 5}, "ids": [1, "x", 3]}


def test_render_args_passes_through_non_string_scalars():
    out = render_args({"a": 1, "b": True, "c": None, "d": 1.5}, {})
    assert out == {"a": 1, "b": True, "c": None, "d": 1.5}


def test_render_args_handles_top_level_string():
    assert render_args("hello-${who}", {"who": "world"}) == "hello-world"


def test_resolve_env_returns_value_when_no_env_prefix():
    assert _resolve_env("application/json") == "application/json"


def test_resolve_env_resolves_full_match(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN_TEST", "secret-123")
    assert _resolve_env("env:MCP_TOKEN_TEST") == "secret-123"


def test_resolve_env_raises_on_missing_var(monkeypatch):
    monkeypatch.delenv("UNSET_FOR_MCP_TEST", raising=False)
    with pytest.raises(KeyError, match="UNSET_FOR_MCP_TEST"):
        _resolve_env("env:UNSET_FOR_MCP_TEST")


def test_resolve_env_only_resolves_whole_string():
    """A bare `env:NAME` substring inside a longer string is plain text —
    consistent with the rest-runtime convention. Use ${var} for inline."""
    os.environ.setdefault("ANY_VAR", "x")
    assert _resolve_env("Bearer env:ANY_VAR") == "Bearer env:ANY_VAR"


def test_import_mcp_friendly_error_when_sdk_missing():
    """If the `mcp` SDK is not installed, the lazy import should raise a
    RuntimeError with installation guidance — never an opaque ImportError."""
    from clio.runtime import mcp_client
    try:
        import mcp  # noqa: F401
        pytest.skip("mcp SDK is installed; cannot exercise the friendly-error path")
    except ImportError:
        pass
    with pytest.raises(RuntimeError, match=r"`mcp` package is required"):
        mcp_client._import_mcp()


# -- _ensure_loop ------------------------------------------------------------


def test_ensure_loop_creates_running_daemon_thread_loop():
    """First call boots a daemon-thread asyncio loop; the second call must
    reuse it (singleton — there's exactly one MCP client loop per process)."""
    import asyncio
    from clio.runtime import mcp_client

    loop1 = mcp_client._ensure_loop()
    assert isinstance(loop1, asyncio.AbstractEventLoop)
    assert loop1.is_running()
    # Singleton: returned reference is stable across calls.
    loop2 = mcp_client._ensure_loop()
    assert loop2 is loop1


# -- _extract: result-to-Python mapping --------------------------------------

class _StubText:
    def __init__(self, text):
        self.text = text


class _StubNonText:
    """A content block with no .text attribute, mimicking a non-text MCP block."""
    pass


class _StubResult:
    def __init__(self, content, *, isError=False):
        self.content = content
        self.isError = isError


def test_extract_parses_json_text_block():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([_StubText('{"hits": [1, 2, 3]}')])
    assert _extract(result, "json") == {"hits": [1, 2, 3]}


def test_extract_returns_text_verbatim_for_parse_text():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([_StubText("ok, done")])
    assert _extract(result, "text") == "ok, done"


def test_extract_raises_on_is_error_true():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([_StubText("server complained")], isError=True)
    with pytest.raises(RuntimeError, match="MCP tool returned an error"):
        _extract(result, "json")


def test_extract_raises_on_empty_content():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([])
    with pytest.raises(RuntimeError, match="empty content"):
        _extract(result, "json")


def test_extract_raises_on_non_text_first_block():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([_StubNonText()])
    with pytest.raises(RuntimeError, match="non-text content"):
        _extract(result, "text")


def test_extract_raises_on_unknown_parse_mode():
    from clio.runtime.mcp_client import _extract
    result = _StubResult([_StubText("anything")])
    with pytest.raises(ValueError, match="unknown parse mode"):
        _extract(result, "yaml")


def test_extract_treats_missing_is_error_as_success():
    """`getattr(result, 'isError', False)` defaults to False — older MCP
    SDK versions don't always populate the field. Missing should not raise."""
    from clio.runtime.mcp_client import _extract

    class _ResultWithoutIsError:
        content = [_StubText("ok")]

    assert _extract(_ResultWithoutIsError(), "text") == "ok"
