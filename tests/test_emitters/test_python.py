from pathlib import Path

import pytest

from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse


FIXTURES = Path(__file__).parent.parent / "fixtures"


def _read_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_text()
    return out


def test_emit_skeleton(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v03_skeleton")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_skeleton_copies_cache_verbatim(tmp_path):
    src = (FIXTURES / "mvp_v03_skeleton.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    src_cache = (Path(__file__).parent.parent.parent / "clio" / "runtime" / "cache.py").read_text()
    out_cache = (tmp_path / "classify" / "clio_runtime" / "cache.py").read_text()
    assert out_cache == src_cache
