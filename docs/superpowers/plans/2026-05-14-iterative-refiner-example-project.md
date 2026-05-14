# Iterative Refiner Example Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `examples/projects/01-iterative-refiner/` — the first self-contained example project for CLIO. README + `flow.clio` (writer + critic refine loop with bounded `WHILE`) + `data/article.txt` (~600-word penicillin discovery piece) + committed `--target python` compiled output + `rebuild.sh` + pytest drift guard. Plus targeted updates to cookbook, examples catalog, and CHANGELOG.

**Architecture:** Pure additive content under `examples/projects/01-iterative-refiner/`. No compiler changes. The CI invariant — a pytest that recompiles `flow.clio` to a temp dir and diffs against the checked-in `expected_output/` — keeps the compiled artefact honest as the emitter evolves. Branch already exists: `docs/example-01-iterative-refiner`. Spec at `docs/superpowers/specs/2026-05-14-iterative-refiner-example-project-design.md`.

**Tech Stack:** Python 3.12+, CLIO compiler (v0.15.0), pytest, ruff. No new dependencies.

---

## File Map

**Created:**
- `examples/projects/01-iterative-refiner/data/article.txt` — ~600-word historical-science piece
- `examples/projects/01-iterative-refiner/flow.clio` — CLIO source, 2 CONTRACTs / 5 STEPs / 1 WHILE / 1 TEST
- `examples/projects/01-iterative-refiner/expected_output/**` — output of `clio compile --target python`
- `examples/projects/01-iterative-refiner/rebuild.sh` — regenerate + diff
- `examples/projects/01-iterative-refiner/README.md` — user-facing project guide
- `examples/projects/01-iterative-refiner/TESTING.md` — maintainer note on the drift invariant
- `tests/test_examples/__init__.py` — empty package marker
- `tests/test_examples/test_iterative_refiner_drift.py` — CI drift guard

**Modified:**
- `docs/manual/03-cookbook.md` — new "Refine loop (writer + critic)" recipe section
- `examples/README.md` — new top-of-file "Project examples" section
- `CHANGELOG.md` — one-line entry under `## Unreleased` → `### Examples`

**Out of scope (per spec):** no `clio/**` changes, no `pyproject.toml` change, no new top-level Makefile.

---

## Pre-flight check

Run these once before starting, fail loudly if any returns unexpected output.

```bash
git branch --show-current        # expected: docs/example-01-iterative-refiner
git status --short               # expected: empty (clean) — design commit already landed
uv run python -m clio --help     # confirms CLIO compiler is installed and importable
uv run pytest tests/ --collect-only -q 2>&1 | tail -3   # baseline test count
```

If any check fails: stop and report.

---

### Task 1: Write the article (penicillin discovery, ~600 words)

**Files:**
- Create: `examples/projects/01-iterative-refiner/data/article.txt`

**Rationale:** The article exists before the `.clio` so the prompts in `flow.clio` (especially the STRATEGIES for the critic) can reference concrete facts a reader can verify against the text. Five paragraphs, one per beat: discovery / dormancy / Oxford rescue / wartime scale-up / Nobel. End with a "Sources" footer block in `#`-style comment so it survives the `cat` step at runtime (the loader treats the whole file as the `article` string — the footer becomes part of the input).

Length target: 550-650 words **excluding** the footer (the footer is meta, not summarisable content). One blank line between paragraphs. Plain ASCII (no smart quotes, no em-dashes), so diffs stay clean across editors.

- [ ] **Step 1: Create the directory and write the article**

```bash
mkdir -p examples/projects/01-iterative-refiner/data
```

Then write `examples/projects/01-iterative-refiner/data/article.txt` with this exact content:

