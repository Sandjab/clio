# Getting started

## Install

```bash
git clone https://github.com/Sandjab/clio.git
cd clio
uv pip install -e .
```

You'll also need `claude` (the Claude Code CLI) authenticated locally if you want to compile to the `claude-cli` target, or `ANTHROPIC_API_KEY` set if you go to the `python` target.

Verify:

```bash
uv run python -m clio --help
```

You should see `compile`, `check`, `graph`, `gen`.

## Hello, CLIO

Create a file `hello.clio`:

```
STEP load_text
  TAKES: file: str
  GIVES: text: str
  MODE:  exact
  impl:
    mode: shell
    cmd:  "cat ${file}"

STEP summarize
  TAKES: text:    str
  GIVES: summary: str
  MODE:  judgment

FLOW hello
  load_text(file="input.txt")
    -> summarize(text)
```

Three things to notice:

- **`STEP`** declares an atomic unit of work with typed inputs (`TAKES`) and outputs (`GIVES`).
- **`MODE: exact`** means deterministic code (here a shell `cat`). **`MODE: judgment`** means an LLM call.
- **`FLOW`** wires steps together. The arrow `->` reads as "and then".

## Validate it

```bash
uv run python -m clio check hello.clio
```

No output = the file parsed and type-checked. If something's wrong, you'll get a `ParseError` with the exact line.

## Visualise it

```bash
uv run python -m clio graph hello.clio --format html --output hello.html
open hello.html
```

This opens a self-contained HTML viewer: the flow as a Mermaid diagram, with click-to-inspect cards showing each step's takes/gives, mode, cache policy, and referenced contracts. Useful to share in a PR or just to verify your mental model.

For a Mermaid string suitable to paste into a GitHub README:

```bash
uv run python -m clio graph hello.clio
```

## Compile and run

To produce a runnable Python project:

```bash
echo "Hello world" > input.txt
uv run python -m clio compile hello.clio --target python --output ./out
cp input.txt ./out/
```

The compiler emits a Python package under `./out/`:

```
out/
  pyproject.toml
  README.md
  hello/
    __main__.py        # the entrypoint
    flow.py            # the orchestrator
    contracts.py       # Pydantic models (none here)
    steps/
      load_text.py     # impl.shell — runs `cat` via subprocess
      summarize.py     # judgment — calls Anthropic SDK
    clio_runtime/      # cache + logging helpers
```

Install and run it:

```bash
uv pip install ./out
ANTHROPIC_API_KEY=sk-... cd out && hello
cat state.json   # the result of the flow
```

## Three lessons in this 5-minute walk

1. **CLIO source is declarative.** You said *what*, not *how to glue it together*.
2. **The compiler decides what runs as code vs LLM** based on `MODE`. Same `.clio` file can compile to Python, MCP server, or Claude Code orchestration depending on `--target`.
3. **The output is a real project.** No CLIO runtime to install at deploy time — just the emitted files plus their declared dependencies (Pydantic, Anthropic SDK if needed, etc.).

Next step: [the language tour](02-language-tour.md) for a deeper look at `STEP`, `CONTRACT`, and `FLOW`.
