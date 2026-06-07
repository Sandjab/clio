"""Renderer for the top-level Flow orchestrator (Sources/ClioFlow/Flow.swift).

Phase 3a: adds IF/ELSE, MATCH/CASE, WHILE control flow to the linear
chain of CallIR items supported in Phase 1/2.
"""
from __future__ import annotations

from clio.emitters._swift_helpers import _type_to_swift
from clio.emitters._swift_step_renderers import _step_struct_prefix
from clio.ir.graph import (
    BoolOpIR,
    CallIR,
    ConditionIR,
    ContractIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    StepIR,
    WhileBlockIR,
)


def _build_state_field_to_step(graph: FlowGraph) -> dict[str, StepIR]:
    """Map each state-dict key (GIVES field name) to the StepIR that produced it.

    Walks the flow's chain recursively into nested IF/MATCH/WHILE bodies so
    steps inside control-flow blocks get a typed `as!` cast.  Mirrors the
    recursive walk in _go_flow_renderer._build_state_field_to_step.
    """
    result: dict[str, StepIR] = {}
    if graph.flow is None:
        return result
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}

    def walk(items: tuple) -> None:  # type: ignore[type-arg]
        for it in items:
            if isinstance(it, CallIR):
                step = steps_by_name.get(it.step_name)
                if step is not None and step.gives is not None:
                    result[step.gives.name] = step
            elif isinstance(it, IfBlockIR):
                walk(it.then_body)
                walk(it.else_body)
            elif isinstance(it, MatchBlockIR):
                for case in it.cases:
                    walk(case.body)
            elif isinstance(it, WhileBlockIR):
                walk(it.body)
            elif isinstance(it, ForEachIR):
                walk(it.body)

    walk(graph.flow.chain)
    return result


def _swift_condition_expr(
    condition: ConditionIR | BoolOpIR,
    scope_local: set[str],
    state_field_to_step: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    take_types: dict[str, str],
) -> str:
    """Render a CLIO IF/WHILE condition as a Swift boolean expression.

    Mirrors _go_condition_expr (from _shared_utils) but uses Swift `as!` casts
    and parenthesizes field-access casts correctly.

    Resolution order for `ConditionIR.step_name` (state-dict key):
      1. Loop variable (scope_local) — bare identifier, no state lookup.
      2. Step producer (state_field_to_step) — `(state["k"] as! SwiftType).field`
      3. Flow TAKE (take_types) — `(state["k"] as! SwiftType).field`
      4. Unknown — untyped fallback (should not occur after IR validation).

    `BoolOpIR` renders as `(left) &&/|| (right)` — unconditional parentheses
    preserve IR precedence at any nesting depth.
    """
    if isinstance(condition, BoolOpIR):
        left = _swift_condition_expr(
            condition.left, scope_local, state_field_to_step, contracts_by_name, take_types
        )
        right = _swift_condition_expr(
            condition.right, scope_local, state_field_to_step, contracts_by_name, take_types
        )
        swift_op = "&&" if condition.op == "and" else "||"
        return f"({left}) {swift_op} ({right})"

    # Leaf: ConditionIR
    # condition.step_name is the state-dict key (GIVES field name), not the step's name.
    state_field = condition.step_name
    step = state_field_to_step.get(state_field)

    if state_field in scope_local:
        # Loop variable inside FOR EACH — bare identifier, field accessed directly.
        access = f"{state_field}.{condition.field}"
    elif step is not None and step.gives is not None:
        swift_type = _type_to_swift(step.gives.type, contracts_by_name)
        access = f'(state["{state_field}"] as! {swift_type}).{condition.field}'
    elif state_field in take_types:
        access = f'(state["{state_field}"] as! {take_types[state_field]}).{condition.field}'
    else:
        # Unknown state field — fallback (should not occur after IR validation).
        access = f'state["{state_field}"]'

    # Render the RHS literal in Swift syntax.
    if condition.literal_kind == "int":
        lit = str(condition.literal_value)
    elif condition.literal_kind == "float":
        lit = repr(condition.literal_value)
    elif condition.literal_kind == "bool":
        lit = "true" if condition.literal_value else "false"
    elif condition.literal_kind == "ident":
        # Enum ident — rendered as a Swift string literal (same as Go).
        escaped = str(condition.literal_value).replace("\\", "\\\\").replace('"', '\\"')
        lit = f'"{escaped}"'
    else:
        # str — Swift interpreted string literal.
        escaped = str(condition.literal_value).replace("\\", "\\\\").replace('"', '\\"')
        lit = f'"{escaped}"'

    return f"{access} {condition.op} {lit}"


