"""Runtime helpers for impl.mode: mcp_tool steps.

Copied verbatim into the emitted package's `clio_runtime/` directory by the
python, mcp-server, and claude-cli emitters when the flow has any
mcp_tool step. See LANGUAGE_SPEC.md §impl.mode: mcp_tool for the
source-language reference.

Design:
- Long-lived per-server clients in the python / mcp-server targets:
  the first call_tool_*() that references a server lazily boots the
  client (subprocess for stdio, HTTP session for sse/http) and
  subsequent calls reuse it. Cleanup at process exit via atexit.
- Sync calls (`call_tool_sync`) bridge to a daemon thread running an
  asyncio loop, so emitted python code can stay sync (matching the
  `requests`-based REST runtime).
- The async API (`call_tool_async`) is what mcp-server target uses
  directly inside its async tool handlers.
- claude-cli scripts run `asyncio.run(call_tool_async(...))` per step
  (no shared state between subprocess invocations).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import threading
from typing import Any

from .rest import subst as _subst

_ENV_WHOLE = re.compile(r"^env:([A-Z_][A-Z0-9_]*)$")


def _resolve_env(value: str) -> str:
    """`env:NAME` → os.environ[NAME] when the whole string matches; else
    return the value unchanged. Used for headers/env entries in server
    specs."""
    m = _ENV_WHOLE.match(value)
    if m is None:
        return value
    name = m.group(1)
    if name not in os.environ:
        raise KeyError(f"impl.mcp_tool: env var {name!r} is not set")
    return os.environ[name]


def render_args(args: Any, takes: dict[str, Any]) -> Any:
    """Walk a tool-args structure (scalar / dict / list), substituting
    `${var}` on every string leaf via the same `subst` rules as
    `clio_runtime.rest`. Non-string scalars pass through unchanged."""
    if isinstance(args, str):
        return _subst(args, takes)
    if isinstance(args, dict):
        return {k: render_args(v, takes) for k, v in args.items()}
    if isinstance(args, (list, tuple)):
        return [render_args(v, takes) for v in args]
    return args


# ---- background asyncio loop (sync bridge) ---------------------------------
#
# python target emits sync code; mcp_tool needs async. We keep one daemon
# thread running an asyncio loop and submit coroutines to it via
# run_coroutine_threadsafe. Started on first sync call, torn down at
# exit.

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=loop.run_forever, name="clio-mcp-loop", daemon=True,
            )
            t.start()
            _loop = loop
            _loop_thread = t
        return _loop


# ---- per-server client cache -----------------------------------------------

class _Client:
    """Holds the live ClientSession plus the two async-context-managers
    that produced it (the transport ctx and the ClientSession ctx)."""
    __slots__ = ("_session_ctx", "_transport_ctx", "session")

    def __init__(self, session: Any, transport_ctx: Any, session_ctx: Any) -> None:
        self.session = session
        self._transport_ctx = transport_ctx
        self._session_ctx = session_ctx

    async def aclose(self) -> None:
        try:
            await self._session_ctx.__aexit__(None, None, None)
        except Exception:  # pragma: no cover — best-effort cleanup
            pass
        try:
            await self._transport_ctx.__aexit__(None, None, None)
        except Exception:  # pragma: no cover
            pass


_clients: dict[str, _Client] = {}
# Lock is created lazily inside the background loop via `_get_clients_lock()`.
# Initialising at module level would bind it to whatever loop happens to be
# current at import time (often none — RuntimeError on Python <3.10), which
# differs from the daemon-thread loop where `_ensure_client` actually runs.
_clients_lock: asyncio.Lock | None = None


def _get_clients_lock() -> asyncio.Lock:
    """Return the singleton lock, creating it on first call inside the
    running event loop. Must be invoked from the loop's own thread."""
    global _clients_lock
    if _clients_lock is None:
        _clients_lock = asyncio.Lock()
    return _clients_lock


def _import_mcp() -> tuple[Any, Any, Any, Any, Any]:
    """Lazy import of the `mcp` SDK. Raises a friendly error if missing."""
    try:
        from mcp import ClientSession  # type: ignore[import-not-found]
        from mcp.client.sse import sse_client  # type: ignore[import-not-found]
        from mcp.client.stdio import (  # type: ignore[import-not-found]
            StdioServerParameters,
            stdio_client,
        )
        try:
            from mcp.client.streamable_http import (  # type: ignore[import-not-found]
                streamablehttp_client,
            )
        except ImportError:
            streamablehttp_client = None  # type: ignore[assignment]
        return ClientSession, stdio_client, StdioServerParameters, sse_client, streamablehttp_client
    except ImportError as e:
        raise RuntimeError(
            "The `mcp` package is required for impl.mcp_tool steps. "
            "Install it with `pip install mcp` (or add `mcp` to your "
            "project's deps)."
        ) from e


