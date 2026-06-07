"""Integration test: swift build must succeed on the emitted swift_minimal project."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from clio.cli import _cmd_compile
from clio.emitters._swift_runtime_templates import (
    render_runtime_cache_swift,
    render_runtime_sha256_swift,
)
from clio.runtime.cache import cache_key

FIXTURES = Path(__file__).parent.parent / "fixtures"
swift_missing = shutil.which("swift") is None
swiftc = shutil.which("swiftc")
swiftc_missing = swiftc is None


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


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_contract_builds(tmp_path: Path) -> None:
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_contract.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_judgment_builds(tmp_path: Path) -> None:
    """swift build must succeed on a judgment-only flow (Anthropic URLSession client)."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# CACHE support tests
# ---------------------------------------------------------------------------


def test_swift_judgment_cache_emits_runtime_files(tmp_path: Path) -> None:
    """SHA256.swift and Cache.swift are emitted for a flow with CACHE: ttl(...)."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment_cache.clio", out)
    runtime_dir = out / "Sources" / "ClioFlow" / "Runtime"
    assert (runtime_dir / "SHA256.swift").exists(), "SHA256.swift not emitted"
    assert (runtime_dir / "Cache.swift").exists(), "Cache.swift not emitted"


def test_swift_judgment_cache_step_references_cache(tmp_path: Path) -> None:
    """The emitted judgment step references Cache.lookup and Cache.store."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment_cache.clio", out)
    steps_dir = out / "Sources" / "ClioFlow" / "Steps"
    step_src = next(steps_dir.glob("*.swift")).read_text()
    assert "Cache.lookup" in step_src, "Cache.lookup not in emitted step"
    assert "Cache.store" in step_src, "Cache.store not in emitted step"
    assert "Cache.cacheDirFromEnv" in step_src, "Cache.cacheDirFromEnv not in emitted step"
    assert "Cache.key" in step_src, "Cache.key not in emitted step"


def test_swift_no_cache_runtime_files_absent(tmp_path: Path) -> None:
    """SHA256.swift and Cache.swift are NOT emitted for a flow without CACHE."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment.clio", out)
    runtime_dir = out / "Sources" / "ClioFlow" / "Runtime"
    assert not (runtime_dir / "SHA256.swift").exists(), "SHA256.swift unexpectedly emitted"
    assert not (runtime_dir / "Cache.swift").exists(), "Cache.swift unexpectedly emitted"


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_judgment_cache_builds(tmp_path: Path) -> None:
    """swift build must succeed on a judgment flow with CACHE: ttl(24h)."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment_cache.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_judgment_onfail_builds(tmp_path: Path) -> None:
    """swift build must succeed — and emit NO warnings — on a judgment flow with
    an ON_FAIL chain (retry + fallback).

    The fresh tmp_path means this is a clean build that compiles every source,
    so a regression that reintroduces a `lastError written but never read`
    (or `fbOut unused`) warning would surface here."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_judgment_onfail.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning:" not in (proc.stdout + proc.stderr), proc.stdout + proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_control_flow_builds(tmp_path: Path) -> None:
    """swift build must succeed — and emit NO warnings — on a flow with
    IF/ELSE, MATCH/CASE, and bounded WHILE control flow (Phase 3a)."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_control_flow.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning:" not in (proc.stdout + proc.stderr), proc.stdout + proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_foreach_seq_builds(tmp_path: Path) -> None:
    """swift build must succeed — and emit NO warnings — on a flow with
    sequential FOR EACH containing nested MATCH and IF on the loop variable
    (Phase 3b: loop-variable scoping)."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_foreach_seq.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning:" not in (proc.stdout + proc.stderr), proc.stdout + proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_foreach_take_contract_builds(tmp_path: Path) -> None:
    """swift build must succeed on a flow whose only reference to a CONTRACT is
    a `TAKES: List<contract>` accessed via a loop-var condition.

    Regression: render_contracts_swift collected contract refs from
    graph.steps only, never from the flow's takes/gives — so a contract used
    solely by a FLOW take produced a Flow.swift that referenced an undefined
    type ([Risk]) with no Contracts.swift, failing to compile with
    `cannot find type 'Risk' in scope`."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_foreach_take.clio", out)
    assert (out / "Sources/ClioFlow/Contracts.swift").exists(), (
        "Contracts.swift not emitted for a contract referenced only by a FLOW take"
    )
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning:" not in (proc.stdout + proc.stderr), proc.stdout + proc.stderr


