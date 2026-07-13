"""Helpers for target: claude-workflow — diagnostics, literals, meta, README."""
from __future__ import annotations

import json
from collections.abc import Callable

from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    ApiInvokeIR,
    BoolOpIR,
    CallIR,
    CodeImplIR,
    ConditionIR,
    ContractIR,
    FlowCallIR,
    FlowGraph,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    McpToolImplIR,
    RestImplIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)
from clio.parser.ast_nodes import TypeExpr

# ---------------------------------------------------------------------------
# Compile-time validation error codes (permanent — stable across releases)
# ---------------------------------------------------------------------------

E_WF_001 = "E_WF_001: source declares no FLOW; nothing to orchestrate."
E_WF_002 = (
    "E_WF_002: target: claude-workflow runs steps as Claude Code subagents and "
    "cannot call non-Anthropic providers. Use --target python for "
    "openai / bedrock / vertex."
)
E_WF_003 = (
    "E_WF_003: target: claude-workflow cannot execute IO from an exact step: the "
    "workflow sandbox has no process, no network and no filesystem. Move the IO "
    "out of the flow, or use --target python / go / swift."
)
E_WF_004 = (
    "E_WF_004: target: claude-workflow can only embed exact step bodies in "
    "JavaScript (LANG: node or LANG: auto). Use --target python / go / swift for "
    "other languages."
)
E_WF_005 = (
    "E_WF_005: CONTRACT reference cycle — cannot inline a self-referential schema "
    "for target claude-workflow (the sandbox cannot resolve a $ref at run time)."
)
E_WF_006 = (
    "E_WF_006: the source declares more than one FLOW and none was selected. "
    "target: claude-workflow emits exactly one script, and compiling the first "
    "declared FLOW would silently drop the others. Re-run with --flow <name>."
)
E_WF_007 = (
    "E_WF_007: FLOW recursion — target claude-workflow inlines every sub-flow as a "
    "local function in the same script (§4.2), so a FLOW that calls itself, "
    "directly or through a cycle, would inline into a function that calls itself "
    "and overflow the stack at run time."
)

# ---------------------------------------------------------------------------
# Compile-time degradation warnings (the feature still compiles, with less)
# ---------------------------------------------------------------------------

W_WF_001 = (
    "W_WF_001: `cache:` is ignored by target claude-workflow — the sandbox has no "
    "filesystem and no clock. A cache miss is slower, never wrong."
)
W_WF_002 = (
    "W_WF_002: ON_FAIL retries run WITHOUT backoff under target claude-workflow — "
    "the sandbox has no clock (`Date.now()` throws)."
)
W_WF_003 = (
    "W_WF_003: `CONTRACT … ASSERT` is not enforced by target claude-workflow. The "
    "JSON Schema (types, ranges, enums) IS enforced by the host; the ASSERT "
    "predicate is not."
)

# Langs whose exact bodies this target can emit (no LANG = None = auto-detect).
_WF_OK_LANGS: frozenset[str | None] = frozenset({"node", "auto", None})

# Impls that need a process, a socket or a filesystem — none of which exist here.
_IO_IMPLS = (ShellImplIR, RestImplIR, SqlImplIR, McpToolImplIR)

# Names that cannot be a function name in the emitted script. The list is not
# from memory: every entry below was checked with `node --check` on a module
# declaring `async function <name>(state) {}` — module code is strict code, which
# is why the strict-mode reserved words and `eval` / `arguments` belong here and
# not in a "maybe" pile.
_JS_RESERVED = frozenset({
    # ECMAScript reserved words + the three reserved literals
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "enum", "export", "extends", "false",
    "finally", "for", "function", "if", "import", "in", "instanceof", "new",
    "null", "return", "super", "switch", "this", "throw", "true", "try",
    "typeof", "var", "void", "while", "with",
    # reserved in strict mode / module code (the emitted script is an ES module)
    "await", "implements", "interface", "let", "package", "private",
    "protected", "public", "static", "yield",
    # strict mode refuses to *bind* these two, so they cannot name a function
    "eval", "arguments",
    # `function undefined(…)` is legal JS — which makes it worse than a
    # SyntaxError: the declaration hoists and shadows the global, so the
    # judgment wrapper's `result === undefined` guard would compare against a
    # function object and never fire (§6.1: agent() returns null, it does not
    # throw). Mangled for that reason, not for the parser's.
    "undefined",
})


