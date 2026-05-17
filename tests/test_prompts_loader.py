import uuid
from pathlib import Path

import pytest


def test_load_prompt_reads_named_file():
    from clio import prompts

    name = f"_test_loader_smoke_{uuid.uuid4().hex[:8]}"
    real_dir = Path(prompts.__file__).parent
    (real_dir / f"{name}.md").write_text("hello prompt\n")
    try:
        prompts.load_prompt.cache_clear()
        assert prompts.load_prompt(name) == "hello prompt\n"
    finally:
        (real_dir / f"{name}.md").unlink()
        prompts.load_prompt.cache_clear()


def test_load_prompt_raises_on_missing_file():
    from clio import prompts

    prompts.load_prompt.cache_clear()
    with pytest.raises(FileNotFoundError):
        prompts.load_prompt("does_not_exist_anywhere")


def test_load_prompt_is_cached():
    from clio import prompts

    prompts.load_prompt.cache_clear()
    name = f"_test_cache_smoke_{uuid.uuid4().hex[:8]}"
    real_dir = Path(prompts.__file__).parent
    target = real_dir / f"{name}.md"
    target.write_text("v1\n")
    try:
        assert prompts.load_prompt(name) == "v1\n"
        target.write_text("v2\n")
        assert prompts.load_prompt(name) == "v1\n"
    finally:
        target.unlink()
        prompts.load_prompt.cache_clear()


def test_nl_to_clio_system_prompt_loads_from_disk():
    from clio.prompts import load_prompt
    load_prompt.cache_clear()
    body = load_prompt("nl_to_clio_system")
    # Sanity markers preserved from the previous inline constants
    assert "You are CLIO" in body
    assert "Output ONLY a valid .clio source" in body
    assert "ERROR:" in body
    # The template MUST contain the four placeholders the assembly relies on
    for placeholder in ("{spec}", "{mvp}", "{entities}", "{classify}"):
        assert placeholder in body


def test_nl_to_clio_retry_prompt_loads_from_disk():
    from clio.prompts import load_prompt
    load_prompt.cache_clear()
    body = load_prompt("nl_to_clio_retry")
    assert "did not parse" in body
    assert "{previous_attempt}" in body
    assert "{error}" in body


def test_skill_to_clio_system_prompt_has_all_required_sections():
    from clio.prompts import load_prompt
    load_prompt.cache_clear()
    body = load_prompt("skill_to_clio_system")
    # Section markers — keep these stable so future edits don't accidentally
    # drop a required section.
    for marker in [
        "Role and output format",
        "CLIO grammar reference",
        "Mapping rules",
        "Annotation rules",
        "Output language policy",
    ]:
        assert marker in body, f"missing required section: {marker}"


def test_skill_to_clio_system_prompt_mentions_clio_import_annotation():
    from clio.prompts import load_prompt
    load_prompt.cache_clear()
    body = load_prompt("skill_to_clio_system")
    assert "# CLIO-import:" in body
    # Authoritative-source guidance for process_flow.dot
    assert "process_flow.dot" in body


def test_skill_to_clio_retry_prompt_has_placeholders():
    from clio.prompts import load_prompt
    load_prompt.cache_clear()
    body = load_prompt("skill_to_clio_retry")
    assert "{previous_attempt}" in body
    assert "{error}" in body
