"""CLI entry point: `python -m classify`."""
import argparse
import json
import sys

from .flow import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="classify")
    parser.add_argument("--kwargs", default="{}", help="JSON dict of initial flow kwargs")
    args = parser.parse_args(argv)
    initial = json.loads(args.kwargs)
    result = run(**initial)
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
