"""E2E timing test for FOR EACH PARALLEL on the python target."""
import os
import subprocess
import sys

import pytest

from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


@pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="parallelism timing test gated; set CLIO_E2E=1 to run",
)
def test_python_parallel_runs_actually_in_parallel(tmp_path):
    """Compile a flow whose body step sleeps 100ms. With N=5 items and
    PARALLEL, wall-clock should be ~100ms, not ~500ms."""
    src = (
        "STEP load\n  GIVES: items: List<str>\n  MODE: exact\n"
        "STEP slow\n  TAKES: x: str\n  GIVES: r: str\n  MODE: exact\n"
        "FLOW pipe\n"
        "  load()\n"
        "    -> FOR EACH item IN items PARALLEL AS results:\n"
        "         slow(x=item)\n"
    )
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)

    # Provide implementations.
    (tmp_path / "pipe" / "steps" / "load.py").write_text(
        "def load() -> list:\n"
        "    return ['a', 'b', 'c', 'd', 'e']\n"
    )
    (tmp_path / "pipe" / "steps" / "slow.py").write_text(
        "import time\n"
        "def slow(*, x: str) -> str:\n"
        "    time.sleep(0.1)\n"
        "    return x.upper()\n"
    )

    # Install and run as a subprocess to isolate from our test env.
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(tmp_path)],
    )
    runner = (
        "import time\n"
        "from pipe.flow import run\n"
        "t0 = time.monotonic()\n"
        "result = run()\n"
        "elapsed = time.monotonic() - t0\n"
        "print(f'elapsed: {elapsed:.3f}')\n"
        "print(f'results: {sorted(result[\"results\"])}')\n"
        "assert elapsed < 0.4, f'expected <400ms wall-clock, got {elapsed:.3f}s'\n"
        "assert sorted(result['results']) == ['A', 'B', 'C', 'D', 'E']\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", runner],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"runner failed: {proc.stderr}\n{proc.stdout}"
    print(proc.stdout)
