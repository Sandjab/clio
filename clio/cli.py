from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.emitters.python import PythonEmitter
from clio.graph_render import to_dot, to_html, to_mermaid
from clio.ir.builder import IRBuildError, build_ir
from clio.ir.resolver import CompileError, resolve_imports
from clio.parser.parser import ParseError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    compile_p = sub.add_parser("compile")
    compile_p.add_argument("source")
    compile_p.add_argument(
        "--target", required=True,
        choices=["claude-cli", "python", "mcp-server", "langgraph", "claude-skill", "go"],
    )
    compile_p.add_argument("--output", required=True)
    compile_p.add_argument(
        "--flow", dest="flow", default=None,
        help="select a FLOW by name when the source declares more than one",
    )

    check_p = sub.add_parser("check")
    check_p.add_argument("source")

    graph_p = sub.add_parser("graph")
    graph_p.add_argument("source")
    graph_p.add_argument("--format", choices=["mermaid", "dot", "html"], default="mermaid")
    graph_p.add_argument("--output", default=None)
    graph_p.add_argument(
        "--flow", dest="flow", default=None,
        help="select a FLOW by name when the source declares more than one",
    )

    gen_p = sub.add_parser("gen")
    gen_p.add_argument("description", nargs="?")
    gen_p.add_argument("--from-file", dest="from_file")
    gen_p.add_argument("--output")
    gen_p.add_argument("--model", default="claude-sonnet-4-6")

    doctor_p = sub.add_parser("doctor")
    doctor_p.add_argument("source", nargs="?", default=None)
    doctor_p.add_argument(
        "--flow", dest="flow", default=None,
        help="select a FLOW by name when the source declares more than one",
    )
    doctor_p.add_argument(
        "--migrate-v018", dest="migrate_v018", action="store_true", default=False,
        help="propose (or apply with --write) the v0.17 → v0.18 EXPOSE migration",
    )
    doctor_p.add_argument(
        "--write", dest="write", action="store_true", default=False,
        help="write migration changes back to the source file (use with --migrate-v018)",
    )

    status_p = sub.add_parser("status")
    status_p.add_argument("--state-file", dest="state_file", default=None)
    status_p.add_argument("--log-file", dest="log_file", default=None)
    status_p.add_argument("--limit", type=int, default=10)

    import_p = sub.add_parser("import")
    import_p.add_argument("skill_dir")
    import_p.add_argument("--output")
    import_p.add_argument("--model", default="claude-sonnet-4-6")
    import_p.add_argument(
        "--mode", choices=["auto", "strict", "infer"], default="auto",
        help=(
            "auto: use sidecar when present; "
            "strict: require sidecar + matching hashes; "
            "infer: force LLM-assisted import"
        ),
    )

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        return _cmd_compile(args.source, args.target, args.output, args.flow)
    if args.cmd == "check":
        return _cmd_check(args.source)
    if args.cmd == "graph":
        return _cmd_graph(args.source, args.format, args.output, args.flow)
    if args.cmd == "gen":
        return _cmd_gen(
            description=args.description,
            from_file=args.from_file,
            output=args.output,
            model=args.model,
        )
    if args.cmd == "doctor":
        return _cmd_doctor(args.source, args.flow,
                           migrate_v018=args.migrate_v018, write=args.write)
    if args.cmd == "status":
        return _cmd_status(args.state_file, args.log_file, args.limit)
    if args.cmd == "import":
        return _cmd_import(
            skill_dir=args.skill_dir,
            output=args.output,
            model=args.model,
            mode=args.mode,
        )
    return 2


