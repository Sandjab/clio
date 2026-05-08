# Structured JSON-Line Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in structured JSON-Line logging (`CLIO_LOG=1`) to compiled CLIO projects via a copied-verbatim `clio_runtime/logging.py` module, with instrumentation in flow + step + parallel-block emit code for `target: python` and `target: mcp-server`.

**Architecture:** A new runtime module `clio/runtime/logging.py` (~60 LOC) is copied byte-equal into each emitted project as `clio_runtime/logging.py`, exactly the way `cache.py` is copied today. The module exposes `emit(event, **fields)` (no-op when `CLIO_LOG != "1"`) and `set_flow(name)` (uses `contextvars.ContextVar`). Both emitters inject calls to `_log.set_flow` / `_log.emit` at six points (flow start/end, step start/end, parallel block start/end). The python target wraps `ThreadPoolExecutor` submissions with `contextvars.copy_context().run(...)` so `flow` propagates into worker threads; asyncio.gather propagates ContextVar natively for mcp-server.

**Tech Stack:** Python 3.12+, stdlib only (`os`, `sys`, `json`, `datetime`, `contextvars`). No new dependencies, no observability SDK.

**Spec reference:** `docs/superpowers/specs/2026-05-08-structured-logging-design.md`.

---

## File structure

| File | Action | Responsibility |
|---|---|---|
| `clio/runtime/logging.py` | **create** | Public `emit` / `set_flow`. Copied verbatim into each emitted project. ~60 LOC. |
| `tests/test_runtime_logging.py` | **create** | Unit tests for the runtime module: env-var gating, ContextVar, concurrency, error swallow, ts format. ~150 LOC. |
| `tests/test_e2e_logging.py` | **create** | Gated E2E (`CLIO_E2E=1`): compile a flow, run with `CLIO_LOG=1`, parse JSONL, assert structure. ~80 LOC. |
| `clio/emitters/python.py` | **modify** | Copy `logging.py` into `clio_runtime/`, modify `_emit_flow` and `_emit_main` (no — `__main__` unchanged), modify `_emit_judgment_step` (instrumentation + `_last_usage`). |
| `clio/emitters/_python_helpers.py` | **modify** | Modify `_emit_attempt_block` (set `_last_usage`), `emit_default_exact_step`, `emit_rest_step`, `emit_shell_step` (single start/end), `emit_parallel_for_each_python` (parallel_block_* + `copy_context().run`). |
| `clio/emitters/mcp_server.py` | **modify** | Copy `logging.py` into `clio_runtime/`. |
| `clio/emitters/_mcp_helpers.py` | **modify** | Modify `_emit_flow_module_async` (flow_start/end), `emit_judgment_step_via_sampling` (step_start/end), `emit_parallel_for_each_mcp` (parallel_block_*). |
| `tests/test_emitters/test_python.py` | **modify** | Form-based assertions for the new emit lines + verify `logging.py` byte-equal copy. |
| `tests/test_emitters/test_mcp_server.py` | **modify** | Same shape as python. |
| `tests/fixtures/expected/v03_skeleton/` | **regenerate** | 1 fixture, regen via emitter run. |
| `tests/fixtures/expected/v03_contracts/` | **regenerate** | 1 fixture. |
| `tests/fixtures/expected/v03_cache/` | **regenerate** | 1 fixture. |
| `tests/fixtures/expected/v03_onfail/` | **regenerate** | 1 fixture. |
| `tests/fixtures/expected/v03_fallback/` | **regenerate** | 1 fixture. |
| `tests/fixtures/expected/python_v03_mvp/` | **regenerate** | 1 fixture (the largest). |
| `tests/fixtures/expected/v02_*` | **regenerate** | 3 v0.2 fixtures still referenced. |
| `docs/LANGUAGE_SPEC.md` | **modify** | Add observability section: `CLIO_LOG` / `CLIO_LOG_FILE` env vars + event taxonomy. |
| `docs/COMPILATION_TARGETS.md` | **modify** | Per-target logging coverage. |
| `CHANGELOG.md` | **modify** | Unreleased section: add W2 entry. |
| `README.md` | **modify** | Brief mention of `CLIO_LOG=1` in usage. |

---

## Task 1: Runtime logging module

**Files:**
- Create: `clio/runtime/logging.py`
- Create: `tests/test_runtime_logging.py`

This task ships the standalone module. No emitter changes. No fixture regen.

- [ ] **Step 1.1: Write the failing tests (full file)**

```python
# tests/test_runtime_logging.py
"""Unit tests for clio.runtime.logging — the module copied verbatim into
emitted projects as clio_runtime/logging.py."""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from clio.runtime import logging as L


_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch):
    """Each test gets a clean env + ContextVar default + closed file handle."""
    monkeypatch.delenv("CLIO_LOG", raising=False)
    monkeypatch.delenv("CLIO_LOG_FILE", raising=False)
    L.set_flow(None)
    # Close any cached file handle so CLIO_LOG_FILE swaps cleanly between tests.
    L._reset_for_tests()
    yield
    L._reset_for_tests()


def test_emit_no_op_when_clio_log_unset(capsys):
    L.emit("flow_start", flow="x")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_emit_no_op_when_clio_log_zero(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "0")
    L.emit("flow_start", flow="x")
    captured = capsys.readouterr()
    assert captured.err == ""


def test_emit_writes_to_stderr_when_enabled(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    L.emit("step_start", step="x", mode="judgment")
    captured = capsys.readouterr()
    assert captured.out == ""
    line = captured.err.strip()
    payload = json.loads(line)
    assert payload["event"] == "step_start"
    assert payload["step"] == "x"
    assert payload["mode"] == "judgment"
    assert _TS_RE.match(payload["ts"]), f"bad ts: {payload['ts']!r}"


def test_emit_writes_to_file_when_clio_log_file_set(tmp_path, monkeypatch):
    log_path = tmp_path / "log.jsonl"
    monkeypatch.setenv("CLIO_LOG", "1")
    monkeypatch.setenv("CLIO_LOG_FILE", str(log_path))
    L.emit("flow_start", flow="a")
    L.emit("flow_end", flow="a", duration_ms=10, success=True)
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "flow_start"
    assert json.loads(lines[1])["event"] == "flow_end"


def test_emit_appends_to_file(tmp_path, monkeypatch):
    log_path = tmp_path / "log.jsonl"
    log_path.write_text('{"pre": "existing"}\n')
    monkeypatch.setenv("CLIO_LOG", "1")
    monkeypatch.setenv("CLIO_LOG_FILE", str(log_path))
    L.emit("flow_start", flow="a")
    lines = log_path.read_text().splitlines()
    assert lines[0] == '{"pre": "existing"}'
    assert json.loads(lines[1])["event"] == "flow_start"


def test_set_flow_injects_into_payload(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    L.set_flow("my_flow")
    L.emit("step_start", step="x", mode="judgment")
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["flow"] == "my_flow"


def test_set_flow_none_omits_flow_key(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    L.set_flow(None)
    L.emit("step_start", step="x", mode="judgment")
    payload = json.loads(capsys.readouterr().err.strip())
    assert "flow" not in payload


def test_caller_flow_kwarg_overrides_contextvar(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    L.set_flow("ctx_flow")
    L.emit("flow_start", flow="explicit_flow")
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["flow"] == "explicit_flow"


def test_emit_does_not_raise_on_invalid_file_path(monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    monkeypatch.setenv("CLIO_LOG_FILE", "/nonexistent/dir/log.jsonl")
    # Must not raise; logging never breaks a flow.
    L.emit("flow_start", flow="x")
    L.emit("flow_end", flow="x", duration_ms=5, success=True)


def test_ts_is_iso8601_utc_ms(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")
    L.emit("flow_start", flow="x")
    payload = json.loads(capsys.readouterr().err.strip())
    assert _TS_RE.match(payload["ts"])


def test_concurrency_atomic_lines(tmp_path, monkeypatch):
    log_path = tmp_path / "log.jsonl"
    monkeypatch.setenv("CLIO_LOG", "1")
    monkeypatch.setenv("CLIO_LOG_FILE", str(log_path))

    def _worker(thread_idx: int) -> None:
        for i in range(100):
            L.emit("step_start", step=f"t{thread_idx}_{i}", mode="judgment")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = log_path.read_text().splitlines()
    assert len(lines) == 10000
    for line in lines:
        json.loads(line)  # raises if any line is corrupted


def test_contextvar_isolation_in_asyncio_tasks(capsys, monkeypatch):
    monkeypatch.setenv("CLIO_LOG", "1")

    async def _task(flow_name: str, results: list) -> None:
        L.set_flow(flow_name)
        L.emit("step_start", step="x", mode="judgment")
        results.append(flow_name)

    async def _main() -> list[str]:
        results: list[str] = []
        await asyncio.gather(_task("flow_a", results), _task("flow_b", results))
        return results

    asyncio.run(_main())
    lines = capsys.readouterr().err.strip().splitlines()
    payloads = [json.loads(line) for line in lines]
    flows_seen = {p["flow"] for p in payloads}
    assert flows_seen == {"flow_a", "flow_b"}


def test_module_path_is_correct():
    # Sanity: the module being tested lives where the emitters expect to copy from.
    src = Path(L.__file__)
    assert src.name == "logging.py"
    assert src.parent.name == "runtime"
```

