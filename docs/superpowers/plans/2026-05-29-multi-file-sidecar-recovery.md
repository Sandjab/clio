# Multi-file `IMPORT` Sidecar Recovery (#67) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a cross-file (`FROM … IMPORT`) project emitted to `target: claude-skill` round-trip through `clio import` and recompile cleanly, by storing every imported source verbatim in the `.clio/` sidecar.

**Architecture:** The import resolver already returns `dict[Path, Program]` keyed by every resolved source path. The CLI forwards those paths to `ClaudeSkillEmitter.emit(…, sources=…)` (a new defaulted kwarg mirroring `source_path`). The sidecar writer copies the full source tree under `.clio/sources/` rooted at `os.path.commonpath`, records a `sources` hash map + `entry` relpath in the manifest, and `clio import --output DIR` reconstructs the tree. Single-file output stays byte-identical to v0.21 (new manifest keys appear only when imports are present).

**Tech Stack:** Python 3.12+, pytest. Run everything via `uv run` — there is **no bare `python` on PATH** in this environment.

---

## Spec

`docs/superpowers/specs/2026-05-29-multi-file-sidecar-recovery-design.md`

## Conventions (read before starting)

- Tests import the symbol under test **inline** inside each test function (see existing `tests/test_sidecar.py`).
- CLI tests call `from clio.cli import main` → `main([...])` (returns the int exit code) or `_cmd_compile(str(src), target, str(out), None)`; assert on `capsys.readouterr()`.
- Hashes are strings prefixed `"sha256:"`, LF-normalized for text (`compute_source_hash` / `compute_file_hash` in `clio/emitters/_sidecar.py`).
- Before the final push: `uv run ruff check . --fix` then `uv run mypy` then `uv run pytest` — all three must be green (CI gates pytest behind ruff and runs mypy in strict mode).
- Work happens on branch `feat/v0.22-multi-file-sidecar-recovery` (already created; the spec is already committed there).

## File structure

| File | Responsibility | Change |
|---|---|---|
| `clio/emitters/_sidecar.py` | sidecar writer + drift | `build_manifest` gains `sources_map`/`entry`; `write_sidecar` gains `sources`; new `check_source_drift`; `import os` |
| `clio/emitters/base.py` | abstract emitter | `emit` gains `sources` kwarg (protocol) |
| `clio/emitters/{claude_cli,python,mcp_server,langgraph,go}.py` | other emitters | accept-and-ignore `sources` (mypy-compat override) |
| `clio/emitters/claude_skill.py` | skill emitter | `emit` gains `sources`, forwards to `write_sidecar` |
| `clio/cli.py` | CLI | `_cmd_compile` passes `sources=tuple(parsed)`; `_cmd_import` reconstructs multi-file trees |
| `tests/test_sidecar.py` | unit tests | manifest keys, source tree, `check_source_drift` |
| `tests/test_cli_import.py` | integration | compile writes tree; round-trip recompiles; fail-loud stdout; strict source drift |
| `docs/manual/06-troubleshooting.md` | manual | additive note on multi-file round-trip |
| `docs/superpowers/specs/2026-05-17-skill-to-clio-importer-design.md` | importer spec | lift the "Multi-file import" deferral |

---

### Task 1: `build_manifest` — optional `sources`/`entry` keys

**Files:**
- Modify: `clio/emitters/_sidecar.py:69-83` (`build_manifest`)
- Test: `tests/test_sidecar.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sidecar.py`:

```python
def test_build_manifest_adds_sources_and_entry_when_provided(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "main.clio"
    src.write_text("STEP s\n  MODE: exact\n")
    m = build_manifest(
        src.read_bytes(),
        skill,
        clio_version="0.22.0",
        sources_map={"main.clio": "sha256:aaa", "lib.clio": "sha256:bbb"},
        entry="main.clio",
    )
    assert m["entry"] == "main.clio"
    assert m["sources"] == {"main.clio": "sha256:aaa", "lib.clio": "sha256:bbb"}


def test_build_manifest_omits_sources_when_not_provided(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "main.clio"
    src.write_text("STEP s\n  MODE: exact\n")
    m = build_manifest(src.read_bytes(), skill, clio_version="0.22.0")
    assert "sources" not in m
    assert "entry" not in m
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sidecar.py::test_build_manifest_adds_sources_and_entry_when_provided -v`
Expected: FAIL with `TypeError: build_manifest() got an unexpected keyword argument 'sources_map'`

