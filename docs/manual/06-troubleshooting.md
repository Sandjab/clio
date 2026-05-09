# Troubleshooting

Errors you're likely to hit, organised by where they happen in the pipeline.

## At parse time (`clio check` / `clio compile`)

### `ParseError: target 'rust' is not supported (valid targets: claude-cli, python, mcp-server)`

Your `RESOURCES target:` value isn't recognised. The three valid values are listed in the error message itself.

**Fix:** change `target: rust` to one of `claude-cli`, `python`, or `mcp-server`. The `target:` field is informational anyway — `--target` at compile time is what selects the emitter.

### `ParseError: RESOURCES with target: claude-cli requires a 'models' field`

You declared `target: claude-cli` without a `models:` line. Claude CLI needs the haiku→sonnet→opus escalation chain.

**Fix:** add `models: [haiku, sonnet, opus]` (or whichever subset). For `python` and `mcp-server` targets, `models:` is optional — per-step `invoke.api.model:` overrides.

### `ParseError: expected comparison operator at end of input`

Your `ASSERT` expression is missing its right-hand side, e.g. `ASSERT: score >=`.

**Fix:** complete the expression: `ASSERT: score >= 0.0`.

### `IRBuildError: line 22:8: unknown STEP 'classify'`

A `FLOW` calls a step name that wasn't declared. Often a typo (`classify` vs `classify_ticket`) or the `STEP` block is below the `FLOW` (CLIO doesn't care about order, but case-sensitivity does).

**Fix:** declare the step, or correct the spelling.

### `IRBuildError: line 36:5: FOR EACH iterates over 'tickets' but no upstream step produced it`

The collection variable in a `FOR EACH` doesn't match anything in scope at this point in the flow. Either the upstream step doesn't `GIVES` it, or its `GIVES` field has a different name.

**Fix:** check your upstream `STEP`'s `GIVES: tickets: List<...>`. The names must match exactly.

## At emit time

### `ValueError: claude-cli target does not support FOR EACH PARALLEL`

The `claude-cli` emitter explicitly rejects `FOR EACH ... PARALLEL AS` because bash can't safely manage concurrent state.

**Fix:** compile to `--target python` or `--target mcp-server` instead. Or rewrite the loop as a sequential `FOR EACH` if the parallelism isn't critical.

### `ValueError: invoke.protocol 'bedrock' is not yet supported`

Bedrock and Vertex are specced but not implemented in any emitter yet.

**Fix:** route through an OpenAI-compat proxy (LiteLLM) and use `protocol: openai`, **or** stick to `protocol: anthropic` for direct Claude.

### `ValueError: CONTRACT 'foo' ASSERT references multi-field (...)`

Your `ASSERT` expression references more than one field name, e.g. `ASSERT: a > b` — Pydantic field validators only see one field at a time.

**Fix:** either restructure your contract so the constraint is on a single field, or wait for the planned `model_validator` extension. For numeric ranges on the same field (`0.0 <= score <= 1.0`), use chained comparators — that's a single field, multi-comparison, and is supported.

## At runtime (after `bash run.sh` / `python -m flow_name`)

### `pydantic_core.ValidationError: ASSERT failed: (urgency_score >= 0.0) and (urgency_score <= 1.0)`

The LLM returned a value out of the declared range. The contract validator caught it.

**What the runtime does:** triggers the step's `ON_FAIL` chain. If you have `ON_FAIL: retry(3) then escalate ...`, it reattempts up to 3 times, then escalates to the next model in `RESOURCES.models` (claude-cli) or whatever `escalate` means for your target.

**Fix:** if it happens chronically, sharpen the prompt — make the contract's range constraint explicit in the JSON schema the model sees.

### `[clio] resume requested (start_at=N) but state.json missing`

You ran `<entrypoint> --from-step N` but there's no `state.json` in the cwd (or where `CLIO_STATE_FILE` points).

**Fix:** run the flow from scratch first to produce a `state.json`, *then* resume.

### `ANTHROPIC_API_KEY not set`

The Python target's anthropic-protocol step couldn't authenticate.

**Fix:** `export ANTHROPIC_API_KEY=sk-ant-...`. For OpenAI-compat steps, set whatever env var is named in the step's `auth: env:NAME` field.

### `subprocess.TimeoutExpired` on an `impl.shell` step

Your shell command exceeded its `timeout: <s>` setting (or the runtime default).

**Fix:** raise the timeout in the step's `impl.shell` block, or split the work.

## At graph render time

### Mermaid renders but click-to-inspect doesn't work in `--format html`

The viewer needs internet to load `mermaid@10` + Geist fonts from CDN.

**Fix:** check the network. There's no offline `--inline` flag yet (planned). If you need offline, `--format mermaid` produces source that works in any markdown viewer.

### The arrows in the HTML viewer arrive at the wrong corner of the cards

Mermaid v10 with rich HTML labels has bbox-measurement quirks. CLIO works around this by **not** forcing `display: block` on Mermaid's wrappers. If you've extended the CSS yourself, watch for that.

**Fix:** if you didn't extend the CSS, file a bug — this should not happen on the bundled viewer.

## Build & install

### `pip install ./out` fails with "Multiple top-level packages discovered in a flat-layout"

The emitted project has a flat layout (no `src/`). Setuptools 68+ wants this configured.

**Fix:** the emitted `pyproject.toml` already declares `[tool.setuptools.packages.find]` correctly. If you're seeing this, your local setuptools may be older than 68 — `pip install -U setuptools` first.

## When the docs and code diverge

The CHANGELOG, language spec, and this manual are kept in sync **per release tag**. If you're on `main` between tags, expect occasional drift.

**Authoritative sources, in order of trust:**
1. The tests in `tests/` (machine-checked).
2. `LANGUAGE_SPEC.md` (kept current with each merge).
3. This manual (updated on tag boundaries — slightly delayed).

If something here contradicts the tests, the tests win and this page is stale. File an issue.

---

That's the manual. Compile, run, ship. If you hit something not covered here, open a GitHub issue with the `.clio` source + the exact error.
