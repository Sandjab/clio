---
name: skill2clio
description: Convertit un répertoire de skill Claude Code en source .clio en utilisant la session Claude Code courante comme modèle — aucune clé API, contrairement à `clio import` (chemin LLM) qui exige ANTHROPIC_API_KEY. Boucle agentique gather → draft → `clio check` → fix, sans plafond de retry. USER-INVOCABLE ONLY. DÉCLENCHEURS OBLIGATOIRES : '/skill2clio', 'skill2clio', 'convertis ce skill en clio', 'importe ce skill en .clio', 'récupère ce skill en clio sans clé', 'reconstruis le .clio de ce skill', 'skill vers clio sans clé API'. Ne PAS déclencher sur : compilation .clio → cible (c'est `clio compile`), récupération verbatim d'un skill émis par clio avec sidecar (c'est `clio import --mode strict`), ou paraphrases vagues.
---

# skill2clio — reasoned skill → .clio importer (no API key)

Convert a Claude Code skill directory into a `.clio` source by reasoning, using
THIS session as the model. No Anthropic API key, no `clio[gen]` install. Unlike the
single-shot API path in `clio/skill_to_clio.py`, you have an **unbounded compile-correct
loop**: draft, run `python -m clio check`, read the error, fix, repeat.

## Arguments

`/skill2clio <skill-dir> [output.clio]`

- `<skill-dir>` — path to the skill directory (the one containing `SKILL.md`).
- `[output.clio]` — optional. Default: `<basename of skill-dir>.clio` in the current directory.

## Sidecar courtesy (do this first)

If `<skill-dir>/.clio/` exists (a CLIO-emitted sidecar), print exactly ONE line:

> Note: this skill has a `.clio/` sidecar — `clio import --mode strict <skill-dir>` would
> recover the original source verbatim, deterministically, with no key. Proceeding with a
> reasoned conversion instead (as requested).

Then continue with the reasoned conversion below. **Do NOT read the `.clio/` directory** — the
reasoned path must not cheat off the sidecar.

## Workflow

### 1. Gather
Read the skill's content files:
- `SKILL.md`
- everything under `scripts/`, `prompts/`, `schemas/`
- `process_flow.dot` if present (authoritative for FLOW structure)

Ignore: any hidden file/dir (`.clio/`, `.git/`, `.DS_Store`), `scripts/_validate.py`,
`scripts/_cache_key.py`, and binary files. (This mirrors `_gather_skill_files`.)

### 2. Load the mapping rules (single source of truth)
Read `clio/prompts/skill_to_clio_system.md`. Apply its:
- grammar reference,
- mapping table (skill file → CLIO construct),
- annotation rules (`# CLIO-import: ...`),
- output language policy (judgment-step prompt bodies stay in the source skill's language).

**Override that prompt's API-shaped framing.** It was written for a single API call and says
"Output ONLY raw .clio source. No prose." and "respond with a single line starting with
`ERROR:`". IGNORE those two instructions here. In this session you WILL write a file, run
`clio check`, narrate what you map, and iterate. Use the prompt for its KNOWLEDGE (grammar,
mapping, annotations, language), not its output protocol.

### 3. Draft
Write the `.clio` to the output path. Follow the few-shot style in the prompt exactly:
- `CONTRACT name` then 2-space-indented fields — NOT `CONTRACT X { SHAPE: object {...} }`.
- single-line `SHAPE: { ... }` (multi-line record types do not parse).
- `MODE: judgment` or `MODE: exact`; `len(...)` (never `length(...)`).
- `MATCH c.field:` with `CASE variant:` (bare enum variants, no quotes).

Annotate every non-obvious origin with `# CLIO-import: ...` and mark lossy fields
(CACHE / VALIDATE / STRATEGIES / RESCUE / ON_FAIL) as `# CLIO-import: TODO` rather than
inventing values.

### 4. Check
Run: `python -m clio check <output.clio>`

### 5. Loop
If `check` reports an error:
- Read the line/col and message.
- Fix the `.clio`. Common pitfalls (all detailed in the prompt): reserved keywords used as
  identifiers, multi-line `SHAPE`, `IF`/`MATCH` on a bare `str`/`int` param (must be a dotted
  CONTRACT field), step-arg type mismatches, `FLOW.GIVES` field-NAME mismatches, `CACHE`/
  `ON_FAIL` on an exact step (judgment-only), `LANG`/`impl` on a judgment step (exact-only).
- Re-run `check`.

Repeat until `check` passes. **Brake:** if 4 successive fixes do not reach a clean check, STOP.
Save the best-effort `.clio`, report the last error and which construct is fighting the
grammar. Do not loop indefinitely.

### 6. Report
On success, state:
- the output path,
- a one-line inventory: N contracts, N steps (judgment / exact split), the FLOW shape,
- which fields were annotated `# CLIO-import: TODO` (unrecoverable resilience fields).
