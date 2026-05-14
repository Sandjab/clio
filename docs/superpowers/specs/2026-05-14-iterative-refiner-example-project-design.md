# Example project `01-iterative-refiner` — Design

Status: design proposed, awaiting user review.
Date: 2026-05-14.
Branch: `docs/example-01-iterative-refiner`.
Addresses: brainstorming session on enriching `examples/` with full **project examples** (vs flat snippets). First entry of the new `examples/projects/<NN-name>/` family — approach **A** of the three options sketched in the session.

## Goal

Ship the **first** self-contained example project that a new reader can read on GitHub without any local setup: a README that explains *what* and *why*, a `flow.clio` source, the **compiled output committed alongside** so the reader sees the result before running anything, and a one-line `rebuild.sh` to regenerate the output deterministically.

The project illustrates a pattern **no current example covers**: a **refine loop** with two cooperating LLM roles (writer + critic) and a bounded `WHILE` exit condition. Iterating until a critic-driven quality threshold is met (or `MAX` passes are exhausted) is one of the canonical agentic patterns; CLIO's `WHILE … MAX N` primitive was introduced in v0.7 and made fully composable in v0.12, but no example demonstrates it end-to-end today.

Concretely the reader gets, on disk and on GitHub:

```
examples/projects/01-iterative-refiner/
  README.md            # objective, prerequisites, step-by-step run-through
  flow.clio            # source — single FLOW, two CONTRACTs, five STEPs, one WHILE
  data/
    article.txt        # ~600-word historical-science piece (penicillin discovery)
  expected_output/     # checked-in result of `clio compile --target python`
    pyproject.toml
    flow.py
    contracts.py
    steps/*.py
    prompts/*.md       # judgment-step prompts
    schemas/*.json     # contract schemas
    tests/             # pytest emitted from the `TEST` block (see below)
  rebuild.sh           # `clio compile … --output /tmp/x && diff -r expected_output/ /tmp/x`
  TESTING.md           # short note on what "expected_output is up to date" means
```

## Non-goals

- **Modify the compiler.** Every language feature the project needs — `WHILE … MAX N` with composable `and`, per-step `invoke: { model: … }`, judgment contracts, `ON_FAIL` chains, `CACHE: ttl(...)`, `TEST` blocks — already lands in v0.15. This is a **pure example** drop, not a language sprint.
- **Cover all five emitter targets.** The committed output is `--target python` only. The flow compiles cleanly to `claude-cli` and `mcp-server` too (no `FOR EACH PARALLEL`, no SQL); the README mentions this in passing but we do not commit five copies of the compiled output.
- **Build a generic "harness for example projects".** Approach A pilots **one** project. The folder layout and the CI invariant are designed so the next project (e.g. `02-churn-detection` migrating `mvp.clio`) can clone the structure, but we do not factor anything out preemptively.
- **Ship a multi-article corpus or a JSONL dataset.** One article is enough to exercise the loop; adding more would dilute the lesson and inflate the diff in `expected_output/` for negligible pedagogical gain.
- **Embed evaluation metrics, BLEU/ROUGE, or a human-rating harness.** The critic's `score: float (0..1)` is the only quality signal the loop uses; we do not also produce an external metric. The loop terminating on `score ≥ 0.85` is the visible quality bar.
- **Re-package the user manual around the new projects.** The manual (`docs/manual/`) gets one cross-reference added to `03-cookbook.md` (the new "Refine loop" recipe section linking out to the project). No structural change to the manual hierarchy.

## Why this project, in this order

`examples/` today shows linear flows (`mvp`, `entities`), parallel `FOR EACH` (`classify_corpus`), routing with `IF`/`MATCH` (`feedback_routing`), and RAG-like retrieval (`rag_basic`). **No example shows `WHILE`.** And no example shows two LLM roles co-operating with distinct models. Both gaps close in one project.

The user choice to start the project series with this one (numbered `01-`) rather than starting with `mvp.clio` (now postponed to a later `NN-`) is deliberate: this is the project where the **modop** payoff is highest. A reader who already understands "linear pipeline with one LLM" gets little from re-seeing it in the new format; a reader who has never seen a critic-driven refine loop gets a complete worked example.