def _swift_kwarg_value(
    value: object,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    take_types: dict[str, str],
    scope_local: set[str] | None = None,
) -> str:
    """Render one CallIR kwarg value as a Swift expression.

    Resolution order for ``@<ref>``:
      1. Loop variable (scope_local) — bare identifier, no state lookup.
      2. Step producer (state_field_to_step) — ``state["k"] as! SwiftType``
      3. Flow TAKE (take_types) — ``state["k"] as! SwiftType``
      4. Unknown — untyped fallback.
    """
    _scope = scope_local or set()
    if isinstance(value, str) and value.startswith("@"):
        ref = value[1:]
        if ref in _scope:
            # Loop variable inside FOR EACH — bare identifier, no state lookup.
            return ref
        step = state_field_to_step.get(ref)
        if step is not None and step.gives is not None:
            swift_type = _type_to_swift(step.gives.type, contracts)
            return f'state["{ref}"] as! {swift_type}'
        if ref in take_types:
            return f'state["{ref}"] as! {take_types[ref]}'
        # Unknown ref — untyped fallback (should not occur after IR validation).
        return f'state["{ref}"]'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    # String literal
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_kwarg_swift_type(
    ref: str,
    contracts: dict[str, ContractIR],
    state_field_to_step: dict[str, StepIR],
    take_types: dict[str, str],
) -> str | None:
    """Return the Swift type of a `@<ref>` kwarg value, or None if unresolvable.

    Mirrors the resolution order in _swift_kwarg_value (step producer, then flow
    TAKE). The loop-var case is handled by the caller before this is reached.
    Used by the parallel renderer to decide whether a hoisted local is Sendable.
    """
    step = state_field_to_step.get(ref)
    if step is not None and step.gives is not None:
        return _type_to_swift(step.gives.type, contracts)
    if ref in take_types:
        return take_types[ref]
    return None


def _type_tokens(swift_type: str) -> set[str]:
    """Split a Swift type expression into its identifier tokens.

    `[String: Any]` -> {'String', 'Any'}; `[Risk]` -> {'Risk'}. Used to detect
    a bare `Any` (non-Sendable) anywhere inside a composite type."""
    tokens: set[str] = set()
    cur = ""
    for ch in swift_type:
        if ch.isalnum() or ch == "_":
            cur += ch
        else:
            if cur:
                tokens.add(cur)
            cur = ""
    if cur:
        tokens.add(cur)
    return tokens


