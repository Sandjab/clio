"""End-to-end tests: compile mvp.clio, run output/run.sh, validate state.

Both tests gated by CLIO_E2E=1. The `_real_claude` test invokes actual
`claude -p`. The `_cached_replay` test uses a `claude` stub from the
fixtures dir to verify cache-hit behavior without touching the API.
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
LOAD_FIX = REPO_ROOT / "tests" / "fixtures" / "load_customers_real.py"
NAIVE_FIX_SRC = """import argparse, json, sys
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "state.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--customers", required=True)
    args = parser.parse_args()
    customers = json.loads(args.customers)
    risks = []
    for c in customers:
        rev = c["revenue"]
        if rev < 1000:
            risk = "high"
        elif rev < 10000:
            risk = "mid"
        else:
            risk = "low"
        risks.append({"client": c["name"], "risk": risk,
                      "reason": f"heuristic: revenue={rev}"})
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    state["risks"] = risks
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

CLAUDE_STUB = REPO_ROOT / "tests" / "fixtures" / "claude_stub.sh"


def _patch_steps(out_dir: Path) -> None:
    (out_dir / "steps" / "01_load_customers.py").write_text(LOAD_FIX.read_text())
    naive = out_dir / "steps" / "02_detect_churn_naive.py"
    if naive.exists():
        naive.write_text(NAIVE_FIX_SRC)


def _compile(out: Path) -> None:
    rc = subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(EXAMPLE),
         "--target", "claude-cli", "--output", str(out)],
        check=False,
    ).returncode
    assert rc == 0


def _run(out: Path, env_extra: dict | None = None):
    env = {**os.environ, "PYTHON": sys.executable}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(out / "run.sh")],
        cwd=out, capture_output=True, text=True, env=env,
    )


def test_real_claude_then_cache_hit(tmp_path):
    out = tmp_path / "out"
    _compile(out)
    shutil.copy(SAMPLE_CSV, out / "customers.csv")
    _patch_steps(out)

    # Run 1: real claude
    proc1 = _run(out)
    assert proc1.returncode == 0, f"run1 failed:\n{proc1.stderr}"
    state1 = json.loads((out / "state.json").read_text())
    assert isinstance(state1.get("risks"), list) and state1["risks"]

    # Run 2: claude stub that records calls. Because the cache is fresh,
    # zero calls should be recorded.
    log = tmp_path / "claude_calls.log"
    log.write_text("")
    # Override `claude` on PATH by prepending a dir containing only the stub
    # symlinked to the name `claude`.
    stub_dir = tmp_path / "stubdir"
    stub_dir.mkdir()
    (stub_dir / "claude").symlink_to(CLAUDE_STUB)

    # Reset state.json so step 1 re-runs (cheap; doesn't touch API).
    (out / "state.json").write_text("{}")

    proc2 = _run(out, env_extra={
        "PATH": f"{stub_dir}:{os.environ['PATH']}",
        "CLIO_TEST_CLAUDE_LOG": str(log),
    })
    assert proc2.returncode == 0, f"run2 failed:\n{proc2.stderr}"
    state2 = json.loads((out / "state.json").read_text())
    assert state2["risks"] == state1["risks"], "cache replay should give identical output"
    assert log.read_text() == "", (
        f"expected zero claude invocations on cache hit, got: {log.read_text()!r}"
    )