# The names the emitted script itself binds or calls. A step named after one of
# these is not a style problem: `function state(…)` beside the script's own
# `const state = {}` is a duplicate declaration — a SyntaxError in module code —
# and `function agent(…)` would SHADOW the host global, so the judgment wrapper
# would recurse into itself instead of spawning a subagent. Mangled like the
# reserved words, and for the same reason: the file must parse, and it must mean
# what it says.
_WF_SCRIPT_NAMES = frozenset({
    "state", "args", "meta",                                      # bound by the script
    "agent", "parallel", "pipeline", "workflow", "phase", "log",  # host globals
})


def js_identifier(name: str) -> str:
    """A CLIO step/flow name rendered as a legal JS identifier.

    Identity for names that already are one — an ordinary step keeps its name, so
    the emitted function still reads like the source. Reserved words get a `$`
    suffix.

    `$` and not `_`: the CLIO lexer only accepts `[a-zA-Z_][a-zA-Z0-9_]*`
    (lexer.py:126-142), so a `$` cannot occur in a source identifier — which makes
    this mangling injective. `delete$` can never collide with a step the author
    actually declared. The house convention for Python (`_to_field_name`,
    _shared_utils.py:57) suffixes `_` because Python has no `$`; that mapping is
    NOT collision-free — a step `delete` and a step `delete_` both land on
    `delete_`, and in a single JS module the second function would silently
    overwrite the first.
    """
    return f"{name}$" if name in _JS_RESERVED | _WF_SCRIPT_NAMES else name


def js_string(s: str) -> str:
    """A single-quoted JS string literal.

    json.dumps gives correct escaping for backslashes, control chars and
    non-ASCII; we then convert the double-quoted form to single quotes to match
    the house style of the emitted script.
    """
    inner = json.dumps(s, ensure_ascii=False)[1:-1].replace("'", "\\'")
    return f"'{inner}'"


_REF_PREFIX = "../contracts/"
_REF_SUFFIX = ".schema.json"


def _deref(schema: dict, contracts: dict[str, ContractIR], seen: frozenset[str]) -> dict:
    """Recursively replace every {"$ref": "../contracts/X.schema.json"} with X's
    own (recursively inlined) schema. `seen` carries the ancestor chain so a cycle
    raises instead of recursing forever, and an unresolvable name raises rather
    than leaving a dangling $ref the sandbox cannot read.

    `x-clio-assert` is dropped from an inlined contract: the host validates the
    agent's output against this schema and does not evaluate CLIO's assert AST
    (W_WF_003). The claude-cli target strips it for the same reason.
    """
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF_PREFIX):
        name = ref[len(_REF_PREFIX):-len(_REF_SUFFIX)]
        if name in seen:
            raise ValueError(f"{E_WF_005} (contract={name!r})")
        target = contracts.get(name)
        if target is None:
            raise ValueError(f"{E_WF_005} (unknown contract {name!r})")
        inlined = {
            k: v for k, v in target.json_schema.items() if k != "x-clio-assert"
        }
        return _deref(inlined, contracts, seen | {name})

    out: dict = {}
    for k, v in schema.items():
        if isinstance(v, dict):
            out[k] = _deref(v, contracts, seen)
        elif isinstance(v, list):
            out[k] = [
                _deref(i, contracts, seen) if isinstance(i, dict) else i for i in v
            ]
        else:
            out[k] = v
    return out


def inline_schema(t: TypeExpr, contracts: dict[str, ContractIR]) -> dict:
    """A fully self-contained JSON Schema for `t` — every $ref inlined."""
    return _deref(type_to_json_schema(t), contracts, frozenset())


