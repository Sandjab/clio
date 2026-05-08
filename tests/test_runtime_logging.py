"""Unit tests for clio.runtime.logging — the module copied verbatim into
emitted projects as clio_runtime/logging.py."""
from __future__ import annotations

import asyncio
import json
import re
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
        await asyncio.sleep(0)  # yield — force interleaving with the other task
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
    src = Path(L.__file__)
    assert src.name == "logging.py"
    assert src.parent.name == "runtime"