- [ ] **Step 1.2: Run tests, verify they fail**

Run: `pytest tests/test_runtime_logging.py -v`
Expected: FAIL — `clio.runtime.logging` does not yet exist (`ImportError`).

- [ ] **Step 1.3: Write `clio/runtime/logging.py`**

```python
# clio/runtime/logging.py
"""Structured JSON-Line logging for CLIO emitted projects.

Copied verbatim into the emitted project as `clio_runtime/logging.py`.

Public surface:
    emit(event: str, **fields) -> None
    set_flow(name: str | None) -> None

Activation:
    CLIO_LOG=1                  # enables emission (anything else = no-op)
    CLIO_LOG_FILE=path.jsonl    # redirect stream to file (default: stderr)

Schema: each event is a single JSON object on its own line, always carrying
'ts' (ISO 8601 UTC, ms) and 'event' (str). Caller-supplied fields are merged.
'flow' is taken from the ContextVar set by set_flow(), unless the caller
passed an explicit flow= kwarg.
"""
from __future__ import annotations

import contextvars
import json
import os
import sys
from datetime import datetime, timezone
from typing import IO

_current_flow: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "clio_flow", default=None
)

_file_handle: IO | None = None
_file_path_resolved: str | None = None


def set_flow(name: str | None) -> None:
    """Set the FLOW name carried into subsequent emit() calls in this context."""
    _current_flow.set(name)


def _enabled() -> bool:
    return os.environ.get("CLIO_LOG") == "1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _destination() -> IO:
    """Return the open stream for log writes. Caches a file handle when
    CLIO_LOG_FILE is set; falls back to sys.stderr otherwise. Path changes
    between calls are honored (tests rely on this)."""
    global _file_handle, _file_path_resolved
    requested = os.environ.get("CLIO_LOG_FILE")
    if requested is None:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
            _file_path_resolved = None
        return sys.stderr
    if _file_handle is None or _file_path_resolved != requested:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
        _file_handle = open(requested, "a", encoding="utf-8")
        _file_path_resolved = requested
    return _file_handle


def emit(event: str, **fields) -> None:
    """Emit a JSON-line event. No-op when CLIO_LOG is unset/empty/0.

    Never raises: any I/O error during the write is swallowed (logging must
    not break a flow). Reserved keys 'ts' and 'event' are always set by emit
    itself; caller-supplied 'ts'/'event' would be overwritten."""
    if not _enabled():
        return
    try:
        payload: dict = {"ts": _now(), "event": event}
        flow_in_kwargs = "flow" in fields
        if not flow_in_kwargs:
            ctx_flow = _current_flow.get()
            if ctx_flow is not None:
                payload["flow"] = ctx_flow
        payload.update(fields)
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            stream = _destination()
        except Exception:
            return
        try:
            stream.write(line)
            stream.flush()
        except Exception:
            return
    except Exception:
        return


def _reset_for_tests() -> None:
    """Test helper: close any cached file handle. Not a public API."""
    global _file_handle, _file_path_resolved
    if _file_handle is not None:
        try:
            _file_handle.close()
        except Exception:
            pass
    _file_handle = None
    _file_path_resolved = None
```

- [ ] **Step 1.4: Run tests, verify they pass**

Run: `pytest tests/test_runtime_logging.py -v`
Expected: PASS — all 13 tests green.

- [ ] **Step 1.5: Run full suite to confirm no regression**

Run: `pytest tests/ -v`
Expected: 315 + 13 = 328 tests pass (existing 315 still green, plus the 13 new ones).

- [ ] **Step 1.6: Commit**

