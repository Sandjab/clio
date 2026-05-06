# `clio gen` — Natural Language to `.clio` (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-06.

## Goal

Add a `clio gen` subcommand that takes a natural-language description of a hybrid LLM/code pipeline and emits a valid `.clio` source file. The output must parse and build the IR — i.e. it must compile via the existing pipeline without manual edits.

This is the most demo-impressive feature on the v0.4+ roadmap (per `next_steps.md`). It also dogfoods the project's pitch: a compiler-first LLM tooling stack uses the LLM only at the appropriate layer (compile-time, NL → DSL), not as a runtime mediator.

## Non-goals (v0)

- No interactive REPL (one-shot per invocation).
- No autonomic compile chain — the user pipes/redirects the output to `clio compile` themselves.
- No multi-provider abstraction (Anthropic SDK only in v0).
- No pipeline-style decomposition inside the generator (see "Architecture" — single-LLM compile-correct loop, not multi-step extraction).

## Architecture

### Compile-correct loop

A single LLM produces the `.clio` source. The output is validated (parse + IR build); if validation fails, a single retry feeds the previous attempt and the validation error back to the model. After at most 1 retry, the loop terminates either with a valid source or a structured failure.

```
generate(description) -> str | raises GenerationError

┌─────────────────────────────────────────────┐
│  build messages: [system, user]             │
│  system = LANGUAGE_SPEC + 3 examples + rules │
│  user   = description                        │
└──────────┬──────────────────────────────────┘
           │
           ▼
┌─────────────────────────┐    parse + build_ir
│ anthropic.messages.create │ ─────────────► OK ─► return source
└────────────┬────────────┘                  │
             │                               │ FAIL
             │                               ▼
             │      ┌────────────────────────────────┐
             │      │  retry: append to messages:    │
             │      │   [assistant: prev, user: err] │
             │      └────────────┬───────────────────┘
             │                   │
             │                   ▼
             │   ┌─────────────────────────┐    parse + build_ir
             └─► │ anthropic.messages.create │ ────► OK ─► return source
                 └────────────┬────────────┘             │
                              │                          │ FAIL
                              ▼                          ▼
                  raise GenerationError(last_attempt, last_error)
```

The single retry is justified by the rationale in the brainstorm: if Sonnet fails twice with the full spec in context, the description is ambiguous — a third attempt won't fix it, and the user gets a clearer signal by failing fast.

### Why compile-correct over one-shot

The parser already produces precise error messages with line and column numbers. Feeding those back to the LLM is essentially free signal — far higher leverage than asking the LLM to re-read a static system prompt and self-correct without grounding. The pattern is also consistent with the project's broader "compiler is the oracle" philosophy.

### Why not pipeline (multi-step extraction)

Multi-step decomposition (extract STEPs first, then types, then contracts, then assemble) adds significant control-plane complexity and many more LLM calls per generation. Sonnet has the context window and capability to handle the full task in one shot when fed the spec. The pipeline approach is reserved for a v1 where we hit concrete failure modes that single-shot can't recover from.

## Components

### `clio/nl_to_clio.py` — generator module

Public interface:

```python
class GenerationError(Exception):
    """Raised when the LLM produced invalid .clio after the retry budget."""
    def __init__(self, last_attempt: str, last_error: str):
        self.last_attempt = last_attempt
        self.last_error = last_error
        super().__init__(f"failed to generate valid .clio: {last_error}")


def generate(
    description: str,
    *,
    model: str = "claude-sonnet-4-6",
    max_retries: int = 1,
    client: "anthropic.Anthropic | None" = None,  # injectable for tests
) -> str:
    """Compile-correct loop: returns a parseable + IR-buildable .clio source."""
```

Internal helpers:
- `_build_system_prompt() -> str` — concatenates the role intro, the contents of `docs/LANGUAGE_SPEC.md`, the three reference examples, and the output rules.
- `_strip_markdown_fences(raw: str) -> str` — removes leading ```clio ... ``` if the model adds them despite the instruction.
- `_validate(source: str) -> str | None` — runs `parse(source)` then `build_ir(...)`; returns `None` on success, the error string (with line/col) on failure.

### `pyproject.toml` — optional dependency group