## Source — `flow.clio`

The full source, tight and self-contained. (Length matters here: the whole point of the example is that 70 lines of CLIO express a non-trivial dual-LLM refine loop. The reader reads the source in 30 seconds.)

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

### Notes on the source

- **Two `judgment` invocations, two models.** `draft_summary` and `refine_summary` use `sonnet` (richer writer); `judge_summary` uses `haiku` (fast scorer). The per-step `invoke: { mode: api, model: ... }` overrides the cheaper default that would otherwise be inferred from `RESOURCES.models`. This makes the cost/quality split visible in the source — a thing the reader can change in one line and observe.
- **`refine_summary` takes the whole `judgment`, not just `missing_points`.** CLIO's flow-level step calls require simple identifiers as kwarg values; dotted access (`judgment.missing_points`) is grammatically permitted only inside RESCUE bodies. Passing the whole `judgment: summary_judgment` is the idiomatic way to give a step access to a sub-field — and arguably more honest: the writer receives the critic's full verdict, not a pre-extracted slice. The STRATEGIES line is explicit about which sub-field to read and which to ignore.
- **The body of the `WHILE` is two steps.** `refine_summary` then `judge_summary`. The compiler does **not** validate that the body updates `judgment.score` (caller-side invariant, per spec); the example is a clean illustration of how to keep that invariant honest — re-judge at the end of each pass so the loop has a chance to terminate.
- **`CACHE: ttl(7d)` on every judgment step.** Cheap reproducibility: a reader running the example twice within a week pays for one API round, not two. The hash includes `missing_points`, so each refine pass is its own cache entry — no accidental cross-iteration collapse.
- **`ON_FAIL: retry(3) then escalate then abort(...)`.** Same chain on every judgment step. Lets the reader see the canonical resilience pattern; escalation traverses `RESOURCES.models = [haiku, sonnet, opus]` so a transient haiku failure on the critic falls through to sonnet then opus.
- **`TEST` block, minimal.** We assert that the loop terminated and produced all three top-level state fields (`result`, `draft`, `judgment`). We **do not** assert `final_score >= 0.85` — that would couple the test to LLM determinism we don't have, **and** `TEST EXPECTS` accepts top-level state fields only (no dotted sub-field access in v0.15 — confirmed against the parser test suite). The point of the TEST block in the source is to prove the loop *exits* and the contract path is exercised; the emitted pytest file is what readers actually run.

## Article — `data/article.txt`

A ~600-word historical-science piece on the discovery of penicillin (Fleming 1928 → Florey/Chain 1940 → mass production 1944 → Nobel 1945). Three reasons this choice:

1. **Rich in named entities and dates** — the critic has concrete things to score (years, names, places). Each refine pass has a chance of *meaningfully* changing missing_points.
2. **Anchored, citable, low pretraining-collapse risk on details.** The LLM "knows" the topic broadly, but the specific phrasing, dates, and minor names in *this* text need to be grounded — so a hallucination check has teeth.
3. **Diffusable.** We re-tell the standard story in our own words from public sources, with a short bibliography comment at the foot of the file. No copyright lift; everyone has seen this story.

Length target: 550-650 words, plain text, one paragraph per logical beat (discovery / dormancy / Oxford rescue / wartime scale-up / Nobel). The file is committed and rebuilt only if we want a longer/shorter trial; it is *data*, not generated.

## Output target — why `python`

Three reasons we commit the `--target python` output and not `claude-cli`:

1. **Readable on GitHub.** A reader who clicks through `expected_output/steps/judge_summary.py` sees a typed Pydantic body, a normal `def run(...)` call, and a clear `client.messages.create(...)` invocation. The same reader on `--target claude-cli` would see `.prompt` templates + `.sh` glue + `jq` state-threading — instructive but noisier.
2. **Per-step `invoke.model` works.** The `python` emitter honours per-step model overrides; the `claude-cli` emitter only uses `RESOURCES.models` as an escalation chain. Our two-roles design needs the override.
3. **`TEST` blocks emit only on `python`.** The pytest file under `expected_output/tests/test_refine_loop_terminates_with_known_article.py` is part of the lesson — readers see the declarative `TEST` block in the source and the concrete pytest function next to it.

