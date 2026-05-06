# `target: mcp-server` (Design)

Status: design approved, ready for implementation plan.
Date: 2026-05-07.

## Goal

Add a third compilation target — `mcp-server` — that compiles a `.clio` source into a runnable MCP (Model Context Protocol) server. Each `FLOW` becomes a tool exposed by the server. Judgment steps delegate to the MCP client's LLM via `sampling/createMessage` rather than calling a provider SDK directly. Drop-in usable from Claude Desktop, Cursor, Claude Code, or any MCP-aware client.

This is the highest-leverage target on the roadmap (`next_steps.md`, "Mood A — moat differentiator"): it concretizes the Anthropic-native-compiler pitch without requiring a clé API on the deployed server, and it positions CLIO directly inside the MCP ecosystem.

## Non-goals (v0)

- SSE / HTTP transports (stdio only — the standard for local MCP servers).
- MCP `prompts`, `resources`, `notifications` features (tools only).
- Server-side authentication (stdio servers run with the parent process's permissions).
- Multi-instance / horizontal scaling.
- Hot-reload of the `.clio` source.
- Streaming tool responses.

## Architecture

### Strategy: sampling-only

Judgment steps are emitted as wrappers around `session.create_message(...)` from the MCP Python SDK — i.e. they ask the *client* (Claude Desktop, Cursor, etc.) to perform the LLM call and return the result. The server never holds an API key, never depends on `anthropic` or `openai` SDKs.

Concretely:
- A step with `MODE: judgment` and no explicit `invoke:` block defaults to `mcp_sampling`.
- A step with `invoke.mode: api` and any provider (`anthropic`, `openai`, `bedrock`, `vertex`) is rejected at compile time with a message pointing at `--target python` (which has full SDK support).
- A step with explicit `invoke.mode: mcp_sampling` is honored as documented in `LANGUAGE_SPEC.md` §invoke.mode.

This is the structural decision that makes mcp-server distinct from the python target; it earns the "Anthropic-native" framing.

### Plumbing: official Python SDK

The emitted server uses the `mcp` Python package (`pip install mcp`, maintained by Anthropic / the MCP working group). This handles stdio transport, JSON-RPC framing, message types, tool registration, and `sampling/createMessage` semantics.

Rationale: MCP is an evolving protocol (sampling capabilities, structuredContent, tool annotations, model preferences) — owning the protocol bytes ourselves means tracking specs forever, for no value-add.

### Mapping `.clio` → MCP

| `.clio` element | MCP element |
|---|---|
| `FLOW <name>` | A registered tool whose `name` is `<name>` |
| TAKES of the first STEP in the FLOW | The tool's `inputSchema` (each TAKES → JSON Schema property) |
| Literal kwargs in the FLOW (e.g. `load_lines(file="reviews.txt")`) | Defaults in the inputSchema |
| GIVES of the last STEP in the FLOW | The tool's `outputSchema` (MCP 2025-06+) |
| CONTRACT declarations | Inlined as JSON Schemas in the relevant input/output schemas |
| FOR EACH, CACHE, ON_FAIL, impl.rest, impl.shell | Inherited unchanged from the `python` target's helpers |
| Judgment STEP (default or `invoke.mode: mcp_sampling`) | Wrapped in a function that calls `session.create_message(...)` and validates against the contract |

Multiple FLOWs in one `.clio` → multiple tools registered by the same server. A `.clio` with no FLOW is rejected at compile time.

### State at runtime

Tool calls are stateless from the MCP perspective. Each `tools/call` invocation executes `flow.run(**args)` from scratch in a fresh in-memory dict (mirrors how the `python` target's `flow.run` already works).

CACHE blocks (e.g. `CACHE: ttl(24h)`) write to a persistent cache directory on disk, exactly as in the `python` target — this survives across tool calls but is content-addressed (SHA256 of prompt + schema + model), so two simultaneous calls don't conflict on the same key.

## Components

### `clio/emitters/mcp_server.py` (new)

```python
class MCPServerEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path) -> None:
        ...
```

Reuses `_python_helpers` for: `_step_signature`, `_type_to_python`, `_render_type_short`, `_to_class_name`, `_to_field_name`, the entire FOR EACH lowering, the CACHE wrapper, the ON_FAIL chain, the impl.rest emission with url templating, and the impl.shell emission.

The delta versus the python emitter:
1. Judgment-step body uses `session.create_message(...)` instead of `anthropic.Anthropic().messages.create(...)` — see `_mcp_helpers.py`.
2. `pyproject.toml` deps: `mcp>=1.0` (always), `pydantic>=2` (if any contracts), `requests>=2.31` (if any rest steps). No `anthropic`, no `openai`.
3. New `server.py` file at the package root, registering each FLOW as a tool.
4. New `__main__.py` that runs the stdio server.
5. README emitted with the client-config snippet.

### `clio/emitters/_mcp_helpers.py` (new)

Module-level helpers:
- `_emit_judgment_step_via_sampling(step, contracts_by_name)` — produces a step file that calls `session.create_message(...)` with the JSON-only system prompt, the inlined schema in the user message, parses the response, validates against the contract (Pydantic), and returns the typed value.
- `_emit_server_module(graph)` — produces `server.py`: the `mcp.server.lowlevel.Server` setup, `@server.list_tools()` and `@server.call_tool()` handlers, schema generation per FLOW.
- `_emit_main_module(pkg_name)` — produces `__main__.py`: stdio runner that calls `await server.run(...)`.
- `_emit_readme(pkg_name, graph)` — produces a README with the MCP client config snippet and per-tool documentation.

### `clio/emitters/_python_helpers.py` (existing, no change expected)

Already exports the helpers the new emitter needs. The judgment-step emission lives in `clio/emitters/python.py:_emit_judgment_step` (anthropic) — that path is *not* reused for mcp-server. Instead the new `_mcp_helpers._emit_judgment_step_via_sampling` covers it.

### CLI surface (existing CLI)

`clio compile <source> --target mcp-server --output ./srv` (cohérent avec les autres targets). Add `mcp-server` to the `--target` choices in `clio/cli.py`.

## Data flow at runtime

```
MCP client (Claude Desktop, Cursor, …)
    │
    ▼  stdin/stdout (JSON-RPC)
[emitted server.py]
    │
    │  tools/list → registry from _emit_server_module
    │  tools/call → server.py dispatches to flow.run(**args)
    │
    ▼
flow.run(**initial)
    │
    ├─ EXACT step (impl.code/rest/shell) → standard Python execution
    │
    └─ JUDGMENT step (mcp_sampling)
           │
           │  step body builds a Pydantic-driven prompt (same as python target)
           │  but instead of anthropic.Anthropic().messages.create(...),
           │  it calls await ctx.session.create_message(
           │      messages=[...], system_prompt=..., max_tokens=...
           │  )
           │
           ▼
       MCP client routes the sampling request to its configured LLM
       (Claude, GPT-4, local model, anything the client wires up)
           │
           ▼
       Response comes back; step validates it against contract; returns
```

## Output project structure

```
output/
  pyproject.toml
  README.md
  <pkg_name>/
    __init__.py
    __main__.py             # `python -m <pkg_name>` starts the stdio server
    server.py               # MCP server: tool registration + dispatch to flow.run
    flow.py                 # `def run(**initial) -> dict` (from python target's _emit_flow)
    contracts.py            # Pydantic models from the contracts (from python target)
    steps/
      <step_name>.py        # exact steps: same as python target ; judgment: via sampling
    clio_runtime/
      cache.py              # copied from clio/runtime/cache.py (same as python target)
```

The `pkg_name` is derived from the source filename or first FLOW name (mirroring the python target's `_package_name` helper).

## Refused at compile time

| Combination | Rejection message |
|---|---|
| `invoke.protocol: anthropic` (or default judgment with no invoke block AND user explicitly set anthropic at RESOURCES) | `mcp-server target is sampling-only; for direct Anthropic SDK use --target python` |
| `invoke.protocol: openai` | `mcp-server target is sampling-only; for OpenAI-compat (LiteLLM/etc.) use --target python` |
| `invoke.protocol: bedrock` / `vertex` | already rejected globally; reject path remains |
| `invoke.mode: cli` | `mcp-server target does not support invoke.mode: cli; use claude-cli or python target` |
| `.clio` with no FLOW declared | `mcp-server target requires at least one FLOW (each FLOW is exposed as a tool)` |

The default judgment path (no `invoke:` block on the step) maps to `mcp_sampling` automatically — that's the whole point. `RESOURCES.target: claude-cli` set in the source does not block compilation when the CLI override `--target mcp-server` is passed (the CLI flag wins, same as for `--target python` today).

## Tool registration details

Pseudocode for the emitted `server.py`:

```python
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import flow as _flow

server = Server("<pkg_name>")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="<flow_name>",
            description="<derived from FLOW name + first step's docstring>",
            inputSchema={
                "type": "object",
                "properties": { ... derived from first STEP's TAKES ... },
                "required": [ ...names without literal defaults in the FLOW ...],
            },
            outputSchema={ ... derived from last STEP's GIVES ... },
        ),
        # ... one per FLOW
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "<flow_name>":
        result = _flow.run(**arguments)
        return [TextContent(type="text", text=json.dumps(result, default=str))]
    # ... dispatch per FLOW
    raise ValueError(f"unknown tool: {name}")
```

`outputSchema` is included even though some MCP clients don't yet consume it — the spec is forward-compatible (clients ignore unknown fields), and it documents the tool's contract.

## Judgment step via sampling — emitted shape

```python
"""STEP <name> (judgment, mcp_sampling).
Auto-generated. Do not edit; regenerate via `clio compile`.
"""
from __future__ import annotations
import json
from .. import contracts


_PROMPT_TEMPLATE = '...'   # same shape as python target
_INLINED_SCHEMA = '...'    # same shape as python target
_SYSTEM_PROMPT = (
    'You are a strict JSON-only API. Output exactly one JSON document …'
)
_MAX_TOKENS = 4096  # or override from invoke.max_tokens


async def <step_name>(*, <takes>, _session) -> <gives_type>:
    prompt = _PROMPT_TEMPLATE
    prompt = prompt.replace('${<take>}', json.dumps(<take>))
    prompt = prompt.replace('${schema}', _INLINED_SCHEMA)

    msg = await _session.create_message(
        messages=[{"role": "user", "content": {"type": "text", "text": prompt}}],
        system_prompt=_SYSTEM_PROMPT,
        max_tokens=_MAX_TOKENS,
        # model_hints / priorities come from invoke.mcp_sampling fields when set
    )
    raw = msg.content.text if msg.content.type == "text" else ""
    cleaned = '\n'.join(line for line in raw.splitlines() if not line.startswith('```'))
    return contracts.<ContractClass>.model_validate(json.loads(cleaned))
```

The `_session` is threaded through from the `call_tool` handler (which has access to the running MCP server's session via the SDK's request context). `flow.run` becomes `async def run(...)` for mcp-server target only — the python target keeps its sync `run`.

This means the server-side `flow.run` signature for mcp-server has an extra implicit `_session` dependency. Since FOR EACH and other helpers don't need the session, the threading is mechanical.

## Error handling

| Failure mode | Behavior |
|---|---|
| Tool called with missing required arg | Server returns JSON-RPC error from MCP SDK validation (input schema mismatch) |
| `flow.run` raises | Caught in `call_tool`; return a `TextContent` with `isError=true` and the error message |
| Sampling request fails (client disconnects, model errors) | Propagate as a `flow.run` exception path; ON_FAIL chain takes over if declared |
| Pydantic validation fails on judgment output | Standard ON_FAIL path (retry/escalate/fallback/abort), inherited from python target |
| `mcp` package missing | `__main__.py` imports `mcp` at module load — surfaces a clear ImportError when `python -m <pkg>` is run; the README's install step (`pip install -e .`) installs `mcp>=1.0` automatically |

## Testing strategy

### Unit tests — `tests/test_emitters/test_mcp_server.py`

Generation-only tests (compile a fixture, assert structure of emitted files):

- `test_emit_basic_flow_creates_server_module` — fixture with a single FLOW, assert `server.py` exists and registers a tool with the FLOW's name.
- `test_emit_pyproject_includes_mcp_dep` — assert `mcp>=1.0` in the emitted pyproject.
- `test_emit_pyproject_omits_anthropic_dep` — assert `anthropic` is NOT in the emitted pyproject (sampling-only differentiator).
- `test_emit_judgment_step_uses_session_create_message` — assert the emitted judgment step body contains `session.create_message` and does NOT contain `anthropic.Anthropic`.
- `test_emit_input_schema_from_first_step_takes` — fixture with `first_step(file: str)` → assert tool's inputSchema has `file: string`.
- `test_emit_input_schema_marks_required_when_no_literal_default` — same fixture without literal kwargs → `required: ["file"]`.
- `test_emit_input_schema_marks_optional_when_literal_default` — fixture with `first_step(file="default.csv")` → `default: "default.csv"`, not in `required`.
- `test_emit_output_schema_from_last_step_gives` — fixture with last step `GIVES: result: <contract>` → tool's outputSchema matches the contract's JSON Schema.
- `test_emit_multiple_flows_register_multiple_tools` — fixture with 2 FLOWs → server.py registers both.
- `test_emit_rejects_anthropic_protocol` — fixture with `invoke.protocol: anthropic` → ValueError at emit with the documented message.
- `test_emit_rejects_openai_protocol` — same for openai.
- `test_emit_rejects_when_no_flow` — fixture with only STEPs → ValueError.
- `test_emit_for_each_supported` — fixture with FOR EACH → emitted flow.py contains `for x in state[...]` (inherited from python target).
- `test_emit_cache_supported` — fixture with `CACHE: ttl(24h)` → emitted step has the cache wrapper.
- `test_emit_on_fail_supported` — fixture with `ON_FAIL: retry(3)` → emitted step has the retry wrapper.
- `test_emit_rest_step_supported` — fixture with `impl.mode: rest` → emitted step uses `requests.request(...)`.
- `test_emit_rest_step_with_url_templating_supported` — fixture with `${var}` in url → templating block emitted.
- `test_emit_shell_step_supported` — fixture with `impl.mode: shell` → emitted step uses `subprocess.run(...)`.

### Smoke runtime test

One end-to-end test that:

1. Compiles a minimal fixture (one FLOW with one exact step + one judgment step) to a `tmp_path`.
2. Installs the emitted package (in a virtualenv if needed, or via `pip install -e tmp_path` in the test env — but this requires `mcp` already installed, gated behind `CLIO_E2E=1` or `CLIO_MCP_E2E=1`).
3. Spawns `python -m <pkg>` as a subprocess.
4. Sends a `tools/list` JSON-RPC request on stdin.
5. Asserts the response on stdout contains the expected tool name and inputSchema.

This is gated behind an env var (similar to existing `CLIO_E2E=1`) so the default test run doesn't require `mcp` installed.

## Documentation updates

- `README.md` — add `mcp-server` to the targets table; add a Quick start example showing `--target mcp-server`.
- `CLAUDE.md` — add to "How to run".
- `CHANGELOG.md` — new "Emitters" entry under Unreleased.
- `LANGUAGE_SPEC.md` — implementation-status table: bump `mcp-server` row from N/A to ✅ for the supported features.
- `COMPILATION_TARGETS.md` — bump `mcp-server` from "Candidate" to "Implemented", expand its row with what's emitted.

## Open questions intentionally deferred

- **Streaming tool responses** — MCP supports `progress` notifications. Useful for long FLOWs (multi-minute LLM batches). Out of v0.
- **Server-side caching of `tools/list` responses** — the FLOW set is static at compile time. A future optimization could pre-serialize the tool list. Premature.
- **Resource exposure** — `.clio` source files could be exposed as MCP resources for client introspection. Nice for tooling but not the value-add. Defer.
- **Prompts capability** — JUDGMENT steps could be exposed as MCP prompts (re-usable templates). Probably not — they're internal to the FLOW. Defer.

## Review checklist (self)

- ✅ Placeholders: none.
- ✅ Internal consistency: sampling-only strategy is consistent across architecture, components, judgment-step shape, refusal table, and tests.
- ✅ Scope: single new emitter + new helpers module + CLI choice + tests + docs. Single PR-able. Reuses python emitter helpers; clear delta documented.
- ✅ Ambiguity: the `invoke.mode: cli` rejection (which is a documented concept in the spec but currently only emitted by claude-cli) is explicitly listed in the refusal table. The `outputSchema` forward-compat note is explicit. The async `flow.run` for mcp-server vs sync for python is documented.
