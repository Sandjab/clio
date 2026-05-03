# CLIO MVP example

Compile and run:

```bash
python -m clio compile examples/mvp.clio --target claude-cli --output ./out
cp examples/customers.csv ./out/
# v0.1: edit out/steps/01_load_customers.py to replace the echo body with
# the CSV-parsing body shown in tests/fixtures/load_customers_real.py.
# v0.2: also edit out/steps/02_detect_churn_naive.py to a real heuristic
# (e.g. revenue < 1000 -> high; revenue < 10000 -> mid; else -> low).
bash ./out/run.sh
cat ./out/state.json
```

Requires `claude` (Claude Code CLI) authenticated, `python>=3.12`, and `jq`.

## Caching

The `detect_churn` step uses `CACHE: ttl(24h)`. The first run hits `claude -p`;
subsequent runs within 24 hours read from `out/.cache/detect_churn/<key>.json`
and do not invoke the API. To force a fresh call, `rm -rf out/.cache` or
override `CLIO_CACHE_DIR=/tmp/somewhere bash ./out/run.sh`.

## Resilience

If `claude -p --model haiku` produces a response that does not match the
contract, `detect_churn` retries up to 3 times. If still failing, it
escalates to `sonnet` (one attempt). If still failing, it falls back to
the heuristic `detect_churn_naive` step. If that fails too, the flow
aborts with an explicit message.
