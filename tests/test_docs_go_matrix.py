"""Doc-consistency guard: the v0.23 go-target doc surfaces must say the right
thing. A grep here is the correct tool — these are static markdown claims about
target support, not emitted code; a go build cannot verify a table cell. Each
test encodes WHY a row flipped (rest/shell/composition shipped in v0.23, the
narrowed multi-GIVES-parallel E_GO_006 survives)."""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

MATRIX = (_ROOT / "docs" / "manual" / "04-targets.md").read_text()


def _row(prefix: str) -> str:
    for line in MATRIX.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no matrix row starting {prefix!r}")


def test_flow_composition_row_supported_on_go() -> None:
    row = _row("| **FLOW composition**")
    # last column is the go cell; v0.23 supports it -> no E_GO_006 there.
    assert "E_GO_006" not in row
    assert "run<Name>" in row or "run_<name>" in row or "sub-flow func" in row


def test_parallel_subflow_row_supported_on_go() -> None:
    row = _row("| `FOR EACH PARALLEL` body = sub-flow")
    # The go cell shows single-GIVES support; the only E_GO_006 mention is the
    # narrowed multi-GIVES caveat in the same cell.
    assert "single-GIVES" in row


def test_impl_rest_row_supported_on_go() -> None:
    row = _row("| `MODE: exact` + `impl.rest`")
    assert "E_GO_007" not in row


def test_impl_shell_rows_supported_on_go() -> None:
    shell = _row("| `MODE: exact` + `impl.shell` |")
    shell_json = _row("| `MODE: exact` + `impl.shell` + `parse: json`")
    assert "E_GO_008" not in shell
    assert "E_GO_008" not in shell_json


def test_when_not_to_use_go_no_longer_lists_rest_shell_composition() -> None:
    # The "Don't use when" bullets must not deferral-flag rest/shell/composition.
    assert "FLOW composition (sub-flow calls) — deferred" not in MATRIX
    assert "rest / shell / sql / mcp_tool` (deferred — E_GO_007..010)" not in MATRIX


COMPTARGETS = (_ROOT / "docs" / "COMPILATION_TARGETS.md").read_text()


def test_comptargets_no_longer_defers_rest_shell_composition() -> None:
    """The refused-combo list must not blanket-defer rest/shell/full composition."""
    assert "`impl.mode: rest` — deferred to v0.20.x (E_GO_007)." not in COMPTARGETS
    assert "`impl.mode: shell` — deferred to v0.20.x (E_GO_008)." not in COMPTARGETS
    assert (
        "**FLOW composition** (sub-flow calls) — deferred to v0.20.x (E_GO_006)."
        not in COMPTARGETS
    )


def test_comptargets_narrowed_e_go_006_present() -> None:
    """The one remaining composition refusal is documented."""
    assert "multi-GIVES sub-flow" in COMPTARGETS
    assert "E_GO_006" in COMPTARGETS


def test_comptargets_lists_composition_rest_shell_as_inherited() -> None:
    """rest/shell/composition appear in the supported-features prose."""
    assert "net/http" in COMPTARGETS
    assert "os/exec" in COMPTARGETS
    assert "run<Name>" in COMPTARGETS or "sub-flow" in COMPTARGETS.lower()


LANGSPEC = (_ROOT / "docs" / "LANGUAGE_SPEC.md").read_text()


def test_langspec_subflow_table_lists_go() -> None:
    """The sub-flow target-support table must carry a go row showing support."""
    rows = [ln for ln in LANGSPEC.splitlines() if ln.strip().startswith("| `go`")]
    assert rows, "no `go` row in the sub-flow target-support table"
    go_row = rows[0]
    assert "yes" in go_row.lower()
    assert "run<Name>" in go_row or "run_<name>" in go_row or "sub-flow" in go_row.lower()


def test_langspec_multi_gives_note_mentions_go_refusal() -> None:
    """The multi-GIVES limitation prose must note the go-target parallel refusal."""
    assert "E_GO_006" in LANGSPEC


COOKBOOK = (_ROOT / "docs" / "manual" / "03-cookbook.md").read_text()
TROUBLE = (_ROOT / "docs" / "manual" / "06-troubleshooting.md").read_text()


def test_cookbook_go_scope_note_updated() -> None:
    """Recipe #24 must not still list rest/shell/composition as v0.20.x deferrals."""
    assert (
        "to v0.20.x (OpenAI, FLOW composition, `impl.mode {rest, sql, mcp_tool,"
        not in COOKBOOK
    )
    # The deferral set is now sql/mcp_tool/OpenAI/RESUME/TEST + multi-GIVES parallel.
    assert "v0.20.x" not in COOKBOOK or "v0.23" in COOKBOOK


def test_troubleshooting_e_go_006_narrowed() -> None:
    """The E_GO_006 troubleshooting entry describes the multi-GIVES parallel case."""
    assert "does not support FLOW composition (sub-flow calls) in v0.20.0" not in TROUBLE
    assert "multi-GIVES" in TROUBLE


def test_troubleshooting_e_go_007_008_removed_or_marked_lifted() -> None:
    """REST/shell are supported; their old 'deferred' troubleshooting entries
    must not claim v0.20.0 non-support."""
    assert "does not support impl.mode: rest in v0.20.0" not in TROUBLE
    assert "does not support impl.mode: shell in v0.20.0" not in TROUBLE


CHANGELOG = (_ROOT / "CHANGELOG.md").read_text()


def test_changelog_has_0_23_0_entry() -> None:
    assert "## [0.23.0]" in CHANGELOG
    # 0.23.0 must sit above 0.22.0 (newest-first).
    assert CHANGELOG.index("## [0.23.0]") < CHANGELOG.index("## [0.22.0]")


def test_changelog_0_23_0_mentions_go_rest_shell_subflow() -> None:
    head = CHANGELOG.split("## [0.22.0]")[0]
    assert "go" in head.lower()
    assert "rest" in head.lower()
    assert "shell" in head.lower()
    assert "sub-flow" in head.lower() or "composition" in head.lower()
    assert "E_GO_006" in head
