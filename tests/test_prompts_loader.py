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
