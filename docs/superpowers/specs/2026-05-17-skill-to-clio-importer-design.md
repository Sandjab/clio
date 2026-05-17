# CLIO — `clio import`: skill → `.clio` importer — design

**Date**: 2026-05-17
**Sprint**: v0.19 candidate
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

CLIO compiles a `.clio` source forward into runnable targets (`claude-cli`, `python`, `mcp-server`, `langgraph`, `claude-skill`). The reverse direction — recovering a `.clio` source from one of these emitted artifacts, or from a hand-written Claude Code skill — has never existed. The v0.14 `target: claude-skill` design doc explicitly deferred it: *"The reverse direction (skill → CLIO importer) is out of scope for this spec — see Roadmap."*

The roadmap moment has arrived. Three concrete use cases motivate the feature:

1. **Refactor existing skills via the CLIO toolchain.** The Claude Code ecosystem contains hundreds of hand-written skills (`superpowers:*`, `impeccable:*`, `feature-dev:*`, plugin skills, in-house skills). Each is a markdown blob whose structure is implicit. Importing one as a typed `.clio` source lets the user iterate on it with the full compiler discipline (CONTRACTs, control flow, RESCUE, CACHE) and re-emit a higher-quality skill.
2. **Recovery and audit.** A user who loses the `.clio` source of a CLIO-emitted skill should be able to recover it from the skill folder. A user auditing whether a skill has been modified post-emission needs the same machinery.
3. **Ecosystem readability.** Browsing a skill as `.clio` reveals its structural skeleton (steps, contracts, flow) in one screen, where the markdown SKILL.md scatters the same information across many sections.

This spec introduces `clio import <skill-dir>` and the supporting modules.

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| Scope | Both CLIO-emitted skills and arbitrary hand-written skills, dispatched automatically | (a) CLIO-emitted only — leaves the ecosystem use case unaddressed; (b) Hand-written only — misses the round-trip use case |
| Round-trip mechanism | Emit-side sidecar: a `.clio/` directory under the output containing `source.clio` (verbatim copy of the source) and `manifest.json` (hashes of every emitted file). Import-side: if `.clio/source.clio` exists AND file hashes match, return it directly. No reverse-engineering algorithm. | (a) Structural fingerprint (no marker, infer from naming) — fragile to user modifications, requires reconstructing the source from output, defeats the "deterministic round-trip" claim; (b) Reverse-engineering algorithm — collapses to "just store the source" once analyzed |
| Hand-written import | Single LLM call with retry-on-validation-error, mirroring the architecture of `nl_to_clio.py` (the existing NL → `.clio` generator) | (a) Deterministic pipeline + LLM only on FLOW — works for CLIO-emitted skills (already covered by the marker), brings little to hand-written skills (no structure to extract); (b) Multi-stage chain-of-thought — 4-5× more tokens and latency, surface for inter-stage incoherence, YAGNI for skills with < 20 steps |
| LLM provider | Anthropic SDK only, same as `nl_to_clio.py`. Multi-provider abstraction deferred to v0.20+ | Drop-in `litellm` / OpenAI-compat now — out of scope, deserves its own design |
| Language policy | (1) All compiler-side content (system prompts, error messages, CLI help, code comments, docs) is English. (2) The `.clio` output of an import respects the source skill's user-facing language for prompts, descriptions, and strategies. (3) Compiler-side annotations injected into the output (`# CLIO-import: ...`) remain English. | (a) Hardcode all output English — defeats the point of importing non-EN skills; (b) Hardcode binary FR/EN like the existing emit-side `detect_skill_language` — same bias the user explicitly rejected |
| Language detection | LLM-in-prompt: the system prompt instructs the model to detect the source's user-facing language and produce all user-facing output in that language. No external library | (a) `langdetect` / `lingua` / `fasttext-langid` library — extra dependency, risk of detection / production mismatch since the LLM ultimately produces the output; (b) Hybrid pre-detection + LLM — over-engineered for v0.19 |
| Output fidelity | Moderate fidelity with explicit annotations. Always extract: STEPs, CONTRACTs, FLOW (linear + control flow), TAKES/GIVES. Best-effort: DESCRIPTION, ON_FAIL. Skip: CACHE, VALIDATE, STRATEGIES, RESCUE (rarely recoverable from skill content, sometimes not represented at all). The LLM is required to add `# CLIO-import: ...` annotations marking origin or omission. | (a) Bit-identical round-trip on arbitrary skills — impossible by construction; (b) Skeletal output (steps + flow only, no contracts) — too lossy to be useful for the refactor use case |
| Prompt storage | New directory `clio/prompts/` with one `.md` per prompt, loaded via a small `__init__.py` helper. `nl_to_clio.py` is also refactored to load its prompt from this directory (incident refactor, keeps two modules consistent). | (a) Status quo (inline Python constants) — does not scale beyond 2 modules, mixing prose and Python is awkward; (b) Hybrid `clio/prompts/<module>/<fragment>.md` with composition — premature for 2 modules, defer to v0.21+ if real fragment sharing materializes |
| Emit-side label localization | Out of scope. The existing `detect_skill_language` keeps its binary FR/EN behavior in v0.19. The same bias surfaces (Spanish skill → English labels in SKILL.md) but the impact is purely cosmetic (section headings). A separate v0.20+ effort generalizes label localization with a `labels.<lang>.toml` table per supported language. | Bundle the emit-side fix into v0.19 — drags ~25 hardcoded sites into the refactor, requires localized label tables, distracts from the import core |

