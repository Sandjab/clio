# CLIO Positioning

This document captures **why CLIO exists** as a distinct project, against the two most common reference points in its space — **LangGraph** and **n8n** — and the action plan to address its current weaknesses without diluting what makes it different.

It is meant as an anchor against scope drift: when a feature request looks like "let's just add X like n8n does", come back here first.

---

## Reference points

| Dimension | LangGraph | n8n | CLIO |
|---|---|---|---|
| **Nature** | Runtime library (Python/JS) | Self-hosted monolithic server | **Compiler** to standalone project |
| **Program form** | Imperative Python code | JSON in a database + visual canvas | **Declarative text file** (`.clio`) |
| **Typing** | TypedDict on state, opt-in `with_structured_output` | None (JSON between nodes) | **CONTRACT first-class**, validated at compile time |
| **Code/LLM distinction** | None (everything is a Python node) | None (an OpenAI node among 400) | **EXACT/JUDGMENT** structurally encoded in the language |
| **Execution targets** | The LangGraph runtime, period | The n8n server, period | **Multi-target** (claude-cli, python, planned: temporal, mcp-server, …) |
| **What you ship** | An app that depends on LangGraph | A workflow inside an n8n instance | **A standalone project** (no CLIO dependency at runtime) |
| **Integration library** | LangChain (very rich) | 400+ pre-wired nodes | **None** (assumed weakness — see plan) |
| **Visual / no-code** | No (LangGraph Studio in addition) | Yes (canvas) | No (and probably never, by design) |
| **Native observability** | LangSmith integrated | Executions UI, logs in DB | **None yet** (assumed weakness — see plan) |
| **Target audience** | Python devs familiar with LangChain | PMs, ops, integrators | **Devs who want to treat an LLM workflow as code** |

---

## The five structural differentiators

### 1. Compiler ≠ runtime

LangGraph and n8n are runtimes: you install their engine, you run it, your workflow lives inside it. The day you decommission them, you rewrite.

CLIO compiles to a project **you own**. Emitted code is idiomatic Python (or bash, or Rust…) with **no runtime dependency on CLIO itself**. If you throw the compiler away tomorrow, the emitted project still runs. It is a **scaffolding + ownership** model: the compiler bootstraps, the code is yours.

### 2. The program is a reviewable text artifact

A LangGraph graph is imperative Python embedded in your app's logic — `git diff` shows code changes, not workflow changes.

An n8n workflow is JSON in a database; reviewing it as a pull request means exporting borderline-illegible JSON.

A `.clio` is a structured, concise text file. **A PR that "adds a classification step between extract and synthesize" reviews in 30 seconds**, which is false in both alternatives.

### 3. EXACT / JUDGMENT is semantic, not convention

This is the deepest differentiator.

- In LangGraph, nothing distinguishes a node that calls an LLM from a node that performs a pure transformation. Static analysis has nothing to work with.
- In n8n, an OpenAI node is a node among 400; no special semantics.
- In CLIO, **the language *knows*** which steps are stochastic. Therefore the compiler can:
  - route models per step (cost/quality tradeoffs)
  - cache differentially (judgment hash includes prompt+model, exact does not)
  - generate appropriate retry/fallback (not the same meaning for a pure call vs. an LLM call)
  - perform static cost analysis ("this flow will cost ~$0.04 per run")
  - eventually batch compatible judgments, do automatic model routing

Neither competitor can do this because neither *knows* what is stochastic vs. deterministic.

### 4. Multi-target from a single source

A `.clio` can compile to `claude-cli` (fast prototype), `python` (deployment), `mcp-server` (tool exposed to Claude Desktop), `temporal` (durable production), `step-functions` (AWS-native). **One source, several runtimes.**

LangGraph is single-runtime. n8n is single-runtime. Neither can take your workflow and redeploy it as an MCP server or a Temporal workflow without a full rewrite.

### 5. CONTRACT as a primitive, not an annotation

`CONTRACT Foo { … }` is in the language. The compiler uses it to:
- check that step N's `GIVES` matches step N+1's `TAKES` **at compile time**
- generate the inline JSON Schema embedded in the prompt
- generate the JSON-only `_SYSTEM_PROMPT`
- validate LLM output at runtime
- verify graph-wide consistency before any execution

LangGraph approaches this with TypedDict, but it's a Python convention, not a language guarantee. n8n has no equivalent.

---

## The honest weaknesses

| Weakness | LangGraph / n8n | CLIO today |
|---|---|---|
| Integration library | huge (LangChain tools / 400 n8n nodes) | **none** — every integration via REST/shell/SQL or hand-written code |
| Runtime observability | LangSmith / n8n executions UI | **none** for now |
| Visual / no-code | n8n native, LangGraph Studio as bonus | **none**, and probably never (by design) |
| Ecosystem maturity | years | **months** |
| Time-travel debugging / replay | LangGraph checkpoints | **none** |
| Re-execution from a checkpoint | native | **none** |