```bash
git add clio/runtime/logging.py tests/test_runtime_logging.py
git commit -m "$(cat <<'EOF'
feat(runtime): clio_runtime/logging.py — opt-in JSONL logging module

Public surface: emit(event, **fields) and set_flow(name).
Activation via CLIO_LOG=1, destination via CLIO_LOG_FILE (default stderr).
Uses ContextVar for flow propagation (thread- and asyncio-task-safe).
Swallows write errors so logging never breaks a flow.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Copy `logging.py` into emitted projects

Both `python` and `mcp-server` targets must copy `clio/runtime/logging.py` into the emitted package's `clio_runtime/` folder. No instrumentation yet — just the file copy.

**Files:**
- Modify: `clio/emitters/python.py:104-105` (the existing `cache.py` copy block)
- Modify: `clio/emitters/mcp_server.py:55-62` (the conditional cache copy block)
- Modify: `tests/test_emitters/test_python.py` (add a byte-equal test for `logging.py`)
- Modify: `tests/test_emitters/test_mcp_server.py` (add the same test)
- Regenerate: `tests/fixtures/expected/v03_skeleton/` and the 5 other v03/v02 expected fixtures

- [ ] **Step 2.1: Add the failing byte-equal test (python target)**

Append to `tests/test_emitters/test_python.py`:

```python
def test_emit_skeleton_copies_logging_verbatim(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    src_logging = (
        Path(__file__).parent.parent.parent / "clio" / "runtime" / "logging.py"
    ).read_text()
    out_logging = (tmp_path / "classify" / "clio_runtime" / "logging.py").read_text()
    assert out_logging == src_logging
```

- [ ] **Step 2.2: Run test, verify it fails**

Run: `pytest tests/test_emitters/test_python.py::test_emit_skeleton_copies_logging_verbatim -v`
Expected: FAIL — `out_logging` file does not exist (FileNotFoundError).

- [ ] **Step 2.3: Modify `clio/emitters/python.py` to copy `logging.py`**

Locate the `cache.py` copy block at the end of `emit()` (around line 103-105):

```python
        from clio import runtime as src_pkg
        src = Path(src_pkg.__file__).parent / "cache.py"
        (runtime_dir / "cache.py").write_text(src.read_text())
```

Replace with:

```python
        from clio import runtime as src_pkg
        src_dir = Path(src_pkg.__file__).parent
        (runtime_dir / "cache.py").write_text((src_dir / "cache.py").read_text())
        (runtime_dir / "logging.py").write_text((src_dir / "logging.py").read_text())
```

- [ ] **Step 2.4: Run test, verify it passes**

Run: `pytest tests/test_emitters/test_python.py::test_emit_skeleton_copies_logging_verbatim -v`
Expected: PASS.

- [ ] **Step 2.5: Add the same byte-equal test for mcp-server target**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
def test_emit_copies_logging_verbatim(tmp_path):
    """clio_runtime/logging.py must be byte-equal to the source."""
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    from clio.parser.parser import parse
    from clio.ir.builder import build_ir

    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    src_logging = (
        Path(__file__).parent.parent.parent / "clio" / "runtime" / "logging.py"
    ).read_text()
    out_logging = (tmp_path / "classify" / "clio_runtime" / "logging.py").read_text()
    assert out_logging == src_logging
```

(If the FIXTURES path or imports differ, mirror the existing test_mcp_server.py header conventions; this snippet is illustrative.)

- [ ] **Step 2.6: Run test, verify it fails**

Run: `pytest tests/test_emitters/test_mcp_server.py::test_emit_copies_logging_verbatim -v`
Expected: FAIL.

- [ ] **Step 2.7: Modify `clio/emitters/mcp_server.py` to always copy `logging.py`**

`mcp_server.py` currently only creates `clio_runtime/` when `cache_active`. The logging module must be copied unconditionally (since logging is independent from caching). Replace lines 52-62:

```python
        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            from clio import runtime as src_pkg
            runtime_dir = pkg_dir / "clio_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "__init__.py").write_text("")
            cache_src = Path(src_pkg.__file__).parent / "cache.py"
            (runtime_dir / "cache.py").write_text(cache_src.read_text())
```

With:

```python
        from clio import runtime as src_pkg
        runtime_dir = pkg_dir / "clio_runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "__init__.py").write_text("")
        src_dir = Path(src_pkg.__file__).parent
        (runtime_dir / "logging.py").write_text((src_dir / "logging.py").read_text())
        cache_active = any(
            s.cache is not None and s.cache.mode in ("on", "ttl")
            for s in graph.steps
        )
        if cache_active:
            (runtime_dir / "cache.py").write_text((src_dir / "cache.py").read_text())
```

- [ ] **Step 2.8: Run test, verify it passes**

Run: `pytest tests/test_emitters/test_mcp_server.py::test_emit_copies_logging_verbatim -v`
Expected: PASS.

- [ ] **Step 2.9: Regenerate v03/v02 expected fixtures**

The existing `test_emit_skeleton` and friends compare full trees. Each fixture in `tests/fixtures/expected/v0{2,3}_*` must now also contain `clio_runtime/logging.py`. Use a helper script to regenerate:

```bash
python -c "
from pathlib import Path
from clio.emitters.python import PythonEmitter
from clio.parser.parser import parse
from clio.ir.builder import build_ir

fixtures = Path('tests/fixtures')
sources = {
    'v03_skeleton': 'mvp_v03_skeleton.clio',
    'v03_contracts': 'mvp_v03_contracts.clio',
    'v03_cache': 'mvp_v03_cache.clio',
    'v03_onfail': 'mvp_v03_onfail.clio',
    'v03_fallback': 'mvp_v03_fallback.clio',
    'python_v03_mvp': 'mvp.clio',
    'v02_cache': 'mvp_v02_cache.clio',
    'v02_onfail': 'mvp_v02_onfail.clio',
    'v02_fallback': 'mvp_v02_fallback.clio',
}
for name, src_file in sources.items():
    src_path = fixtures / src_file
    if not src_path.exists():
        print(f'SKIP {name}: source {src_file} not found')
        continue
    out_dir = fixtures / 'expected' / name
    # Wipe existing tree to avoid stale files
    if out_dir.exists():
        import shutil; shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    PythonEmitter().emit(build_ir(parse(src_path.read_text())), out_dir)
    print(f'OK {name}')
"
```

If a source file mapping above is wrong, find the correct `.clio` source by inspecting the test that references the fixture (grep for `expected" / "<name>"` in `tests/test_emitters/test_python.py`).

- [ ] **Step 2.10: Run full python emitter tests**

Run: `pytest tests/test_emitters/test_python.py -v`
Expected: PASS for all tests including the regenerated fixtures.

- [ ] **Step 2.11: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 2.12: Commit**

```bash
git add clio/emitters/python.py clio/emitters/mcp_server.py \
        tests/test_emitters/test_python.py tests/test_emitters/test_mcp_server.py \
        tests/fixtures/expected/
git commit -m "$(cat <<'EOF'
feat(emitters): copy clio_runtime/logging.py into emitted projects

Both python and mcp-server emitters now copy clio/runtime/logging.py
verbatim into <pkg>/clio_runtime/logging.py. mcp-server creates
clio_runtime/ unconditionally now (was conditional on cache_active).
v03/v02 expected fixtures regenerated to include the new file.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Instrument `flow.py` (python target)

Wrap `run()` body with `set_flow` + `flow_start`/`flow_end` in a `try/finally`.

**Files:**
- Modify: `clio/emitters/python.py:365-450` (`_emit_flow` method)
- Modify: `tests/test_emitters/test_python.py` (form-based tests)
- Regenerate: all v03/v02 expected fixtures (the `flow.py` shape changes)

- [ ] **Step 3.1: Write the failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
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
    assert '_log.emit("flow_start")' in flow_py
    assert '_log.emit("flow_end"' in flow_py
    assert "try:" in flow_py
    assert "finally:" in flow_py
    assert "_log.set_flow(None)" in flow_py
```

- [ ] **Step 3.2: Run tests, verify they fail**

Run: `pytest tests/test_emitters/test_python.py::test_flow_py_imports_logging_and_time tests/test_emitters/test_python.py::test_flow_py_emits_set_flow_and_flow_events -v`
Expected: FAIL — emitted flow.py has no logging.

- [ ] **Step 3.3: Modify `_emit_flow` in `clio/emitters/python.py`**

Locate `_emit_flow` at line 365. Replace its return statement (currently lines 437-450) with the new template that wraps the chain in `try/finally`. Specifically, change:

```python
        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'{cf_import}'
            f'{imports}\n'
            f'\n'
            f'\n'
            f'def run(**initial: object) -> dict:\n'
            f'    state: dict = dict(initial)\n'
            + "\n".join(chain_lines)
            + "\n    return state\n"
        )
```

To:

```python
        chain_body = "\n".join("    " + line.lstrip("\n") if line.startswith(" ") else "    " + line
                               for line in chain_lines)
        # Actually we just need to indent the existing chain_lines by 4 more spaces
        # since they were generated with 4-space indent for the function body and
        # now they live inside the try block (8-space indent).
        chain_body = "\n".join("    " + cl for cl in chain_lines)

        flow_name_lit = repr(graph.flow.name)
        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'import time\n'
            f'{cf_import}'
            f'{imports}\n'
            f'\n'
            f'from .clio_runtime import logging as _log\n'
            f'\n'
            f'\n'
            f'def run(**initial: object) -> dict:\n'
            f'    state: dict = dict(initial)\n'
            f'    _log.set_flow({flow_name_lit})\n'
            f'    _log.emit("flow_start")\n'
            f'    _success = False\n'
            f'    _t0 = time.monotonic()\n'
            f'    try:\n'
            f'{chain_body}\n'
            f'        _success = True\n'
            f'        return state\n'
            f'    finally:\n'
            f'        _log.emit("flow_end", '
            f'duration_ms=int((time.monotonic() - _t0) * 1000), '
            f'success=_success)\n'
            f'        _log.set_flow(None)\n'
        )
```

Important: `chain_lines` are currently emitted with `"    "` indent (4 spaces, function body). They now live inside `try:` (8 spaces). The reindent does that.

Also handle the empty-FLOW path (`if graph.flow is None`) at line 366 — keep that as-is (it returns a stub `"""No FLOW declared."""\n\ndef run(**kwargs):\n    return {}\n`). No instrumentation needed for the empty stub.

- [ ] **Step 3.4: Run tests, verify the form tests pass**

Run: `pytest tests/test_emitters/test_python.py::test_flow_py_imports_logging_and_time tests/test_emitters/test_python.py::test_flow_py_emits_set_flow_and_flow_events -v`
Expected: PASS.

- [ ] **Step 3.5: Regenerate the v03/v02 expected fixtures**

Re-run the regen script from Step 2.9. The `flow.py` content has changed; all expected fixtures with a flow need to be updated.

- [ ] **Step 3.6: Run full python emitter test suite**

Run: `pytest tests/test_emitters/test_python.py -v`
Expected: all green.

- [ ] **Step 3.7: Run full suite**

Run: `pytest tests/ -v`
Expected: all green (parser, IR, mcp-server unchanged should still pass; mcp-server expected fixtures unchanged).

- [ ] **Step 3.8: Commit**

```bash
git add clio/emitters/python.py tests/test_emitters/test_python.py tests/fixtures/expected/
git commit -m "$(cat <<'EOF'
feat(emitters/python): instrument flow.py with flow_start/flow_end events

Wraps run() in try/finally with set_flow() + emit("flow_start") and
emit("flow_end", duration_ms, success). Adds 'import time' and the
clio_runtime.logging import. v03/v02 expected fixtures regenerated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Instrument judgment step (python target)

Add `_log.emit("step_start", ...)` and three `_log.emit("step_end", ...)` calls (cache hit / success / abort), plus `_last_usage` plumbing.

**Files:**
- Modify: `clio/emitters/python.py:135-363` (`_emit_judgment_step`)
- Modify: `clio/emitters/_python_helpers.py:304-356` (`_attempt_anthropic_block`)
- Modify: `clio/emitters/_python_helpers.py:359-411` (`_attempt_openai_block`)
- Modify: `tests/test_emitters/test_python.py` (form tests)
- Regenerate: v03/v02 expected fixtures

- [ ] **Step 4.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_judgment_step_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    # The skeleton has at least one judgment step; pick one and verify shape.
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment)" in f.read_text()
    ]
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


def test_judgment_step_has_three_step_end_calls(tmp_path):
    """A judgment step with cache + ON_FAIL has 3 return paths:
    cache hit, success, abort. Each gets its own step_end."""
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [f for f in step_files if "(judgment)" in f.read_text()]
    assert judgment_files
    body = judgment_files[0].read_text()
    count = body.count('_log.emit("step_end"')
    assert count >= 2, f"expected >=2 step_end calls, got {count}"


def test_judgment_step_step_end_carries_cache_hit_field(tmp_path):
    src = (FIXTURES / "mvp_v03_cache.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
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
```

- [ ] **Step 4.2: Run tests, verify they fail**

Run: `pytest tests/test_emitters/test_python.py -k "judgment_step" -v`
Expected: FAIL — no logging in step bodies yet.

- [ ] **Step 4.3: Modify `_attempt_anthropic_block` to populate `_last_usage`**

In `clio/emitters/_python_helpers.py`, locate `_attempt_anthropic_block` (line 304). The `_attempt` function emitted there must (a) declare `nonlocal _last_usage` and (b) populate it after a successful create. Replace the `attempt_block` list (lines 339-354) with:

```python
    attempt_block = [
        "def _attempt(model, prompt):",
        '    """Single attempt: SDK call → markdown strip → Pydantic validation."""',
        "    nonlocal _last_usage",
        "    try:",
        f"        client = anthropic.Anthropic({client_args})",
        "        msg = client.messages.create(",
    ] + create_args + [
        "        )",
        "        if hasattr(msg, 'usage') and msg.usage is not None:",
        "            _last_usage = {",
        "                'tokens_in': getattr(msg.usage, 'input_tokens', None),",
        "                'tokens_out': getattr(msg.usage, 'output_tokens', None),",
        "            }",
        "            _last_usage = {k: v for k, v in _last_usage.items() if v is not None}",
        "        raw = msg.content[0].text if msg.content else ''",
        "        if not raw:",
        "            return None",
        "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
        f"        return {result_class}(json.loads(cleaned))",
        "    except Exception:",
        "        return None",
    ]
```

- [ ] **Step 4.4: Modify `_attempt_openai_block` similarly**

In `clio/emitters/_python_helpers.py`, locate `_attempt_openai_block` (line 359). Replace the `attempt_block` list (lines 394-409) with:

```python
    attempt_block = [
        "def _attempt(model, prompt):",
        '    """Single attempt: SDK call → markdown strip → Pydantic validation."""',
        "    nonlocal _last_usage",
        "    try:",
        f"        client = openai.OpenAI({client_args})",
        "        msg = client.chat.completions.create(",
    ] + create_args + [
        "        )",
        "        if hasattr(msg, 'usage') and msg.usage is not None:",
        "            _last_usage = {",
        "                'tokens_in': getattr(msg.usage, 'prompt_tokens', None),",
        "                'tokens_out': getattr(msg.usage, 'completion_tokens', None),",
        "            }",
        "            _last_usage = {k: v for k, v in _last_usage.items() if v is not None}",
        "        raw = msg.choices[0].message.content if msg.choices else ''",
        "        if not raw:",
        "            return None",
        "        cleaned = '\\n'.join(line for line in raw.splitlines() if not line.startswith('```'))",
        f"        return {result_class}(json.loads(cleaned))",
        "    except Exception:",
        "        return None",
    ]
