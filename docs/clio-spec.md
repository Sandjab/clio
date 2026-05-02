# CLIO — Compiled Language for Intent Orchestration

**C**ompiled **L**anguage for **I**ntent **O**rchestration

## L'intuition en une phrase

Tout programme hybride LLM/code se réduit à un **graphe de steps typés**, où chaque step déclare *ce qu'il veut* (intent) et *ce qu'il garantit* (contrat), et où un compilateur décide *qui l'exécute* (code, LLM, ou les deux).

---

## Référence syntaxique

Le jeu d'instructions initial. Extensible par la suite.

### Déclarations

| Mot-clé                | Rôle                                           | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `STEP`                 | Déclare une unité atomique de travail           | `STEP extraire_anomalies`                            |
| `FLOW`                 | Déclare un graphe composé de steps              | `FLOW analyser_données`                              |
| `CONTRACT`             | Déclare un schéma de sortie typé                | `CONTRACT anomalie`                                  |
| `RESOURCES`            | Déclare les contraintes d'exécution du flow     | `RESOURCES budget: 30€/mois`                         |

### Champs d'un STEP

| Mot-clé                | Rôle                                            | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `TAKES`                | Entrées typées                                  | `TAKES: données: CSV`                                |
| `GIVES`                | Sorties typées                                  | `GIVES: anomalies: List<{...}>`                      |
| `MODE`                 | Mode d'exécution : `exact`, `judgment`, `auto`  | `MODE: judgment`                                     |
| `VALIDATE`             | Assertion sur la sortie (postcondition)         | `VALIDATE: ligne >= 1`                               |
| `ON_FAIL`              | Stratégie d'échec                               | `ON_FAIL: retry(3) then fallback(règles_stats)`      |
| `LANG`                 | Langage d'implémentation (steps `exact` only)   | `LANG: rust` (défaut : `auto`)                       |
| `CACHE`                | Cache de réponse (steps `judgment` only)         | `CACHE: on \| off \| ttl(24h)` (défaut : `off`)     |

### Champs d'un CONTRACT

| Mot-clé                | Rôle                                            | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `SHAPE`                | Schéma de la donnée (JSON Schema / type)        | `SHAPE: {ligne: int, raison: str}`                   |
| `ASSERT`               | Invariant à vérifier                            | `ASSERT: ligne >= 1 AND ligne <= len(données)`       |
| `CONFIDENCE`           | Seuil de confiance minimal                      | `CONFIDENCE: >= 0.85`                                |

### Contrôle de flux

| Mot-clé                | Rôle                                            | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `->`                   | Chaînage séquentiel                             | `charger_csv -> extraire_anomalies`                  |
| `FOR EACH`             | Boucle sur une collection                       | `FOR EACH anomalie: enrichir(anomalie)`              |
| `WHILE`                | Boucle conditionnelle (compile en agent)        | `WHILE !résolu AND tentatives < 5:`                  |
| `IF`                   | Branchement conditionnel                        | `IF extraire_anomalies.FAILS:`                       |
| `ELSE`                 | Branche alternative                             | `ELSE: signaler_absence()`                           |
| `MATCH`                | Aiguillage multi-branches (switch/case)         | `MATCH sévérité:`                                    |
| `CASE`                 | Branche d'un MATCH                              | `CASE "high": escalader()`                           |
| `DEFAULT`              | Branche par défaut d'un MATCH                   | `DEFAULT: journaliser()`                             |

### Opérateurs d'échec et reprise

| Mot-clé                | Rôle                                            | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `ON_FAIL`              | Stratégie en cas d'échec                        | `ON_FAIL: retry(3)`                                  |
| `retry(n)`             | Relance N fois                                  | `retry(3)`                                           |
| `fallback(step)`       | Bascule vers un step de secours                 | `fallback(règles_statistiques)`                      |
| `escalate`             | Monte au modèle LLM supérieur                   | `ON_FAIL: escalate then retry(2)`                    |
| `abort`                | Arrêt du flow avec erreur                       | `ON_FAIL: abort("données invalides")`                |

