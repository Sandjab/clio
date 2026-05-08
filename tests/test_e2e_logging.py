"""End-to-end test for structured logging — gated by CLIO_E2E=1.

Compiles a small flow, runs it with CLIO_LOG=1 + CLIO_LOG_FILE pointing
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
_FIXTURES = Path(__file__).parent / "fixtures"


def _compile(src_path: Path, target: str, out_dir: Path) -> str:
    """Compile <src_path> with the given target, return the emitted package name."""
    subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(src_path),
         "--target", target, "--output", str(out_dir)],
        check=True,
    )
    pkg_name = next(
        p.name for p in out_dir.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    )
    return pkg_name


def _run_emitted_pkg(
    out_dir: Path,
    pkg_name: str,
    env_overrides: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke `python -m <pkg>` from inside out_dir."""
    env = {**os.environ}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )


def test_python_target_emits_flow_events_with_clio_log(tmp_path):
    """Compile the smallest possible flow, run it with CLIO_LOG=1,
    parse the log, assert flow_start/flow_end events exist."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    log_file = tmp_path / "log.jsonl"
    pkg_name = _compile(src, "python", out_dir)

    proc = _run_emitted_pkg(
        out_dir, pkg_name,
        env_overrides={"CLIO_LOG": "1", "CLIO_LOG_FILE": str(log_file)},
    )
    # The skeleton's exact steps raise NotImplementedError by default.
    # That's fine — flow_end is emitted from finally, so we still get events.
    assert log_file.exists(), (
        f"log file should be created (rc={proc.returncode}, stderr={proc.stderr!r})"
    )

    events = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    assert events, "expected at least one event"

    # First event must be flow_start with the right flow name.
    assert events[0]["event"] == "flow_start"
    assert events[0]["flow"] == "classify"
    assert _TS_RE.match(events[0]["ts"])

    # flow_end is emitted from finally — present even when steps raised.
    flow_ends = [e for e in events if e["event"] == "flow_end"]
    assert len(flow_ends) == 1
    fe = flow_ends[0]
    assert "duration_ms" in fe
    assert "success" in fe
    if proc.returncode == 0:
        assert fe["success"] is True
    else:
        # Step raised; flow_end records the failure but logging itself worked.
        assert fe["success"] is False


def test_no_log_when_clio_log_unset(tmp_path):
    """Without CLIO_LOG=1, no log file is written even if CLIO_LOG_FILE is set."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    log_file = tmp_path / "log.jsonl"
    pkg_name = _compile(src, "python", out_dir)

    # Set CLIO_LOG_FILE but leave CLIO_LOG unset (env_overrides explicitly omits it).
    env_overrides = {"CLIO_LOG_FILE": str(log_file)}
    # Make sure CLIO_LOG is absent — start from a clean env.
    env = {k: v for k, v in os.environ.items() if k != "CLIO_LOG"}
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )

    # No log file should exist — the emit() in clio_runtime/logging.py is a no-op.
    assert not log_file.exists() or log_file.read_text() == "", (
        f"log_file unexpectedly written: {log_file.read_text()[:200]!r}"
    )


def test_clio_log_zero_is_no_op(tmp_path):
    """CLIO_LOG=0 (not '1') is also a no-op."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    log_file = tmp_path / "log.jsonl"
    pkg_name = _compile(src, "python", out_dir)

    proc = _run_emitted_pkg(
        out_dir, pkg_name,
        env_overrides={"CLIO_LOG": "0", "CLIO_LOG_FILE": str(log_file)},
    )

    assert not log_file.exists() or log_file.read_text() == ""