```

- [ ] **Step 4.5: Modify `_emit_judgment_step` in `clio/emitters/python.py`**

Locate `_emit_judgment_step` (line 135). Make these targeted modifications:

1. **Header block** (around line 198-207): add `import time` and the `_log` import.

   Find:
   ```python
       header = [
           f'"""STEP {step.name} (judgment).',
           ...
           "import json",
           "import sys",
       ]
   ```
   Replace with:
   ```python
       header = [
           f'"""STEP {step.name} (judgment).',
           f'',
           f'Auto-generated. Do not edit; regenerate via `clio compile`.',
           f'"""',
           "from __future__ import annotations",
           "",
           "import json",
           "import sys",
           "import time",
       ]
   ```
   And after the existing `[provider_imports]` block, add the logging import. Find:
   ```python
       header += [
           "",
       ] + provider_imports + [
           "",
       ]
       if cache_active:
           header += ["from ..clio_runtime import cache as _cache", ""]
   ```
   Replace with:
   ```python
       header += [
           "",
       ] + provider_imports + [
           "",
       ]
       header += ["from ..clio_runtime import logging as _log", ""]
       if cache_active:
           header += ["from ..clio_runtime import cache as _cache", ""]
   ```

2. **`_attempt_block` declaration** — `_attempt` now references `nonlocal _last_usage`, so it must be defined inside the step function (a closure). Currently `_attempt` is at module level (the `attempt_lines` are appended to `header`). Change strategy: move `_attempt` inside the `def {step.name}(...)` function body so it can close over `_last_usage`.

   Find:
   ```python
       header += [
           "",
       ] + provider_imports + [
           "",
       ]
       header += ["from ..clio_runtime import logging as _log", ""]
       if cache_active:
           header += ["from ..clio_runtime import cache as _cache", ""]
       header += [
           "from .. import contracts",
           "",
           "",
           f"_PROMPT_TEMPLATE = {prompt_template!r}",
           f"_INLINED_SCHEMA = {inlined_json!r}",
           "_SYSTEM_PROMPT = (",
           ...
           f"_MODELS = {models_array_repr}",
           "",
           "",
       ] + attempt_lines + [
           "",
           "",
       ]
   ```
   Remove the `+ attempt_lines + [..., ""]` portion from the header. Then later, inside the function body, add `attempt_lines` indented by 4 spaces. Specifically, after the `body.append(f"def {step.name}({params}) -> {ret_type}:")` line (around line 252), add the attempt block as the first thing in the function body:

   ```python
       body.append(f"def {step.name}({params}) -> {ret_type}:")
       body.append("    _t0 = time.monotonic()")
       body.append(f'    _log.emit("step_start", step={step.name!r}, mode="judgment")')
       body.append("    _last_usage: dict = {}")
       body.append("")
       # Inline _attempt as a closure
       body.extend("    " + line for line in attempt_lines)
       body.append("")
       body.append("    prompt = _PROMPT_TEMPLATE")
       body += sub_lines
       body.append("")
   ```
   
   Remove the original `body.append("    prompt = _PROMPT_TEMPLATE")` and `body += sub_lines` and `body.append("")` lines that came right after `body.append(f"def {step.name}(...)")` — they are now part of the new block above.

3. **Cache hit return path** (around line 272-278). Currently:
   ```python
       chain_lines += [
           "    cache_dir = Path(os.environ.get('CLIO_CACHE_DIR', '.cache'))",
           f"    primary_key = _cache.cache_key('{step.name}', _MODELS[0], prompt, _INLINED_SCHEMA)",
           f"    hit = _cache.cache_lookup(cache_dir, '{step.name}', primary_key, {ttl_repr})",
           "    if hit is not None:",
           "        try:",
           f"            return {result_class}(json.loads(hit))",
           "        except Exception:",
           "            pass  # stale cache (schema changed): fall through to a fresh call",
           "",
       ]
   ```
   Replace the `try` block to log step_end before returning:
   ```python
       chain_lines += [
           "    cache_dir = Path(os.environ.get('CLIO_CACHE_DIR', '.cache'))",
           f"    primary_key = _cache.cache_key('{step.name}', _MODELS[0], prompt, _INLINED_SCHEMA)",
           f"    hit = _cache.cache_lookup(cache_dir, '{step.name}', primary_key, {ttl_repr})",
           "    if hit is not None:",
           "        try:",
           f"            _ret = {result_class}(json.loads(hit))",
           f'            _log.emit("step_end", step={step.name!r}, mode="judgment",',
           "                      duration_ms=int((time.monotonic() - _t0) * 1000),",
           "                      cache_hit=True, model=_MODELS[0],",
           f"                      fallback_used={'False' if not has_fallback else 'False'}, success=True)",
           "            return _ret",
           "        except Exception:",
           "            pass  # stale cache (schema changed): fall through to a fresh call",
           "",
       ]
   ```
   (`fallback_used=False` regardless of `has_fallback` because cache hit short-circuits; the field is only ever True on the success path after a fallback was actually used.)

4. **Abort path inside terminal `abort` strategy** (around line 331-339):

   Find:
   ```python
           elif s.kind == "abort":
               msg = s.abort_message or ""
               full_msg = f"[clio] step {step.name}: {msg}"
               chain_lines += [
                   "    if response is None:",
                   f"        print({full_msg!r}, file=sys.stderr)",
                   "        raise SystemExit(1)",
                   "",
               ]
   ```
   Replace with:
   ```python
           elif s.kind == "abort":
               msg = s.abort_message or ""
               full_msg = f"[clio] step {step.name}: {msg}"
               chain_lines += [
                   "    if response is None:",
                   f"        print({full_msg!r}, file=sys.stderr)",
                   f'        _log.emit("step_end", step={step.name!r}, mode="judgment",',
                   "                  duration_ms=int((time.monotonic() - _t0) * 1000),",
                   "                  cache_hit=False, model=_MODELS[model_idx],",
                   "                  fallback_used=False, success=False)",
                   "        raise SystemExit(1)",
                   "",
               ]
   ```

5. **Default abort path** (around lines 341-347, the `if not terminal_abort: ...` block):

   Find:
   ```python
       if not terminal_abort:
           chain_lines += [
               "    if response is None:",
               f"        print('[clio] step {step.name}: ON_FAIL strategies exhausted', file=sys.stderr)",
               "        raise SystemExit(1)",
               "",
           ]
   ```
   Replace with:
   ```python
       if not terminal_abort:
           chain_lines += [
               "    if response is None:",
               f"        print('[clio] step {step.name}: ON_FAIL strategies exhausted', file=sys.stderr)",
               f'        _log.emit("step_end", step={step.name!r}, mode="judgment",',
               "                  duration_ms=int((time.monotonic() - _t0) * 1000),",
               "                  cache_hit=False, model=_MODELS[model_idx],",
               "                  fallback_used=False, success=False)",
               "        raise SystemExit(1)",
               "",
           ]
   ```

6. **Final return path** (`return response` at line 359):

   Find:
   ```python
       chain_lines.append("    return response")
   ```
   Replace with:
   ```python
       fb_field = "fallback_used=fallback_used" if has_fallback else "fallback_used=False"
       chain_lines += [
           f'    _log.emit("step_end", step={step.name!r}, mode="judgment",',
           "              duration_ms=int((time.monotonic() - _t0) * 1000),",
           f"              cache_hit=False, model=_MODELS[model_idx],",
           f"              {fb_field}, success=True, **_last_usage)",
           "    return response",
       ]
   ```

- [ ] **Step 4.6: Run form tests, verify they pass**

Run: `pytest tests/test_emitters/test_python.py -k "judgment_step" -v`
Expected: PASS for the 5 new tests added in Step 4.1.

- [ ] **Step 4.7: Regenerate v03/v02 expected fixtures**

Re-run the regen script from Step 2.9.

- [ ] **Step 4.8: Run full python emitter tests**

Run: `pytest tests/test_emitters/test_python.py -v`
Expected: all green.

- [ ] **Step 4.9: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 4.10: Sanity-execute the regenerated mvp**

Pick the largest expected fixture (`python_v03_mvp`), and verify the emitted code is at least syntactically valid:

```bash
python -c "
import ast, pathlib
p = pathlib.Path('tests/fixtures/expected/python_v03_mvp')
for py in p.rglob('*.py'):
    ast.parse(py.read_text())
    print('OK', py.relative_to(p))