```text
The discovery of penicillin is the story of a chance observation followed by
sixteen years of unglamorous work that turned a laboratory curiosity into the
drug that ended the era of routine death from bacterial infection.

In September 1928, Alexander Fleming returned to his laboratory at St Mary's
Hospital in London after a holiday and noticed that a Petri dish of
Staphylococcus had been contaminated by a blue-green mould. Around the mould,
the bacterial colonies were dissolving. Fleming identified the mould as a
Penicillium species and named the antibacterial substance it secreted
"penicillin". He published the observation in 1929 in the British Journal of
Experimental Pathology, then largely set the work aside; the substance was
unstable, hard to purify, and Fleming was not a chemist.

For nearly a decade penicillin remained an isolated curiosity. The mould juice
was difficult to concentrate, the active compound degraded within days, and
the small amounts that could be produced were too dilute to test on animals
in any rigorous way. Several teams tried; all gave up.

The turning point came at the University of Oxford in 1939, when Howard
Florey, an Australian pathologist, and Ernst Chain, a German biochemist who
had fled Nazi Germany, decided to revisit the substance with modern
biochemical methods. Working with Norman Heatley, they devised freeze-drying
and back-extraction techniques that allowed them to produce stable, dry
penicillin powder. In May 1940 they tested it on eight mice infected with a
lethal dose of streptococci: the four treated mice survived, the four
untreated died within sixteen hours. The first human trial followed in
February 1941 on Albert Alexander, a policeman dying of a face infection; he
improved dramatically until the small supply of the drug ran out, at which
point the infection returned and he died. The experiment proved efficacy and
proved that scale was the next problem.

Britain, under wartime conditions, lacked the industrial capacity to scale
production. In the summer of 1941 Florey and Heatley travelled to the United
States. With the wartime Office of Scientific Research and Development as
broker, four American pharmaceutical companies — Merck, Squibb, Pfizer, and
Lederle — were enlisted to develop deep-tank fermentation methods. By 1944,
penicillin was produced at industrial scale and shipped to Allied front-line
hospitals. The standard estimate is that penicillin saved between twelve and
fifteen percent of Allied soldiers who would otherwise have died of wound
infections during the final year of the war.

In 1945 Fleming, Florey, and Chain shared the Nobel Prize in Physiology or
Medicine "for the discovery of penicillin and its curative effect in various
infectious diseases". Fleming, characteristically self-deprecating, used his
acceptance lecture to warn that the routine use of antibiotics would
inevitably select for resistant bacteria — a prediction confirmed within a
decade. The drug that ended one era opened a slower, still-unfinished problem.

# Sources (paraphrased and rewritten from public material):
# - Wikipedia, "History of penicillin" (CC BY-SA)
# - Wikipedia, "Alexander Fleming", "Howard Florey", "Ernst Chain"
# - Eric Lax, "The Mould in Dr Florey's Coat" (Henry Holt, 2004)
# - Robert Bud, "Penicillin: Triumph and Tragedy" (Oxford University Press, 2007)
```

- [ ] **Step 2: Verify word count and ASCII-only**

Run:
```bash
wc -w examples/projects/01-iterative-refiner/data/article.txt
LC_ALL=C grep -P '[^\x00-\x7f]' examples/projects/01-iterative-refiner/data/article.txt && echo "NON-ASCII FOUND" || echo "ascii ok"
```

Expected: word count between 550 and 650; `ascii ok`. The footer counts in `wc -w` but the ~600-word target refers to the body — at ~595 words body + ~30 words footer ≈ 625 total, which falls in range.

- [ ] **Step 3: Commit**

```bash
git add examples/projects/01-iterative-refiner/data/article.txt
git commit -m "examples(01-iterative-refiner): add penicillin article"
```

---

### Task 2: Write `flow.clio` and validate it parses

**Files:**
- Create: `examples/projects/01-iterative-refiner/flow.clio`

**Rationale:** Single FLOW, two CONTRACTs, five STEPs, one WHILE, one TEST. Per the spec the source must compile under `--target python` without errors, which we verify with `clio check` before going further.

- [ ] **Step 1: Create `flow.clio`**

Write `examples/projects/01-iterative-refiner/flow.clio` with this exact content:

