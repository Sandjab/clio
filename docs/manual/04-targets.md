# Compilation targets

CLIO emits a runnable project for **one of four targets** today, selected via `--target`:

| Target | Output | Best for |
|---|---|---|
| `claude-cli` | Bash orchestrator + step files (.sh / .prompt) calling `claude -p` | Scripts, demos, "I want it to run with Claude Code in my shell" |
| `python` | Python package (Anthropic / OpenAI SDK) | Production, integration with other Python code, CI pipelines |
| `mcp-server` | Python MCP server using the official `mcp` SDK | Exposing the flow as a tool to Claude Desktop / IDE / any MCP client |
| `langgraph` | Python package whose `flow.py` builds a `langgraph.graph.StateGraph` | Bridging into LangChain ecosystems; using LangGraph's runtime features (persistence, human-in-the-loop) with CLIO-defined logic |

The `RESOURCES.target:` field in the `.clio` source is **informational** — the `--target` flag at compile time is what actually selects the emitter.

## When to use which

### `claude-cli`

```bash
uv run python -m clio compile flow.clio --target claude-cli --output ./out
bash ./out/run.sh
```

You get a `run.sh` orchestrator + step files. Each `judgment` step is a `claude -p --model haiku` invocation against a `.prompt` template. State lives in `state.json`, threaded between steps via `jq`.

**Use when:**
- You want to demo a flow without setting up a Python env.
- You want to read each prompt as a flat file and tweak it iteratively.
- You're already in Claude Code and don't want to spin up an SDK.

