import importlib.util
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


def _load_module(name: str, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_emit_contracts(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    graph = build_ir(parse(src))
    PythonEmitter().emit(graph, tmp_path)

    expected = _read_tree(FIXTURES / "expected" / "v03_contracts")
    actual = _read_tree(tmp_path)
    assert actual == expected


def test_emit_contracts_pydantic_validates(tmp_path):
    """Smoke: load the emitted contracts module and validate a sample dict."""
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    contracts_path = tmp_path / "retention" / "contracts.py"
    mod = _load_module("v03_contracts_test", contracts_path)
    CustomerRisk = mod.CustomerRisk
    ok = CustomerRisk.model_validate({"client": "X", "risk": "low", "reason": "ok"})
    assert ok.client == "X"
    with pytest.raises(Exception):
        CustomerRisk.model_validate({"client": "X", "risk": "low", "reason": ""})
    with pytest.raises(Exception):
        CustomerRisk.model_validate({"client": "X", "risk": "ZZZ", "reason": "ok"})


def test_emit_exact_step_stub_is_callable(tmp_path):
    src = (FIXTURES / "mvp_v03_contracts.clio").read_text()
    PythonEmitter().emit(build_ir(parse(src)), tmp_path)
    step_path = tmp_path / "retention" / "steps" / "load_customers.py"
    mod = _load_module("v03_load_customers_test", step_path)
    with pytest.raises(NotImplementedError):
        mod.load_customers(file="x.csv")