def schema_literal(
    t: TypeExpr, contracts: dict[str, ContractIR], field_name: str
) -> str:
    """The JS object literal for a step's GIVES schema.

    A step's GIVES is a single NAMED field, and conditions read it as
    state[step].<field> — so the agent must return an OBJECT wrapping that one
    field, not the bare value. `field_name` is StepIR.gives.name (not the step
    name: they differ).
    """
    obj = {
        "type": "object",
        "properties": {field_name: inline_schema(t, contracts)},
        "required": [field_name],
        "additionalProperties": False,
    }
    return json.dumps(obj, indent=2, ensure_ascii=False)


def entry_flow(graph: FlowGraph) -> FlowIR:
    """The single FLOW this script compiles.

    Deliberately never falls back to `graph.flows[0]`. The builder leaves
    `graph.flow` at None when the source declares several FLOWs and `--flow`
    selected none (builder.py:226-237); guessing the first one there would emit a
    perfectly plausible script for a flow the author never asked for, and drop the
    others without a word. LANGUAGE_SPEC:459-461 makes `--flow` *required* in that
    case, so this raises: E_WF_001 when there is nothing to compile, E_WF_006 when
    there is more than one candidate.
    """
    if graph.flow is not None:
        return graph.flow
    if not graph.flows:
        raise ValueError(E_WF_001)
    declared = ", ".join(f.name for f in graph.flows)
    raise ValueError(f"{E_WF_006} (declared: {declared})")


def workflow_name(graph: FlowGraph) -> str:
    """kebab-case name of the entry flow — used for meta.name and the filename."""
    return entry_flow(graph).name.replace("_", "-")


def phase_titles(flow: FlowIR) -> list[str]:
    """One title per TOP-LEVEL element of the flow chain — never one per step.

    §4.3, and the reason it is a rule rather than a preference: `phase()` is
    *global* state in the Workflow runtime, so calling it from inside a
    parallel()/pipeline() stage is racy — the last writer wins and the UI reports
    a phase no one is in. Agents spawned inside a block therefore carry the
    block's phase through `agent({phase})`, and only the top level moves the
    global. `meta.phases` lists exactly these titles, in order (the runtime
    rejects a phase() it never saw declared), which is why the renderer walks the
    chain and this list in lockstep.

    A block is named after what it branches on / iterates, so the title stays
    stable when its body changes.
    """
    titles: list[str] = []
    for item in flow.chain:
        if isinstance(item, CallIR):
            titles.append(item.step_name)
        elif isinstance(item, FlowCallIR):
            titles.append(item.flow_name)
        elif isinstance(item, ForEachIR):
            titles.append(f"each:{item.collection}")
        elif isinstance(item, IfBlockIR):
            titles.append(_branch_title("if", item.condition))
        elif isinstance(item, MatchBlockIR):
            titles.append(f"match:{item.state_field}")
        elif isinstance(item, WhileBlockIR):
            titles.append(_branch_title("while", item.condition))
        else:
            raise AssertionError(f"unreachable: chain node {type(item).__name__}")
    return titles


def _branch_title(kind: str, condition: ConditionIR | BoolOpIR) -> str:
    """`if:<state field>` — §4.3 names a branch after the field it branches on.
    A BoolOpIR reads several fields, so there is no single one to name: the bare
    kind is the honest title."""
    if isinstance(condition, ConditionIR):
        return f"{kind}:{condition.step_name}"
    return kind


def render_meta(graph: FlowGraph) -> str:
    """The `export const meta` block. It MUST be a pure literal — no variables,
    no calls, no interpolation — or the Workflow runtime rejects the script."""
    flow = entry_flow(graph)
    name = workflow_name(graph)
    desc = flow.description or f"CLIO flow {flow.name}"
    lines = [
        "export const meta = {",
        f"  name: {js_string(name)},",
        f"  description: {js_string(desc)},",
        "  phases: [",
    ]
    for title in phase_titles(flow):
        lines.append(f"    {{ title: {js_string(title)} }},")
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines)


