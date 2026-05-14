---
name: pipeline
description: Execute flow pipeline
allowed-tools: Bash, Read, Write, TodoWrite
---

# pipeline
## Step 01 — load_clients (MODE: exact)

Run:

    python scripts/01_load_clients.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

## Step 02 — detect_churn (MODE: judgment)

**Reads from state**: see prompt template `prompts/02_detect_churn.md`
**Writes to state**: `state.detect_churn` validated by `schemas/02_detect_churn.output.json`

Steps:
1. Read `prompts/02_detect_churn.md`, substitute `{{state.x}}` placeholders from `state.json`.
2. Generate an output as the assistant, save verbatim to `out.json`.
3. Validate using the bundled helper:

        python scripts/_validate.py out.json schemas/02_detect_churn.output.json

4. If exit 0 (valid): merge into `state.json` under `state.detect_churn`.
5. If exit ≠ 0 (invalid): see RESCUE/RETRY section below if present, otherwise stop.

Tick the corresponding TodoWrite todo.


**Retry**: on failure, regenerate up to 3 time(s). After the budget is exhausted, see RESCUE section if present, otherwise stop.

## Step 03 — fallback_detect_churn (MODE: exact)

Run:

    python scripts/03_fallback_detect_churn.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

## Step 04 — route_alerts (MODE: exact)

Run:

    python scripts/04_route_alerts.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

## Step 05 — notify_slack (MODE: exact)

Run:

    python scripts/05_notify_slack.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

### RESCUE: If step `detect_churn` fails

Available expressions in the handler body: `detect_churn.error.message` (str), `detect_churn.error.type` (str — Python exception class name).

1. Call `notify_slack(…)`
1. Call `fallback_detect_churn(…)`

**RESUME**: set `state.detect_churn.report` ← `state.fallback_detect_churn.report`, then continue with the step after `detect_churn`.


## Resources

**Target**: `python`

**Models**: `haiku`, `sonnet`