def _render_chain_item(
    item: object,
    call_idx: list[int],
    indent: str,
    *,
    steps_by_name: dict[str, StepIR],
    state_field_to_step: dict[str, StepIR],
    contracts_by_name: dict[str, ContractIR],
    take_types: dict[str, str],
    step_to_idx: dict[str, int],
    scope_local: set[str] | None = None,
) -> list[str]:
    """Render one chain item to Swift lines.

    `indent` is the current indentation string (top-level = 8 spaces; each
    nested block level adds 4 more spaces — Swift convention).
    `scope_local` is the set of active loop-variable names; used by the
    condition and kwarg renderers to skip the state-dict lookup for loop
    variables. Accumulates across nested FOR EACH blocks.

    Supported: CallIR, IfBlockIR, MatchBlockIR, WhileBlockIR, ForEachIR
    (both sequential and parallel — parallel uses withThrowingTaskGroup with a
    cap-10 back-pressure and ordered collect).
    Other item types fall through to the backstop ValueError.
    """
    _scope = scope_local or set()

    if isinstance(item, CallIR):
        step = steps_by_name.get(item.step_name)
        if step is None:
            return []

        call_idx[0] += 1
        n = call_idx[0]
        idx = step_to_idx[step.name]
        prefix = _step_struct_prefix(idx, step.name)

        in_args: list[str] = []
        for name, val in item.kwargs:
            swift_val = _swift_kwarg_value(
                val, contracts_by_name, state_field_to_step, take_types, _scope
            )
            in_args.append(f"{name}: {swift_val}")

        lines: list[str] = [
            f"{indent}let in{n} = {prefix}_In({', '.join(in_args)})",
            f"{indent}let out{n} = try await step_{step.name}(in{n})",
        ]
        if step.gives is not None:
            lines.append(f'{indent}state["{step.gives.name}"] = out{n}.{step.gives.name}')
        else:
            # Side-effect step (no GIVES): out{n} is never read. Swift warns on
            # an unused immutable binding, so discard it explicitly. Mirrors the
            # Go target's `_ = {out_var}` (see _go_flow_renderer.py).
            lines.append(f"{indent}_ = out{n}")
        lines.append("")
        return lines

    if isinstance(item, IfBlockIR):
        cond = _swift_condition_expr(
            item.condition, _scope, state_field_to_step, contracts_by_name, take_types
        )
        inner_indent = indent + "    "
        lines = [f"{indent}if {cond} {{"]
        for sub in item.then_body:
            lines.extend(_render_chain_item(
                sub, call_idx, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                take_types=take_types,
                step_to_idx=step_to_idx,
                scope_local=scope_local,
            ))
        if item.else_body:
            lines.append(f"{indent}}} else {{")
            for sub in item.else_body:
                lines.extend(_render_chain_item(
                    sub, call_idx, inner_indent,
                    steps_by_name=steps_by_name,
                    state_field_to_step=state_field_to_step,
                    contracts_by_name=contracts_by_name,
                    take_types=take_types,
                    step_to_idx=step_to_idx,
                    scope_local=scope_local,
                ))
        lines.append(f"{indent}}}")
        lines.append("")
        return lines

    if isinstance(item, MatchBlockIR):
        step = state_field_to_step.get(item.state_field)
        if item.state_field in _scope:
            # Loop variable — bare identifier, sub-field accessed directly.
            scrutinee = f"{item.state_field}.{item.sub_field}"
        elif step is not None and step.gives is not None:
            swift_type = _type_to_swift(step.gives.type, contracts_by_name)
            scrutinee = f'(state["{item.state_field}"] as! {swift_type}).{item.sub_field}'
        elif item.state_field in take_types:
            scrutinee = (
                f'(state["{item.state_field}"] as! {take_types[item.state_field]})'
                f".{item.sub_field}"
            )
        else:
            scrutinee = f'state["{item.state_field}"]'

        inner_indent = indent + "    "
        has_default = any(arm.value is None for arm in item.cases)
        lines = [f"{indent}switch {scrutinee} {{"]
        for arm in item.cases:
            if arm.value is None:
                lines.append(f"{inner_indent}default:")
            else:
                escaped = arm.value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{inner_indent}case "{escaped}":')
            for sub in arm.body:
                lines.extend(_render_chain_item(
                    sub, call_idx, inner_indent + "    ",
                    steps_by_name=steps_by_name,
                    state_field_to_step=state_field_to_step,
                    contracts_by_name=contracts_by_name,
                    take_types=take_types,
                    step_to_idx=step_to_idx,
                    scope_local=scope_local,
                ))
        if not has_default:
            # Swift switch on String MUST be exhaustive — emit a fallthrough guard.
            lines.append(f"{inner_indent}default: break")
        lines.append(f"{indent}}}")
        lines.append("")
        return lines

    if isinstance(item, WhileBlockIR):
        # Use a unique counter variable to implement the MAX bound.
        # Increment call_idx to share the monotonic counter; the _whileN variable
        # name uses a different prefix from in/out so no collision occurs.
        call_idx[0] += 1
        n = call_idx[0]
        cond = _swift_condition_expr(
            item.condition, _scope, state_field_to_step, contracts_by_name, take_types
        )
        inner_indent = indent + "    "
        lines = [
            f"{indent}var _while{n} = 0",
            f"{indent}while ({cond}) && _while{n} < {item.max_iters} {{",
        ]
        for sub in item.body:
            lines.extend(_render_chain_item(
                sub, call_idx, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                take_types=take_types,
                step_to_idx=step_to_idx,
                scope_local=scope_local,
            ))
        lines.append(f"{inner_indent}_while{n} += 1")
        lines.append(f"{indent}}}")
        lines.append("")
        return lines

    if isinstance(item, ForEachIR) and not item.parallel:
        # Render the collection expression: `state["<coll>"] as! [<ElemType>]`.
        # The loop variable type is inferred by Swift from the typed array cast.
        coll_name = item.collection
        coll_step = state_field_to_step.get(coll_name)
        if coll_step is not None and coll_step.gives is not None:
            list_swift_type = _type_to_swift(coll_step.gives.type, contracts_by_name)
            coll_expr = f'state["{coll_name}"] as! {list_swift_type}'
        elif coll_name in take_types:
            coll_expr = f'state["{coll_name}"] as! {take_types[coll_name]}'
        else:
            # Unknown collection source — fall back to untyped (should not
            # happen after IR validation).
            coll_expr = f'state["{coll_name}"] as! [Any]'

        var = item.loop_var
        inner_indent = indent + "    "
        # Accumulate loop_var into scope_local so nested MATCH/IF/kwarg
        # renderers resolve it as a bare identifier (not a state-dict lookup).
        inner_scope = _scope | {var}
        lines = [f"{indent}for {var} in ({coll_expr}) {{"]
        for sub in item.body:
            lines.extend(_render_chain_item(
                sub, call_idx, inner_indent,
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                take_types=take_types,
                step_to_idx=step_to_idx,
                scope_local=inner_scope,
            ))
        lines.append(f"{indent}}}")
        lines.append("")
        return lines

    if isinstance(item, ForEachIR) and item.parallel:
        # Parallel FOR EACH — withThrowingTaskGroup with cap-10 back-pressure
        # and ordered collect via a pre-allocated [Int: ResultType] dictionary.
        #
        # Swift 6 Sendable guarantee: group.addTask closures capture only the
        # loop element (value type or Codable/Sendable struct), _idx{n} (Int),
        # and hoisted Sendable locals (see the kwarg-hoisting block below).
        # They do NOT touch `state` ([String: Any] is not Sendable).  All
        # mutations of _collected{n} happen in the TaskGroup body closure, which
        # runs on the same actor as the caller (@MainActor for Flow.run).
        #
        # Non-single-CallIR body: refused fail-loud here.  In Phase 3c the
        # reachable body is always a single CallIR step (FlowCallIR is
        # unreachable because the len(graph.flows) > 1 gate fires first).
        if len(item.body) != 1 or not isinstance(item.body[0], CallIR):
            raise ValueError(
                "swift target: parallel FOR EACH body must be a single step "
                "call; compound/control-flow bodies are not yet supported — "
                "use --target python or go for now"
            )
        body_call: CallIR = item.body[0]  # type: ignore[assignment]
        body_step = steps_by_name.get(body_call.step_name)
        if body_step is None:
            return []  # unknown step — IR validation should have caught this

        call_idx[0] += 1
        n = call_idx[0]

        coll_name = item.collection
        coll_step = state_field_to_step.get(coll_name)

        # Collection cast.
        # elem_type is not used directly; the Swift compiler infers the loop-var
        # type from the array cast.  result_type (derived from the body step's
        # GIVES) drives the TaskGroup return type.
        if coll_step is not None and coll_step.gives is not None:
            list_swift_type = _type_to_swift(coll_step.gives.type, contracts_by_name)
            coll_expr = f'state["{coll_name}"] as! {list_swift_type}'
        elif coll_name in take_types:
            coll_expr = f'state["{coll_name}"] as! {take_types[coll_name]}'
        else:
            coll_expr = f'state["{coll_name}"] as! [Any]'

        # Result type: what the body step GIVES (one field).
        result_type = "Any"
        gives_field = ""
        if body_step.gives is not None:
            result_type = _type_to_swift(body_step.gives.type, contracts_by_name)
            gives_field = body_step.gives.name

        collector = item.collector or "results"
        body_step_idx = step_to_idx[body_step.name]
        body_prefix = _step_struct_prefix(body_step_idx, body_step.name)

        # Build kwargs for the In struct constructor inside group.addTask.
        #
        # Sendable hoisting (Fix 1): the group.addTask closure is @Sendable and
        # runs OFF the main actor, so it may NOT capture `state` ([String: Any],
        # non-Sendable, and a `var`). Any kwarg that reads `state[...]` (an
        # upstream GIVES/TAKE, i.e. NOT the loop var and NOT a literal) is HOISTED
        # to a `let` on the actor before withThrowingTaskGroup; the closure then
        # references that Sendable local. The loop var stays inline (Sendable
        # element); literals stay inline (Sendable constants).
        inner_scope = _scope | {item.loop_var}
        hoist_lines: list[str] = []
        task_args: list[str] = []
        for kname, kval in body_call.kwargs:
            if isinstance(kval, str) and kval.startswith("@"):
                ref = kval[1:]
                if ref in inner_scope:
                    # Loop var (or outer loop var) — Sendable, stays inline.
                    task_args.append(f"{kname}: {ref}")
                    continue
                # Upstream state read — must be hoisted to the actor.
                swift_val = _swift_kwarg_value(
                    kval, contracts_by_name, state_field_to_step, take_types, inner_scope
                )
                hoisted_type = _resolve_kwarg_swift_type(
                    ref, contracts_by_name, state_field_to_step, take_types
                )
                if hoisted_type is None or "Any" in _type_tokens(hoisted_type):
                    raise ValueError(
                        f"swift target: parallel FOR EACH body cannot capture a "
                        f"non-Sendable value {ref!r} (resolved Swift type "
                        f"{hoisted_type or 'unknown'!r}); use --target python or "
                        f"go for now"
                    )
                local = f"_kw{n}_{ref}"
                hoist_lines.append(f"{indent}let {local} = {swift_val}")
                task_args.append(f"{kname}: {local}")
            else:
                # Literal (str/int/float/bool) — Sendable constant, stays inline.
                swift_val = _swift_kwarg_value(
                    kval, contracts_by_name, state_field_to_step, take_types, inner_scope
                )
                task_args.append(f"{kname}: {swift_val}")

        var = item.loop_var
        task_return_type = f"(Int, {result_type})"
        # Indentation levels (base `indent` = 8 spaces for the top-level FLOW):
        #   group_indent:     body of withThrowingTaskGroup { group in ... }
        #   for_indent:       body of for ... { ... }
        #   addtask_indent:   body of group.addTask { ... }
        group_indent = indent + "    "
        for_indent = indent + "        "
        addtask_indent = indent + "            "

        # Collector registration follows Go's pattern: do NOT register for a
        # step body (only sub-flow bodies are registered in Go, and sub-flows
        # are unreachable in Phase 3c).  Speculative registration would emit
        # wrong scalar-typed reads of an array collector downstream.

        par_lines: list[str] = [
            f"{indent}let _items{n} = {coll_expr}",
            *hoist_lines,
            f"{indent}var _collected{n} = [Int: {result_type}](minimumCapacity:"
            f" _items{n}.count)",
            f"{indent}try await withThrowingTaskGroup("
            f"of: {task_return_type}.self) {{ group in",
            f"{group_indent}var _inflight{n} = 0",
            f"{group_indent}for (_idx{n}, {var}) in _items{n}.enumerated() {{",
            f"{for_indent}if _inflight{n} >= 10 {{",
            f"{for_indent}    if let (_i, _r) = try await group.next() {{",
            f"{for_indent}        _collected{n}[_i] = _r",
            f"{for_indent}        _inflight{n} -= 1",
            f"{for_indent}    }}",
            f"{for_indent}}}",
            f"{for_indent}group.addTask {{",
            f"{addtask_indent}let _in = {body_prefix}_In({', '.join(task_args)})",
            f"{addtask_indent}let _out = try await step_{body_step.name}(_in)",
            f"{addtask_indent}return (_idx{n}, _out.{gives_field})",
            f"{for_indent}}}",
            f"{for_indent}_inflight{n} += 1",
            f"{group_indent}}}",
            f"{group_indent}while let (_i, _r) = try await group.next() {{",
            f"{group_indent}    _collected{n}[_i] = _r",
            f"{group_indent}}}",
            f"{indent}}}",
            f'{indent}state["{collector}"] = '
            f"(0..<_items{n}.count).map {{ _collected{n}[$0]! }}",
            "",
        ]
        return par_lines

    raise ValueError(
        f"E_SWIFT: {type(item).__name__} not yet supported"
    )


def render_flow_swift(graph: FlowGraph, step_to_idx: dict[str, int]) -> str:
    """Render Sources/ClioFlow/Flow.swift — top-level orchestrator."""
    assert graph.flow is not None
    flow = graph.flow
    contracts_by_name = {c.name: c for c in graph.contracts}
    steps_by_name = {s.name: s for s in graph.steps if isinstance(s, StepIR)}
    state_field_to_step = _build_state_field_to_step(graph)
    take_types: dict[str, str] = {
        f.name: _type_to_swift(f.type, contracts_by_name) for f in flow.takes
    }

    lines: list[str] = [
        "import Foundation",
        "",
        "public enum Flow {",
        "    @MainActor",
        "    public static func run(kwargs: [String: Any]) async throws -> [String: Any] {",
        "        var state = kwargs",
        "",
    ]

    call_idx = [0]
    for item in flow.chain:
        lines.extend(
            _render_chain_item(
                item,
                call_idx,
                "        ",
                steps_by_name=steps_by_name,
                state_field_to_step=state_field_to_step,
                contracts_by_name=contracts_by_name,
                take_types=take_types,
                step_to_idx=step_to_idx,
                scope_local=set(),
            )
        )

    lines.append("        return state")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)
