# CLIO MVP example

Compile and run:

```bash
python -m clio compile examples/mvp.clio --target claude-cli --output ./out
cp examples/customers.csv ./out/
# v0.1: edit out/steps/01_load_customers.py to replace the echo body with
# the CSV-parsing body shown in tests/fixtures/load_customers_real.py.
bash ./out/run.sh
cat ./out/state.json
```

Requires `claude` (Claude Code CLI) authenticated, `python>=3.12`, and `jq`.