We mention in the README that `claude-cli` and `mcp-server` would also compile cleanly (same flow, no banned features); we do not commit those outputs.

## `expected_output/` — what gets checked in

The full result of `uv run python -m clio compile flow.clio --target python --output expected_output/`. Concretely:

```
expected_output/
  pyproject.toml             # generated by the emitter
  flow.py                    # orchestrator with the WHILE loop
  contracts.py               # two Pydantic models
  steps/
    load_article.py
    draft_summary.py
    judge_summary.py
    refine_summary.py
    finalize.py
  prompts/
    draft_summary.md
    judge_summary.md
    refine_summary.md
  schemas/
    summary_judgment.json
    final_summary.json
  tests/
    test_refine_loop_terminates_with_known_article.py
  __init__.py                # if the emitter produces one
```

Two consequences:

- **The reader sees the result on GitHub.** Click `flow.py`, see the for-loop with break, the state threading, the two model clients. The lesson is partially absorbed before any local install.
- **Every emitter change has to regenerate this folder.** Mitigated by `rebuild.sh` + a CI test that exits non-zero on diff (see CI plan below).

## `rebuild.sh`

```bash
#!/usr/bin/env bash
# Regenerate expected_output/ from flow.clio and verify there is no drift.
set -euo pipefail
cd "$(dirname "$0")"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

uv run python -m clio compile flow.clio --target python --output "$tmp"

if diff -r --brief expected_output/ "$tmp" > /dev/null; then
  echo "expected_output/ is up to date."
  exit 0
fi

echo "Drift detected. Update expected_output/ with:"
echo "  rm -rf expected_output/ && cp -r '$tmp' expected_output/"
exit 1
```

Symmetric usage:
- A reader: runs `bash rebuild.sh` and gets confirmation the checked-in output matches the compiler. No surprise.
- A maintainer who legitimately changed the emitter: runs the suggested `rm -rf && cp -r`, commits the regenerated `expected_output/` in the same PR as the emitter change. The diff in PR-review shows exactly what changed.

## `README.md`

Outline (we write the prose during the implementation plan, not here):

1. **What this project shows** — one paragraph, the writer/critic refine loop, the two CLIO primitives it leans on (`WHILE`, per-step `invoke.model`).
2. **What you'll see when it runs** — a 3-iteration sample transcript captured once and pasted in (the iteration count is non-deterministic; we present the transcript as "one possible run").
3. **Prerequisites** — `uv pip install -e .[dev]` at repo root, `ANTHROPIC_API_KEY` set, that's it.
4. **Run it** — three commands:
   ```bash
   cd examples/projects/01-iterative-refiner
   uv pip install ./expected_output
   ANTHROPIC_API_KEY=... iterative_refiner --file data/article.txt
   ```
5. **Inspect the compiled output** — pointer to `expected_output/flow.py` (the WHILE), `expected_output/steps/judge_summary.py` (the contract enforcement), `expected_output/prompts/judge_summary.md` (the critic system prompt).
6. **Tweak the source** — three suggested edits with one-line answers: raise the threshold to `0.95`, swap critic to `sonnet`, add a `fluency` field to the judgment contract.
7. **Cross-reference** — link to `docs/manual/03-cookbook.md` "Refine loop" recipe.

## CI invariant

A single new pytest test:

```
tests/test_examples/test_iterative_refiner_drift.py
  - test_iterative_refiner_expected_output_is_up_to_date
```

Implementation: invokes `rebuild.sh` in a subprocess from the repo root, asserts exit code 0. Runs as part of the standard `pytest tests/` invocation. ~15 LOC.

Acts as a one-way gate: an emitter change that changes the emitted bytes for this flow **must** also commit the regenerated `expected_output/`. The PR diff makes the impact of the emitter change visible. This is the only mechanism that keeps checked-in compiled output honest in the long term; it's cheap and we want it from day one.

## Manual / changelog updates

