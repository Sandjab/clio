"""Compile-check tests: emit a fixture to Go, run `go mod tidy`, then `go build ./...`.

Skipped entirely if the `go` toolchain is not on PATH. The test does require
network access on first run for `go mod tidy` to fetch the pinned deps; once
GOPATH/GOCACHE under the per-test HOME are warm, subsequent runs are offline.
The test exercises syntactic correctness of the emitter's output; it does
NOT execute the emitted binary.
"""
from __future__ import annotations

import os
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
    env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out_dir / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    return subprocess.run(
        ["go", "build", "./..."],
        cwd=out_dir,
        capture_output=True,
        text=True,
        env=env,
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

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env)
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_go_build_passes_on_rest_flow(tmp_path: Path, monkeypatch) -> None:  # type: ignore[type-arg]
    """Emit a REST flow (GIVES-typed geocode + no-GIVES notify) to Go and
    `go build`. Catches the type-assertion / import / runtime-template class of
    bugs a string-grep can never see."""
    from clio.emitters import go as _go
    from clio.emitters.go import GoEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    src_text = (
        "CONTRACT geo_point\n"
        "  SHAPE: {lat: float, lng: float}\n"
        "STEP geocode\n"
        "  TAKES: address: str\n"
        "  GIVES: location: geo_point\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:           rest\n"
        "    method:         GET\n"
        '    url:            "https://maps.example.com/geocode"\n'
        '    query:          {address: "${address}", key: "env:MAPS_KEY"}\n'
        '    headers:        {Accept: "application/json"}\n'
        '    response_path:  "results[0].geometry.location"\n'
        "    timeout:        30s\n"
        '    retry:          {attempts: 3, on: ["5xx", "429", "timeout"]}\n'
        "STEP notify\n"
        "  TAKES: msg: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  POST\n"
        '    url:     "https://hooks.example.com/notify"\n'
        '    body:    {text: "${msg}", urgent: true}\n'
        "FLOW pipeline\n"
        '  geocode(address="123 Main St")\n'
        '  -> notify(msg="done")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    src = tmp_path / "src.clio"
    src.write_text(src_text)
    graph = build_ir(parse(src.read_text()))

    # Phase 6 owns lifting E_GO_007; bypass the refusal so the emitted module —
    # the thing under test — is what `go build` checks.
    monkeypatch.setattr(_go, "validate_graph_for_go", lambda g: None)
    out = tmp_path / "out"
    GoEmitter().emit(graph, out)

    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert (out / "clio_runtime" / "rest" / "rest.go").exists()
    assert (out / "clio_runtime" / "substitute" / "substitute.go").exists()
