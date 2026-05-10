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

pytestmark = pytest.mark.skipif(
    os.environ.get("CLIO_E2E") != "1",
    reason="CLIO_E2E=1 not set",
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