```toml
[project.optional-dependencies]
gen = ["anthropic>=0.40"]
```

`anthropic` stays out of the core deps. The compile/check/graph commands work without it; only `clio gen` needs it. The module guards the import:

```python
try:
    import anthropic
except ImportError as e:
    raise ImportError(
        "clio gen requires the `anthropic` package. "
        "Install with: pip install 'clio[gen]'"
    ) from e
```

### `clio/cli.py` — new subcommand

```python
gen_p = sub.add_parser("gen")
gen_p.add_argument("description", nargs="?")
gen_p.add_argument("--from-file", dest="from_file")
gen_p.add_argument("--output")
gen_p.add_argument("--model", default="claude-sonnet-4-6")
```

Dispatch reads the description from (in order of precedence): `description` arg, `--from-file`, then stdin. Errors clearly when none are present.

Output: stdout by default; `--output FILE` writes to file.

On `GenerationError`: print the error message and the `last_attempt` (each line prefixed with `# `) to **stderr**, exit code 1, stdout stays empty so a shell redirect doesn't get a partial file.

On missing `ANTHROPIC_API_KEY`: print a clear message + the env-var name, exit code 1.

## System prompt structure

```
You are CLIO, a compiler from natural language to .clio source.

.clio is a declarative DSL for hybrid LLM/code pipelines. Three primitives:
STEP (unit of work, MODE = exact | judgment), CONTRACT (typed guarantee),
FLOW (composition). EXACT steps are deterministic (code, REST, shell);
JUDGMENT steps are LLM-invoked and validated against a CONTRACT.

# Language specification

<full contents of docs/LANGUAGE_SPEC.md>

# Reference examples

## Example 1 — customer churn detection (CSV in, classification out, with cache and on-fail)
<contents of examples/mvp.clio>

## Example 2 — named-entity recognition + summarization (nested record types, two contracts)
<contents of examples/entities.clio>

## Example 3 — corpus classification using FOR EACH and OpenAI-compat (LiteLLM → Gemini)
<contents of examples/classify_corpus.clio>

# Output rules

- Output ONLY a valid .clio source. No markdown fences. No prose. No commentary.
- Use the smallest set of features that solves the user's request.
- Step names are lowercase_with_underscores. Contract names are lowercase_with_underscores too.
- If the request is too vague to disambiguate (e.g. "do something with my data"), respond with a single line starting with "ERROR:" explaining what's missing.
- Do not invent features that do not appear in the language specification.
```

The system prompt is sent with `cache_control: {type: "ephemeral"}` so subsequent calls (the retry, plus future generations within the 5-minute TTL) read it from cache at ~10% of the input cost.

### Retry message structure

Turn 2 user message:

```
The .clio you produced did not parse / build. Here is the error:

<line:col error message from ParseError or IRBuildError>

Your previous output:
```
<previous .clio attempt>
```

Please correct the .clio. Output only the corrected source, no commentary.
```

