#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
echo '{}' > state.json

# Step 1: load_customers (exact)
python steps/01_load_customers.py --file=customers.csv

# Step 2: detect_churn (judgment)
PROMPT="$(python -m clio_runtime.substitute steps/02_detect_churn.prompt state.json)"
PROMPT="${PROMPT//\$\{schema\}/$(cat steps/02_detect_churn.schema.json)}"
RESPONSE="$(printf %s "$PROMPT" | claude -p --model haiku --output-format text)"
printf %s "$RESPONSE" | python -m clio_runtime.validate steps/02_detect_churn.schema.json -
jq --argjson r "$RESPONSE" '.risks = $r' state.json > state.json.tmp && mv state.json.tmp state.json

echo "[clio] flow customer_retention completed."
