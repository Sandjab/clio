# 01-iterative-refiner -- writer + critic refine loop

A complete CLIO project that summarises a single article in a feedback loop
between two LLM roles:

- a **writer** (`sonnet`) drafts then revises the summary,
- a **critic** (`haiku`) scores each draft on fidelity (no hallucinated
  claims) and coverage (key facts present) and lists what's missing.

The loop iterates until the critic's score crosses `0.85` or after `3` refine
passes -- whichever happens first.

It demonstrates two CLIO primitives no other example covers end-to-end today:

1. **`WHILE ... MAX N`** with a composed condition (`score < 0.85 and verdict == "refine"`).
2. **Per-step `invoke: { protocol: anthropic, model: ... }`** -- different LLMs for different roles in the same flow.

## What you'll see when it runs

A single run prints, in order:

1. The article is loaded from `data/article.txt`.
2. `draft_summary` returns a ~200-word first draft.
3. `judge_summary` returns a `summary_judgment` record: a numeric score, a verdict (`accept` or `refine`), and up to five `missing_points`.
4. If the verdict is `refine`, `refine_summary` rewrites the draft, and `judge_summary` re-scores. Repeat up to three times.
5. `finalize` packages the last draft into a `final_summary` record: `{text, iterations, final_score}`.

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
process -- just a Python virtualenv and an Anthropic key.

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

## Known limitations (v0.15)

The committed `expected_output/` faithfully reflects what `clio compile
--target python` produces today. Two compiler bugs identified during this
project's code review currently prevent it from running end-to-end:

1. **Per-step `invoke.model` aliases are not resolved.** Symbolic names
   (`sonnet`, `haiku`) are passed verbatim to the Anthropic API, which
   requires versioned model IDs (e.g. `claude-sonnet-4-6`). The fix is in
   the python emitter's `ApiInvokeIR` path -- the existing `_model_id()`
   resolver needs to be applied there. Until fixed, the API will reject
   the request with `BadRequestError`.

2. **Pydantic inputs are not `.model_dump()`'d before JSON prompt
   substitution.** When `refine_summary` receives the `review:
   summary_judgment` record, the emitter calls `json.dumps(review)` which
   raises `TypeError` on the Pydantic model. The fix is symmetric to the
   one already in `_serialize()` for cache writes.

Both bugs are tracked separately and will be fixed in a follow-up PR. The
project ships now because its **structural** value (a runnable shape on
GitHub showing the writer + critic refine loop in CLIO source, with the
compiled python target alongside) is independent of those runtime bugs --
and because the drift-guard CI test ensures `expected_output/` will
regenerate cleanly the moment the emitter is fixed.

A separate caveat: the emitted `tests/test_*.py` calls the flow end-to-end
and will fail with `NotImplementedError` until you write the bodies of the
`exact` steps (`load_article`, `finalize`) in `expected_output/iterative_refiner/steps/`.
This is intended scaffolding -- exact steps are deliberately left for the
user to implement, just like in `mvp.clio` / `entities.clio`.

## Inspect the compiled output

The whole point of `expected_output/` being committed is that you can
read the result before running it.

- `expected_output/iterative_refiner/flow.py` -- the orchestrator. Look for the `for _i in range(3)` loop with `if not cond: break` -- that's the `WHILE ... MAX 3:` primitive after compilation.
- `expected_output/iterative_refiner/steps/judge_summary.py` -- typed Pydantic body, contract enforcement, `anthropic.Anthropic().messages.create(...)` call. The `_MODELS` tuple holds the per-step model override (`('haiku',)`).
- `expected_output/iterative_refiner/steps/draft_summary.py` -- the writer prompt embedded as `_SYSTEM_PROMPT`, with the DESCRIPTION and STRATEGIES text from the source.
- `expected_output/iterative_refiner/contracts.py` -- two Pydantic models (`SummaryJudgment`, `FinalSummary`) with the field validators from the `.clio` ASSERTs.
- `expected_output/tests/test_refine_loop_terminates_with_known_article.py` -- the pytest emitted from the `TEST` block.

## Tweak the source

Three one-line edits to try once you've understood the example:

| Try | In `flow.clio` | Effect |
|---|---|---|
| Stricter quality bar | `WHILE review.score < 0.95` (was `0.85`) | Loop almost always hits `MAX 3`; more API rounds, fewer early-accepts. |
| Swap the critic to `sonnet` | `STEP judge_summary` -> `invoke: model: sonnet` | More expensive critic, usually higher scores per draft, fewer iterations. |
| Add a `fluency` field | `CONTRACT summary_judgment` -> add `fluency: float` | The critic now returns three scores; the loop condition can be reweighted. |

After any edit, run `bash rebuild.sh` to regenerate `expected_output/`. The
drift-guard test in `tests/test_examples_projects/` will fail until you do.

## Where this fits

- **Manual recipe:** [`docs/manual/03-cookbook.md`](../../../docs/manual/03-cookbook.md) -- the "Refine loop (writer + critic)" section is a 15-line distillation of this project's `flow.clio`.
- **Language reference:** [`docs/LANGUAGE_SPEC.md`](../../../docs/LANGUAGE_SPEC.md#while-v07-composed-in-v012) -- `WHILE ... MAX N` semantics; [per-step `invoke:`](../../../docs/LANGUAGE_SPEC.md#judgment-invocation-invoke-block) block.
- **Other examples** in `examples/` cover linear pipelines (`mvp.clio`, `entities.clio`), routing (`feedback_routing.clio`), parallelism (`classify_corpus.clio`), and RAG-like retrieval (`rag_basic.clio`). This is the first **project** with committed compiled output.