```
# 01-iterative-refiner — writer + critic refine loop on a single article.
#
# Two LLM roles cooperate: a writer drafts and refines a summary, a critic
# scores it on fidelity (no claims absent from the source) and coverage
# (key points of the source represented). The loop terminates when the
# critic's score crosses 0.85 or after 3 refine passes.

CONTRACT summary_judgment
  SHAPE:  {
    score:          float,
    missing_points: List<str(max=200)>(max=5),
    verdict:        enum(accept|refine)
  }
  ASSERT: 0.0 <= score <= 1.0

CONTRACT final_summary
  SHAPE: {
    text:        str(max=4000),
    iterations:  int,
    final_score: float
  }
  ASSERT: iterations >= 1

STEP load_article
  TAKES: file:    str
  GIVES: article: str
  MODE:  exact

STEP draft_summary
  TAKES:       article: str
  GIVES:       draft:   str
  MODE:        judgment
  DESCRIPTION: "Write the first draft of a faithful 150-200 word summary of the article."
  STRATEGIES:  "Cover the main historical facts (who, when, what, why it mattered). Do not introduce claims absent from the source."
  CACHE:       ttl(7d)
  ON_FAIL:     retry(3) then escalate then abort("draft_summary failed")
  invoke:
    mode:  api
    model: sonnet

STEP judge_summary
  TAKES:       article: str, draft: str
  GIVES:       judgment: summary_judgment
  MODE:        judgment
  DESCRIPTION: "Score the draft against the article on fidelity (no hallucination) and coverage (key facts represented)."
  STRATEGIES:  "score = mean(fidelity, coverage). Fidelity = 1 if no claim in the draft is absent from the article; deduct for each unsupported claim. Coverage = 1 if every key fact of the article appears; deduct for each missing key fact. verdict = 'accept' iff score >= 0.85, else 'refine'. missing_points lists up to 5 short labels of facts the writer should add or correct."
  CACHE:       ttl(7d)
  ON_FAIL:     retry(3) then escalate then abort("judge_summary failed")
  invoke:
    mode:  api
    model: haiku

STEP refine_summary
  TAKES:       article: str, draft: str, judgment: summary_judgment
  GIVES:       draft:    str
  MODE:        judgment
  DESCRIPTION: "Revise the draft to address judgment.missing_points while keeping it faithful to the article and within ~200 words."
  STRATEGIES:  "Read judgment.missing_points and integrate each item in the revised draft. Do not invent supporting details. If a missing_point cannot be supported by the article, omit it rather than fabricate. Ignore judgment.score; the loop terminates on it, the writer does not need to react to it."
  CACHE:       ttl(7d)
  ON_FAIL:     retry(3) then escalate then abort("refine_summary failed")
  invoke:
    mode:  api
    model: sonnet

STEP finalize
  TAKES: draft:    str, judgment: summary_judgment
  GIVES: result:   final_summary
  MODE:  exact

FLOW iterative_refiner
    load_article(file="article.txt")
    -> draft_summary(article=article)
    -> judge_summary(article=article, draft=draft)
    -> WHILE judgment.score < 0.85 and judgment.verdict == "refine" MAX 3:
        refine_summary(article=article, draft=draft, judgment=judgment)
        -> judge_summary(article=article, draft=draft)
    -> finalize(draft=draft, judgment=judgment)

TEST refine_loop_terminates_with_known_article
  FLOW: iterative_refiner
  WITH:
    file: "data/article.txt"
  EXPECTS:
    result:   not_empty
    draft:    not_empty
    judgment: not_empty

RESOURCES
  target: python
  models: [haiku, sonnet, opus]
```

- [ ] **Step 2: Validate with `clio check`**

Run:
```bash
uv run python -m clio check examples/projects/01-iterative-refiner/flow.clio
```

Expected: exit code 0, no errors. If the parser reports a grammar issue (especially around `WHILE … and … MAX 3:` indentation, per-step `invoke:` block, or the `TEST … EXPECTS` block), stop and report — the spec assumed v0.15 grammar and a mismatch means either the spec or the parser disagrees and the design needs to be revisited rather than worked around.

- [ ] **Step 3: Dry-run compile to a temp dir (no commit yet)**

Run:
```bash
tmp=$(mktemp -d) && \
uv run python -m clio compile examples/projects/01-iterative-refiner/flow.clio \
  --target python --output "$tmp" && \
echo "compile ok, output in $tmp"
```

Expected: exit code 0, message confirms the temp dir contains `flow.py`, `contracts.py`, `steps/`, `prompts/`, `schemas/`, and `tests/`. Note the `$tmp` path — Task 3 will redo this into the canonical `expected_output/` location.

- [ ] **Step 4: Commit**

```bash
git add examples/projects/01-iterative-refiner/flow.clio
git commit -m "examples(01-iterative-refiner): add flow.clio (writer+critic refine loop)"
```

---

### Task 3: Generate and commit `expected_output/`

**Files:**
- Create: `examples/projects/01-iterative-refiner/expected_output/**` (full python-target output)

**Rationale:** This is the artefact the reader sees on GitHub before running anything. We compile **from the project directory** so the relative paths inside the emitted files (the `data/article.txt` reference in the emitted test) line up.

- [ ] **Step 1: Compile to the canonical location**

Run from the repo root:
```bash
uv run python -m clio compile \
  examples/projects/01-iterative-refiner/flow.clio \
  --target python \
  --output examples/projects/01-iterative-refiner/expected_output/
```

Expected: exit code 0. The compiler writes the full python target tree under `expected_output/`. Inspect with `ls examples/projects/01-iterative-refiner/expected_output/` to confirm `pyproject.toml`, `flow.py`, `contracts.py`, `steps/`, `prompts/`, `schemas/`, and `tests/` are present.

