# CLIO — `target: claude-skill` emitter — design

**Date**: 2026-05-14
**Sprint**: TBD (candidate for v0.14)
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

A Claude Code *skill* is a portable, self-contained instruction unit — a directory with a `SKILL.md` (frontmatter + markdown body), optional `scripts/`, `assets/`, `references/`. Skills are invoked by the LLM host (Claude Code, Copilot CLI, Gemini CLI, …) via a `Skill` tool, auto-triggered by their frontmatter `description`. They are the unit of reuse that the broader Claude Code ecosystem revolves around (cf. the dozens of `superpowers:*`, `impeccable:*`, `feature-dev:*`, … skills already on this machine).

CLIO today emits three runnable targets — `claude-cli`, `python`, `mcp-server` — but none of them produces a skill. Yet a `.clio` source is structurally a perfect skill candidate: it is typed (CONTRACTs), it is deterministic where it matters (STEP `exact`), it is auditable (process flow graph), and its judgments are explicit (STEP `judgment` + contract). Writing a serious skill today means hand-crafting markdown, with no validation, no typed state, no retry/rescue semantics, no caching. CLIO can change that.

This spec describes a new emitter, `target: claude-skill`, that takes an IR and produces a directory installable as a Claude Code skill, **executed by the LLM host itself** (no external runtime, no API key, no CLIO binary on the user's machine after install).

The reverse direction (skill → CLIO importer) is **out of scope** for this spec — see Roadmap.

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| Direction | CLIO → skill (emitter) only | (a) skill → CLIO (importer) — separate spec, requires a judgment classifier; (b) round-trip — scope creep |
| Skill purpose | Runtime alternative — the emitted skill **executes** the flow on the LLM host | (a) Packaging-only stub; (b) Pedagogical documentation skill (already covered by `clio graph --format html`) |
| Execution model | LLM-orchestrated — the LLM host reads `SKILL.md` and drives the flow, calling sub-scripts for `exact` steps and producing judgment outputs in-line | (a) Script-orchestrated (`SKILL.md` says "run ./run.sh") — redundant with `claude-cli`; (b) MCP-delegated — depends on an external `mcp-server` running, not self-contained |
| Skill skeleton | Hybrid: narrative master `SKILL.md` + `scripts/` (exact) + `schemas/` (contracts) + `prompts/` (judgment templates) + `process_flow.dot` | (a) Minimalist stub — not a real skill; (b) Pure narrative — drifts on control flow without an anchor |
| Drift anchor | TodoWrite checklist (one todo per major step, mandated in the emitted body) + section-end "tick the todo, validate before advancing" instructions + final "verification" section that checks all todos done and contract-of-flow fields present | Inline LLM-self-assertions only — too weak |
| Languages supported for STEP `exact` in v1 | `python` and `bash` only | Rust/Go/…: error at compile time, direct user to a different target |
| Parallelism | Serialized in topological order, with compile warning if the source flow has `PARALLEL` | Genuine concurrency — the LLM host doesn't truly parallelize; would force a script-orchestrated layer (rejected above) |
| Reuse of existing helpers | Targeted **duplication** into `_claude_skill_helpers.py` of the minimum needed from `_python_helpers.py` for `exact`/python script bodies. No edits to `python.py` or `_python_helpers.py`. | (a) Importing across emitters — forbidden by CLAUDE.md ("emitters never import from each other"); (b) Refactoring shared code into a common module — surgical-changes rule, out of scope for this sprint |
| Frontmatter `description` | Derived from `FLOW.description` if present; otherwise default + compile **warning** (auto-trigger weakens) | Auto-generating a description with another LLM call — out of scope, deterministic emitter only |
| State format | Single namespaced JSON file `state.json` (`state.<step_name>.<field>`) — same convention as `claude-cli` / `python` | Per-step state files — fragments harder for the LLM host to reason about |
| Natural language of emitted markdown | English by default. If the source `.clio` carries a `lang:` flow-level hint (already free-form metadata), the emitter respects it for section titles ("Step" → "Étape", "If" → "Si", "Cache" → "Mise en cache", "For each" → "Pour chaque", …). Frontmatter `description` always preserves the source. | Hardcoded English — "Étape" examples in this spec come from FR-source flows; we don't want the emitter to assume English universally. (Translation note: "Cache" in French is *"Mise en cache"* — never *"cacher"* which means *to hide*.) |
| Bundled helper scripts | The emitter ships small autonomous helper scripts inside the output's `scripts/` directory: `_validate.py` (JSON Schema validation, stdlib-fallback if `jsonschema` is missing) and `_cache_key.py` (SHA256 of a normalized input subset). The SKILL.md invokes them via `python scripts/_validate.py …` and `python scripts/_cache_key.py …` — no PyPI dep at runtime, no hashing in-LLM-prose. | (a) `python -m jsonschema` — requires PyPI install on the LLM host's machine; conflicts with "no external runtime"; (b) instruct the LLM to hash inputs textually — error-prone, breaks cache hit determinism |
| v0.13 features supported (RESCUE / `step.error.*` / RESUME / CACHE / RETRY / RESOURCES) | All supported at v1, parity with `python` and `mcp-server` | Phased rollout — would leave the first cut artificially weaker than peer emitters |
| Tests | Three layers: emission structure (heavy), exact-script execution (medium), LLM-host fidelity (manual, documented) | LLM-as-judge automated test — non-deterministic, expensive, low value |

## Architecture

### New files

```
clio/emitters/
  claude_skill.py              # ClaudeSkillEmitter(BaseEmitter)
  _claude_skill_helpers.py     # markdown rendering, frontmatter, schema dump, script body generator (extracted minimum from _python_helpers)
tests/test_emitters/
  test_claude_skill.py         # mirror of test_claude_cli.py
docs/
  COMPILATION_TARGETS.md       # update: claude-skill moves from "future/candidate" to "Implemented"
  manual/                      # add cookbook recipe + troubleshooting entries (per project rule)
```

### Emitter interface

Same `BaseEmitter.emit(graph: FlowGraph, output_dir: Path) -> None`. Registered in `cli.py`'s target table. CLI: `python -m clio compile flow.clio --target claude-skill --output ./skill-out`.

### Emitted output structure

```
<output-dir>/
  SKILL.md                          # narrative orchestrator + checklist
  scripts/
    _validate.py                    # bundled: JSON Schema validation, stdlib fallback if jsonschema absent
    _cache_key.py                   # bundled: SHA256 cache-key generator over normalized input
    NN_<exact-step>.py              # or .sh — one autonomous script per exact STEP
  schemas/
    NN_<step>.input.json            # JSON Schema for step input (when typed)
    NN_<step>.output.json           # JSON Schema for step output / contract
  prompts/
    NN_<judgment-step>.md           # prompt template with {{state.x}} placeholders
  process_flow.dot                  # DOT rendering of the flow
  state.example.json                # initial-state template
  README.md                         # 5-line human intro (what this skill does)
```

## `SKILL.md` skeleton

Each block is emitted only when relevant. Italicized text in `<>` is filled at emit time. Section titles below are shown in French (`Étape`) for a FR-source flow; for an EN-source flow, they read `Step NN — …`. See the "Natural language" decision above.

```markdown
---
name: <flow-name-kebab>
description: <FLOW.description, or default + warning>
allowed-tools: Bash, Read, Write, TodoWrite
---

# <FLOW Title>

<FLOW docstring/description if present>

## Process flow

<DOT graph inline, or "see `process_flow.dot`" if > N lines>

## Initial state

The flow state lives in `state.json`. Template: `state.example.json`. Copy it to `state.json`, then create one TodoWrite todo per step listed below before starting.

## Étape 01 — <step_name> (MODE: exact)

<step docstring>

**Reads from state**: <fields>
**Writes to state**: <fields>, validated against `schemas/01_<step>.output.json`

Run:
    python scripts/01_<step>.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding todo. Do not advance until the script exited 0.

## Étape 02 — <step_name> (MODE: judgment)

<step docstring>

**Reads from state**: <fields>
**Expected output** (contract `schemas/02_<step>.output.json`):
    <one-line summary>

Prompt template (rendered from `prompts/02_<step>.md`):

> <prompt with {{state.x}} substituted from state.json>

After generating the output:
1. Save your response verbatim to `out.json`.
2. Validate: `python scripts/_validate.py out.json schemas/02_<step>.output.json` (exit 0 = valid)
3. If valid: merge into `state.json` under `state.<step_name>`
4. If invalid: see RESCUE section below (or retry per RETRY config, if any)

Tick the corresponding todo.

## Control structures

(only emitted if the flow contains them)

### FOR EACH <var> IN <state.collection>   (source line L)
For each element:
- create a sub-todo "Iteration <var>=<value>"
- run the sub-sequence: Étape K → Étape K+1 → …
- mark the sub-todo done, append result to `state.<output>`

### IF <condition>   (source line L)
Evaluate the condition (inline bash for `exact` conditions, in-line judgment for `judgment` conditions). True → Étape A. False → Étape B.

(MATCH and WHILE similarly rendered)

## RESCUE handlers

### If Étape 02 fails
Available expressions in the handler body: `step02.error.message`, `step02.error.type`.
Action: <handler description — chain of exact/judgment sub-steps as in the source `.clio`>
RESUME: set `state.step02.<field>` ← `<fallback expression>`, then advance to Étape 03.

## Verification (final)

Before returning control:
- check all TodoWrite todos are done
- check `state.json` contains all fields declared by the flow's GIVES contract
- print a one-line summary of the run
```

## IR → artefact mapping

| IR element | Emitted artefact |
|---|---|
| `FlowGraph` | `SKILL.md` (frontmatter + body), `process_flow.dot`, `README.md`, `state.example.json` |
| `StepIR` MODE=`exact`, lang=`python` | `scripts/NN_<name>.py` (stdin/stdout JSON, validates own output against the contract) |
| `StepIR` MODE=`exact`, lang=`bash` | `scripts/NN_<name>.sh` |
| `StepIR` MODE=`exact`, lang=other | **compile error**: "claude-skill v1 supports python and bash for exact steps; got `<lang>` at line L" |
| `StepIR` MODE=`judgment` | section in `SKILL.md` + `prompts/NN_<name>.md` + `schemas/NN_<name>.output.json` |
| `ContractIR` (step input/output) | `schemas/NN_<step>.<input|output>.json` (JSON Schema derived from Pydantic) |
| `IfIR` / `MatchIR` | narrative section in `SKILL.md` + ordinally-prefixed sub-steps emitted at top level |
| `WhileIR` / `ForEachIR` | narrative section + explicit "create a sub-todo per iteration" instruction |
| `RescueBlockIR` (v0.13) | section "RESCUE handlers" in `SKILL.md` |
| `ErrorAccessIR` / `ResumeIR` (v0.13) | rendered as in-prose instructions (`"Available: step.error.message …"`, `"RESUME state.step.<field> ← <value>"`) |
| `CacheConfig` on a step | "Cache" (FR: "Mise en cache") sub-block: cache-key generated by `python scripts/_cache_key.py state.json '<step_name>' '<key_fields_json>'`, path `.cache/NN_<name>.json`, "check before executing" instruction |
| `RetryConfig` on a step | "Retry" sub-block: budget + backoff in textual instructions |
| `Resources` on the flow | "Resources" annex + injection into frontmatter `allowed-tools` |

## Runtime data flow (LLM host driving the emitted skill)

1. **Bootstrap**: read `state.example.json`, copy to `state.json`, create one TodoWrite todo per top-level step in the process flow.
2. **Exact step**: run the Bash command exactly as written. The script reads `state.json` on stdin, writes the next state on stdout, atomic swap. The script validates its own output against `schemas/NN_*.output.json` before emitting — determinism preserved at the script level.
3. **Judgment step**: read the prompt template, substitute `{{state.x}}` placeholders, generate the output, save verbatim to `out.json`, validate with `python scripts/_validate.py out.json schemas/NN_*.output.json` (bundled helper, no PyPI dep), merge into `state.json` under the step's namespace.
4. **Control structure**: for `FOR EACH` / `WHILE`, create sub-todos and iterate. For `IF` / `MATCH`, evaluate the condition (inline) and follow the relevant branch — sub-steps are already separate sections in `SKILL.md`.
5. **RESCUE**: on exit ≠ 0 or contract-validation failure, jump to the corresponding RESCUE section, execute the handler chain, apply RESUME if present (mutate `state.json` accordingly), resume.
6. **Termination**: verify all todos done + flow GIVES fields present in `state.json` + emit one-line summary.

## Error handling

### Compile-time (emitter)

| Case | Behaviour |
|---|---|
| FLOW lacks `description` | Warning. Default `description: "Execute flow <name>"`. Auto-trigger weakened — documented. |
| Judgment STEP without output contract | **Error** (already rejected by the IR; we don't relax). |
| Exact STEP in unsupported language (rust, go, …) | **Error** with source line: "claude-skill v1 supports python and bash". |
| Parallel construct in source | Warning. Serialized in topological order. |
| WHILE without provably-terminating condition | Warning. Suggest explicit budget. |
| Cycle in graph (outside WHILE/FOR EACH) | **Error** (already rejected by the IR). |

All messages include the source line from the `.clio` file (project convention).

### Runtime (LLM host)

1. **Exact step exit ≠ 0** — if the step has a `RescueBlockIR`, render the RESCUE section in `SKILL.md` so the LLM jumps there; apply RESUME or terminate. If no rescue, the SKILL ends with a generic "unrecovered failure" section: stop, dump `state.json`, report `stderr`.
2. **Judgment contract validation fails** — if RETRY: in-section instruction to regenerate with a feedback string, with budget N. If RESCUE: handler path. Otherwise: hard stop.
3. **LLM host drift** (skipping a step, forgetting to validate) — mitigated by the TodoWrite checklist mandate + per-section "tick + don't advance" instruction + final verification section. Not guaranteed; documented honestly in the emitted `README.md`.

### Non-objectives v1

- No rollback of `state.json` after partial-failure inside a loop (manual debug from the residual state).
- No automatic restart from a stored checkpoint (the user re-runs the skill from scratch — same constraint as `claude-cli` v1).

## Tests

### Layer 1 — Emission (heavy coverage, priority high)
`tests/test_emitters/test_claude_skill.py`, mirroring `test_claude_cli.py`. For each `.clio` fixture in `tests/fixtures/`:

- `SKILL.md` exists, frontmatter parsable as YAML, `name`/`description` non-empty.
- `scripts/` has exactly N files for N exact STEPs.
- `schemas/` has input + output JSON Schemas for each contracted step.
- `prompts/` has one file per judgment STEP.
- `process_flow.dot` is parsable (minimal validation, no `pydot` dependency unless already present).
- Each STEP appears as a `## Étape NN — <name>` section.
- For each `RescueBlockIR`: a "If Étape X fails" section exists, mentions `<step>.error.message` and any `RESUME` target.
- For each control structure: corresponding section exists in expected order.

### Layer 2 — Exact-script execution (medium)
For fixtures with trivial exact STEPs, actually run `python scripts/NN_<name>.py < state.example.json` and assert the output validates against the step's output schema. This guarantees emitted scripts are genuinely autonomous (no hidden dependency).

### Layer 3 — LLM-host fidelity (manual, v1)
No automated test. Instead, document a manual smoke procedure in `docs/manual/troubleshooting.md` (new entry "Test a claude-skill output by installing it"): copy the output dir to `~/.claude/skills/<name>/`, invoke from a fresh session, verify expected commands are issued. Optionally golden-snapshot the `SKILL.md` for key fixtures (`tests/snapshots/<fixture>/SKILL.md`) so CI flags any unintended drift in generated markdown.

### v0.13 regression
A dedicated test verifies a `.clio` with RESCUE + `step.error.*` + RESUME emits:
- the RESCUE section in `SKILL.md`,
- the textual references to `step.error.message` / `step.error.type`,
- the RESUME instruction targeting the correct field with the fallback value.

## Out-of-scope v1

- skill → CLIO importer (reverse direction). Separate spec, requires a judgment classifier.
- Packaging the output directory into an Anthropic plugin-format `.zip`.
- Genuine parallel execution.
- Cross-language exact STEPs (rust, go, …) — error at compile time.
- Rollback / checkpoint restart.
- Per-LLM-host adaptation (the emitted skill is generic; non-Claude hosts may not have a `TodoWrite` tool, in which case the checklist becomes a plain markdown list).

## Roadmap

| Sprint | Scope |
|---|---|
| v0.14 (this spec) | `target: claude-skill` emitter — CLIO → skill, parity with v0.13 features. |
| v0.15 candidate | `clio import-skill` — skill → CLIO importer, with a judgment-driven classifier for the markdown body. Separate spec. |
| v0.16+ candidate | Anthropic plugin packaging; publish-helper subcommand; LLM-host adaptation matrix. |

## Open questions

None at this stage — all decisions resolved during brainstorm. Spec is ready for review.