def _backticked(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _degradations(graph: FlowGraph) -> list[str]:
    """The W_WF_NNN degradations THIS graph actually triggers, as README bullets.

    The same three predicates as validate_graph_for_workflow's `warn` calls, over
    the same nodes — deliberately, and the two are swept for agreement by
    test_readme_never_warns_about_a_degradation_the_compiler_did_not.

    Neither a canned list nor no list. Printing all three unconditionally would tell
    an author who declared no CACHE that their cache is ignored, and the section
    would stop being read on the flow where it matters; printing none would leave
    the compile-time warnings — which scroll past once and are gone — as the only
    record that this target does not honor what the source declares.

    Over-reporting is the safe direction, and the reason this reads `graph.steps`
    (every declared step) rather than the steps the script emits: on a multi-FLOW
    source a cached step in an UNSELECTED flow still earns a W_WF_001 on stderr, and
    a README that stayed silent about a warning the author watched scroll past would
    read as 'it got fixed'. The stub list below is the one that tracks the emitted
    script, because that one answers a different question: what do I have to fill in.
    """
    cached = [
        s.name for s in graph.steps if s.cache is not None and s.cache.mode != "off"
    ]
    retried = [
        s.name
        for s in graph.steps
        if s.on_fail is not None
        and any(st.kind == "retry" for st in s.on_fail.strategies)
    ]
    asserted = [c.name for c in graph.contracts if c.assert_json_ast is not None]

    bullets: list[str] = []
    if cached:
        bullets.append(
            f"- **`CACHE:` is ignored** (`W_WF_001`) — declared by "
            f"{_backticked(cached)}. The sandbox has no filesystem and no clock, so "
            "every run recomputes. A cache miss is slower, never wrong: this one "
            "costs you tokens and latency, not correctness."
        )
    if retried:
        bullets.append(
            f"- **`ON_FAIL` retries run without backoff** (`W_WF_002`) — declared by "
            f"{_backticked(retried)}. The sandbox has no clock, so the attempts run "
            "back-to-back. If you are retrying against something rate-limited, it "
            "will not wait between attempts."
        )
    if asserted:
        bullets.append(
            f"- **`CONTRACT … ASSERT` is not enforced** (`W_WF_003`) — declared by "
            f"{_backticked(asserted)}. The host DOES validate a judgment step's "
            "output against the JSON Schema (types, ranges, enums); it does not "
            "evaluate the ASSERT predicate. Re-check it downstream if you depend "
            "on it holding."
        )
    return bullets


_SANDBOX = (
    "the workflow sandbox has no filesystem, no network, no process and no clock "
    "— `Date.now()`, `new Date()` and `Math.random()` throw if you call them"
)


def render_readme(graph: FlowGraph, steps: tuple[StepIR, ...]) -> str:
    """README.md — how to install the script, what stays your job, what this target
    does not honor.

    `steps` is what the script actually declares (workflow.emitted_steps), not
    `graph.steps`: it is read here only to name the exact stubs the author has to
    fill in, and naming one that is not in the file would be a wild goose chase.

    The purity rule is stated in BOTH branches below rather than once, after them:
    an exact-step-free flow that reads "there is nothing to fill in" followed by
    "what you write must be pure" is telling the author about a `there` that does
    not exist, and prose nobody can place is prose nobody reads.
    """
    flow = entry_flow(graph)
    name = workflow_name(graph)
    script = f"{name}.workflow.js"
    exact = [s.name for s in steps if s.mode == "exact"]

    if exact:
        plural = "step" if len(exact) == 1 else "steps"
        stubs = (
            f"This flow has {len(exact)} exact {plural}: {_backticked(exact)}. The "
            "compiler emitted the signature, the state keys to read and the field to "
            "return — never the body. **Each stub throws until you fill it in**, and "
            f"what you fill in must be **pure JavaScript**: {_SANDBOX}.\n"
            "\n"
            "A step that needs IO cannot run here at all. Compile that flow with "
            "`--target python`, `go` or `swift` instead."
        )
    else:
        stubs = (
            "Nothing. This flow declares no exact step — every step is a judgment "
            "step, run by a subagent — so the script runs as emitted.\n"
            "\n"
            f"Add an exact step later and its body must be **pure JavaScript**: "
            f"{_SANDBOX}. A step that needs IO cannot run here at all."
        )

    degraded = _degradations(graph)
    caveats = "\n".join(degraded) if degraded else (
        "Nothing, for this flow. It declares no `CACHE:`, no `ON_FAIL` retry and no "
        "CONTRACT `ASSERT` — the three things this target degrades. Had it declared "
        "one, it would be listed here, under the same `W_WF_NNN` code the compiler "
        "prints when it compiles the source."
    )

    return f"""\
# {flow.name} — claude-workflow

Compiled by CLIO from a `.clio` source. The script is **`{script}`**: it runs this
flow inside a Claude Code session, spawning a subagent for each judgment step.

## Install

```bash
mkdir -p .claude/workflows
cp {script} .claude/workflows/
```

Then invoke it from a Claude Code session — it registers under the name `{name}`,
declared in the script's own `meta` block. CLIO only ever writes inside its
`--output` directory, so this copy is yours to make.

## Run

Nothing to install, nothing to configure: **no API key**, no runtime, no dependency.
The Claude Code session IS the runtime — it reads `meta`, runs the script, and
spawns the subagents. (Same bargain as the `claude-skill` target: the host executes,
CLIO only emits.)

## What is still your job

{stubs}

## What this target does not honor

{caveats}

## Recovering the source

`.clio/` holds the source this was compiled from, plus a hash manifest of the files
above. To get the source back, verbatim:

```bash
clio import . --mode strict --output recovered.clio
```

Strict mode refuses on drift — including the drift you create yourself, the moment
you fill a stub in. Drop the flag to recover from a directory you have already
edited.
"""


def _noop_warn(_msg: str) -> None:
    return None


def validate_graph_for_workflow(
    graph: FlowGraph, warn: Callable[[str], None] = _noop_warn
) -> None:
    """Raise ValueError with a stable E_WF_NNN code for anything this target
    cannot honor; call `warn` for anything it degrades. Called as the first
    statement of emit().

    `warn` is injected (rather than printing directly) so tests can capture it —
    same seam as ClaudeSkillEmitter._validate.

    TEST blocks are deliberately NOT refused: they are inert here (only the
    python target emits pytest files), and refusing them would reject a source
    over a block this target simply ignores.

    One refusal is NOT here: E_WF_007 (FLOW recursion) is raised by
    _workflow_subflows.reachable_flows, which owns the flow-call graph and is the
    only walk that can see a back edge. render_script calls it before it emits a
    line, so emit() still refuses a recursive source before writing a file.
    """
    entry_flow(graph)  # E_WF_001 (no FLOW) / E_WF_006 (several, none selected)

    for step in graph.steps:
        where = f"(step={step.name!r}, line {step.line})"

        # E_WF_003 first: an IO impl is refused whatever LANG it carries.
        if isinstance(step.impl, _IO_IMPLS):
            raise ValueError(f"{E_WF_003} {where}")

        # E_WF_004: a non-JS exact body, in either spelling — the `LANG:`
        # directive (StepIR.lang) or impl.lang (CodeImplIR.lang).
        if step.mode == "exact" and step.lang not in _WF_OK_LANGS:
            raise ValueError(f"{E_WF_004} {where} lang={step.lang!r}")
        if isinstance(step.impl, CodeImplIR) and step.impl.lang not in _WF_OK_LANGS:
            raise ValueError(f"{E_WF_004} {where} lang={step.impl.lang!r}")

        # E_WF_002: an agent() cannot reach a non-Anthropic provider.
        # CliInvokeIR needs no mapping: here the agent() call IS the Claude Code
        # invocation. It is accepted as-is.
        if isinstance(step.invoke, ApiInvokeIR) and step.invoke.protocol != "anthropic":
            raise ValueError(
                f"{E_WF_002} {where} protocol={step.invoke.protocol!r}"
            )

        if step.cache is not None and step.cache.mode != "off":
            warn(f"{W_WF_001} {where}")
        if step.on_fail is not None and any(
            s.kind == "retry" for s in step.on_fail.strategies
        ):
            warn(f"{W_WF_002} {where}")

    for contract in graph.contracts:
        if contract.assert_json_ast is not None:
            warn(f"{W_WF_003} (contract={contract.name!r}, line {contract.line})")
