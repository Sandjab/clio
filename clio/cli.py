import argparse
from pathlib import Path

from clio.emitters.claude_cli import ClaudeCLIEmitter
from clio.ir.builder import build_ir
from clio.parser.parser import ParseError, parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clio")
    sub = parser.add_subparsers(dest="cmd", required=True)

    compile_p = sub.add_parser("compile")
    compile_p.add_argument("source")
    compile_p.add_argument("--target", required=True, choices=["claude-cli"])
    compile_p.add_argument("--output", required=True)

    check_p = sub.add_parser("check")
    check_p.add_argument("source")

    args = parser.parse_args(argv)
    if args.cmd == "compile":
        return _cmd_compile(args.source, args.target, args.output)
    if args.cmd == "check":
        return _cmd_check(args.source)
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
    if target != "claude-cli":
        return 2
    ClaudeCLIEmitter().emit(graph, out_path)
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
