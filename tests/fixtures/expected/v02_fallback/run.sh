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

# Helper: run one judgment attempt against $1=model with $2=prompt and validate against $3=schema_path.
# Prints the cleaned response on success, nothing on failure. Exit 0 on success, 1 on failure.
_clio_run_attempt() {
    local model="$1" prompt="$2" schema_path="$3" raw clean
    raw="$(printf %s "$prompt" | claude -p --model "$model" --output-format text 2>/dev/null || true)"
    [ -n "$raw" ] || return 1
    clean="$(printf %s "$raw" | awk '!/^```/')"
    printf %s "$clean" | "$PYTHON" -m clio_runtime.validate "$schema_path" - >/dev/null 2>&1 || return 1
    printf %s "$clean"
    return 0
}

echo '{}' > state.json

# Step 1: load_customers (exact)
"$PYTHON" steps/01_load_customers.py --file=customers.csv

# Step 2: detect_churn (judgment)
INLINED_SCHEMA_02='{"type":"array","items":{"type":"object","properties":{"client":{"type":"string"},"risk":{"enum":["low","mid","high"]},"reason":{"type":"string"}},"required":["client","risk","reason"],"additionalProperties":false}}'
PROMPT_02="$("$PYTHON" -m clio_runtime.substitute steps/02_detect_churn.prompt state.json)"
PROMPT_02="${PROMPT_02//\$\{schema\}/$INLINED_SCHEMA_02}"
MODELS_02=(haiku sonnet opus)
MODEL_IDX_02=0
RESPONSE_02=""
CACHE_DIR_02="${CLIO_CACHE_DIR:-.cache}"
KEY_02="$("$PYTHON" -m clio_runtime.cache key detect_churn haiku "$PROMPT_02" "$INLINED_SCHEMA_02")"
RESPONSE_02="$("$PYTHON" -m clio_runtime.cache lookup "$CACHE_DIR_02" detect_churn "$KEY_02" 86400 2>/dev/null || true)"
if [ -z "$RESPONSE_02" ]; then
    RESPONSE_02="$(_clio_run_attempt "${MODELS_02[$MODEL_IDX_02]}" "$PROMPT_02" steps/02_detect_churn.schema.json || true)"
    if [ -z "$RESPONSE_02" ]; then
        for _ in $(seq 1 3); do
            RESPONSE_02="$(_clio_run_attempt "${MODELS_02[$MODEL_IDX_02]}" "$PROMPT_02" steps/02_detect_churn.schema.json || true)"
            [ -n "$RESPONSE_02" ] && break
        done
    fi
    if [ -z "$RESPONSE_02" ] && [ $MODEL_IDX_02 -lt $((${#MODELS_02[@]} - 1)) ]; then
        MODEL_IDX_02=$((MODEL_IDX_02 + 1))
        KEY_02_ESC="$("$PYTHON" -m clio_runtime.cache key detect_churn "${MODELS_02[$MODEL_IDX_02]}" "$PROMPT_02" "$INLINED_SCHEMA_02")"
        RESPONSE_02="$("$PYTHON" -m clio_runtime.cache lookup "$CACHE_DIR_02" detect_churn "$KEY_02_ESC" 86400 2>/dev/null || true)"
        if [ -z "$RESPONSE_02" ]; then
            RESPONSE_02="$(_clio_run_attempt "${MODELS_02[$MODEL_IDX_02]}" "$PROMPT_02" steps/02_detect_churn.schema.json || true)"
        fi
        if [ -n "$RESPONSE_02" ]; then
            "$PYTHON" -m clio_runtime.cache store "$CACHE_DIR_02" detect_churn "$KEY_02_ESC" "${MODELS_02[$MODEL_IDX_02]}" "$RESPONSE_02"
        fi
    fi
    if [ -z "$RESPONSE_02" ]; then
        "$PYTHON" steps/02_detect_churn_naive.py --customers="$(jq -r .customers state.json)"
        RESPONSE_02="$(jq -c .risks state.json)"
    fi
    if [ -z "$RESPONSE_02" ]; then
        echo '[clio] step detect_churn: churn detection exhausted' >&2
        exit 1
    fi
    if [ $MODEL_IDX_02 -eq 0 ] && [ -n "$RESPONSE_02" ]; then
        "$PYTHON" -m clio_runtime.cache store "$CACHE_DIR_02" detect_churn "$KEY_02" haiku "$RESPONSE_02"
    fi
fi
jq --argjson r "$RESPONSE_02" '.risks = $r' state.json > state.json.tmp && mv state.json.tmp state.json

echo "[clio] flow retention completed."
