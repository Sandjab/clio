# CLIO v0.22 — Multi-file `IMPORT` sidecar recovery (issue #67) — design

**Date**: 2026-05-29
**Sprint**: v0.22 candidate
**Status**: Spec drafted, awaiting user review before writing the implementation plan.

## Motivation

v0.18 introduced cross-file `FROM "<path>" IMPORT <names>` (shared `CONTRACT`s and
`FLOW`s across files). v0.19 introduced the `target: claude-skill` `.clio/` sidecar:
when a skill is emitted, `.clio/source.clio` stores a **verbatim copy** of the source
and `.clio/manifest.json` stores SHA-256 hashes, so `clio import` can recover the
exact source deterministically (no LLM, no reverse-engineering).

These two features are silently incompatible. The sidecar writer copies **only the
single entry source file**. When the entry file imports siblings
(`FROM "../schemas.clio" IMPORT Article`), the recovered `source.clio` still contains
those `FROM …` lines, but the imported files are absent from the sidecar. Recompiling
the recovered source therefore fails:

```
clio.ir.resolver.CompileError: imported file not found: .../schemas.clio
```

(`clio/ir/resolver.py:51`). This breaks the verbatim round-trip promise v0.19 made,
specifically for the multi-file projects v0.18 enabled. The bug is real and observable
on `examples/multi_file/` (its `nlp/nlp.clio:1` does `FROM "../schemas.clio"`).

Issue #67 closes this gap: a cross-file project emitted to `claude-skill` must round-trip
through `clio import` and recompile cleanly, byte-identical to the original sources.

## Scope decision (why this is its own release)

v0.22 ships **#67 alone**. It was scoped against the Go-parity work (the other tracked
v0.22 candidate) and deliberately separated:

- **No shared code.** #67 touches the sidecar writer, the `claude-skill` emitter, the
  `emit()` signature, the `clio import` CLI path, and the IR import-resolution plumbing.
  The Go work touches `clio/emitters/_go_*.py` exclusively. Zero file overlap, zero
  IR-surface overlap. They share only the word "flow".
- **One theme per tag** (repo convention). Bundling them produces an oversized feature
  PR spanning two unrelated subsystems with two unrelated review surfaces.

Go external-call parity (REST + shell + sub-flow composition) is tracked for v0.23;
Go `sql` + `mcp_tool` for v0.24. Both are out of scope here.

## Decisions made during brainstorm

