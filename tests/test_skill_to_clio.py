# tests/test_skill_to_clio.py
from pathlib import Path

import pytest


def _make_skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# my skill\n\nDoes X.\n")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "01_foo.py").write_text("print('foo')\n")
    (skill / "scripts" / "_validate.py").write_text("# boilerplate\n")
    (skill / "scripts" / "_cache_key.py").write_text("# boilerplate\n")
    (skill / "prompts").mkdir()
    (skill / "prompts" / "02_explain.md").write_text("Explain X.\n")
    return skill


def test_gather_includes_skill_md_and_scripts(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    payload = _gather_skill_files(skill)
    assert "=== SKILL.md ===" in payload
    assert "# my skill" in payload
    assert "=== scripts/01_foo.py ===" in payload
    assert "print('foo')" in payload
    assert "=== prompts/02_explain.md ===" in payload


def test_gather_excludes_clio_sidecar(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    (skill / ".clio").mkdir()
    (skill / ".clio" / "source.clio").write_text("STEP foo\nMODE: exact\n")
    (skill / ".clio" / "manifest.json").write_text("{}")
    payload = _gather_skill_files(skill)
    assert ".clio/" not in payload
    assert "source.clio" not in payload
    assert "manifest.json" not in payload


def test_gather_excludes_validate_and_cache_key_boilerplate(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    payload = _gather_skill_files(skill)
    assert "_validate.py" not in payload
    assert "_cache_key.py" not in payload


def test_gather_skips_binary_files(tmp_path: Path) -> None:
    from clio.skill_to_clio import _gather_skill_files

    skill = _make_skill(tmp_path)
    (skill / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    payload = _gather_skill_files(skill)
    assert "image.png" not in payload


def test_check_size_warns_above_100k_tokens(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.skill_to_clio import _check_size

    payload = "x" * (100_001 * 4)  # ~100k tokens (4 chars / token approx)
    _check_size(payload)  # must not raise — just warn
    captured = capsys.readouterr()
    assert "warning" in captured.err.lower()
    assert "100" in captured.err  # mentions the threshold


def test_check_size_aborts_above_180k_tokens(tmp_path: Path) -> None:
    from clio.skill_to_clio import GenerationError, _check_size

    payload = "x" * (180_001 * 4)
    with pytest.raises(GenerationError, match="too large"):
        _check_size(payload)


def test_check_size_silent_under_100k(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from clio.skill_to_clio import _check_size

    payload = "x" * (50_000 * 4)
    _check_size(payload)
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# generate() tests (Task 10)
# ---------------------------------------------------------------------------

_VALID_CLIO = "STEP foo\n  MODE: exact\n  LANG: python\nFLOW pipe\n  foo()\n"


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._responses.pop(0))


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.messages = _FakeMessages(responses)


def _tiny_skill(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Tiny skill\nDoes foo.\n")
    (skill / "scripts").mkdir()
    (skill / "scripts" / "01_foo.py").write_text("print('foo')\n")
    return skill


def test_generate_returns_valid_clio_on_first_try(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient([_VALID_CLIO])
    out = generate(_tiny_skill(tmp_path), client=client)
    assert out == _VALID_CLIO
    assert len(client.messages.calls) == 1


def test_generate_strips_markdown_fences(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient(["```clio\n" + _VALID_CLIO + "```\n"])
    out = generate(_tiny_skill(tmp_path), client=client)
    assert out == _VALID_CLIO


def test_generate_retries_on_parse_error_then_succeeds(tmp_path):
    from clio.skill_to_clio import generate

    invalid = "STEP\n  MODE: exact\n"  # missing name
    client = _FakeClient([invalid, _VALID_CLIO])
    out = generate(_tiny_skill(tmp_path), client=client)
    assert out == _VALID_CLIO
    assert len(client.messages.calls) == 2


def test_generate_retries_on_ir_build_error(tmp_path):
    from clio.skill_to_clio import generate

    invalid = "STEP a\n  MODE: exact\nFLOW f\n  nope()\n"
    client = _FakeClient([invalid, _VALID_CLIO])
    out = generate(_tiny_skill(tmp_path), client=client)
    assert out == _VALID_CLIO


def test_generate_raises_after_retry_exhausted(tmp_path):
    from clio.skill_to_clio import GenerationError, generate

    invalid = "STEP\n  MODE: exact\n"
    client = _FakeClient([invalid, invalid])
    with pytest.raises(GenerationError) as exc_info:
        generate(_tiny_skill(tmp_path), client=client)
    err = exc_info.value
    assert err.last_attempt == invalid
    assert err.last_error
    assert len(client.messages.calls) == 2


def test_generate_passes_model(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient([_VALID_CLIO])
    generate(_tiny_skill(tmp_path), client=client, model="claude-opus-4-7")
    assert client.messages.calls[0]["model"] == "claude-opus-4-7"


def test_generate_uses_ephemeral_cache_control_on_system(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient([_VALID_CLIO])
    generate(_tiny_skill(tmp_path), client=client)
    system = client.messages.calls[0]["system"]
    assert isinstance(system, list)
    assert any(
        block.get("cache_control") == {"type": "ephemeral"}
        for block in system
    )


def test_generate_includes_payload_in_user_message(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient([_VALID_CLIO])
    generate(_tiny_skill(tmp_path), client=client)
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "=== SKILL.md ===" in user_msg
    assert "# Tiny skill" in user_msg
    assert "=== scripts/01_foo.py ===" in user_msg


def test_generate_excludes_clio_sidecar_from_payload(tmp_path):
    from clio.skill_to_clio import generate

    skill = _tiny_skill(tmp_path)
    (skill / ".clio").mkdir()
    (skill / ".clio" / "source.clio").write_text("STEP cheat\nMODE: exact\n")
    (skill / ".clio" / "manifest.json").write_text("{}")
    client = _FakeClient([_VALID_CLIO])
    generate(skill, client=client)
    user_msg = client.messages.calls[0]["messages"][0]["content"]
    assert "cheat" not in user_msg
    assert ".clio/" not in user_msg


def test_generate_retry_message_includes_error_and_attempt(tmp_path):
    from clio.skill_to_clio import generate

    invalid = "STEP\n  MODE: exact\n"
    client = _FakeClient([invalid, _VALID_CLIO])
    generate(_tiny_skill(tmp_path), client=client)
    second = client.messages.calls[1]["messages"]
    # Conversation now contains [user_original, assistant_invalid, user_retry]
    assert second[1]["role"] == "assistant"
    assert second[1]["content"] == invalid
    assert second[2]["role"] == "user"
    assert "did not parse" in second[2]["content"]
    assert invalid in second[2]["content"]


def test_generate_aborts_when_payload_too_large(tmp_path):
    from clio.skill_to_clio import GenerationError, generate

    skill = _tiny_skill(tmp_path)
    # Force a >180k tokens payload
    (skill / "big.md").write_text("x" * (181_000 * 4))
    client = _FakeClient([_VALID_CLIO])  # would-be response; never used
    with pytest.raises(GenerationError, match="too large"):
        generate(skill, client=client)
    assert len(client.messages.calls) == 0


def test_generate_system_prompt_loaded_from_file(tmp_path):
    from clio.skill_to_clio import generate

    client = _FakeClient([_VALID_CLIO])
    generate(_tiny_skill(tmp_path), client=client)
    system_block_text = client.messages.calls[0]["system"][0]["text"]
    # The first words of clio/prompts/skill_to_clio_system.md
    assert "CLIO's skill importer" in system_block_text
