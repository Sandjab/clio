"""Drift guard: `examples/projects/01-iterative-refiner/expected_output/`
must match what `clio compile --target python` produces right now.

If the emitter changes the bytes it produces for this flow, the maintainer
who made the emitter change must regenerate `expected_output/` and commit
it in the same PR. The diff in PR-review then makes the impact visible.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_DIR = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "projects"
    / "01-iterative-refiner"
)


def test_iterative_refiner_expected_output_is_up_to_date() -> None:
    script = PROJECT_DIR / "rebuild.sh"
    assert script.is_file(), f"rebuild.sh not found at {script}"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "rebuild.sh reported drift between flow.clio and expected_output/.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
