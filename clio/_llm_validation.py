"""Shared utilities for LLM-generated .clio validation.

Used by clio.nl_to_clio (natural-language → .clio) and clio.skill_to_clio
(skill → .clio). Both modules wrap a single Anthropic SDK call in a
compile-correct loop: the model emits .clio, parse+build_ir validate it,
and on failure the model gets one shot at correction.

The strip-fences helper and the validator live here rather than in either
caller so a change applies uniformly to both modules."""
from __future__ import annotations

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse


def validate(source: str) -> str | None:
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


def strip_markdown_fences(raw: str) -> str:
    """Remove leading ```clio/``` and trailing ``` fences if present.
    The model is told not to add fences, but Sonnet sometimes does anyway."""
    text = raw.strip()
    if not text.startswith("```"):
        return raw
    first_newline = text.find("\n")
    if first_newline == -1:
        return raw
    body = text[first_newline + 1:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.lstrip("\n").rstrip() + "\n"
