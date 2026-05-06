import pytest


def test_generation_error_carries_attempt_and_message():
    from clio.nl_to_clio import GenerationError
    e = GenerationError(last_attempt="STEP foo\n", last_error="line 1: oops")
    assert e.last_attempt == "STEP foo\n"
    assert e.last_error == "line 1: oops"
    assert "oops" in str(e)


def test_validate_returns_none_for_valid_source():
    from clio.nl_to_clio import _validate
    src = "STEP foo\n  MODE: exact\n"
    assert _validate(src) is None


def test_validate_returns_error_string_for_parse_error():
    from clio.nl_to_clio import _validate
    # Missing step name — should fail at parse time
    src = "STEP\n  MODE: exact\n"
    err = _validate(src)
    assert err is not None
    assert "line" in err.lower()


def test_validate_returns_error_string_for_ir_build_error():
    from clio.nl_to_clio import _validate
    # FLOW references a step that does not exist
    src = (
        "STEP a\n  MODE: exact\n"
        "FLOW f\n"
        '  nope()\n'
    )
    err = _validate(src)
    assert err is not None


def test_strip_no_fences_returns_input_unchanged():
    from clio.nl_to_clio import _strip_markdown_fences
    src = "STEP foo\n  MODE: exact\n"
    assert _strip_markdown_fences(src) == src


def test_strip_fenced_with_lang_tag():
    from clio.nl_to_clio import _strip_markdown_fences
    src = "```clio\nSTEP foo\n  MODE: exact\n```\n"
    assert _strip_markdown_fences(src) == "STEP foo\n  MODE: exact\n"


def test_strip_fenced_without_lang_tag():
    from clio.nl_to_clio import _strip_markdown_fences
    src = "```\nSTEP foo\n  MODE: exact\n```\n"
    assert _strip_markdown_fences(src) == "STEP foo\n  MODE: exact\n"


def test_strip_handles_leading_trailing_whitespace_around_fences():
    from clio.nl_to_clio import _strip_markdown_fences
    src = "\n\n```clio\nSTEP foo\n  MODE: exact\n```\n\n"
    assert _strip_markdown_fences(src) == "STEP foo\n  MODE: exact\n"


def test_system_prompt_contains_role_intro():
    from clio.nl_to_clio import _build_system_prompt
    prompt = _build_system_prompt()
    assert "CLIO" in prompt
    assert ".clio" in prompt


def test_system_prompt_contains_full_language_spec():
    from clio.nl_to_clio import _build_system_prompt
    prompt = _build_system_prompt()
    # A few markers that should be present somewhere in LANGUAGE_SPEC.md
    assert "Implementation status" in prompt
    assert "EXACT" in prompt
    assert "JUDGMENT" in prompt


def test_system_prompt_contains_three_repo_examples():
    from clio.nl_to_clio import _build_system_prompt
    prompt = _build_system_prompt()
    # mvp.clio markers
    assert "detect_churn" in prompt
    # entities.clio markers
    assert "extract_entities" in prompt
    # classify_corpus.clio markers
    assert "classify_corpus" in prompt
    assert "FOR EACH line IN lines" in prompt


def test_system_prompt_contains_output_rules():
    from clio.nl_to_clio import _build_system_prompt
    prompt = _build_system_prompt()
    assert "Output ONLY a valid .clio" in prompt
    assert "ERROR:" in prompt  # the ambiguity-refusal escape hatch


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


_VALID_CLIO = "STEP foo\n  MODE: exact\n"


def test_generate_returns_valid_clio_on_first_try():
    from clio.nl_to_clio import generate
    client = _FakeClient([_VALID_CLIO])
    out = generate("describe X", client=client)
    assert out == _VALID_CLIO
    assert len(client.messages.calls) == 1


def test_generate_passes_model_to_sdk():
    from clio.nl_to_clio import generate
    client = _FakeClient([_VALID_CLIO])
    generate("describe X", model="claude-opus-4-7", client=client)
    assert client.messages.calls[0]["model"] == "claude-opus-4-7"


def test_generate_uses_ephemeral_cache_control_on_system_prompt():
    from clio.nl_to_clio import generate
    client = _FakeClient([_VALID_CLIO])
    generate("describe X", client=client)
    system = client.messages.calls[0]["system"]
    # System is a list of blocks, the prompt block carries cache_control
    assert isinstance(system, list)
    assert any(
        block.get("cache_control") == {"type": "ephemeral"}
        for block in system
    )


def test_generate_passes_user_description_in_messages():
    from clio.nl_to_clio import generate
    client = _FakeClient([_VALID_CLIO])
    generate("describe X precisely", client=client)
    msgs = client.messages.calls[0]["messages"]
    assert msgs[0]["role"] == "user"
    assert "describe X precisely" in msgs[0]["content"]


def test_generate_strips_fences_from_llm_output():
    from clio.nl_to_clio import generate
    client = _FakeClient(["```clio\n" + _VALID_CLIO + "```\n"])
    out = generate("describe X", client=client)
    assert out == _VALID_CLIO


def test_generate_retries_on_parse_error_then_succeeds():
    from clio.nl_to_clio import generate
    invalid = "STEP\n  MODE: exact\n"  # missing name → parse error
    client = _FakeClient([invalid, _VALID_CLIO])
    out = generate("describe X", client=client)
    assert out == _VALID_CLIO
    assert len(client.messages.calls) == 2


def test_generate_retries_on_ir_build_error_then_succeeds():
    from clio.nl_to_clio import generate
    invalid = "STEP a\n  MODE: exact\nFLOW f\n  nope()\n"
    client = _FakeClient([invalid, _VALID_CLIO])
    out = generate("describe X", client=client)
    assert out == _VALID_CLIO
    assert len(client.messages.calls) == 2


def test_retry_message_includes_previous_attempt_and_error():
    from clio.nl_to_clio import generate
    invalid = "STEP\n  MODE: exact\n"
    client = _FakeClient([invalid, _VALID_CLIO])
    generate("describe X", client=client)

    second_messages = client.messages.calls[1]["messages"]
    # First message: user (original description)
    assert second_messages[0]["role"] == "user"
    assert "describe X" in second_messages[0]["content"]
    # Second message: assistant (previous attempt)
    assert second_messages[1]["role"] == "assistant"
    assert second_messages[1]["content"] == invalid
    # Third message: user (correction request with error)
    assert second_messages[2]["role"] == "user"
    correction = second_messages[2]["content"]
    assert "did not parse" in correction or "did not build" in correction
    assert invalid in correction


def test_max_retries_zero_disables_retry():
    from clio.nl_to_clio import GenerationError, generate
    invalid = "STEP\n  MODE: exact\n"
    client = _FakeClient([invalid])
    with pytest.raises(GenerationError):
        generate("describe X", client=client, max_retries=0)
    assert len(client.messages.calls) == 1
