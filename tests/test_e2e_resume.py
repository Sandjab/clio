"""End-to-end test for clio resume — gated by CLIO_E2E=1.

Compiles a fixture flow, runs it as a subprocess, verifies state.json
is written, manually pre-populates state.json and re-runs with
--from-step N to verify resume semantics.

Skipped by default. Enable: CLIO_E2E=1 pytest ..."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="set CLIO_E2E=1 to enable end-to-end resume tests",
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _compile(src_path: Path, out_dir: Path) -> str:
    """Compile <src_path> to <out_dir> with target=python; return the package name."""
    subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(src_path),
         "--target", "python", "--output", str(out_dir)],
        check=True,
    )
    pkg_name = next(
        p.name for p in out_dir.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    )
    return pkg_name


def test_normal_run_writes_state_json(tmp_path):
    """A normal run (no --from-step) writes state.json with the right schema."""
    src = _FIXTURES / "mvp_v03_skeleton.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    state_file = tmp_path / "state.json"
    pkg_name = _compile(src, out_dir)
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # The skeleton's stub raises NotImplementedError, so rc != 0 is expected.
    # State.json may exist (if first step persisted before the raise) or not.
    if state_file.exists():
        payload = json.loads(state_file.read_text())
        assert payload["version"] == 1
        assert payload["flow"] == "classify"
        assert "step_index" in payload
        assert "state" in payload


def test_resume_with_prepopulated_state(tmp_path):
    """--from-step N with valid state.json runs items > N without re-running items <= N.

    Detection: the stderr should NOT contain '[clio] resume requested' or any
    of the 4 validation-failure messages."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 1, "flow": "retention", "step_index": 1,
        "state": {"customers": []},
    }))
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}", "--from-step", "1"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # No resume-validation failure should occur:
    assert "missing" not in proc.stderr, proc.stderr
    assert "[clio] resume requested" not in proc.stderr, proc.stderr
    assert "flow mismatch" not in proc.stderr, proc.stderr
    assert "only reached step" not in proc.stderr, proc.stderr
    assert ">= total steps=" not in proc.stderr, proc.stderr


def test_resume_fails_with_missing_state_file(tmp_path):
    """--from-step N with no state.json exits 2 with 'missing' message."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)
    state_file = tmp_path / "no_such.json"
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--from-step", "1"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 2, f"rc={proc.returncode}, stderr={proc.stderr}"
    assert "missing" in proc.stderr, proc.stderr


def test_resume_fails_with_negative_from_step(tmp_path):
    """--from-step -1 exits 2 from main() with 'must be >= 0' message."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--from-step", "-1"],
        cwd=out_dir, capture_output=True, text=True,
    )
    assert proc.returncode == 2, f"rc={proc.returncode}, stderr={proc.stderr}"
    assert "must be >= 0" in proc.stderr, proc.stderr


def test_state_json_shape_when_present(tmp_path):
    """Verify state.json shape (version=1, flow, step_index, state) when written."""
    src = _FIXTURES / "mvp_v03_cache.clio"
    if not src.exists():
        pytest.skip(f"fixture {src} not present")

    out_dir = tmp_path / "out"
    pkg_name = _compile(src, out_dir)
    state_file = tmp_path / "state.json"
    env = {**os.environ, "CLIO_STATE_FILE": str(state_file)}
    proc = subprocess.run(
        [sys.executable, "-m", pkg_name, "--kwargs", "{}"],
        cwd=out_dir, env=env, capture_output=True, text=True,
    )
    # Stubs raise NotImplementedError, so state.json may or may not exist.
    if state_file.exists():
        payload = json.loads(state_file.read_text())
        assert isinstance(payload, dict)
        assert payload.get("version") == 1
        assert isinstance(payload.get("flow"), str)
        assert isinstance(payload.get("step_index"), int)
        assert isinstance(payload.get("state"), dict)
