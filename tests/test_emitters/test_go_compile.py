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


def test_go_build_passes_on_rest_bodyless_and_raw(tmp_path: Path, monkeypatch) -> None:  # type: ignore[type-arg]
    """Emit two REST shapes that exercise the conditional-import edges and
    `go build`:

      * ping  — DELETE with no `body:` and no `GIVES:` and no retry/timeout.
        This step uses neither encoding/json, bytes, strings, nor time; if any
        of those were imported unconditionally the module would not compile
        ("imported and not used"). Locks must-fix #1 + the time/timeout edge.
      * raw   — POST with a text/plain (RawBodyIR) body, exercising the
        strings.NewReader path (no encoding/json marshal, no GIVES).
    """
    from clio.emitters import go as _go
    from clio.emitters.go import GoEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    src_text = (
        "STEP ping\n"
        "  TAKES: id: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  DELETE\n"
        '    url:     "https://api.example.com/things/${id}"\n'
        "STEP raw\n"
        "  TAKES: payload: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  POST\n"
        '    url:     "https://api.example.com/raw"\n'
        '    body:    "raw=${payload}"\n'
        "FLOW pipeline\n"
        '  ping(id="1")\n'
        '  -> raw(payload="p")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    src = tmp_path / "src.clio"
    src.write_text(src_text)
    graph = build_ir(parse(src.read_text()))

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
    # ping (no body, no GIVES) must NOT import encoding/json / bytes / strings / time.
    ping_src = (out / "steps" / "01_ping.go").read_text()
    assert '"encoding/json"' not in ping_src
    assert '"bytes"' not in ping_src
    assert '"strings"' not in ping_src
    assert '"time"' not in ping_src
    assert "http.NewRequestWithContext(ctx, method, _url, nil)" in ping_src
    # raw uses strings.NewReader and defaults text/plain, no json marshal.
    raw_src = (out / "steps" / "02_raw.go").read_text()
    assert "strings.NewReader(_raw)" in raw_src
    assert '"encoding/json"' not in raw_src


def test_go_build_json_body_with_retry_rebuilds_reader(tmp_path: Path, monkeypatch) -> None:  # type: ignore[type-arg]
    """A JSON-body step that also has impl.retry must rebuild the body reader on
    every attempt: a single bytes.Reader is at EOF after the first send, so
    retries 2+ would silently transmit an empty body. Locks must-fix #2 — the
    `bytes.NewReader(_bodyBytes)` line must live INSIDE the retry `for` loop, and
    the module must `go build`."""
    from clio.emitters import go as _go
    from clio.emitters.go import GoEmitter
    from clio.ir.builder import build_ir
    from clio.parser.parser import parse

    src_text = (
        "STEP push\n"
        "  TAKES: msg: str\n"
        "  MODE:  exact\n"
        "  impl:\n"
        "    mode:    rest\n"
        "    method:  POST\n"
        '    url:     "https://hooks.example.com/push"\n'
        '    body:    {text: "${msg}"}\n'
        '    retry:   {attempts: 3, on: ["5xx", "timeout"]}\n'
        "FLOW pipeline\n"
        '  push(msg="hi")\n'
        "RESOURCES\n"
        "  target: go\n"
        "  models: [haiku]\n"
    )
    src = tmp_path / "src.clio"
    src.write_text(src_text)
    graph = build_ir(parse(src.read_text()))

    monkeypatch.setattr(_go, "validate_graph_for_go", lambda g: None)
    out = tmp_path / "out"
    GoEmitter().emit(graph, out)

    push_src = (out / "steps" / "01_push.go").read_text()
    # The reader construction must be inside the for-loop body (deeper indent),
    # i.e. AFTER the `for _i := ...` line — proving it is rebuilt per attempt.
    loop_at = push_src.index("for _i := 0; _i < _attempts; _i++ {")
    reader_at = push_src.index("_bodyReader := bytes.NewReader(_bodyBytes)")
    assert reader_at > loop_at, "body reader must be rebuilt inside the retry loop"
    assert "\t\t_bodyReader := bytes.NewReader(_bodyBytes)" in push_src

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
