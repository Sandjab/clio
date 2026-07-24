"""Shared test helpers."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def assert_valid_js(source: str, tmp_path: Path) -> None:
    """Syntax-check emitted JS with `node --check`.

    The emitted script is an ES module with top-level `await` and undeclared
    globals (`agent`, `parallel`, `args`). `node --check` accepts exactly that:
    it validates syntax without resolving globals or executing anything.

    Skips when node is absent so the suite stays green on a bare machine.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed; skipping JS syntax gate")
    f = tmp_path / "probe.mjs"
    f.write_text(source)
    proc = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert proc.returncode == 0, f"node --check failed:\n{proc.stderr}\n---\n{source}"
