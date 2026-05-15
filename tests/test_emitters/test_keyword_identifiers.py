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


# Two more cross-emitter sanitization gaps surfaced by the mcp-server PR #36
# Gemini review and tracked in issue #37: when an `item.collection` (FOR EACH
# inner) or an `item.state_field` / `item.sub_field` (MATCH) lands as a Python
# identifier post-emission, it must route through `_to_field_name`. The mcp
# side was fixed in `47e2d7b`; the python and langgraph emitters share the
# same dispatch shape and have the same bugs. `claude-skill` is unaffected —
# its dispatch is a per-script orchestrator that doesn't walk FOR EACH /
# MATCH the same way.

# Nested FOR EACH where the inner collection references an outer loop var
# whose name collides with a Python keyword. Outer `class` is sanitized to
# `class_` at the loop-var definition (existing v0.17.1 fix); the inner
# `for y in class:` must also reference `class_`. langgraph rejects FOR EACH
# at compile time, so it's excluded here.
_KEYWORD_FOR_EACH_COLLECTION_SRC = (
    "STEP inner\n"
    "  TAKES: y: str\n"
    "  GIVES: z: str\n"
    "  MODE: exact\n"
    "FLOW pipeline\n"
    "  TAKES: xs: List<List<str>>\n"
    "  FOR EACH class IN xs:\n"
    "    FOR EACH y IN class:\n"
    "      inner(y=y)\n"
)

# MATCH on a contract field whose name is a Python keyword. `result.class`
# is a SyntaxError; emitters must rewrite it to `result.class_` (the
# Pydantic model already renames the field with `alias='class'` per the
# v0.17.1 contract fix). Both python and langgraph compile MATCH; both
# need the fix.
_KEYWORD_MATCH_SUB_FIELD_SRC = (
    "CONTRACT Foo\n"
    "  SHAPE: {class: enum(a|b|c)}\n"
    "\n"
    "STEP s\n"
    "  TAKES: x: str\n"
    "  GIVES: result: Foo\n"
    "  MODE: exact\n"
    "STEP handler_a\n"
    "  TAKES: r: Foo\n"
    "  GIVES: y: str\n"
    "  MODE: exact\n"
    "FLOW pipeline\n"
    '    s(x="hello")\n'
    "    -> MATCH result.class:\n"
    "        CASE a:\n"
    "            handler_a(r=result)\n"
    "        DEFAULT:\n"
    "            handler_a(r=result)\n"
)


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (ClaudeSkillEmitter, "claude-skill"),
    ],
)
def test_keyword_for_each_inner_collection_compiles_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    """Nested FOR EACH whose inner collection references an outer keyword-
    named loop variable. mcp-server already had the fix (PR #36 / issue #37
    closer); this test extends the parametrization so python and
    claude-skill also stay green. langgraph rejects FOR EACH at compile
    time and is excluded."""
    graph = build_ir(parse(_KEYWORD_FOR_EACH_COLLECTION_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)


@pytest.mark.parametrize(
    "emitter_cls,target_label",
    [
        (PythonEmitter, "python"),
        (MCPServerEmitter, "mcp-server"),
        (LangGraphEmitter, "langgraph"),
    ],
)
def test_keyword_match_sub_field_compiles_to_valid_python(
    tmp_path: Path, emitter_cls: type, target_label: str
) -> None:
    """MATCH on a contract field whose name is a Python keyword. The
    Pydantic model has `Field(alias='class')` so the attribute is
    accessible as `obj.class_`; the emitter must rewrite the bare
    `obj.class` (a SyntaxError) accordingly. claude-skill currently
    does not emit MATCH dispatch as Python — its orchestrator renders
    a markdown narrative — so it's out of scope."""
    graph = build_ir(parse(_KEYWORD_MATCH_SUB_FIELD_SRC))
    emitter_cls().emit(graph, tmp_path)
    _assert_all_py_parses(tmp_path, target_label)