- [ ] **Step 2: Sanity-check the emitted flow.py contains the WHILE**

Run:
```bash
grep -n "for _i in range(3)\|for i in range(3)\|while " \
  examples/projects/01-iterative-refiner/expected_output/flow.py
```

Expected: at least one match — the python emitter renders `WHILE … MAX 3:` as a bounded `for _i in range(3): if not cond: break; body` pattern (per `LANGUAGE_SPEC.md:819`). If no match, the WHILE didn't survive emission and we need to fix the source before continuing.

- [ ] **Step 3: Sanity-check both models are referenced**

Run:
```bash
grep -E "sonnet|haiku" examples/projects/01-iterative-refiner/expected_output/steps/*.py | sort -u
```

Expected: at least one `sonnet` reference (from `draft_summary.py` and `refine_summary.py`) and at least one `haiku` reference (from `judge_summary.py`). Confirms per-step `invoke.model` overrides made it through emission.

- [ ] **Step 4: Sanity-check the TEST emitted to pytest**

Run:
```bash
ls examples/projects/01-iterative-refiner/expected_output/tests/
```

Expected: a file named `test_refine_loop_terminates_with_known_article.py` (or close — the emitter may prefix with `test_`). Confirms TEST block survived emission.

- [ ] **Step 5: Commit the compiled output**

```bash
git add examples/projects/01-iterative-refiner/expected_output/
git commit -m "examples(01-iterative-refiner): commit compiled --target python output"
```

---

### Task 4: TDD — drift guard test fails, then `rebuild.sh` makes it pass

**Files:**
- Create: `tests/test_examples/__init__.py`
- Create: `tests/test_examples/test_iterative_refiner_drift.py`
- Create: `examples/projects/01-iterative-refiner/rebuild.sh`