def _cmd_compile(source: str, target: str, output: str, flow: str | None = None) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2

    try:
        parsed = resolve_imports(src_path)
        had_imports = any(p.imports for p in parsed.values())
        if target == "claude-cli" and had_imports:
            print(
                "error: target 'claude-cli' does not support cross-file imports "
                "(deferred to a future release)",
                file=sys.stderr,
            )
            return 1
        graph = build_ir(parsed, entry=src_path.resolve(), flow_name=flow)
    except (ParseError, IRBuildError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_path = Path(output)
    src_resolved = src_path.resolve()
    if target == "claude-cli":
        ClaudeCLIEmitter().emit(graph, out_path, source_path=src_resolved)
    elif target == "python":
        PythonEmitter().emit(graph, out_path, source_path=src_resolved)
    elif target == "mcp-server":
        from clio.emitters.mcp_server import MCPServerEmitter
        MCPServerEmitter().emit(graph, out_path, source_path=src_resolved)
    elif target == "langgraph":
        from clio.emitters.langgraph import LangGraphEmitter
        LangGraphEmitter().emit(graph, out_path, source_path=src_resolved)
    elif target == "claude-skill":
        from clio.emitters.claude_skill import ClaudeSkillEmitter
        ClaudeSkillEmitter().emit(graph, out_path, source_path=src_resolved, sources=tuple(parsed))
    elif target == "go":
        from clio.emitters.go import GoEmitter
        GoEmitter().emit(graph, out_path, source_path=src_resolved)
    else:
        return 2
    return 0


def _cmd_check(source: str) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2
    try:
        parsed = resolve_imports(src_path)
        build_ir(parsed, entry=src_path.resolve())
    except (ParseError, IRBuildError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("ok")
    return 0


def _cmd_graph(source: str, fmt: str, output: str | None, flow: str | None = None) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2
    try:
        parsed = resolve_imports(src_path)
        graph = build_ir(parsed, entry=src_path.resolve(), flow_name=flow)
    except (ParseError, IRBuildError, CompileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if fmt == "mermaid":
        rendered = to_mermaid(graph)
    elif fmt == "dot":
        rendered = to_dot(graph)
    else:
        rendered = to_html(graph)
    if output is None:
        sys.stdout.write(rendered)
    else:
        Path(output).write_text(rendered)
    return 0


def _cmd_gen(
    *,
    description: str | None,
    from_file: str | None,
    output: str | None,
    model: str,
) -> int:
    if description is not None and from_file is not None:
        print(
            "clio gen: pass either DESCRIPTION inline or --from-file, not both",
            file=sys.stderr, flush=True,
        )
        return 2

    if description is None and from_file is not None:
        description = Path(from_file).read_text()
    elif description is None:
        description = sys.stdin.read()

    if not description.strip():
        print("clio gen: empty description", file=sys.stderr, flush=True)
        return 2

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "clio gen: ANTHROPIC_API_KEY env var is not set",
            file=sys.stderr,
            flush=True,
        )
        return 1

    from clio import nl_to_clio
    try:
        source = nl_to_clio.generate(description, model=model)
    except nl_to_clio.GenerationError as e:
        print(f"clio gen: {e.last_error}", file=sys.stderr, flush=True)
        for line in e.last_attempt.splitlines():
            print(f"# {line}", file=sys.stderr, flush=True)
        return 1

    if output is None:
        sys.stdout.write(source)
    else:
        Path(output).write_text(source)
    return 0


def _cmd_doctor(
    source: str | None,
    flow: str | None = None,
    *,
    migrate_v018: bool = False,
    write: bool = False,
) -> int:
    if migrate_v018:
        if not source:
            print("clio doctor --migrate-v018: a source file is required", file=sys.stderr)
            return 2
        from clio.diagnostics import migrate_v018 as do_migrate
        src_path = Path(source)
        new_text, changes = do_migrate(src_path)
        if not changes:
            print(f"{source}: no v0.18 migration needed")
            return 0
        print(f"file: {source}")
        print("Proposed changes (using v0.17 sibling-call heuristic):")
        for line_num, _prefix in changes:
            print(f"  line {line_num}: + EXPOSE before existing declaration")
        if write:
            src_path.write_text(new_text)
            print(f"\nWrote {len(changes)} change(s) to {source}")
        else:
            print("\nRun with --write to apply.")
        return 0
    from clio.diagnostics import run_doctor
    src = Path(source) if source else None
    code, report = run_doctor(src, flow_name=flow)
    sys.stdout.write(report)
    return code


def _cmd_status(state_file: str | None, log_file: str | None, limit: int) -> int:
    from clio.diagnostics import status_summary
    sf = Path(state_file) if state_file else None
    lf = Path(log_file) if log_file else None
    sys.stdout.write(status_summary(sf, lf, limit))
    return 0


def _print_drift_list(drift: list[str]) -> None:
    """Print 'N file(s) changed' + first 5 paths + '... and N more' to stderr.

    Shared by --mode strict (exit-2 path) and --mode auto (warn-then-LLM path)."""
    print(f"{len(drift)} file(s) changed:", file=sys.stderr)
    for path in drift[:5]:
        print(f"  - {path}", file=sys.stderr)
    if len(drift) > 5:
        print(f"  ... and {len(drift) - 5} more", file=sys.stderr)


def _cmd_import(*, skill_dir: str, output: str | None, model: str, mode: str) -> int:
    from clio.emitters._sidecar import check_drift

    sk_path = Path(skill_dir)
    if not sk_path.is_dir():
        print(f"clio import: {skill_dir} is not a directory", file=sys.stderr)
        return 2

    source_file = sk_path / ".clio" / "source.clio"
    manifest_file = sk_path / ".clio" / "manifest.json"

    if mode == "strict":
        if not source_file.exists():
            print(
                f"clio import: --mode strict requires {source_file} (sidecar absent)",
                file=sys.stderr,
            )
            return 2
        try:
            drift = check_drift(sk_path, manifest_file)
        except FileNotFoundError:
            print(
                f"clio import: --mode strict and manifest missing at {manifest_file} "
                f"(partial sidecar). Cannot verify hashes.",
                file=sys.stderr,
            )
            return 2
        except (ValueError, KeyError) as e:
            # Corrupted manifest (empty / invalid JSON / missing keys)
            print(
                f"clio import: --mode strict and manifest at {manifest_file} is corrupted "
                f"({e}). Cannot verify hashes.",
                file=sys.stderr,
            )
            return 2
        if drift:
            print(
                "clio import: --mode strict and skill drifted.",
                file=sys.stderr,
            )
            _print_drift_list(drift)
            return 2
        return _recover_from_sidecar(sk_path, manifest_file, source_file, output, strict=True)

    if mode == "infer":
        return _import_via_llm(sk_path, model=model, output=output)

    # mode == "auto"
    if source_file.exists():
        try:
            drift = check_drift(sk_path, manifest_file)
        except FileNotFoundError:
            print(
                f"clio import: source.clio present but manifest missing at {manifest_file} "
                f"(partial sidecar). Falling back to LLM-assisted import.",
                file=sys.stderr,
            )
            # treat as drift — fall through to LLM
        except (ValueError, KeyError) as e:
            print(
                f"clio import: source.clio present but manifest at {manifest_file} is corrupted "
                f"({e}). Falling back to LLM-assisted import.",
                file=sys.stderr,
            )
            # treat as drift — fall through to LLM
        else:
            if drift is None:
                return _recover_from_sidecar(sk_path, manifest_file, source_file, output, strict=False)
            # Drift detected → warn and fall through to LLM
            emitted_at = _read_emitted_at(manifest_file)
            print(
                "clio import: skill has been modified since CLIO emitted it"
                + (f" on {emitted_at}." if emitted_at else "."),
                file=sys.stderr,
            )
            _print_drift_list(drift)
            print("Falling back to LLM-assisted import.", file=sys.stderr)

    return _import_via_llm(sk_path, model=model, output=output)


def _emit_imported_source(source_text: str, output: str | None) -> int:
    if output is None:
        sys.stdout.write(source_text)
    else:
        Path(output).write_text(source_text)
    return 0


def _recover_from_sidecar(
    sk_path: Path,
    manifest_file: Path,
    source_file: Path,
    output: str | None,
    *,
    strict: bool,
) -> int:
    """Recover the source from a CLIO sidecar. Single-file → emit the entry
    (stdout or file). Multi-file (`sources` present) → reconstruct the tree
    under the output directory."""
    import json as _json

    manifest = _json.loads(manifest_file.read_text(encoding="utf-8"))
    sources = manifest.get("sources")
    if not sources:
        return _emit_imported_source(source_file.read_text(), output)

    if strict:
        from clio.emitters._sidecar import check_source_drift

        src_drift = check_source_drift(sk_path, manifest_file)
        if src_drift:
            print(
                "clio import: --mode strict and stored sources drifted.",
                file=sys.stderr,
            )
            _print_drift_list(src_drift)
            return 2

    if output is None:
        print(
            "clio import: multi-file skill — pass --output <dir> to reconstruct "
            "the source tree.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(output).resolve()
    if out_dir.exists() and not out_dir.is_dir():
        print(
            f"clio import: multi-file skill — --output must be a directory, "
            f"but {out_dir} is an existing file.",
            file=sys.stderr,
        )
        return 2
    sources_dir = (sk_path / ".clio" / "sources").resolve()
    for rel in sorted(sources):
        # `rel` comes from a potentially untrusted manifest; reject any path
        # that would escape its base dir (path-traversal hardening).
        src = (sources_dir / rel).resolve()
        dst = (out_dir / rel).resolve()
        if not src.is_relative_to(sources_dir) or not dst.is_relative_to(out_dir):
            print(
                f"clio import: path traversal detected in source path: {rel}",
                file=sys.stderr,
            )
            return 2
        if not src.exists():
            print(
                f"clio import: stored source missing: {src} "
                "(incomplete sidecar; restore it or use --mode infer for "
                "best-effort single-file recovery).",
                file=sys.stderr,
            )
            return 2
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    entry = manifest.get("entry")
    print(
        f"clio import: recovered {len(sources)} source files to {out_dir} "
        f"(entry: {entry}).",
        file=sys.stderr,
    )
    return 0


def _read_emitted_at(manifest_file: Path) -> str | None:
    import json as _json
    try:
        return _json.loads(manifest_file.read_text(encoding="utf-8")).get("emitted_at")
    except (OSError, ValueError):
        return None


def _import_via_llm(skill_dir: Path, *, model: str, output: str | None) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "clio import: ANTHROPIC_API_KEY env var is not set "
            "(required for LLM-assisted import). "
            "Set the env var or use --mode strict on a CLIO-emitted skill.",
            file=sys.stderr,
        )
        return 1

    from clio import skill_to_clio
    try:
        source = skill_to_clio.generate(skill_dir, model=model)
    except skill_to_clio.GenerationError as e:
        print(f"clio import: {e.last_error}", file=sys.stderr)
        for line in e.last_attempt.splitlines():
            print(f"# {line}", file=sys.stderr)
        return 1

    return _emit_imported_source(source, output)
