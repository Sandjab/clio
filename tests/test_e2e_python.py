"""E2E test for the Python emitter.

Gated by CLIO_E2E=1. Compiles examples/mvp.clio --target python, fills in the
exact step bodies, runs once against real claude (via SDK), then runs again
with the SDK monkeypatched to record calls — must record zero (cache hit).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_NEEDS_E2E = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="CLIO_E2E=1 not set",
)


def test_e2e_rescue_resume_python_flow(tmp_path):
    """A flow whose first (non-trivial) step always raises is rescued by
    RESUME(recover.report).  The downstream step reads the injected value
    and returns True, proving RESUME injected the fallback successfully."""
    import json as _json

    from clio.emitters.python import PythonEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    src = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  exact

STEP recover
  TAKES: rows: List<int>
  GIVES: report: str
  MODE:  exact

STEP downstream
  TAKES: report: str
  GIVES: ok: bool
  MODE:  exact

FLOW pipeline
  load(path="data.csv")
    -> detect(rows=rows)
    -> downstream(report=report)

  RESCUE detect:
    -> recover(rows=rows)
    -> RESUME(recover.report)

RESOURCES
  target: python
  models: [haiku]
"""
    out_dir = tmp_path / "out"
    PythonEmitter().emit(build_ir(parse(src)), out_dir)

    pkg = out_dir / "pipeline"
    (pkg / "steps" / "load.py").write_text(
        "def load(*, path):\n"
        "    return [1, 2, 3]\n"
    )
    (pkg / "steps" / "detect.py").write_text(
        "def detect(*, rows):\n"
        "    raise RuntimeError('detect always fails in this test')\n"
    )
    (pkg / "steps" / "recover.py").write_text(
        "def recover(*, rows):\n"
        "    return 'rescued-report'\n"
    )
    (pkg / "steps" / "downstream.py").write_text(
        "def downstream(*, report):\n"
        "    return report == 'rescued-report'\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pipeline", "--kwargs", "{}"],
        cwd=out_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"subprocess failed (rc={result.returncode})\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    output = _json.loads(result.stdout)
    assert output.get("ok") is True, (
        f"expected ok=True in output, got: {output!r}\n"
        f"stderr: {result.stderr}"
    )


REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "mvp.clio"
SAMPLE_CSV = REPO_ROOT / "examples" / "customers.csv"


LOAD_CUSTOMERS_BODY = '''
import csv
from pathlib import Path


def load_customers(*, file: str) -> list[dict]:
    out = []
    for row in csv.DictReader(Path(file).open()):
        out.append({"name": row["name"], "revenue": float(row["revenue"])})
    return out
'''

NAIVE_BODY = '''
def detect_churn_naive(*, customers: list[dict]) -> list[dict]:
    risks = []
    for c in customers:
        rev = c["revenue"]
        if rev < 1000:
            risk = "high"
        elif rev < 10000:
            risk = "mid"
        else:
            risk = "low"
        risks.append({"client": c["name"], "risk": risk, "reason": f"heuristic: revenue={rev}"})
    return risks
'''


def _patch_steps(out_dir: Path) -> None:
    pkg = out_dir / "customer_retention"
    (pkg / "steps" / "load_customers.py").write_text(LOAD_CUSTOMERS_BODY)
    naive = pkg / "steps" / "detect_churn_naive.py"
    if naive.exists():
        naive.write_text(NAIVE_BODY)


@_NEEDS_E2E
def test_python_real_claude_then_cache_hit(tmp_path, monkeypatch):
    out = tmp_path / "out"
    rc = subprocess.run(
        [sys.executable, "-m", "clio", "compile", str(EXAMPLE),
         "--target", "python", "--output", str(out)],
        check=False,
    ).returncode
    assert rc == 0
    shutil.copy(SAMPLE_CSV, out / "customers.csv")
    _patch_steps(out)

    sys.path.insert(0, str(out))
    old_cwd = os.getcwd()
    try:
        os.chdir(out)
        from customer_retention.flow import run
        state1 = run(file="customers.csv")
        os.chdir(old_cwd)

        assert isinstance(state1.get("risks"), list) and state1["risks"]

        # Run 2: monkeypatch the SDK to record calls (and raise if called).
        # Cache should short-circuit before any call.
        import anthropic
        call_log = []

        class BoomMessages:
            @staticmethod
            def create(**kw):
                call_log.append(kw)
                raise AssertionError("SDK must not be called on cache hit")

        class BoomClient:
            def __init__(self, *_a, **_k):
                self.messages = BoomMessages()

        monkeypatch.setattr(anthropic, "Anthropic", BoomClient)

        os.chdir(out)
        # No need to reload customer_retention — `import anthropic` +
        # `anthropic.Anthropic()` at call-time picks up the monkeypatch.
        state2 = run(file="customers.csv")
        os.chdir(old_cwd)

        assert call_log == [], f"expected zero SDK calls on cache hit, got {len(call_log)}"
        assert state2["risks"] == state1["risks"]
    finally:
        try:
            os.chdir(old_cwd)
        except Exception:
            pass
        if str(out) in sys.path:
            sys.path.remove(str(out))
        for k in list(sys.modules):
            if k.startswith("customer_retention"):
                del sys.modules[k]
