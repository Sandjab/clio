# Refonte « honnêteté » — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aligner les docs de référence de clio sur ce que le code fait réellement — un
compilateur déterministe — en élaguant la vision jamais construite (axe V) et la grammaire non
implémentée (axe G), et en nettoyant le drift mécanique (axe M).

**Architecture:** Refonte ~98 % documentaire, précédée du merge du baseline #90, puis 2 PRs
séquentielles : **PR-A** (manuel + cibles + commentaires de code — tout sauf les docs d'identité)
puis **PR-B** (réécriture des 3 docs d'identité : `ARCHITECTURE.md`, `LANGUAGE_SPEC.md`,
`CLAUDE.md`). Découpage **par document** (raffinement du « par axe » du spec) pour qu'aucun fichier
ne soit touché dans deux PRs.

**Tech Stack:** Markdown. Vérification via `uv run python -m clio check`, `grep -rn`,
`uv run pytest -q`, `uv run ruff check .`, `uv run mypy`.

**Adaptation (doc-refonte, pas de TDD code).** Le détail ligne-à-ligne du drift vit dans
`docs/audit-drift-2026-06-02.md` (30+ findings, 6 clusters). Ce plan **ne le reproduit pas** (DRY) :
il référence les findings par ID + ligne et fournit, pour chaque tâche, les **patterns d'édition
concrets** + une **commande de vérification**. La granularité est « un document par tâche », pas
« 5 min par étape » — approprié pour de l'éditorial.

**Contrainte machine.** Sur cet hôte, `python`/`python3` du PATH sont cassés (Python 3.9). **Seul
`uv run` fonctionne** — toutes les commandes clio passent par `uv run python -m clio …`.

**Patterns de réparation de snippets** (réutilisés par plusieurs tâches PR-A) :

- **`SHAPE:` multiligne → ligne logique unique** (le parser n'absorbe pas NEWLINE/INDENT dans
  `parse_record_type`) :
  ```clio
  # AVANT (ne parse pas)
  SHAPE: {
    name: str
    age: int
  }
  # APRÈS (parse)
  SHAPE: { name: str, age: int }
  ```
- **`invoke: { … }` inline → bloc indenté** (`parse_invoke_block` exige `NEWLINE → INDENT`) :
  ```clio
  # AVANT (ne parse pas)
  invoke: { cli: claude, model: claude-sonnet-4-6 }
  # APRÈS (parse)
  invoke:
    cli: claude
    model: claude-sonnet-4-6
  ```
- **Condition `IF` wrappée → une ligne logique** (la condition doit finir par `:` sur la même
  ligne) :
  ```clio
  # AVANT (ne parse pas)
  IF score > 0
     and verified:
  # APRÈS (parse)
  IF score > 0 and verified:
  ```

---

## Précondition P0 — Merger le baseline #90 (manuel, gated)

> ⚠️ Action sortante (merge d'une PR) — **gated** : à déclencher par l'humain, pas par un agent
> exécutant. Le reste du plan branche sur le `main` résultant.

- [ ] **Étape 1 — Déclencher la revue Gemini sur #90**

PR #90 (`docs(audit): code-vs-docs drift baseline`) est CI-verte mais sans revue (auto-trigger
Gemini non fiable). Poster `/gemini review` en commentaire sur la PR.

- [ ] **Étape 2 — Traiter le cycle Gemini puis merger**

Appliquer/réfuter les commentaires (réponse threadée citant le commit de fix), puis merger #90.

- [ ] **Étape 3 — Faire atterrir spec + plan**

Le spec (`docs/refonte-honesty-scope-2026-06-04.md`) et ce plan sont sur la branche
`docs/refonte-honesty-scope`. Les faire atterrir via leur propre petite PR planning (ou les replier
dans PR-A). Vérifier : `git log --oneline main` montre l'audit + le spec + le plan.

---

## PR-A — Drift mécanique & manuel (axe M + retraits triviaux)

**Branche :** `docs/refonte-honesty-mechanical` (depuis `main` post-#90).
**Périmètre :** `manual/01-06`, `manual/README.md`, `COMPILATION_TARGETS.md`, `04-targets.md`,
commentaires de code. **Hors** : les 3 docs d'identité (→ PR-B).

### Task A1 : `docs/manual/05-cli-reference.md`

**Files:** Modify `docs/manual/05-cli-reference.md`

- [ ] **Étape 1 — Éditer** (findings 3.5 F1/F3/F4/F5/F6/F11)
  - **F1 (CRITIQUE, L14)** : `compile --target` liste 5 cibles → ajouter `go` (source de vérité
    `clio/cli.py:24` `choices=[…, "go"]`). Les 6 : `claude-cli, python, mcp-server, langgraph,
    claude-skill, go`.
  - **F3** : documenter les flags `doctor` manquants `--flow`, `--migrate-v018`, `--write`
    (`cli.py:52-63`).
  - **F4** : `check` n'est pas « silent » — il imprime `ok` (`cli.py:167`). Corriger la prose.
  - **F5** : `gen` fait **1 retry**, pas « up to 3 » (`nl_to_clio.py:32` `max_retries=1`).
  - **F6** : indiquer le défaut `status --limit` = 10.
  - **F11** : message resume cite `{path}`, pas `state.json` littéral.
- [ ] **Étape 2 — Vérifier**
  Run: `grep -n "go" docs/manual/05-cli-reference.md | grep -i target` → `go` présent.
  Run: `grep -ni "up to 3 retries\|silent" docs/manual/05-cli-reference.md` → 0 ligne fautive.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/05-cli-reference.md
  git commit -m "docs(cli): list go target, fix doctor flags + check/gen behavior"
  ```

### Task A2 : `docs/manual/06-troubleshooting.md`

**Files:** Modify `docs/manual/06-troubleshooting.md`

- [ ] **Étape 1 — Éditer** (findings 3.5 F9/F10/F11)
  - **F9 (CRITIQUE)** : le message de validation cité liste 5 targets → 6. Doit matcher le
    **verbatim** émis par `clio/parser/parser.py:432-433` (inclut `go`).
  - **F10 (CRITIQUE)** : `E_CLI_001` présenté comme `ValueError: target=claude-cli does not
    support FROM … IMPORT` est **fabriqué**. Vrai chemin (`cli.py:122-127`) : `error: target
    'claude-cli' does not support cross-file imports (deferred to a future release)` (stderr,
    exit 1, **pas** un `ValueError`). Remplacer.
  - **F11** : message resume `{path}` (cf. A1).
- [ ] **Étape 2 — Vérifier**
  Run: `grep -n "E_CLI_001" docs/manual/06-troubleshooting.md` → **0**.
  Run: `grep -n "valid targets" docs/manual/06-troubleshooting.md` → la ligne liste 6 cibles.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/06-troubleshooting.md
  git commit -m "docs(troubleshooting): fix target list (5->6), drop fabricated E_CLI_001"
  ```

### Task A3 : `docs/COMPILATION_TARGETS.md`

**Files:** Modify `docs/COMPILATION_TARGETS.md`

- [ ] **Étape 1 — Éditer** (findings 3.3 C1/C2/M3/M4/M5/m1)
  - **C1** : retirer `E_GO_011` (jamais levé — `grep "E_GO_" clio/` → pas de 011).
  - **C2 (issue #83)** : Go `--from-step` est étiqueté « refusé (`E_GO_011`) » alors qu'il est
    **non implémenté** — le binaire relance tout le flow sans erreur. Requalifier « refusé » →
    « non implémenté (re-run complet du flow) ».
  - **M5** : `langgraph` marqué « Candidate » (L13) → **livré**.
  - **M4** : compléter la liste de refus langgraph (manque `WHILE` `langgraph.py:175`,
    `impl.mode: sql` :201).
  - **M3** : nuancer `E_MCP_001` — la garde ne tire que si `target: mcp-server` est **déclaré en
    source** (`builder.py:265-269`), contournable via le flag `--target`.
  - **m1** : retirer le stamp périmé « v0.4 » sur le logging claude-cli.
- [ ] **Étape 2 — Vérifier**
  Run: `grep -rn "E_GO_011" docs/COMPILATION_TARGETS.md` → **0**.
  Run: `grep -n "Candidate" docs/COMPILATION_TARGETS.md` → plus de `langgraph` Candidate.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/COMPILATION_TARGETS.md
  git commit -m "docs(targets): drop phantom E_GO_011, requalify go --from-step, langgraph shipped"
  ```

### Task A4 : `docs/manual/04-targets.md`

**Files:** Modify `docs/manual/04-targets.md`

- [ ] **Étape 1 — Éditer** (findings 3.3 C1/C2)
  - **C1** : cellule de matrice `04-targets.md:193` `❌ E_GO_011` → retirer le code fantôme.
  - **C2** : requalifier Go `--from-step` « refusé » → « non implémenté » (cohérent avec A3).
- [ ] **Étape 2 — Vérifier**
  Run: `grep -rn "E_GO_011" docs/manual/04-targets.md` → **0**.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/04-targets.md
  git commit -m "docs(targets-matrix): drop phantom E_GO_011 cell, requalify go --from-step"
  ```

### Task A5 : `docs/manual/02-language-tour.md`

**Files:** Modify `docs/manual/02-language-tour.md`

- [ ] **Étape 1 — Éditer** (findings 3.4 C1 + C3)
  - **C1 (L109-118)** : `SHAPE:{` multiligne → single-line (pattern SHAPE supra).
  - **C3 (L22)** : retirer le claim `MODE: auto` « parsé, runtime pas encore implémenté » — il est
    **rejeté au parse**. Retrait trivial du `auto` (cohérent avec l'élagage V de PR-B).
- [ ] **Étape 2 — Vérifier**
  Extraire le bloc SHAPE corrigé dans `/tmp/a5.clio`, puis :
  Run: `uv run python -m clio check /tmp/a5.clio` → `ok`.
  Run: `grep -ni "auto" docs/manual/02-language-tour.md` → 0 mention de `MODE: auto`.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/02-language-tour.md
  git commit -m "docs(tour): single-line SHAPE snippet, drop MODE:auto claim"
  ```

### Task A6 : `docs/manual/03-cookbook.md`

**Files:** Modify `docs/manual/03-cookbook.md`

- [ ] **Étape 1 — Éditer** (findings 3.4 C2/C4/C1/C3 + M2)
  - **C2** : `invoke:{…}` inline ×4 (#18/#25) → bloc indenté (pattern invoke supra). #18 est annoté
    « Sketch (compilable) » alors qu'il ne compile pas → doit compiler après fix.
  - **C4 (#8, L192-198)** : condition `IF` wrappée → une ligne logique (pattern IF supra).
  - **C1 (#27)** : `SHAPE:{` multiligne → single-line.
  - **C3** : retirer le claim `auto`.
  - **M2 (#15)** : le layout claude-skill omet le sidecar `.clio/` et sur-affiche
    `schemas/01_greet.input.json` (non émis car `greet` n'a pas de `TAKES`). Corriger le layout.
- [ ] **Étape 2 — Vérifier**
  Extraire chaque bloc clio corrigé (#8, #18, #25, #27) dans `/tmp/a6_*.clio`, puis pour chacun :
  Run: `uv run python -m clio check /tmp/a6_<n>.clio` → `ok`.
  Run: `grep -ni "auto" docs/manual/03-cookbook.md` → 0 mention de `MODE: auto`.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/03-cookbook.md
  git commit -m "docs(cookbook): block invoke, single-line SHAPE, unwrap IF, fix skill layout"
  ```

### Task A7 : `docs/manual/01-getting-started.md` + `docs/manual/README.md`

**Files:** Modify `docs/manual/01-getting-started.md`, `docs/manual/README.md`

- [ ] **Étape 1 — Éditer** (findings 3.4 / 3.6)
  - **01 M1 (L56)** : `check` imprime `ok`, pas « No output ». Corriger.
  - **01 m1** : le CLI imprime `error: line L:C: …` (la classe `ParseError` n'est pas surfacée) —
    corriger l'affirmation « tu auras un `ParseError` ».
  - **README m (L15)** : la prose d'index omet `go` → l'ajouter.
- [ ] **Étape 2 — Vérifier**
  Run: `grep -ni "no output" docs/manual/01-getting-started.md` → 0.
  Run: `grep -n "go" docs/manual/README.md` → `go` listé.
- [ ] **Étape 3 — Commit**
  ```bash
  git add docs/manual/01-getting-started.md docs/manual/README.md
  git commit -m "docs(manual): check prints ok, real parse-error surface, list go"
  ```

### Task A8 : Commentaires de code périmés

**Files:** Modify `clio/emitters/go.py:14`, `clio/ir/resolver.py`

- [ ] **Étape 1 — Éditer** (annexe audit / cluster 4)
  - `go.py:14` : docstring « refuses RESUME » est **stale** — le code émet bien RESUME
    (`_go_flow_renderer.py:330-356`). Corriger le commentaire.
  - `resolver.py` : retirer les codes `E_RES_*/E_VIS_*/E_IMP_*` des docstrings/commentaires (jamais
    levés — `CompileError` à chaînes nues). **Commentaires seulement, pas de changement de
    comportement.**
- [ ] **Étape 2 — Vérifier** (aucune régression)
  Run: `uv run ruff check . && uv run mypy && uv run pytest -q` → tout vert.
  Run: `grep -rn "E_RES_\|E_VIS_" clio/ir/resolver.py` → **0**.
- [ ] **Étape 3 — Commit**
  ```bash
  git add clio/emitters/go.py clio/ir/resolver.py
  git commit -m "chore(comments): fix go RESUME docstring, drop unraised resolver error codes"
  ```

### Task A9 : Vérification PR-A + ouverture PR

- [ ] **Étape 1 — Sweep complet**
  Run: `grep -rn "E_GO_011\|E_CLI_001" docs/` → **0**.
  Run: `grep -rln "up to 3 retries" docs/` → **0**.
  Run: `uv run ruff check . && uv run mypy && uv run pytest -q` → vert.
- [ ] **Étape 2 — Push + PR**
  ```bash
  git push -u origin docs/refonte-honesty-mechanical
  gh pr create --fill --title "docs(refonte A): mechanical drift cleanup (axis M)"
  ```
  Puis discipline repo : CI verte → `/gemini review` → traiter le cycle → merger.

---

## PR-B — Réécriture des docs d'identité (axes V + G)

**Branche :** `docs/refonte-honesty-editorial` (depuis `main` post-PR-A).
**Périmètre :** `docs/ARCHITECTURE.md`, `docs/LANGUAGE_SPEC.md`, `CLAUDE.md`. C'est le cœur
éditorial controversé — réécriture, pas micro-édits.

### Task B1 : `docs/ARCHITECTURE.md` — réécriture fidèle

**Files:** Modify `docs/ARCHITECTURE.md`

> Pas de prose pré-écrite ici : la réécriture s'appuie sur le code réel (cf. valeurs ci-dessous) et
> l'énoncé north star (« le compilateur émet ; il ne décide/optimise/infère/exécute pas »). La
> tâche fournit la **vérité-terrain** à refléter + la vérification.

- [ ] **Étape 1 — Élaguer la vision (axe V)** (findings 3.2 C1/C4 + M4)
  - Retirer **`ir/optimizer.py`** + l'étage « Optimizer » (batching / context-budgeting /
    model-routing) — n'existe pas ; pipeline réel `cli.py:128` = `resolve_imports → build_ir →
    emit`.
  - Retirer **`ContractValidator`** (0 occurrence dans `clio/`).
  - Retirer **inférence MODE/LANG** + insertion de steps `summarize` implicites — MODE parsé
    verbatim (`parser.py:555` → `builder.py:1035`), LANG verbatim (`graph.py:225`).
- [ ] **Étape 2 — Corriger les faits structurels** (findings 3.2 C2/C3/M1/M2/M3/M5/M6/M7 + m1-m4)
  - **Noms de nœuds AST** : `StepDecl` (`ast_nodes.py:68`), `ContractDecl` (:92), `FlowDecl`
    (:219), `ForEachBlock` (:125) — suffixe `Decl`/`Block`, **jamais `Node`**.
  - **Invariant « emitters ne s'importent jamais » (C3)** : **documenter la réalité** — ils
    composent via `_*_helpers`, et `langgraph.py:36` délègue à `PythonEmitter` ;
    `mcp_server.py:20`/`python.py:295`/`_mcp_helpers.py:1051-1052` importent des helpers partagés.
    Présenter l'isolation stricte comme idéal **non tenu**, pas comme invariant. (Décision spec
    §6.2 : doc, pas de refactor.)
  - **Passes IR (M1)** : décrire les vraies passes — passe 0.5 signatures (`builder.py:829`), un
    `_build_flow` par flow, détection de cycles d'appels (`_detect_flow_call_cycles:224`).
  - **Resolver (M2/M3)** : `resolve_imports` appelé par `cli.py` (pas le builder) ; merge
    alpha-rename dans `builder.py:_flatten_to_program`.
  - **`contracts.py` (M5)** : ~74 lignes, une fonction `type_to_json_schema` — pas de Pydantic ni
    de génération de code.
  - **`go` (M6/m4)** : ajouter au diagramme pipeline + Layer 3 (6 emitters, pas 5).
  - **`BaseEmitter.emit` (M7)** : signature inclut `source_path`, `sources`.
  - **Mineurs** : documenter `parser/{tokens,expressions}.py` (m1), `ir/types.py` (m2) ; count réel
    = **98 keywords** (`keywords.py`), pas « ~20 » (m3).
- [ ] **Étape 3 — Vérifier**
  Run: `grep -rn "optimizer\|ContractValidator\|StepNode\|ContractNode\|FlowNode\|ForEachNode" docs/ARCHITECTURE.md`
  → **0**.
  Run: `grep -n "go" docs/ARCHITECTURE.md` → présent dans diagramme + Layer 3.
- [ ] **Étape 4 — Commit**
  ```bash
  git add docs/ARCHITECTURE.md
  git commit -m "docs(arch): prune unbuilt optimizer/validator/inference, describe real pipeline"
  ```

### Task B2 : `docs/LANGUAGE_SPEC.md` — élagage G + cohérence

**Files:** Modify `docs/LANGUAGE_SPEC.md`

- [ ] **Étape 1 — Couper la grammaire non implémentée (axe G)** (findings 3.1 C1-C9)
  Retirer (ou re-encadrer comme inexistant) :
  - **C1** `MODE: auto` (L116/L119/L130-133) — parser n'accepte que `{exact, judgment}`.
  - **C2** `VALIDATE:` sur STEP (L122, et le **§Example canonique** L1403/L1415).
  - **C3** `CONFIDENCE:` sur CONTRACT (L150/L160).
  - **C4** `Set<T>` (L1318) — pas de `SetType`.
  - **C5** domain types `JSON/Log/Email/URL/Markdown` (L1377-1384) — **garder `CSV`** (seul réel,
    `parser.py:2057-2059`).
  - **C6** `invoke.mode: embedded` (L477-498) + `mcp_sampling` (L500-519) — `_VALID_INVOKE_MODES =
    {"cli","api"}`.
  - **C7** `impl.mode: binary` (L409-424) — `_VALID_IMPL_MODES = {code,rest,shell,mcp_tool,sql}`.
  - **C8** champs invoke CLI `allowed_tools/permission_mode/append_system_prompt/session`
    (L440-449).
  - **C9** champs invoke API `response_format/extra_headers/extra_body` (L460-473).
- [ ] **Étape 2 — Faire parser le §Example canonique**
  Après retrait de `VALIDATE:`/`auto`, le §Example doit parser.
  Extraire le §Example dans `/tmp/spec_example.clio`, puis :
  Run: `uv run python -m clio check /tmp/spec_example.clio` → `ok`.
- [ ] **Étape 3 — Cohérence (findings 3.1 M1-M5 + m1/m2)**
  - **M1** : titre « v0.2 » périmé (le doc décrit jusqu'à v0.22) → mettre à jour.
  - **M2** : ligne de table de statut `FLOW.TAKES/GIVES ❌` contredit le reste (implémenté
    `parser.py:2222-2249`) → corriger.
  - **M3** : escape `#88` (`\"`/`\\`) est générique au scanner STRING (`lexer.py:77-87`), pas
    shell-only → reformuler.
  - **M4** : limitation sidecar `#67` périmée (multi-file livré v0.22) → mettre à jour.
  - **M5/m1/m2** : `output_format` non validé/sans défaut ; défaut LANG `auto` stocké `None` ;
    colonne `go` ambiguë dans la table de statut.
- [ ] **Étape 4 — Vérifier**
  Run: `grep -rn "MODE: auto\|VALIDATE:\|CONFIDENCE:\|Set<\|invoke.mode: embedded\|mcp_sampling\|impl.mode: binary" docs/LANGUAGE_SPEC.md`
  → **0** (hors mentions explicites « non supporté »).
  Run: `uv run python -m clio check /tmp/spec_example.clio` → `ok`.
- [ ] **Étape 5 — Commit**
  ```bash
  git add docs/LANGUAGE_SPEC.md
  git commit -m "docs(spec): prune unimplemented grammar, make canonical example parse"
  ```

### Task B3 : `CLAUDE.md` — pitch honnête + File structure

**Files:** Modify `CLAUDE.md`

- [ ] **Étape 1 — Corriger le pitch (axe V)**
  Section « What this project is ». Remplacer :
  ```
  The compiler decides what runs as code and what runs as LLM, based on the `MODE` field
  (`exact`, `judgment`, `auto`).
  ```
  par (rédaction au plus proche du style, sens = north star) :
  ```
  The author marks each STEP's `MODE` (`exact` or `judgment`); the compiler emits deterministic
  code for `exact` steps and LLM-call scaffolding for `judgment` steps. It does not decide, infer,
  or execute — it emits.
  ```
- [ ] **Étape 2 — Compléter « File structure »** (findings 3.6 C1-C4 — drift uni-directionnel,
  aucun chemin fantôme à retirer ; **ajouts** seulement)
  - `runtime/` : ajouter `substitute.py`, `validate.py`.
  - `parser/` : ajouter `expressions.py`, `tokens.py`.
  - `ir/` : ajouter `types.py`.
  - top-level : ajouter `__main__.py`, `_llm_validation.py`, `diagnostics.py`, `graph_render.py`,
    `keywords.py`.
- [ ] **Étape 3 — Vérifier**
  Run: `grep -n "auto" CLAUDE.md` → plus de `MODE … auto` dans le pitch.
  Run: `grep -n "keywords.py\|graph_render.py\|diagnostics.py" CLAUDE.md` → présents.
- [ ] **Étape 4 — Commit**
  ```bash
  git add CLAUDE.md
  git commit -m "docs(claude): honest deterministic-compiler pitch, complete file structure"
  ```

### Task B4 : Vérification PR-B + ouverture PR + critères de succès du spec

- [ ] **Étape 1 — Critères de succès (spec §7)**
  Run: `grep -rn "MODE: auto\|optimizer\|ContractValidator\|E_GO_011\|E_CLI_001" docs/ CLAUDE.md`
  → **0** (hors mentions explicites « retiré/non supporté »).
  Run: `uv run python -m clio check /tmp/spec_example.clio` → `ok`.
  Run: `uv run pytest -q && uv run ruff check . && uv run mypy` → vert.
- [ ] **Étape 2 — Push + PR**
  ```bash
  git push -u origin docs/refonte-honesty-editorial
  gh pr create --fill --title "docs(refonte B): honesty rewrite of identity docs (axes V+G)"
  ```
  Puis discipline repo : CI verte → `/gemini review` → traiter le cycle → merger.

---

## Hors scope (rappel spec §5)

- ❌ Construire auto / optimizer / grammaire G.
- ❌ Refactor du code des emitters (l'invariant violé est **documenté**, pas corrigé).
- ❌ Diff exhaustif des ~70 chaînes d'erreur `06-troubleshooting.md:25-518` (différé/flaggé ;
  seules F9/F10 fixées).