async def _ensure_client(server_spec: dict[str, Any]) -> _Client:
    """Look up or start the client for `server_spec['name']`. Returns
    the cached _Client on subsequent calls."""
    name = server_spec["name"]
    async with _get_clients_lock():
        if name in _clients:
            return _clients[name]

        ClientSession, stdio_client, StdioServerParameters, sse_client, streamablehttp_client = (
            _import_mcp()
        )
        transport = server_spec.get("transport", "stdio")

        if transport == "stdio":
            env_overrides = {
                k: _resolve_env(v) for k, v in server_spec.get("env", [])
            }
            params = StdioServerParameters(
                command=server_spec["command"],
                args=list(server_spec.get("args", [])),
                env={**os.environ, **env_overrides},
            )
            transport_ctx = stdio_client(params)
        elif transport == "sse":
            url = server_spec["url"]
            headers = {
                k: _resolve_env(v) for k, v in server_spec.get("headers", [])
            }
            transport_ctx = sse_client(url, headers=headers)
        elif transport == "http":
            if streamablehttp_client is None:
                raise RuntimeError(
                    "MCP transport 'http' requires the `mcp` package with "
                    "streamable-http support (>= 1.4). Upgrade with "
                    "`pip install -U mcp`."
                )
            url = server_spec["url"]
            headers = {
                k: _resolve_env(v) for k, v in server_spec.get("headers", [])
            }
            transport_ctx = streamablehttp_client(url, headers=headers)
        else:
            raise ValueError(
                f"unknown MCP transport {transport!r} for server "
                f"{name!r} (expected stdio | sse | http)"
            )

        streams = await transport_ctx.__aenter__()
        # stdio_client / sse_client return (read, write); streamable_http
        # may return a 3-tuple (read, write, get_session_id). Take first 2.
        read, write = streams[0], streams[1]
        session_ctx = ClientSession(read, write)
        session = await session_ctx.__aenter__()
        await session.initialize()

        client = _Client(session=session, transport_ctx=transport_ctx, session_ctx=session_ctx)
        _clients[name] = client
        return client


# ---- public API ------------------------------------------------------------

async def call_tool_async(
    server_spec: dict[str, Any],
    tool: str,
    args: Any,
    takes: dict[str, Any],
    timeout: float,
    parse: str = "json",
) -> Any:
    """Call `tool` on the MCP server described by `server_spec`. `args`
    is the raw tool-args structure (with un-substituted `${var}` strings);
    `takes` provides the substitution scope."""
    client = await _ensure_client(server_spec)
    rendered = render_args(args, takes)
    result = await asyncio.wait_for(
        client.session.call_tool(tool, rendered), timeout=timeout,
    )
    return _extract(result, parse)


def call_tool_sync(
    server_spec: dict[str, Any],
    tool: str,
    args: Any,
    takes: dict[str, Any],
    timeout: float,
    parse: str = "json",
) -> Any:
    """Sync wrapper for `call_tool_async`. Spins up a daemon-thread
    asyncio loop on first call and reuses it across all servers and
    subsequent calls."""
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(
        call_tool_async(server_spec, tool, args, takes, timeout, parse),
        loop,
    )
    return fut.result(timeout=timeout + 5.0)


def _extract(result: Any, parse: str) -> Any:
    """Map a CallToolResult to a Python value. Reads the first text content
    block; `parse: json` json.loads it, `parse: text` returns it as-is."""
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool returned an error: {result!r}")
    content = getattr(result, "content", None) or []
    if not content:
        raise RuntimeError("MCP tool returned empty content")
    first = content[0]
    text = getattr(first, "text", None)
    if text is None:
        raise RuntimeError(
            f"MCP tool returned non-text content "
            f"(got {type(first).__name__}); only the first text block "
            f"is supported in v0.10"
        )
    if parse == "text":
        return text
    if parse == "json":
        return json.loads(text)
    raise ValueError(f"unknown parse mode {parse!r} (expected 'json' | 'text')")


# ---- atexit cleanup --------------------------------------------------------

def _shutdown() -> None:  # pragma: no cover — atexit-only
    if _loop is None:
        return

    async def _close_all() -> None:
        for client in list(_clients.values()):
            await client.aclose()
        _clients.clear()

    try:
        fut = asyncio.run_coroutine_threadsafe(_close_all(), _loop)
        fut.result(timeout=5.0)
    except Exception:
        pass
    try:
        _loop.call_soon_threadsafe(_loop.stop)
    except Exception:
        pass


atexit.register(_shutdown)
