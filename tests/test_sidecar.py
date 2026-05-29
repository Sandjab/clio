import json

import pytest


def test_compute_text_hash_normalizes_crlf_to_lf(tmp_path):
    from clio.emitters._sidecar import compute_file_hash

    a = tmp_path / "a.txt"
    a.write_bytes(b"hello\nworld\n")
    b = tmp_path / "b.txt"
    b.write_bytes(b"hello\r\nworld\r\n")
    c = tmp_path / "c.txt"
    c.write_bytes(b"hello\rworld\r")
    h_a = compute_file_hash(a)
    h_b = compute_file_hash(b)
    h_c = compute_file_hash(c)
    assert h_a == h_b == h_c
    assert h_a.startswith("sha256:")


def test_compute_binary_hash_does_not_normalize(tmp_path):
    from clio.emitters._sidecar import compute_file_hash

    # Bytes that decode invalid as utf-8 → treated as binary; CRLF inside must
    # NOT be normalized for binary files (otherwise we'd corrupt the hash).
    p = tmp_path / "p.bin"
    p.write_bytes(b"\xff\x00line1\r\nline2\xff")
    q = tmp_path / "q.bin"
    q.write_bytes(b"\xff\x00line1\nline2\xff")
    assert compute_file_hash(p) != compute_file_hash(q)


def test_compute_source_hash_normalizes_lf(tmp_path):
    from clio.emitters._sidecar import compute_source_hash

    a = b"STEP foo\nMODE: exact\n"
    b = b"STEP foo\r\nMODE: exact\r\n"
    assert compute_source_hash(a) == compute_source_hash(b)


def test_build_manifest_required_keys(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "src.clio"
    src.write_text("STEP foo\n  MODE: exact\n")
    manifest = build_manifest(src.read_bytes(), skill_dir, clio_version="0.19.0")
    assert manifest["clio_version"] == "0.19.0"
    assert "emitted_at" in manifest
    assert manifest["source_hash"].startswith("sha256:")
    assert "file_hashes" in manifest
    assert "SKILL.md" in manifest["file_hashes"]


def test_build_manifest_excludes_dotted_paths(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill_dir = tmp_path / "skill"
    (skill_dir / ".clio").mkdir(parents=True)
    (skill_dir / ".clio" / "preexisting.txt").write_text("x")
    (skill_dir / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "src.clio"
    src.write_text("STEP foo\n  MODE: exact\n")
    manifest = build_manifest(src.read_bytes(), skill_dir, clio_version="0.19.0")
    assert ".clio/preexisting.txt" not in manifest["file_hashes"]
    # SKILL.md still included
    assert "SKILL.md" in manifest["file_hashes"]


def test_build_manifest_uses_posix_paths(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "01_foo.py").write_text("# script\n")
    (skill_dir / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "src.clio"
    src.write_text("STEP foo\n  MODE: exact\n")
    manifest = build_manifest(src.read_bytes(), skill_dir, clio_version="0.19.0")
    assert "scripts/01_foo.py" in manifest["file_hashes"]


def test_write_sidecar_writes_source_and_manifest(tmp_path):
    from clio.emitters._sidecar import compute_source_hash, write_sidecar

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "src.clio"
    src.write_bytes(b"STEP foo\n  MODE: exact\n")
    write_sidecar(src, skill_dir, clio_version="0.19.0")
    assert (skill_dir / ".clio" / "source.clio").read_bytes() == src.read_bytes()
    manifest = json.loads((skill_dir / ".clio" / "manifest.json").read_text())
    assert manifest["clio_version"] == "0.19.0"
    # Guards the single-read invariant: stored source.clio and recorded
    # source_hash must agree on the same bytes.
    assert manifest["source_hash"] == compute_source_hash(src.read_bytes())


def test_build_manifest_reproducible_modulo_timestamp(tmp_path):
    from clio.emitters._sidecar import build_manifest

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    src = tmp_path / "src.clio"
    src.write_text("STEP foo\n  MODE: exact\n")
    m1 = build_manifest(src.read_bytes(), skill_dir, clio_version="0.19.0")
    m2 = build_manifest(src.read_bytes(), skill_dir, clio_version="0.19.0")
    assert m1["source_hash"] == m2["source_hash"]
    assert m1["file_hashes"] == m2["file_hashes"]
    # emitted_at may legitimately differ between calls; not part of the
    # reproducibility contract — confirm it's the only difference.
    m1_no_ts = {k: v for k, v in m1.items() if k != "emitted_at"}
    m2_no_ts = {k: v for k, v in m2.items() if k != "emitted_at"}
    assert m1_no_ts == m2_no_ts


# ---------------------------------------------------------------------------
# check_drift tests (Task 7)
# ---------------------------------------------------------------------------


def _setup_clio_emitted_skill(tmp_path):
    from clio.emitters._sidecar import write_sidecar

    src = tmp_path / "src.clio"
    src.write_text(
        "STEP foo\n  MODE: exact\n  LANG: python\n"
        "FLOW pipe\n  foo()\n"
    )
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill\n")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "01_foo.py").write_text("# foo\n")
    write_sidecar(src, skill_dir, clio_version="0.19.0")
    return skill_dir, src


def test_check_drift_returns_none_when_no_changes(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir, _ = _setup_clio_emitted_skill(tmp_path)
    assert check_drift(skill_dir, skill_dir / ".clio" / "manifest.json") is None


def test_check_drift_detects_modified_file(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir, _ = _setup_clio_emitted_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text("# skill modified\n")
    drift = check_drift(skill_dir, skill_dir / ".clio" / "manifest.json")
    assert drift == ["SKILL.md"]


def test_check_drift_detects_added_file(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir, _ = _setup_clio_emitted_skill(tmp_path)
    (skill_dir / "extra.md").write_text("extra\n")
    drift = check_drift(skill_dir, skill_dir / ".clio" / "manifest.json")
    assert drift == ["extra.md"]


def test_check_drift_detects_removed_file(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir, _ = _setup_clio_emitted_skill(tmp_path)
    (skill_dir / "scripts" / "01_foo.py").unlink()
    drift = check_drift(skill_dir, skill_dir / ".clio" / "manifest.json")
    assert drift == ["scripts/01_foo.py"]


def test_check_drift_sorted_when_multiple_paths_change(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir, _ = _setup_clio_emitted_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text("# changed\n")
    (skill_dir / "scripts" / "01_foo.py").write_text("# changed\n")
    (skill_dir / "zzz_added.md").write_text("added\n")
    drift = check_drift(skill_dir, skill_dir / ".clio" / "manifest.json")
    assert drift == sorted(drift)
    assert set(drift) == {"SKILL.md", "scripts/01_foo.py", "zzz_added.md"}


def test_check_drift_raises_when_manifest_missing(tmp_path):
    from clio.emitters._sidecar import check_drift

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        check_drift(skill_dir, skill_dir / ".clio" / "manifest.json")


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
