"""CLI entry point: `python -m iterative_refiner`."""
import argparse
import json
import sys

from .flow import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iterative_refiner")
    parser.add_argument("--kwargs", default="{}", help="JSON dict of initial flow kwargs")
    parser.add_argument(
        "--from-step",
        type=int,
        default=0,
        metavar="N",
        help="Resume from step N+1 (1-based; reads state.json or $CLIO_STATE_FILE).",
    )
    args = parser.parse_args(argv)
    if args.from_step < 0:
        print(f"[clio] --from-step must be >= 0, got {args.from_step}", file=sys.stderr)
        return 2
    initial = json.loads(args.kwargs)
    result = run(start_at=args.from_step, **initial)
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
