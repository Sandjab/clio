"""Integration test: swift build must succeed on the emitted swift_minimal project."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clio.cli import _cmd_compile

FIXTURES = Path(__file__).parent.parent / "fixtures"
swift_missing = shutil.which("swift") is None


def _compile(src: Path, out: Path) -> None:
    assert _cmd_compile(str(src), "swift", str(out), None) == 0


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_minimal_builds(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_minimal.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
