# Refonte « honnêteté » — Spec de scope — 2026-06-04

> **But.** Figer le périmètre de la refonte conséquente préparée par l'audit
> `docs/audit-drift-2026-06-02.md`. Ce document n'édite aucune doc de référence ; il décide
> **ce qui est dans / hors** de la refonte, dans quel ordre, et comment on vérifie.
>
> **Statut.** Spec de scope approuvée (décisions V et G tranchées le 2026-06-04). Le détail
> ligne-à-ligne du drift vit dans l'audit, qui sert de checklist au plan d'implémentation.

---

## 1. Décisions tranchées

L'audit (§4) isolait deux décisions produit. Elles sont tranchées :

| Axe | Contenu | Décision |
|---|---|---|
| **V — Vision** | `MODE: auto`, étage Optimizer, `ContractValidator`, inférence MODE/LANG, model-routing | **Tout élaguer.** Vision abandonnée — clio ne deviendra pas « le compilateur décide ». |
| **G — Grammaire** | `VALIDATE`, `CONFIDENCE`, `Set<T>`, domain types (hors `CSV`), `invoke.mode embedded/mcp_sampling`, `impl.mode binary`, champs invoke en trop | **Élaguer vers la réalité.** La SPEC ne décrit que ce que le parser accepte. |

Ces deux choix sont cohérents avec la discipline déjà inscrite dans `CLAUDE.md` (« What NOT to
build (yet) » : pas de runtime, pas de model-routing dans le compilateur).

## 2. Identité-cible (north star)

> **clio est un compilateur déterministe.** Il transforme un programme `.clio`
> (STEP / CONTRACT / FLOW, mode `exact` | `judgment` **choisi explicitement par l'humain**) en 6
> cibles exécutables. Il **ne décide pas, n'optimise pas, n'infère pas, n'exécute pas — il émet.**
> La doc de référence ne décrit **que** ce que le parser accepte et ce que les emitters produisent.

Toute la refonte se mesure à cet énoncé : une phrase de doc qui le contredit est du drift à
corriger ; une phrase qui le respecte reste.

## 3. Nature du chantier

V et G sont **~98 % documentaires** : `auto` et la grammaire G n'ont **jamais été codés** (le
parser les rejette déjà), donc il n'y a **rien à démonter dans le code**. Le seul code touché =
quelques commentaires/docstrings périmés. Risque faible.

## 4. Scope — DANS

