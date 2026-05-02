# CLIO Language Specification v0.1

This is the reference grammar for the CLIO language. The compiler parses `.clio` files written in this syntax.

## File extension

`.clio`

## Comments

Lines starting with `#` are comments. Inline comments after `#` are also supported.

```
# This is a comment
STEP do_something  # inline comment
```

## Declarations

### STEP

An atomic unit of work. Does not describe HOW — only WHAT and with what guarantees.

```
STEP <name>
  TAKES:     <name>: <type> [, <name>: <type>]*
  GIVES:     <name>: <type>
  MODE:      exact | judgment | auto
  LANG:      python | rust | go | node | bash | auto    # optional, exact only
  CACHE:     on | off | ttl(<duration>)                  # optional, judgment only
  VALIDATE:  <boolean expression>                        # optional
  ON_FAIL:   <failure strategy>                          # optional
```

**MODE values:**
- `exact` — deterministic code. The compiler generates a function/script.
- `judgment` — requires an LLM. The compiler generates a prompt + schema.
- `auto` — the compiler decides. Tries `exact` first, falls back to `judgment`.

**LANG** only applies to `exact` steps. If omitted, defaults to `auto` (compiler picks based on data size, dependencies, and target). Can also be set globally in RESOURCES.

**CACHE** only applies to `judgment` steps. Controls reproducibility:
- `on` — permanent cache. The input is hashed (prompt + schema + model + parameters) to produce a key. Same input = same output guaranteed, no API call.
- `ttl(<duration>)` — cache with expiration. Duration uses suffixes: `s`, `m`, `h`, `d` (e.g. `ttl(24h)`, `ttl(7d)`).
- `off` — every run calls the LLM. This is the default.

### CONTRACT

A typed shape guarantee on data flowing between steps.

```
CONTRACT <name>
  SHAPE:      <type definition>
  ASSERT:     <boolean expression>          # optional
  CONFIDENCE: >= <float>                    # optional, judgment steps only
```

**SHAPE** uses a type notation inspired by JSON Schema / Python typing:
- Primitives: `int`, `float`, `str`, `bool`
- Containers: `List<T>`, `Dict<K, V>`, `Optional<T>`
- Enums: `enum(val1|val2|val3)`
- Records: `{field: type, field: type}`
- Constrained: `str(max=200)`, `int(min=0)`

**CONFIDENCE** sets a threshold for LLM outputs. Below this, the step retries or escalates.

### FLOW

A directed graph of steps.

```
FLOW <name>
  <step_call>
    -> <step_call>
    -> <step_call>
```

### RESOURCES

Execution constraints declared at the flow level.

```
RESOURCES
  budget:     <amount>                          # e.g. 30€/month
  prefer:     cost | latency | quality
  models:     [<model>, <model>, ...]
  strategy:   escalate | round-robin | fixed
  target:     claude-cli | python | rust | go | node | docker | hybrid
  lang:       python | rust | go | node | bash | auto
```

## Control flow

### Sequential chaining

```
step_a -> step_b -> step_c
```

### FOR EACH

Iterates over a collection. The loop variable is available inside the block.

```
FOR EACH <item> IN <collection>:
  <step_call(item)>
```

### WHILE

Conditional loop. Compiles to an agent pattern (iterative LLM calls with state).

```
WHILE <condition>:
  <step_call>
    -> <step_call>
```

### IF / ELSE

Conditional branching.

```
IF <condition>:
  -> <step_call>
ELSE:
  -> <step_call>
```

A step's failure can be tested:

```
IF <step_name>.FAILS:
  -> <fallback_step>
```

### MATCH / CASE / DEFAULT

Multi-way branching.

```
MATCH <expression>:
  CASE <value>: <step_call>
  CASE <value>: <step_call>
  DEFAULT:      <step_call>
```

## Failure strategies (ON_FAIL)

Composable with `then`:

```
ON_FAIL: retry(3)
ON_FAIL: retry(3) then fallback(other_step)
ON_FAIL: escalate then retry(2)
ON_FAIL: abort("reason")
```

- `retry(n)` — retry the step up to n times
- `fallback(step)` — switch to an alternative step
- `escalate` — switch to a more capable LLM model
- `abort(message)` — stop the flow with an error

## Types

### Primitives

`int`, `float`, `str`, `bool`

### Containers

`List<T>`, `Dict<K, V>`, `Optional<T>`, `Set<T>`

### Records

`{field_name: type, field_name: type}`

Nested records are allowed: `{user: {name: str, age: int}, score: float}`

### Enums

`enum(value1|value2|value3)`

### Constrained types

`str(max=200)`, `str(min=1)`, `int(min=0, max=100)`, `float(precision=2)`

### Domain types

Domain types are aliases for common patterns:

- `CSV`, `JSON`, `Log` — file inputs
- `Email`, `URL`, `Markdown` — string subtypes

These compile to their base type + validation.

## Example

```
CONTRACT compte_risque
  SHAPE:      {client: str, risque: enum(low|mid|high), raison: str(max=300)}
  ASSERT:     len(raison) > 0

STEP charger_clients
  TAKES:     fichier: CSV
  GIVES:     clients: List<{nom: str, ca: float, dernière_commande: str}>
  MODE:      exact

STEP détecter_churn
  TAKES:     clients: List<{nom: str, ca: float, dernière_commande: str}>
  GIVES:     risques: List<compte_risque>
  MODE:      judgment
  CACHE:     ttl(24h)
  VALIDATE:  each risque.raison cites a column from clients
  ON_FAIL:   retry(3) then escalate

STEP vérifier_ticket_zendesk
  TAKES:     client: str
  GIVES:     dernier_ticket: {sujet: str, date: str, statut: str}
  MODE:      exact

STEP rédiger_mail_rétention
  TAKES:     risque: compte_risque, ticket: {sujet: str, date: str, statut: str}
  GIVES:     mail: {objet: str, corps: str}
  MODE:      judgment
  VALIDATE:  len(mail.corps) > 50 AND len(mail.corps) < 2000

FLOW rétention_clients
  charger_clients(fichier="clients.csv")
    -> détecter_churn(clients)
    -> FOR EACH risque IN risques:
         vérifier_ticket_zendesk(risque.client)
           -> rédiger_mail_rétention(risque, dernier_ticket)

  IF détecter_churn.FAILS:
    -> abort("Impossible de détecter le churn — vérifier le format du CSV")

RESOURCES
  prefer:     quality
  models:     [haiku, sonnet]
  strategy:   escalate
  target:     claude-cli
  lang:       python
```
