"""Substitute ${var} placeholders in a template using values from state.json.

Each ${name} is replaced by the JSON serialization of state[name]. Strings are
JSON-encoded with quotes; complex values render as their JSON. The reserved
placeholder ${schema} is left as-is so the orchestrator can substitute the
step's schema separately.

Usage:
    python -m clio_runtime.substitute <template_path> <state_path>
"""

import json
import re
import sys
from pathlib import Path


_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render(template: str, state: dict) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name == "schema":
            return match.group(0)
        if name not in state:
            raise KeyError(f"missing state key for placeholder ${{{name}}}")
        return json.dumps(state[name])
    return _PLACEHOLDER.sub(repl, template)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m clio_runtime.substitute <template> <state.json>", file=sys.stderr)
        return 2
    template = Path(args[0]).read_text()
    state = json.loads(Path(args[1]).read_text())
    sys.stdout.write(render(template, state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