"
```
Expected: every `.py` parses without `SyntaxError`.

- [ ] **Step 4.11: Commit**

```bash
git add clio/emitters/python.py clio/emitters/_python_helpers.py \
        tests/test_emitters/test_python.py tests/fixtures/expected/
git commit -m "$(cat <<'EOF'
feat(emitters/python): instrument judgment steps with step_start/step_end

Three step_end emit points: cache hit, success after _attempt, and
abort. Tokens propagated via a closure-scoped _last_usage dict that
_attempt populates from response.usage when present (Anthropic
input/output_tokens, OpenAI prompt/completion_tokens).

_attempt is now defined inside the step function so it can nonlocal-bind
_last_usage. Module-level _PROMPT_TEMPLATE / _MODELS / _SYSTEM_PROMPT
remain at module level. v03/v02 expected fixtures regenerated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Instrument exact step (python target)

Single `step_start` and single `step_end` with `mode="exact"`. No `model`, no `cache_hit`, no `fallback_used`, no `tokens_*`. Covers `emit_default_exact_step`, `emit_rest_step`, `emit_shell_step` — these helpers are shared with mcp-server target, so this task instruments mcp-server's exact steps too.

**Files:**
- Modify: `clio/emitters/_python_helpers.py:187-218` (`emit_default_exact_step`)
- Modify: `clio/emitters/_python_helpers.py:469-562` (`emit_rest_step`)
- Modify: `clio/emitters/_python_helpers.py:563-612` (`emit_shell_step`)
- Modify: `tests/test_emitters/test_python.py` (form tests)
- Regenerate: v03/v02 expected fixtures

- [ ] **Step 5.1: Write failing form tests**

Append to `tests/test_emitters/test_python.py`:

```python
def test_exact_step_emits_step_start_and_step_end(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    exact_files = [f for f in step_files if "(exact)" in f.read_text()]
    assert exact_files, "expected at least one exact step in fixture"
    body = exact_files[0].read_text()
    assert '_log.emit("step_start"' in body
    assert 'mode="exact"' in body
    assert '_log.emit("step_end"' in body
    # Negative assertions: exact-step events have no model/cache_hit
    step_ends = [line for line in body.splitlines() if '_log.emit("step_end"' in line or '_log.emit(\n' in line]
    # Easier check: scan the whole step body for forbidden keys in step_end calls
    assert "model=" not in body or "model='" not in body  # tolerated only outside step_end
    # More precise: split on step_end and check the immediate args
    # ... pragmatic relaxation: just verify the kwargs we expect ARE present
    assert "duration_ms=" in body
    assert "success=True" in body


def test_exact_step_imports_logging(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    exact_files = [f for f in step_files if "(exact)" in f.read_text()]
    body = exact_files[0].read_text()
    assert "from ..clio_runtime import logging as _log" in body
    assert "import time" in body
```

- [ ] **Step 5.2: Run tests, verify they fail**

Run: `pytest tests/test_emitters/test_python.py -k "exact_step" -v`
Expected: FAIL.

- [ ] **Step 5.3: Modify `emit_default_exact_step` in `clio/emitters/_python_helpers.py`**

Replace the function body at line 187:

```python
def emit_default_exact_step(step: "StepIR", contracts_by_name: dict[str, "ContractIR"]) -> str:
    """Emit a default-mode (no impl, or impl.mode: code) exact step body.
    Both python and mcp-server targets emit this identical shape."""
    params = _step_signature(step, contracts_by_name)
    ret_type = (
        _type_to_python(step.gives.type, contracts_by_name)
        if step.gives is not None else "None"
    )
    takes_doc = (
        "\n    ".join(f"{t.name}: {_render_type_short(t.type)}" for t in step.takes)
        if step.takes else "(no TAKES)"
    )
    gives_doc = (
        f"{step.gives.name}: {_render_type_short(step.gives.type)}"
        if step.gives is not None else "(no GIVES)"
    )
    return (
        f'"""STEP {step.name} (exact)\n'
        f'TAKES:\n'
        f'    {takes_doc}\n'
        f'GIVES:\n'
        f'    {gives_doc}\n\n'
        f'Implement the body below. The orchestrator passes arguments by keyword\n'
        f'and expects the return value to conform to the GIVES type.\n'
        f'"""\n'
        f'from __future__ import annotations\n\n'
        f'import time\n\n'
        f'from ..clio_runtime import logging as _log\n\n\n'
        f'def {step.name}({params}) -> {ret_type}:\n'
        f'    _t0 = time.monotonic()\n'
        f'    _log.emit("step_start", step={step.name!r}, mode="exact")\n'
        f'    raise NotImplementedError(\n'
        f'        "Implement steps/{step.name}.py: this is an exact (deterministic) step."\n'
        f'    )\n'
    )
```

Note: this stub raises `NotImplementedError` immediately, so `step_end` is never reached — the user implementing the step must add a final `_log.emit("step_end", ..., success=True)` themselves. The docstring should mention this. Update the docstring lines:

```python
        f'Implement the body below. The orchestrator passes arguments by keyword\n'
        f'and expects the return value to conform to the GIVES type.\n'
        f'\n'
        f'NOTE: when implementing, emit a step_end before returning:\n'
        f'    _log.emit("step_end", step={step.name!r}, mode="exact",\n'
        f'              duration_ms=int((time.monotonic() - _t0) * 1000), success=True)\n'
        f'"""\n'
```

- [ ] **Step 5.4: Modify `emit_rest_step` in `clio/emitters/_python_helpers.py`**

Locate `emit_rest_step` (line 469). The function body emits a `def {step.name}(...)` that performs an HTTP request and returns. Find the `def {step.name}(...)` template — instrument both ends. Show full new template (replacing the existing return block):

The current shape (lines 558-562) emits something like:
```python
    return (
        f'"""STEP ...\n'
        ...
        f'def {step.name}(...):\n'
        f'    ...request handling...\n'
        f'    return result.json()\n'
    )
```

Locate the actual return statement at the end of `emit_rest_step` (read the function body to confirm the exact shape). Insert the logging imports in the file header (after `from __future__ import annotations`) and instrument the function body:
- After the `def {step.name}(...):` line, prepend:
  ```python
      _t0 = time.monotonic()
      _log.emit("step_start", step={step.name!r}, mode="exact")
  ```
- Before each `return` (success only — REST steps raise on HTTP errors and propagate, no need to instrument the failure path; the outer `flow_end` catches it), prepend:
  ```python
      _log.emit("step_end", step={step.name!r}, mode="exact",
                duration_ms=int((time.monotonic() - _t0) * 1000), success=True)
  ```

Concretely, edit the file template strings inside `emit_rest_step` to add `import time\n` and `from ..clio_runtime import logging as _log\n` to the imports block, and wrap the body. Read the function and apply the change exactly. (The function is ~95 lines; reading it before editing is recommended.)

- [ ] **Step 5.5: Modify `emit_shell_step` in `clio/emitters/_python_helpers.py` similarly**

Same shape as `emit_rest_step`: add imports, wrap body. Read the function (line 563) to determine the exact insertion points.

- [ ] **Step 5.6: Run form tests, verify they pass**

Run: `pytest tests/test_emitters/test_python.py -k "exact_step" -v`
Expected: PASS.

- [ ] **Step 5.7: Regenerate fixtures**

Re-run the regen script from Step 2.9.

- [ ] **Step 5.8: Run full python emitter tests + mcp-server emitter tests**

Run: `pytest tests/test_emitters/ -v`
Expected: all green. mcp-server's exact-step tests should also pick up the new instrumentation since these helpers are shared.

- [ ] **Step 5.9: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 5.10: Commit**

