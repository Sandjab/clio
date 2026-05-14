"""Diagnostic helpers for `clio status` and `clio doctor`.

`status` reads a python-target run's `state.json` and optional `CLIO_LOG`
JSONL log file to summarise the latest run.

`doctor` runs a series of environment checks. When a `.clio` source is
supplied, additional checks are derived from its RESOURCES block
(MCP servers reachable, DB URLs parsable, ANTHROPIC_API_KEY present for
flows that need it).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# v0.15 — required Python version for the python target.
_REQUIRED_PYTHON = (3, 12)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str          # "pass" | "warn" | "fail"
    detail: str


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def status_summary(
    state_file: Path | None = None,
    log_file: Path | None = None,
    limit: int = 10,
) -> str:
    """Return a human-readable status report.

    Looks for `state.json` (cwd or `CLIO_STATE_FILE`) and the JSONL log
    (`CLIO_LOG_FILE` or argument). Missing files are reported, not raised.
    """
    out: list[str] = []

    sf = state_file or Path(os.environ.get("CLIO_STATE_FILE", "state.json"))
    out.append(f"state file: {sf}")
    if sf.exists():
        try:
            data = json.loads(sf.read_text())
            out.append(f"  flow:           {data.get('flow', '<unknown>')}")
            out.append(f"  step_index:     {data.get('step_index', '<unknown>')}")
            state = data.get("state", {})
            if isinstance(state, dict):
                out.append(f"  state fields:   {len(state)} "
                           f"({', '.join(sorted(state)[:8])}"
                           f"{'...' if len(state) > 8 else ''})")
            else:
                out.append("  state:          <not a dict>")
        except json.JSONDecodeError as e:
            out.append(f"  <invalid JSON: {e}>")
    else:
        out.append("  <missing — has the flow been run yet?>")

    lf = log_file or (Path(os.environ["CLIO_LOG_FILE"])
                      if "CLIO_LOG_FILE" in os.environ else None)
    out.append("")
    out.append(f"log file:   {lf if lf else '<unset — set CLIO_LOG_FILE to capture events>'}")
    if lf and lf.exists():
        events = _tail_jsonl(lf, limit)
        if not events:
            out.append("  <no events>")
        else:
            out.append(f"  last {len(events)} event(s):")
            for ev in events:
                ts = ev.get("ts", "")
                kind = ev.get("event", "?")
                extras = []
                for k in ("flow", "step", "mode", "success", "duration_ms"):
                    if k in ev:
                        extras.append(f"{k}={ev[k]}")
                out.append(f"    {ts}  {kind}  {' '.join(extras)}")
    elif lf:
        out.append("  <file does not exist>")

    return "\n".join(out) + "\n"


def _tail_jsonl(path: Path, limit: int) -> list[dict]:
    """Return the last `limit` JSON lines, oldest-first. Skips malformed."""
    lines: list[dict] = []
    try:
        with path.open() as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return lines[-limit:]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def run_doctor(source: Path | None) -> tuple[int, str]:
    """Run environment checks. If `source` is set, also derive flow-specific
    checks from its RESOURCES block. Returns (exit_code, report_text).
    """
    checks: list[CheckResult] = []

    checks.append(_check_python_version())
    checks.append(_check_anthropic_sdk())

    if source is not None:
        if not source.exists():
            checks.append(CheckResult(
                name="source file",
                status="fail",
                detail=f"not found: {source}",
            ))
            return checks_exit_code(checks), _format_doctor(checks)
        # Lazy import to keep `clio status` cheap.
        from clio.ir.builder import IRBuildError, build_ir
        from clio.parser.parser import ParseError, parse
        try:
            graph = build_ir(parse(source.read_text()))
        except (ParseError, IRBuildError) as e:
            checks.append(CheckResult(
                name="source compiles",
                status="fail",
                detail=f"{source.name}:{e}",
            ))
            return checks_exit_code(checks), _format_doctor(checks)
        checks.append(CheckResult(
            name="source compiles",
            status="pass",
            detail=f"{source.name} — {len(graph.steps)} step(s)",
        ))
        checks.extend(_checks_from_graph(graph))
    else:
        checks.append(CheckResult(
            name="ANTHROPIC_API_KEY",
            status=("pass" if os.environ.get("ANTHROPIC_API_KEY") else "warn"),
            detail=("set" if os.environ.get("ANTHROPIC_API_KEY")
                    else "unset (required when running a flow with judgment steps)"),
        ))

    return checks_exit_code(checks), _format_doctor(checks)


def _check_python_version() -> CheckResult:
    cur = sys.version_info[:2]
    if cur >= _REQUIRED_PYTHON:
        return CheckResult("python version", "pass", f"{cur[0]}.{cur[1]}")
    return CheckResult(
        "python version", "fail",
        f"{cur[0]}.{cur[1]} (need >= {_REQUIRED_PYTHON[0]}.{_REQUIRED_PYTHON[1]})",
    )


def _check_anthropic_sdk() -> CheckResult:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return CheckResult(
            "anthropic SDK", "warn",
            "not installed (pip install anthropic — needed for python/gen targets)",
        )
    return CheckResult("anthropic SDK", "pass", "importable")


def _checks_from_graph(graph) -> list[CheckResult]:
    out: list[CheckResult] = []

    has_judgment = any(s.mode == "judgment" for s in graph.steps)
    if has_judgment:
        out.append(CheckResult(
            "ANTHROPIC_API_KEY", ("pass" if os.environ.get("ANTHROPIC_API_KEY") else "fail"),
            "set" if os.environ.get("ANTHROPIC_API_KEY")
            else "unset (flow has judgment steps that need it)",
        ))

    if graph.resources:
        for srv in graph.resources.mcp_servers:
            out.append(_check_mcp_server(srv))
        for db in graph.resources.databases:
            out.append(_check_database(db))

    return out


def _check_mcp_server(srv) -> CheckResult:
    name = getattr(srv, "name", "<unnamed>")
    # Stdio: check command exists on PATH.
    if hasattr(srv, "command"):
        cmd = srv.command
        if shutil.which(cmd):
            return CheckResult(f"mcp_server[{name}]", "pass", f"command on PATH: {cmd}")
        return CheckResult(f"mcp_server[{name}]", "warn",
                           f"command not on PATH at doctor time: {cmd}")
    # HTTP/SSE: check URL parses.
    if hasattr(srv, "url"):
        url = srv.url
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return CheckResult(f"mcp_server[{name}]", "pass", f"url parses: {url}")
        return CheckResult(f"mcp_server[{name}]", "fail", f"invalid url: {url}")
    return CheckResult(f"mcp_server[{name}]", "warn", "unknown server spec shape")


def _check_database(db) -> CheckResult:
    name = getattr(db, "name", "<unnamed>")
    url = getattr(db, "url", "")
    driver = getattr(db, "driver", "?")
    if url.startswith("env:"):
        var = url[4:]
        if os.environ.get(var):
            return CheckResult(f"database[{name}]", "pass", f"{driver}, {var} set")
        return CheckResult(f"database[{name}]", "fail",
                           f"{driver}, env var {var} unset")
    if driver == "sqlite":
        # sqlite path doesn't need to exist; it's created on first write.
        return CheckResult(f"database[{name}]", "pass", f"sqlite, path={url}")
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return CheckResult(f"database[{name}]", "pass", f"{driver}, url parses")
    return CheckResult(f"database[{name}]", "fail", f"{driver}, invalid url: {url}")


def _format_doctor(checks: Iterable[CheckResult]) -> str:
    symbols = {"pass": "OK  ", "warn": "WARN", "fail": "FAIL"}
    lines = ["clio doctor", "-" * 60]
    for c in checks:
        lines.append(f"  [{symbols[c.status]}]  {c.name:<24}  {c.detail}")
    return "\n".join(lines) + "\n"


def checks_exit_code(checks: Iterable[CheckResult]) -> int:
    if any(c.status == "fail" for c in checks):
        return 1
    return 0
