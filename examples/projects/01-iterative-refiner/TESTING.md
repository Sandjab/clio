# TESTING -- `expected_output/` invariant

This project commits the result of `clio compile --target python ...`
directly under `expected_output/`. The reader sees the compiled artefact
on GitHub before running anything; that's deliberate, and worth a tiny
amount of maintenance discipline.

## The invariant

After any change that affects the python emitter -- or any change to this
project's `flow.clio` -- `expected_output/` must match what the current
compiler produces. The check is:

```bash
bash examples/projects/01-iterative-refiner/rebuild.sh
```

Exit 0 means up to date. Exit 1 prints the `cp -r` command that accepts
the new output.

## How CI enforces it

`tests/test_examples_projects/test_iterative_refiner_drift.py` runs `rebuild.sh`
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
  bug -- open a separate issue rather than working around it here.
