#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo '{}' > state.json

# Step 1: load_customers (exact)
python steps/01_load_customers.py --file=customers.csv

# Step 2: summarize (exact)
python steps/02_summarize.py --customers="$(jq -r .customers state.json)"

echo "[clio] flow pipeline completed."