---

## Resulting positioning

CLIO is not playing the same game:

- **n8n targets non-developers** who want to automate Slack/Postgres/HTTP via point-and-click. Different audience.
- **LangGraph targets Python developers building agents** inside an embedded Python app. Closer audience, but we optimize for something else: flow readability + portability.
- **CLIO targets developers who want to treat an LLM workflow as code**: PR-reviewable, typed, multi-target, with no runtime lock-in, with static analysis that *knows* what is stochastic.

One-line pitch: **write your LLM workflow as a source file, compile it to the runtime of your choice, own the emitted code.**

---

## Action plan: closing the weakness gaps without dilution

For each weakness, the principle is the same: **do not copy what competitors do — leverage the compiler-not-runtime stance to address it differently.**

Horizons used below:
- **Short-term**: v0.4 – v0.5 (next 1–2 milestones)
- **Mid-term**: v0.6 – v0.8
- **Long-term**: v1.0 and beyond

### W1. Integration library

**Strategy** — *do not rebuild LangChain or n8n's catalog*. Make the existing ecosystems usable from CLIO via a small set of generic invocation modes, then leverage MCP as the standardization vector. The compiler turns "integration coverage" from a quantity problem into a generic-mechanism problem.

| Horizon | Action |
|---|---|
| Short-term | Land `impl.mode: rest`, `shell`, `sql` for EXACT steps (covers ~80 % of naïve integrations). Document the patterns with worked examples (geocoding, DB lookup, PDF extraction). |
| Mid-term | Land `impl.mode: mcp_tool` — any MCP server (and there are now hundreds) becomes callable as an EXACT step. CLIO inherits the MCP ecosystem for free. |
| Long-term | Lightweight **step-template registry**: importable `.clio` snippets shared via plain git URLs (no proprietary registry, no runtime). Pattern: `IMPORT step "github.com/clio-templates/stripe-charge@v1"`. Stays declarative, stays inspectable. |

**Anti-pattern to refuse**: building a proprietary connector store.

### W2. Runtime observability

**Strategy** — *emit instrumented code rather than embed an observability runtime*. Lean on open standards (structured logs, OpenTelemetry) so emitted projects integrate with whatever the team already runs (Datadog, Honeycomb, Tempo, Langfuse, …).

| Horizon | Action |
|---|---|
| Short-term | Structured JSON-line logging in every emitter: one event per step (start, end, duration, cache_hit, estimated_cost, model). No agreement needed with any vendor. |
| Mid-term | OpenTelemetry traces (one span per step, parent span for the flow). Works with any OTel backend. Optional — disabled by default to keep emitted projects lean. |
| Long-term | Optional vendor-specific decorators via emitter flag (`--observability=langfuse`). Default stays OTel. |

**Anti-pattern to refuse**: building a CLIO-specific dashboard or backend.

### W3. Visual / no-code

**Strategy** — *no canvas editor*. But a `.clio` is declarative — a static visualization is essentially free, and a read-only HTML viewer is reachable. Editing visually stays out of scope.

| Horizon | Action |
|---|---|
| Short-term | `clio graph file.clio` emits Mermaid or DOT — renders inline in GitHub Markdown, in PR descriptions, in docs. Zero hosting. |
| Mid-term | `clio graph file.clio --html` emits a single self-contained HTML file: the graph, click-to-inspect each step's TAKES/GIVES/CONTRACT/mode. Static, no runtime. |
| Long-term | Optionally: VS Code extension that renders the graph beside the source. **No editor**. |

**Anti-pattern to refuse**: building a drag-and-drop canvas. If a user wants to author visually, let them generate `.clio` from a third-party canvas — keep the language as the source of truth.

### W4. Ecosystem maturity

**Strategy** — *no shortcut*. The compounding factors are: working examples, documented patterns, demonstrable reliability. Pulumi vs. Terraform, Bun vs. Node — maturity is earned through demos and reliability, not through marketing surface area.

| Horizon | Action |
|---|---|
| Short-term | 2–3 polished examples in `examples/` (entity extraction, ticket classification, RAG-like flow). Each comes with a README explaining the pattern. Already prioritized as step 1 in `next_steps.md`. |
| Mid-term | Walkthrough tutorials (markdown), one demo video, a written "from prompt to compiled project in 5 minutes" story. |
| Long-term | Public PyPI package, conference talk or blog post. Plant a flag in the LLM-tooling discussion. |

**Anti-pattern to refuse**: faking maturity (premature optimization announcements, vapor features). Ship small, ship correct.

### W5. Time-travel debugging / replay

**Strategy** — *the foundation already exists*. State serialization to `state.json` + cache per step (already implemented for both targets) is exactly the substrate replay needs. Replay is not a runtime feature — it's an orchestrator extension.