| Doc | Travail | Findings audit | Poids |
|---|---|---|:--:|
| `docs/ARCHITECTURE.md` | **Réécriture fidèle** : retirer optimizer / `ContractValidator` / inférence MODE-LANG / steps `summarize` implicites ; corriger noms de nœuds AST (`*Decl`/`*Block`), passes IR réelles, propriété du resolver, signature `BaseEmitter.emit`, count keywords (98), ajouter `go` au diagramme/Layer 3 | 3.2 (C1-C4, M1-M7, m1-m4) | 🔴 |
| `docs/LANGUAGE_SPEC.md` | **Élagage G** : couper `auto`, `VALIDATE`, `CONFIDENCE`, `Set<T>`, domain types (garder `CSV`), `invoke.mode embedded/mcp_sampling`, `impl.mode binary`, champs invoke en trop ; **faire parser le §Example canonique** ; retitrer « v0.2 », fix statut `FLOW.TAKES/GIVES`, reframe escape `#88`, fix note sidecar `#67`, `output_format` | 3.1 (C1-C9, M1-M5, m1-m2) | 🔴 |
| `CLAUDE.md` | Corriger **le pitch (ligne 1)** : retirer « auto » et « le compilateur décide… selon MODE » ; compléter le bloc « File structure » (~10 modules réels) | 3.6 (C1-C4) | 🟠 |
| `docs/COMPILATION_TARGETS.md` + `docs/manual/04-targets.md` | Tuer `E_GO_011` fantôme ; requalifier Go `--from-step` « refusé » → « non implémenté » (issue #83) ; `langgraph` « Candidate » → livré ; compléter listes de refus (langgraph `WHILE`/`impl.mode sql`), nuancer `E_MCP_001` | 3.3 (C1-C2, M3-M5) | 🟠 |
| `docs/manual/02-language-tour.md` · `03-cookbook.md` | Reflow snippets cassés (`SHAPE:{` single-line, `invoke:{}` → bloc, `IF` dé-wrappé) ; retirer claims `auto` ; fix layout claude-skill (#15) | 3.4 (C1-C4, M2) | 🟢 |
| `docs/manual/05-cli-reference.md` · `06-troubleshooting.md` | **Critiques** : `go` dans `compile --target`, message validation 5→6 cibles, tuer `E_CLI_001` ; + flags `doctor`, `check` imprime `ok`, `gen` 1 retry | 3.5 (F1, F9, F10, F3-F5) | 🟠 |
| `docs/manual/01-getting-started.md` · `manual/README.md` | `check` imprime `ok` (≠ « No output ») ; surfaçage réel `error: line L:C:` ; `go` dans la prose d'index | 3.4 / 3.6 | 🟢 |
| Code (commentaires seulement) | `clio/emitters/go.py:14` docstring « refuses RESUME » périmé ; couper les codes `E_RES_*/E_VIS_*` des docstrings `resolver.py` | annexe / cluster 4 | 🟢 |

## 5. Scope — HORS (explicite)

- ❌ **Construire quoi que ce soit** (auto, optimizer, grammaire G) — inverse de la décision.
- ❌ **Refactor du code des emitters.** L'invariant « emitters ne s'importent jamais » est violé
  dans le code (`langgraph.py` → `PythonEmitter` ; `*_helpers` partagés). On **documente la
  réalité** (sous-décision 2), on ne refactore pas.
- ❌ **Diff exhaustif des ~70 chaînes d'erreur** `06-troubleshooting.md:25-518` (non fait par
  l'audit). On fixe les 2 critiques confirmées (F9/F10). L'audit string-level complet est
  **différé et flaggé** — à faire si une garantie string-level totale devient requise.

## 6. Sous-décisions (résolues)

1. **Packaging & séquencement.**
   1. **Merger #90 d'abord** (audit baseline ; CI verte) → figer la référence sur `main`.
   2. **PR-A — mécanique** (axe M, non controversé) : propager `go`, tuer codes/chaînes fantômes,
      reflow snippets, micro-drifts.
   3. **PR-B — éditoriale** (axes V/G) : réécriture `ARCHITECTURE.md`, élagage `LANGUAGE_SPEC.md`,
      pitch `CLAUDE.md`.
   - Chaque PR suit la discipline repo (branche + PR + revue Gemini).
2. **Invariant « emitters ne s'importent jamais ».** → **Documenter la réalité** (composition via
   `_*_helpers`, délégation `langgraph` → `PythonEmitter`), en notant l'isolation stricte comme
   idéal non tenu. Pas de refactor de code.
3. **Codes `E_RES_*/E_VIS_*`** (en docstrings, jamais levés) → **couper des docstrings**
   `resolver.py`, plutôt que les faire surfacer (cohérent avec l'honnêteté).

## 7. Critères de succès (vérifiables)

- Tout snippet `.clio` des docs de référence passe `uv run python -m clio check` (les 6 snippets
  cassés recensés parsent, **§Example de la SPEC inclus**).
- `grep` de `auto` / `optimizer` / `ContractValidator` / `E_GO_011` / `E_CLI_001` dans les docs de
  référence → **0 occurrence** (hors mentions explicites « retiré / non supporté »).
- Toute énumération de cibles dans les docs liste les **6** (`go` inclus).
- `uv run pytest` + `uv run ruff check .` + `uv run mypy` restent verts (touch-ups de
  commentaires sans impact comportemental).

## 8. Référence

Détail ligne-à-ligne du drift : `docs/audit-drift-2026-06-02.md` (30+ findings, 6 clusters).
Ce spec en est la **résolution de scope** ; le plan d'implémentation en dérivera la checklist
ordonnée.
