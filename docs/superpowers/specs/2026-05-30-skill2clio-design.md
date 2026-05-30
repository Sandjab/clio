# Spec — skill `/skill2clio`

**Date:** 2026-05-30
**Status:** approved (brainstorm), pending implementation plan
**Author:** Jean-Paul Gavini (with Claude)

## 1. Intent

A Claude Code **skill**, versioned in the CLIO repo at `.claude/skills/skill2clio/`,
that converts a Claude Code skill directory back into a `.clio` source **using the
running Claude Code session as the model** — no Anthropic API key, no `pip install -e .[gen]`.

It is the **third path** for skill → `.clio`, alongside the two that `clio import` already ships:

| Path | Mechanism | Key required | Where it lives |
|---|---|---|---|
| Sidecar / verbatim | Deterministic read of `.clio/sources/` | No | `clio import --mode strict` |
| LLM (API) | Single Anthropic SDK call, 1-retry budget | **Yes** | `clio/skill_to_clio.py` |
| **Reasoned (this skill)** | CC session as the model, **unbounded `clio check` loop** | **No** | `.claude/skills/skill2clio/` |

### Why it earns its place

`testprojects/skill2clio/math-olympiad/import.stderr` is direct evidence: on a complex
skill, the API path (Sonnet, **1 retry**) emitted ~122 lines of **invented, wrong grammar**
(`CONTRACT X { SHAPE: object {...} }`, `MATCH x { "v" -> }`, `length(...)`) and failed with
`unterminated string literal`, never recovering. The reasoned path runs `python -m clio check`
**in a loop with no retry ceiling**, so the same skill can be driven to a compiling `.clio`.
That loop is the core value-add.

## 2. Decisions (settled in brainstorm)

1. **Location** — `.claude/skills/skill2clio/` (versioned with the repo). DECIDED prior to brainstorm.
2. **Scope — reasoned path only.** The skill ALWAYS does the CC-as-model conversion. It does
   not implement the sidecar/verbatim path (already covered, key-free, by `clio import --mode strict`).
3. **Reuse — reference + override.** SKILL.md instructs Claude to **read**
   `clio/prompts/skill_to_clio_system.md` at runtime for the durable knowledge (grammar reference,
   mapping table, annotation rules, language policy) — single source of truth that tracks spec
   evolution automatically. SKILL.md **owns and overrides** the orchestration/output behavior,
   explicitly neutralizing the prompt's API-shaped framing (`Output ONLY raw .clio, no prose`,
   `respond with a single line ERROR:`).
4. **Sidecar courtesy line — yes.** If a `.clio/` sidecar is detected, emit one informational line
   pointing to `clio import --mode strict` for a free deterministic recovery — **without** switching
   to it (scope stays reasoned-only). The `.clio/` directory itself is NOT read (anti-cheat, consistent
   with `_gather_skill_files` excluding hidden dirs).

## 3. Structure

```
.claude/skills/skill2clio/
  SKILL.md          # frontmatter + workflow + framing override
```

Single file. No `references/` subfolder — the durable knowledge is read at runtime from
`clio/prompts/skill_to_clio_system.md`.

**Frontmatter `description`** follows the repo's skill convention (explicit mandatory triggers,
French, *USER-INVOCABLE ONLY*) — same shape as `insight-repo` and `repo-judge`. Mandatory triggers
include: `/skill2clio`, `skill2clio`, and French phrasings for "convert/import/recover this skill to
.clio without an API key". Must NOT trigger on vague paraphrases.

## 4. Workflow the SKILL.md drives

```
1. Gather  → Read ALL text files recursively (incl. references/, examples/, evals/,
             templates/, README.md) — mirrors _gather_skill_files (rglob the whole tree).
             process_flow.dot (anywhere) is authoritative for FLOW. Ignore: hidden dirs
             (.clio/, .git/), _validate.py, _cache_key.py, binaries. (.clio/ anti-cheat respected)
2. Map     → Read clio/prompts/skill_to_clio_system.md ; apply grammar + mapping table
             + annotation rules + language policy.
3. Draft   → Write <output>.clio.
4. Check   → Bash `python -m clio check <output>.clio`.
5. Loop    → on error: read message (line/col), fix, re-check.
             Brake (global Rule 4): after ~4 failed attempts on the same target, STOP →
             save best-effort + report the last error. Do not spin.
6. Report  → written path + summary: what was extracted vs annotated `# CLIO-import: TODO`
             (lossy fields: CACHE/VALIDATE/STRATEGIES/RESCUE/ON_FAIL).
```

**Framing override** is explicit in SKILL.md: the prompt is consulted for *knowledge*, but the
*behavior* is "write a file, run `clio check`, comment, iterate" — NOT "output only raw .clio".

## 5. Interface & output

- Invocation: `/skill2clio <skill-dir> [output.clio]`
- **Output default:** `<basename-of-skill-dir>.clio` in the cwd. Optional 2nd arg overrides.
- **Sidecar present:** courtesy line only (decision 4). `.clio/` is not read.

## 6. Reused vs new

| Element | Source |
|---|---|
| Grammar, mapping, annotations, language policy | **Reused:** `clio/prompts/skill_to_clio_system.md` (read at runtime) |
| Files-to-ignore logic | **Reused conceptually:** `_gather_skill_files` exclusion rules |
| In-loop validator | **Reused:** `python -m clio check` |
| Agentic workflow + framing override + reporting | **New:** SKILL.md |

## 7. Success criteria (manual verification — a CC skill is not pytest-testable)

Fixtures already in the repo:

- **Flagship demo:** `/skill2clio testprojects/skill2clio/inskill/math-olympiad` → `clio check`
  **passes** (the exact skill the API path failed on — direct proof of value).
- `inskill/agent-development` → result `clio check`-clean; compare against the known-good
  `testprojects/skill2clio/agent-development/agent-development.clio`.
- `inskill/hook-development` → `clio check` passes.
- `tests/fixtures/skills_for_import/handwritten_{fr,es,en}_pipeline` → language policy honored
  (FR prompts stay FR, ES stay ES, EN stay EN).

**Explicit assumption:** repo-local dev tool — always run from a CLIO checkout (the relative prompt
reference and `python -m clio` both require the repo).

## 8. Out of scope (YAGNI)

- Sidecar/verbatim recovery (use `clio import --mode strict`).
- Full parity with `clio import` modes (auto/strict/infer).
- A subagent-isolated variant (keep the conversion inline in the session).
- Any change to `clio/skill_to_clio.py`, the CLI, or the compiler core — this skill adds a file
  under `.claude/skills/` and nothing else.