## Architecture

### High-level flow

```
EMIT  (modification of clio/emitters/claude_skill.py, +30 LOC)
─────────────────────────────────────────────────────────────
  .clio source --parse+IR--> emit --> skill/
                                       ├── SKILL.md
                                       ├── scripts/, prompts/, schemas/
                                       ├── process_flow.dot
                                       ├── state.example.json
                                       ├── README.md
                                       └── .clio/                   <-- NEW
                                            ├── source.clio         <-- verbatim copy
                                            └── manifest.json       <-- version + hashes

IMPORT  (new sub-command `clio import`, ~200 LOC)
─────────────────────────────────────────────────────────────
  skill/  --> detect .clio/  --> hash check
                  │                 │
          absent  │                 │ drift detected
                  │                 │       ┌---------------------┐
                  ▼                 ▼       ▼                     │
            Path B (LLM)      return source.clio                  │
                  │                                               │
                  ▼                                               │
            build LLM context (all skill text files,              │
            EXCLUDING .clio/, _validate.py, _cache_key.py)        │
                  │                                               │
                  ▼                                               │
            Anthropic SDK call (system + user)                    │
                  │                                               │
                  ▼                                               │
            parse + build_ir (validation)                         │
                  │                                               │
          ┌──ok───┴───ko───┐                                      │
          ▼                ▼                                      │
       return .clio    1 retry with error feedback ───────────────┘
                          │
                          ▼ ok? return .clio  /  ko? GenerationError
```

### New files

```
clio/
  prompts/
    __init__.py                       # NEW: load_prompt(name) helper, ~15 LOC
    nl_to_clio_system.md              # NEW: extracted from nl_to_clio.py constants
    nl_to_clio_retry.md               # NEW: extracted from nl_to_clio.py retry message
    skill_to_clio_system.md           # NEW: role + grammar + mapping + annotations + language policy
    skill_to_clio_retry.md            # NEW: same retry pattern as nl_to_clio
  skill_to_clio.py                    # NEW: ~150 LOC, mirror of nl_to_clio.py
  cli.py                              # MODIFIED: new sub-command `import`
  emitters/
    claude_skill.py                   # MODIFIED: write .clio/ sidecar after main emission
  nl_to_clio.py                       # MODIFIED: load prompt from clio/prompts/ (incident refactor)

tests/
  test_skill_to_clio.py               # NEW: ~18 mock-based unit tests
  test_cli.py                         # MODIFIED: +7 tests for `clio import`
  test_emitters/test_claude_skill.py  # MODIFIED: +5 tests for .clio/ sidecar
  fixtures/skills_for_import/         # NEW: 5-7 fixture skills (CLIO-emitted + hand-written EN/FR/other)
```

### CLI surface

```
clio import <skill-dir>                       # auto-dispatch, write to stdout
clio import <skill-dir> --output flow.clio    # write to file
clio import <skill-dir> --model <model>       # override model (default claude-sonnet-4-6)
clio import <skill-dir> --mode auto           # explicit default (= no flag)
clio import <skill-dir> --mode strict         # require .clio/ marker AND hash match, else exit 2
clio import <skill-dir> --mode infer          # ignore .clio/ marker, force LLM-assisted
```

### Dispatch table

