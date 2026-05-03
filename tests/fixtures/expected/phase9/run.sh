#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Resolve a Python 3.12+ interpreter (override with PYTHON env var if needed).
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for candidate in python3.12 python3.13 python3.14 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 \
           && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' >/dev/null 2>&1; then
            PYTHON="$candidate"
            break
        fi
    done
fi
if [ -z "$PYTHON" ]; then
    echo "[clio] error: Python 3.12+ not found on PATH (set PYTHON=/path/to/python)" >&2
    exit 1
fi

echo '{}' > state.json

# Step 1: load_customers (exact)
"$PYTHON" steps/01_load_customers.py --file=customers.csv

# Step 2: detect_churn (judgment)
INLINED_SCHEMA_02='{"type":"array","items":{"type":"object","properties":{"client":{"type":"string"},"risk":{"enum":["low","mid","high"]},"reason":{"type":"string","maxLength":300}},"required":["client","risk","reason"],"additionalProperties":false}}'
PROMPT_02="$("$PYTHON" -m clio_runtime.substitute steps/02_detect_churn.prompt state.json)"
PROMPT_02="${PROMPT_02//\$\{schema\}/$INLINED_SCHEMA_02}"
RESPONSE_02=""
RAW_RESPONSE_02="$(printf %s "$PROMPT_02" | claude -p --model haiku --output-format text)"
if [ -z "$RAW_RESPONSE_02" ]; then echo "[clio] empty response from claude -p in step 2 (detect_churn)" >&2; exit 1; fi
RESPONSE_02="$(printf %s "$RAW_RESPONSE_02" | awk '!/^```/')"
printf %s "$RESPONSE_02" | "$PYTHON" -m clio_runtime.validate steps/02_detect_churn.schema.json -
jq --argjson r "$RESPONSE_02" '.risks = $r' state.json > state.json.tmp && mv state.json.tmp state.json

echo "[clio] flow customer_retention completed."
