import argparse


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
    return 0


def _cmd_check(source: str) -> int:
    return 0