| Topic | Decision | Rejected alternatives |
|---|---|---|
| **Recovery path** | Sidecar (deterministic) path only. The LLM-recovery path (`skill_to_clio.py`, used when no sidecar is present) stays single-file and is **not touched**. | Teach the LLM path to emit multiple files best-effort — an LLM cannot reliably reconstruct file boundaries; widens scope into prompts + adds hallucination risk for no verbatim guarantee. |
| **Plumbing** | Approach A: a new keyword-only, defaulted `sources` parameter on `BaseEmitter.emit`, mirroring the existing `source_path` precedent. The CLI passes the resolved source set; `ClaudeSkillEmitter` consumes it; the other 5 emitters accept-and-ignore it exactly as they do `source_path`. | Approach C: carry source text on `FlowGraph` — pollutes a frozen, target-independent IR with verbatim bytes for a single emitter, and contradicts how `source_path` is already handled (side-channel, not IR). Approach B: re-parse/re-resolve inside the sidecar writer — the emitter would import the resolver, breaking emitter↔IR decoupling. |
| **Rooting of stored sources** | Root the stored tree at `os.path.commonpath(all resolved source paths)`; store each file at its path relative to that root; record the entry's relpath in the manifest. | Root at `entry.parent` — a `FROM "../x.clio"` import resolves **outside** `entry.parent`, so its relpath would escape the sidecar tree. Flatten all files into one dir — destroys `../` and nested offsets, breaking re-resolution on recompile. |
| **`.clio/sources/` contents** | Full tree: entry **plus** every imported file. `clio import` dumps the directory wholesale. | Imports-only (entry lives only as `source.clio`) — saves one duplicated file but forces import to special-case the entry's placement; the wholesale-dump path is simpler code (Rule 2 favors code simplicity over a few KB on disk). |
| **Entry duplication** | Accepted. The entry is stored twice: `.clio/source.clio` (unchanged, back-compat) **and** `.clio/sources/<entry-relpath>` (part of the full tree). | Drop `source.clio` for multi-file — breaks v0.19 / pre-#67 importers that read `.clio/source.clio`, and the issue's backward-compatibility requirement. |
| **Manifest extension** | Add two keys, emitted **only when imports are present** (≥2 resolved files): `sources` (`{relpath: "sha256:…"}`, full tree) and `entry` (entry relpath). Single-file output stays byte-identical to v0.21. | Always emit `sources`/`entry` (even single-file) — churns every existing single-file `claude-skill` golden for no benefit. |
| **Stored-source integrity (strict mode)** | A **new** `check_source_drift` comparing the `sources` map against the actual `.clio/sources/` tree. `check_drift` is unchanged. | Extend `check_drift` to cover `.clio/sources/` — `_iter_skill_files` deliberately excludes dotted components (the manifest cannot hash itself / the sidecar); stored sources are not emitted output and need their own, explicit check. |
| **`clio import` for multi-file** | Require `--output DIR`. Reconstruct the tree under `DIR`. If stdout (or a single-file `--output`) is requested for a multi-file skill, **fail loud** (non-zero exit, explanatory message). | Silently emit only the entry to stdout — reproduces the original bug (a non-recompilable single file) and violates the fail-loud rule. |
| **Recovery fidelity** | Byte-identical verbatim. Stored files are raw copies; the acceptance test asserts byte-equality of every recovered file against its original, and a clean recompile (rc 0, no `imported file not found`). | "Recompiles cleanly" only — weaker than the v0.19 verbatim promise the entry file already meets; verbatim is free (it's a copy). |
| **`E_GO_011` doc drift / stale "pending golden" note** | Out of scope. Tracked as a separate micro-issue. | Fold the doc-honesty fixes into #67 — unrelated subsystem (Go), violates surgical-changes. |

## Architecture

### Pipeline change

```
v0.21 (single-file sidecar):
  main.clio → resolve_imports() → dict[Path, Program] → build_ir() → FlowGraph
            → ClaudeSkillEmitter.emit(graph, out, source_path=main.clio)
            → write_sidecar(source_path=main.clio, …)
                 └─ .clio/source.clio  (entry only)   ← the gap

v0.22 (multi-file aware):
  main.clio → resolve_imports() → dict[Path, Program]   ← keys = every resolved file
            → build_ir() → FlowGraph
   CLI captures the resolved path set, passes it down:
            → ClaudeSkillEmitter.emit(graph, out, source_path=main.clio,
                                      sources=(main.clio, schemas.clio, nlp/nlp.clio))
            → write_sidecar(source_path=main.clio, …, sources=<set>)
                 ├─ .clio/source.clio          (entry, verbatim — unchanged)
                 ├─ .clio/sources/<tree>        (full tree, verbatim — NEW)
                 └─ .clio/manifest.json         (+ sources, + entry — NEW keys)
```

The import resolver already opens and parses every imported file (it must, to resolve
the graph). #67 surfaces that already-known set of paths to the CLI, which forwards it
to `emit()`. The exact mechanism by which the CLI obtains the set (a return value or a
small accessor on the resolution step) is a plan-level detail; the resolver's
`dict[Path, Program]` keys are its source of truth.

### Sidecar layout (multi-file)

For `examples/multi_file/` (`main.clio` imports `schemas.clio` and `nlp/nlp.clio`,
the latter importing `../schemas.clio`), `commonpath` resolves to the
`examples/multi_file/` directory:

```
skill-out/.clio/
  source.clio                 # entry, verbatim — UNCHANGED (back-compat)
  manifest.json               # + "sources", + "entry"
  sources/
    main.clio                 # entry, again (full tree)
    schemas.clio
    nlp/
      nlp.clio                # ../schemas.clio re-anchors correctly under the common root
```

Single-file projects emit **no** `sources/` directory and **no** new manifest keys —
output is byte-identical to v0.21.

### Manifest schema

Real v0.21 keys (from `clio/emitters/_sidecar.py:78-83`), with the two additions:

```jsonc
{
  "clio_version": "0.22.0",
  "emitted_at": "2026-05-29T12:34:56Z",
  "source_hash": "sha256:…",            // entry hash — UNCHANGED
  "file_hashes": { /* emitted skill files */ },   // UNCHANGED; excludes .clio/ (dotted)
  "entry": "main.clio",                 // NEW (multi-file only) — entry relpath in sources/
  "sources": {                          // NEW (multi-file only) — full tree, LF-normalized sha256
    "main.clio":     "sha256:…",
    "schemas.clio":  "sha256:…",
    "nlp/nlp.clio":  "sha256:…"
  }
}
```

Hashes reuse the existing helpers (`compute_source_hash` for the LF-normalized text
hash, format `"sha256:<hex>"`). v0.19 readers ignore unknown keys, so a multi-file
manifest stays readable by old importers (which then recover only the entry — the
current behavior, no regression).

### `commonpath` rooting — the one correctness subtlety

All resolved paths are absolute. `root = os.path.commonpath([entry, *imports])`;
each file's relpath is `path.relative_to(root).as_posix()`. This is robust to the two
shapes that break naïve `entry.parent` rooting:

- **Sibling-up import**: `nlp/nlp.clio` does `FROM "../schemas.clio"`, resolving to
  `<dir>/schemas.clio` (already in the entry's subtree). `commonpath` = `<dir>`,
  relpaths preserve the `nlp/` nesting.
- **Entry-in-subdir**: entry `proj/sub/main.clio` imports `../../lib/x.clio` →
  resolves to `proj/lib/x.clio` (sibling of `sub/`). `commonpath` = `proj/`, relpaths
  `sub/main.clio` and `lib/x.clio` — offsets preserved, re-resolution on recompile
  finds `../../lib/x.clio` correctly.

At write time, assert no relpath escapes the root (no leading `..`); `commonpath`
guarantees this, the assert is a fail-loud backstop.

### `clio import` flow

```
clio import skill-out [--output DIR] [--mode strict|auto]
  read .clio/manifest.json
  ├─ "sources" key absent  → single-file: existing behavior, UNCHANGED
  └─ "sources" key present → multi-file:
        if --output is not a directory (stdout / single file):
            error, non-zero exit   ── "multi-file skill; pass --output <dir>"   (fail loud)
        for relpath in .clio/sources/:
            write verbatim → DIR/<relpath>   (mkdir -p parents)
        report DIR/<entry> as the recompile entrypoint
        if --mode strict:
            check_source_drift(...) → on mismatch, exit 2
```

### Touched modules

| File | Change |
|---|---|
| `clio/emitters/base.py` | `emit(... , source_path=None, sources: tuple[Path, ...] \| None = None)` — additive defaulted kwarg on the abstract method. |
| `clio/emitters/{python,mcp_server,langgraph,claude_cli,go}.py` | Accept the new kwarg in their `emit` signature and ignore it (same as `source_path` today). No behavior change. |
| `clio/emitters/claude_skill.py` | Forward `sources` into `write_sidecar`. |
| `clio/emitters/_sidecar.py` | `write_sidecar(..., sources=None)`: when `len(sources) > 1`, write the `.clio/sources/` tree (commonpath-rooted, verbatim, read-once) and pass the `sources` map + `entry` relpath into `build_manifest`. New `check_source_drift(skill_dir, manifest_path)`. `build_manifest` gains optional `sources_map` / `entry`. |
| `clio/cli.py` | `_cmd_compile`: pass the resolved source set to `emit(sources=…)`. `_cmd_import`: branch on `manifest["sources"]`; require `--output DIR`, reconstruct the tree, fail loud on stdout, run `check_source_drift` in strict mode. |
| `clio/ir/{resolver,builder}.py` | Surface the resolved path set to the CLI (minimal — the data already exists in `resolve_imports`'s `dict[Path, Program]`). No IR schema change to `FlowGraph`. |

## Goals

- A cross-file `claude-skill` project round-trips: `compile → import --output DIR → recompile` succeeds (rc 0), with every recovered file byte-identical to the original.
- Single-file `claude-skill` output is byte-identical to v0.21 (no golden churn, no new manifest keys).
- v0.19 manifests and the existing single-file import path keep working unchanged.
- `clio import --mode strict` detects tampering of any stored source file.

## Non-goals (out of scope)

- The LLM-recovery path (`skill_to_clio.py`) — stays single-file.
- Any Go-target work (v0.23 / v0.24).
- The `E_GO_011` documentation drift and the stale "pending golden snapshot" note — a separate micro-issue.
- Dedup / compression of stored sources — store verbatim copies (Rule 2 simplicity).
- Multi-file sidecar for any target other than `claude-skill` (no other target writes a sidecar).
- Resources/TEST cross-file semantics — unchanged from v0.18 (entry file remains the single source of truth).

## Error handling (fail-loud)

- `clio import` of a multi-file skill **without** `--output DIR` → explanatory error, non-zero exit. Never silently emit the entry alone.
- `--mode strict` + a stored source whose hash ≠ the manifest's `sources` entry → exit 2 (consistent with the existing strict-mode drift exit).
- A stored relpath that would escape the output directory → refuse and error (defensive; `commonpath` should make this unreachable).

## Testing

**`tests/test_sidecar.py`**
- Emit `examples/multi_file/` to `claude-skill`: assert `.clio/sources/` contains `main.clio`, `schemas.clio`, `nlp/nlp.clio` at the correct relpaths (nesting preserved); each byte-identical to the original; `source.clio` byte-identical to the entry; manifest carries `sources` (correct LF-normalized hashes) and `entry == "main.clio"`.
- Emit a **single-file** skill: assert the manifest carries **no** `sources` / `entry` keys and the output is byte-identical to the v0.21 baseline (back-compat / no golden churn).
- `check_source_drift`: clean tree → `None`; tampered stored source → that relpath returned.

**`tests/test_cli_import.py`** (the acceptance test)
- Round-trip: compile `examples/multi_file/` → skill; `clio import skill --output DIR`; assert every file under `DIR` byte-identical to the original tree; recompile `DIR/main.clio` → rc 0, **no** `imported file not found` `CompileError`.
- `clio import` of the multi-file skill to stdout (no `--output DIR`) → non-zero exit, error message.
- `clio import --mode strict` after tampering a stored source → exit 2.
- Existing single-file import tests unchanged and green.

**`tests/test_emitters/test_claude_skill.py`** — confirm the new kwarg doesn't perturb existing single-file skill emission (existing assertions unchanged).

## Definition of done (repo conventions)

- New tests green; **existing single-file** sidecar / import / claude-skill tests unchanged (no golden churn).
- `docs/manual/06-troubleshooting.md`: the "multi-file recovered source fails to recompile" entry flipped from a known-limitation to fixed; add a round-trip note.
- `docs/superpowers/specs/2026-05-17-skill-to-clio-importer-design.md:396` "multi-file import out of scope" deferral lifted (cross-reference this spec).
- `CHANGELOG.md`: a v0.22.0 entry.
- **Dual version bump** `pyproject.toml` **and** `clio/__init__.py` → `0.22.0`, in the **separate release-admin PR**.
- `uv run ruff check . --fix && uv run mypy && uv run pytest` all green (no bare `python` on PATH — use `uv run`).
- Feature work on `feat/v0.22-multi-file-sidecar-recovery`; feature PR + Gemini review; release-admin PR separate.

## Follow-ups (filed, not in this release)

- Micro-issue: `E_GO_011` documents a `ValueError` the Go emitter never raises (`docs/manual/06-troubleshooting.md:500-504`); `--from-step` resume is net-new Go work, not a removable refusal. Also correct the stale "three pending golden snapshots" note (all four `expected_go/` goldens exist and pass).