| Skill state                          | `auto` (default)              | `--mode strict`        | `--mode infer` |
|--------------------------------------|-------------------------------|------------------------|----------------|
| `.clio/` present, hashes match       | Return `source.clio` (no LLM) | Return `source.clio`   | LLM call       |
| `.clio/` present, hashes drift       | Warning + LLM fallback        | Exit 2 (fail loud)     | LLM call       |
| `.clio/` absent                      | LLM call                      | Exit 2 (fail loud)     | LLM call       |
| Skill directory missing              | Exit 2                        | Exit 2                 | Exit 2         |

### Exit codes

- `0`: success regardless of the path taken
- `1`: LLM-assisted import failed after retry budget (`GenerationError`)
- `2`: argument error, missing directory, or strict-mode failure

These are aligned with `clio gen`'s existing exit-code conventions.

## Detailed design

### Emit-side sidecar (`.clio/`)

#### Contents

```
skill_dir/.clio/
  source.clio       # verbatim copy of the source .clio file
  manifest.json     # CLIO version, emission timestamp, source hash, per-file hashes
```

#### `manifest.json` format

```json
{
  "clio_version": "0.19.0",
  "emitted_at": "2026-05-17T12:34:56Z",
  "source_hash": "sha256:7a3f...",
  "file_hashes": {
    "SKILL.md": "sha256:9c2e...",
    "scripts/01_detect_churn.py": "sha256:4b1a...",
    "scripts/_validate.py": "sha256:8d7c...",
    "prompts/02_explain.md": "sha256:6f9e...",
    "schemas/01_detect_churn.input.json": "sha256:1e5b...",
    "process_flow.dot": "sha256:3c8d...",
    "state.example.json": "sha256:5a2f...",
    "README.md": "sha256:9b4c..."
  }
}
```

Rules:
- `source_hash` is SHA-256 of the source `.clio` bytes. Enables audit "did this source produce this skill?" without the source file at hand.
- `file_hashes` is SHA-256 per emitted file. The `.clio/` directory is excluded from hashing (otherwise the manifest would need to hash itself).
- `emitted_at` is informational, used in import warnings (`"skill was emitted on 2026-05-17 ..."`). Excluded from any hash so the manifest is reproducible across emissions of the same source.
- `clio_version` enables forward-compatibility in v0.20+ if the manifest format changes.

#### Emitter modification

A new private helper `_write_clio_sidecar(graph, source_path, output_dir)` is called at the end of `ClaudeSkillEmitter.emit()`. The source path is plumbed through the emitter interface as a new keyword-only parameter:

```python
class ClaudeSkillEmitter(BaseEmitter):
    def emit(self, graph: FlowGraph, output_dir: Path, *, source_path: Path | None = None) -> None:
        ...  # existing logic unchanged
        if source_path is not None:
            _write_clio_sidecar(graph, source_path, output_dir)
```

The `source_path` is optional to preserve backward compatibility with programmatic usages of the emitter (tests, scripts) that pass only the graph. The CLI always passes the source path. Other emitters ignore the parameter (they accept `**kwargs` or are extended to accept `source_path: Path | None = None` and ignore it).

#### Failure modes

- **Source path inaccessible** (moved/deleted between parse and emit): warning printed to stderr, sidecar skipped, main skill emission completes normally.
- **Filesystem write failure on `.clio/`**: warning printed, main skill emission unaffected.

In both cases, the user falls back to the LLM-assisted import path at `clio import` time.

### Import-side dispatcher

The `cmd_import` function in `cli.py` implements the table above. Pseudo-code:

```python
def cmd_import(args) -> int:
    skill_dir = Path(args.skill_dir)
    if not skill_dir.is_dir():
        print(f"clio import: {skill_dir} is not a directory", file=sys.stderr)
        return 2

    mode = args.mode  # "auto" | "strict" | "infer"
    source_file = skill_dir / ".clio" / "source.clio"
    manifest_file = skill_dir / ".clio" / "manifest.json"

    if mode == "strict":
        if not source_file.exists():
            print(f"clio import: --mode strict but {source_file} missing", file=sys.stderr)
            return 2
        drift = _check_drift(skill_dir, manifest_file)
        if drift:
            print(f"clio import: --mode strict and skill drifted ({len(drift)} files changed)", file=sys.stderr)
            for path in drift[:5]:
                print(f"  - {path}", file=sys.stderr)
            return 2
        return _emit_source(source_file, args.output)

    if mode == "infer":
        return _import_via_llm(skill_dir, args.model, args.output)

    # mode == "auto"
    if source_file.exists():
        drift = _check_drift(skill_dir, manifest_file)
        if drift is None:
            return _emit_source(source_file, args.output)
        emitted_at = _read_emitted_at(manifest_file)
        print(
            f"clio import: skill has been modified since CLIO emitted it on {emitted_at}.",
            f"{len(drift)} files changed:",
            file=sys.stderr,
        )
        for path in drift[:5]:
            print(f"  - {path}", file=sys.stderr)
        if len(drift) > 5:
            print(f"  ... and {len(drift) - 5} more", file=sys.stderr)
        print("Falling back to LLM-assisted import.", file=sys.stderr)

    return _import_via_llm(skill_dir, args.model, args.output)
```

