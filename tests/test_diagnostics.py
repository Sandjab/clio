"""Unit tests for `clio.diagnostics`.

Covers the per-resource doctor checks (`_check_mcp_server`, `_check_database`)
and the `status_summary` reader. The previous suite (v0.15) only exercised
these indirectly through CLI smoke tests — coverage was 68%; this file pulls
it up by hitting each branch directly with lightweight stubs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from clio.diagnostics import (
    CheckResult,
    _check_database,
    _check_mcp_server,
    checks_exit_code,
    status_summary,
)


# ---------- _check_mcp_server -----------------------------------------------


@dataclass
class _StdioSrv:
    name: str
    command: str


@dataclass
class _RemoteSrv:
    name: str
    url: str


@dataclass
class _UnknownSrv:
    name: str


def test_check_mcp_server_stdio_command_on_path():
    """A stdio server whose command resolves via shutil.which → pass."""
    # `sh` is on every POSIX/macOS box CI runs on.
    r = _check_mcp_server(_StdioSrv(name="docs", command="sh"))
    assert r.status == "pass"
    assert "docs" in r.name


def test_check_mcp_server_stdio_command_missing(monkeypatch):
    """A stdio command that's not on PATH → warn (not fail — it could be
    installed by deploy-time tooling)."""
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    r = _check_mcp_server(_StdioSrv(name="docs", command="nonexistent-mcp-binary"))
    assert r.status == "warn"
    assert "not on PATH" in r.detail


def test_check_mcp_server_remote_url_parses():
    r = _check_mcp_server(_RemoteSrv(name="remote", url="https://api.example.com/mcp"))
    assert r.status == "pass"
    assert "url parses" in r.detail


def test_check_mcp_server_remote_invalid_url():
    r = _check_mcp_server(_RemoteSrv(name="bad", url="not a url"))
    assert r.status == "fail"
    assert "invalid url" in r.detail


def test_check_mcp_server_unknown_shape():
    r = _check_mcp_server(_UnknownSrv(name="weird"))
    assert r.status == "warn"
    assert "unknown" in r.detail


# ---------- _check_database -------------------------------------------------


@dataclass
class _Db:
    name: str
    driver: str
    url: str


def test_check_database_env_url_present(monkeypatch):
    monkeypatch.setenv("CRM_DB_URL", "postgresql://u:p@h/d")
    r = _check_database(_Db(name="crm", driver="postgres", url="env:CRM_DB_URL"))
    assert r.status == "pass"
    assert "CRM_DB_URL set" in r.detail


def test_check_database_env_url_missing(monkeypatch):
    monkeypatch.delenv("UNSET_DB_URL_FOR_TEST", raising=False)
    r = _check_database(_Db(name="crm", driver="postgres", url="env:UNSET_DB_URL_FOR_TEST"))
    assert r.status == "fail"
    assert "UNSET_DB_URL_FOR_TEST unset" in r.detail


def test_check_database_sqlite_local_path_passes_without_existing():
    """SQLite path doesn't need to exist — it's created on first write."""
    r = _check_database(_Db(name="local", driver="sqlite", url="./does-not-exist.db"))
    assert r.status == "pass"
    assert "sqlite" in r.detail


def test_check_database_remote_url_parses():
    r = _check_database(_Db(name="warehouse", driver="postgres",
                             url="postgresql://user:pass@host:5432/db"))
    assert r.status == "pass"
    assert "url parses" in r.detail


def test_check_database_invalid_url():
    r = _check_database(_Db(name="bad", driver="mysql", url="bad-string"))
    assert r.status == "fail"
    assert "invalid url" in r.detail


# ---------- checks_exit_code ------------------------------------------------


def test_checks_exit_code_zero_on_all_pass():
    checks = [
        CheckResult(name="x", status="pass", detail=""),
        CheckResult(name="y", status="pass", detail=""),
    ]
    assert checks_exit_code(checks) == 0


def test_checks_exit_code_zero_on_warn_only():
    """Warnings don't fail the doctor — only `fail` does. Matches the v0.15
    contract: `doctor` exits 1 iff any check is FAIL."""
    checks = [
        CheckResult(name="x", status="pass", detail=""),
        CheckResult(name="y", status="warn", detail=""),
    ]
    assert checks_exit_code(checks) == 0


def test_checks_exit_code_one_on_any_fail():
    checks = [
        CheckResult(name="x", status="pass", detail=""),
        CheckResult(name="y", status="fail", detail=""),
        CheckResult(name="z", status="warn", detail=""),
    ]
    assert checks_exit_code(checks) == 1


# ---------- status_summary --------------------------------------------------


def test_status_summary_missing_state_file_reports_missing(tmp_path):
    """Calling status with no state.json should report it gracefully —
    not raise. Same idea for the log file."""
    out = status_summary(state_file=tmp_path / "nope.json",
                          log_file=tmp_path / "no.jsonl", limit=10)
    assert isinstance(out, str)
    assert "missing" in out


def test_status_summary_reads_state_and_tails_events(tmp_path):
    state = tmp_path / "state.json"
    # Real shape written by the python emitter (clio/emitters/python.py:801):
    # `{version, flow, step_index, state: {...}}`.
    state.write_text(json.dumps({
        "version": "v0.15.0",
        "flow": "p",
        "step_index": 2,
        "state": {"load": [1, 2, 3], "summary": "ok"},
    }))
    log = tmp_path / "events.jsonl"
    # 5 lines, tail last 3 with limit=3.
    log.write_text(
        "\n".join(json.dumps({"event": "step_start", "step": f"s{i}"})
                  for i in range(5)) + "\n"
    )
    out = status_summary(state_file=state, log_file=log, limit=3)
    # Tail honoured: only s2/s3/s4 should appear.
    assert "s4" in out
    assert "s0" not in out
    # state.json summary surfaces both fields (count + names).
    assert "state fields:   2" in out
    assert "load" in out
    assert "summary" in out


def test_status_summary_handles_malformed_state_file(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not valid json")
    out = status_summary(state_file=state, log_file=None, limit=5)
    assert "invalid JSON" in out


def test_status_summary_skips_malformed_log_lines(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text(
        json.dumps({"event": "step_start", "step": "ok"}) + "\n"
        "{not valid}\n"  # malformed — must be skipped silently
        + json.dumps({"event": "step_end", "step": "ok"}) + "\n"
    )
    out = status_summary(state_file=None, log_file=log, limit=10)
    # Both valid events show up despite the bad line in the middle.
    assert "step_start" in out
    assert "step_end" in out
