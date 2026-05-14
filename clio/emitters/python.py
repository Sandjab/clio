"""Emitter for `target: python`.

Produces a runnable Python package (Anthropic SDK + Pydantic v2) from a
target-independent IR. Reuses `clio/runtime/cache.py` and `clio/runtime/
logging.py` verbatim under the emitted package's `clio_runtime/`.

Module-level helpers live in `_python_helpers.py`; this file holds only
the PythonEmitter class.
"""

import json
from pathlib import Path

from clio.emitters._python_helpers import (
    _emit_attempt_block,
    _gives_validator_expr,
    _has_parallel,
    _model_id,
    _python_condition_expr,
    _step_signature,
    _to_field_name,
    _type_to_python,
    emit_contracts,
    emit_default_exact_step,
    emit_mcp_tool_step,
    emit_parallel_for_each_python,
    emit_rest_step,
    emit_shell_step,
    emit_sql_step,
)
from clio.emitters.base import BaseEmitter
from clio.ir.graph import (
    ApiInvokeIR,
    CallIR,
    ContractIR,
    DatabaseSpecIR,
    FlowGraph,
    ForEachIR,
    IfBlockIR,
    MatchBlockIR,
    McpServerSpecIR,
    McpToolImplIR,
    RestImplIR,
    ResumeIR,
    ShellImplIR,
    SqlImplIR,
    StepIR,
    WhileBlockIR,
)


class PythonEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        pkg_name = self._package_name(graph)
        pkg_dir = output_dir / pkg_name
        steps_dir = pkg_dir / "steps"
        runtime_dir = pkg_dir / "clio_runtime"

        for d in (pkg_dir, steps_dir, runtime_dir):
            d.mkdir(parents=True, exist_ok=True)

        (pkg_dir / "__init__.py").write_text("")
        (steps_dir / "__init__.py").write_text("")
        (runtime_dir / "__init__.py").write_text("")

        (pkg_dir / "contracts.py").write_text(emit_contracts(graph))

        contracts_by_name = {c.name: c for c in graph.contracts}
        mcp_servers_by_name = {
            s.name: s
            for s in (graph.resources.mcp_servers if graph.resources is not None else ())
        }
        databases_by_name = {
            d.name: d
            for d in (graph.resources.databases if graph.resources is not None else ())
        }
        for step in graph.steps:
            if step.mode == "exact":
                body = self._emit_exact_step(
                    step, contracts_by_name, mcp_servers_by_name, databases_by_name,
                )
            else:
                body = self._emit_judgment_step(step, graph, contracts_by_name)
            (steps_dir / f"{step.name}.py").write_text(body)

        needs_requests = any(
            isinstance(s.impl, RestImplIR) for s in graph.steps
        )
        needs_mcp = any(
            isinstance(s.impl, McpToolImplIR) for s in graph.steps
        )
        needs_sql = any(
            isinstance(s.impl, SqlImplIR) for s in graph.steps
        )
        needs_openai = any(
            isinstance(s.invoke, ApiInvokeIR) and s.invoke.protocol == "openai"
            for s in graph.steps
        )
        needs_anthropic = any(
            s.mode == "judgment" and (
                s.invoke is None
                or (isinstance(s.invoke, ApiInvokeIR) and s.invoke.protocol == "anthropic")
            )
            for s in graph.steps
        )
        needs_pydantic = bool(graph.contracts)
        (output_dir / "pyproject.toml").write_text(
            self._pyproject(
                pkg_name,
                needs_requests=needs_requests,
                needs_openai=needs_openai,
                needs_anthropic=needs_anthropic,
                needs_pydantic=needs_pydantic,
            )
        )
        (output_dir / "README.md").write_text(self._readme(pkg_name, graph))

        (pkg_dir / "flow.py").write_text(self._emit_flow(graph))
        (pkg_dir / "__main__.py").write_text(self._emit_main(pkg_name))

        from clio import runtime as src_pkg
        src_dir = Path(src_pkg.__file__).parent
        (runtime_dir / "cache.py").write_text((src_dir / "cache.py").read_text())
        (runtime_dir / "logging.py").write_text((src_dir / "logging.py").read_text())
        if needs_requests:
            (runtime_dir / "rest.py").write_text((src_dir / "rest.py").read_text())
        if needs_mcp:
            # mcp_client imports `subst` from rest, so always bundle rest too.
            if not needs_requests:
                (runtime_dir / "rest.py").write_text((src_dir / "rest.py").read_text())
            (runtime_dir / "mcp_client.py").write_text(
                (src_dir / "mcp_client.py").read_text()
            )
        if needs_sql:
            (runtime_dir / "sql.py").write_text((src_dir / "sql.py").read_text())

    def _emit_contracts(self, graph: FlowGraph) -> str:
        return emit_contracts(graph)

    def _emit_exact_step(
        self,
        step: StepIR,
        contracts_by_name: dict[str, "ContractIR"],
        mcp_servers_by_name: dict[str, "McpServerSpecIR"],
        databases_by_name: dict[str, "DatabaseSpecIR"],
    ) -> str:
        if isinstance(step.impl, RestImplIR):
            return self._emit_rest_step(step, contracts_by_name, step.impl)
        if isinstance(step.impl, ShellImplIR):
            return self._emit_shell_step(step, contracts_by_name, step.impl)
        if isinstance(step.impl, McpToolImplIR):
            spec = mcp_servers_by_name[step.impl.server]   # validated upstream by IR
            return emit_mcp_tool_step(
                step, contracts_by_name, step.impl, spec, async_call=False,
            )
        if isinstance(step.impl, SqlImplIR):
            db_spec = databases_by_name[step.impl.db]      # validated upstream by IR
            return emit_sql_step(step, contracts_by_name, step.impl, db_spec)

        # default branch (no impl, or impl.mode: code)
        return emit_default_exact_step(step, contracts_by_name)

    def _emit_rest_step(
        self,
        step: StepIR,
        contracts_by_name: dict[str, "ContractIR"],
        impl: RestImplIR,
    ) -> str:
        return emit_rest_step(step, contracts_by_name, impl)

    def _emit_shell_step(
        self,
        step: StepIR,
        contracts_by_name: dict[str, "ContractIR"],
        impl: ShellImplIR,
    ) -> str:
        return emit_shell_step(step, contracts_by_name, impl)

    def _emit_judgment_step(
        self,
        step: StepIR,
        graph: FlowGraph,
        contracts_by_name: dict[str, "ContractIR"],
    ) -> str:
        import json as _json

        from clio.emitters._claude_cli_helpers import _inline_schema, _render_prompt

        params = _step_signature(step, contracts_by_name)
        ret_type = (
            _type_to_python(step.gives.type, contracts_by_name)
            if step.gives is not None else "None"
        )

        inlined = (
            _inline_schema(step.gives.type, contracts_by_name)
            if step.gives is not None else {}
        )
        inlined_json = _json.dumps(inlined, separators=(",", ":"))

        # invoke.model overrides RESOURCES.models when set; otherwise the
        # RESOURCES.models list drives the escalate chain as before.
        invoke = step.invoke
        models: tuple[str, ...]
        models_full: tuple[str, ...]
        if isinstance(invoke, ApiInvokeIR):
            models = (invoke.model,)
            models_full = (invoke.model,)  # invoke.model is the raw provider ID
        else:
            models = (
                graph.resources.models
                if graph.resources is not None and graph.resources.models
                else ("haiku",)
            )
            models_full = tuple(_model_id(m) for m in models)
        models_array_repr = (
            "(" + ", ".join(repr(m) for m in models_full) + ",)"
            if len(models_full) == 1
            else "(" + ", ".join(repr(m) for m in models_full) + ")"
        )

        strategies = step.on_fail.strategies if step.on_fail is not None else ()
        has_fallback = any(s.kind == "fallback" for s in strategies)
        terminal_abort = bool(strategies) and strategies[-1].kind == "abort"

        prompt_template = _render_prompt(step)
        result_class = _gives_validator_expr(step.gives)

        cache_active = step.cache is not None and step.cache.mode != "off"
        ttl_repr = (
            "None" if step.cache is None or step.cache.mode == "on"
            else str(step.cache.ttl_seconds)
        )

        sub_lines = [
            f"    prompt = prompt.replace('${{{t.name}}}', json.dumps({_to_field_name(t.name)}))"
            for t in step.takes
        ]
        sub_lines.append("    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)")

        provider_imports, attempt_lines, attempt_needs_os = _emit_attempt_block(
            invoke, result_class,
        )

        header = [
            f'"""STEP {step.name} (judgment).',
            '',
            'Auto-generated. Do not edit; regenerate via `clio compile`.',
            '"""',
            "from __future__ import annotations",
            "",
            "import json",
            "import sys",
            "import time",
        ]
        if cache_active or attempt_needs_os:
            header += ["import os"]
        if cache_active:
            header += ["from pathlib import Path"]
        header += ["", *provider_imports, ""]
        header += ["from ..clio_runtime import logging as _log", ""]
        if cache_active:
            header += ["from ..clio_runtime import cache as _cache", ""]
        header += [
            "from .. import contracts",
            "",
            "",
            f"_PROMPT_TEMPLATE = {prompt_template!r}",
            f"_INLINED_SCHEMA = {inlined_json!r}",
            "_SYSTEM_PROMPT = (",
            "    'You are a strict JSON-only API. Output exactly one JSON document matching '",
            "    'the requested schema, with no prose, no markdown code fences, no commentary, '",
            "    'and no leading or trailing whitespace beyond the JSON itself.'",
            ")",
            f"_MODELS = {models_array_repr}",
            "",
            "",
        ]

        if cache_active:
            header += [
                "def _serialize(response):",
                '    """Re-serialize a validated response for cache storage."""',
                "    if isinstance(response, list):",
                (
                    "        return json.dumps("
                    "[(item.model_dump() if hasattr(item, 'model_dump') else item) for item in response]"
                    ")"
                ),
                "    if hasattr(response, 'model_dump'):",
                "        return json.dumps(response.model_dump())",
                "    return json.dumps(response)",
                "",
                "",
            ]

        body = list(header)
        body.append(f"def {step.name}({params}) -> {ret_type}:")
        body.append("    _t0 = time.monotonic()")
        body.append(f'    _log.emit("step_start", step={step.name!r}, mode="judgment")')
        body.append("    _last_usage: dict = {}")
        body.append("")
        # Inline _attempt as a closure so it can nonlocal-bind _last_usage
        body.extend("    " + line for line in attempt_lines)
        body.append("")
        body.append("    prompt = _PROMPT_TEMPLATE")
        body += sub_lines
        body.append("")

        chain_lines: list[str] = [
            "    model_idx = 0",
        ]
        if has_fallback:
            chain_lines.append("    fallback_used = False")
        chain_lines += [
            "    response = None",
            "",
        ]

        if cache_active:
            chain_lines += [
                "    cache_dir = Path(os.environ.get('CLIO_CACHE_DIR', '.cache'))",
                f"    primary_key = _cache.cache_key('{step.name}', _MODELS[0], prompt, _INLINED_SCHEMA)",
                f"    hit = _cache.cache_lookup(cache_dir, '{step.name}', primary_key, {ttl_repr})",
                "    if hit is not None:",
                "        try:",
                f"            _ret = {result_class}(json.loads(hit))",
                f'            _log.emit("step_end", step={step.name!r}, mode="judgment",',
                "                      duration_ms=int((time.monotonic() - _t0) * 1000),",
                "                      cache_hit=True, model=_MODELS[0],",
                "                      fallback_used=False, success=True)",
                "            return _ret",
                "        except Exception:",
                "            pass  # stale cache (schema changed): fall through to a fresh call",
                "",
            ]

        chain_lines += [
            "    response = _attempt(_MODELS[model_idx], prompt)",
            "",
        ]

        for s in strategies:
            if s.kind == "retry":
                n = s.max_retries
                chain_lines += [
                    "    if response is None:",
                    f"        for _ in range({n}):",
                    "            response = _attempt(_MODELS[model_idx], prompt)",
                    "            if response is not None:",
                    "                break",
                    "",
                ]
            elif s.kind == "escalate":
                if cache_active:
                    chain_lines += [
                        "    if response is None and model_idx < len(_MODELS) - 1:",
                        "        model_idx += 1",
                        (
                            f"        esc_key = _cache.cache_key('{step.name}', "
                            f"_MODELS[model_idx], prompt, _INLINED_SCHEMA)"
                        ),
                        (
                            f"        esc_hit = _cache.cache_lookup("
                            f"cache_dir, '{step.name}', esc_key, {ttl_repr})"
                        ),
                        "        if esc_hit is not None:",
                        "            try:",
                        f"                _ret = {result_class}(json.loads(esc_hit))",
                        f'                _log.emit("step_end", step={step.name!r}, mode="judgment",',
                        "                          duration_ms=int((time.monotonic() - _t0) * 1000),",
                        "                          cache_hit=True, model=_MODELS[model_idx],",
                        "                          fallback_used=False, success=True)",
                        "                return _ret",
                        "            except Exception:",
                        "                pass  # stale escalate cache: fall through",
                        "        response = _attempt(_MODELS[model_idx], prompt)",
                        "        if response is not None:",
                        (
                            f"            _cache.cache_store(cache_dir, '{step.name}', "
                            f"esc_key, _MODELS[model_idx], _serialize(response))"
                        ),
                        "",
                    ]
                else:
                    chain_lines += [
                        "    if response is None and model_idx < len(_MODELS) - 1:",
                        "        model_idx += 1",
                        "        response = _attempt(_MODELS[model_idx], prompt)",
                        "",
                    ]
            elif s.kind == "fallback":
                assert s.fallback_step is not None
                fb_name = s.fallback_step.name
                kw_str = ", ".join(f"{t.name}={_to_field_name(t.name)}" for t in step.takes)
                chain_lines += [
                    "    if response is None:",
                    f"        from . import {fb_name} as _{fb_name}_mod",
                    f"        fb_response = _{fb_name}_mod.{fb_name}({kw_str})",
                    (
                        f"        response = {result_class}("
                        f"fb_response if not isinstance(fb_response, str) "
                        f"else json.loads(fb_response))"
                    ),
                    "        fallback_used = True",
                    "",
                ]
            elif s.kind == "abort":
                msg = s.abort_message or ""
                full_msg = f"[clio] step {step.name}: {msg}"
                chain_lines += [
                    "    if response is None:",
                    f"        print({full_msg!r}, file=sys.stderr)",
                    f'        _log.emit("step_end", step={step.name!r}, mode="judgment",',
                    "                  duration_ms=int((time.monotonic() - _t0) * 1000),",
                    "                  cache_hit=False, model=_MODELS[model_idx],",
                    "                  fallback_used=False, success=False)",
                    "        raise SystemExit(1)",
                    "",
                ]

        if not terminal_abort:
            chain_lines += [
                "    if response is None:",
                f"        print('[clio] step {step.name}: ON_FAIL strategies exhausted', file=sys.stderr)",
                f'        _log.emit("step_end", step={step.name!r}, mode="judgment",',
                "                  duration_ms=int((time.monotonic() - _t0) * 1000),",
                "                  cache_hit=False, model=_MODELS[model_idx],",
                "                  fallback_used=False, success=False)",
                "        raise SystemExit(1)",
                "",
            ]

        if cache_active:
            gate_terms = ["model_idx == 0", "response is not None"]
            if has_fallback:
                gate_terms.append("not fallback_used")
            chain_lines += [
                f"    if {' and '.join(gate_terms)}:",
                f"        _cache.cache_store(cache_dir, '{step.name}', primary_key, _MODELS[0], _serialize(response))",
                "",
            ]

        fb_field = "fallback_used=fallback_used" if has_fallback else "fallback_used=False"
        chain_lines += [
            f'    _log.emit("step_end", step={step.name!r}, mode="judgment",',
            "              duration_ms=int((time.monotonic() - _t0) * 1000),",
            "              cache_hit=False, model=_MODELS[model_idx],",
            f"              {fb_field}, success=True, **_last_usage)",
            "    return response",
        ]

        body += chain_lines
        body.append("")
        return "\n".join(body)

    def _emit_flow(self, graph: FlowGraph) -> str:
        if graph.flow is None:
            return '"""No FLOW declared."""\n\ndef run(**kwargs):\n    return {}\n'

        chain_groups: list[list[str]] = []
        imported_steps: list[str] = []
        steps_by_name = {s.name: s for s in graph.steps}
        # RESCUE bookkeeping: the names of steps protected by a RESCUE block
        # (used to wrap their call site in try/except) and the blocks
        # themselves (used to emit the _rescue_<name> helper functions).
        rescue_target_names = {rb.step_name for rb in graph.flow.rescues}
        # The currently-being-built group; reset between top-level items.
        # Mutated (.append) by closures below; never reassigned inside them.
        _current: list[str] = []

        def _emit_call(call: CallIR, indent: str, scope_local: set[str]) -> None:
            step = next(s for s in graph.steps if s.name == call.step_name)
            if step.name not in imported_steps:
                imported_steps.append(step.name)
            kw_parts = []
            for name, value in call.kwargs:
                if isinstance(value, str) and value.startswith("@"):
                    ref = value[1:]
                    if ref in scope_local:
                        kw_parts.append(f"{name}={ref}")
                    else:
                        kw_parts.append(f"{name}=state[{ref!r}]")
                else:
                    kw_parts.append(f"{name}={value!r}")
            kwargs_str = ", ".join(kw_parts)
            out_name = step.gives.name if step.gives is not None else "_result"
            # Inside a FOR EACH body, results are not assigned to the global state
            # (no accumulation semantic in v0); the call is invoked for its side
            # effects on whatever it explicitly writes.
            if scope_local:
                call_line = f"{step.name}_mod.{step.name}({kwargs_str})"
            else:
                call_line = (
                    f"state[{out_name!r}] = {step.name}_mod.{step.name}({kwargs_str})"
                )
            # RESCUE: wrap the protected step's call site in try/except. Any
            # uncaught Exception triggers _rescue_<name>(state); FlowAborted
            # (raised by the rescue body's terminal abort) propagates verbatim.
            # The IR builder restricts RESCUE targets to the top-level FLOW
            # chain in v0.8, so this branch is only reached when scope_local
            # is empty — i.e. never inside a FOR EACH / IF / MATCH / WHILE
            # body. We still gate on `not scope_local` for defence in depth.
            if step.name in rescue_target_names and not scope_local:
                _current.append(f"{indent}try:")
                _current.append(f"{indent}    {call_line}")
                _current.append(f"{indent}except FlowAborted:")
                _current.append(f"{indent}    raise")
                _current.append(f"{indent}except Exception:")
                _current.append(f"{indent}    _rescue_{step.name}(state)")
                _current.append(f"{indent}    raise")
            else:
                _current.append(f"{indent}{call_line}")

        def _emit_item(
            item: CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR | ResumeIR,
            indent: str,
            scope_local: set[str],
        ) -> None:
            # `abort("msg")` is a synthetic CallIR injected by the IR builder
            # only inside RESCUE bodies. It compiles to `raise FlowAborted(msg)`
            # regardless of context (rescue body root, or nested IF/MATCH/
            # WHILE/FOR EACH inside the rescue body). Placed at the top of
            # _emit_item so every recursive call site picks it up.
            if isinstance(item, CallIR) and item.step_name == "abort":
                msg = next(
                    (v for k, v in item.kwargs if k == "message"), ""
                )
                _current.append(f"{indent}raise FlowAborted({msg!r})")
                return
            if isinstance(item, ForEachIR):
                if item.parallel:
                    _current.append(emit_parallel_for_each_python(item, steps_by_name, indent))
                    inner = item.body[0]
                    assert isinstance(inner, CallIR)  # IR builder enforces
                    if inner.step_name not in imported_steps:
                        imported_steps.append(inner.step_name)
                    return
                # FOR EACH item IN collection:
                #     <body>
                source = (
                    item.collection
                    if item.collection in scope_local
                    else f"state[{item.collection!r}]"
                )
                _current.append(f"{indent}for {item.loop_var} in {source}:")
                inner_scope = scope_local | {item.loop_var}
                inner_indent = indent + "    "
                if not item.body:
                    _current.append(f"{inner_indent}pass")
                for child in item.body:
                    _emit_item(child, inner_indent, inner_scope)
                return
            if isinstance(item, IfBlockIR):
                cond_expr = _python_condition_expr(item.condition, scope_local)
                _current.append(f"{indent}if {cond_expr}:")
                inner_indent = indent + "    "
                if not item.then_body:
                    _current.append(f"{inner_indent}pass")
                for sub in item.then_body:
                    _emit_item(sub, inner_indent, scope_local)
                if item.else_body:
                    _current.append(f"{indent}else:")
                    for sub in item.else_body:
                        _emit_item(sub, inner_indent, scope_local)
                return
            if isinstance(item, MatchBlockIR):
                base = (
                    item.state_field
                    if item.state_field in scope_local
                    else f"state[{item.state_field!r}]"
                )
                _current.append(f"{indent}match {base}.{item.sub_field}:")
                inner_indent = indent + "    "
                for arm in item.cases:
                    if arm.value is None:
                        _current.append(f"{inner_indent}case _:")
                    else:
                        _current.append(f"{inner_indent}case {arm.value!r}:")
                    body_indent = inner_indent + "    "
                    if not arm.body:
                        _current.append(f"{body_indent}pass")
                    for sub in arm.body:
                        _emit_item(sub, body_indent, scope_local)
                return
            if isinstance(item, WhileBlockIR):
                cond_expr = _python_condition_expr(item.condition, scope_local)
                _current.append(f"{indent}for _i in range({item.max_iters}):")
                inner_indent = indent + "    "
                _current.append(f"{inner_indent}if not ({cond_expr}):")
                _current.append(f"{inner_indent}    break")
                if not item.body:
                    _current.append(f"{inner_indent}pass")
                for sub in item.body:
                    _emit_item(sub, inner_indent, scope_local)
                return
            if isinstance(item, ResumeIR):
                # RESUME(<step>.<field>) — use the fallback value as the result.
                _current.append(
                    f"{indent}return state[{item.fallback_step!r}].{item.field_name}"
                )
                return
            if isinstance(item, CallIR):
                _emit_call(item, indent, scope_local)
                return
            raise ValueError(f"unknown flow item: {type(item).__name__}")

        for item in graph.flow.chain:
            _current = []
            _emit_item(item, "    ", set())
            chain_groups.append(list(_current))

        # Emit one _rescue_<step_name>(state) helper per RESCUE block. Each
        # helper body is rendered with the same _emit_item walker; the body's
        # terminal abort(...) is rewritten to `raise FlowAborted(msg)`, so
        # the helper never returns normally. The helpers live at module
        # level (after `run`) and are called from the wrapped try/except in
        # the main chain.
        rescue_helpers: list[str] = []
        for rb in graph.flow.rescues:
            _current = []
            for sub in rb.body:
                _emit_item(sub, "    ", set())
            rescue_helpers.append(
                f"def _rescue_{rb.step_name}(state: dict) -> None:\n"
                + "\n".join(_current)
                + "\n"
            )

        needs_concurrent = _has_parallel(graph.flow.chain)
        cf_import = (
            "import concurrent.futures\nimport contextvars\n\n"
            if needs_concurrent else ""
        )

        imports = "\n".join(f"from .steps import {n} as {n}_mod" for n in imported_steps)

        # Each group's lines were constructed at 4-space indent for top-level
        # (deeper for nested). Re-indent every line +8 so the body lives at
        # 12-space (inside try:8sp -> if start_at:12sp). Append a 12-space
        # _persist_state(N, state) call after each group.
        chain_body_parts: list[str] = []
        for idx, group in enumerate(chain_groups, start=1):
            chain_body_parts.append(f"        if start_at < {idx}:")
            for line in group:
                for line_part in line.split("\n"):
                    chain_body_parts.append("        " + line_part)
            chain_body_parts.append(f"            _persist_state({idx}, state)")
        chain_body = "\n".join(chain_body_parts)
        # FlowAborted is defined locally in the emitted flow module when at
        # least one RESCUE block is present. Keeping the class here (rather
        # than in clio_runtime) means flow.py is self-contained for rescue
        # semantics and existing snapshot fixtures stay byte-identical when
        # no RESCUE is declared.
        flow_aborted_block = (
            "class FlowAborted(Exception):\n"
            "    \"\"\"Raised by RESCUE bodies' terminal abort(...) call. Propagates out\n"
            "    of `run()` and is re-raised verbatim by any try/except wrapper around\n"
            "    a protected step's call site.\"\"\"\n"
            "    pass\n"
            "\n"
            "\n"
            if graph.flow.rescues else ""
        )
        rescue_helpers_block = (
            "\n\n" + "\n\n".join(rescue_helpers) if rescue_helpers else ""
        )
        # JSON-style double-quoted literal so callers can grep for
        # `set_flow("name")` consistently across emitters.
        flow_name_lit = json.dumps(graph.flow.name)
        total_steps = len(graph.flow.chain)
        return (
            f'"""FLOW {graph.flow.name}.\n\n'
            f'Auto-generated. Calls steps in chain order, threading state through a dict.\n'
            f'"""\n'
            f'\n'
            f'import json\n'
            f'import os\n'
            f'import sys\n'
            f'import time\n'
            f'{cf_import}'
            f'{imports}\n'
            f'\n'
            f'from .clio_runtime import logging as _log\n'
            f'\n'
            f'\n'
            f'{flow_aborted_block}'
            f'TOTAL_STEPS = {total_steps}\n'
            f'\n'
            f'\n'
            f'def _persist_state(step_idx: int, state: dict) -> None:\n'
            f'    """Atomic write of {{version, flow, step_index, state}} to state.json."""\n'
            f'    path = os.environ.get("CLIO_STATE_FILE", "state.json")\n'
            f'    payload = {{"version": 1, "flow": {flow_name_lit}, "step_index": step_idx, "state": state}}\n'
            f'    tmp = path + ".tmp"\n'
            f'    with open(tmp, "w") as f:\n'
            f'        json.dump(payload, f, default=str)\n'
            f'    os.replace(tmp, path)\n'
            f'\n'
            f'\n'
            f'def run(*, start_at: int = 0, **initial: object) -> dict:\n'
            f'    if start_at > 0:\n'
            f'        path = os.environ.get("CLIO_STATE_FILE", "state.json")\n'
            f'        if not os.path.exists(path):\n'
            f'            print('
            f'f\'[clio] resume requested (start_at={{start_at}}) but {{path}} missing\', '
            f'file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        with open(path) as f:\n'
            f'            payload = json.load(f)\n'
            f'        if payload.get("flow") != {flow_name_lit}:\n'
            f'            print('
            f'f\'[clio] state.json flow mismatch: expected {flow_name_lit}, '
            f'got {{payload.get("flow")!r}}\', file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        if payload.get("step_index", 0) < start_at:\n'
            f'            print('
            f'f\'[clio] state.json only reached step {{payload.get("step_index", 0)}}, '
            f'cannot resume from {{start_at}}\', file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        if start_at >= TOTAL_STEPS:\n'
            f'            print(f\'[clio] start_at={{start_at}} >= total steps={{TOTAL_STEPS}}\', file=sys.stderr)\n'
            f'            raise SystemExit(2)\n'
            f'        state: dict = payload["state"]\n'
            f'    else:\n'
            f'        state: dict = dict(initial)\n'
            f'    _log.set_flow({flow_name_lit})\n'
            f'    _log.emit("flow_start", resumed_from=start_at if start_at > 0 else 0)\n'
            f'    _success = False\n'
            f'    _t0 = time.monotonic()\n'
            f'    try:\n'
            f'{chain_body}\n'
            f'        _success = True\n'
            f'        return state\n'
            f'    finally:\n'
            f'        _log.emit("flow_end", '
            f'duration_ms=int((time.monotonic() - _t0) * 1000), '
            f'success=_success)\n'
            f'        _log.set_flow(None)\n'
            f'{rescue_helpers_block}'
        )

    def _emit_main(self, pkg_name: str) -> str:
        return (
            f'"""CLI entry point: `python -m {pkg_name}`."""\n'
            f'import argparse\n'
            f'import json\n'
            f'import sys\n'
            f'\n'
            f'from .flow import run\n'
            f'\n'
            f'\n'
            f'def main(argv: list[str] | None = None) -> int:\n'
            f'    parser = argparse.ArgumentParser(prog="{pkg_name}")\n'
            f'    parser.add_argument("--kwargs", default="{{}}", help="JSON dict of initial flow kwargs")\n'
            f'    parser.add_argument(\n'
            f'        "--from-step",\n'
            f'        type=int,\n'
            f'        default=0,\n'
            f'        metavar="N",\n'
            f'        help="Resume from step N+1 (1-based; reads state.json or $CLIO_STATE_FILE).",\n'
            f'    )\n'
            f'    args = parser.parse_args(argv)\n'
            f'    if args.from_step < 0:\n'
            f'        print(f"[clio] --from-step must be >= 0, got {{args.from_step}}", file=sys.stderr)\n'
            f'        return 2\n'
            f'    initial = json.loads(args.kwargs)\n'
            f'    result = run(start_at=args.from_step, **initial)\n'
            f'    json.dump(result, sys.stdout, indent=2, default=str)\n'
            f'    sys.stdout.write("\\n")\n'
            f'    return 0\n'
            f'\n'
            f'\n'
            f'if __name__ == "__main__":\n'
            f'    raise SystemExit(main())\n'
        )

    @staticmethod
    def _package_name(graph: FlowGraph) -> str:
        if graph.flow is None:
            return "clio_flow"
        return graph.flow.name

    @staticmethod
    def _pyproject(
        pkg_name: str,
        *,
        needs_requests: bool = False,
        needs_openai: bool = False,
        needs_anthropic: bool = False,
        needs_pydantic: bool = False,
    ) -> str:
        deps: list[str] = []
        if needs_pydantic:
            deps.append('    "pydantic>=2",')
        if needs_anthropic:
            deps.insert(0, '    "anthropic>=0.40",')
        if needs_requests:
            deps.append('    "requests>=2.31",')
        if needs_openai:
            deps.append('    "openai>=1.0",')
        deps_block = "\n".join(deps)
        return (
            "[build-system]\n"
            'requires = ["setuptools>=70"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            f'name = "{pkg_name}"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.12"\n'
            "dependencies = [\n"
            f"{deps_block}\n"
            "]\n"
            "\n"
            "[project.scripts]\n"
            f'{pkg_name} = "{pkg_name}.__main__:main"\n'
            "\n"
            "[tool.setuptools.packages.find]\n"
            f'include = ["{pkg_name}*"]\n'
        )

    @staticmethod
    def _readme(pkg_name: str, graph: FlowGraph) -> str:
        flow_name = graph.flow.name if graph.flow else "(no flow)"
        return (
            f"# {pkg_name}\n\n"
            f"Generated by CLIO from a `.clio` source. Implements FLOW `{flow_name}`.\n\n"
            "## Install\n\n"
            "```bash\n"
            "pip install -e .\n"
            "```\n\n"
            "## Run\n\n"
            "```bash\n"
            f"python -m {pkg_name}\n"
            "```\n\n"
            "Or programmatically:\n\n"
            "```python\n"
            f"from {pkg_name}.flow import run\n"
            "result = run()\n"
            "```\n"
        )
