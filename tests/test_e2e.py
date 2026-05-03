"""End-to-end test: compile mvp.clio, run output/run.sh against real claude -p, validate.

Gated by `CLIO_E2E=1` because it requires:
  - `claude` (Claude Code CLI) authenticated on the host
  - network access
  - non-zero API cost

Run manually before declaring v0.1 done. CI does not run this test.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="CLIO_E2E=1 not set",
)


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "mvp.clio"
SAMPLE_CSV = REPO_ROOT / "examples" / "customers.csv"


def _patch_load_customers(out_dir: Path) -> None:
    """Replace the echo body with a real CSV parser."""
    target = out_dir / "steps" / "01_load_customers.py"
    target.write_text((REPO_ROOT / "tests" / "fixtures" / "load_customers_real.py").read_text())


def test_compile_and_run_mvp(tmp_path):
    out = tmp_path / "out"
    rc = subprocess.run(
        [
            sys.executable, "-m", "clio", "compile",
            str(EXAMPLE),
            "--target", "claude-cli",
            "--output", str(out),
        ],
        check=False,
    ).returncode
    assert rc == 0

    shutil.copy(SAMPLE_CSV, out / "customers.csv")
    _patch_load_customers(out)

    proc = subprocess.run(
        ["bash", str(out / "run.sh")],
        cwd=out,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON": sys.executable},
    )
    assert proc.returncode == 0, f"run.sh failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"

    state = json.loads((out / "state.json").read_text())
    assert "risks" in state
    risks = state["risks"]
    assert isinstance(risks, list)
    assert len(risks) > 0

    for r in risks:
        assert set(r) >= {"client", "risk", "reason"}
        assert r["risk"] in {"low", "mid", "high"}
        assert isinstance(r["reason"], str) and len(r["reason"]) > 0
        assert len(r["reason"]) <= 300
