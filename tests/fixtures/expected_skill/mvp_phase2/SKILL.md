---
name: echo-str
description: Execute flow echo_str
allowed-tools: Bash, Read, Write, TodoWrite
---

# echo_str
## Step 01 — echo_str (MODE: exact)

Run:

    python scripts/01_echo_str.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

