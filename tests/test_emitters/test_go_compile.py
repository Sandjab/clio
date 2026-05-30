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

from tests.test_emitters.test_go import FIXTURES, _compile, _compile_flow

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


def test_go_build_passes_on_rest_flow(tmp_path: Path) -> None:
    """Emit a REST flow (GIVES-typed geocode + no-GIVES notify) to Go and
    `go build`. Catches the type-assertion / import / runtime-template class of
    bugs a string-grep can never see."""
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


def test_go_build_passes_on_rest_bodyless_and_raw(tmp_path: Path) -> None:
    """Emit two REST shapes that exercise the conditional-import edges and
    `go build`:

      * ping  — DELETE with no `body:` and no `GIVES:` and no retry/timeout.
        This step uses neither encoding/json, bytes, strings, nor time; if any
        of those were imported unconditionally the module would not compile
        ("imported and not used"). Locks must-fix #1 + the time/timeout edge.
      * raw   — POST with a text/plain (RawBodyIR) body, exercising the
        strings.NewReader path (no encoding/json marshal, no GIVES).
    """
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


def test_go_form_body_refused_while_json_body_builds(tmp_path: Path) -> None:
    """E_GO_013 boundary, both halves:

    * A form body must be REFUSED at compile time (no go build needed) — this is
      the fix for the silent-drop bug where a form body fell through to a nil
      reader and was never sent.
    * A json body must still emit a module that ACTUALLY go-builds — proving the
      refusal does not over-fire onto the supported shapes.
    """
    import pytest as _pytest

    from tests.test_emitters.test_go import _rest_body_src

    # Negative: form body refused at compile time.
    form_src = tmp_path / "form.clio"
    form_src.write_text(_rest_body_src('body: {form: {user: "${u}"}}'))
    with _pytest.raises(ValueError, match="E_GO_013"):
        _compile(form_src, tmp_path / "form_out")

    # Positive: json body emits and go-builds.
    json_src = tmp_path / "json.clio"
    json_src.write_text(_rest_body_src('body: {payload: "${u}"}'))
    out = tmp_path / "json_out"
    _compile(json_src, out)
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


def test_go_build_passes_on_shell_flow(tmp_path: Path) -> None:
    """A flow with parse:json, parse:none, and a no-GIVES shell step must
    emit Go that actually compiles (catches import-path / type-assertion bugs
    a string-grep cannot)."""
    fixtures = Path(__file__).parent.parent / "fixtures"
    from tests.test_emitters.test_go import _compile as _go_compile

    out = tmp_path / "out"
    _go_compile(fixtures / "go_shell.clio", out)

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


def test_go_build_json_body_with_retry_rebuilds_reader(tmp_path: Path) -> None:
    """A JSON-body step that also has impl.retry must rebuild the body reader on
    every attempt: a single bytes.Reader is at EOF after the first send, so
    retries 2+ would silently transmit an empty body. Locks must-fix #2 — the
    `bytes.NewReader(_bodyBytes)` line must live INSIDE the retry `for` loop, and
    the module must `go build`."""
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


def test_go_build_passes_on_entry_takes_flow(tmp_path: Path) -> None:
    """A flow whose entry FLOW declares a TAKE and reads it via `@take` must
    compile.  This is the type-assertion guard: if the typed read is missing,
    `any` is assigned to a `string` field and `go build` fails.  (A missing
    seed line is a *runtime* nil-interface panic, not a compile error — that
    half is pinned by the grep test on the `state["url"] = kwargs["url"]` seed.)
    A string-grep test cannot catch the type-assertion regression, so the build
    is the real verification.
    """
    src = tmp_path / "src.clio"
    src.write_text(
        "STEP fetch\n"
        "  TAKES: url: str\n"
        "  GIVES: body: str\n"
        "  MODE:  exact\n"
        "  LANG:  go\n"
        "FLOW pipeline\n"
        "  TAKES: url: str\n"
        "  GIVES: body: str\n"
        "  fetch(url=url)\n"
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
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env,
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "fixture,entry",
    [
        ("go_subflow_seq.clio", "pipeline"),
        ("go_subflow_parallel.clio", "batch"),
        ("go_subflow_collision.clio", "pipeline"),
        ("go_subflow_abc.clio", "pipeline"),
    ],
)
def test_go_build_passes_on_subflow_composition(
    tmp_path: Path, fixture: str, entry: str
) -> None:
    """Emit a sub-flow composition fixture, `go mod tidy`, then `go build ./...`.
    A real build is the only check that catches a wrong @take/boundary type
    assertion — a string grep cannot. Covers sequential, parallel single-GIVES
    collector, sibling collision (last-writer-wins), and A->B->C nesting."""
    out = tmp_path / "out"
    _compile_flow(FIXTURES / fixture, out, entry)
    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env,
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed for {fixture}:\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


@pytest.mark.parametrize(
    "fixture,entry,typed_read",
    [
        # Transitive boundary: `middle` GIVES `c`, produced two sub-flow levels
        # down (pipeline -> middle -> deep -> produce_c). The downstream
        # `consume(c=c)` must assert to the DEEP producer's Out struct, proving
        # _resolve_give_producer walks nested FlowCallIR boundaries — not just
        # the immediate sub-flow's direct steps.
        (
            "go_subflow_transitive_read.clio",
            "pipeline",
            'state["c"].(steps.ProduceCOut).C',
        ),
        # Sibling collision: flow_a (result:int) then flow_b (result:str) both
        # GIVE `result`; `sink(result=result)` must read the LAST writer
        # (flow_b -> make_b). Types differ on purpose: a wrong (flow_a) assertion
        # would type `int64` against sink's `str` TAKE and fail go build, so the
        # build itself proves last-writer-wins ordering, not just "it compiles".
        (
            "go_subflow_collision_read.clio",
            "pipeline",
            'state["result"].(steps.MakeBOut).Result',
        ),
    ],
)
def test_go_build_passes_on_subflow_downstream_typed_read(
    tmp_path: Path, fixture: str, entry: str, typed_read: str
) -> None:
    """Sub-flow compositions that CONSUME the boundary-extended producer in a
    downstream typed read (sequential), not merely return it. The emitted
    typed assertion (`typed_read`) must reference the correct producer, and the
    module must `go build` — a wrong producer would fail the build (transitive
    case) or fail it on the type mismatch (collision last-writer-wins case)."""
    out = tmp_path / "out"
    _compile_flow(FIXTURES / fixture, out, entry)
    body = (out / "flow" / "flow.go").read_text()
    assert typed_read in body, (
        f"{fixture}: expected downstream typed read {typed_read!r} not found in "
        f"flow.go — boundary extension pointed at the wrong producer.\n{body}"
    )
    tidy_env = {
        "GOFLAGS": "-mod=mod",
        "HOME": str(out / ".gohome"),
        "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin:/bin"),
    }
    subprocess.run(
        ["go", "mod", "tidy"], cwd=out, check=True, capture_output=True, env=tidy_env,
    )
    result = _go_build(out)
    assert result.returncode == 0, (
        f"go build failed for {fixture}:\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
