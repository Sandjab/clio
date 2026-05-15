"""Cross-emitter regression test for issue #28.

When a STEP / FLOW / FOR EACH declares an identifier whose name collides
with a Python keyword (`from`, `class`, `return`, ...), every Python-
emitting target must still produce syntactically valid Python. The emit-
side fix is to sanitize the identifier (via `_to_field_name`) whenever it
lands in a Python identifier position (kwarg LHS, local variable LHS,
function-signature parameter, FOR EACH loop variable definition + usage),
while keeping the original name in string positions (dict keys, state
lookups). claude-cli is out of scope (shell target, no Python identifiers
derived from CLIO sources).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from clio.emitters.claude_skill import ClaudeSkillEmitter
from clio.emitters.langgraph import LangGraphEmitter
from clio.emitters.mcp_server import MCPServerEmitter
from clio.emitters.python import PythonEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import parse

_KEYWORD_FIELDS_SRC = (
    "STEP relay\n"
    "  TAKES: from: str, class: str\n"
    "  GIVES: text: str\n"
    "  MODE:  exact\n"
    "FLOW pipeline\n"
    "  TAKES: from: str, class: str\n"
    "  GIVES: text: str\n"
    "  relay(from=from, class=class)\n"
)

# FOR EACH PARALLEL with a Python keyword as loop variable AND as inner-step
# TAKES — exercises every Python identifier position that derives from a
# user-declared name: the inner step's signature, the kwarg LHS, the
# parallel `_task` / `enumerate` definition, and the kwarg RHS where the
# loop variable is referenced via `@return`. langgraph rejects FOR EACH at
# compile time so it's excluded from the parametrization.
_KEYWORD_LOOP_VAR_SRC = (
    "STEP echo\n"
    "  TAKES: from: str\n"
    "  GIVES: text: str\n"
    "  MODE:  exact\n"
    "FLOW batch\n"
    "  TAKES: items: List<str>\n"
    "  GIVES: text: List<str>\n"
    "  FOR EACH return IN items PARALLEL AS text:\n"
    "    echo(from=return)\n"
)

# Same bug class as `_KEYWORD_FIELDS_SRC` but on STEP / FLOW names themselves:
# the parser accepts Python keywords as identifiers, but every emitter that
# turns the name into a Python identifier (function definition, module alias,
# call site, sub-flow runner, langgraph state TypedDict) must sanitize via
# `_to_field_name`. String positions (filenames, dict keys, log labels) keep
# the original name. Two separate sources keep the failure mode obvious in
# the test output.
_KEYWORD_STEP_NAME_SRC = (
    "STEP class\n"
    "  TAKES: x: str\n"
    "  GIVES: y: str\n"
    "  MODE:  exact\n"
    "FLOW pipeline\n"
    "  TAKES: x: str\n"
    "  GIVES: y: str\n"
    "  class(x=x)\n"
)

_KEYWORD_FLOW_NAME_SRC = (
    "STEP relay\n"
    "  TAKES: x: str\n"
    "  GIVES: y: str\n"
    "  MODE:  exact\n"
    "FLOW return\n"
    "  TAKES: x: str\n"
    "  GIVES: y: str\n"
    "  relay(x=x)\n"
)


def _assert_all_py_parses(root: Path, target: str) -> None:
    py_files = sorted(root.rglob("*.py"))
    assert py_files, f"{target}: no .py files emitted under {root}"
    for p in py_files:
        body = p.read_text()
        try:
            ast.parse(body)
        except SyntaxError as exc:
            pytest.fail(
                f"{target}: emitted Python file {p.relative_to(root)} fails "
                f"to parse: {exc}\n--- body ---\n{body}"
            )


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (LangGraphEmitter, "langgraph"),
        (ClaudeSkillEmitter, "claude-skill"),
    ],
)
def test_keyword_field_names_compile_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    graph = build_ir(parse(_KEYWORD_FIELDS_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (ClaudeSkillEmitter, "claude-skill"),
    ],
)
def test_keyword_loop_variable_compiles_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    graph = build_ir(parse(_KEYWORD_LOOP_VAR_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (LangGraphEmitter, "langgraph"),
        (ClaudeSkillEmitter, "claude-skill"),
    ],
)
def test_keyword_step_name_compiles_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    graph = build_ir(parse(_KEYWORD_STEP_NAME_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (LangGraphEmitter, "langgraph"),
        (ClaudeSkillEmitter, "claude-skill"),
    ],
)
def test_keyword_flow_name_compiles_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    graph = build_ir(parse(_KEYWORD_FLOW_NAME_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)
