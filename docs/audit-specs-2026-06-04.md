# Audit specs approfondi (post-refonte) — 2026-06-04

> **But.** Revue générale « au carré » de TOUTES les docs après la refonte honnêteté (#90-#94).
> Orientée **vérification** : 6 agents read-only ont croisé chaque doc contre le code vivant et
> **exécuté** ce qui est exécutable (`clio check` sur snippets, `clio compile`, `grep` des chaînes
> d'erreur). Cible ce que l'audit initial **et** la refonte ont raté.
>
> **Méthode.** 6 agents, 16 docs, 9 dimensions (vision / grammaire rejetée / snippets qui parsent /
> CLI↔cli.py / codes d'erreur réels / 6 cibles / versions-counts / chemins existent / liens).
> ~75 findings. **2 findings haute-confiance re-vérifiés en direct par l'orchestrateur (justes).**
>
> **Machine.** `python`/`python3` cassés (3.9) → `uv run python -m clio` uniquement.

---

## 1. Tableau de bord

| Doc | Verdict | C | M | m |
|---|---|:--:|:--:|:--:|
| `README.md` | 🔴 | 6 | 2 | 2 |
| `docs/LANGUAGE_SPEC.md` | 🔴 | 4 | 5 | 4 |
| `docs/COMPILATION_TARGETS.md` | 🔴 | 3 | 1 | 1 |
| `docs/manual/06-troubleshooting.md` | 🔴 | 0 | ~24 | 2 |
| `docs/manual/03-cookbook.md` | 🔴 | 1 | 4 | 0 |
| `docs/manual/04-targets.md` | 🔴 | 2 | 2 | 1 |
| `docs/manual/06-migration-v018.md` | 🔴 | 1 | 0 | 0 |
| `docs/POSITIONING.md` | 🔴 | 2 | 4 | 1 |
| `docs/COMPARISON_OPENPROSE.md` | 🔴 | 3 | 3 | 2 |
| `docs/ARCHITECTURE.md` | 🟡 | 0 | 3 | 0 |
| `docs/manual/02-language-tour.md` | 🟡 | 0 | 3 | 1 |
| `CLAUDE.md` | 🟡 | 0 | 1 | 1 |
| `CHANGELOG.md` | 🟡 | 0 | 2 | 2 |
| `docs/manual/01-getting-started.md` | 🟡 | 0 | 1 | 0 |
| `examples/README.md` | 🟡 | 0 | 1 | 1 |
| `docs/manual/05-cli-reference.md` | 🟡 | 0 | 1 | 0 |
| `docs/manual/README.md` | 🟢 | 0 | 0 | 0 |

**Lecture.** Le drift se concentre (a) sur les **2 docs jamais auditées** (POSITIONING,
COMPARISON_OPENPROSE — pleines de cibles/features fantômes), (b) sur la **traîne de chaînes
d'erreur** de `06-troubleshooting` (le NEEDS-HUMAN-CHECK assumé), (c) sur des **fuites de vision**
en façade (README) que mes sweeps grep ont ratées, et (d) **deux bugs doc↔code confirmés** que la
refonte a perpétués.

---

## 2. Findings confirmés en direct (haute confiance, orchestrateur)

### CONF-1 — `RESOURCES.impl` / `RESOURCES.invoke` sont REJETÉS au parse
`parse_resources` (`parser.py:415-468`) n'accepte QUE `target`, `models`, `mcp_servers`,
`databases`. `impl`/`invoke`/`lang` tombent dans `else: raise ParseError("unexpected RESOURCES
field")`. **La refonte B2 a gardé à tort `impl:`/`invoke:` dans le bloc RESOURCES** (`LANGUAGE_SPEC`)
et laissé toute la section « Override semantics for impl and invoke » (qui décrit un shallow-merge
inexistant). → Bloc RESOURCES à corriger + section à supprimer + L45/L404 à reformuler.

### CONF-2 — `target: claude-cli` CRASHE sur IF / MATCH / WHILE
`uv run python -m clio compile examples/feedback_routing.clio --target claude-cli` →
`AttributeError: 'IfBlockIR' object has no attribute 'step_name'` (`claude_cli.py:223`). L'émetteur
claude-cli ne gère QUE `ForEachIR`/`CallIR` ; IF/MATCH/WHILE crashent (pas de refus propre). Or
`COMPILATION_TARGETS` (L46-49) et `04-targets` annoncent le support bash if/case/while. → drift
critique : doc annonce une feature qui **plante**. (Connexe : `LexError` n'est pas catché par
`cli.py` → traceback au lieu de `error: line L:C:` — fix code mineur, hors scope doc.)

---

## 3. Findings par doc

### 3.1 `README.md` — 🔴 (6C / 2M / 2m)
- **[C] L19** dim1 — « the compiler **decides** what runs as code vs LLM » → l'auteur choisit ; le compilateur émet.
- **[C] L27** dim2 — exemple hero utilise `VALIDATE:` → ne parse pas. Retirer la ligne.
- **[C] L41** dim2 — MODE « …or `auto` (compiler decides) » → retirer `auto`.
- **[C] L42** dim2 — CONTRACT « (SHAPE, ASSERT, `CONFIDENCE`) » → retirer CONFIDENCE.
- **[C] L46** dim1 — « optimizes it (batching, context budgeting, model routing) » → « validates it ».
- **[C] L224** dim2 — judgment invocation liste `mcp_sampling` comme mode → invoke.mode ∈ {cli,api} ; le sampling est interne au target mcp-server.
- **[M] L53** dim1 — mermaid output « bash / Python / **Docker** / … » → Docker n'est pas une sortie. Lister les vraies.
- **[M] L190** dim1 — « `ir/` # … **optimization** » → retirer.
- **[m] L214** dim7 — « 1270 unit tests » → réel 1281 (épinglé v0.23.0 ; clarifier).
- **[m] L70** dim6 — table cibles inclut « rust / docker (planned) » → déplacer en note prose, garder la table à 6.

### 3.2 `docs/LANGUAGE_SPEC.md` — 🔴 (4C / 5M / 4m)
- **[C] L45 / L404 / L758-765 / L858-884** dim2 — `RESOURCES.impl`/`invoke` + section « Override semantics » = **CONF-1** (rejetés au parse). Supprimer impl/invoke du bloc RESOURCES, supprimer la section override, reformuler L45 (« defaults at RESOURCES level ») et L404 (« falls back to RESOURCES.invoke »).
- **[C] L477** dim4 — mentionne un flag `clio compile --kwargs '{…}'` inexistant (`cli.py` n'a que source/--target/--output/--flow). Retirer.
- **[M] L165-170 / L272-278** dim3 — exemples impl `code`/`shell` utilisent `TAKES: file: Path` → `Path` n'est pas un type (`unknown contract reference 'Path'`). Remplacer par `str`.
- **[M] L427 / L434** dim3 — exemple invoke api : `model: gemini-1.5-pro` et `base_url: http://…` non quotés → LexError. Mettre entre guillemets.
- **[M] L1089-1099** dim6 — table cibles RESCUE liste seulement python/mcp-server ✓ ; or `claude-skill` ET `go` gèrent RESCUE (v0.23). Ajouter ✓.
- **[M] L51-76** dim6 — table de statut v0.2 : la colonne `go` n'est remplie que sur la 1re ligne (vides ensuite). Remplir ou retirer la colonne.
- **[m]** divers : L45 mention historique « defaults at RESOURCES » (couvert par C), formulation v0.2.
*(Aligné re-vérifié : MODE/invoke/impl/domain-types/containers/CONTRACT, AST names, emit sig, contracts.py, resolver ownership, §Example parse, 6 cibles, version v0.23.)*

### 3.3 `docs/ARCHITECTURE.md` — 🟡 (3M)
- **[M] L37** dim9 — `exposed_flow_names` décrit comme « not called by a sibling » (heuristique v0.17 **supprimée** en v0.18, `builder.py:257`) ; c'est désormais le marqueur `EXPOSE`. Corriger.
- **[M] L39** dim9 — dit `contracts.py` attache `x-clio-assert` ; en réalité c'est `builder.py:173`. Réattribuer.
- **[M] L79** dim9 — « emitters depend only on the IR — never on the parser » : faux, 5 helpers importent `clio.parser.ast_nodes` (TypeExpr subtypes). Nuancer (« …and shared type nodes from ast_nodes »).

### 3.4 `docs/COMPILATION_TARGETS.md` — 🔴 (3C / 1M / 1m)
- **[C] L46-49** dim3/6 — claude-cli WHILE/IF-ELSE/MATCH-CASE annoncés supportés = **CONF-2** (crash AttributeError). Passer en ❌ non supporté.
- **[C] L187** dim5 — « source sans FLOW = no-op » pour mcp-server → en réalité **erreur dure** `ValueError` (`mcp_server.py:147`). Corriger.
- **[M] L13** dim7 — langgraph `Effort: Medium` alors qu'il est livré → `—`.
- **[M] L297** dim9 — lien #67 « track for the multi-file extension » → #67 **fermé** (livré v0.22). Reformuler.
- **[m] L121/127/200** dim7 — stamps « v0.4+ » périmés.

### 3.5 `docs/manual/04-targets.md` — 🔴 (2C / 2M / 1m)
- **[C] L174-175** dim6 — matrice : claude-cli `LANG: go/auto` et `python/bash/…` marqués ✅ alors que LANG est **silencieusement ignoré** (toujours stub Python). Passer en ⚠️.
- **[M] L100-101 / L206** dim1 — milestones périmés « planned for v0.7 / v0.8 / v0.7+ » → « planned (not yet shipped) ».
- **[m] L25** dim6 — nuance jq/state.json.

### 3.6 `docs/manual/01·02·03` (usage)
**01 — 🟡 (1M) :** [M] L111 « the compiler decides what runs as code vs LLM » → reformuler (vision).
**02-language-tour — 🟡 (3M/1m) :** [M] L87 défaut invoke mal décrit (python → SDK Anthropic, pas cli) · [M] L153 WHILE compile aussi claude-skill+go (pas que python/mcp) · [M] L220-222 RESCUE compile aussi go · [m] L150 « in v0 » → « in v1 ». *(14 snippets testés, 14 pass.)*
**03-cookbook — 🔴 (1C/4M) :** [C] L55-58 recette 3 SHAPE multi-ligne + `…` placeholders → ne parse pas · [M] L213/L258/L559 RESCUE/WHILE target-coverage sous-listée (manque claude-skill+go) · [M] L655 `examples/risk.clio` n'existe pas. *(26 snippets, 1 FAIL, reste pass/fragments.)*

### 3.7 `docs/manual/05·06` (reference)
**05-cli-reference — 🟡 (1M) :** [M] L27 `ValueError` emitter présenté comme géré → en réalité traceback non catché.
**06-troubleshooting — 🔴 (~24M / 2m) :** la traîne de chaînes d'erreur (le weak-spot connu) :
- **Préfixes de classe inexistants/non surfacés** : `ResolveError:` (E_RES_001-006) n'existe pas → `CompileError` ; `IRBuildError:` (E_VIS_003/004) → `CompileError` ; `E_MCP_001 ValueError:` → `CompileError`. **La classe n'est JAMAIS montrée à l'utilisateur** (`cli.py` imprime `error: {e}`).
- **Chaînes E_GO_* fausses** : E_GO_001/002/003/005/009/010/012 — les messages cités (`target=go …`) ne matchent pas le code (`target: go …`, « does not yet », qualificatifs). Manquent **E_GO_004** et **E_GO_013** (réels, non documentés).
- **Chaînes E_IMP_/E_RES_/E_VIS_** : wording divergent (path absolu vs relatif, `→` vs `->`, sens inversé pour E_RES_006).
- **Doublons/truncations** : « unknown STEP » (L25) vs message réel complet ; RESCUE body (L182 vs L200) = un seul message réel.
**06-migration-v018 — 🔴 (1C) :** [C] L48-53 prétend qu'un backup `.bak` est créé → **faux**, `cli.py:265` `write_text` en place sans backup. Retirer la promesse `.bak`.
**manual/README — 🟢 (0).**

### 3.8 `CLAUDE.md` — 🟡 (1M / 1m)
- **[M] L76** dim1 — « IR : … **optimizable** » → implique un optimizer ; remplacer par « immutable ». *(que mes sweeps ont raté)*
- **[m] L114** dim8 — « builder.py (4 passes) » imprécis (3 passes nommées + flow-build).

### 3.9 `CHANGELOG.md` — 🟡 (2M / 2m)
- **[M] L218** dim5 — entrée cite `E_CLI_001` (fabriqué) → aligner avec la refonte (retirer le label).
- **[M] L74** dim7 — v0.21.0 : « 1136→1188 (+52) » vs section Tests « 1136→1184 (+48) » incohérent.
- **[m] L191** dim5 — « E_RES_006 message » (label non surfacé) · **[m] L1070** dim2 — entrée v0.4.0 liste `binary` comme impl.mode futur (jamais livré). *(Historique : à annoter plutôt qu'à réécrire — décision.)*

### 3.10 `docs/POSITIONING.md` — 🔴 (2C / 4M / 1m) — JAMAIS AUDITÉ
- **[C] L59** — cible vers `temporal` / `step-functions` (inexistants) présentés comme actuels.
- **[C] L52** — « static cost analysis ($/run) » comme capacité présente (inexistante).
- **[M] L17/L85/L95/L250** — `mcp-server` listé « planned/future » alors que livré ; `temporal` listé comme s'il existait ; « **five** targets » → six.
- **[m] L32** — « idiomatic … or Rust » → pas d'émetteur Rust ; mettre Go.

### 3.11 `docs/COMPARISON_OPENPROSE.md` — 🔴 (3C / 3M / 2m) — JAMAIS AUDITÉ
- **[C] L5/L17/L44/L48** — `impl: …binary` et `invoke: …embedded / mcp_sampling` (fantômes, rejetés).
- **[C] L31** — « v0.19, 1067+ tests, **5 targets** » → v0.23.0, ~1300 tests, 6 cibles.
- **[M] L23/L43** — « 5 shipped » → 6 (manque go).
- **[m] L25/L84** — count tests « 1000+/1067+ » incohérent ; « drives a sprint » (PR mergée).

### 3.12 `examples/README.md` — 🟡 (1M / 1m) — JAMAIS AUDITÉ
- **[M] L3-6** — « **three** compilable files » → le doc lui-même a 5 sections, et `examples/` contient **14** `.clio` ; la raison d'exclusion claude-cli de classify_corpus est fausse (c'est `FOR EACH`+judgment, pas `invoke.protocol: openai`).
- **[m]** — 11 fichiers `.clio` présents mais non documentés + `multi_file/`. Ajouter un index.

---

## 4. Thèmes transverses (root cause)

1. **Vision en façade (README/CLAUDE)** — mes sweeps PR-B étaient **pattern-limités** (`optimizer`≠« optimizes », `MODE: auto`≠« auto (decides) ») → la prose marketing a survécu. Leçon : grep ≠ relecture sémantique.
2. **Bugs doc↔code perpétués (CONF-1/CONF-2)** — RESOURCES.impl/invoke et claude-cli control-flow : la refonte a fait confiance à la doc existante au lieu de **compiler** un exemple. Leçon : exécuter, pas lire.
3. **Docs jamais auditées** (POSITIONING, COMPARISON_OPENPROSE, examples/README) — angle mort total de l'audit initial. Cibles/features fantômes (temporal, step-functions, cost-analysis, binary, embedded, mcp_sampling) + counts périmés.
4. **Traîne de chaînes d'erreur** (`06-troubleshooting`) — préfixes de classe qui n'existent pas / ne surfacent pas + wording divergent. C'est le NEEDS-HUMAN-CHECK assumé, maintenant chiffré (~26).
5. **Target-coverage sous-listée** — claude-skill + go gèrent WHILE/RESCUE ; plusieurs docs ne listent que python/mcp-server.

---

## 5. Proposition de batching des corrections (PRs)

Indépendantes, faible risque sauf indiqué :

- **PR-1 — README + CLAUDE (façade)** : §3.1 + §3.8. Haute visibilité publique.
- **PR-2 — LANGUAGE_SPEC (CONF-1 + reste)** : §3.2. Inclut le fix RESOURCES.impl/invoke + section override.
- **PR-3 — Targets (CONF-2)** : §3.4 + §3.5 (claude-cli control-flow ❌, LANG ignoré ⚠️, mcp no-FLOW=erreur). ⚠️ *Question : doit-on aussi **fixer le code** pour que claude-cli REFUSE proprement IF/MATCH/WHILE au lieu de crasher ? (hors scope doc — décision).*
- **PR-4 — Manual usage** : §3.6 (cookbook recette 3, target-coverage, risk.clio) + ARCHITECTURE §3.3.
- **PR-5 — 06-troubleshooting + migration** : §3.7. Le plus volumineux ; décision sur la convention « préfixe de classe ».
- **PR-6 — Docs jamais auditées** : §3.10 + §3.11 + §3.12 (POSITIONING, COMPARISON, examples/README).
- **PR-7 — CHANGELOG** : §3.9 — décision « annoter vs réécrire l'historique ».

**Décisions tranchées (2026-06-04)** :
- (a) claude-cli IF/MATCH/WHILE → **doc seule** : marquer ❌ non supporté dans les matrices ; le crash code (`AttributeError`) et le `LexError` non-catché restent (hors scope refonte doc-only).
- (b) `06-troubleshooting` préfixes de classe → **stripper** : retirer `ParseError:`/`IRBuildError:`/`ResolveError:` des en-têtes, garder le message réel (`error: line L:C: …`), corriger les wording divergents.
- (c) CHANGELOG → **fixer le récent** (label `E_CLI_001`, count incohérent v0.21) ; **laisser l'historique pur** (`binary` dans l'entrée v0.4.0 — registre daté).

**Note exécution.** Les 7 PRs proposées sont consolidées en **3 PRs thématiques** (tout doc-only, faible risque) pour réduire les cycles de merge : **PR-A** façade+specs (README/CLAUDE/LANGUAGE_SPEC/ARCHITECTURE), **PR-B** targets+manuel (COMPILATION_TARGETS/04/01/02/03/05/06/migration), **PR-C** jamais-auditées+changelog (POSITIONING/COMPARISON/examples-README/CHANGELOG).