**Don't use when:**
- You need `FOR EACH PARALLEL` (rejected at compile time — bash can't async safely).
- You need OpenAI / Bedrock / Vertex (only Anthropic via `claude-cli` today).
- You need precise model overrides per step (uses `RESOURCES.models` chain only).

### `python`

```bash
uv run python -m clio compile flow.clio --target python --output ./out
uv pip install ./out
ANTHROPIC_API_KEY=... my_flow_name
```

You get a clean Python package with `pyproject.toml`, `flow.py` orchestrator, `contracts.py` (Pydantic models), and one `steps/<name>.py` per step.

**Use when:**
- You want to integrate the flow into existing Python code.
- You need OpenAI-compat (LiteLLM, vLLM, Ollama, OpenRouter, Together, Groq).
- You need `FOR EACH PARALLEL` with `concurrent.futures.ThreadPoolExecutor`.
- You need step-granularity resume (`--from-step N` reads the persisted `state.json`).

**Don't use when:**
- You don't want a Python toolchain in the loop. Pick `claude-cli`.

### `mcp-server`

```bash
uv run python -m clio compile flow.clio --target mcp-server --output ./out
uv pip install ./out
```

You get a Python MCP server that registers each `FLOW` as a tool. Judgment steps delegate to the MCP client via `sampling/createMessage` — **no API key on the server**, no `anthropic`/`openai` SDK dep.

**Use when:**
- You want to expose your flow as a tool inside Claude Desktop / Cursor / any MCP client.
- You don't want to manage API keys (the client provides the LLM access).
- You want the flow callable from another agentic system without re-implementing it.

**Don't use when:**
- You need `protocol: bedrock` or `protocol: vertex` (rejected at compile time — point your client at MCP sampling instead).
- You need `invoke.mode: cli` (Claude CLI is per-machine; MCP clients hold the LLM access).

### `langgraph`

```bash
uv run python -m clio compile flow.clio --target langgraph --output ./out
uv pip install ./out
ANTHROPIC_API_KEY=... my_flow_name --kwargs '{"file": "input.txt"}'
```

You get a Python package whose `flow.py` builds a `langgraph.graph.StateGraph`:

```python
from <pkg>.flow import build_graph, run

app = build_graph()                        # the compiled StateGraph
state = app.invoke({"file": "input.txt"})  # or just: run(file="input.txt")
```

Each `STEP` becomes a node function `(state: State) -> dict`. The State is a `TypedDict` aggregating all TAKES and GIVES across the flow. `retry(N)` from `ON_FAIL` translates to a `RetryPolicy(max_attempts=N)` on the corresponding `add_node` call. Step files themselves are reused verbatim from the python target — only the orchestrator changes.

**Use when:**
- You're already in a LangChain/LangGraph stack and want CLIO-defined logic to fit native.
- You need LangGraph's runtime features (persistence layer, human-in-the-loop, event streaming) on top of a CLIO-described pipeline.

**Don't use when (v0):**
- You need `FOR EACH` (any kind) — rejected at compile time. Send-API support is planned for v0.7. Use `--target python` today.
- You need `invoke.api.openai/bedrock/vertex` — only `anthropic` is wired in v0. Use `--target python`.
- You need `invoke.mode: cli` — LangGraph runs server-side. Use `--target claude-cli`.
- You need `ON_FAIL escalate` or `fallback(<step>)` — only `retry(N)` and `abort(...)` are wired in v0. Use `--target python` for the full retry chain.

## Cross-target feature support

| Feature | claude-cli | python | mcp-server | langgraph |
|---|:-:|:-:|:-:|:-:|
| `MODE: exact` (code stub) | ✅ | ✅ | ✅ | ✅ |
| `MODE: exact` + `impl.shell` | ✅ | ✅ | ✅ | ✅ |
| `MODE: exact` + `impl.shell` + `parse: json` | ⚠️ silently ignored | ✅ | ✅ | ✅ |
| `MODE: exact` + `impl.rest` | ✅ (uses `requests` at runtime) | ✅ | ✅ | ✅ |
| `MODE: judgment` + `invoke: cli` (default) | ✅ | ❌ rejected | ❌ rejected | ❌ rejected |
| `MODE: judgment` + `invoke.api.anthropic` | (uses `RESOURCES.models` chain) | ✅ | ❌ rejected | ✅ |
| `MODE: judgment` + `invoke.api.openai` | ❌ | ✅ | ❌ | ❌ rejected (v0) |
| `MODE: judgment` + `invoke.api.bedrock`/`vertex` | ❌ | ❌ | ❌ | ❌ |
| `CACHE: ttl(...)` | ✅ | ✅ | ✅ | ✅ (reuses python runtime) |
| `ON_FAIL: retry(N)` | ✅ | ✅ | ✅ | ✅ via `RetryPolicy` |
| `ON_FAIL: escalate / fallback` | ✅ | ✅ | ✅ minimum-compliance | ❌ rejected (v0) |
| `ON_FAIL: abort` | ✅ | ✅ | ✅ | ✅ |
| `FOR EACH` (sequential) | ✅ | ✅ | ✅ | ❌ rejected (v0; v0.7) |
| `FOR EACH ... PARALLEL AS` | ❌ rejected | ✅ ThreadPool | ✅ asyncio.gather | ❌ rejected (v0; v0.7 via Send) |
| `--from-step N` resume | ❌ | ✅ | ❌ | ❌ (use LangGraph checkpointers) |
| `clio graph --format html` | n/a (graph is target-independent) | n/a | n/a | n/a |

## A common workflow: `python` for production, `claude-cli` for sketches

A `.clio` file is target-independent (modulo the limitations above). A common pattern:

1. **Sketch** the flow with `--target claude-cli`. Read the emitted `.prompt` files, tune the wording.
2. **Test** at scale with `--target python` once the prompts are stable.
3. **Distribute** as `--target mcp-server` if you want it consumable by other AI clients.
4. **Bridge** to `--target langgraph` if you need to plug into LangChain runtime features (checkpointers, human-in-the-loop, streaming). Subset features today, full parity is on the v0.7+ roadmap.

The same source compiles all four (within each target's scope).

Next: [CLI reference](05-cli-reference.md) for every command and flag.
