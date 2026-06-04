# Migration guide — v0.17 → v0.18

v0.18 introduces explicit visibility markers (`EXPOSE` / `INTERNAL`) that
replace the v0.17 implicit-exposure heuristic for `target: mcp-server`. All
other targets are unaffected by this change.

---

## What changed

### v0.17 behaviour (implicit exposure)

For `target: mcp-server`, the compiler implicitly exposed every signed FLOW
(one with both `TAKES:` and `GIVES:`) that was not called by any sibling FLOW
in the same source. No marker was required.

### v0.18 behaviour (explicit `EXPOSE`)

The compiler now requires an explicit `EXPOSE FLOW` declaration on every FLOW
that should become an MCP tool. A source file compiled to `mcp-server` with no
`EXPOSE FLOW` raises **E_MCP_001** at compile time.

Sources compiled to `python`, `claude-skill`, or `langgraph` are not affected:
`EXPOSE` and `INTERNAL` are informational on those targets — all symbols compile
regardless of their marker.

---

## Migration steps

### Step 1: check which FLOWs the v0.17 heuristic would expose

```bash
clio doctor --migrate-v018 your_file.clio
```

The command prints a list of FLOWs that the v0.17 heuristic would expose (every
signed FLOW not called by a sibling). No file is modified at this step.

### Step 2: apply the migration automatically (recommended)

```bash
clio doctor --migrate-v018 --write your_file.clio
```

With `--write`, the doctor inserts `EXPOSE` before each FLOW in the list and
leaves the rest of the file unchanged. The modified file is written **in place
with no backup** — `--write` is destructive. Back up the file manually before
running (e.g., `cp your_file.clio your_file.clio.orig`, or rely on `git diff`
after the write to review what changed).

### Step 3: apply manually (if preferred)

Add `EXPOSE` before each FLOW that should become an MCP tool:

```diff
-FLOW classify_article
+EXPOSE FLOW classify_article
   TAKES: article: Article
   GIVES: label: str
   internal_helper(x=article)
   -> score(text=article)
```

FLOWs that are internal helpers (called by sibling FLOWs) should be left
unmarked or explicitly marked `INTERNAL`:

```diff
-FLOW internal_helper
+INTERNAL FLOW internal_helper
   TAKES: x: str
   GIVES: y: str
   bump(x=x)
```

### Step 4: verify

```bash
clio check your_file.clio
uv run pytest tests/ --tb=short -q
```

No test output means no regressions. If you see **E_MCP_001**, you missed a
FLOW that the mcp-server target expected to expose — check step 2.

---

## Multi-file projects (new in v0.18)

If you also want to split your source into multiple files, v0.18 adds the
`FROM … IMPORT` syntax. This is a purely additive feature — existing single-file
sources continue to compile unchanged (after the `EXPOSE` migration above).

See [the language tour chapter](02-language-tour.md#splitting-your-code-across-files-v018)
and recipes [#21](03-cookbook.md#21-shared-schemas-across-pipelines-v018) and
[#22](03-cookbook.md#22-façade-file-barrel-file-pattern-v018) in the cookbook.

---

## Quick reference: before and after

```diff
 RESOURCES
   target: mcp-server
   models: [sonnet]

-CONTRACT Article
+EXPOSE CONTRACT Article
   SHAPE: {title: str}

-FLOW classify_article
+EXPOSE FLOW classify_article
   TAKES: article: Article
   GIVES: label: str
   internal_helper(x=article)
   -> score(text=article)

-FLOW internal_helper
+INTERNAL FLOW internal_helper
   TAKES: x: str
   GIVES: y: str
   bump(x=x)

 STEP score
   MODE: judgment
   TAKES: text: str
   GIVES: label: str

 STEP bump
   MODE: judgment
   TAKES: x: str
   GIVES: y: str
```

The diff above corresponds exactly to
`tests/fixtures/imports/migration_v017_to_v018/` (before → expected_after).
