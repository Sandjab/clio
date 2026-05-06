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


def _strip_markdown_fences(raw: str) -> str:
    """Remove leading ```clio/``` and trailing ``` fences if present.
    The model is told not to add fences, but Sonnet sometimes does anyway."""
    text = raw.strip()
    if not text.startswith("```"):
        return raw
    # First line is ```clio or ```; drop it
    first_newline = text.find("\n")
    if first_newline == -1:
        return raw
    body = text[first_newline + 1:]
    # Trailing fence: last line is ```
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.lstrip("\n").rstrip() + "\n"
