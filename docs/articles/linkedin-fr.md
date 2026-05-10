# Et si un workflow LLM redevenait un fichier source ?

Aujourd'hui, un système qui mêle code et LLM ressemble presque toujours à la même chose : des prompts dans des chaînes Python, des appels API dispersés, des scripts de glue, un orchestrateur quelque part, et personne ne sait vraiment où vit la « logique métier ». Ça marche. Ça ne se relit pas en pull request.

Chaque outil grand public résout une part du problème. DSPy optimise les prompts. LangGraph orchestre des agents. Outlines contraint les sorties. Prefect gère les flux. Aucun n'unifie code déterministe et raisonnement LLM dans **une seule abstraction lisible**.

J'ai passé quelques mois sur une autre piste : et si on écrivait le pipeline dans un fichier source, et qu'un compilateur en émettait le projet exécutable ?

## L'idée

CLIO — *Compiled Language for Intent Orchestration* — est un compilateur. Tu écris un fichier `.clio`, il émet un projet : un package Python autonome, un projet Claude Code, un serveur MCP. Trois primitives :

- **STEP** — une unité de travail. Elle déclare ses entrées, ses sorties, et un `MODE` : `exact` (code déterministe), `judgment` (appel LLM), `auto` (à venir, le compilateur tranche).
- **CONTRACT** — une garantie typée sur la forme des données. Le compilateur l'utilise à la fois pour générer le JSON Schema injecté dans le prompt **et** pour valider la sortie LLM à la volée.
- **FLOW** — la composition : dépendances, `FOR EACH PARALLEL`, `IF/ELSE`, `MATCH/CASE`, `WHILE`, et stratégies d'échec (`retry`, `fallback`, `escalate`).

Petit échantillon :

```
STEP detect_churn
  TAKES:    customers: List<{name: str, revenue: float}>
  GIVES:    risks:     List<customer_risk>
  MODE:     judgment
  CACHE:    ttl(24h)
  ON_FAIL:  retry(3) then escalate then fallback(detect_churn_naive) then abort("churn detection exhausted")
```

Six lignes. Pas de wiring à écrire à la main : le compilateur génère les trois retries sur Haiku, l'escalade vers Sonnet, le fallback vers la version déterministe, le cache 24 h, le contrôle de schéma sur la sortie. Le code émis n'a aucune dépendance à CLIO.

## Ce que ça apporte de vraiment différent

Trois choses, sans les survendre.

**1. Compilateur, pas runtime.** LangGraph, n8n, BAML — ce sont des runtimes. Tu installes leur moteur, ton workflow vit dedans, le jour où tu décommissionnes, tu réécris. CLIO compile vers du Python idiomatique (ou du bash, ou du Rust à terme) **sans dépendance à CLIO à l'exécution**. Le jour où j'arrête de maintenir le projet, le code émis tourne toujours. Ce n'est pas un détail : c'est la différence entre adopter un outil et adopter un fournisseur.

**2. Le code stochastique est nommé comme tel.** Aucun framework généraliste ne distingue un node qui appelle un LLM d'un node qui transforme une chaîne. Conséquence : il ne peut ni router les modèles par étape, ni mettre en cache différemment selon le mode, ni faire d'analyse de coût statique. CLIO fait ces choses parce que `MODE: judgment` est dans la grammaire — l'information existe au compile-time.

**3. Un `.clio` se relit en pull request.** Une étape ajoutée entre deux autres, un contrat modifié, un fallback retiré : le diff est lisible en trente secondes. Un `git diff` sur du code LangGraph ou un export JSON d'un workflow n8n, non.

## Le manque que ça comble

Pas de **format texte, typé, multi-cible** où le compilateur sait ce qui est stochastique. BAML s'en approche (DSL typé pour fonctions LLM) mais reste un runtime, et un appel LLM = une fonction — la composition repart dans le langage hôte. LMQL est plus riche sur le contrôle de décodage mais tourne dans son propre interpréteur.

CLIO occupe l'angle « le pipeline lui-même est dans le langage, et tu choisis la cible de déploiement ».

