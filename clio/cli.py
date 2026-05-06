import argparse
import sys
from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.emitters.python import PythonEmitter
from clio.graph_render import to_dot, to_mermaid
from clio.ir.builder import build_ir
from clio.parser.parser import ParseError, parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    compile_p = sub.add_parser("compile")
    compile_p.add_argument("source")
    compile_p.add_argument("--target", required=True, choices=["claude-cli", "python"])
    compile_p.add_argument("--output", required=True)

    check_p = sub.add_parser("check")
    check_p.add_argument("source")

    graph_p = sub.add_parser("graph")
    graph_p.add_argument("source")
    graph_p.add_argument("--format", choices=["mermaid", "dot"], default="mermaid")
    graph_p.add_argument("--output", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        return _cmd_compile(args.source, args.target, args.output)
    if args.cmd == "check":
        return _cmd_check(args.source)
    if args.cmd == "graph":
        return _cmd_graph(args.source, args.format, args.output)
    return 2


def _cmd_compile(source: str, target: str, output: str) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2

    try:
        graph = build_ir(parse(src_path.read_text()))
    except ParseError as e:
        print(f"{src_path.name}:{e}", flush=True)
        return 1

    out_path = Path(output)
    if target == "claude-cli":
        ClaudeCLIEmitter().emit(graph, out_path)
    elif target == "python":
        PythonEmitter().emit(graph, out_path)
    else:
        return 2
    return 0


def _cmd_check(source: str) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2
    try:
        parse(src_path.read_text())
    except ParseError as e:
        print(f"{src_path.name}:{e}", flush=True)
        return 1
    return 0


def _cmd_graph(source: str, fmt: str, output: str | None) -> int:
    src_path = Path(source)
    if not src_path.exists():
        print(f"clio: source file not found: {source}", flush=True)
        return 2
    try:
        graph = build_ir(parse(src_path.read_text()))
    except ParseError as e:
        print(f"{src_path.name}:{e}", flush=True)
        return 1

    rendered = to_mermaid(graph) if fmt == "mermaid" else to_dot(graph)
    if output is None:
        sys.stdout.write(rendered)
    else:
        Path(output).write_text(rendered)
    return 0