`_check_drift` recomputes SHA-256 of every file in the skill (excluding `.clio/`) and compares to `manifest.json`. Returns `None` if all match, otherwise a sorted list of paths that drifted (added, removed, or modified).

### LLM-assisted import (`skill_to_clio.py`)

Mirror of `nl_to_clio.py`. Same `GenerationError` shape, same `_validate` (parse + build_ir), same `_strip_markdown_fences`, same retry budget (1 retry).

#### Context gathering

```python
def _gather_skill_files(skill_dir: Path) -> str:
    """Walk skill_dir, concatenate readable text files with === <relpath> ===
    delimiters. Excludes .clio/ (anti-cheating), _validate.py, _cache_key.py
    (CLIO boilerplate), and binary files."""
    parts = []
    excluded_basenames = {"_validate.py", "_cache_key.py"}
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        if ".clio" in path.parts:
            continue
        if path.name in excluded_basenames:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(skill_dir).as_posix()
        parts.append(f"=== {rel} ===\n{content}\n")
    return "\n".join(parts)
```

#### Size limits

The payload is approximated as `len(payload) // 4` tokens. The thresholds:
- `> 100_000` tokens: warning to stderr, proceed.
- `> 180_000` tokens: abort before the SDK call. Message recommends `--mode strict` (if the user knows the skill is CLIO-emitted) or manual decomposition.

These give ~20k tokens of headroom for the response (Sonnet 4.6 default 8k output, 200k context).

#### `generate` entry point

```python
def generate(skill_dir: Path, *, model: str = "claude-sonnet-4-6") -> str:
    from anthropic import Anthropic  # lazy: requires `pip install -e .[gen]`

    payload = _gather_skill_files(skill_dir)
    _check_size(payload)  # warns or raises

    client = Anthropic()
    system_prompt = load_prompt("skill_to_clio_system")
    user_msg_initial = (
        "The following files compose a Claude Code skill. "
        "Produce the .clio source that would emit this skill. "
        "Follow the language policy and annotation rules from the system prompt.\n\n"
        + payload
    )

    last_attempt = ""
    last_error = ""
    user_msg = user_msg_initial
    for attempt in range(2):
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        candidate = _strip_markdown_fences(resp.content[0].text)
        err = _validate(candidate)
        if err is None:
            return candidate
        last_attempt = candidate
        last_error = err
        retry_template = load_prompt("skill_to_clio_retry")
        user_msg = retry_template.format(last_attempt=last_attempt, last_error=last_error)

    raise GenerationError(last_attempt, last_error)
```

#### `process_flow.dot` is gold

For CLIO-emitted skills that drifted (and therefore fell through to the LLM path), `process_flow.dot` is the canonical flow representation. The system prompt explicitly tells the LLM: *"if a process_flow.dot file is present in the payload, treat it as the authoritative source for FLOW structure (including IF / MATCH / WHILE / FOR EACH blocks). Use SKILL.md narration only for naming and DESCRIPTION inference."* For hand-written skills, `process_flow.dot` is absent and the LLM falls back to SKILL.md narration alone.

### Prompt file structure

```
clio/prompts/
  __init__.py
  nl_to_clio_system.md
  nl_to_clio_retry.md
  skill_to_clio_system.md
  skill_to_clio_retry.md
```

`__init__.py`:

```python
"""Prompt loader. Prompts live as markdown files alongside this module so
they can be edited and reviewed independently of Python code."""
from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> str:
    """Load a prompt by name (without .md extension)."""
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
```

Packaging: add to `pyproject.toml`:

```toml
[tool.setuptools.package-data]
clio = ["prompts/*.md"]
```

Incidental refactor of `nl_to_clio.py`:

- Extract the existing `_ROLE_INTRO + _OUTPUT_RULES + ...` constants into `clio/prompts/nl_to_clio_system.md` byte-for-byte.
- Extract the retry user message template into `clio/prompts/nl_to_clio_retry.md`.
- Replace the module-level constants with `_SYSTEM_PROMPT = load_prompt("nl_to_clio_system")` and the retry template loading.

