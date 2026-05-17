"""Prompt loader. Prompts live as markdown files alongside this module so
they can be edited and reviewed independently of Python code."""
from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@cache
def load_prompt(name: str) -> str:
    """Load a prompt by name (without .md extension).

    Raises FileNotFoundError if the prompt is missing — surface loud rather
    than fall back to a wrong default."""
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