- [ ] **Step 3: Implement**

Replace `build_manifest` in `clio/emitters/_sidecar.py` (currently lines 69-83) with:

```python
def build_manifest(
    source_bytes: bytes,
    skill_dir: Path,
    *,
    clio_version: str,
    sources_map: dict[str, str] | None = None,
    entry: str | None = None,
) -> dict[str, Any]:
    """Build the manifest dict from already-read source bytes. Caller is
    responsible for serializing to JSON. Accepting bytes (not Path) lets
    write_sidecar guarantee the stored source.clio and the recorded
    source_hash agree by reading the file only once.

    `sources_map` / `entry` are recorded only for multi-file projects (the
    full source tree + the entry's relpath); single-file manifests omit both
    keys, keeping v0.21 output byte-identical."""
    file_hashes: dict[str, str] = {}
    for f in _iter_skill_files(skill_dir):
        rel = f.relative_to(skill_dir).as_posix()
        file_hashes[rel] = compute_file_hash(f)
    manifest: dict[str, Any] = {
        "clio_version": clio_version,
        "emitted_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_hash": compute_source_hash(source_bytes),
        "file_hashes": file_hashes,
    }
    if sources_map is not None:
        manifest["entry"] = entry
        manifest["sources"] = sources_map
    return manifest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sidecar.py -k build_manifest -v`
Expected: PASS (the two new tests + the existing `build_manifest` tests).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_sidecar.py tests/test_sidecar.py
git commit -m "feat(v0.22): build_manifest records sources/entry for multi-file skills"
```

---

### Task 2: `write_sidecar` — store the `.clio/sources/` tree

**Files:**
- Modify: `clio/emitters/_sidecar.py:14-21` (imports), `:86-99` (`write_sidecar`)
- Test: `tests/test_sidecar.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sidecar.py`:

```python
def test_write_sidecar_multi_file_stores_source_tree(tmp_path):
    from clio.emitters._sidecar import compute_source_hash, write_sidecar

    # Tree with a nested file whose import escapes its own dir (../schemas.clio),
    # exactly the shape commonpath rooting must handle.
    root = tmp_path / "proj"
    (root / "nlp").mkdir(parents=True)
    entry = root / "main.clio"
    entry.write_bytes(b'FROM "./schemas.clio" IMPORT X\nSTEP s\n  MODE: exact\n')
    schemas = root / "schemas.clio"
    schemas.write_bytes(b"CONTRACT X\n  SHAPE: {a: str}\n")
    nested = root / "nlp" / "nlp.clio"
    nested.write_bytes(b'FROM "../schemas.clio" IMPORT X\nSTEP t\n  MODE: exact\n')

    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")

    write_sidecar(entry, skill, clio_version="0.22.0", sources=(entry, schemas, nested))

    srcdir = skill / ".clio" / "sources"
    assert srcdir.joinpath("main.clio").read_bytes() == entry.read_bytes()
    assert srcdir.joinpath("schemas.clio").read_bytes() == schemas.read_bytes()
    assert srcdir.joinpath("nlp", "nlp.clio").read_bytes() == nested.read_bytes()

    manifest = json.loads((skill / ".clio" / "manifest.json").read_text())
    assert manifest["entry"] == "main.clio"
    assert set(manifest["sources"]) == {"main.clio", "schemas.clio", "nlp/nlp.clio"}
    assert manifest["sources"]["schemas.clio"] == compute_source_hash(schemas.read_bytes())
    # source.clio is still the verbatim entry (back-compat path unchanged)
    assert (skill / ".clio" / "source.clio").read_bytes() == entry.read_bytes()