@pytest.mark.skipif(swift_missing, reason="swift toolchain not on PATH")
def test_swift_sideeffect_step_builds_warning_free(tmp_path: Path) -> None:
    """A side-effect step (TAKES but no GIVES) must build with NO warnings.

    The emitted `let outN = try await step_...(...)` is never read when the
    step has no GIVES, so without an explicit `_ = outN` discard Swift emits
    `warning: initialization of immutable value 'outN' was never used`."""
    out = tmp_path / "out"
    _compile(FIXTURES / "swift_sideeffect.clio", out)
    proc = subprocess.run(
        ["swift", "build"],
        cwd=out,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    assert "warning:" not in (proc.stdout + proc.stderr), proc.stdout + proc.stderr


@pytest.mark.skipif(swift_missing or swiftc_missing, reason="swift/swiftc toolchain not on PATH")
def test_sha256_known_vector(tmp_path: Path) -> None:
    """sha256Hex("abc") equals the FIPS 180-4 known test vector.

    Compiles SHA256.swift with a minimal main.swift and checks the output.
    """
    assert swiftc is not None  # satisfied by skip condition
    sha256_file = tmp_path / "SHA256.swift"
    main_file = tmp_path / "main.swift"
    bin_path = tmp_path / "sha256_vec"

    sha256_file.write_text(render_runtime_sha256_swift())
    main_file.write_text('print(sha256Hex("abc"))\n')

    compile_proc = subprocess.run(
        [swiftc, str(sha256_file), str(main_file), "-o", str(bin_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert compile_proc.returncode == 0, f"swiftc compile error:\n{compile_proc.stderr}"

    run_proc = subprocess.run(
        [str(bin_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run_proc.returncode == 0, run_proc.stderr
    got = run_proc.stdout.strip()
    want = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert got == want, f"SHA256 vector mismatch: got {got!r}, want {want!r}"


@pytest.mark.skipif(swift_missing or swiftc_missing, reason="swift/swiftc toolchain not on PATH")
def test_cache_key_parity(tmp_path: Path) -> None:
    """Swift Cache.key(...) is byte-identical to Python cache_key(...) for fixed inputs.

    This is the critical parity test: proves the key derivation is byte-identical
    so cache files generated by the Swift target are readable by the Python target
    and vice versa.

    Key derivation (both targets):
      SHA256(step_name + "\\n" + model + "\\n" + prompt + "\\n" + schema_json)
    encoded as UTF-8 hex.
    """
    assert swiftc is not None  # satisfied by skip condition

    # Fixed inputs — same in both Python and Swift
    step_name = "test_step"
    model = "claude-test-model"
    prompt = "hello world"
    schema = ""

    # Python expected value
    expected = cache_key(step_name, model, prompt, schema)

    # Build Swift harness: SHA256.swift + Cache.swift + main.swift
    sha256_file = tmp_path / "SHA256.swift"
    cache_file = tmp_path / "Cache.swift"
    main_file = tmp_path / "main.swift"
    bin_path = tmp_path / "cache_key_test"

    sha256_file.write_text(render_runtime_sha256_swift())
    cache_file.write_text(render_runtime_cache_swift())
    main_file.write_text(
        "import Foundation\n"
        f'print(Cache.key(step: "{step_name}", model: "{model}",'
        f' prompt: "{prompt}", schema: "{schema}"))\n'
    )

    compile_proc = subprocess.run(
        [swiftc, str(sha256_file), str(cache_file), str(main_file), "-o", str(bin_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert compile_proc.returncode == 0, f"swiftc compile error:\n{compile_proc.stderr}"

    run_proc = subprocess.run(
        [str(bin_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert run_proc.returncode == 0, run_proc.stderr
    got = run_proc.stdout.strip()

    assert got == expected, (
        f"Cache key parity FAILED.\n"
        f"  Python cache_key() = {expected!r}\n"
        f"  Swift  Cache.key() = {got!r}\n"
        f"  Inputs: step={step_name!r} model={model!r} prompt={prompt!r} schema={schema!r}"
    )
