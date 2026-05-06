"""NL→.clio compiler. Wraps a single Anthropic SDK call in a compile-correct
loop: the model emits .clio, parse + build_ir validate it, and on failure
the model gets one shot at correction before GenerationError is raised."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


_ROLE_INTRO = """You are CLIO, a compiler from natural language to .clio source.

.clio is a declarative DSL for hybrid LLM/code pipelines. Three primitives:
STEP (unit of work, MODE = exact | judgment), CONTRACT (typed guarantee),
FLOW (composition). EXACT steps are deterministic (code, REST, shell);
JUDGMENT steps are LLM-invoked and validated against a CONTRACT.
"""


_OUTPUT_RULES = """# Output rules

- Output ONLY a valid .clio source. No markdown fences. No prose. No commentary.
- Use the smallest set of features that solves the user's request.
- Step names are lowercase_with_underscores. Contract names are lowercase_with_underscores too.
- If the request is too vague to disambiguate, respond with a single line starting with "ERROR:" explaining what's missing.
- Do not invent features that do not appear in the language specification.
"""


def generate(
    description: str,
    *,
    model: str = _DEFAULT_MODEL,
    max_retries: int = 1,
    client=None,
) -> str:
    """Compile-correct loop: returns a parseable + IR-buildable .clio source.

    Pass `client=` to inject a fake; otherwise a default Anthropic client is
    constructed (which requires the `anthropic` package and ANTHROPIC_API_KEY)."""
    if client is None:
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "clio gen requires the `anthropic` package. "
                "Install with: pip install 'clio[gen]'"
            ) from e
        client = anthropic.Anthropic()

    system_prompt = _build_system_prompt()
    system = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages: list[dict] = [{"role": "user", "content": description}]
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

        # Append assistant turn (the bad attempt) and a user correction.
        messages = messages + [
            {"role": "assistant", "content": candidate},
            {
                "role": "user",
                "content": _retry_message(candidate, err),
            },
        ]

    raise GenerationError(last_attempt=last_attempt, last_error=last_error)


def _retry_message(previous_attempt: str, error: str) -> str:
    return (
        "The .clio you produced did not parse / build. Here is the error:\n\n"
        f"{error}\n\n"
        "Your previous output:\n\n"
        "```\n"
        f"{previous_attempt}\n"
        "```\n\n"
        "Please correct the .clio. Output only the corrected source, no commentary."
    )


@lru_cache(maxsize=None)
def _build_system_prompt() -> str:
    """Build the system prompt for NL→.clio generation.

    Reads from:
    - docs/LANGUAGE_SPEC.md (full language reference)
    - examples/mvp.clio (example 1: customer churn detection)
    - examples/entities.clio (example 2: NER + summarization)
    - examples/classify_corpus.clio (example 3: corpus classification with FOR EACH)

    Returns a single string with role intro, spec, examples, and output rules.
    """
    spec = (_REPO_ROOT / "docs" / "LANGUAGE_SPEC.md").read_text()
    mvp = (_REPO_ROOT / "examples" / "mvp.clio").read_text()
    entities = (_REPO_ROOT / "examples" / "entities.clio").read_text()
    classify = (_REPO_ROOT / "examples" / "classify_corpus.clio").read_text()

    return (
        _ROLE_INTRO
        + "\n# Language specification\n\n"
        + spec
        + "\n\n# Reference examples\n\n"
        + "## Example 1 — customer churn detection (CSV in, classification out, with cache and on-fail)\n\n"
        + "```\n" + mvp + "```\n\n"
        + "## Example 2 — named-entity recognition + summarization (nested record types, two contracts)\n\n"
        + "```\n" + entities + "```\n\n"
        + "## Example 3 — corpus classification using FOR EACH and OpenAI-compat (LiteLLM → Gemini)\n\n"
        + "```\n" + classify + "```\n\n"
        + _OUTPUT_RULES
    )