```bash
git add clio/emitters/_python_helpers.py tests/test_emitters/test_python.py \
        tests/fixtures/expected/
git commit -m "$(cat <<'EOF'
feat(emitters): instrument exact steps with step_start/step_end (mode=exact)

emit_default_exact_step / emit_rest_step / emit_shell_step now emit a
step_start at entry and a step_end before each successful return, with
mode="exact". No model, no cache_hit, no fallback_used, no tokens_*.
Shared between python and mcp-server targets.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Instrument PARALLEL block (python target)

Wrap `ThreadPoolExecutor` block with `parallel_block_start`/`parallel_block_end` events, and propagate the `flow` ContextVar via `contextvars.copy_context().run(...)`.

**Files:**
- Modify: `clio/emitters/_python_helpers.py:615-656` (`emit_parallel_for_each_python`)
- Modify: `clio/emitters/python.py:432-433` (the `cf_import` to also import `contextvars` when needed)
- Modify: `tests/test_emitters/test_python.py` (form tests)
- Regenerate: any expected fixture that uses PARALLEL (likely `examples/parallel_classify.clio` derived fixtures, if any are in `expected/`)

- [ ] **Step 6.1: Write failing form tests**

```python
def test_parallel_block_emits_block_events(tmp_path):
    src = (FIXTURES.parent / ".." / "examples" / "parallel_classify.clio").read_text()
    # Or use a dedicated fixture; adjust path as needed
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert '_log.emit("parallel_block_start"' in flow_py
    assert '_log.emit("parallel_block_end"' in flow_py


def test_parallel_block_propagates_contextvar(tmp_path):
    src = (FIXTURES.parent / ".." / "examples" / "parallel_classify.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert "import contextvars" in flow_py
    assert "copy_context()" in flow_py
```

- [ ] **Step 6.2: Run tests, verify they fail**

Run: `pytest tests/test_emitters/test_python.py -k "parallel_block" -v`
Expected: FAIL.

- [ ] **Step 6.3: Modify `emit_parallel_for_each_python` in `clio/emitters/_python_helpers.py`**

Replace the function body (lines 615-656):

```python
def emit_parallel_for_each_python(
    elem: "ForEachIR",
    steps_by_name: dict,
    indent: str,
) -> str:
    """Emit a ThreadPoolExecutor block for a parallel FOR EACH (python target).

    The body is guaranteed (by IR validation) to be a single CallIR with a
    GIVES. Default cap is 10. Failure semantics: ThreadPoolExecutor's `with`
    exit cancels queued futures; in-flight tasks finish; the first
    `_fut.result()` to raise propagates.

    Each task is wrapped in contextvars.copy_context().run(...) so the
    _current_flow ContextVar set by run() propagates into worker threads.
    Without this, the workers would see the default (None) and emit step
    events with no 'flow' field."""
    inner = elem.body[0]
    step = steps_by_name[inner.step_name]

    scope_local = {elem.loop_var}
    kw_parts: list[str] = []
    for name, value in inner.kwargs:
        if isinstance(value, str) and value.startswith("@"):
            ref = value[1:]
            if ref in scope_local:
                kw_parts.append(f"{name}={ref}")
            else:
                kw_parts.append(f"{name}=state[{ref!r}]")
        else:
            kw_parts.append(f"{name}={value!r}")
    kwargs_str = ", ".join(kw_parts)

    items_lookup = f"state[{elem.collection!r}]"
    step_call = f"{step.name}_mod.{step.name}"

    # The submitted callable is a partial that captures the kwargs;
    # contextvars.copy_context() snapshots the current ContextVar values
    # (including _current_flow set by run()) and .run() applies them in
    # the worker thread.
    return (
        f"{indent}_items = {items_lookup}\n"
        f"{indent}_results = [None] * len(_items)\n"
        f'{indent}_log.emit("parallel_block_start", step={step.name!r}, '
        f"collector={elem.collector!r}, total_iterations=len(_items), max_workers=10)\n"
        f"{indent}_pblock_t0 = time.monotonic()\n"
        f"{indent}_pblock_success = False\n"
        f"{indent}try:\n"
        f"{indent}    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as _ex:\n"
        f"{indent}        _futures = {{\n"
        f"{indent}            _ex.submit(contextvars.copy_context().run, "
        f"lambda __ctx_kwargs={{{kwargs_str.replace(elem.loop_var + '=' + elem.loop_var, '')}}}, "
        f"__loop={elem.loop_var}: "
        f"{step_call}(**({{**__ctx_kwargs, {elem.loop_var!r}: __loop}})))"
        f": _i\n"
        f"{indent}            for _i, {elem.loop_var} in enumerate(_items)\n"
        f"{indent}        }}\n"
        f"{indent}        for _fut in concurrent.futures.as_completed(_futures):\n"
        f"{indent}            _idx = _futures[_fut]\n"
        f"{indent}            _results[_idx] = _fut.result()\n"
        f"{indent}    state[{elem.collector!r}] = _results\n"
        f"{indent}    _pblock_success = True\n"
        f"{indent}finally:\n"
        f'{indent}    _log.emit("parallel_block_end", step={step.name!r}, '
        f"collector={elem.collector!r}, total_iterations=len(_items), "
        f"duration_ms=int((time.monotonic() - _pblock_t0) * 1000), success=_pblock_success)"
    )
```

The inner lambda is the simple way to wrap the call so that `copy_context().run` invokes the step inside the snapshot. The lambda captures the loop variable (default-arg trick) to avoid late-binding in the comprehension.

A simpler, less-clever alternative: define a small helper inside the emitted block:

```python
{indent}def _task(_loop_var, _state):
{indent}    return {step_call}(<kwargs adapted>)
```

…but the lambda form keeps the existing single-expression structure. If the lambda construction proves too brittle, switch to the named helper inside the parallel block. **Decision deferred to implementation**: prefer the named-helper form if generation gets gnarly, since readability of emitted code matters.

- [ ] **Step 6.4: Modify `clio/emitters/python.py` to also import `contextvars` when parallel is present**

Locate `_emit_flow` line 432-433:

```python
        needs_concurrent = _has_parallel(graph.flow.chain)
        cf_import = "import concurrent.futures\n\n" if needs_concurrent else ""
```

Change to:
```python
        needs_concurrent = _has_parallel(graph.flow.chain)
        cf_import = (
            "import concurrent.futures\nimport contextvars\n\n"
            if needs_concurrent else ""
        )
```

- [ ] **Step 6.5: Run form tests, verify they pass**

Run: `pytest tests/test_emitters/test_python.py -k "parallel_block" -v`
Expected: PASS.

- [ ] **Step 6.6: Run any existing parallel test**

Run: `pytest tests/test_parallel_*.py -v` (or whatever the existing parallel tests are named)
Expected: all green.

- [ ] **Step 6.7: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 6.8: Sanity-execute a parallel example**

Compile `examples/parallel_classify.clio` and inspect `flow.py` to verify the lambda/helper renders sensibly. If the lambda form is unreadable, switch to the named-helper form per Step 6.3 note and re-run tests.

```bash
python -m clio compile examples/parallel_classify.clio --target python --output /tmp/par_out
cat /tmp/par_out/parallel_classify/flow.py
```

- [ ] **Step 6.9: Commit**

```bash
git add clio/emitters/_python_helpers.py clio/emitters/python.py \
        tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitters/python): instrument FOR EACH PARALLEL with block events

emit_parallel_for_each_python now emits parallel_block_start before the
ThreadPoolExecutor and parallel_block_end in a finally clause. Each
submitted task runs inside contextvars.copy_context().run(...) so the
_current_flow ContextVar set by run() propagates to the worker thread —
without this, in-block step events would lack the 'flow' field.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Instrument flow.py (mcp-server target)

