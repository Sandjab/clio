"""Sidecar writer for the `.clio/` directory inside emitted skills.

Layout:
    skill_dir/.clio/
        source.clio       # verbatim copy of the source .clio file
        manifest.json     # clio_version, emitted_at, source_hash, file_hashes

Hashes are SHA-256. Text files are hashed on LF-normalized bytes so a skill
edited across platforms (CRLF on Windows ↔ LF on Unix) does not show false
drift. Binary files are hashed on raw bytes.

The `.clio/` directory is excluded from `file_hashes` (the manifest cannot
hash itself), as are all hidden files/directories at any depth."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def _is_text(raw: bytes) -> bool:
    """Return True iff the bytes decode as utf-8 (treated as text for the
    purpose of LF normalization)."""
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _normalize_lf(raw: bytes) -> bytes:
    """Convert CRLF and bare CR to LF. Used only on bytes already classified
    as text (utf-8 decodable)."""
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _sha256_hex(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def compute_file_hash(path: Path) -> str:
    """SHA-256 of file bytes. LF-normalized when text, raw when binary."""
    raw = path.read_bytes()
    if _is_text(raw):
        raw = _normalize_lf(raw)
    return _sha256_hex(raw)


def compute_source_hash(source_bytes: bytes) -> str:
    """SHA-256 of source bytes after LF normalization."""
    return _sha256_hex(_normalize_lf(source_bytes))


def _iter_skill_files(skill_dir: Path) -> Iterator[Path]:
    """Yield every file under `skill_dir`, excluding any path component
    starting with '.' (covers `.clio/`, `.git/`, `.DS_Store`, etc.)."""
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        yield path


def build_manifest(source_path: Path, skill_dir: Path, *, clio_version: str) -> dict[str, Any]:
    """Build the manifest dict. Caller is responsible for serializing to JSON."""
    source_bytes = source_path.read_bytes()
    file_hashes: dict[str, str] = {}
    for f in _iter_skill_files(skill_dir):
        rel = f.relative_to(skill_dir).as_posix()
        file_hashes[rel] = compute_file_hash(f)
    return {
        "clio_version": clio_version,
        "emitted_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_hash": compute_source_hash(source_bytes),
        "file_hashes": file_hashes,
    }


def write_sidecar(source_path: Path, skill_dir: Path, *, clio_version: str) -> None:
    """Write `.clio/source.clio` (verbatim copy) and `.clio/manifest.json`.

    Called at the very end of `ClaudeSkillEmitter.emit()` so the manifest
    reflects every file the emitter has just written."""
    sidecar = skill_dir / ".clio"
    sidecar.mkdir(parents=True, exist_ok=True)
    (sidecar / "source.clio").write_bytes(source_path.read_bytes())
    manifest = build_manifest(source_path, skill_dir, clio_version=clio_version)
    (sidecar / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
