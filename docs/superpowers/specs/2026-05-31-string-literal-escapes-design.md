# Spec — string-literal escapes (`\"` / `\\`)

**Date:** 2026-05-31
**Status:** approved (brainstorm), pending implementation plan
**Closes:** #88
**Author:** Jean-Paul Gavini (with Claude)

## 1. Intent

CLIO single-line string literals (`"…"`) currently have no escape mechanism: the lexer
scans from the opening `"` to the next `"`, so any `"` in the intended content terminates
the string early and a `\` in JSON-emitting shell commands trips `LexError: unexpected
character '\'`. This makes an inline `impl` shell `cmd:` that emits JSON inexpressible.

Add a minimal escape mechanism — `\"` (literal double-quote) and `\\` (literal backslash) —
so such strings are expressible. The multi-line `|` block form is unchanged.

## 2. Decision (settled in brainstorm)

**Escape set = minimal: `\"` and `\\` only.** Any other `\x` (where `x ∉ {", \}`) stays a
literal backslash followed by `x`. No `\n` / `\t` / `\uXXXX` — newlines already have the `|`
block form, and a richer set would be YAGNI for the motivating case (emitting JSON from a
shell `cmd:`).

## 3. Core change — the lexer

`clio/parser/lexer.py:74-83`. The string scanner moves from a raw slice to a buffer that
consumes escapes:

```python
if ch == '"':
    j = i + 1
    buf = []
    while j < len(stripped) and stripped[j] != '"':
        if stripped[j] == '\\' and j + 1 < len(stripped) and stripped[j + 1] in ('"', '\\'):
            buf.append(stripped[j + 1]); j += 2; continue
        buf.append(stripped[j]); j += 1
    if j >= len(stripped):
        raise LexError("unterminated string literal", lineno, col)
    tokens.append(Token(TokenType.STRING, "".join(buf), lineno, col))
    col += (j - i) + 1   # unchanged: col counts SOURCE columns (j = index of the closing quote)
    i = j + 1
    continue
```

~8 lines, localized. Column counting is unchanged (it counts source characters; `j` is still
the source index of the closing `"`).

## 4. Downstream — already safe except one spot

The re-emission machinery already handles arbitrary `"` in string values. **Proof:** multi-line
`|` `DESCRIPTION` / `STRATEGIES` blocks routinely contain `"` (e.g. the `math-olympiad`
prompts) and emit cleanly. Specifically:

- `cmd:` → shlex-split into `argv` at IR build (`clio/ir/builder.py:1066`) → emitted via
  `repr()` (Python, `_python_helpers.py:658`), `json.dumps()` (Go, `_go_step_renderers.py:495`),
  `shlex.quote()` (claude-cli). Safe.
- Go `MATCH` case values already escape (`_go_flow_renderer.py:481`). Safe.
- **The one gap:** `clio/emitters/_shared_utils.py:538` → `lit = f'"{condition.literal_value}"'`
  (IF/MATCH literal rendering) interpolates the literal **without** escaping. Post-fix, a literal
  carrying an escaped quote would emit broken target code. Fix: render it through a safe encoder
  (`json.dumps` / `repr`). The exact target semantics of this renderer are confirmed during
  planning before choosing the encoder.

All other `f'"{…}"'` interpolations in the emitters carry **identifiers** (field / step names,
regex `[a-zA-Z_]…`) which cannot contain a quote — out of scope.

## 5. Backward compatibility

- `\x` with `x ∉ {", \}` stays a literal `\` + `x`, so `"C:\foo"` still works. **Zero
  regression** — a grep found no fixture with a `\` inside a single-line string today.
- `"foo\"` (trailing backslash before the closing quote) now reads as an escaped quote and
  therefore becomes *unterminated* — consistent with the escape rule. A literal trailing
  backslash is written `"foo\\"`.

## 6. Free bonus

The misleading `unexpected character '\'` error disappears on its own (the `\` is now consumed
inside the string), which also satisfies issue #88's fallback direction #3 with no dedicated work.

## 7. Testing (the bulk of the volume, not the difficulty)

- **Lexer unit tests:** `\"` → `"`; `\\` → `\`; `\x` → literal `\x` preserved; unterminated
  after a trailing `\"`.
- **End-to-end:** a `.clio` whose exact shell step is `cmd: "echo '{\"available\": true}'"` →
  `clio check` passes, then emit to all six targets and confirm the emitted code is valid
  (Python `repr`, Go `json.dumps`, bash `shlex.quote`).
- **IF/MATCH regression:** a condition literal carrying an escaped quote emits correctly
  (covers the `_shared_utils.py:538` fix).
- **claude-skill round-trip:** the `.clio/` sidecar is stored verbatim (no re-lex) so
  `--mode strict` is unaffected; still verify a round-trip with an escaped quote.

## 8. Scope

- `clio/parser/lexer.py` — the escape-consuming scanner.
- `clio/emitters/_shared_utils.py` — harden the IF/MATCH literal renderer.
- Tests (lexer + cross-emitter end-to-end + round-trip).
- One line in `docs/LANGUAGE_SPEC.md` noting that `"…"` supports `\"` and `\\`; a CHANGELOG
  `[Unreleased]` entry.

## 9. Out of scope (YAGNI)

- `\n` / `\t` / `\r` / `\uXXXX` and any richer escape set.
- A raw / alternate-delimiter string form.
- Any change to the `|` block form, other emitters, or the parser beyond the lexer.
