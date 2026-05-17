"""Compile-check tests: emit a fixture to Go, then run `go build ./...`.

Skipped entirely if the `go` toolchain is not on PATH. The smoke does NOT
download Go, does NOT run `go mod tidy` against the network (the emitted
go.sum is committed or absent), and does NOT execute the emitted binary —
this exercises syntactic correctness of the emitter's output only.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_emitters.test_go import _compile

pytestmark = pytest.mark.skipif(
    shutil.which("go") is None,
    reason="Go toolchain not on PATH",
)


def _go_build(out_dir: Path) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run `go build ./...` inside out_dir. Returns the completed process."""
    return subprocess.run(
        ["go", "build", "./..."],
        cwd=out_dir,
        capture_output=True,
        text=True,
        env={
            "GOFLAGS": "-mod=mod",
            "HOME": str(out_dir / ".gohome"),
            "PATH": "/usr/bin:/usr/local/bin:/bin",
        },
    )


def test_go_build_passes_on_minimal_contract_flow(tmp_path: Path) -> None:
    src = tmp_path / "src.clio"
    src.write_text(
        "CONTRACT customer_risk\n"
        "  SHAPE: {client: str}\n"
        "STEP detect\n"
        "  TAKES: x: str\n"
        "  GIVES: risk: customer_risk\n"
        "  MODE:  judgment\n"
        "FLOW pipeline\n"
        "  detect(x=\"hi\")\n"
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    out = tmp_path / "out"
    _compile(src, out)

    subprocess.run(["go", "mod", "tidy"], cwd=out, check=True, capture_output=True)
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