### Champs RESOURCES

| Mot-clé                | Rôle                                            | Exemple                                              |
|------------------------|-------------------------------------------------|------------------------------------------------------|
| `budget`               | Enveloppe de coût                               | `budget: 30€/mois`                                   |
| `prefer`               | Priorité d'optimisation                         | `prefer: cost \| latency \| quality`                 |
| `models`               | Pool de modèles disponibles                     | `models: [haiku, sonnet, opus]`                      |
| `strategy`             | Politique de routage                            | `strategy: escalate`                                 |
| `target`               | Cible de compilation                            | `target: python`                                     |
| `lang`                 | Langage par défaut pour les steps `exact`        | `lang: python` (défaut : `auto`)                     |

---

## Les 3 primitives

### 1. STEP — l'unité atomique de travail

Un step ne dit pas *comment* faire. Il dit *quoi* et *avec quelles garanties*.

```
STEP extraire_anomalies
  TAKES:     données: CSV
  GIVES:     anomalies: List<{ligne: int, raison: str, sévérité: enum(low|mid|high)}>
  MODE:      judgment          # ← le signal clé
  CACHE:     ttl(24h)          # ← même input = même output pendant 24h
  VALIDATE:  chaque anomalie cite un numéro de ligne existant dans les données
  ON_FAIL:   retry(3) then fallback(règles_statistiques)
```

