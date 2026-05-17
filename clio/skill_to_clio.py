"""LLM-assisted skill → .clio importer. Mirror of `nl_to_clio.py` adapted
for the import use case.

Single Anthropic SDK call wrapped in a compile-correct loop: the model emits
a `.clio` source, parse + build_ir validate it, and on failure the model gets
one shot at correction before GenerationError is raised."""
from __future__ import annotations

import sys
from pathlib import Path

from clio.ir.builder import IRBuildError, build_ir
from clio.parser.parser import ParseError, parse
from clio.prompts import load_prompt


class GenerationError(Exception):
    """Raised when the LLM produced invalid .clio after the retry budget,
    or when pre-flight checks (payload size) fail."""

    def __init__(self, last_attempt: str, last_error: str) -> None:
        self.last_attempt = last_attempt
        self.last_error = last_error
        super().__init__(f"failed to import skill: {last_error}")


_EXCLUDED_BASENAMES = frozenset({"_validate.py", "_cache_key.py"})
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 8000
_SIZE_WARN_TOKENS = 100_000
_SIZE_ABORT_TOKENS = 180_000


def _approx_tokens(payload: str) -> int:
    return len(payload) // 4


def _check_size(payload: str) -> None:
    """Warn on > 100k tokens, abort on > 180k. Conservative — leaves
    ~20k tokens of headroom for the model's response in a 200k context."""
    n = _approx_tokens(payload)
    if n > _SIZE_ABORT_TOKENS:
        raise GenerationError(
            last_attempt="",
            last_error=(
                f"skill payload too large ({n} approx tokens; threshold "
                f"{_SIZE_ABORT_TOKENS}). Try `--mode strict` if the skill "
                f"was CLIO-emitted, or split the skill manually."
            ),
        )
    if n > _SIZE_WARN_TOKENS:
        print(
            f"clio import warning: large skill payload ({n} approx tokens; "
            f"threshold {_SIZE_WARN_TOKENS}). Proceeding with the SDK call.",
            file=sys.stderr,
        )


def _gather_skill_files(skill_dir: Path) -> str:
    """Walk `skill_dir`, concatenate readable text files with delimiters.

    Excludes:
      - All hidden files/directories (any path component starting with '.'):
        covers `.clio/` (anti-cheating), `.git/`, `.DS_Store`, `.idea/`, etc.
      - `_validate.py` and `_cache_key.py` (CLIO boilerplate).
      - Binary files (caught via UnicodeDecodeError)."""
    parts: list[str] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(skill_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.name in _EXCLUDED_BASENAMES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(skill_dir).as_posix()
        parts.append(f"=== {rel} ===\n{content}\n")
    return "\n".join(parts)


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
    """Remove leading ```clio/``` and trailing ``` fences if present."""
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


def _build_user_message_initial(payload: str) -> str:
    return (
        "The following files compose a Claude Code skill. Produce the .clio "
        "source that would emit this skill. Follow the language policy and "
        "annotation rules from the system prompt.\n\n"
        + payload
    )


def generate(
    skill_dir: Path,
    *,
    model: str = _DEFAULT_MODEL,
    max_retries: int = 1,
    client=None,
) -> str:
    """Import a Claude Code skill back to .clio via an LLM-assisted pass.

    Pass `client=` to inject a fake; otherwise a default Anthropic client is
    constructed (requires the `anthropic` package and ANTHROPIC_API_KEY)."""
    payload = _gather_skill_files(skill_dir)
    _check_size(payload)  # may raise GenerationError before any SDK call

    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "clio import requires the `anthropic` package. "
                "Install with: pip install 'clio[gen]'"
            ) from e
        client = anthropic.Anthropic()

    system_prompt = load_prompt("skill_to_clio_system")
    system = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages: list[dict] = [
        {"role": "user", "content": _build_user_message_initial(payload)}
    ]
    last_attempt = ""
    last_error = ""

    for attempt_idx in range(max_retries + 1):
        msg = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=messages,
        )
        raw = msg.content[0].text if msg.content else ""
        candidate = _strip_markdown_fences(raw)
        err = _validate(candidate)
        if err is None:
            return candidate

        last_attempt = candidate
        last_error = err

        if attempt_idx == max_retries:
            break

        messages = [
            *messages,
            {"role": "assistant", "content": candidate},
            {
                "role": "user",
                "content": load_prompt("skill_to_clio_retry").format(
                    previous_attempt=candidate, error=err,
                ),
            },
        ]

    raise GenerationError(last_attempt=last_attempt, last_error=last_error)
