"""FOR EACH for target: claude-workflow — the sequential loop, and the fan-out.

This is the module that justifies the target. `FOR EACH … PARALLEL` is degraded to
sequential by claude-skill (with a warning) and refused outright by claude-cli;
here it becomes real concurrency, because the Workflow host gives us `parallel()`
over live subagents.

`parallel()` is the only fan-out primitive emitted. The host also offers
`pipeline()` — one item threaded through several stages — but the language has no
source that reaches it: the IR builder refuses a PARALLEL body of more than one
call for every .clio file there is (builder.py:2043). Rendering it anyway would be
a branch no user can run.

It renders a STATEMENT, so it needs to render its own body — and the body may
contain anything, including another FOR EACH. Rather than import the dispatcher
(which imports this module: a cycle), it takes `render_body` as a parameter. That
one callback is the whole coupling between the two modules.
"""
from __future__ import annotations

from collections.abc import Callable

from clio.emitters._workflow_expressions import (
    Bindings,
    call_js,
    flow_input,
    loop_binding,
    read,
)
from clio.emitters._workflow_helpers import js_string, loop_var_js
from clio.emitters._workflow_subflows import subflow_fn_name
from clio.ir.graph import (
    CallIR,
    FlowCallIR,
    FlowIR,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
    WhileBlockIR,
)

# `_render_body` of the flow renderer: (body, steps_by_name, phase_js, indent, bindings).
RenderBody = Callable[
    [tuple[object, ...], dict[str, StepIR], str, str, Bindings], list[str]
]

# `$$`, and not a bare name: these two are out of reach of BOTH manglings —
# js_identifier only ever appends a `$` to a step's name, and a loop variable is `$`
# + a CLIO name, whose first character is never a `$`. So neither can be shadowed by
# a step or a loop variable the author happens to name after them.
_SETTLE = "$$settle"
_COLLECT = "$$collect"

# The two functions the emitted script needs to tell a FAILED item from a
# legitimate `null` one. Emitted once, and only into a script that fans out.
#
# A plain string, not an f-string: every `${…}` below is a JS template-literal
# interpolation, and the names are already spelled the way they are emitted.
PARALLEL_RUNTIME = """\
// A thunk that throws resolves to `null` in the result array of parallel() — the
// call itself never rejects (§6.1). But a SUCCESSFUL item can be `null` too: a step
// whose GIVES is `Optional<T>` returns one, and the host validates it against the
// schema. In the raw array the two are the same value.
//
// So each thunk reports its own outcome rather than having it guessed at
// afterwards. $$collect then applies the rule every other target has — a step that
// fails fails the FLOW; its ON_FAIL chain already ran, inside its own function, and
// a RESCUE cannot protect a call made inside a FOR EACH (it only ever wraps a
// top-level one) — and maps the successes back IN ORDER, so state[<collector>][i]
// stays the result of item i. A legitimate `null` survives; a failure is never
// silently dropped.
async function $$settle(fn) {
  try {
    return { ok: true, value: await fn() }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
}

function $$collect(results, items, where) {
  return results.map((r, i) => {
    if (r !== null && r !== undefined && r.ok) {
      return r.value
    }
    const item = String(JSON.stringify(items[i])).slice(0, 200)
    // A raw null means no outcome came back at all: the host killed the subagent
    // outright, out of reach of the try/catch above.
    const why = r === null || r === undefined ? 'no outcome reported' : r.error
    throw new Error(`clio: ${where}: item ${i} (${item}) failed: ${why}`)
  })
}
"""


def render_foreach(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
    render_body: RenderBody,
) -> list[str]:
    """A sequential FOR EACH: a native `for…of` over the collection.

    The loop variable enters `bindings` for the body — every read of it (a kwarg
    `@doc`, a `MATCH doc.level`) then resolves to the JS binding instead of to a
    state key that does not exist. The collection itself is read through the OUTER
    bindings: in a nested `FOR EACH b IN a`, `a` is the enclosing loop's variable.

    Results are discarded, and that is the language's rule rather than a shortcut
    here: the parser refuses `AS` without `PARALLEL` (parser.py:2371-2375).

    `loop_var_js`, so the `const` this head binds cannot shadow the function of a
    step the body calls — `FOR EACH classify IN items: classify(item=classify)` is
    legal CLIO, and `for (const classify of …) { await classify(…) }` is legal JS
    that throws `TypeError: classify is not a function` on every item.
    """
    if item.parallel:
        return _render_parallel(item, steps_by_name, phase_js, indent, bindings)

    var = loop_var_js(item.loop_var)
    inner = loop_binding(item.loop_var, bindings)
    lines = [f"{indent}for (const {var} of {read(item.collection, bindings)}) {{"]
    lines += render_body(item.body, steps_by_name, phase_js, indent, inner)
    lines.append(f"{indent}}}")
    return lines


