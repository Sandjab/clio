"""Emitter for `target: claude-cli`.

Module-level helpers and template strings live in `_claude_cli_helpers.py`;
this file holds only the ClaudeCLIEmitter class.
"""

import json
import shlex
from pathlib import Path

from clio.emitters._claude_cli_helpers import (
    _CLAUDE_MD,
    _EXACT_STEP_TEMPLATE,
    _JUDGMENT_PROMPT_TEMPLATE,
    _REST_STEP_TEMPLATE,
    _SHELL_STEP_TEMPLATE,
    _STEP_NO_FIELDS,
    _chain_has_for_each,
    _field_doc,
    _flatten_calls,
    _inline_schema,
    _is_primitive_type,
    _render_prompt,
    _render_type,
    _resolve_iter_inner_type,
)
from clio.emitters.base import BaseEmitter
from clio.ir.contracts import type_to_json_schema
from clio.ir.graph import (
    CallIR,
    ContractIR,
    FieldIR,
    FlowGraph,
    ForEachIR,
    RestImplIR,
    ShellImplIR,
    StepIR,
)


class ClaudeCLIEmitter(BaseEmitter):
    def _reject_parallel(self, graph: FlowGraph) -> None:
        def _walk(chain) -> None:
            for elem in chain:
                if isinstance(elem, ForEachIR):
                    if elem.parallel:
                        raise ValueError(
                            "claude-cli target does not support FOR EACH "
                            "PARALLEL; use --target python or --target mcp-server "
                            f"(line {elem.line})"
                        )
                    _walk(elem.body)

        if graph.flow is not None:
            _walk(graph.flow.chain)

    def _reject_rescue(self, graph: FlowGraph) -> None:
        """RESCUE handlers are not supported by the claude-cli target.
        Pointer to --target python / mcp-server."""
        if graph.flow and graph.flow.rescues:
            rb = graph.flow.rescues[0]
            raise ValueError(
                f"RESCUE handlers are not supported by the claude-cli target. "
                f"Use --target python or --target mcp-server. "
                f"Rescue at line {rb.line}."
            )

    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        self._reject_parallel(graph)
        self._reject_rescue(graph)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "CLAUDE.md").write_text(_CLAUDE_MD)

        claude_dir = output_dir / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "hooks.json").write_text("{}")

        if graph.contracts:
            contracts_dir = output_dir / "contracts"
            contracts_dir.mkdir(exist_ok=True)
            for c in graph.contracts:
                (contracts_dir / f"{c.name}.schema.json").write_text(
                    json.dumps(c.json_schema, indent=2) + "\n"
                )

        steps_dir = output_dir / "steps"
        steps_dir.mkdir(exist_ok=True)
        for idx, step in enumerate(graph.steps, start=1):
            self._emit_step(steps_dir, idx, step)

        if graph.flow is not None:
            self._emit_run_sh(graph, output_dir)
            if any(
                self._step_for_call(graph, c).mode == "judgment"
                for c in _flatten_calls(graph.flow.chain)
            ):
                self._copy_runtime(output_dir)

    def _copy_runtime(self, output_dir: Path) -> None:
        """Copy `clio.runtime.*` into the output as a top-level `clio_runtime/` package."""
        from clio import runtime as src_pkg
        src_dir = Path(src_pkg.__file__).parent
        dest = output_dir / "clio_runtime"
        dest.mkdir(exist_ok=True)
        for name in ("__init__.py", "validate.py", "substitute.py", "cache.py"):
            src_file = src_dir / name
            if src_file.exists():
                (dest / name).write_text(src_file.read_text())

    @staticmethod
    def _step_for_call(graph: FlowGraph, call) -> StepIR:
        for s in graph.steps:
            if s.name == call.step_name:
                return s
        raise KeyError(call.step_name)

    def _emit_run_sh(self, graph: FlowGraph, output_dir: Path) -> None:
        lines: list[str] = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            'cd "$(dirname "$0")"',
            "",
            "# Resolve a Python 3.12+ interpreter (override with PYTHON env var if needed).",
            'PYTHON="${PYTHON:-}"',
            'if [ -z "$PYTHON" ]; then',
            "    for candidate in python3.12 python3.13 python3.14 python3 python; do",
            '        if command -v "$candidate" >/dev/null 2>&1 \\',
            "           && \"$candidate\" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' >/dev/null 2>&1; then",
            '            PYTHON="$candidate"',
            "            break",
            "        fi",
            "    done",
            "fi",
            'if [ -z "$PYTHON" ]; then',
            '    echo "[clio] error: Python 3.12+ not found on PATH (set PYTHON=/path/to/python)" >&2',
            "    exit 1",
            "fi",
            "",
            "# Helper: run one judgment attempt against $1=model with $2=prompt and validate against $3=schema_path.",
            "# Prints the cleaned response on success, nothing on failure. Exit 0 on success, 1 on failure.",
            "_clio_run_attempt() {",
            "    local model=\"$1\" prompt=\"$2\" schema_path=\"$3\" raw clean",
            "    raw=\"$(printf %s \"$prompt\" | claude -p --model \"$model\" --output-format text 2>/dev/null || true)\"",
            "    [ -n \"$raw\" ] || return 1",
            "    clean=\"$(printf %s \"$raw\" | awk '!/^```/')\"",
            "    printf %s \"$clean\" | \"$PYTHON\" -m clio_runtime.validate \"$schema_path\" - >/dev/null 2>&1 || return 1",
            "    printf %s \"$clean\"",
            "    return 0",
            "}",
            "",
            "echo '{}' > state.json",
            "",
        ]
        models_list = (
            graph.resources.models
            if graph.resources is not None and graph.resources.models
            else ("haiku",)
        )
        contracts_by_name = {c.name: c for c in graph.contracts}

        # Track types of state fields produced by upstream steps so we can
        # decide jq -r vs jq -c when iterating with FOR EACH.
        available_types: dict = {}
        # Counter for unique mapfile array variable names across nested loops.
        iter_counter = [0]

        def _emit_chain(items, indent: str, local_scope: dict) -> None:
            for item in items:
                if isinstance(item, ForEachIR):
                    _emit_for_each(item, indent, local_scope)
                else:  # CallIR
                    step = self._step_for_call(graph, item)
                    idx = self._step_index_in_emit(graph, step)
                    if step.mode == "exact":
                        args = self._render_kwargs_as_cli(item, local_scope)
                        script_name = f"steps/{idx:02d}_{step.name}.py"
                        lines.append(f"{indent}# Step {idx}: {step.name} (exact)")
                        lines.append(f'{indent}"$PYTHON" {script_name} {args}')
                    else:
                        if local_scope:
                            raise NotImplementedError(
                                f"judgment step {step.name!r} inside a FOR EACH body is "
                                "not yet supported by the claude-cli emitter in v0.2; "
                                "use --target python or move the judgment step out of the loop"
                            )
                        lines.extend(self._render_judgment_step(
                            graph, idx, step, item,
                            models_list=models_list,
                            contracts_by_name=contracts_by_name,
                        ))
                    if step.gives is not None and not local_scope:
                        # Only top-level state is tracked; inside a loop the body
                        # may produce values but they are not accumulated in v0.
                        available_types[step.gives.name] = step.gives.type
                    lines.append("")

        def _emit_for_each(item: ForEachIR, indent: str, local_scope: dict) -> None:
            if item.collection in local_scope:
                inner_type = _resolve_iter_inner_type(local_scope[item.collection])
                source_expr = (
                    f'"${item.collection}"'
                    if _is_primitive_type(local_scope[item.collection])
                    else f'"${item.collection}"'
                )
                # Iterating a loop-local list is rare but allowed; same shape.
                primitive = _is_primitive_type(inner_type) if inner_type is not None else True
                jq_flag = "-r" if primitive else "-c"
                # In this case the local var holds a JSON list. Use jq on stdin.
                array_var = f"_CLIO_ITER_{iter_counter[0]}"
                iter_counter[0] += 1
                lines.append(
                    f"{indent}# FOR EACH {item.loop_var} IN {item.collection} (loop-local)"
                )
                lines.append(
                    f"{indent}mapfile -t {array_var} < <(printf %s \"${item.collection}\" | jq {jq_flag} '.[]')"
                )
            else:
                if item.collection not in available_types:
                    raise ValueError(
                        f"FOR EACH iterates over {item.collection!r} which is not "
                        f"a known state field at emit time"
                    )
                coll_type = available_types[item.collection]
                inner_type = _resolve_iter_inner_type(coll_type)
                primitive = _is_primitive_type(inner_type) if inner_type is not None else True
                jq_flag = "-r" if primitive else "-c"
                array_var = f"_CLIO_ITER_{iter_counter[0]}"
                iter_counter[0] += 1
                lines.append(
                    f"{indent}# FOR EACH {item.loop_var} IN {item.collection}"
                )
                lines.append(
                    f"{indent}mapfile -t {array_var} < <(jq {jq_flag} '.{item.collection}[]' state.json)"
                )

            lines.append(
                f'{indent}for {item.loop_var} in "${{{array_var}[@]}}"; do'
            )
            inner_scope = dict(local_scope)
            inner_scope[item.loop_var] = inner_type  # may be None
            _emit_chain(item.body, indent + "    ", inner_scope)
            lines.append(f"{indent}done")

        _emit_chain(graph.flow.chain, "", {})

        lines.append('echo "[clio] flow ' + graph.flow.name + ' completed."')
        run_path = output_dir / "run.sh"
        run_path.write_text("\n".join(lines) + "\n")
        run_path.chmod(0o755)

    @staticmethod
    def _step_index_in_emit(graph: FlowGraph, target: "StepIR") -> int:
        """Index (1-based) of `target` in graph.steps in emission order. The
        emitter writes step files as `steps/NN_<name>.py` indexed by this order."""
        for i, s in enumerate(graph.steps, start=1):
            if s.name == target.name:
                return i
        raise KeyError(target.name)

    def _render_judgment_step(
        self,
        graph: FlowGraph,
        idx: int,
        step: StepIR,
        call,
        models_list: tuple[str, ...],
        contracts_by_name: dict[str, ContractIR],
    ) -> list[str]:
        prompt_path = f"steps/{idx:02d}_{step.name}.prompt"
        schema_path = f"steps/{idx:02d}_{step.name}.schema.json"
        out_name = step.gives.name if step.gives else "result"
        inlined = _inline_schema(step.gives.type, contracts_by_name) if step.gives else {}
        inlined_json = json.dumps(inlined, separators=(",", ":"))

        cache_active = step.cache is not None and step.cache.mode != "off"
        ttl_arg = "" if step.cache is None or step.cache.mode == "on" else str(step.cache.ttl_seconds)

        models_array = "(" + " ".join(shlex.quote(m) for m in models_list) + ")"
        primary_model = models_list[0]
        first_model_lit = shlex.quote(primary_model)

        strategies = step.on_fail.strategies if step.on_fail is not None else ()
        has_fallback = any(s.kind == "fallback" for s in strategies)

        out: list[str] = [
            f"# Step {idx}: {step.name} (judgment)",
            f"INLINED_SCHEMA_{idx:02d}={shlex.quote(inlined_json)}",
            f'PROMPT_{idx:02d}="$("$PYTHON" -m clio_runtime.substitute {prompt_path} state.json)"',
            f'PROMPT_{idx:02d}="${{PROMPT_{idx:02d}//\\$\\{{schema\\}}/$INLINED_SCHEMA_{idx:02d}}}"',
            f"MODELS_{idx:02d}={models_array}",
            f"MODEL_IDX_{idx:02d}=0",
            f'RESPONSE_{idx:02d}=""',
        ]
        if has_fallback:
            out.append(f"FALLBACK_USED_{idx:02d}=0")

        # Cache lookup against the primary model
        if cache_active:
            ttl_str = ttl_arg if ttl_arg else '""'
            out += [
                f'CACHE_DIR_{idx:02d}="${{CLIO_CACHE_DIR:-.cache}}"',
                f'KEY_{idx:02d}="$("$PYTHON" -m clio_runtime.cache key {step.name} {first_model_lit} '
                f'"$PROMPT_{idx:02d}" "$INLINED_SCHEMA_{idx:02d}")"',
                f'RESPONSE_{idx:02d}="$("$PYTHON" -m clio_runtime.cache lookup '
                f'"$CACHE_DIR_{idx:02d}" {step.name} "$KEY_{idx:02d}" {ttl_str} 2>/dev/null || true)"',
            ]

        # Open the "if no cache hit" guard (only if cache is active).
        if cache_active:
            out.append(f'if [ -z "$RESPONSE_{idx:02d}" ]; then')
            ind = "    "
        else:
            ind = ""

        # Initial attempt: counted as part of strategy 1 if it's retry, else standalone.
        # We always do at least one attempt with the primary model.
        out += [
            f'{ind}RESPONSE_{idx:02d}="$(_clio_run_attempt "${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" '
            f'"$PROMPT_{idx:02d}" {schema_path} || true)"',
        ]

        for s in strategies:
            if s.kind == "retry":
                # Up to N additional attempts on the current model (so total = 1 + N for this clause).
                n = s.max_retries
                out += [
                    f'{ind}if [ -z "$RESPONSE_{idx:02d}" ]; then',
                    f'{ind}    for _ in $(seq 1 {n}); do',
                    f'{ind}        RESPONSE_{idx:02d}="$(_clio_run_attempt '
                    f'"${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" '
                    f'"$PROMPT_{idx:02d}" {schema_path} || true)"',
                    f'{ind}        [ -n "$RESPONSE_{idx:02d}" ] && break',
                    f'{ind}    done',
                    f'{ind}fi',
                ]
            elif s.kind == "escalate":
                out += [
                    f'{ind}if [ -z "$RESPONSE_{idx:02d}" ] '
                    f'&& [ $MODEL_IDX_{idx:02d} -lt $((${{#MODELS_{idx:02d}[@]}} - 1)) ]; then',
                    f'{ind}    MODEL_IDX_{idx:02d}=$((MODEL_IDX_{idx:02d} + 1))',
                ]
                if cache_active:
                    out += [
                        f'{ind}    KEY_{idx:02d}_ESC="$("$PYTHON" -m clio_runtime.cache key {step.name} '
                        f'"${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" '
                        f'"$PROMPT_{idx:02d}" "$INLINED_SCHEMA_{idx:02d}")"',
                        f'{ind}    RESPONSE_{idx:02d}="$("$PYTHON" -m clio_runtime.cache lookup '
                        f'"$CACHE_DIR_{idx:02d}" {step.name} "$KEY_{idx:02d}_ESC" {ttl_str} 2>/dev/null || true)"',
                        f'{ind}    if [ -z "$RESPONSE_{idx:02d}" ]; then',
                        f'{ind}        RESPONSE_{idx:02d}="$(_clio_run_attempt '
                        f'"${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" '
                        f'"$PROMPT_{idx:02d}" {schema_path} || true)"',
                        f'{ind}    fi',
                        f'{ind}    if [ -n "$RESPONSE_{idx:02d}" ]; then',
                        f'{ind}        "$PYTHON" -m clio_runtime.cache store "$CACHE_DIR_{idx:02d}" '
                        f'{step.name} "$KEY_{idx:02d}_ESC" '
                        f'"${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" "$RESPONSE_{idx:02d}"',
                        f'{ind}    fi',
                    ]
                else:
                    out += [
                        f'{ind}    RESPONSE_{idx:02d}="$(_clio_run_attempt '
                        f'"${{MODELS_{idx:02d}[$MODEL_IDX_{idx:02d}]}}" '
                        f'"$PROMPT_{idx:02d}" {schema_path} || true)"',
                    ]
                out += [
                    f'{ind}fi',
                ]
            elif s.kind == "abort":
                full_msg = f"[clio] step {step.name}: {s.abort_message or ''}"
                out += [
                    f'{ind}if [ -z "$RESPONSE_{idx:02d}" ]; then',
                    f'{ind}    echo {shlex.quote(full_msg)} >&2',
                    f'{ind}    exit 1',
                    f'{ind}fi',
                ]
            elif s.kind == "fallback":
                # Run the fallback step. The fallback step's own emitted Python
                # script reads its TAKES from state.json and writes its GIVES
                # back, exactly the same way as a regular exact step would.
                fb_step = s.fallback_step
                # We need to know the index of the fallback step in graph.steps
                # to construct its filename. The emitter is stateless re: this,
                # so we look it up via the helper.
                fb_idx = self._step_index_in_emit(graph, fb_step)
                fb_script = f"steps/{fb_idx:02d}_{fb_step.name}.py"
                # Reconstruct the kwargs from the call's state references —
                # we use the same kwargs the main step uses, since fallback
                # has identical TAKES.
                args = self._render_kwargs_as_cli(call)
                out += [
                    f'{ind}if [ -z "$RESPONSE_{idx:02d}" ]; then',
                    f'{ind}    "$PYTHON" {fb_script} {args}',
                    f"{ind}    RESPONSE_{idx:02d}=\"$(jq -c .{out_name} state.json)\"",
                    f"{ind}    FALLBACK_USED_{idx:02d}=1",
                    f'{ind}fi',
                ]

        # Implicit abort if no terminal `abort` clause and the response is still empty.
        terminal = strategies and strategies[-1].kind == "abort"
        if not terminal:
            out += [
                f'{ind}if [ -z "$RESPONSE_{idx:02d}" ]; then',
                f'{ind}    echo "[clio] step {step.name}: ON_FAIL strategies exhausted" >&2',
                f'{ind}    exit 1',
                f'{ind}fi',
            ]

        # Cache store on success (only when MODEL_IDX is still on the primary model;
        # other models cache under their own key only if they were hit by lookup later).
        # Per spec §6: a successful fallback must NOT be cached under the main key.
        if cache_active:
            if has_fallback:
                guard = (
                    f'{ind}if [ $MODEL_IDX_{idx:02d} -eq 0 ] && [ -n "$RESPONSE_{idx:02d}" ] '
                    f'&& [ "$FALLBACK_USED_{idx:02d}" = "0" ]; then'
                )
            else:
                guard = (
                    f'{ind}if [ $MODEL_IDX_{idx:02d} -eq 0 ] && [ -n "$RESPONSE_{idx:02d}" ]; then'
                )
            out += [
                guard,
                f'{ind}    "$PYTHON" -m clio_runtime.cache store "$CACHE_DIR_{idx:02d}" '
                f'{step.name} "$KEY_{idx:02d}" {first_model_lit} "$RESPONSE_{idx:02d}"',
                f'{ind}fi',
                "fi",  # closes the `if [ -z "$RESPONSE_NN" ]; then` cache-miss guard
            ]

        # Apply
        out += [
            f"jq --argjson r \"$RESPONSE_{idx:02d}\" '.{out_name} = $r' state.json > state.json.tmp "
            f"&& mv state.json.tmp state.json",
        ]
        return out

    @staticmethod
    def _render_kwargs_as_cli(call, local_scope: dict | None = None) -> str:
        local_scope = local_scope or {}
        parts: list[str] = []
        for name, value in call.kwargs:
            if isinstance(value, str) and value.startswith("@"):
                ref = value[1:]
                if ref in local_scope:
                    # Loop variable bound by an enclosing FOR EACH; resolve via
                    # the bash variable rather than re-querying state.json.
                    parts.append(f'--{name}="${ref}"')
                else:
                    parts.append(f'--{name}="$(jq -r .{ref} state.json)"')
            else:
                parts.append(f'--{name}={shlex.quote(str(value))}')
        return " ".join(parts)

    @staticmethod
    def _emit_step(steps_dir: Path, idx: int, step: StepIR) -> None:
        prefix = f"{idx:02d}_{step.name}"
        if step.mode == "judgment":
            (steps_dir / f"{prefix}.prompt").write_text(_render_prompt(step))
            if step.gives is not None:
                schema = type_to_json_schema(step.gives.type)
                (steps_dir / f"{prefix}.schema.json").write_text(
                    json.dumps(schema, indent=2) + "\n"
                )
            return

        # exact step
        if isinstance(step.impl, RestImplIR):
            io_doc = "\n".join(_field_doc(f) for f in step.takes)
            if step.gives:
                io_doc += f"\nGIVES: {step.gives.name}: {_render_type(step.gives.type)}"
            argparse_block = "\n".join(
                f'    parser.add_argument("--{f.name}", required=True)'
                for f in step.takes
            ) or "    pass"

            templating_active = "${" in step.impl.url
            if templating_active:
                unused_takes = ""
                url_lines = [f"    url = {step.impl.url!r}"]
                for f in step.takes:
                    url_lines.append(
                        f"    url = url.replace('${{{f.name}}}', str(args.{f.name}))"
                    )
                url_block = "\n".join(url_lines) + "\n"
                url_arg = "url"
            else:
                unused_takes = (
                    "".join(f"    _ = args.{f.name}\n" for f in step.takes)
                    if step.takes else ""
                )
                url_block = ""
                url_arg = repr(step.impl.url)

            out_name = step.gives.name if step.gives else "result"
            body = _REST_STEP_TEMPLATE.format(
                name=step.name,
                io_doc=io_doc,
                argparse_block=argparse_block,
                unused_takes=unused_takes,
                url_block=url_block,
                url_arg=url_arg,
                method=step.impl.method,
                timeout_repr=repr(step.impl.timeout_seconds),
                response_path_repr=repr(step.impl.response_path),
                out_name=out_name,
            )
        elif isinstance(step.impl, ShellImplIR):
            io_doc = "\n".join(_field_doc(f) for f in step.takes)
            if step.gives:
                io_doc += f"\nGIVES: {step.gives.name}: {_render_type(step.gives.type)}"
            argparse_block = "\n".join(
                f'    parser.add_argument("--{f.name}", required=True)'
                for f in step.takes
            ) or "    pass"
            argv_repr = "[" + ", ".join(repr(t) for t in step.impl.argv) + "]"
            sub_lines = [
                f"    argv = [_t.replace('${{{f.name}}}', str(args.{f.name})) for _t in argv]"
                for f in step.takes
            ]
            sub_block = ("\n".join(sub_lines) + "\n") if sub_lines else ""
            out_name = step.gives.name if step.gives else "result"
            body = _SHELL_STEP_TEMPLATE.format(
                name=step.name,
                io_doc=io_doc,
                argparse_block=argparse_block,
                argv_repr=argv_repr,
                sub_block=sub_block,
                timeout_repr=repr(step.impl.timeout_seconds),
                out_name=out_name,
            )
        elif not step.takes and step.gives is None:
            body = _STEP_NO_FIELDS.format(name=step.name, mode=step.mode)
        else:
            io_doc = "\n".join(_field_doc(f) for f in step.takes)
            if step.gives:
                io_doc += f"\nGIVES: {step.gives.name}: {_render_type(step.gives.type)}"
            argparse_block = "\n".join(
                f'    parser.add_argument("--{f.name}", required=True)'
                for f in step.takes
            )
            out_name = step.gives.name if step.gives else "result"
            assigned_from = step.takes[0].name if step.takes else '""'
            assignment = f"{out_name} = args.{assigned_from}  # echo: TODO replace with real logic"
            body = _EXACT_STEP_TEMPLATE.format(
                name=step.name,
                mode=step.mode,
                io_doc=io_doc,
                argparse_block=argparse_block,
                body=assignment,
                out_name=out_name,
            )
        (steps_dir / f"{prefix}.py").write_text(body)


