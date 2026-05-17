from pathlib import Path

import pytest


def test_load_prompt_reads_named_file(tmp_path, monkeypatch):
    from clio import prompts

    # Place a temp prompt file inside the real prompts dir
    real_dir = Path(prompts.__file__).parent
    (real_dir / "_test_loader_smoke.md").write_text("hello prompt\n")
    try:
        # Bust the @cache between runs by clearing
        prompts.load_prompt.cache_clear()
        assert prompts.load_prompt("_test_loader_smoke") == "hello prompt\n"
    finally:
        (real_dir / "_test_loader_smoke.md").unlink()
        prompts.load_prompt.cache_clear()


def test_load_prompt_raises_on_missing_file():
    from clio import prompts

    prompts.load_prompt.cache_clear()
    with pytest.raises(FileNotFoundError):
        prompts.load_prompt("does_not_exist_anywhere")


def test_load_prompt_is_cached():
    from clio import prompts

    prompts.load_prompt.cache_clear()
    # Build a temp file we control
    real_dir = Path(prompts.__file__).parent
    target = real_dir / "_test_cache_smoke.md"
    target.write_text("v1\n")
    try:
        assert prompts.load_prompt("_test_cache_smoke") == "v1\n"
        target.write_text("v2\n")  # second read should still return v1
        assert prompts.load_prompt("_test_cache_smoke") == "v1\n"
    finally:
        target.unlink()
        prompts.load_prompt.cache_clear()