def _render_parallel(
    item: ForEachIR,
    steps_by_name: dict[str, StepIR],
    phase_js: str,
    indent: str,
    bindings: Bindings,
) -> list[str]:
    """The payoff: real fan-out, collected into `state[<collector>]`.

    `parallel(items.map(x => () => step(x)))`. The inner arrow is load-bearing:
    parallel() takes THUNKS, and `.map(x => step(x))` would start every call during
    the map and hand it promises already in flight — bypassing the very concurrency
    limit it exists to enforce.

    ONE call per item, because the language states nothing else: the IR builder
    refuses a PARALLEL body of more than one call for every source there is
    (builder.py:2043). The two checks below are that invariant asserted where it is
    relied on — not a user-facing path. A source that trips them does not exist; only
    hand-built IR can, and it must not be rendered into some concurrency the author
    never asked for.

    The body renders no nested statement, so this branch needs no `render_body`:
    the IR builder allows only calls here (builder.py:2043-2079).

    The call is wrapped in `$$settle` and the result array goes through `$$collect`,
    which is what keeps a failed item apart from an item that legitimately gave
    `null` — see PARALLEL_RUNTIME.

    So a step that fails here fails the FLOW. That is not this module overriding the
    author's error handling: an ON_FAIL chain has already run inside the step's own
    function by the time $$settle sees a throw, and a RESCUE cannot protect a call
    made inside a FOR EACH at all — render_guarded only ever wraps a top-level one.
    """
    if item.collector is None:
        raise AssertionError(
            "unreachable: PARALLEL requires an AS binding (parser.py:2362-2366)"
        )
    if len(item.body) != 1:
        raise AssertionError(
            f"unreachable: a PARALLEL FOR EACH body holds exactly one step or "
            f"sub-flow call (builder.py:2043) — got {len(item.body)} at line "
            f"{item.line}"
        )

    var = loop_var_js(item.loop_var)
    items = read(item.collection, bindings)
    inner = loop_binding(item.loop_var, bindings)
    target = f"{indent}state[{js_string(item.collector)}] ="
    # The item that failed is named by its position in the collection and by the FOR
    # EACH that produced it — with the source line, as every CLIO diagnostic carries.
    where = js_string(f"FOR EACH {item.loop_var} IN {item.collection} (line {item.line})")
    collected = f"{indent}), {items}, {where})"

    call = item.body[0]
    if isinstance(call, FlowCallIR):
        return _render_parallel_subflow(
            call, var, items, target, indent, phase_js, inner, collected
        )
    if not isinstance(call, CallIR):
        raise AssertionError(
            f"unreachable: a PARALLEL FOR EACH body is a step or a sub-flow call "
            f"(builder.py:2049-2079) — got {type(call).__name__} at line {item.line}"
        )

    thunk = call_js(call, steps_by_name, phase_js, inner).removeprefix("await ")
    return [
        f"{target} {_COLLECT}(await parallel(",
        f"{indent}  {items}.map(({var}) => () => {_SETTLE}(() => {thunk})),",
        collected,
    ]


def _render_parallel_subflow(
    call: FlowCallIR,
    var: str,
    items: str,
    target: str,
    indent: str,
    phase_js: str,
    inner: Bindings,
    collected: str,
) -> list[str]:
    """A sub-flow call as the PARALLEL body: `parallel(items.map(x => () => flow_$f(…)))`.

    The IR builder allows exactly this (builder.py:2057-2069) and states what the
    collector then holds: "a list of the sub-flow's GIVES dicts at runtime". The
    inlined function returns precisely that object, so the thunk IS the call — no
    field is extracted. (go extracts the lone GIVES field instead, because a Go
    slice must have one static element type; JS has no such constraint, and
    extracting here would silently drop the other fields of a multi-GIVES sub-flow.)

    The thunk arrow is the same load-bearing one as for a step call — parallel()
    takes THUNKS, and mapping straight to the promise would start every sub-flow
    during the map, bypassing the concurrency limit.
    """
    fn = subflow_fn_name(call.flow_name)
    thunk = f"{fn}({flow_input(call.kwargs, inner)}, {phase_js})"
    return [
        f"{target} {_COLLECT}(await parallel(",
        f"{indent}  {items}.map(({var}) => () => {_SETTLE}(() => {thunk})),",
        collected,
    ]


def needs_parallel_runtime(flows: tuple[FlowIR, ...]) -> bool:
    """Does any of these flows fan out? Only then is PARALLEL_RUNTIME emitted.

    A linear flow that carried two functions it never calls would be dead code in a
    file whose whole point is that the author reads it and fills its stubs in.
    """
    def walk(items: tuple[object, ...]) -> bool:
        for it in items:
            if isinstance(it, ForEachIR):
                if it.parallel or walk(it.body):
                    return True
            elif isinstance(it, IfBlockIR):
                if walk(it.then_body) or walk(it.else_body):
                    return True
            elif isinstance(it, MatchBlockIR):
                if any(walk(c.body) for c in it.cases):
                    return True
            elif isinstance(it, WhileBlockIR):
                if walk(it.body):
                    return True
        return False

    return any(
        walk(f.chain) or any(walk(r.body) for r in f.rescues) for f in flows
    )