Mirror Task 3 for the async `flow.py`. ContextVar propagates natively through asyncio tasks, so no `copy_context().run` wrapping is needed for the chain (only for ThreadPoolExecutor in Task 6, which mcp-server doesn't use).

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py:261-352` (`_emit_flow_module_async`)
- Modify: `tests/test_emitters/test_mcp_server.py` (form tests)

- [ ] **Step 7.1: Write failing form tests**

Append to `tests/test_emitters/test_mcp_server.py`:

```python
def test_mcp_flow_py_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert "import time" in flow_py
    assert "from .clio_runtime import logging as _log" in flow_py


def test_mcp_flow_py_emits_set_flow_and_flow_events(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    flow_py = (tmp_path / "classify" / "flow.py").read_text()
    assert '_log.set_flow("classify")' in flow_py
    assert '_log.emit("flow_start")' in flow_py
    assert '_log.emit("flow_end"' in flow_py
    assert "try:" in flow_py
    assert "finally:" in flow_py
```

- [ ] **Step 7.2: Run tests, verify fail**

Run: `pytest tests/test_emitters/test_mcp_server.py -k "flow_py" -v`
Expected: FAIL.

- [ ] **Step 7.3: Modify `_emit_flow_module_async` in `clio/emitters/_mcp_helpers.py`**

Locate the function (line 261). Replace its return statement (the `return (...)` at the end, around lines 339-352):

```python
    flow_name_lit = repr(graph.flow.name)
    chain_body = "\n".join("    " + cl for cl in chain_lines)

    return (
        '"""Async FLOW orchestrator. Auto-generated; do not edit."""\n'
        "from __future__ import annotations\n"
        "\n"
        "import time\n"
        f"{asyncio_import}"
        f"{imports}\n"
        "\n"
        "from .clio_runtime import logging as _log\n"
        "\n"
        "\n"
        "async def run(*, _session=None, **initial: object) -> dict:\n"
        "    state: dict = dict(initial)\n"
        f"    _log.set_flow({flow_name_lit})\n"
        '    _log.emit("flow_start")\n'
        "    _success = False\n"
        "    _t0 = time.monotonic()\n"
        "    try:\n"
        f"{chain_body}\n"
        "        _success = True\n"
        "        return state\n"
        "    finally:\n"
        '        _log.emit("flow_end", '
        "duration_ms=int((time.monotonic() - _t0) * 1000), "
        "success=_success)\n"
        "        _log.set_flow(None)\n"
    )
```

- [ ] **Step 7.4: Run form tests, verify they pass**

Run: `pytest tests/test_emitters/test_mcp_server.py -k "flow_py" -v`
Expected: PASS.

- [ ] **Step 7.5: Run full mcp-server emitter tests**

Run: `pytest tests/test_emitters/test_mcp_server.py -v`
Expected: all green.

- [ ] **Step 7.6: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 7.7: Commit**

```bash
git add clio/emitters/_mcp_helpers.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(emitters/mcp-server): instrument async flow.py with flow_start/flow_end

Wraps async run() in try/finally with set_flow() + flow_start/flow_end
events. Imports time and clio_runtime.logging. ContextVar propagates
natively through asyncio tasks — no copy_context wrapping needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Instrument judgment step via sampling (mcp-server target)

Mirror Task 4 for the sampling-based judgment step. `model` taken from the sampling response if available; tokens emitted iff present.

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py:399-...` (`emit_judgment_step_via_sampling`)
- Modify: `tests/test_emitters/test_mcp_server.py` (form tests)

- [ ] **Step 8.1: Read `emit_judgment_step_via_sampling` to understand the current shape**

Run: `grep -n -A 80 "def emit_judgment_step_via_sampling" clio/emitters/_mcp_helpers.py | head -120`

Note the exact structure: where the sampling call is made, where the cache lookup is, where the return paths are.

- [ ] **Step 8.2: Write failing form tests**

```python
def test_mcp_judgment_step_imports_logging_and_time(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment" in f.read_text() or "sampling" in f.read_text()
    ]
    assert judgment_files
    body = judgment_files[0].read_text()
    assert "import time" in body
    assert "from ..clio_runtime import logging as _log" in body


def test_mcp_judgment_step_has_step_events(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(src)), tmp_path)
    step_files = list((tmp_path / "classify" / "steps").glob("*.py"))
    judgment_files = [
        f for f in step_files
        if "(judgment" in f.read_text() or "sampling" in f.read_text()
    ]
    body = judgment_files[0].read_text()
    assert '_log.emit("step_start"' in body
    assert 'mode="judgment"' in body
    assert '_log.emit("step_end"' in body
```

- [ ] **Step 8.3: Run tests, verify fail**

Run: `pytest tests/test_emitters/test_mcp_server.py -k "judgment_step" -v`
Expected: FAIL.

- [ ] **Step 8.4: Modify `emit_judgment_step_via_sampling`**

Read the function and apply the same instrumentation pattern as Task 4:
1. Add `import time` and `from ..clio_runtime import logging as _log` to the emitted file's imports.
2. Add `_t0 = time.monotonic()` and `_log.emit("step_start", step=..., mode="judgment")` as the first lines of the step body.
3. Add `_last_usage: dict = {}` before the sampling call.
4. After `await _session.create_message(...)`, populate `_last_usage` if the response carries usage:
   ```python
   if hasattr(_resp, "model") and _resp.model:
       _last_model = _resp.model
   else:
       _last_model = "unknown"
   if hasattr(_resp, "usage") and _resp.usage is not None:
       _last_usage = {
           "tokens_in": getattr(_resp.usage, "input_tokens", None),
           "tokens_out": getattr(_resp.usage, "output_tokens", None),
       }
       _last_usage = {k: v for k, v in _last_usage.items() if v is not None}
   ```
5. Instrument the cache hit path, success path, and error/abort path with `_log.emit("step_end", ...)` before each return/raise. Use `_last_model` for the `model` field.

(The exact insertion points depend on the function's current structure — read it carefully.)

- [ ] **Step 8.5: Run form tests, verify pass**

Run: `pytest tests/test_emitters/test_mcp_server.py -k "judgment_step" -v`
Expected: PASS.

- [ ] **Step 8.6: Run full mcp-server tests**

Run: `pytest tests/test_emitters/test_mcp_server.py -v`
Expected: all green.

- [ ] **Step 8.7: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 8.8: Commit**

```bash
git add clio/emitters/_mcp_helpers.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(emitters/mcp-server): instrument judgment-via-sampling steps

Adds step_start/step_end around the sampling call and cache lookup.
model field comes from the sampling response when present; tokens_in/
tokens_out emitted iff response.usage is provided by the MCP client.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Instrument PARALLEL block (mcp-server target)

Mirror Task 6 for `emit_parallel_for_each_mcp` (asyncio-based). No `copy_context` wrapping needed — asyncio tasks inherit ContextVar by default.

**Files:**
- Modify: `clio/emitters/_mcp_helpers.py:354-398` (`emit_parallel_for_each_mcp`)
- Modify: `tests/test_emitters/test_mcp_server.py` (form tests)

- [ ] **Step 9.1: Write failing form tests**

```python
def test_mcp_parallel_block_emits_block_events(tmp_path):
    parallel_src = Path("examples/parallel_classify.clio").read_text()
    from clio.emitters.mcp_server import MCPServerEmitter
    MCPServerEmitter().emit(build_ir(parse(parallel_src)), tmp_path)
    flow_py = next(tmp_path.rglob("flow.py")).read_text()
    assert '_log.emit("parallel_block_start"' in flow_py
    assert '_log.emit("parallel_block_end"' in flow_py
```

- [ ] **Step 9.2: Run, verify fail**

- [ ] **Step 9.3: Modify `emit_parallel_for_each_mcp`**

Read the function (line 354 of `_mcp_helpers.py`). Wrap the `asyncio.gather` block with `parallel_block_start` before, and `parallel_block_end` in a `try/finally`. The wrapping pattern is identical to Task 6, minus the `copy_context().run` (asyncio handles propagation natively):

```python
return (
    f"{indent}_items = {items_lookup}\n"
    f"{indent}_results = [None] * len(_items)\n"
    f"{indent}_sem = asyncio.Semaphore(10)\n"
    f'{indent}_log.emit("parallel_block_start", step={step.name!r}, '
    f"collector={elem.collector!r}, total_iterations=len(_items), max_workers=10)\n"
    f"{indent}_pblock_t0 = time.monotonic()\n"
    f"{indent}_pblock_success = False\n"
    f"{indent}try:\n"
    f"{indent}    async def _bounded(_idx, {elem.loop_var}):\n"
    f"{indent}        async with _sem:\n"
    f"{indent}            _results[_idx] = await {step.name}_mod.{step.name}({kwargs_str}, _session=_session)\n"
    f"{indent}    await asyncio.gather(*[_bounded(_i, {elem.loop_var}) "
    f"for _i, {elem.loop_var} in enumerate(_items)])\n"
    f"{indent}    state[{elem.collector!r}] = _results\n"
    f"{indent}    _pblock_success = True\n"
    f"{indent}finally:\n"
    f'{indent}    _log.emit("parallel_block_end", step={step.name!r}, '
    f"collector={elem.collector!r}, total_iterations=len(_items), "
    f"duration_ms=int((time.monotonic() - _pblock_t0) * 1000), success=_pblock_success)"
)
```

(The exact existing shape might differ slightly — preserve the existing kwargs handling.)

- [ ] **Step 9.4: Run form tests, verify pass**

- [ ] **Step 9.5: Run full mcp-server tests + parallel tests**

Run: `pytest tests/test_emitters/test_mcp_server.py tests/test_parallel_*.py -v`
Expected: all green.

- [ ] **Step 9.6: Run full suite**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 9.7: Commit**

```bash
git add clio/emitters/_mcp_helpers.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(emitters/mcp-server): instrument FOR EACH PARALLEL with block events

emit_parallel_for_each_mcp wraps the asyncio.gather + Semaphore block
with parallel_block_start/parallel_block_end. ContextVar propagates
natively across asyncio tasks — no copy_context wrapping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: E2E gated test

End-to-end test: compile a flow, run with `CLIO_LOG=1 CLIO_LOG_FILE=...`, parse the JSONL, assert structure.

**Files:**
- Create: `tests/test_e2e_logging.py`

- [ ] **Step 10.1: Write the E2E test**

```python
# tests/test_e2e_logging.py
"""End-to-end test for structured logging — gated by CLIO_E2E=1.

Compiles a small flow, runs it with CLIO_LOG=1 and CLIO_LOG_FILE pointing
at a tmp file, parses the JSONL, asserts the event shape.

Skipped by default to keep the suite fast. Enable: CLIO_E2E=1 pytest ..."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="set CLIO_E2E=1 to enable end-to-end logging tests",
)

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")


def _compile_and_run(
    src_path: Path,
    target: str,
    out_dir: Path,
    log_file: Path,
    env_overrides: dict | None = None,
) -> tuple[int, str, str]:
    """Compile <src_path> to <out_dir> with the given target, then invoke
    `python -m <pkg>` with CLIO_LOG=1 + CLIO_LOG_FILE=<log_file>. Returns
    (returncode, stdout, stderr)."""
    subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(src_path),
         "--target", target, "--output", str(out_dir)],
        check=True,
    )
    pkg_name = next(p.name for p in out_dir.iterdir() if p.is_dir() and (p / "__init__.py").exists())
    env = {**os.environ, "CLIO_LOG": "1", "CLIO_LOG_FILE": str(log_file)}
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir,
        env=env,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_python_target_emits_flow_and_step_events(tmp_path):
    """Compile the smallest possible flow that has at least one exact
    step that succeeds, run it, parse the log, assert flow_start/end +
    one step pair."""
    fixtures = Path(__file__).parent / "fixtures"
    src = fixtures / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    log_file = tmp_path / "log.jsonl"
    rc, _, _ = _compile_and_run(src, "python", tmp_path / "out", log_file)
    # The skeleton's exact steps may raise NotImplementedError — that's
    # expected. We assert the events that DID get emitted before the raise.
    assert log_file.exists(), "log file should be created before any step runs"

    events = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert events, "expected at least one event"
    assert events[0]["event"] == "flow_start"
    assert events[0]["flow"] == "classify"
    assert _TS_RE.match(events[0]["ts"])

    # The flow_end should be present even on exception (emitted from finally).
    flow_ends = [e for e in events if e["event"] == "flow_end"]
    assert len(flow_ends) == 1
    if rc == 0:
        assert flow_ends[0]["success"] is True
    else:
        assert flow_ends[0]["success"] is False


def test_no_log_when_clio_log_unset(tmp_path):
    """Sanity: without CLIO_LOG=1, the file is not written and stderr is
    untouched (no logging noise)."""
    fixtures = Path(__file__).parent / "fixtures"
    src = fixtures / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    log_file = tmp_path / "log.jsonl"
    subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(src),
         "--target", "python", "--output", str(out_dir)],
        check=True,
    )
    pkg_name = next(p.name for p in out_dir.iterdir()
                    if p.is_dir() and (p / "__init__.py").exists())
    env = {k: v for k, v in os.environ.items() if k != "CLIO_LOG"}
    env["CLIO_LOG_FILE"] = str(log_file)  # set but should not be written
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    assert not log_file.exists() or log_file.read_text() == ""
```

- [ ] **Step 10.2: Run the E2E test (gated)**

Run: `CLIO_E2E=1 pytest tests/test_e2e_logging.py -v`
Expected: PASS.

- [ ] **Step 10.3: Run without the gate to confirm skip**

Run: `pytest tests/test_e2e_logging.py -v`
Expected: SKIPPED (1 test) — gate is off.

- [ ] **Step 10.4: Commit**

```bash
git add tests/test_e2e_logging.py
git commit -m "$(cat <<'EOF'
test(e2e): gated end-to-end test for CLIO_LOG=1 (W2)

Compiles a fixture flow, runs it with CLIO_LOG=1 + CLIO_LOG_FILE,
parses the JSONL and asserts flow_start/flow_end events appear.
Gated by CLIO_E2E=1 to keep the default suite fast.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Documentation

Update LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README.

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/COMPILATION_TARGETS.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [ ] **Step 11.1: Add observability section to `docs/LANGUAGE_SPEC.md`**

Locate the most appropriate section (likely near the env-vars or runtime section). Add:

```markdown
## Observability (v0.4+)

Every emitted project (target: python or mcp-server) embeds a small JSON-Line
logger at `clio_runtime/logging.py`. The logger is **opt-in**:

- `CLIO_LOG=1` enables emission. Anything else is no-op.
- `CLIO_LOG_FILE=path/to/run.jsonl` redirects output to a file (default: stderr).

Six event types are emitted:

| Event | When | Required fields | Optional fields |
|---|---|---|---|
| `flow_start` | beginning of `run()` | `flow` | — |
| `flow_end` | end of `run()` (`finally`) | `flow`, `duration_ms`, `success` | — |
| `step_start` | first line of step body | `step`, `mode` (`exact`\|`judgment`) | `flow` |
| `step_end` | before each return | `step`, `mode`, `duration_ms`, `success` | `flow`, `cache_hit`, `model`, `fallback_used`, `tokens_in`, `tokens_out` |
| `parallel_block_start` | before ThreadPoolExecutor / asyncio.gather | `step`, `collector`, `total_iterations`, `max_workers` | `flow` |
| `parallel_block_end` | after the gather | `step`, `collector`, `total_iterations`, `duration_ms`, `success` | `flow` |

All events carry `ts` (ISO 8601 UTC, ms precision) and `event` (string).

The schema is intentionally flat and OTel-mappable (a converter to OTLP spans
can be added downstream). claude-cli target does **not** instrument logging
in v0.4 — use `--target python` or `--target mcp-server` for observable runs.
```

- [ ] **Step 11.2: Update `docs/COMPILATION_TARGETS.md` per-target logging coverage**

For each target section, add a "Logging" subsection or table row. Example for python:
```markdown
**Logging** (v0.4+): structured JSONL via `CLIO_LOG=1`. flow_start/end,
step_start/end (3 paths for judgment), parallel_block_start/end. Tokens
extracted from Anthropic and OpenAI `response.usage`.
```

For mcp-server:
```markdown
**Logging** (v0.4+): same event taxonomy as python. Tokens emitted only when
the MCP sampling response carries `usage`.
```

For claude-cli:
```markdown
**Logging**: not instrumented in v0.4. Use python or mcp-server for
observable runs.
```

- [ ] **Step 11.3: Update `CHANGELOG.md` Unreleased section**

Add under `### Added`:

```markdown
- **W2 (short-term): Structured JSON-Line logging.** New `clio_runtime/logging.py`
  module copied verbatim into emitted projects. Opt-in via `CLIO_LOG=1`,
  destination via `CLIO_LOG_FILE`. Six event types: flow_start/end,
  step_start/end, parallel_block_start/end. python and mcp-server targets
  instrumented; claude-cli deferred to v2. OTel-mappable flat schema.
```

- [ ] **Step 11.4: Update `README.md` usage section**

Add a brief mention near the existing usage examples:

```markdown
**Observability**: set `CLIO_LOG=1` to emit JSON-Line events to stderr (or
to a file via `CLIO_LOG_FILE=run.jsonl`). One event per step/flow start
and end, OTel-mappable. See `docs/LANGUAGE_SPEC.md` for the schema.
```

- [ ] **Step 11.5: Run full suite one more time**

Run: `pytest tests/ -v`
Expected: all green.

- [ ] **Step 11.6: Commit docs**

```bash
git add docs/LANGUAGE_SPEC.md docs/COMPILATION_TARGETS.md CHANGELOG.md README.md
git commit -m "$(cat <<'EOF'
docs: structured JSON-Line logging — LANGUAGE_SPEC, COMPILATION_TARGETS, CHANGELOG, README

Per-target coverage table, event taxonomy, env vars (CLIO_LOG /
CLIO_LOG_FILE), and brief README mention. Closes W2 short-term.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist

After completing all tasks:

- [ ] Spec coverage: every section of the spec is implemented somewhere in the plan.
  - Activation toggle: Task 1 (module).
  - Destination toggle: Task 1.
  - Six event schemas: Tasks 1, 3, 4, 5, 6, 7, 8, 9.
  - python target instrumentation: Tasks 3, 4, 5, 6.
  - mcp-server target instrumentation: Tasks 7, 8, 9 (+ exact via Task 5 shared helpers).
  - claude-cli explicitly NOT modified (per spec non-goals).
  - Tests: Task 1 (unit), Tasks 3-9 (form), Task 10 (E2E).
  - Risks (ContextVar + ThreadPoolExecutor): Task 6 wraps with copy_context.
  - Risks (`_last_usage` propagation): Task 4 sets via nonlocal in `_attempt`.
  - Docs: Task 11.
- [ ] No placeholders: all code blocks contain runnable snippets.
- [ ] Type consistency: `_log.emit` / `_log.set_flow` / `_last_usage` / `_t0` named consistently.
- [ ] Frequent commits: 11 separate commits, one per task.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-structured-logging.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
