# Audit de drift code ↔ docs — 2026-06-02

> **But.** Établir un baseline propre *avant* une refonte conséquente : où la documentation
> de référence et le code divergent, de sorte qu'on puisse distinguer le drift préexistant des
> changements introduits par la refonte.
>
> **Statut.** Rapport-only. **Aucun fichier n'a été modifié** par cet audit (hors ce rapport).
>
> **État de version au moment de l'audit.** Version livrée = `0.23.0` (`pyproject.toml` et
> `clio/__init__.py` concordent, tag `v0.23.0`). `main` porte du travail **non publié** dans
> `CHANGELOG [Unreleased]` : le skill `/skill2clio` (#87) et le fix parser des escapes
> `\"`/`\\` (#88).
>
> **Méthode.** 6 agents read-only, un par domaine documentaire cohérent, chacun croisé
> ligne-à-ligne contre sa surface de code (parser, IR, 6 emitters, `cli.py`, `examples/`).
> Vérifications live via `uv run python -m clio check` / `--help` (sur cette machine,
> `python`/`python3` du PATH sont cassés — Python 3.9 système ; **seul `uv run` fonctionne**).

---

## 1. Tableau de bord

| Doc de référence | Verdict | Critiques | Modérés | Mineurs |
|---|---|:--:|:--:|:--:|
| `docs/LANGUAGE_SPEC.md` | 🔴 Drift significatif | 9 | 5 | 2 |
| `docs/ARCHITECTURE.md` | 🔴 Drift significatif | 4 | 7 | 4 |
| `docs/COMPILATION_TARGETS.md` | 🔴 Drift significatif | 2 | 3 | 2 |
| `docs/manual/04-targets.md` | 🔴 Drift significatif | 1 | 1 | — |
| `docs/manual/02-language-tour.md` | 🔴 Drift significatif | 2 | 0 | 0 |
| `docs/manual/03-cookbook.md` | 🔴 Drift significatif | 4 | 1 | 0 |
| `docs/manual/05-cli-reference.md` | 🔴 Drift significatif | 1 | 3 | 3 |
| `docs/manual/06-troubleshooting.md` | 🔴 Drift significatif | 2 | 0 | 1 |
| `docs/manual/01-getting-started.md` | 🟡 Drift mineur | 0 | 1 | 1 |
| `CLAUDE.md` | 🟡 Drift mineur | 0 | 4 | 2 |
| `README.md` | 🟢 Aligné | 0 | 0 | 0 |
| `CHANGELOG.md` | 🟢 Aligné | 0 | 0 | 0 |
| `docs/manual/06-migration-v018.md` | 🟢 Aligné | 0 | 0 | 0 |
| `docs/manual/README.md` | 🟢 Aligné | 0 | 0 | 1 |

**Lecture.** Le drift n'est pas uniforme. Les docs *opérationnelles* (README, CHANGELOG,
migration, version) sont saines. Le drift se concentre (a) sur les docs qui décrivent la
**vision du langage** (`LANGUAGE_SPEC`, `ARCHITECTURE`) et (b) sur **deux omissions mécaniques**
propagées à de multiples endroits (target `go`, codes d'erreur fantômes).

---

## 2. Causes racines (30+ findings → 6 clusters)

### Cluster 1 — `MODE: auto` : la prémisse affichée qui n'existe pas dans le code
Le parser n'accepte que `{exact, judgment}` (`clio/parser/parser.py:88`,
`_VALID_MODES = {"exact", "judgment"}`, appliqué `parser.py:550-552`). Vérifié en live :
`clio check` → `unknown MODE 'auto', expected one of ['exact', 'judgment']`. Or `auto` est :
- présenté comme **le pitch central** (`CLAUDE.md` : « le compilateur décide… selon `MODE` :
  exact, judgment, auto ») ;
- documenté avec sémantique complète dans `LANGUAGE_SPEC.md` (L116, L119, L130-133) ;
- répété dans `language-tour` (L22) et `cookbook` (L1123) comme « parsé, runtime pas encore
  implémenté » — **faux** : il est *rejeté au parse*, pas parsé.

→ Décision produit, pas un typo. Voir §4.

### Cluster 2 — Le target `go` jamais propagé dans les énumérations « en dur »
Livré en v0.20, absent de ~6 énumérations exhaustives :
- `docs/manual/05-cli-reference.md:14` — `compile --target` ne liste que 5 cibles, alors que
  `cli.py:24` a `choices=[…, "go"]`. **CRITIQUE**.
- `docs/manual/06-troubleshooting.md:7` — message de validation cité `valid targets: …` (5),
  alors que `parser.py:432-433` émet les 6 (avec `go`). Le doc présente ce message comme la
  chaîne *verbatim* émise. **CRITIQUE**.
- `docs/ARCHITECTURE.md` — diagramme pipeline + Layer 3 énumèrent 5 emitters (M6/m4).
- `docs/LANGUAGE_SPEC.md` — table de statut v0.2, colonne `go` ambiguë (m2).
- `docs/manual/README.md:15` — prose d'index omet `go` (M1).

→ Mécanique, orthogonal à la refonte.

### Cluster 3 — `ARCHITECTURE.md` décrit un compilateur en partie fantasmé
Le doc d'archi est le **moins fiable** comme baseline. Il décrit des éléments **jamais
construits** :
- **`ir/optimizer.py`** + étage « Optimizer » (batching, context budgeting, model routing) —
  n'existe pas (`git log --all -- clio/ir/optimizer.py` vide) ; le pipeline va
  `resolve_imports → build_ir → emit` sans optimizer (`cli.py:128`).
- **`ContractValidator`** — 0 occurrence (`grep -rn ContractValidator clio/`).
- **inférence MODE/LANG** + insertion de steps `summarize` implicites — inexistantes (MODE
  parsé verbatim `parser.py:555` → `builder.py:1035` ; LANG verbatim `graph.py:225`).
- **noms de nœuds AST tous faux** : doc dit `StepNode/ContractNode/FlowNode/ForEachNode…`,
  réalité `StepDecl` (`ast_nodes.py:68`), `ContractDecl` (:92), `FlowDecl` (:219),
  `ForEachBlock` (:125), etc. (suffixe `Decl`/`Block`, jamais `Node`).
- l'invariant **« les emitters ne s'importent jamais entre eux »** (`ARCHITECTURE.md:99`,
  repris dans `CLAUDE.md`) est **violé** : `langgraph.py:36` `from clio.emitters.python import
  PythonEmitter` ; `mcp_server.py:20` importe `_python_helpers` ; `python.py:295` importe
  `_claude_cli_helpers` ; `_mcp_helpers.py:1051-1052` importe des deux.

### Cluster 4 — Codes / chaînes d'erreur fabriqués
- **`E_GO_011`** : documenté (`COMPILATION_TARGETS.md:385`, `04-targets.md:193`) comme refus Go
  `--from-step`, mais **jamais levé** (`grep "E_GO_" clio/` → 001-006, 009, 010, 012, 013
  seulement). De plus la feature est *non implémentée*, pas *refusée* : le binaire relance tout
  le flow sans erreur (`COMPILATION_TARGETS.md:422` le dit lui-même). → confirme **issue #83**
  (les deux moitiés).
- **`E_CLI_001`** : `06-troubleshooting.md:761` cite un `ValueError: target=claude-cli does not
  support FROM … IMPORT` **inexistant**. Vrai chemin : `cli.py:122-127` imprime
  `error: target 'claude-cli' does not support cross-file imports (deferred to a future
  release)` (stderr, exit 1), pas un `ValueError`.
- Codes resolver `E_RES_*`/`E_IMP_*`/`E_VIS_*` présents **seulement en commentaires/docstrings**
  de `resolver.py`, jamais dans les messages levés (`CompileError` à chaînes nues).
  *(NEEDS-HUMAN-CHECK : taxonomie spec-level voulue, ou implication qu'ils surfacent ?)*

### Cluster 5 — La spec documente une grammaire jamais implémentée
Documentés dans `LANGUAGE_SPEC.md`, **rejetés par le parser** :
- **`MODE: auto`** (cf. cluster 1) — C1.
- **`VALIDATE:`** sur STEP (L122, et dans le §Example canonique L1403/L1415) → `clio check` :
  `unexpected IDENT 'VALIDATE'`. **Le §Example de la spec ne parse pas.** — C2.
- **`CONFIDENCE:`** sur CONTRACT (L150, L160) → `unsupported contract field 'CONFIDENCE'`. — C3.
- **`Set<T>`** (L1318) → pas de `SetType`, parse error `LANGLE`. — C4.
- **Domain types** : seul `CSV` existe (`parser.py:2057-2059`) ; `JSON/Log/Email/URL/Markdown`
  (L1377-1384) tombent en `ContractRef` et échouent. — C5.
- **`invoke.mode: embedded`** (L477-498) et **`mcp_sampling`** (L500-519) → `_VALID_INVOKE_MODES
  = {"cli", "api"}` (`parser.py:94`). Sections dédiées sans bandeau « non implémenté ». — C6.
- **`impl.mode: binary`** (L409-424) → `_VALID_IMPL_MODES = {code, rest, shell, mcp_tool, sql}`
  (`parser.py:90`). — C7.
- **invoke CLI** : `allowed_tools`, `permission_mode`, `append_system_prompt`, `session`
  (L440-449) rejetés ; seuls `cli/model/output_format/max_turns` acceptés (`parser.py:1793`). — C8.
- **invoke API** : `response_format`, `extra_headers`, `extra_body` (L460-473) rejetés
  (`parser.py:1839-1842`). — C9.

### Cluster 6 — Snippets du manuel qui ne parsent pas + micro-drifts de comportement
La grammaire est strictement orientée-ligne. Échouent `clio check` :
- **`SHAPE: { … }` multilignes** (`02-language-tour.md:109-118`, `03-cookbook.md:1085-1093`) :
  `parse_record_type` ne saute pas NEWLINE/INDENT/DEDENT (`parser.py:2187-2201`) → doit tenir sur
  une ligne logique. La forme single-line parse `ok`. — C1(manual).
- **`invoke: { … }` inline ×4** (`cookbook` #18/#25) : `parse_invoke_block` exige
  `NEWLINE → INDENT` (`parser.py:1729-1734`). — C2(manual).
- **condition `IF` wrappée sur 2 lignes** (`cookbook:192-198`, #8) : la condition doit finir par
  `:` sur la même ligne logique. — C4(manual).
  *(Le feature `and`/`or`/parens lui-même fonctionne ; seul le wrap échoue.)*

Micro-drifts de comportement :
- **`clio check` imprime `ok`** (`cli.py:167`), documenté « silent » (`05-cli-reference.md:35`),
  « No output » (`01-getting-started.md:56`) — incohérent à 2-3 endroits. Un script CI qui
  asserte stdout vide casse.
- **`gen` « up to 3 retries »** (`05-cli-reference.md:94`) alors que `nl_to_clio.py:32`
  `max_retries=1` (1 appel + 1 retry). `06-troubleshooting.md:793` dit correctement « 1 retry ».
- **`doctor` flags non documentés** : `--flow`, `--migrate-v018`, `--write`
  (`cli.py:52-63`) absents de `05-cli-reference.md:145`.
- **bloc « File structure » de `CLAUDE.md`** sous-liste ~10 modules réels (cf. §3, doc CLAUDE.md).

---

## 3. Findings détaillés par document

### 3.1 `docs/LANGUAGE_SPEC.md` — 🔴 9C / 5M / 2m
**Critiques :** C1 `MODE: auto` rejeté · C2 `VALIDATE:` inexistant (le §Example ne parse pas) ·
C3 `CONFIDENCE:` inexistant · C4 `Set<T>` inexistant · C5 5/6 domain types inexistants ·
C6 `invoke.mode: embedded`/`mcp_sampling` non parseables · C7 `impl.mode: binary` non parseable ·
C8 4 champs invoke CLI rejetés · C9 3 champs invoke API rejetés.
**Modérés :** M1 titre « v0.2 » périmé (le doc décrit jusqu'à v0.22) · M2 ligne de table de statut
`FLOW.TAKES/GIVES ❌` contredit le reste de la spec (implémenté `parser.py:2222-2249`) ·
M3 escape `#88` sous-cadré (documenté shell-only, alors que c'est le scanner STRING générique
`lexer.py:77-87` → tous les littéraux) · M4 limitation sidecar `#67` périmée (multi-file livré
v0.22) *(NEEDS-HUMAN-CHECK formulation)* · M5 `output_format` non validé/sans défaut au parse.
**Mineurs :** m1 défaut LANG `auto` stocké `None` · m2 colonne `go` ambiguë dans la table.
**Aligné (vérifié) :** primitives, `Dict<K,V>`, `Optional<T>`, types contraints (str/int/float
min/max/precision), enums/records, IMPORT/FROM/EXPOSE/INTERNAL, ON_FAIL/RESCUE/RESUME,
control flow, TEST, RESOURCES.

### 3.2 `docs/ARCHITECTURE.md` — 🔴 4C / 7M / 4m
**Critiques :** C1 `ir/optimizer.py` + batching/context-budgeting/model-routing inexistants ·
C2 noms de nœuds AST faux · C3 invariant « emitters ne s'importent jamais » violé ·
C4 `ContractValidator` inexistant.
**Modérés :** M1 nombre/structure des passes IR sous-décrit (le doc dit « 4 passes » ; le code a
une passe 0.5 signatures `builder.py:829`, un `_build_flow` par flow, une détection de cycles
d'appels de flow `_detect_flow_call_cycles:224`) · M2 propriété du resolver mal décrite
(`resolve_imports` appelé par `cli.py`, pas par builder ; merge alpha-rename dans
`builder.py:_flatten_to_program`, pas dans resolver) · M3 mapping passes↔codes resolver inexact ·
M4 inférence MODE/LANG + steps implicites fictives · M5 `contracts.py` surdécrit (74 lignes, une
fonction `type_to_json_schema`, pas de Pydantic ni de génération de code) · M6 `go` absent du
diagramme/Layer 3 · M7 signature `BaseEmitter.emit` périmée (manque `source_path`, `sources`).
**Mineurs :** m1 `parser/{tokens,expressions}.py` non documentés · m2 `ir/types.py` non documenté ·
m3 « ~20 keywords » (réel : 98 dans `keywords.py`) · m4 forme de sortie `go` absente du diagramme.
**Aligné :** modèle 3-couches, parser récursif descendant indenté, nœuds frozen/immutables,
`FlowGraph`/`FlowCallIR`, le compilateur n'exécute pas de runtime, sidecar `.clio/`.

### 3.3 `docs/COMPILATION_TARGETS.md` + `docs/manual/04-targets.md` — 🔴
**Critiques :** C1 `E_GO_011` fantôme (les deux docs) · C2 Go `--from-step` étiqueté refus
(`❌ E_GO_011`, cellule de matrice `04-targets.md:193`) alors que c'est non implémenté — issue #83.
**Modérés :** M3 contrat mcp-server `E_MCP_001` incomplet (la garde ne tire que si `target:
mcp-server` est déclaré en source `builder.py:265-269`, contournée via flag `--target`) ·
M4 liste de refus langgraph incomplète (manque `WHILE` `langgraph.py:175`, `impl.mode: sql` :201) ·
M5 `langgraph` marqué « Candidate » dans `COMPILATION_TARGETS.md:13` alors qu'il est livré.
**Mineurs :** m1 stamp « v0.4 » périmé sur le logging claude-cli · m2 incohérence interne du
message `E_GO_003` (code-only, pas de fix doc).
**Aligné (coché cellule par cellule) :** model map (haiku/sonnet/opus → IDs), caps de concurrence
= 10 (Go errgroup, python ThreadPool, mcp Semaphore), Go rest json/raw + refus form/file/multipart
`E_GO_013`, Go shell parse none/json, Go FLOW composition + collecteur parallèle single-GIVES
terminal-only, refus E_GO_001/002/003/005/009/010/012, refus mcp-server, refus langgraph (FOR
EACH/RESCUE/escalate+fallback/non-anthropic/cli), refus claude-cli (PARALLEL/RESCUE/composition/
SQL), claude-skill (LANG python/bash-only, PARALLEL sérialisé, sub-flow linéaire, sidecar `.clio/`).
> ⚠️ Note code (hors scope doc) : `go.py:14` docstring dit « refuse RESUME » — **stale** ;
> le code émet bien RESUME (`_go_flow_renderer.py:330-356`) et les deux docs ont raison.

### 3.4 `docs/manual/01·02·03` (usage) — 🟡 01 / 🔴 02 / 🔴 03
**01-getting-started — mineur :** M1 `check` imprime `ok` (≠ « No output » L56) · m1 « tu auras
un `ParseError` » alors que le CLI imprime `error: line L:C: …` (la classe n'est pas surfacée).
**02-language-tour — 2 critiques :** C1 `SHAPE:{` multilignes (L109-118) · C3 `auto` MODE « parsé »
(L22) — rejeté au parse.
**03-cookbook — 4 critiques :** C2 `invoke:{…}` inline ×4 (#18/#25 ; #18 annoté « Sketch
(compilable) » alors qu'il ne compile pas) · C4 condition `IF` wrappée (#8) · C1 `SHAPE:{`
multilignes (#27) · C3 claim `auto` ; **modéré** M2 layout claude-skill (#15) omet le sidecar
`.clio/` et sur-affiche `schemas/01_greet.input.json` — non émis car `greet` n'a pas de `TAKES`.
**Aligné (vérifié par `clio check`) :** tous les `examples/*.clio` référencés parsent ; SHAPE
single-line, ASSERT chaîné/`len()`, `str(max=N)`, `List<str(max=200)>`, v0.21 (Dict/Optional/
contraintes/nested), control flow (FOR EACH PARALLEL, IF/ELSE and/or single-line, MATCH, WHILE),
RESCUE/RESUME, multi-file IMPORT/EXPOSE/INTERNAL/façade, FLOW signature + composition (y compris
dans FOR EACH PARALLEL), TEST, impl REST/mcp_tool/sql (dict *values* inline OK), réservé `judgment`
rejeté comme nom de champ.

### 3.5 `docs/manual/05-cli-reference.md` + `06-troubleshooting.md` — 🔴
**05 — critique :** F1 `go` absent de `compile --target` (L14). **Modérés :** F3 flags `doctor`
non documentés · F4 `check` non « silent » (imprime `ok`) · F5 `gen` « up to 3 retries » vs 1.
**Mineurs :** F6 défaut `status --limit` (=10) non indiqué · F8 pas de flag `--version` (l'hypothèse
d'audit était fausse ; aucun doc ne le revendique) · F11 message resume `{path}` vs `state.json`.
**06 — 2 critiques :** F9 message de validation cite 5 targets (vs 6) · F10 `E_CLI_001` =
`ValueError` fabriqué. **Mineur :** F11 (cf. supra).
**Aligné :** 7 sous-commandes (compile/check/graph/gen/doctor/status/import), table de dispatch
`import` + exit codes, `--model` défaut `claude-sonnet-4-6`, échantillon représentatif de messages
parse/emit.
> NEEDS-HUMAN-CHECK : la longue traîne des ~70 chaînes `IRBuildError/ParseError/E_*` de
> `06-troubleshooting.md:25-518` n'a **pas** été diffée exhaustivement (hors scope CLI). À faire si
> une garantie string-level totale est requise avant la refonte.

### 3.6 Méta : `README` · `CLAUDE.md` · `CHANGELOG` · `06-migration` · `manual/README`
**README.md — 🟢 aligné.** Toutes les commandes du Quick start mappent une vraie sous-commande ;
tous les chemins d'exemple existent ; badge version v0.23.0 ; « 1270 unit tests » cohérent.
**CLAUDE.md — 🟡 mineur (4M / 2m).** Le bloc « File structure » sous-liste ~10 modules réels —
drift **uni-directionnel** (aucun chemin fantôme) : C1 `runtime/` manque `substitute.py`,
`validate.py` ; C2 `parser/` manque `expressions.py`, `tokens.py` ; C3 `ir/` manque `types.py` ;
C4 top-level manque `__main__.py`, `_llm_validation.py`, `diagnostics.py`, `graph_render.py`,
`keywords.py` (ce dernier pourtant nommé dans « Conventions »). La table des 6 targets et le
« How to run » sont **exacts**.
**CHANGELOG.md — 🟢 aligné.** #87/#88 correctement sous `[Unreleased]` ; `0.23.0` = dernière
release ; version concorde ; claims 0.23.0 vérifiés contre le code.
**06-migration-v018.md — 🟢 aligné.** `EXPOSE`/`INTERNAL` + `E_MCP_001` + `doctor --migrate-v018
[--write]` (`cli.py:56-63`) ; fixture `tests/fixtures/imports/migration_v017_to_v018/` existe.
**manual/README.md — 🟢 aligné** (m : la prose d'index L15 omet `go`).

---

## 4. Ce qui relève d'une DÉCISION (pas un fix mécanique)

Le drift le plus lourd (`MODE: auto`, l'« optimizer », le model-routing, l'inférence MODE/LANG)
recoupe **exactement la zone « vision »** du projet. Deux questions ouvertes, à trancher avant de
réécrire quoi que ce soit :

1. **`MODE: auto`** — feature planifiée (alors : labelliser partout « planifié / non implémenté »,
   et corriger le pitch de `CLAUDE.md` qui le présente comme existant) **ou** abandonnée (alors :
   le retirer de la prémisse) ?
2. **Étage « Optimizer » + `ContractValidator` + inférence MODE/LANG** d'`ARCHITECTURE.md` —
   roadmap à matérialiser (la doc est alors un état-cible, pas du drift à « corriger ») **ou**
   vision périmée à élaguer ?

> ⚠️ **Si la refonte porte sur ces éléments**, alors une grande partie du « drift » de
> `LANGUAGE_SPEC` et `ARCHITECTURE` **n'est pas à corriger maintenant** — c'est l'état-cible. Le
> corriger pour matcher le code *actuel* serait du travail jeté.

---

## 5. Drift mécanique, orthogonal à la refonte (nettoyable tout de suite)

Indépendant de toute refonte, faible risque, gros gain de propreté du baseline :
- **Propager `go`** dans les 6 énumérations (cluster 2).
- **Retirer les codes/chaînes fantômes** : `E_GO_011` (issue #83), `E_CLI_001`, requalifier Go
  `--from-step` de « refusé » → « non implémenté (re-run complet) ».
- **Reflow des snippets cassés** : `SHAPE:{` single-line (2 spots), `invoke:{…}` → bloc (4 spots),
  condition `IF` #8 dé-wrappée.
- **Micro-drifts** : `check` imprime `ok` (3 spots), `gen` retry 1 (pas 3), flags `doctor`,
  `langgraph` « Candidate » → « livré », bloc « File structure » de `CLAUDE.md`.

---

## Annexe — couverture

6 agents, périmètre « docs de référence vivantes » (plans/specs figés exclus à dessein).
Verdicts : 8 docs en drift significatif, 2 en drift mineur, 4 alignés. Issue #83 confirmée par
deux angles indépendants (cluster 4). Points laissés en **NEEDS-HUMAN-CHECK** : formulation de la
limitation sidecar `#67` (LANGUAGE_SPEC M4) ; intention du layout claude-skill du cookbook (M2) ;
nature des codes `E_RES_*/E_VIS_*` (taxonomie vs message) ; traîne complète des chaînes d'erreur
de `06-troubleshooting.md`.
