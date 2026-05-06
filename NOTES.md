# Notes — reprise

Branche : `claude/review-github-actions-RP9Pz` (déjà poussée)

## Fait
- CI : `anthropic>=0.40` ajouté aux dev extras de `pyproject.toml`. Les 7 tests qui crashaient sur `ModuleNotFoundError: No module named 'anthropic'` passent.
- `PythonEmitter._pyproject()` : `anthropic` dans le projet émis n'est plus inclus systématiquement, seulement si un step `judgment` cible le protocole anthropic (ou omet `invoke`, qui retombe sur anthropic). +2 tests (`test_emit_openai_only_pyproject_omits_anthropic_dep`, `test_emit_pyproject_includes_anthropic_when_judgment_step_present`).
- Tests : 195 passed, 2 skipped.

## À regarder ce soir
- **`pydantic` conditionnel dans le pyproject émis.** Pareil que pour anthropic : aujourd'hui il est toujours déclaré. À retirer quand `graph.contracts` est vide. **Avant de le faire** : auditer si le runtime émis (`clio/runtime/cache.py`, code généré dans `flow.py`, validation SDK) utilise pydantic ailleurs que dans le module `contracts.py`. Si oui → laisser tel quel ou découpler. Cas trivial sans contract : voir `tests/fixtures/mvp_v03_skeleton.clio`.
- Idem pour `anthropic` : dans `_attempt_anthropic_block` (cf. `clio/emitters/_python_helpers.py:268+`), vérifier si on importe `anthropic` *uniquement* dans les steps judgment qui en ont besoin. Si un step OpenAI émis fait quand même `import anthropic` quelque part, le test `test_emit_openai_only_pyproject_omits_anthropic_dep` cache un bug runtime.

## Hors scope mais noté
- `litellm` n'a pas de protocole dédié — passe par `protocol: openai` + `base_url`. OK pour l'instant, mais à reconsidérer si un jour le SDK `litellm` natif est utile.
- Workflow GitHub Actions ne tourne que sur push/PR vers `main`. La branche actuelle ne déclenchera rien tant qu'il n'y a pas de PR.
