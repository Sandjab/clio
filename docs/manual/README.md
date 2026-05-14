# CLIO user manual

A structured walkthrough of the CLIO compiler, organised by what you want to do.

This is the **manual** — a learning path. For exhaustive reference, see
[`../LANGUAGE_SPEC.md`](../LANGUAGE_SPEC.md).

## Reading order

If you've never seen CLIO before, read these top-to-bottom:

1. **[Getting started](01-getting-started.md)** — your first compiled `.clio` flow in 5 minutes.
2. **[Language tour](02-language-tour.md)** — `STEP`, `CONTRACT`, `FLOW`, and how they fit together (incl. `RESCUE` + `RESUME`, `DESCRIPTION` / `STRATEGIES`, multi-`FLOW`, `TEST`).
3. **[Cookbook](03-cookbook.md)** — recipes for common patterns (RAG, classification, validation, retry chains).
4. **[Targets](04-targets.md)** — when to compile to `claude-cli`, `python`, `mcp-server`, `langgraph`, or `claude-skill`.
5. **[CLI reference](05-cli-reference.md)** — every command, every flag (`compile`, `check`, `graph`, `gen`, `doctor`, `status`).
6. **[Troubleshooting](06-troubleshooting.md)** — errors you're likely to see, and how to fix them.

If you already know CLIO and just need a recipe or a flag, jump directly to the relevant page.

## What's in this manual vs the spec

|  | This manual | `LANGUAGE_SPEC.md` |
|---|---|---|
| Audience | New & day-to-day users | Spec implementers, deep questions |
| Style | Tutorial, cookbook, narrative | Exhaustive reference per keyword |
| Examples | Always real, runnable code | Minimal illustrative snippets |
| Cross-links | To other manual pages | To AST/IR types and emit semantics |

If a topic is in both, the spec is authoritative. The manual aims to be **enough** to ship; the spec is **exact**.