This refactor is part of v0.19 (not a separate PR). It ensures both modules use the same prompt-loading convention from day 1.

### `skill_to_clio_system.md` outline

Sections (final text written during implementation):

1. **Role and output format**: "You are CLIO's skill importer. Produce raw `.clio` source. No markdown fences. No commentary."
2. **CLIO grammar reference**: concise excerpt of `LANGUAGE_SPEC.md` covering STEP, FLOW, CONTRACT, MODE, TAKES, GIVES, IMPORT, EXPOSE, control flow (IF, MATCH, WHILE, FOR EACH).
3. **Mapping rules** (skill structure to CLIO):
   - `scripts/NN_<name>.py` → `STEP <name> MODE: exact LANG: python`
   - `scripts/NN_<name>.sh` → `STEP <name> MODE: exact LANG: bash`
   - `prompts/NN_<name>.md` → `STEP <name> MODE: judgment` (prompt content becomes step body)
   - `schemas/<step>.input.json` / `<step>.output.json` → TAKES / GIVES (deterministic JSON Schema → CLIO type mapping)
   - `scripts/_validate.py` and `scripts/_cache_key.py` → CLIO boilerplate, **ignore** (do not map to STEPs)
   - `scripts/sub_<name>.py` → secondary FLOW called from the main FLOW
   - `process_flow.dot` → if present, **authoritative source for FLOW structure**
   - `SKILL.md` → user-facing narration, source for inferring FLOW when `process_flow.dot` is absent, and source for DESCRIPTION / ON_FAIL best-effort
4. **Annotation rules**: produce `# CLIO-import: ...` comments for every inferred element. Templates:
   - `# CLIO-import: extracted from schemas/<file>.json` (deterministic)
   - `# CLIO-import: inferred from SKILL.md narration` (LLM-inferred FLOW or TAKES/GIVES)
   - `# CLIO-import: best-effort from prompts/<file>.md` (DESCRIPTION, ON_FAIL)
   - `# CLIO-import: TODO — could not determine from skill (original .clio may have had CACHE/VALIDATE/STRATEGIES/RESCUE)`
5. **Output language policy**: as discussed — keywords English, annotations English, user-facing content (DESCRIPTION, prompts, STRATEGIES) in the source skill's user-facing language. Language priority for detection: (a) `prompts/` files if present (LLM-facing content, most authoritative for the skill's working language); (b) SKILL.md narrative if `prompts/` is absent or empty (typical for all-exact skills with no judgment steps); (c) fall back to English if no user-facing content is detectable. The LLM detects the language itself in-prompt.

### Out of scope (deferred or never)

**Deferred to v0.20+:**
- Multi-provider LLM abstraction (Anthropic / OpenAI-compat / LiteLLM). Shared benefit with `clio gen`.
- Emit-side label localization (`labels.<lang>.toml` per supported language; replace the binary FR/EN switch in `_claude_skill_helpers.py`).
- Multi-stage chain-of-thought import for very large skills (> 100k tokens).
- Recovery of CACHE / VALIDATE / STRATEGIES / RESCUE — requires finer heuristics, separate scope.
- Multi-file import: a CLIO-emitted skill whose source used IMPORT / EXPOSE (v0.18) produces a single skill directory; v0.19 imports it back as a single `.clio` file with all flows inline. Multi-file output requires inferring file boundaries from the skill, which has no signal.

**Never:**
- Bit-identical round-trip on hand-written skills — impossible by construction.
- Preserving original `.clio` source comments and formatting on the LLM path — no signal in the skill.

## Issues to open before merge

- **i18n of emit-side labels.** Replace the binary FR/EN logic in `detect_skill_language` and the ~25 label switches in `_claude_skill_helpers.py` with a multilingual scheme (`labels.<lang>.toml`). Documented as deferred from v0.19.
- **Multi-provider LLM abstraction.** Shared by `gen` and `import`. Either a thin `LLMProvider` interface (lowest dep weight) or adopt `litellm`. Designed separately.
- **Recovery of CACHE / VALIDATE / RESCUE on import.** Heuristic extraction from SKILL.md prose; opt-in flag (`--recover-resilience`); document expected fragility.

## Tests

### Unit (~30 new tests, all mock-based)