- `docs/manual/03-cookbook.md` — add a "Refine loop (writer + critic)" recipe section with a 15-line snippet (subset of `flow.clio` showing just the WHILE) and a link to `examples/projects/01-iterative-refiner/`.
- `docs/manual/01-getting-started.md` — no change. The new project is **not** the entry point; it's the second thing a reader sees once they've understood a linear flow.
- `examples/README.md` — add a small "Project examples" section at the top with a link to `examples/projects/`. We do **not** touch the existing flat-example catalog below.
- `CHANGELOG.md` — under `## Unreleased`, a one-line `### Examples` entry: "Add `examples/projects/01-iterative-refiner/` — full project demonstrating the writer/critic refine loop with `WHILE … MAX 3`."

## Files created / modified — summary

| Path | Action | Approx size |
|---|---|---|
| `examples/projects/01-iterative-refiner/README.md` | new | ~120 lines |
| `examples/projects/01-iterative-refiner/flow.clio` | new | ~70 lines (source above) |
| `examples/projects/01-iterative-refiner/data/article.txt` | new | ~600 words |
| `examples/projects/01-iterative-refiner/expected_output/**` | new (compiled) | ~600 lines total across files |
| `examples/projects/01-iterative-refiner/rebuild.sh` | new | ~15 lines |
| `examples/projects/01-iterative-refiner/TESTING.md` | new | ~25 lines |
| `tests/test_examples/__init__.py` | new | 0 lines |
| `tests/test_examples/test_iterative_refiner_drift.py` | new | ~15 lines |
| `docs/manual/03-cookbook.md` | modified | +~30 lines (new recipe section) |
| `examples/README.md` | modified | +~10 lines (top section pointer) |
| `CHANGELOG.md` | modified | +1 line |

No `clio/**` changes. No `pyproject.toml` changes.

## Risks and how we mitigate them

| Risk | Mitigation |
|---|---|
| Checked-in `expected_output/` drifts silently as the emitter evolves | CI test `test_iterative_refiner_expected_output_is_up_to_date` fails the build on drift; PR review sees the regenerated diff |
| The loop never terminates on a real API call (`MAX 3` exhausted, `score` still < 0.85) | `MAX 3` is a hard ceiling — the flow always reaches `finalize`. The `final_summary.iterations` field surfaces this in the result; the TEST asserts `iterations >= 1`, not `final_score >= 0.85`. README says "MAX-reached is normal output, not failure" |
| Reader runs the example, sees one API round per step, finds it slow | `CACHE: ttl(7d)` makes re-runs free within a week. README says so explicitly |
| Article text causes a CC or attribution issue | We write the article in our own words from public sources; the file has a short "Sources" footer block listing the wiki pages and reference books we leaned on |
| The `invoke: { model: ... }` per-step syntax fails at compile time on some target the README claims to support | We only commit the `python` output, and the `python` emitter honours per-step model overrides (already tested elsewhere in the test suite). The README says `claude-cli` and `mcp-server` "should compile" — we will verify both at implementation time and remove the claim if either rejects something |
| `TEST` block runtime cost (it really calls the API) makes the drift CI test slow / flaky | The drift test does **not** run the emitted pytest. It only checks that `clio compile` produces byte-identical output. The emitted `tests/test_*.py` is for the reader to opt into running locally |

## Open question — sequencing inside the implementation plan

One thing for the writing-plans phase to decide: do we author the article text **before** the `.clio` (so the contracts can be tuned to facts in the text) or **after** (so we can iterate the language until prose lands cleanly)? The dependency runs one way — writing the article requires knowing what the critic will score, but the critic prompt only references abstract criteria. **Tentative answer**: write the article first, then tune the prompts against it. Implementation plan should confirm.

## Out of scope for this spec — explicitly

- A *second* project (`02-…`). One project, complete, validated, then we use the same pattern for the next.
- A test that asserts the *quality* of the summary. The critic's `score` is the in-flow signal; we do not double-grade externally.
- Re-numbering the existing `examples/*.clio` files. They stay where they are; the new `examples/projects/` folder is additive.
- Adding a `Makefile` or task runner at repo root. `rebuild.sh` per project is enough today.