**Rationale:** This is the only mechanism that keeps the checked-in `expected_output/` honest. We write the test first (it fails because `rebuild.sh` doesn't exist yet), then write `rebuild.sh`, then verify the test passes. Two commits to keep the red→green visible in history.

- [ ] **Step 1: Create the test package and the failing test**

Run:
```bash
mkdir -p tests/test_examples
touch tests/test_examples/__init__.py
```

Write `tests/test_examples/test_iterative_refiner_drift.py` with this exact content:

```python
"""Drift guard: `examples/projects/01-iterative-refiner/expected_output/`
must match what `clio compile --target python` produces right now.

If the emitter changes the bytes it produces for this flow, the maintainer
who made the emitter change must regenerate `expected_output/` and commit
it in the same PR. The diff in PR-review then makes the impact visible.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_DIR = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "projects"
    / "01-iterative-refiner"
)


def test_iterative_refiner_expected_output_is_up_to_date() -> None:
    script = PROJECT_DIR / "rebuild.sh"
    assert script.is_file(), f"rebuild.sh not found at {script}"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "rebuild.sh reported drift between flow.clio and expected_output/.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
uv run pytest tests/test_examples/test_iterative_refiner_drift.py -v
```

Expected: FAIL with `AssertionError: rebuild.sh not found at .../examples/projects/01-iterative-refiner/rebuild.sh`. This proves the test is wired to the right file and the assertion is meaningful.

- [ ] **Step 3: Create `rebuild.sh`**

Write `examples/projects/01-iterative-refiner/rebuild.sh` with this exact content:

```bash
#!/usr/bin/env bash
# Regenerate expected_output/ from flow.clio and verify there is no drift.
# Exit 0 if expected_output/ matches a fresh compile, exit 1 otherwise.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

(
  cd "$repo_root"
  uv run python -m clio compile \
    "$here/flow.clio" \
    --target python \
    --output "$tmp"
)

if diff -r --brief "$here/expected_output/" "$tmp" > /dev/null; then
  echo "expected_output/ is up to date."
  exit 0
fi

echo "Drift detected between expected_output/ and a fresh compile."
echo "To accept the new output:"
echo "  rm -rf '$here/expected_output' && cp -r '$tmp' '$here/expected_output'"
exit 1
```

Then make it executable:
```bash
chmod +x examples/projects/01-iterative-refiner/rebuild.sh
```

- [ ] **Step 4: Run the test and verify it passes**

Run:
```bash
uv run pytest tests/test_examples/test_iterative_refiner_drift.py -v
```

Expected: PASS. If it fails with "Drift detected", the most likely cause is non-determinism in the emitter output (e.g. timestamp in pyproject.toml). Diff manually:
```bash
tmp=$(mktemp -d) && \
uv run python -m clio compile examples/projects/01-iterative-refiner/flow.clio \
  --target python --output "$tmp" && \
diff -r examples/projects/01-iterative-refiner/expected_output/ "$tmp"
```
…and report what differs before continuing. (If non-determinism is real, the right fix is in the emitter, in a separate PR — pause this work and surface it.)

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run:
```bash
uv run pytest tests/ -q --tb=no
```

Expected: all previously passing tests still pass; new test passes; count went up by 1.

- [ ] **Step 6: Commit**

```bash
git add tests/test_examples/__init__.py \
        tests/test_examples/test_iterative_refiner_drift.py \
        examples/projects/01-iterative-refiner/rebuild.sh
git commit -m "examples(01-iterative-refiner): add rebuild.sh + drift guard test"
```

---

### Task 5: Write the project README

**Files:**
- Create: `examples/projects/01-iterative-refiner/README.md`

**Rationale:** Project-level entry point. Six sections per the spec: what / what you'll see / prereqs / run / inspect / tweak / cross-ref. Do **not** embed a sample LLM transcript: it would be non-deterministic and stale. Describe the shape of a run instead.

- [ ] **Step 1: Write the README**

Write `examples/projects/01-iterative-refiner/README.md` with this exact content:

````markdown
# 01-iterative-refiner — writer + critic refine loop

A complete CLIO project that summarises a single article in a feedback loop
between two LLM roles:

- a **writer** (`sonnet`) drafts then revises the summary,
- a **critic** (`haiku`) scores each draft on fidelity (no hallucinated
  claims) and coverage (key facts present) and lists what's missing.

The loop iterates until the critic's score crosses `0.85` or after `3` refine
passes — whichever happens first.

It demonstrates two CLIO primitives no other example covers end-to-end today:

1. **`WHILE … MAX N`** with a composed condition (`score < 0.85 and verdict == "refine"`).
2. **Per-step `invoke: { model: ... }`** — different LLMs for different roles
   in the same flow.

## What you'll see when it runs

A single run prints, in order:

1. The article is loaded from `data/article.txt`.
2. `draft_summary` returns a ~200-word first draft.
3. `judge_summary` returns a `summary_judgment` record: a numeric score, a
   verdict (`accept` or `refine`), and up to five `missing_points`.
4. If the verdict is `refine`, `refine_summary` rewrites the draft, and
   `judge_summary` re-scores. Repeat up to three times.
5. `finalize` packages the last draft into a `final_summary` record:
   `{text, iterations, final_score}`.

The exact iteration count varies run-to-run; both an early-accept (one pass)
and a `MAX`-reached (four passes) outcome are normal. The point of the
example is the loop **terminates**; the loop **producing a Nobel-grade
summary** is not its job.

## Prerequisites

From the repo root:

```bash
uv pip install -e .[dev]
export ANTHROPIC_API_KEY=sk-ant-...
```

That's the entire setup. No vector store, no extra service, no separate
process — just a Python virtualenv and an Anthropic key.

## Run it

```bash
cd examples/projects/01-iterative-refiner
uv pip install ./expected_output
iterative_refiner --file data/article.txt
cat state.json | jq .result
```

The compiled package's CLI entry point is named after the flow
(`iterative_refiner`). State is written to `state.json` in the current
directory; `--from-step <name>` resumes a partial run.

## Inspect the compiled output

The whole point of the `expected_output/` directory being committed is that
you can read the result before running it.

- `expected_output/flow.py` — the orchestrator. Look for the `for _i in
  range(3)` loop with `if not cond: break` — that's the `WHILE … MAX 3:`
  primitive after compilation.
- `expected_output/steps/judge_summary.py` — typed Pydantic body, contract
  enforcement, `anthropic.Anthropic().messages.create(...)` call with
  `model="claude-haiku-4-5-20251001"` (or whatever CLIO's `haiku` alias
  resolves to at compile time).
- `expected_output/prompts/judge_summary.md` — the critic's full system
  prompt, including the `DESCRIPTION` and `STRATEGIES` text from the source.
- `expected_output/schemas/summary_judgment.json` — the JSON schema the
  critic's response is validated against.

## Tweak the source

Three one-line edits to try once you've run the example once:

| Try | In `flow.clio` | Effect |
|---|---|---|
| Stricter quality bar | `WHILE judgment.score < 0.95` (was `0.85`) | Loop almost always hits `MAX 3`; more API rounds, fewer early-accepts. |
| Swap the critic to `sonnet` | `STEP judge_summary` → `invoke: model: sonnet` | More expensive critic, usually higher scores per draft, fewer iterations. |
| Add a `fluency` field | `CONTRACT summary_judgment` → add `fluency: float` | The critic now returns three scores; the loop condition can be reweighted. |

After any edit, run `bash rebuild.sh` to regenerate `expected_output/`. The
drift-guard test in `tests/test_examples/` will fail until you do.

## Where this fits

- **Manual recipe:** [`docs/manual/03-cookbook.md`](../../../docs/manual/03-cookbook.md) — the "Refine loop (writer + critic)" section is a 15-line distillation of this project's `flow.clio`.
- **Language reference:** [`docs/LANGUAGE_SPEC.md`](../../../docs/LANGUAGE_SPEC.md#while-v07-composed-in-v012) — `WHILE … MAX N` semantics; [per-step `invoke:`](../../../docs/LANGUAGE_SPEC.md#judgment-invocation-invoke-block) block.
- **Other examples** in `examples/` cover linear pipelines (`mvp.clio`,
  `entities.clio`), routing (`feedback_routing.clio`), parallelism
  (`classify_corpus.clio`), and RAG-like retrieval (`rag_basic.clio`). This
  is the first **project** with committed compiled output.
````

- [ ] **Step 2: Verify the README renders sensibly**

Run:
```bash
head -10 examples/projects/01-iterative-refiner/README.md
wc -l examples/projects/01-iterative-refiner/README.md
```

Expected: clean markdown header lines; ~115-130 lines total.

- [ ] **Step 3: Commit**

```bash
git add examples/projects/01-iterative-refiner/README.md
git commit -m "examples(01-iterative-refiner): add project README"
```

---

### Task 6: Write `TESTING.md`

**Files:**
- Create: `examples/projects/01-iterative-refiner/TESTING.md`

**Rationale:** Short maintainer-facing note that explains why `expected_output/` is checked in and how the CI invariant works. Not user-facing — sits next to `rebuild.sh` for the next maintainer to read.

- [ ] **Step 1: Write `TESTING.md`**

Write `examples/projects/01-iterative-refiner/TESTING.md` with this exact content:

```markdown
# TESTING — `expected_output/` invariant

This project commits the result of `clio compile --target python …`
directly under `expected_output/`. The reader sees the compiled artefact
on GitHub before running anything; that's deliberate, and worth a tiny
amount of maintenance discipline.

## The invariant

After any change that affects the python emitter — or any change to this
project's `flow.clio` — `expected_output/` must match what the current
compiler produces. The check is:

```bash
bash examples/projects/01-iterative-refiner/rebuild.sh
```

Exit 0 means up to date. Exit 1 prints the `cp -r` command that accepts
the new output.

## How CI enforces it

`tests/test_examples/test_iterative_refiner_drift.py` runs `rebuild.sh`
as a subprocess and asserts exit code 0. It runs as part of the default
`pytest tests/` invocation, so PRs that change the emitter without
regenerating this project's output fail their test suite.

## When it fires (and what to do)

- **You changed the python emitter.** Run `rebuild.sh`, accept the diff
  with the `cp -r` it suggests, and commit the regenerated
  `expected_output/` in the same PR as the emitter change. PR-review
  then sees both the emitter diff and its effect.
- **You changed this project's `flow.clio`.** Same workflow.
- **The test failed and you changed nothing relevant.** Likely
  non-determinism in the emitter (a timestamp, a hash). That's a real
  bug — open a separate issue rather than working around it here.
```

- [ ] **Step 2: Commit**

```bash
git add examples/projects/01-iterative-refiner/TESTING.md
git commit -m "examples(01-iterative-refiner): add TESTING.md maintainer note"
```

---

### Task 7: Add the "Refine loop" recipe to the cookbook

**Files:**
- Modify: `docs/manual/03-cookbook.md`

**Rationale:** The manual is the canonical learning path. A reader who finishes the cookbook should know that refine loops exist and where the full project lives. Per memory rule "Update manual on features", we touch the manual whenever we add a user-visible feature; this is one.

- [ ] **Step 1: Inspect the current cookbook structure**

Run:
```bash
grep -n "^## " docs/manual/03-cookbook.md
wc -l docs/manual/03-cookbook.md
```

Expected: a flat list of `## Recipe: …` or similar second-level sections. Note where to insert the new section — by convention "after the last recipe, before any closing/cross-reference section".

- [ ] **Step 2: Add the new section**

Append a new section at the natural insertion point. The block to insert is:

````markdown

## Recipe: Refine loop (writer + critic)

When you want an LLM to revise its own output until a quality bar is met,
the canonical CLIO pattern is two judgment steps in a bounded `WHILE` loop:
one writer step, one critic step.

```
CONTRACT verdict
  SHAPE: {score: float, missing_points: List<str>(max=5), verdict: enum(accept|refine)}
  ASSERT: 0.0 <= score <= 1.0

STEP draft   ... MODE: judgment   invoke: { mode: api, model: sonnet }
STEP judge   ... GIVES: v: verdict   MODE: judgment   invoke: { mode: api, model: haiku }
STEP refine  ... TAKES: ..., v: verdict   MODE: judgment   invoke: { mode: api, model: sonnet }

FLOW refine_loop
    draft(...)
    -> judge(...)
    -> WHILE v.score < 0.85 and v.verdict == "refine" MAX 3:
        refine(..., v=v)
        -> judge(...)
    -> finalize(...)
```

**Three things to notice:**

1. **The critic returns a record, not just a score.** Passing the whole
   `verdict` record into `refine` is how the writer reads
   `missing_points` — kwargs at the flow level are simple identifiers,
   not dotted paths (the latter is only allowed inside `RESCUE` bodies).
2. **The body re-judges at the end of each pass.** Without that re-judge
   the loop condition would be stale and `WHILE` could run all `MAX`
   times even after acceptance.
3. **`MAX` is a hard ceiling.** A loop that exhausts `MAX 3` without
   reaching `score >= 0.85` still produces a valid final draft —
   `MAX`-reached is normal output, not failure. Use a `final_summary`
   contract with an `iterations: int` field if you want to surface
   this to callers.

A complete, runnable project: [`examples/projects/01-iterative-refiner/`](../../examples/projects/01-iterative-refiner/). The committed `expected_output/` lets you read the compiled `flow.py` (the `WHILE` becomes a bounded `for _i in range(3): if not cond: break` loop) without installing anything.
````

Run a quick spot-check to confirm the insertion didn't break anything:
```bash
head -20 docs/manual/03-cookbook.md
tail -30 docs/manual/03-cookbook.md
```

Expected: header and trailing lines unchanged; the new section sits where intended.

- [ ] **Step 3: Commit**

```bash
git add docs/manual/03-cookbook.md
git commit -m "docs(manual): add Refine loop recipe + link to 01-iterative-refiner"
```

---

### Task 8: Add "Project examples" pointer to `examples/README.md`

**Files:**
- Modify: `examples/README.md`

**Rationale:** The existing `examples/README.md` is a flat catalog. We add a short top-section that flags the new `projects/` family without touching the existing catalog body.

- [ ] **Step 1: Read the current top of `examples/README.md`**

Run:
```bash
head -10 examples/README.md
```

Note the current first heading.

- [ ] **Step 2: Insert the new section directly after the top-level `# CLIO examples` heading and intro paragraph**

The block to insert (immediately after the first paragraph of the file, before the first `## 1. …` section):

````markdown

## Project examples

Self-contained example projects live under [`projects/`](projects/). Each
project bundles its own `README.md`, `flow.clio`, input data, and the
**compiled `--target python` output committed alongside** — so you can read
the result on GitHub before running anything.

- [`projects/01-iterative-refiner/`](projects/01-iterative-refiner/) — writer + critic refine loop with bounded `WHILE … MAX 3`. The first project example, and the first end-to-end use of `WHILE` plus per-step `invoke.model` in any CLIO example.

The flat `.clio` files below remain the right format for short, single-concept demos.
````

After insertion, verify with:
```bash
head -25 examples/README.md
```

Expected: the new "## Project examples" section sits between the intro and the existing "## 1. `mvp.clio`…" section.

- [ ] **Step 3: Commit**

```bash
git add examples/README.md
git commit -m "docs(examples): add Project examples section to catalog"
```

---

### Task 9: Update `CHANGELOG.md`

**Files:**
- Modify: `CHANGELOG.md`

**Rationale:** One-line entry per spec; lives under `## Unreleased` → `### Examples`. Create the subsection if it doesn't exist yet.

- [ ] **Step 1: Inspect the current Unreleased block**

Run:
```bash
awk '/^## Unreleased/,/^## /' CHANGELOG.md | head -40
```

Note whether `### Examples` already exists under `## Unreleased`.

- [ ] **Step 2: Add the entry**

If `### Examples` does **not** exist under `## Unreleased`: add the subsection and the line. If it exists: append the line as a new bullet under it.

The line to add:

```markdown
- Add `examples/projects/01-iterative-refiner/` — full project demonstrating the writer/critic refine loop with `WHILE … MAX 3` and per-step `invoke.model` overrides. Includes committed `--target python` output and a CI drift guard.
```

Verify:
```bash
awk '/^## Unreleased/,/^## /' CHANGELOG.md | grep -A2 "01-iterative-refiner"
```

Expected: the new bullet appears under `### Examples` inside the `## Unreleased` block.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): announce 01-iterative-refiner example project"
```

---

### Task 10: Final validation and push

**Files:** none modified — verification only.

**Rationale:** Memory rule "Run ruff before push" — CI gates pytest behind `ruff check`. Run both, fix anything that needs fixing, then push and open the PR.

- [ ] **Step 1: Run ruff**

Run:
```bash
uv run ruff check . --fix
```

Expected: exit 0, no remaining errors. If ruff fixed anything (likely: imports in `tests/test_examples/test_iterative_refiner_drift.py`), the changes are staged automatically by `--fix`; review with `git diff` and commit them with:
```bash
git add -u
git commit -m "chore: ruff --fix"
```

- [ ] **Step 2: Run the full test suite**

Run:
```bash
uv run pytest tests/ -q --tb=short
```

Expected: all tests pass, test count is exactly **previous baseline + 1** (the new drift guard). Any new failure in unrelated tests is a regression — stop and investigate before pushing.

- [ ] **Step 3: Confirm the branch is clean and view the commit log**

Run:
```bash
git status --short
git log --oneline main..HEAD
```

Expected: clean status; a chain of ~9 commits since `main` (design + 8 task commits + maybe ruff). Each commit message is one logical step.

- [ ] **Step 4: Push the branch**

Run:
```bash
git push -u origin docs/example-01-iterative-refiner
```

- [ ] **Step 5: Open the PR**

Run:
```bash
gh pr create --title "examples: add 01-iterative-refiner (writer+critic refine loop)" \
  --body "$(cat <<'EOF'
## Summary

- First entry of the new `examples/projects/<NN-name>/` family.
- Self-contained project that demonstrates the writer + critic refine loop pattern with `WHILE … MAX 3` and per-step `invoke.model` overrides — two CLIO features no other example covered end-to-end.
- Compiled `--target python` output is committed under `expected_output/` so the reader sees the result on GitHub before running anything.
- CI drift guard (`tests/test_examples/test_iterative_refiner_drift.py`) recompiles on every test run and fails if `expected_output/` is stale.
- Cookbook recipe added; examples catalog and CHANGELOG updated.

## Test plan

- [ ] `uv run ruff check .` — clean
- [ ] `uv run pytest tests/ -q` — all pass, count = baseline + 1
- [ ] `bash examples/projects/01-iterative-refiner/rebuild.sh` — exit 0
- [ ] Manual: read `examples/projects/01-iterative-refiner/README.md` and follow the "Run it" section with `ANTHROPIC_API_KEY` set; verify the flow completes and `state.json` contains a `result` field.

## Design + Plan

- Spec: `docs/superpowers/specs/2026-05-14-iterative-refiner-example-project-design.md`
- Plan: `docs/superpowers/plans/2026-05-14-iterative-refiner-example-project.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: the command prints a PR URL. Return that URL.

- [ ] **Step 6: Reply-to-Gemini watch**

Per memory rule: after Gemini posts review comments on the PR, reply in-thread to each one (applied or refused) citing the fix commit. This is a manual ongoing task, not a one-shot — it does not block plan completion, but the implementer should know to expect it.

---

## Self-review against the spec

(Run mentally before declaring the plan done — every spec section maps to at least one task.)

| Spec section | Implementing tasks |
|---|---|
| `flow.clio` source | Task 2 |
| `data/article.txt` | Task 1 |
| Output target — why `python` | Task 3 (sanity checks confirm per-step model overrides and TEST emission survived) |
| `expected_output/` committed | Task 3 |
| `rebuild.sh` | Task 4 step 3 |
| `README.md` | Task 5 |
| CI invariant | Task 4 (TDD: test fails, rebuild.sh makes it pass) |
| Manual / cookbook update | Task 7 |
| `examples/README.md` update | Task 8 |
| `CHANGELOG.md` entry | Task 9 |
| `TESTING.md` | Task 6 |
| "Open question — sequencing" | Resolved: Task 1 (article) before Task 2 (flow), then Task 3 (compile). |

No placeholders, no "TBD", every step contains the exact content the engineer needs.

One residual risk per the spec: if the python emitter introduces non-determinism in its output (timestamps, ordering, hashes), the drift test will flap. Task 4 step 4 surfaces this immediately and explicitly halts the work — that's the right behaviour because the fix belongs in the emitter, not in a workaround here.