**`tests/test_skill_to_clio.py` (~18 tests)** — mirrors `test_nl_to_clio.py`:
- Validation passes on first attempt → returns `.clio` directly.
- Validation fails on first attempt → retry with error feedback → succeeds.
- Validation fails after retry → raises `GenerationError` with `last_attempt` and `last_error`.
- Markdown fence stripping.
- Excludes `.clio/` from the payload sent to the LLM (anti-cheating).
- Excludes `_validate.py` and `_cache_key.py` (CLIO boilerplate).
- Skips binary files.
- Warns when payload exceeds 100k tokens.
- Aborts when payload exceeds 180k tokens.
- Bilingual: French skill fixture + mock LLM response in French → output contains French prompts.
- Multilingual: Spanish skill fixture + mock LLM response in Spanish → output contains Spanish prompts.
- System prompt sent to LLM is loaded from `clio/prompts/skill_to_clio_system.md` (verifies the loader works and the file content is unchanged).

**`tests/test_cli.py` (+7 tests)** for `clio import`:
- No `.clio/` → calls `skill_to_clio.generate`.
- `.clio/` present, hashes OK → returns `source.clio` (no LLM call mocked).
- `.clio/` present, drift detected → warning to stderr, then calls LLM.
- `--mode strict` + drift → exit 2.
- `--mode infer` + `.clio/` present → calls LLM (ignores marker).
- `--output flow.clio` writes to the file, stdout silent.
- Missing directory → exit 2.

**`tests/test_emitters/test_claude_skill.py` (+5 tests)**:
- Emission produces `.clio/source.clio` byte-identical to the input source.
- Emission produces `.clio/manifest.json` with required keys (`clio_version`, `emitted_at`, `source_hash`, `file_hashes`).
- Hashes in `manifest.json` match the actual content of each emitted file.
- Manifest is reproducible across two emissions of the same source (modulo `emitted_at`).
- No `source_path` parameter passed → no sidecar emitted (backward compatibility).

### Opt-in E2E (`@pytest.mark.e2e_llm`)

3-5 cases gated behind `pytest -m e2e_llm`, requiring `ANTHROPIC_API_KEY`:
- Import a CLIO-emitted skill with `.clio/` → trivial copy succeeds.
- Import a CLIO-emitted skill with simulated drift → LLM fallback produces valid `.clio`.
- Import a hand-written English skill → output is sensible.
- Import a hand-written French skill → output contains French content where appropriate.
- Import a hand-written skill in a third language (e.g., Spanish) → output contains Spanish content.

### Fixtures

`tests/fixtures/skills_for_import/`:
- `clio_emitted_simple/` — small skill produced by running the emitter on a known source.
- `clio_emitted_drifted/` — same as above with one file modified.
- `handwritten_en_pipeline/` — a hand-written skill with EN prompts and a clear flow.
- `handwritten_fr_pipeline/` — same in French.
- `handwritten_es_pipeline/` — same in Spanish (for multilingual validation).

## Risks and mitigations

- **LLM context exhaustion on large skills.** Mitigated by size warning + abort, and by the multi-stage architecture deferred to v0.20+ as an explicit follow-up.
- **Output language detection drift.** The LLM might pick the wrong language on a mixed-language skill. Mitigated by the explicit "prioritize prompts/ language" instruction in the system prompt. Documented limitation.
- **Hash sensitivity to line endings.** Hashes are computed on raw bytes (no normalization). A user editing the skill on Windows then re-importing on Unix could see drift from line-ending conversion. Documented; fix is out of scope.
- **Annotation pollution.** A `.clio` covered in `# CLIO-import: ...` comments is ugly. The user is expected to delete the annotations after manual review. The annotations are a feature for first-pass auditing, not for long-term retention.

## Definition of done

- [ ] `clio import <dir>` works for CLIO-emitted skills (Path A) without any LLM call.
- [ ] `clio import <dir>` works for hand-written skills (Path B) via Anthropic SDK.
- [ ] `--mode strict` and `--mode infer` flags work as specified.
- [ ] Drift detection produces clear stderr warnings with file lists.
- [ ] `clio/prompts/` directory exists with the 4 `.md` files; `nl_to_clio.py` is refactored to load from it.
- [ ] All new tests pass; existing tests still pass (no regression on emit side).
- [ ] `docs/manual/03-cookbook.md` and/or `docs/manual/05-cli-reference.md` document `clio import`.
- [ ] `LANGUAGE_SPEC.md` mentions the `.clio/` sidecar convention.
- [ ] CHANGELOG entry written (under `[Unreleased]`).
- [ ] Three follow-up issues opened (i18n emit, multi-provider, recovery of resilience fields).
