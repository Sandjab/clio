from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def test_emit_phase1(tmp_path):
    src = (FIXTURES / "mvp_phase1.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase1")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase2(tmp_path):
    src = (FIXTURES / "mvp_phase2.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase2")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase4(tmp_path):
    src = (FIXTURES / "mvp_phase4.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase4")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase6(tmp_path):
    src = (FIXTURES / "mvp_phase6.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase6")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase7(tmp_path):
    src = (FIXTURES / "mvp_phase7.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase7")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase7_run_sh_is_executable(tmp_path):
    src = (FIXTURES / "mvp_phase7.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)
    assert (tmp_path / "run.sh").stat().st_mode & 0o111


def test_emit_phase8(tmp_path):
    src = (FIXTURES / "mvp_phase8.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase8")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_phase9_full_mvp(tmp_path):
    src = (FIXTURES / "mvp_phase9.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "phase9")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v02_cache(tmp_path):
    src = (FIXTURES / "mvp_v02_cache.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v02_cache")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_v02_onfail(tmp_path):
    src = (FIXTURES / "mvp_v02_onfail.clio").read_text()
    graph = build_ir(parse(src))
    ClaudeCLIEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v02_onfail")
    actual = _read_tree(tmp_path)
    assert actual == expected