| Horizon | Action |
|---|---|
| Short-term | `clio resume <output_dir> --from-step N`: orchestrator reads existing `state.json`, skips the first N steps, resumes from N+1. Trivial in `python` emitter, slightly more involved in bash. |
| Mid-term | `clio replay <output_dir> --rerun-step N`: forces cache miss on step N specifically (useful when you want to retest a single judgment with a new prompt). |
| Long-term | Full event journal (every state transition appended to `events.jsonl`) + minimal CLI to navigate (`clio history`, `clio diff-states`). Could feed a future `--html` viewer. |

**CLIO advantage**: because we are *just an orchestrator pattern*, this extends naturally. LangGraph had to retrofit it as a runtime feature; we get it as a CLI command.

### W6. Re-execution from a checkpoint

Subsumed by W5 — same substrate, same roadmap.

---

## What we deliberately *do not* plan to address

These weaknesses are real, but they are weaknesses *only against the wrong yardstick*. Pursuing them would dilute what makes CLIO valuable:

- **A drag-and-drop visual editor.** It would force the source of truth out of `.clio` and back into a database, which kills the "review like code" promise. Visualization yes, editing no.
- **A proprietary integration marketplace.** We would spend years catching up to n8n and never overtake; meanwhile we would carry a vendor-style runtime burden that contradicts the compiler-not-runtime stance.
- **An embedded observability backend.** Same logic: open standards win, vendor lock-in loses.
- **A no-code-style "self-running" cloud product.** Possible business model, but a different project — would warp the compiler core toward a SaaS runtime.
- **An n8n compilation target.** n8n is itself the kind of monolithic runtime CLIO is designed against. Compiling to n8n means emitting JSON destined to be imported into a live n8n instance — losing source readability, portability, CONTRACT typing, and the EXACT/JUDGMENT semantic. A *visualization export* (`clio graph --format=n8n`) for non-developer stakeholders may make sense as part of W3; an executable emitter does not.
- **A LangChain compilation target.** Anything LangChain offers (tools library, retrievers, loaders) is already reachable from the `python` emitter via `impl.mode: code`. A dedicated LangChain emitter would carry maintenance burden against an unstable upstream API for zero incremental value.

---

---

## Adoption-bridge targets: when to add, when not to

A **bridge target** is an emitter whose primary value is not production deployment but *adoption by an audience already invested in another stack*. LangGraph is the archetypal candidate. The pattern is delicate: legitimate when scoped right, corrosive when not.

### LangGraph — conditional, not now

**Case for.**
- Adoption path for teams already invested in LangChain / LangGraph / LangSmith — they can try CLIO without abandoning their tooling.
- Closes W5/W6 (replay, time-travel) and part of W2 (observability via LangSmith) **for free** on this specific target — LangGraph already provides those capabilities.

**Case against.**
- Breaks the *compiler-not-runtime* promise on this target specifically: emitted code requires LangGraph + LangChain at runtime, in contrast to the standalone `python` target.
- LangGraph boilerplate is verbose; emitted code becomes noticeably less readable than `python`'s output.
- **Suction effect.** If the LangGraph emitter ships before `python` has matured on W2 and W5, users perceive it as strictly more capable and migrate to it by default — turning CLIO into a LangGraph wrapper and ceding roadmap control to an upstream we don't own.
- LangGraph's API evolves; the emitter carries ongoing maintenance cost against an upstream we do not control.

**Conditions to satisfy before building it.**
1. `python` has shipped W2 short-term (structured JSON-line logging) and W5 short-term (`clio resume --from-step N`). Otherwise LangGraph looks strictly superior and pulls users away from the canonical target.
2. The README and CLI document the target explicitly as a **bridge, not production**: `python` remains the recommended emitter for new projects.
3. Tests cover round-trip behavior at parity with `python` on at least one non-trivial example, so the bridge cannot quietly become the more-feature-rich path.

### General principle for bridge targets

A bridge target is legitimate if and only if **both** hold:
- (a) it serves an audience genuinely impractical to reach via the canonical target, **and**
- (b) it ships *after* the canonical target reaches feature parity on the bridge target's main draws.

Otherwise the bridge becomes the destination, and the canonical target withers. The same test will apply to any future "bridge" candidate (DSPy, Haystack, …): not before parity, not without explicit positioning as a bridge.

---

## Summary

CLIO's identity rests on five structural choices: **compiler not runtime**, **declarative text source**, **EXACT/JUDGMENT semantic split**, **multi-target emission**, **CONTRACT as primitive**. The current weaknesses are real, but each can be closed with **mechanisms that respect those five choices** — generic invocation modes (W1), open-standard instrumentation (W2), static visualization (W3), proven examples (W4), orchestrator-extension replay (W5–W6).

The discipline is to refuse the easy versions of each fix that would copy the competitors' shape and erase our own.