The previous attempt is bracketed in a markdown code block in the *retry user message* (not in the model's expected output). The model's previous turn is included as an `assistant` message.

## Data flow

```
description (str)
    │
    ▼
_build_system_prompt()
    │
    ▼
anthropic.messages.create(system=..., messages=[user])
    │
    ▼
extract content[0].text
    │
    ▼
_strip_markdown_fences()
    │
    ▼
_validate() ─── OK ──► return source
    │
    │ FAIL (error string with line:col)
    ▼
anthropic.messages.create(
  system=...,
  messages=[user, assistant=prev, user=retry_template(prev, error)]
)
    │
    ▼
extract + strip + validate ─── OK ──► return source
    │
    │ FAIL
    ▼
raise GenerationError(last_attempt, last_error)
```

## Error handling matrix

| Failure mode | Where caught | User-facing behavior |
|---|---|---|
| `ANTHROPIC_API_KEY` env var unset | CLI dispatch | stderr message naming the env var, exit 1 |
| `anthropic` package missing | module import | `ImportError` with `pip install 'clio[gen]'` instruction |
| API error (rate limit, network, auth) | propagated from SDK | stderr SDK message, exit 1 |
| LLM returns "ERROR: ..." (refused for ambiguity) | parse fails on next line, retry triggers, eventually `GenerationError` | stderr error + last attempt commented out, exit 1 |
| `ParseError` after both attempts | `GenerationError` raised | stderr error + last attempt commented out, exit 1 |
| `IRBuildError` after both attempts | `GenerationError` raised | stderr error + last attempt commented out, exit 1 |

The "last attempt commented out" pattern means each line of the failed `.clio` is prefixed with `# ` and printed to stderr alongside the error. The user can copy-paste, fix manually, and proceed without re-asking the LLM.

## Testing strategy

### Unit — `tests/test_nl_to_clio.py`

Mock the `anthropic.Anthropic` client via a `FakeClient` injected through the `client=` parameter (no monkeypatching needed because the parameter is a public test seam).

- **`test_generate_returns_valid_clio_on_first_try`**: `FakeClient` returns a hard-coded valid `.clio`; assert `generate(...)` returns it and the client was called once.
- **`test_generate_retries_on_parse_error`**: `FakeClient` returns invalid then valid; assert two calls, the second's user message contains the error from the first.
- **`test_generate_retries_on_ir_build_error`**: same as above but the failure is in `build_ir` (e.g. step references unknown step), not `parse`.
- **`test_generate_raises_after_retry_budget`**: `FakeClient` always returns invalid; assert `GenerationError` with `last_attempt` and `last_error` populated.
- **`test_generate_strips_markdown_fences`**: `FakeClient` returns ```clio\n...\n```; assert output is the source without fences.
- **`test_system_prompt_contains_spec_and_examples`**: introspect the messages passed to the client, assert `LANGUAGE_SPEC.md` content is present and the 3 example sources are concatenated.
- **`test_system_prompt_uses_cache_control`**: assert the system message is sent with `cache_control: {type: "ephemeral"}`.

### CLI — extension to `tests/test_cli.py`

- **`test_gen_inline_argument`**: monkeypatch `nl_to_clio.generate` to a stub returning a fixed source, run `clio gen "describe X"`, capture stdout.
- **`test_gen_from_file`**: same with `--from-file desc.txt`.
- **`test_gen_from_stdin`**: same with stdin input via `monkeypatch.setattr(sys, "stdin", ...)`.
- **`test_gen_writes_to_output_file`**: same with `--output flow.clio`, assert file contents.
- **`test_gen_missing_api_key`**: ensure `ANTHROPIC_API_KEY` is unset, assert exit code 1 and the env-var name in stderr.
- **`test_gen_generation_error_prints_last_attempt`**: stub raises `GenerationError`, assert stdout empty + stderr contains both error and `# `-commented attempt + exit code 1.

### Out of scope for v0

No e2e test that hits the real Anthropic API. The cost / nondeterminism / API-key dependency outweigh the signal. Manual testing during development confirms the loop works end-to-end.

## Documentation updates

- `README.md` — add a `clio gen` row to the "Quick start" code block.
- `CLAUDE.md` — add `clio gen` to "How to run".
- `CHANGELOG.md` — new "CLI" entry under Unreleased.
- `LANGUAGE_SPEC.md` — **no change**. The grammar is unchanged; only a tooling consumer is added.

## Open questions intentionally deferred

- **Streaming output**: nice for UX but adds complexity. v0 is non-streaming. Add later if there's a need (e.g. for very long flows).
- **Multi-shot variations** (`clio gen "X" --variants 3`): would benefit from cache and let the user pick. Out of v0.
- **Description templates** (project-aware: pull contracts from existing files, etc.): premature without usage data.
- **Cost reporting** (input/output tokens, cache hit %): nice-to-have, can be added behind `--verbose` later.

## Review checklist (self)

- ✅ Placeholders: none — every section is concrete.
- ✅ Internal consistency: architecture, components, data flow, and error matrix all reference the same compile-correct loop and `GenerationError` shape.
- ✅ Scope: single feature (`clio gen` subcommand + module + tests + docs), single PR-able.
- ✅ Ambiguity: the "ERROR: ..." LLM refusal path was clarified — the retry triggers naturally because "ERROR: ..." is invalid `.clio` syntax, then `GenerationError` propagates. Documented in error matrix.