def test_write_sidecar_single_file_omits_sources(tmp_path):
    from clio.emitters._sidecar import write_sidecar

    entry = tmp_path / "solo.clio"
    entry.write_bytes(b"STEP s\n  MODE: exact\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")

    # A 1-tuple (no imports) must behave exactly like single-file: no tree, no keys.
    write_sidecar(entry, skill, clio_version="0.22.0", sources=(entry,))

    assert not (skill / ".clio" / "sources").exists()
    manifest = json.loads((skill / ".clio" / "manifest.json").read_text())
    assert "sources" not in manifest
    assert "entry" not in manifest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sidecar.py::test_write_sidecar_multi_file_stores_source_tree -v`
Expected: FAIL with `TypeError: write_sidecar() got an unexpected keyword argument 'sources'`

- [ ] **Step 3: Implement**

In `clio/emitters/_sidecar.py`, add `os` to the imports block (currently lines 14-21). The import block becomes:

```python
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
```

Replace `write_sidecar` (currently lines 86-99) with:

```python
def write_sidecar(
    source_path: Path,
    skill_dir: Path,
    *,
    clio_version: str,
    sources: tuple[Path, ...] | None = None,
) -> None:
    """Write `.clio/source.clio` (verbatim entry copy) and `.clio/manifest.json`.

    For a multi-file project (`sources` holds more than one resolved path),
    also write the full source tree under `.clio/sources/`, rooted at the
    common ancestor of all sources so a `FROM "../x.clio"` import keeps its
    relative offset, and record the `sources` hash map + `entry` relpath in the
    manifest. Single-file projects (`sources` None or length 1) write neither —
    output stays byte-identical to v0.21.

    Each file is read exactly once; its stored copy and recorded hash refer to
    the same bytes."""
    source_bytes = source_path.read_bytes()
    sidecar = skill_dir / ".clio"
    sidecar.mkdir(parents=True, exist_ok=True)
    (sidecar / "source.clio").write_bytes(source_bytes)

    sources_map: dict[str, str] | None = None
    entry_rel: str | None = None
    if sources is not None and len(sources) > 1:
        resolved = [p.resolve() for p in sources]
        root = Path(os.path.commonpath([str(p) for p in resolved]))
        sources_map = {}
        for p in resolved:
            # relative_to raises if p escapes root; commonpath guarantees it
            # cannot, so this doubles as a fail-loud backstop.
            rel = p.relative_to(root).as_posix()
            dst = sidecar / "sources" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            raw = p.read_bytes()
            dst.write_bytes(raw)
            sources_map[rel] = compute_source_hash(raw)
        entry_rel = source_path.resolve().relative_to(root).as_posix()

    manifest = build_manifest(
        source_bytes,
        skill_dir,
        clio_version=clio_version,
        sources_map=sources_map,
        entry=entry_rel,
    )
    (sidecar / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
```

> Note: `build_manifest` calls `_iter_skill_files`, which excludes any dotted path component, so the freshly-written `.clio/sources/*` files are correctly absent from `file_hashes`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sidecar.py -k write_sidecar -v`
Expected: PASS (the two new tests + the existing `write_sidecar` test).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_sidecar.py tests/test_sidecar.py
git commit -m "feat(v0.22): write_sidecar stores the full source tree under .clio/sources/"
```

---

### Task 3: `check_source_drift` — verify stored sources

**Files:**
- Modify: `clio/emitters/_sidecar.py` (add function after `check_drift`, end of file)
- Test: `tests/test_sidecar.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sidecar.py`:

```python
def test_check_source_drift_none_when_sources_match(tmp_path):
    from clio.emitters._sidecar import check_source_drift, write_sidecar

    root = tmp_path / "proj"
    root.mkdir()
    entry = root / "main.clio"
    entry.write_bytes(b"A\n")
    lib = root / "lib.clio"
    lib.write_bytes(b"B\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")
    write_sidecar(entry, skill, clio_version="0.22.0", sources=(entry, lib))

    assert check_source_drift(skill, skill / ".clio" / "manifest.json") is None


def test_check_source_drift_detects_tampered_source(tmp_path):
    from clio.emitters._sidecar import check_source_drift, write_sidecar

    root = tmp_path / "proj"
    root.mkdir()
    entry = root / "main.clio"
    entry.write_bytes(b"A\n")
    lib = root / "lib.clio"
    lib.write_bytes(b"B\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")
    write_sidecar(entry, skill, clio_version="0.22.0", sources=(entry, lib))

    (skill / ".clio" / "sources" / "lib.clio").write_bytes(b"TAMPERED\n")
    drift = check_source_drift(skill, skill / ".clio" / "manifest.json")
    assert drift == ["lib.clio"]


def test_check_source_drift_none_for_single_file_manifest(tmp_path):
    from clio.emitters._sidecar import check_source_drift, write_sidecar

    entry = tmp_path / "solo.clio"
    entry.write_bytes(b"A\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# skill\n")
    write_sidecar(entry, skill, clio_version="0.22.0")  # single-file → no sources map

    assert check_source_drift(skill, skill / ".clio" / "manifest.json") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sidecar.py::test_check_source_drift_detects_tampered_source -v`
Expected: FAIL with `ImportError: cannot import name 'check_source_drift'`

- [ ] **Step 3: Implement**

Append to `clio/emitters/_sidecar.py`:

```python
def check_source_drift(skill_dir: Path, manifest_path: Path) -> list[str] | None:
    """Compare the manifest's `sources` hashes to the actual `.clio/sources/`
    tree.

    Returns None when the manifest has no `sources` map (single-file skill) or
    when every recorded source matches. Returns a sorted list of drifted
    relpaths (modified or missing) otherwise.

    This is separate from `check_drift`: stored sources live under `.clio/`,
    which `_iter_skill_files` deliberately excludes from `file_hashes`.

    Raises FileNotFoundError if the manifest is missing."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded: dict[str, str] = manifest.get("sources", {})
    if not recorded:
        return None
    sources_dir = skill_dir / ".clio" / "sources"
    drifted: set[str] = set()
    for rel, h in recorded.items():
        f = sources_dir / rel
        if not f.exists() or compute_file_hash(f) != h:
            drifted.add(rel)
    return sorted(drifted) if drifted else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sidecar.py -k check_source_drift -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add clio/emitters/_sidecar.py tests/test_sidecar.py
git commit -m "feat(v0.22): check_source_drift verifies stored .clio/sources/ integrity"
```

---

### Task 4: Thread `sources` through `emit()` and `_cmd_compile`

**Files:**
- Modify: `clio/emitters/base.py:9-22`
- Modify: `clio/emitters/claude_cli.py:107`, `python.py:142`, `mcp_server.py:42`, `langgraph.py:77`, `go.py:39`
- Modify: `clio/emitters/claude_skill.py:82` (signature) and `:130` (forward)
- Modify: `clio/cli.py:147` (`_cmd_compile`, claude-skill branch)
- Test: `tests/test_cli_import.py`

- [ ] **Step 1: Write the failing test**

Add to the top of `tests/test_cli_import.py` (after the existing imports), a module-level path to the committed multi-file fixture, then a test:

```python
_MULTI_FILE_MAIN = Path(__file__).resolve().parents[1] / "examples" / "multi_file" / "main.clio"


def test_compile_multi_file_skill_writes_source_tree(tmp_path: Path) -> None:
    from clio.cli import _cmd_compile

    skill = tmp_path / "skill"
    rc = _cmd_compile(str(_MULTI_FILE_MAIN), "claude-skill", str(skill), None)
    assert rc == 0
    assert (skill / ".clio" / "sources" / "main.clio").exists()
    assert (skill / ".clio" / "sources" / "schemas.clio").exists()
    assert (skill / ".clio" / "sources" / "nlp" / "nlp.clio").exists()
    import json
    manifest = json.loads((skill / ".clio" / "manifest.json").read_text())
    assert manifest["entry"] == "main.clio"
    assert set(manifest["sources"]) == {"main.clio", "schemas.clio", "nlp/nlp.clio"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_import.py::test_compile_multi_file_skill_writes_source_tree -v`
Expected: FAIL — `.clio/sources/main.clio` does not exist (the CLI does not yet pass `sources`).

- [ ] **Step 3: Implement — base.py**

Replace the abstract `emit` in `clio/emitters/base.py` (lines 9-22) with:

```python
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
        sources: tuple[Path, ...] | None = None,
    ) -> None:
        """Emit a target project under `output_dir`.

        `source_path` is the absolute path to the originating `.clio` file, or
        None when the emitter is invoked programmatically (tests, scripts).

        `sources` is the full set of resolved `.clio` source paths (entry +
        imports) for a multi-file project, or None for single-file /
        programmatic callers. Both are currently consumed only by
        `ClaudeSkillEmitter` (for the `.clio/` sidecar); other emitters accept
        and ignore them."""
        ...
```

- [ ] **Step 4: Implement — the 5 non-skill emitters**

In each of `claude_cli.py:107`, `python.py:142`, `mcp_server.py:42`, `langgraph.py:77`, add `sources` to the `emit` signature. The one-line form becomes (verbatim, for each of the four):

```python
    def emit(self, graph: FlowGraph, output_dir: Path, *, source_path: Path | None = None, sources: tuple[Path, ...] | None = None) -> None:
```

In `go.py:39`, the signature is multi-line; add the parameter after `source_path`:

```python
    def emit(
        self,
        graph: FlowGraph,
        output_dir: Path,
        *,
        source_path: Path | None = None,
        sources: tuple[Path, ...] | None = None,
    ) -> None:
```

(These five ignore `sources`, exactly as they already ignore `source_path`.)

- [ ] **Step 5: Implement — claude_skill.py forwards `sources`**

In `clio/emitters/claude_skill.py`, change the signature at line 82:

```python
    def emit(self, graph: FlowGraph, output_dir: Path, *, source_path: Path | None = None, sources: tuple[Path, ...] | None = None) -> None:
```

and the `write_sidecar` call at line 130:

```python
                write_sidecar(source_path, output_dir, clio_version=_clio_version, sources=sources)
```

- [ ] **Step 6: Implement — `_cmd_compile` passes the resolved set**

In `clio/cli.py`, the `claude-skill` branch (lines 145-147) becomes:

```python
    elif target == "claude-skill":
        from clio.emitters.claude_skill import ClaudeSkillEmitter
        ClaudeSkillEmitter().emit(graph, out_path, source_path=src_resolved, sources=tuple(parsed))
```

(`parsed = resolve_imports(src_path)` from line 119; its keys are every resolved source path, entry included.)

- [ ] **Step 7: Run test + mypy to verify**

Run: `uv run pytest tests/test_cli_import.py::test_compile_multi_file_skill_writes_source_tree -v`
Expected: PASS

Run: `uv run mypy`
Expected: no errors (identical `sources` signatures across all six overrides are Liskov-compatible).

- [ ] **Step 8: Commit**

```bash
git add clio/emitters/base.py clio/emitters/claude_cli.py clio/emitters/python.py clio/emitters/mcp_server.py clio/emitters/langgraph.py clio/emitters/go.py clio/emitters/claude_skill.py clio/cli.py tests/test_cli_import.py
git commit -m "feat(v0.22): thread resolved sources from compile into the claude-skill sidecar"
```

---

### Task 5: `_cmd_import` — reconstruct multi-file trees

**Files:**
- Modify: `clio/cli.py:338` and `:363` (call the new dispatcher), add `_recover_from_sidecar` after `_emit_imported_source` (line 382)
- Test: `tests/test_cli_import.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_import.py`:

```python
def test_import_multi_file_round_trip_recompiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import _cmd_check, _cmd_compile, main

    skill = tmp_path / "skill"
    assert _cmd_compile(str(_MULTI_FILE_MAIN), "claude-skill", str(skill), None) == 0

    recovered = tmp_path / "recovered"
    rc = main(["import", str(skill), "--output", str(recovered)])
    assert rc == 0

    # verbatim recovery of the whole tree
    base = _MULTI_FILE_MAIN.parent
    assert (recovered / "main.clio").read_bytes() == (base / "main.clio").read_bytes()
    assert (recovered / "schemas.clio").read_bytes() == (base / "schemas.clio").read_bytes()
    assert (recovered / "nlp" / "nlp.clio").read_bytes() == (base / "nlp" / "nlp.clio").read_bytes()

    # the recovered entry recompiles — the bug was: imports could not be found
    assert _cmd_check(str(recovered / "main.clio")) == 0


def test_import_multi_file_without_output_dir_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import _cmd_compile, main

    skill = tmp_path / "skill"
    assert _cmd_compile(str(_MULTI_FILE_MAIN), "claude-skill", str(skill), None) == 0
    rc = main(["import", str(skill)])  # no --output → would be stdout
    assert rc == 2
    assert "multi-file" in capsys.readouterr().err.lower()


def test_import_multi_file_strict_detects_source_tampering(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.cli import _cmd_compile, main

    skill = tmp_path / "skill"
    assert _cmd_compile(str(_MULTI_FILE_MAIN), "claude-skill", str(skill), None) == 0
    # tamper a stored source (excluded from file_hashes, so only check_source_drift catches it)
    (skill / ".clio" / "sources" / "schemas.clio").write_text("CONTRACT Tampered\n  SHAPE: {x: str}\n")
    recovered = tmp_path / "recovered"
    rc = main(["import", str(skill), "--mode", "strict", "--output", str(recovered)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "drift" in err.lower()
    assert "schemas.clio" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_import.py -k "multi_file" -v`
Expected: FAIL — `test_import_multi_file_round_trip_recompiles` fails because `main(["import", …, "--output", DIR])` currently writes the entry source as a single file into the `recovered` path (so `recovered/main.clio` is not created and `_cmd_check` errors on the missing schemas import).

- [ ] **Step 3: Implement — add the dispatcher**

In `clio/cli.py`, replace `_emit_imported_source` (lines 377-382) — keep it, and add `_recover_from_sidecar` immediately after it:

```python
def _emit_imported_source(source_text: str, output: str | None) -> int:
    if output is None:
        sys.stdout.write(source_text)
    else:
        Path(output).write_text(source_text)
    return 0


def _recover_from_sidecar(
    sk_path: Path,
    manifest_file: Path,
    source_file: Path,
    output: str | None,
    *,
    strict: bool,
) -> int:
    """Recover the source from a CLIO sidecar. Single-file → emit the entry
    (stdout or file). Multi-file (`sources` present) → reconstruct the tree
    under the output directory."""
    import json as _json

    manifest = _json.loads(manifest_file.read_text(encoding="utf-8"))
    sources = manifest.get("sources")
    if not sources:
        return _emit_imported_source(source_file.read_text(), output)

    if strict:
        from clio.emitters._sidecar import check_source_drift

        src_drift = check_source_drift(sk_path, manifest_file)
        if src_drift:
            print(
                "clio import: --mode strict and stored sources drifted.",
                file=sys.stderr,
            )
            _print_drift_list(src_drift)
            return 2

    if output is None:
        print(
            "clio import: multi-file skill — pass --output <dir> to reconstruct "
            "the source tree.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(output)
    sources_dir = sk_path / ".clio" / "sources"
    for rel in sorted(sources):
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((sources_dir / rel).read_bytes())
    entry = manifest.get("entry")
    print(
        f"clio import: recovered {len(sources)} source files to {out_dir} "
        f"(entry: {entry}).",
        file=sys.stderr,
    )
    return 0
```

- [ ] **Step 4: Implement — route strict and auto through the dispatcher**

In `clio/cli.py`, line 338 (strict mode, after the drift check passes) — replace:

```python
        return _emit_imported_source(source_file.read_text(), output)
```

with:

```python
        return _recover_from_sidecar(sk_path, manifest_file, source_file, output, strict=True)
```

And line 363 (auto mode, `drift is None` branch) — replace:

```python
                return _emit_imported_source(source_file.read_text(), output)
```

with:

```python
                return _recover_from_sidecar(sk_path, manifest_file, source_file, output, strict=False)
```

(Both call sites already proved the manifest is present and readable before reaching this point.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_import.py -v`
Expected: PASS — the three new multi-file tests **and** all existing single-file import tests (regression check: single-file recovery still emits to stdout / file unchanged, because `manifest.get("sources")` is falsy for those).

- [ ] **Step 6: Commit**

```bash
git add clio/cli.py tests/test_cli_import.py
git commit -m "feat(v0.22): clio import reconstructs multi-file source trees from the sidecar"
```

---

### Task 6: Manual documentation

**Files:**
- Modify: `docs/manual/06-troubleshooting.md` (Cross-file imports section, after `E_RES_002` ~line 636)
- Modify: `docs/superpowers/specs/2026-05-17-skill-to-clio-importer-design.md` ("Deferred to v0.20+" → "Multi-file import" bullet)

- [ ] **Step 1: Add a troubleshooting note**

In `docs/manual/06-troubleshooting.md`, after the `E_RES_002` entry (the "imported file not found" block ending ~line 636), insert:

```markdown
### Recovering a multi-file skill (`clio import`, v0.22)

A `claude-skill` compiled from a cross-file project (`FROM … IMPORT`) stores
every imported source verbatim under `.clio/sources/`. To recover it, `clio
import` **requires a directory** so it can rebuild the tree:

```bash
clio import ./skill-out --output ./recovered   # writes recovered/main.clio, recovered/schemas.clio, …
clio compile ./recovered/main.clio --target python --output ./py-out   # recompiles cleanly
```

Importing a multi-file skill without `--output <dir>` exits non-zero: a source
tree cannot be written to stdout. `--mode strict` additionally verifies every
stored source against its recorded hash (exit 2 on tampering). Single-file
skills are unaffected — they still recover to stdout or a single file.
```

- [ ] **Step 2: Lift the importer-spec deferral**

In `docs/superpowers/specs/2026-05-17-skill-to-clio-importer-design.md`, under "Deferred to v0.20+", replace the "Multi-file import" bullet with:

```markdown
- ~~Multi-file import~~ **(resolved in v0.22, sidecar path only)**: a CLIO-emitted skill whose source used IMPORT / EXPOSE (v0.18) now stores the full source tree under `.clio/sources/`, and `clio import --output <dir>` reconstructs it verbatim (see `docs/superpowers/specs/2026-05-29-multi-file-sidecar-recovery-design.md`). The LLM-recovery path (hand-written skills, no sidecar) still produces a single inline `.clio` file.
```

- [ ] **Step 3: Verify the manual still builds / renders**

Run: `uv run pytest tests/ -k "manual or docs" -q` (skip if no such tests exist — these are prose edits).
Expected: PASS or "no tests ran". Manually confirm the inserted Markdown fences are balanced.

- [ ] **Step 4: Commit**

```bash
git add docs/manual/06-troubleshooting.md docs/superpowers/specs/2026-05-17-skill-to-clio-importer-design.md
git commit -m "docs(v0.22): document multi-file skill round-trip; lift importer deferral"
```

---

### Task 7: Final verification + feature PR

- [ ] **Step 1: Full gate (must be green before pushing)**

```bash
uv run ruff check . --fix
uv run mypy
uv run pytest
```
Expected: ruff clean, mypy no errors, pytest all green (existing suite + the new sidecar/import tests; net +~11 tests).

- [ ] **Step 2: Push and open the feature PR**

```bash
git push -u origin feat/v0.22-multi-file-sidecar-recovery
gh pr create --title "feat(v0.22): multi-file IMPORT sidecar recovery (closes #67)" \
  --body "$(cat <<'EOF'
Closes #67.

Cross-file (`FROM … IMPORT`) projects emitted to `target: claude-skill` now store every imported source verbatim under `.clio/sources/`, and `clio import --output <dir>` reconstructs the tree so the recovered project recompiles cleanly.

## What changed
- `_sidecar.py`: `write_sidecar` stores the full source tree (commonpath-rooted), manifest gains `sources`/`entry` (multi-file only); new `check_source_drift`.
- `emit()` gains a defaulted `sources` kwarg (mirrors `source_path`); only `ClaudeSkillEmitter` consumes it.
- `clio import` reconstructs multi-file trees, requires `--output <dir>`, and verifies stored sources under `--mode strict`.
- Single-file output is byte-identical to v0.21 (new keys appear only when imports are present).

## Test plan
- [ ] `uv run ruff check .` clean
- [ ] `uv run mypy` no errors
- [ ] `uv run pytest` green
- [ ] Round-trip: compile `examples/multi_file` → skill → `clio import --output DIR` → recompiles
- [ ] Multi-file import without `--output` exits 2
- [ ] `--mode strict` detects a tampered stored source

Spec: `docs/superpowers/specs/2026-05-29-multi-file-sidecar-recovery-design.md`
EOF
)"
```

- [ ] **Step 3: After CI green, trigger Gemini review manually** (auto-trigger is unreliable)

Post `/gemini review` on the PR. Reply to each Gemini comment citing the fix commit (or a reasoned refusal). Re-run the full gate after any fix.

---

### Release-admin (SEPARATE PR, after the feature PR merges)

> Per repo convention every release goes through two PRs (feature + release-admin); the tag lands on the release-admin commit via `release.yml`. **Both** `pyproject.toml` **and** `clio/__init__.py` must be bumped (drift stamps the wrong `clio_version` into sidecars).

- [ ] Branch from updated `main`: `git checkout main && git pull --ff-only && git checkout -b release/v0.22.0`
- [ ] Bump `pyproject.toml` `version = "0.22.0"` **and** `clio/__init__.py` `__version__ = "0.22.0"`.
- [ ] Prepend to `CHANGELOG.md` (set the date to the release day):

```markdown
## [0.22.0] — 2026-MM-DD

Patch release closing issue #67: cross-file (`FROM … IMPORT`) projects emitted to `target: claude-skill` now round-trip through `clio import`. The sidecar stored only the entry `source.clio`, so a recovered multi-file source referenced imported files that were absent and failed to recompile (`CompileError: imported file not found`). The emitter now stores the full source tree under `.clio/sources/`.

### Added

- **Multi-file `IMPORT` sidecar recovery.** `write_sidecar` stores every resolved source (entry + imports) verbatim under `.clio/sources/`, rooted at their common ancestor so a `FROM "../x.clio"` import keeps its relative offset. The manifest gains `sources` (`{relpath: "sha256:…"}`) and `entry` (entry relpath) — emitted **only** when imports are present, so single-file `claude-skill` output stays byte-identical to v0.21. `emit()` gains a defaulted `sources` kwarg (mirroring `source_path`), consumed only by `ClaudeSkillEmitter`.
- **`clio import` multi-file reconstruction.** When the manifest carries `sources`, `clio import --output <dir>` rebuilds the source tree verbatim; importing to stdout (no `--output`) exits non-zero (a tree cannot be streamed). New `check_source_drift` verifies stored sources under `--mode strict`.

### Tests

- `tests/test_sidecar.py`: +7 (manifest keys, source tree incl. nested `../` re-anchoring, `check_source_drift`).
- `tests/test_cli_import.py`: +4 (compile writes tree, round-trip recompiles, fail-loud stdout, strict source-drift).
```

- [ ] `uv run pytest` green, push, open `chore(release): v0.22.0` PR, merge after CI; the tag lands via `release.yml`.

---

## Self-Review

**Spec coverage:**
- Sidecar-only scope → Tasks 1-5 touch only the sidecar/emit/import path; `skill_to_clio.py` untouched. ✓
- Plumbing approach A (`sources` kwarg) → Task 4. ✓
- commonpath rooting + `../` re-anchor → Task 2 (impl + nested test). ✓
- Full tree in `.clio/sources/` + entry duplication (`source.clio` kept) → Task 2 test asserts both. ✓
- Manifest `sources`/`entry`, multi-file only, single-file byte-identical → Tasks 1 & 2 (both the "adds" and the "omits" tests). ✓
- `check_source_drift` separate from `check_drift` → Task 3. ✓
- import requires `--output DIR`, fail loud → Task 5 (`test_import_multi_file_without_output_dir_exits_2`). ✓
- Verbatim fidelity + clean recompile → Task 5 (`test_import_multi_file_round_trip_recompiles`). ✓
- strict source tamper detection → Task 5 (`test_import_multi_file_strict_detects_source_tampering`). ✓
- Definition of done (manual, CHANGELOG, dual version bump, separate release PR, ruff+mypy+pytest) → Tasks 6, 7, release-admin section. ✓
- `E_GO_011` doc drift out of scope → noted as a follow-up only; not in any task. ✓

**Placeholder scan:** No "TBD"/"implement later"; every code step shows full code. The only non-literal is the CHANGELOG release date (`2026-MM-DD`) — a genuine release-time value, flagged explicitly.

**Type consistency:** `sources: tuple[Path, ...] | None` is identical across `base.py` and all six overrides and the `_cmd_compile` call (`tuple(parsed)`). `sources_map: dict[str, str] | None` / `entry: str | None` match between `build_manifest` (Task 1) and `write_sidecar` (Task 2). `check_source_drift(skill_dir, manifest_path)` signature matches its call in `_recover_from_sidecar` (Task 5). Manifest keys `"sources"` / `"entry"` are spelled identically in Tasks 1, 2, 3, 5.