Le champ **MODE** est le cœur du système. Trois valeurs :
- `exact` → le compilateur sait que c'est du code pur (tri, filtre, calcul, API call)
- `judgment` → nécessite un LLM (interprétation, rédaction, classification ambiguë)
- `auto` → le compilateur décide (il essaie le code d'abord, LLM en fallback)

Le champ **CACHE** (optionnel, uniquement pour les steps `judgment`) contrôle la reproductibilité :
- `on` → cache permanent. Le hash de l'input (prompt + schéma + modèle + paramètres) sert de clé. Même input = même output garanti, sans appel API.
- `ttl(durée)` → cache avec expiration. Après la durée, l'entrée est invalidée.
- `off` → chaque run appelle le LLM. C'est le défaut.

Le cache rend aussi les tests déterministes : on run une fois, on cache, et les tests rejouent sans toucher l'API.

Le champ **LANG** (optionnel, uniquement pour les steps `exact`) force le langage d'implémentation. Si omis ou `auto`, le compilateur choisit selon la taille des données, les dépendances et la cible :

```
STEP analyser_logs_massifs
  TAKES:     fichier: Log(10GB)
  GIVES:     anomalies: List<{ligne: int, pattern: str}>
  MODE:      exact
  LANG:      rust          # ← trop gros pour Python
  VALIDATE:  len(anomalies) > 0
```

### 2. CONTRACT — la membrane entre les mondes

Le contrat est ce qui rend le stochastique composable avec le déterministe.

```
CONTRACT anomalie
  SHAPE:      {ligne: int, raison: str(max=200), sévérité: enum}
  ASSERT:     ligne >= 1 AND ligne <= len(données)
  CONFIDENCE: >= 0.85        # seuil en dessous duquel on retry ou escalade
```

**Pourquoi ça marche** : un LLM qui produit du JSON validé par un schéma strict devient *fonctionnellement déterministe* du point de vue du step suivant. Le contrat est le pont.

Références scientifiques :
- Design by Contract (Meyer, 1986) — préconditions/postconditions
- Gradual Typing (Siek & Taha, 2006) — spectre entre non-typé et pleinement typé
- Structured Outputs (OpenAI, 2024 / Anthropic tool_use) — JSON garanti par grammaire

### 3. FLOW — la composition

```
FLOW analyser_données
  charger_csv                            # exact
    -> extraire_anomalies                # judgment
    -> FOR EACH anomalie:
         enrichir_via_api(anomalie)       # exact
    -> MATCH anomalie.sévérité:
         CASE "high":  escalader(anomalie)       # judgment
         CASE "mid":   planifier_revue(anomalie) # exact
         DEFAULT:      journaliser(anomalie)     # exact
    -> rédiger_rapport(anomalies)        # judgment
    -> valider_schéma(rapport)           # exact — garde-fou final

  IF extraire_anomalies.FAILS:
    -> règles_statistiques               # exact — fallback déterministe
    -> signaler_dégradation              # exact — log
```

C'est du **dataflow déclaratif** — ni Python, ni prompt, ni pseudo-code.

---

## Ce que fait le "compilateur"

Le compilateur traduit ce flow en artéfacts exécutables :

| Step                 | Mode        | Compilé en                                  |
|----------------------|-------------|----------------------------------------------|
| charger_csv          | exact       | `pandas.read_csv()` ou script bash           |
| extraire_anomalies   | judgment    | Prompt + schéma JSON + validation            |
| enrichir_via_api     | exact       | `requests.post()` avec retry                 |
| rédiger_rapport      | judgment    | Prompt avec contexte injecté                 |
| valider_schéma       | exact       | `jsonschema.validate()` ou Pydantic          |

Le compilateur fait aussi :
- **Gestion du contexte** : il sait combien de tokens chaque step LLM consomme, et découpe/résume automatiquement si le contexte déborde
- **Mémoire** : les contrats remplis deviennent le "state" persistant du flow — pas besoin de tout garder en contexte
- **Optimisation** : deux steps `judgment` consécutifs sans step `exact` entre eux → un seul appel LLM batché

---

## Le compilateur comme couche d'exécution intelligente

Le principe fondamental : **tout ce qui suit est une décision du compilateur, pas du langage**. L'utilisateur qui écrit un flow ne pense jamais aux modèles, aux tokens, aux agents. Exactement comme en C on n'alloue pas de registres à la main.

### Agents et sub-agents

Un agent n'est pas une primitive du langage — c'est un **pattern de compilation**. Un FLOW avec une boucle de raisonnement compile en agent. Un sous-FLOW appelé depuis un step compile en sub-agent avec son propre contexte.

```
FLOW investiguer_bug
  reproduire(description)                    # judgment
  -> WHILE !résolu AND tentatives < 5:
       hypothèse(état_courant)               # judgment
       -> tester_hypothèse(hypothèse)        # exact
       -> évaluer_résultat(test)             # judgment
  -> rédiger_fix(solution)                   # judgment
```

Le compilateur voit la boucle, et décide :
- Chaque itération = un appel LLM séparé (contexte propre) ou continuation du même contexte ?
- Le state entre itérations = quoi ? → les contrats remplis, rien de plus
- Quand spawner un sub-agent ? → quand un step interne est lui-même un FLOW avec ses propres itérations

L'utilisateur n'écrit jamais "lance un agent". Il écrit une boucle avec des steps. Le compilateur fait le reste.

### Routage multi-LLM

Le step dit `judgment`, pas "utilise Opus". Le compilateur route selon les contrats :

| Signal                                         | Modèle choisi          |
|------------------------------------------------|------------------------|
| Step simple + contrat strict + output court     | Petit et rapide (Haiku)|
| Raisonnement multi-étapes + contrat complexe    | Moyen (Sonnet)         |
| Créativité, jugement nuancé, output long        | Lourd (Opus)           |
| Step `exact`                                    | Pas de LLM du tout     |

**Stratégie d'escalade** : le compilateur essaie d'abord le modèle le plus léger. Si le contrat n'est pas rempli après N retries → escalade au modèle supérieur. Descente automatique de coût, montée automatique de qualité.

Ça se déclare au niveau du flow, pas du step :

```
RESOURCES
  budget:     30€/mois
  prefer:     cost | latency | quality       # priorité
  models:     [haiku, sonnet, opus]          # pool disponible
  strategy:   escalate                       # essaie le petit d'abord
  target:     python                         # cible de compilation
  lang:       python                         # défaut pour steps exact
```

### Cibles de compilation

Le compilateur distingue deux choses : la **cible** (quel type de projet émettre) et le **langage** (en quoi écrire les steps `exact`). La cible détermine la structure du projet émis. Le langage détermine ce qu'il y a dedans.

| Cible              | Ce que ça produit                                        |
|--------------------|----------------------------------------------------------|
| `claude-cli`       | Dossier Claude Code : CLAUDE.md, hooks, `claude -p`     |
| `python`           | Package Python autonome (Pydantic + API Anthropic)       |
| `rust`             | Binaire Rust + appels API pour les steps `judgment`      |
| `go`               | Binaire Go + appels API                                  |
| `node`             | Projet Node.js/TypeScript                                |
| `docker`           | Dockerfile multi-stage (mix de langages, un step = un stage) |
| `hybrid`           | Claude CLI pour orchestration + binaire pour steps lourds |

Le `LANG` par step peut différer de la cible globale. Un `target: python` avec un step `LANG: rust` émettrait un module Rust appelé via PyO3 ou subprocess. Un `target: docker` compile chaque step dans son langage natif, un stage par step.

### Optimisation tokens et cache

Le compilateur connaît le coût de chaque step et optimise :

**Batching** — deux steps `judgment` consécutifs sans step `exact` entre eux → un seul appel LLM. Économie : 1 aller-retour réseau + tokens de système prompt.

**Élagage de contexte** — entre deux steps, le compilateur ne passe au suivant que les contrats remplis, pas tout l'historique. Le "state" est minimal par construction.

**Prompt caching** — le compilateur ordonne le contexte pour maximiser le préfixe stable : instructions système > définitions de contrats > state accumulé > input variable du step. Le préfixe commun = cache hit.

**Résumé adaptatif** — quand le state accumulé dépasse un seuil (configurable), le compilateur insère un step de résumé automatique. C'est un step `judgment` implicite avec un contrat strict : "produis un résumé de N tokens max qui préserve toutes les clés du state".

**Budget prédictif** — avant d'exécuter un flow, le compilateur estime le coût total (tokens × prix par modèle). Si le budget est dépassé, il propose des alternatives : modèles moins chers, découpage en sous-flows, ou dégradation gracieuse (certains steps `judgment` passent en `exact` avec des heuristiques).

---

## Pourquoi ça résout les 3 trous du LLM

| Problème               | Solution dans ce modèle                                        |
|------------------------|----------------------------------------------------------------|
| Pas de mémoire         | Le state = les contrats remplis, stockés hors contexte         |
| Contexte fini          | Élagage, résumé adaptatif, budget prédictif par step           |
| Non-déterminisme       | Le contrat + validation = déterminisme fonctionnel             |
| Coût imprévisible      | Budget déclaré + escalade automatique + estimation pré-run     |
| Choix du modèle        | Routage par contrat, pas par step — l'utilisateur n'y touche pas |
| Orchestration d'agents | Patterns compilés depuis des boucles déclaratives, pas câblés à la main |
| Reproductibilité       | CACHE par hash d'input + CONTRACT structurel = runs rejouables          |

---

## Reproductibilité

La compilation est toujours reproductible : même `.clio` → même projet émis.

L'exécution se décompose en trois niveaux :

**Steps `exact`** → reproductibles à 100%. C'est du code, même input = même output.

**Steps `judgment` avec `CACHE: on`** → reproductibles à 100% après le premier run. Le hash de l'input (prompt + schéma + modèle + paramètres) sert de clé. Même input = réponse cachée servie, sans appel API.

**Steps `judgment` avec `CACHE: off`** → non reproductibles au niveau textuel. Deux runs produisent des mots différents. Mais le CONTRACT garantit une **reproductibilité structurelle** : la forme, les invariants et le seuil de confiance sont identiques d'un run à l'autre. C'est l'équivalent du property-based testing : on ne teste pas la valeur exacte, on teste les propriétés.

Un flow est **fonctionnellement reproductible** si les steps en aval ne dépendent que de la structure (le contrat), pas du texte exact. Et c'est exactement ce que le design force : les steps communiquent via des contrats typés, pas du texte brut.

L'implémentation du cache par cible :

| Cible            | Mécanisme                                                  |
|------------------|-------------------------------------------------------------|
| `claude-cli`     | Dossier `.cache/`, hash vérifié dans `run.sh` avant appel  |
| `python`         | Décorateur `@cached_judgment` autour de l'appel API         |
| `docker`         | Volume monté pour persister le cache entre runs             |

---

## Ça existe déjà (en morceaux)

| Projet          | Ce qu'il fait bien              | Ce qui manque vs CLIO                  |
|-----------------|----------------------------------|----------------------------------------|
| DSPy (Stanford) | Signatures typées (Pydantic), compilation de prompts, optimisation auto | Pas de distinction `exact`/`judgment` — tout est LLM. Pas de langage déclaratif séparé de Python |
| LangGraph       | Graphe d'exécution, state Pydantic/TypedDict, durabilité | State centralisé partagé, pas de contrats par step. Couplé à l'écosystème LangChain |
| SGLang (Stanford/Berkeley) | Frontend haut niveau (`gen`, `select`, `fork`), optimisation KV cache (RadixAttention) | Tout est LLM — pas de steps déterministes. Pas de compilation vers des cibles multiples |
| LMQL (ETH Zürich) | Langage propre avec contraintes déclaratives SQL-like, multi-backend | Focalisé sur des interactions LLM unitaires, pas de flows multi-steps avec contrats typés |
| Guidance (Microsoft) | Contraintes au niveau tokenizer (grammaires, CFG) | Un seul appel LLM, pas de composition de flows ni d'orchestration |
| Outlines (dottxt) | Contraintes par automates finis, plus rapide que le non-contraint | Idem — un seul appel, pas d'orchestration multi-steps |
| Instructor      | Validation Pydantic + retry sur appels API | Pas de flow, pas de routage multi-modèle, pas de steps déterministes |
| Prefect/Dagster | Orchestration dataflow, DAG, retry, observabilité | Pas de steps LLM natifs, pas de contrats sur les sorties LLM |

**Aucun ne propose les 3 ensemble** : langage déclaratif lisible + contrats typés + compilation hybride.

Guidance, Outlines et Instructor ne sont pas concurrents — ce sont des **briques d'implémentation possibles pour CONTRACT**. Ils résolvent le problème d'un seul appel LLM contraint. Notre projet résout le problème de *n* appels orchestrés.

### Stratégie de validation des contrats

Le proto n'utilise aucune de ces libs. La validation est faite à la main — c'est trivial pour les premières cibles :

| Cible            | Mécanisme de validation                                      | Dépendance             |
|------------------|---------------------------------------------------------------|------------------------|
| `claude-cli`     | `.schema.json` + `python -m jsonschema` dans un hook post    | jsonschema (stdlib-like)|
| `python`         | Modèle Pydantic + `response_model` natif Anthropic           | pydantic               |
| `docker`         | Identique, encapsulé dans le container                       | pydantic               |
| `local` (futur)  | **Outlines ou Guidance** pour contraindre au niveau tokenizer | outlines / guidance    |

L'architecture garde un point d'injection dans l'emitter (`ContractValidator`) pour brancher ces libs plus tard comme backend alternatif. Pas une dépendance jour 1 — une porte ouverte.

---

## La vraie question ouverte

Le langage ci-dessus est encore trop "dev". L'ambition serait qu'un non-développeur puisse écrire :

```
Prends le fichier clients.csv.
Trouve les comptes à risque de churn — fais-toi confiance, mais chaque flag doit citer une colonne.
Pour chaque compte flaggé, vérifie le dernier ticket Zendesk.
Écris un mail personnalisé de rétention.
Format de sortie : un JSON avec {client, risque, mail}.
```

…et que ça compile en exactement le même graphe que la version formelle.

C'est le spectre **graduel** : du naturel pur au formel pur, avec le compilateur qui infère le maximum de structure depuis le minimum de formalisme.

---

## Anatomie d'une cible : l'exemple `claude-cli`

`claude-cli` est la cible de prototypage la plus rapide parce que le CLI a déjà toutes les briques. Mais c'est **une cible parmi d'autres**, pas l'assembleur universel. Chaque cible a sa propre structure d'émission.

### Mapping `target: claude-cli`

| Primitive du langage     | Artefact émis                                              |
|--------------------------|-------------------------------------------------------------|
| STEP `exact`             | Script bash ou Python dans `steps/`                         |
| STEP `judgment`          | Prompt template + schéma JSON dans `steps/`                 |
| CONTRACT                 | JSON Schema + script de validation dans un hook `post`      |
| FLOW                     | Orchestrateur `run.sh` avec `claude -p` et pipes            |
| WHILE (→ agent)          | `claude -p` en boucle avec state sérialisé entre itérations |
| Sub-FLOW (→ sub-agent)   | Appel `claude -p` imbriqué avec son propre CLAUDE.md        |
| RESOURCES                | Section du CLAUDE.md + flags CLI (`--model`, etc.)          |
| ON_FAIL / fallback       | Branche `||` en bash ou hook `on_error`                     |
| FOR EACH                 | Boucle bash `for` + `claude -p` ou `xargs`                  |
| MATCH / CASE             | `case ... esac` en bash                                     |

Dossier émis :

```
mon-flow/
  CLAUDE.md                # config compilée
  .claude/hooks.json       # contrats en hooks pre/post
  steps/
    01_charger.sh          # exact
    02_extraire.prompt     # judgment (template)
    02_extraire.schema.json
    03_enrichir.sh         # exact (appel API)
    04_rediger.prompt      # judgment
    04_rediger.schema.json
  run.sh                   # orchestrateur principal
```

### Mapping `target: python`

```
mon-flow/
  pyproject.toml
  mon_flow/
    __init__.py
    contracts.py           # classes Pydantic
    steps/
      charger.py           # exact — pandas
      extraire.py          # judgment — appel API + validation Pydantic
      enrichir.py          # exact — requests
      rediger.py           # judgment — appel API
    flow.py                # orchestrateur Python (asyncio)
    cli.py                 # point d'entrée
```

### Mapping `target: docker`

```
mon-flow/
  Dockerfile              # multi-stage
  docker-compose.yml
  rust_steps/             # steps exact en Rust
    analyser_logs/
      Cargo.toml
      src/main.rs
  python_steps/           # steps exact en Python
    enrichir.py
  prompts/                # steps judgment
    extraire.prompt
    extraire.schema.json
  orchestrator.py         # glue Python
```

`bash run.sh`, `python -m mon_flow`, ou `docker compose up` — même FLOW source, exécution adaptée.

---

## Prochaines étapes possibles

1. **Prototype : target `claude-cli`** — C'est la cible la plus rapide à implémenter car le runtime existe déjà. Le premier proto est un **compilateur qui prend un FLOW et émet un dossier Claude Code prêt à tourner**. Pas de nouveau framework — juste une couche d'émission au-dessus de ce qui existe.
2. **Nommer la chose** — un bon nom change tout
3. **Formaliser les contrats** — s'appuyer sur Pydantic + JSON Schema comme socle
4. **Deuxième cible : `python`** — package Python autonome, preuve que le langage est indépendant du runtime
5. **Tester sur un cas réel** — un skill Claude actuel réécrit dans ce langage, compilé vers les deux cibles, exécuté tel quel
6. **Publier** — ça vaut un papier ou au minimum un bon post technique
