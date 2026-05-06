"""NL→.clio compiler. Wraps a single Anthropic SDK call in a compile-correct
loop: the model emits .clio, parse + build_ir validate it, and on failure
the model gets one shot at correction before GenerationError is raised."""
from __future__ import annotations

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse


class GenerationError(Exception):
    """Raised when the LLM produced invalid .clio after the retry budget."""

    def __init__(self, last_attempt: str, last_error: str) -> None:
        self.last_attempt = last_attempt
        self.last_error = last_error
        super().__init__(f"failed to generate valid .clio: {last_error}")


def _validate(source: str) -> str | None:
    """Parse + build_ir. Returns None on success, an error string with
    line/col on failure."""
    try:
        program = parse(source)
    except ParseError as e:
        return str(e)
    try:
        build_ir(program)
    except IRBuildError as e:
        return str(e)
    return None
