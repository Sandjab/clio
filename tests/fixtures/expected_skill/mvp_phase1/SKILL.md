---
name: foo
description: Execute flow foo
allowed-tools: Bash, Read, Write, TodoWrite
---

# foo
## Step 01 — foo (MODE: exact)

Run:

    python scripts/01_foo.py < state.json > state.next.json && mv state.next.json state.json

Tick the corresponding TodoWrite todo. Do not advance until the script exited 0.