## Ce que ça ne fait pas (et que je préfère dire avant qu'on me le reproche)

- **Pas de librairie d'intégrations.** LangChain a des centaines de tools, n8n a quatre cents nodes, CLIO a quatre primitives EXACT : `impl.rest`, `impl.shell`, `impl.mcp_tool`, `impl.sql`. `impl.rest` n'est pas un placeholder — query templatées, en-têtes avec auth résolue depuis `os.environ`, cinq formes de body (JSON, raw, fichier inline, form-urlencoded, multipart), `retry` configurable avec backoff exponentiel et respect de `Retry-After`. **`impl.mcp_tool` (v0.10) est la concrétisation du pari : MCP comme standard d'interop — un STEP appelle un tool sur un serveur MCP déclaré dans `RESOURCES.mcp_servers`, trois transports (stdio, SSE, HTTP), client long-vivant partagé entre les steps. `impl.sql` (v0.11) ajoute trois drivers (sqlite, postgres, mysql) avec bindings `:name` traduits automatiquement et auto-mapping des colonnes vers le shape `GIVES`.** Ça ne remplace toujours pas une marketplace de quatre cents nodes ; ça remplace les wrappers maison « fetch + json.loads » et « cursor.execute + zip(cols, row) » qu'on récrit à chaque projet.
- **Pas d'observabilité native riche.** Des événements JSON-line structurés, mappables OpenTelemetry. Pas de Langfuse embarqué, pas de dashboard maison. C'est un choix — open standards plutôt que vendor lock-in — mais c'est pauvre comparé à LangSmith.
- **Pas d'éditeur visuel, et il n'y en aura jamais.** Visualisation oui (`clio graph --format html` produit un HTML autonome cliquable), édition non. La source de vérité reste le fichier `.clio`.
- **Pas de time-travel debugging.** Reprise à partir d'une étape oui (`--from-step N` via `state.json`), exploration d'historique non.
- **Maturité d'écosystème : des mois, pas des années.** v0.11 publiée : quatre cibles (`claude-cli`, `python`, `mcp-server`, `langgraph`), douze exemples polis, **651 tests unitaires plus 13 e2e gated**. Quatre revues de code Gemini consécutives sur les PR — chacune a remonté un vrai bug avant merge (SSRF par bypass `startswith`, `asyncio.Lock` au module level, regex SQL qui se faisait avoir par les literals `'time:00'`). Ça tourne, ce n'est pas Terraform.

## Pour qui c'est, pour qui ce ne l'est pas

**Pour** : un dev qui veut traiter un workflow LLM comme du code — facile à relire en PR, typé au compile-time, sans lock-in runtime, déployable sur plusieurs cibles depuis une seule source.

**Pas pour** : un PM qui veut câbler Slack → Postgres → OpenAI dans un canvas (n8n est meilleur). Une équipe déjà investie dans LangGraph + LangSmith qui cherche un orchestrateur Python embarqué (LangGraph fait le job). Un cas d'usage à fonction LLM unique avec composition côté application (BAML est plus mûr sur ce slice).

## Le projet

Open source, MIT, Python 3.12+. La spec du langage, l'architecture et le positionnement explicite vis-à-vis de LangGraph, n8n, BAML et LMQL sont dans le repo. Douze exemples qui compilent et tournent aujourd'hui : extraction d'entités, classification de corpus, RAG, routage de tickets avec branches IF/MATCH, fan-out parallèle, pipeline critique avec ON_FAIL × RESCUE, intégration REST avancée (auth, multipart, retries), consommation d'un serveur MCP (`mcp_tool.clio`), et requête SQL paramétrée avec auto-mapping vers le shape `GIVES` (`sql_demo.clio`).

Si l'angle « écris ton workflow LLM comme un fichier source, compile-le vers la cible que tu veux, possède le code émis » résonne, j'aimerais des retours — en particulier sur les cas où la grammaire actuelle est trop pauvre ou, à l'inverse, trop bavarde.

Lien vers le repo en commentaire. Critiques bienvenues.
