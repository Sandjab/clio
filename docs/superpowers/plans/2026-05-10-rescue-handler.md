# RESCUE Handler — Plan d'implémentation (v0.8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** Implémenter le handler `RESCUE step_a:` (top-level dans FLOW) qui s'exécute si `step_a` échoue après épuisement de son `ON_FAIL`, body multi-step terminant par `abort(...)`.

**Architecture :** Nouvelle production de parser top-level dans `parse_flow`, nouveau nœud AST/IR (`RescueBlock` / `RescueBlockIR`), 6 validations IR, lowering en try/except dans les emitters python+mcp, rejet à la compilation pour langgraph et claude-cli, cluster rouge dans le viewer mermaid+html.

**Tech Stack :** Python 3.12+, Pydantic v2, pytest, hand-written recursive descent parser. Aucune nouvelle dépendance.

**Spec :** `docs/superpowers/specs/2026-05-10-rescue-handler-design.md`

---

## File Structure

| Fichier | Rôle | Action |
|---|---|---|
| `clio/keywords.py` | enum closed des keywords du lexer | **Modify** : ajouter `RESCUE = "RESCUE"` |
| `clio/parser/ast_nodes.py` | dataclasses AST | **Modify** : ajouter `RescueBlock`, étendre `FlowDecl` avec `rescues` |
| `clio/parser/parser.py` | parser RD (~1200 lignes) | **Modify** : ajouter `parse_rescue_block`, étendre `parse_flow` |
| `clio/ir/graph.py` | IR dataclasses (~210 lignes) | **Modify** : ajouter `RescueBlockIR`, étendre `FlowIR` avec `rescues` |
| `clio/ir/builder.py` | IR builder + validation (~780 lignes) | **Modify** : ajouter `_build_rescue`, 6 validations, descente `_walk` |
| `clio/emitters/python.py` | python emitter (~700 lignes) | **Modify** : try/except wrapper + helper `_rescue_<name>` |
| `clio/emitters/_python_helpers.py` | python helpers | **Modify** : helpers d'émission rescue body |
| `clio/emitters/_mcp_helpers.py` | mcp-server helpers (~680 lignes) | **Modify** : async try/except + async helper |
| `clio/emitters/langgraph.py` | langgraph emitter | **Modify** : étendre `_reject_unsupported` |
| `clio/emitters/claude_cli.py` | claude-cli emitter | **Modify** : `_reject_rescue` à côté de `_reject_parallel` |
| `clio/graph_render.py` | mermaid+dot+html viewer (~1440 lignes) | **Modify** : `_to_mermaid_rich_labels` + `rescue_meta` + accent rouge |
| `tests/test_rescue_block.py` | nouveaux tests parser/IR | **Create** : ~10 tests |
| `tests/test_emitters/test_python.py` | extend snapshots | **Modify** : 2-3 nouveaux tests |
| `tests/test_emitters/test_mcp_server.py` | extend snapshots | **Modify** : 2-3 nouveaux tests |
| `tests/test_emitters/test_langgraph.py` | rejection test | **Modify** : 1 nouveau test |
| `tests/test_graph_render.py` | viewer test | **Modify** : 1 nouveau test |
| `examples/critical_pipeline.clio` | nouvel exemple | **Create** : ON_FAIL × RESCUE composition |
| `docs/LANGUAGE_SPEC.md` | spec officielle | **Modify** : §RESCUE + exemple narratif |
| `docs/manual/02-language-tour.md` | tutorial | **Modify** : section RESCUE |
| `docs/manual/03-cookbook.md` | recipes | **Modify** : recipe « pipeline LLM critique » |
| `docs/manual/06-troubleshooting.md` | erreurs compile | **Modify** : 2 entrées |
| `CHANGELOG.md` | changelog | **Modify** : ouvrir `## v0.8.0` |

---

## Task 1 : Keyword RESCUE + AST node `RescueBlock`

**Files:**
- Modify: `clio/keywords.py`
- Modify: `clio/parser/ast_nodes.py`
- Test: `tests/test_rescue_block.py` (création)

- [ ] **Step 1.1 : Créer le test failing pour le keyword**

Créer `tests/test_rescue_block.py` :

```python
"""Tests pour la primitive RESCUE (handler top-level attaché à un STEP).
Voir docs/superpowers/specs/2026-05-10-rescue-handler-design.md."""

import pytest

from clio.keywords import Keyword


def test_rescue_keyword_present():
    """RESCUE doit être enregistré comme keyword closed du lexer."""
    assert Keyword.RESCUE.value == "RESCUE"
```

- [ ] **Step 1.2 : Run test, vérifier l'échec**

```bash
pytest tests/test_rescue_block.py::test_rescue_keyword_present -v
```
Expected : FAIL avec `AttributeError: RESCUE` ou `KeyError`.

- [ ] **Step 1.3 : Ajouter `RESCUE` dans `clio/keywords.py`**

À la fin de l'enum (après `MAX = "MAX"`) :

```python
    RESCUE = "RESCUE"
```

- [ ] **Step 1.4 : Vérifier que le test passe**

```bash
pytest tests/test_rescue_block.py::test_rescue_keyword_present -v
```
Expected : PASS.

- [ ] **Step 1.5 : Ajouter le test failing pour `RescueBlock` AST**

Append à `tests/test_rescue_block.py` :

```python
from clio.parser.ast_nodes import RescueBlock, StepCall


def test_rescue_block_ast_shape():
    """RescueBlock doit être un frozen dataclass avec step_name / body / line / col."""
    rb = RescueBlock(
        step_name="detect_churn",
        body=(StepCall(name="abort", kwargs=(("message", "boom"),), line=2, col=2),),
        line=1, col=0,
    )
    assert rb.step_name == "detect_churn"
    assert len(rb.body) == 1
    assert rb.line == 1
```

- [ ] **Step 1.6 : Run test, voir l'échec**

```bash
pytest tests/test_rescue_block.py::test_rescue_block_ast_shape -v
```
Expected : FAIL avec `ImportError: cannot import name 'RescueBlock'`.

- [ ] **Step 1.7 : Ajouter `RescueBlock` dans `clio/parser/ast_nodes.py`**

Insérer après la classe `WhileBlock` (ligne ~158) :

```python
@dataclass(frozen=True)
class RescueBlock:
    """RESCUE step_name:
           <chain ending with abort(message)>

    Handler top-level attaché à un STEP de la chain principale du FLOW.
    Le body s'exécute si step_name lève après épuisement de son ON_FAIL.
    Le dernier item top-level du body doit être un StepCall vers `abort`."""
    step_name: str
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    line: int
    col: int
```

- [ ] **Step 1.8 : Étendre `FlowDecl` pour accepter `rescues`**

Remplacer la définition de `FlowDecl` (ligne ~160) par :

```python
@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    rescues: "tuple[RescueBlock, ...]"
    line: int
    col: int
```

Mettre à jour le seul site qui construit FlowDecl (clio/parser/parser.py ligne 1008) — le faire dans la Task 2. Pour cette task : ajouter un test qui construit FlowDecl avec `rescues=()` pour valider l'extension.

```python
from clio.parser.ast_nodes import FlowDecl


def test_flow_decl_has_rescues_field():
    """FlowDecl doit accepter un tuple `rescues` (vide par défaut autorisé)."""
    fd = FlowDecl(name="f", chain=(), rescues=(), line=1, col=0)
    assert fd.rescues == ()
```

- [ ] **Step 1.9 : Adapter `parse_flow` pour passer `rescues=()` (temporaire)**

Dans `clio/parser/parser.py` ligne 1008, changer :

```python
        return FlowDecl(name=ident.value, chain=tuple(chain), line=kw.line, col=kw.col)
```

en :

```python
        return FlowDecl(name=ident.value, chain=tuple(chain), rescues=(), line=kw.line, col=kw.col)
```

- [ ] **Step 1.10 : Run tests, vérifier qu'ils passent et que rien n'est cassé**

```bash
pytest tests/test_rescue_block.py tests/test_parser.py -v
```
Expected : tous PASS.

- [ ] **Step 1.11 : Run full test suite**

```bash
pytest tests/ -q
```
Expected : 457 + 3 = 460 tests passed.

- [ ] **Step 1.12 : Commit**

```bash
git add clio/keywords.py clio/parser/ast_nodes.py clio/parser/parser.py tests/test_rescue_block.py
git commit -m "$(cat <<'EOF'
feat(lang): RESCUE keyword + RescueBlock AST node

Reserve RESCUE in the closed keyword enum. Add RescueBlock dataclass
mirroring IfBlock/WhileBlock shape; extend FlowDecl with a rescues
tuple (empty by default). Parser threading lands in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 : Parser — `parse_rescue_block` + dispatch top-level + `abort(...)` synthétique

**Files:**
- Modify: `clio/parser/parser.py`
- Test: `tests/test_rescue_block.py`

- [ ] **Step 2.1 : Ajouter le test parser shape (single rescue, simple body)**

Append à `tests/test_rescue_block.py` :

```python
from clio.parser.parser import Parser
from clio.parser.lexer import tokenize


def _parse(src: str):
    return Parser(tokenize(src)).parse()


SINGLE_RESCUE_SRC = """
STEP load
  TAKES: path: str
  GIVES: data: List<int>
  MODE:  exact

STEP detect
  TAKES: data: List<int>
  GIVES: result: int
  MODE:  exact

FLOW pipeline
  load(path="x.csv")
    -> detect(data=load)

  RESCUE detect:
    -> abort("detection failed")
"""


def test_parse_single_rescue_block():
    """RESCUE après la chain principale doit produire un RescueBlock dans flow.rescues."""
    program = _parse(SINGLE_RESCUE_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert len(flow.chain) == 2  # load -> detect
    assert len(flow.rescues) == 1
    rb = flow.rescues[0]
    assert rb.step_name == "detect"
    assert len(rb.body) == 1
    abort_call = rb.body[0]
    assert abort_call.name == "abort"
    assert abort_call.kwargs == (("message", "detection failed"),)
```

- [ ] **Step 2.2 : Run test, vérifier l'échec**

```bash
pytest tests/test_rescue_block.py::test_parse_single_rescue_block -v
```
Expected : FAIL — soit `ParseError` (RESCUE inattendu), soit assertion mismatch (rescues vide).

- [ ] **Step 2.3 : Implémenter `parse_rescue_block`**

Dans `clio/parser/parser.py`, après `parse_while_block` (vers ligne 1180), ajouter :

```python
    def parse_rescue_block(self) -> "RescueBlock":
        """RESCUE <step_name>:
            <flow_item> -> <flow_item> -> ...

        Handler top-level attaché à un STEP de la chain principale du FLOW.
        Le dernier item de la chain top-level du body DOIT être un appel à
        `abort("message")` (validation effectuée à l'étape IR)."""
        kw = self.expect(TokenType.KEYWORD, "RESCUE")
        step_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        body = self._parse_block_chain()
        return RescueBlock(
            step_name=step_tok.value,
            body=body,
            line=kw.line, col=kw.col,
        )
```

Et ajouter `RescueBlock` à l'import en haut du fichier (chercher la ligne `from clio.parser.ast_nodes import (` et y ajouter).

- [ ] **Step 2.4 : Étendre `parse_flow` pour collecter les rescues**

Dans `clio/parser/parser.py`, remplacer le corps de `parse_flow` (lignes 986-1008). Diff conceptuel :

```python
    def parse_flow(self) -> FlowDecl:
        kw = self.expect(TokenType.KEYWORD, "FLOW")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        chain: list[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock] = [self.parse_flow_item()]
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.INDENT):
                self.advance()
            if self.peek().type == TokenType.ARROW:
                self.advance()
                chain.append(self.parse_flow_item())
            else:
                break

        # NEW : collecter les RESCUE blocks après la chain.
        rescues: list[RescueBlock] = []
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
                # DEDENT ramène au niveau de la FLOW : on accepte RESCUE comme
                # peer-indent, on continue à lire les newlines/dedents qui le précèdent.
                self.advance()
                if self.peek().type == TokenType.KEYWORD and self.peek().value == "RESCUE":
                    break
            tok = self.peek()
            if tok.type == TokenType.KEYWORD and tok.value == "RESCUE":
                rescues.append(self.parse_rescue_block())
                continue
            break

        return FlowDecl(
            name=ident.value,
            chain=tuple(chain),
            rescues=tuple(rescues),
            line=kw.line, col=kw.col,
        )
```

- [ ] **Step 2.5 : Reconnaître `abort("...")` comme StepCall synthétique**

`abort` est déjà un keyword (utilisé dans `ON_FAIL: abort("msg")`). Dans `parse_step_call` (le parser de step calls), il faut accepter le keyword `abort` comme un nom de step légitime quand il apparaît dans un contexte chain.

Localiser `parse_step_call` :

```bash
grep -n "def parse_step_call" clio/parser/parser.py
```

Si `parse_step_call` exige `TokenType.IDENT`, ajouter un cas pour `TokenType.KEYWORD` avec value `"abort"`. Construire le StepCall avec `name="abort"`, kwargs `(("message", "<literal>"),)`. Concrètement :

```python
    def parse_step_call(self) -> StepCall:
        # ... code existant ...
        # MODIFIER l'expect initial pour accepter abort keyword :
        tok = self.peek()
        if tok.type == TokenType.KEYWORD and tok.value == "abort":
            name_tok = self.advance()
        else:
            name_tok = self.expect(TokenType.IDENT)
        # ... le reste du parsing kwargs reste identique ...
        return StepCall(name=name_tok.value, kwargs=tuple(kwargs), line=name_tok.line, col=name_tok.col)
```

NOTE : si `parse_step_call` traite le nom différemment, adapter. Cf le code actuel pour vérifier où le `name_tok` est lu.

- [ ] **Step 2.6 : Run test, vérifier qu'il passe**

```bash
pytest tests/test_rescue_block.py::test_parse_single_rescue_block -v
```
Expected : PASS.

- [ ] **Step 2.7 : Ajouter test multi-rescue + test rescue avant RESOURCES**

Append à `tests/test_rescue_block.py` :

```python
TWO_RESCUES_SRC = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

STEP b
  TAKES: y: int
  GIVES: z: int
  MODE:  exact

FLOW pipe
  a(x=1) -> b(y=a)

  RESCUE a:
    -> abort("a failed")

  RESCUE b:
    -> abort("b failed")
"""


def test_parse_multiple_rescues():
    program = _parse(TWO_RESCUES_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    assert len(flow.rescues) == 2
    assert {r.step_name for r in flow.rescues} == {"a", "b"}


RESCUE_BEFORE_RESOURCES_SRC = """
STEP s
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  s(x=1)

  RESCUE s:
    -> abort("boom")

RESOURCES
  target: python
"""


def test_rescue_compatible_with_resources():
    program = _parse(RESCUE_BEFORE_RESOURCES_SRC)
    flow = next(d for d in program.decls if d.__class__.__name__ == "FlowDecl")
    res = next(d for d in program.decls if d.__class__.__name__ == "ResourcesDecl")
    assert len(flow.rescues) == 1
    assert res.target == "python"
```

- [ ] **Step 2.8 : Run tests**

```bash
pytest tests/test_rescue_block.py -v
```
Expected : tous PASS. Si le test « before resources » échoue, c'est que la boucle de collecte du Step 2.4 a consommé un DEDENT qui devait clore le FLOW pour laisser place à RESOURCES. Ajuster la condition de sortie de boucle pour ne pas avancer au-delà du dernier RESCUE.

- [ ] **Step 2.9 : Commit**

```bash
git add clio/parser/parser.py tests/test_rescue_block.py
git commit -m "$(cat <<'EOF'
feat(parser): parse_rescue_block + RESCUE collection in parse_flow

RESCUE blocks are parsed as a sibling tuple of FlowDecl.chain, after
the chain and before the optional RESOURCES section. The body reuses
_parse_block_chain so any flow_item is allowed inside (calls, FOR
EACH, IF, MATCH, WHILE). `abort("msg")` is recognised as a synthetic
StepCall whose only kwarg is `message`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 : IR — `RescueBlockIR` + extension `FlowIR`

**Files:**
- Modify: `clio/ir/graph.py`
- Modify: `clio/ir/builder.py`
- Test: `tests/test_rescue_block.py`

- [ ] **Step 3.1 : Test IR shape**

Append à `tests/test_rescue_block.py` :

```python
from clio.ir.builder import build_ir
from clio.ir.graph import RescueBlockIR


def test_build_ir_single_rescue():
    """build_ir doit produire un RescueBlockIR dans flow.rescues."""
    program = _parse(SINGLE_RESCUE_SRC)
    graph = build_ir(program)
    assert graph.flow is not None
    assert len(graph.flow.rescues) == 1
    rb = graph.flow.rescues[0]
    assert isinstance(rb, RescueBlockIR)
    assert rb.step_name == "detect"
    assert len(rb.body) == 1
    call = rb.body[0]
    assert call.step_name == "abort"
    assert call.kwargs == (("message", "detection failed"),)
```

- [ ] **Step 3.2 : Run, vérifier échec**

```bash
pytest tests/test_rescue_block.py::test_build_ir_single_rescue -v
```
Expected : FAIL — `RescueBlockIR` n'existe pas / `FlowIR` n'a pas `rescues`.

- [ ] **Step 3.3 : Ajouter `RescueBlockIR` dans `clio/ir/graph.py`**

Insérer après `WhileBlockIR` (vers ligne 188) :

```python
@dataclass(frozen=True)
class RescueBlockIR:
    """IR mirror of RescueBlock. Bound to a StepIR by name (no direct
    pointer because StepIR is frozen). The handler runs only if the
    referenced STEP raises after its ON_FAIL chain (if any) exhausts."""
    step_name: str
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int
```

- [ ] **Step 3.4 : Étendre `FlowIR` avec `rescues`**

Remplacer `FlowIR` (vers ligne 191) :

```python
@dataclass(frozen=True)
class FlowIR:
    name: str
    chain: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    rescues: "tuple[RescueBlockIR, ...]"
    line: int
```

- [ ] **Step 3.5 : Ajouter `_build_rescue` dans `clio/ir/builder.py`**

Localiser la fonction qui construit `FlowIR` (chercher `FlowIR(` dans builder.py). Avant le retour, ajouter le build des rescues :

```python
def _build_rescue(
    decl: "RescueBlock",
    step_index: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, ContractIR],
) -> "RescueBlockIR":
    """Construit RescueBlockIR. Validations détaillées en Task 4 ; ici on
    se contente de transformer le body en CallIR/ForEachIR/etc."""
    # Réutiliser le walker de build_chain pour transformer le body.
    body_ir = _build_chain(decl.body, step_index, contracts, outer_available)
    return RescueBlockIR(
        step_name=decl.step_name,
        body=body_ir,
        line=decl.line,
    )
```

`_build_chain` est le helper interne qui transforme un `tuple[StepCall|ForEachBlock|...]` en `tuple[CallIR|ForEachIR|...]`. Localiser son nom exact :

```bash
grep -n "def _build_chain\|def _build_flow_chain\|def _build_body\|def _build_items" clio/ir/builder.py
```

Adapter le nom dans `_build_rescue`.

- [ ] **Step 3.6 : Threader `rescues` dans la construction de FlowIR**

Dans la fonction qui retourne `FlowIR` (typiquement `build_ir` ou `_build_flow`), avant le `return FlowIR(...)`, ajouter :

```python
    rescues_ir = tuple(
        _build_rescue(rb, step_index, contracts, outer_available)
        for rb in flow_decl.rescues
    )
    return FlowIR(
        name=flow_decl.name,
        chain=chain_ir,
        rescues=rescues_ir,
        line=flow_decl.line,
    )
```

(les noms exacts `flow_decl`, `chain_ir`, `step_index` doivent matcher le code existant).

- [ ] **Step 3.7 : Run test**

```bash
pytest tests/test_rescue_block.py::test_build_ir_single_rescue -v
```
Expected : PASS.

- [ ] **Step 3.8 : Run full suite**

```bash
pytest tests/ -q
```
Expected : tous PASS (les 5 tests rescue existants + tout le reste).

- [ ] **Step 3.9 : Commit**

```bash
git add clio/ir/graph.py clio/ir/builder.py tests/test_rescue_block.py
git commit -m "$(cat <<'EOF'
feat(ir): RescueBlockIR + FlowIR.rescues

Mirror the AST shape into IR, build via a new _build_rescue helper
that reuses _build_chain. No validations yet — those land in the
next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 : IR validations (6 règles)

**Files:**
- Modify: `clio/ir/builder.py`
- Test: `tests/test_rescue_block.py`

- [ ] **Step 4.1 : Tests pour les 6 validations**

Append à `tests/test_rescue_block.py` :

```python
from clio.ir.builder import IRBuildError


_BASE_STEPS = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

STEP b
  TAKES: y: int
  GIVES: z: int
  MODE:  exact
"""


def _src(flow_body: str) -> str:
    return _BASE_STEPS + "\nFLOW p\n" + flow_body


# (1) RESCUE pour STEP inconnu
def test_rescue_unknown_step():
    src = _src("  a(x=1)\n\n  RESCUE inexistant:\n    -> abort(\"x\")\n")
    with pytest.raises(IRBuildError, match="unknown step 'inexistant'"):
        build_ir(_parse(src))


# (2) RESCUE pour STEP imbriqué (top-level only)
def test_rescue_nested_step_rejected():
    src = (
        _BASE_STEPS
        + "\nFLOW p\n"
        + "  FOR EACH item IN values:\n"
        + "    a(x=item)\n"
        + "\n  RESCUE a:\n    -> abort(\"x\")\n"
    )
    with pytest.raises(IRBuildError, match="must appear in the top-level FLOW chain"):
        build_ir(_parse(src))


# (3) Doublon RESCUE
def test_duplicate_rescue_rejected():
    src = _src(
        "  a(x=1) -> b(y=a)\n\n"
        "  RESCUE a:\n    -> abort(\"x\")\n\n"
        "  RESCUE a:\n    -> abort(\"y\")\n"
    )
    with pytest.raises(IRBuildError, match="already has a RESCUE handler"):
        build_ir(_parse(src))


# (4) ON_FAIL.abort + RESCUE conflit
def test_rescue_with_on_fail_abort_rejected():
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact
  ON_FAIL: abort("aborted in on_fail")

FLOW p
  a(x=1)

  RESCUE a:
    -> abort("from rescue")
"""
    with pytest.raises(IRBuildError, match="redundant when RESCUE"):
        build_ir(_parse(src))


# (5) Rescue body sans abort terminal
def test_rescue_body_must_end_with_abort():
    src = _src(
        "  a(x=1)\n\n"
        "  RESCUE a:\n"
        "    -> b(y=a)\n"   # pas de abort terminal
    )
    with pytest.raises(IRBuildError, match="must end with abort"):
        build_ir(_parse(src))


# (5b) Rescue body avec abort imbriqué uniquement (pas top-level)
def test_rescue_abort_in_branch_not_enough():
    src = """
STEP a
  TAKES: x: int
  GIVES: ok: bool
  MODE:  exact

CONTRACT report
  SHAPE: { ok: bool }

FLOW p
  a(x=1)

  RESCUE a:
    -> IF a.ok == true:
         -> abort("ok-branch")
       ELSE:
         -> abort("ko-branch")
"""
    with pytest.raises(IRBuildError, match="must end with abort"):
        build_ir(_parse(src))
```

- [ ] **Step 4.2 : Run tests, vérifier qu'ils échouent (validations absentes)**

```bash
pytest tests/test_rescue_block.py -v -k rescue_
```
Expected : tous FAIL (validations non implémentées).

- [ ] **Step 4.3 : Implémenter les validations dans `_build_rescue`**

Modifier `_build_rescue` (Task 3.5) pour ajouter les 6 contrôles :

```python
def _build_rescue(
    decl: "RescueBlock",
    step_index: dict[str, StepIR],
    contracts: dict[str, ContractIR],
    outer_available: dict[str, ContractIR],
    top_level_step_names: set[str],
    seen_rescue_steps: set[str],
) -> "RescueBlockIR":
    # (1) Step exists
    step = step_index.get(decl.step_name)
    if step is None:
        raise IRBuildError(
            f"Rescue refers to unknown step '{decl.step_name}' (line {decl.line})"
        )

    # (2) Top-level only
    if decl.step_name not in top_level_step_names:
        raise IRBuildError(
            f"Rescue target '{decl.step_name}' must appear in the top-level FLOW "
            f"chain (v0.8 limitation, line {decl.line})"
        )

    # (3) Single rescue per step
    if decl.step_name in seen_rescue_steps:
        raise IRBuildError(
            f"Step '{decl.step_name}' already has a RESCUE handler (duplicate at "
            f"line {decl.line})"
        )
    seen_rescue_steps.add(decl.step_name)

    # (4) No abort clash with ON_FAIL
    if step.on_fail and step.on_fail.strategies and \
       step.on_fail.strategies[-1].kind == "abort":
        raise IRBuildError(
            f"'abort(...)' final clause in ON_FAIL is redundant when RESCUE "
            f"'{decl.step_name}' is declared (rescue at line {decl.line}, "
            f"abort in step at line {step.line})"
        )

    # Build body via _build_chain (reuse existing helper).
    body_ir = _build_chain(decl.body, step_index, contracts, outer_available)

    # (5) Body terminal abort (top-level only)
    if not body_ir or not isinstance(body_ir[-1], CallIR) or body_ir[-1].step_name != "abort":
        raise IRBuildError(
            f"Rescue body for '{decl.step_name}' must end with abort(...) at the "
            f"top level of the body chain (line {decl.line})"
        )

    return RescueBlockIR(
        step_name=decl.step_name,
        body=body_ir,
        line=decl.line,
    )
```

Et dans la fonction qui appelle `_build_rescue` (Task 3.6), construire `top_level_step_names` et `seen_rescue_steps` :

```python
    top_level_step_names: set[str] = {
        item.step_name for item in chain_ir if isinstance(item, CallIR)
    }
    seen_rescue_steps: set[str] = set()
    rescues_ir = tuple(
        _build_rescue(
            rb, step_index, contracts, outer_available,
            top_level_step_names, seen_rescue_steps,
        )
        for rb in flow_decl.rescues
    )
```

`IRBuildError` est sans doute déjà défini dans `builder.py` ; sinon utiliser le type d'erreur existant pour les autres validations IR (chercher `class IRBuildError\|raise IRBuildError` dans builder.py).

- [ ] **Step 4.4 : Run tests, vérifier qu'ils passent maintenant**

```bash
pytest tests/test_rescue_block.py -v
```
Expected : tous PASS.

- [ ] **Step 4.5 : Run full suite**

```bash
pytest tests/ -q
```
Expected : aucun régression (tous les 460+ tests PASS).

- [ ] **Step 4.6 : Commit**

```bash
git add clio/ir/builder.py tests/test_rescue_block.py
git commit -m "$(cat <<'EOF'
feat(ir): 5 validations on RescueBlockIR

Reject:
  1. RESCUE for unknown step
  2. RESCUE for step nested in FOR EACH / IF / MATCH / WHILE bodies
  3. duplicate RESCUE for the same step
  4. ON_FAIL ending with abort + RESCUE on the same step
  5. rescue body whose last top-level item is not abort(...)

All errors include the source line.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 : IR `_walk` descend dans rescue body (cohérence avec autres walkers)

**Files:**
- Modify: `clio/ir/builder.py`
- Test: `tests/test_rescue_block.py`

- [ ] **Step 5.1 : Test parallel-foreach validation descend dans rescue body**

Append à `tests/test_rescue_block.py` :

```python
def test_walker_descends_into_rescue_body():
    """_validate_parallel_for_each doit descendre dans rescue.body. Si un FOR
    EACH PARALLEL nesting illégal apparaît dans un rescue body, le builder
    doit le rejeter avec le message standard."""
    src = """
STEP load
  TAKES: x: int
  GIVES: items: List<int>
  MODE:  exact

STEP work
  TAKES: i: int
  GIVES: r: int
  MODE:  exact

FLOW p
  load(x=1)

  RESCUE load:
    -> FOR EACH a IN items PARALLEL AS A:
         FOR EACH b IN items PARALLEL AS B:
           work(i=b)
    -> abort("nested parallel forbidden")
"""
    with pytest.raises(IRBuildError, match="nested PARALLEL"):
        build_ir(_parse(src))
```

- [ ] **Step 5.2 : Run, voir l'échec (le walker ne visite pas rescue.body encore)**

```bash
pytest tests/test_rescue_block.py::test_walker_descends_into_rescue_body -v
```
Expected : FAIL — soit pas d'erreur soulevée, soit message différent.

- [ ] **Step 5.3 : Étendre `_walk` dans `_validate_parallel_for_each`**

Localiser la fonction `_validate_parallel_for_each` (vers builder.py:687) puis modifier l'appel final :

```python
    _walk(graph.flow.chain)
    # NEW : descendre aussi dans chaque rescue.body
    for rb in graph.flow.rescues:
        _walk(rb.body)
```

- [ ] **Step 5.4 : Run tests**

```bash
pytest tests/test_rescue_block.py -v
```
Expected : tous PASS.

- [ ] **Step 5.5 : Commit**

```bash
git add clio/ir/builder.py tests/test_rescue_block.py
git commit -m "$(cat <<'EOF'
feat(ir): _validate_parallel_for_each descends into rescue bodies

Cohérent avec le walker qui descend déjà dans IF/MATCH/WHILE/FOR EACH
bodies — un rescue body peut contenir un FOR EACH, donc la validation
de nesting illégal doit s'y appliquer aussi.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 : Python emitter — try/except + helper `_rescue_<name>`

**Files:**
- Modify: `clio/emitters/python.py`
- Modify: `clio/emitters/_python_helpers.py` (selon le découpage existant)
- Test: `tests/test_emitters/test_python.py`

- [ ] **Step 6.1 : Test snapshot — émission rescue minimale**

Append à `tests/test_emitters/test_python.py` :

```python
RESCUE_SIMPLE_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: result: int
  MODE:  exact

FLOW pipeline
  load(path="x.csv")
    -> detect(rows=load)

  RESCUE detect:
    -> abort("detection failed")

RESOURCES
  target: python
"""


def test_python_emit_rescue_basic(tmp_path):
    """Le code python émis doit wrapper detect dans try/except et appeler
    _rescue_detect dans le except, avec raise FlowAborted."""
    program = parse_clio(RESCUE_SIMPLE_SRC)   # adapter au helper existant
    graph = build_ir(program)
    out = emit_python(graph, tmp_path)        # adapter
    main_py = (tmp_path / "flow.py").read_text()
    assert "def _rescue_detect(state" in main_py
    assert "raise FlowAborted(\"detection failed\")" in main_py
    # Le call site detect doit être dans un try/except.
    assert "try:" in main_py
    assert "_rescue_detect(state" in main_py
```

(Adapter `parse_clio` / `emit_python` aux helpers de test existants — chercher dans le fichier comment les autres tests instrumentent l'émission.)

- [ ] **Step 6.2 : Run, voir l'échec**

```bash
pytest tests/test_emitters/test_python.py::test_python_emit_rescue_basic -v
```
Expected : FAIL — output ne contient ni `_rescue_detect` ni `try:` autour du call.

- [ ] **Step 6.3 : Émettre le helper `_rescue_<step_name>`**

Dans `clio/emitters/python.py`, après la génération des fonctions de step (chercher où les `def step_<name>` sont émises), ajouter une boucle :

```python
        # Émettre les helpers _rescue_<step_name>
        for rb in graph.flow.rescues:
            lines.append(f"def _rescue_{rb.step_name}(state):")
            scope_local: set[str] = set(state_keys_so_far)  # adapter au pattern existant
            for item in rb.body:
                _emit_item(item, indent="    ", scope_local=scope_local)
            lines.append("")
```

`_emit_item` est le walker existant (ligne 442) qui sait déjà rendre un `CallIR(step_name="abort", ...)` → `raise FlowAborted("msg")` (cf python.py:354 `s.kind == "abort"`). Si `_emit_item` ne gère pas encore les CallIR à `abort`, l'étendre dans cette task.

- [ ] **Step 6.4 : Wrapper le call site du STEP protégé**

Dans `_emit_call` (python.py:414) ou la couche au-dessus qui sait quel STEP est wrappé : pour chaque `CallIR` dont `call.step_name` apparaît dans `{r.step_name for r in graph.flow.rescues}`, wrapper :

```python
        if call.step_name in rescue_target_names:
            lines.append(f"{indent}try:")
            # Émettre le call existant indenté de 4
            _emit_existing_call(call, indent + "    ", scope_local)
            lines.append(f"{indent}except FlowAborted:")
            lines.append(f"{indent}    raise")
            lines.append(f"{indent}except Exception:")
            lines.append(f"{indent}    _rescue_{call.step_name}(state)")
            lines.append(f"{indent}    raise")
        else:
            # comportement v0.7 inchangé
            _emit_existing_call(call, indent, scope_local)
```

Construire `rescue_target_names = {r.step_name for r in graph.flow.rescues}` une fois en début d'émission et le passer à `_emit_call` (ou le rendre disponible via la closure `_emit_item`).

- [ ] **Step 6.5 : Étendre `_emit_item` pour gérer `CallIR(step_name="abort", ...)`**

Dans le walker `_emit_item` (python.py:442) ou son helper de call, ajouter le cas synthétique :

```python
        if isinstance(item, CallIR) and item.step_name == "abort":
            msg = next((v for k, v in item.kwargs if k == "message"), "")
            lines.append(f"{indent}raise FlowAborted({msg!r})")
            return
```

`FlowAborted` est déjà importable depuis le runtime (cf python.py:355). S'assurer que l'import existe en tête de fichier :

```python
from clio_runtime import FlowAborted   # ou le chemin réel — chercher dans python.py
```

- [ ] **Step 6.6 : Run test, vérifier qu'il passe**

```bash
pytest tests/test_emitters/test_python.py::test_python_emit_rescue_basic -v
```
Expected : PASS.

- [ ] **Step 6.7 : Test runtime : exécuter le flow émis et vérifier le comportement**

Append au même fichier :

```python
RESCUE_RUNTIME_SRC = """
STEP fail_step
  TAKES: x: int
  GIVES: y: int
  MODE:  exact
  IMPL:
    code:
      lang: python
      body: |
        raise RuntimeError("synthetic failure")

FLOW p
  fail_step(x=1)

  RESCUE fail_step:
    -> abort("recovered with abort")

RESOURCES
  target: python
"""


def test_python_runtime_rescue_aborts(tmp_path):
    """Compiler + exécuter : fail_step doit lever, le rescue doit transformer
    en FlowAborted avec le message du abort."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    program = parse_clio(RESCUE_RUNTIME_SRC)
    graph = build_ir(program)
    emit_python(graph, out_dir)

    import subprocess, sys
    result = subprocess.run(
        [sys.executable, str(out_dir / "main.py")],
        cwd=str(out_dir),
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "recovered with abort" in (result.stderr + result.stdout)
```

- [ ] **Step 6.8 : Run, vérifier**

```bash
pytest tests/test_emitters/test_python.py::test_python_runtime_rescue_aborts -v
```
Expected : PASS. Si le test runtime ne fonctionne pas (différences d'orchestration, gating e2e), le marquer `@pytest.mark.e2e` et fournir un test purement snapshot équivalent.

- [ ] **Step 6.9 : Commit**

```bash
git add clio/emitters/python.py clio/emitters/_python_helpers.py tests/test_emitters/test_python.py
git commit -m "$(cat <<'EOF'
feat(emitter,python): RESCUE handler — try/except + _rescue_<name>

Each STEP referenced by a RESCUE block is wrapped in a try/except
Exception in the emitted main flow. The except branch calls a
_rescue_<step_name>(state) helper whose body re-uses the standard
flow_item walker; the synthetic abort("msg") CallIR is rendered as
raise FlowAborted("msg"). FlowAborted is re-raised verbatim — defensive
for the case rule (4) accidentally allowed it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 : MCP-server emitter — async try/except

**Files:**
- Modify: `clio/emitters/mcp_server.py` ou `clio/emitters/_mcp_helpers.py`
- Test: `tests/test_emitters/test_mcp_server.py`

- [ ] **Step 7.1 : Test snapshot mcp-server**

Append à `tests/test_emitters/test_mcp_server.py` (adapter `target: mcp-server` à RESCUE_SIMPLE_SRC) :

```python
RESCUE_MCP_SRC = """
STEP load
  TAKES: path: str
  GIVES: rows: List<int>
  MODE:  exact

STEP detect
  TAKES: rows: List<int>
  GIVES: result: int
  MODE:  judgment

FLOW pipeline
  load(path="x.csv")
    -> detect(rows=load)

  RESCUE detect:
    -> abort("detection failed")

RESOURCES
  target: mcp-server
"""


def test_mcp_emit_rescue_basic(tmp_path):
    program = parse_clio(RESCUE_MCP_SRC)
    graph = build_ir(program)
    out = emit_mcp_server(graph, tmp_path)
    server_py = (tmp_path / "server.py").read_text()
    assert "async def _rescue_detect(state" in server_py
    assert "raise FlowAborted(\"detection failed\")" in server_py
    assert "try:" in server_py
    assert "await _rescue_detect(state" in server_py
```

- [ ] **Step 7.2 : Run, voir l'échec**

```bash
pytest tests/test_emitters/test_mcp_server.py::test_mcp_emit_rescue_basic -v
```
Expected : FAIL.

- [ ] **Step 7.3 : Émettre `async def _rescue_<name>` + wrapper async**

Dans `clio/emitters/_mcp_helpers.py` ou `clio/emitters/mcp_server.py`, dupliquer le pattern de Task 6 mais en async :

- helper : `async def _rescue_<step_name>(state, _session=None):`
- wrapper : `try: ... except FlowAborted: raise except Exception: await _rescue_<step_name>(state, _session=_session); raise`
- threading `_session` : si le rescue body contient des judgment steps, propager `_session=_session` comme dans le pattern FOR EACH PARALLEL existant (cf `clio/emitters/_mcp_helpers.py` chercher `_session`).

- [ ] **Step 7.4 : Run test**

```bash
pytest tests/test_emitters/test_mcp_server.py::test_mcp_emit_rescue_basic -v
```
Expected : PASS.

- [ ] **Step 7.5 : Run full suite**

```bash
pytest tests/ -q
```
Expected : tout PASS.

- [ ] **Step 7.6 : Commit**

```bash
git add clio/emitters/_mcp_helpers.py clio/emitters/mcp_server.py tests/test_emitters/test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(emitter,mcp-server): RESCUE handler — async try/except + _rescue_<name>

Mirror du lowering python en async. Le helper _rescue_<step_name>
prend (state, _session=None) ; les judgment steps imbriqués dans le
body héritent de _session via le même pattern que FOR EACH PARALLEL.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 : Rejet à la compilation — langgraph + claude-cli

**Files:**
- Modify: `clio/emitters/langgraph.py`
- Modify: `clio/emitters/claude_cli.py`
- Test: `tests/test_emitters/test_langgraph.py`
- Test: `tests/test_emitters/test_claude_cli.py` (si existe)

- [ ] **Step 8.1 : Tests rejets**

Append à `tests/test_emitters/test_langgraph.py` :

```python
def test_langgraph_rejects_rescue(tmp_path):
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  a(x=1)

  RESCUE a:
    -> abort("x")

RESOURCES
  target: langgraph
"""
    program = parse_clio(src)
    graph = build_ir(program)
    with pytest.raises(EmitError, match="RESCUE handlers are not supported by the langgraph target"):
        emit_langgraph(graph, tmp_path)
```

(Et un test équivalent pour claude-cli si la suite a un fichier `test_claude_cli.py`.)

- [ ] **Step 8.2 : Étendre `_reject_unsupported` dans `clio/emitters/langgraph.py`**

Localiser `_reject_unsupported` (langgraph.py:126) et ajouter, après l'appel principal `_reject_unsupported(graph.flow.chain)` :

```python
        if graph.flow.rescues:
            rb = graph.flow.rescues[0]
            raise EmitError(
                f"RESCUE handlers are not supported by the langgraph target in v0.8 "
                f"(needs cyclic edges + state reducer; planned for the multi-step "
                f"branches sprint). Use --target python or --target mcp-server. "
                f"Rescue at line {rb.line}."
            )
```

- [ ] **Step 8.3 : Étendre `_reject_parallel` dans `clio/emitters/claude_cli.py`**

Renommer en `_reject_unsupported` (ou ajouter un voisin `_reject_rescue`) et ajouter le rejet :

```python
    def _reject_rescue(self, graph: FlowGraph) -> None:
        if graph.flow and graph.flow.rescues:
            rb = graph.flow.rescues[0]
            raise EmitError(
                f"RESCUE handlers are not supported by the claude-cli target. "
                f"Use --target python or --target mcp-server. Rescue at line {rb.line}."
            )

    # ... dans emit() :
        self._reject_parallel(graph)
        self._reject_rescue(graph)
```

- [ ] **Step 8.4 : Run tests**

```bash
pytest tests/test_emitters/test_langgraph.py::test_langgraph_rejects_rescue tests/test_emitters/test_claude_cli.py -v
```
Expected : PASS.

- [ ] **Step 8.5 : Commit**

```bash
git add clio/emitters/langgraph.py clio/emitters/claude_cli.py tests/test_emitters/
git commit -m "$(cat <<'EOF'
feat(emitter): langgraph + claude-cli reject RESCUE in v0.8

Pointer error message vers --target python / --target mcp-server.
LangGraph attendra le sprint multi-step branches pour supporter à la
fois RESCUE, WHILE et IF/MATCH multi-step.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 : Viewer — cluster RESCUE en rouge dans mermaid + html

**Files:**
- Modify: `clio/graph_render.py`
- Test: `tests/test_graph_render.py`

- [ ] **Step 9.1 : Test snapshot viewer**

Append à `tests/test_graph_render.py` :

```python
def test_mermaid_renders_rescue_cluster():
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  a(x=1)

  RESCUE a:
    -> abort("boom")
"""
    graph = build_ir(parse_clio(src))
    mermaid = to_mermaid(graph)
    # Le STEP a a une edge dotted vers son rescue.
    assert "rescue_a" in mermaid
    assert "a -. fails .-> rescue_a" in mermaid or "a -.->|fails| rescue_a" in mermaid
    # Le rescue est dans un sub-cluster ou nœud rouge.
    assert "rescue_a" in mermaid


def test_html_exposes_rescue_meta():
    src = """
STEP a
  TAKES: x: int
  GIVES: y: int
  MODE:  exact

FLOW p
  a(x=1)

  RESCUE a:
    -> abort("boom")
"""
    graph = build_ir(parse_clio(src))
    html = to_html(graph)
    assert "rescue_meta" in html.lower() or "RESCUE_META_JSON" in html
    assert "boom" in html
```

- [ ] **Step 9.2 : Run, voir l'échec**

```bash
pytest tests/test_graph_render.py -v -k rescue
```
Expected : FAIL.

- [ ] **Step 9.3 : Étendre `_to_mermaid_rich_labels`**

Dans `clio/graph_render.py` (ligne 475 et suivantes) :

1. Ajouter `rescue_meta: dict[str, dict] = {}` à côté des autres metas (ligne ~492).
2. Après le walker principal de la chain, parcourir `graph.flow.rescues` :

```python
    for rb in graph.flow.rescues:
        target_id = _node_id_for_step(rb.step_name)
        rescue_id = f"rescue_{rb.step_name}"
        # Nœud rouge.
        lines.append(f'{rescue_id}["RESCUE<br/>{rb.step_name}"]')
        lines.append(f'class {rescue_id} rescueClass')
        # Edge dotted depuis le STEP protégé.
        lines.append(f"{target_id} -. fails .-> {rescue_id}")
        # Body : émettre les step calls comme sous-flow.
        prev_id = rescue_id
        for item in rb.body:
            if isinstance(item, CallIR):
                if item.step_name == "abort":
                    abort_id = f"abort_{rb.step_name}"
                    msg = next((v for k, v in item.kwargs if k == "message"), "")
                    lines.append(f'{abort_id}(("abort: {msg}"))')
                    lines.append(f"{prev_id} --> {abort_id}")
                    prev_id = abort_id
                else:
                    item_id = _node_id_for_step(item.step_name)
                    lines.append(f"{prev_id} --> {item_id}")
                    prev_id = item_id
        rescue_meta[rescue_id] = {
            "step_name": rb.step_name,
            "body": [{"step_name": c.step_name, "kwargs": list(c.kwargs)} for c in rb.body if isinstance(c, CallIR)],
        }

    # Style classe rouge (vers la fin de la fonction, ajouter au header CSS) :
    lines.insert(after_header_index, "classDef rescueClass fill:#fce4e4,stroke:#d73a49,stroke-width:2px,color:#7b1d1f")
```

3. Étendre la signature de retour pour inclure `rescue_meta` :

```python
    return (
        "\n".join(lines),
        foreach_meta,
        if_meta,
        match_meta,
        while_meta,
        rescue_meta,
    )
```

4. Ligne ~1399, mettre à jour le call site qui dépacke 5 valeurs en 6 :

```python
    mermaid_source, foreach_meta, if_meta, match_meta, while_meta, rescue_meta = (
        _to_mermaid_rich_labels(graph)
    )
```

5. Ligne ~1436, ajouter le replace JSON :

```python
        .replace("__RESCUE_META_JSON__", json.dumps(rescue_meta, ensure_ascii=False))
```

6. Mettre à jour le template HTML (chercher `__WHILE_META_JSON__` pour trouver l'emplacement, dupliquer le pattern pour `RESCUE_META_JSON`).

- [ ] **Step 9.4 : Run tests**

```bash
pytest tests/test_graph_render.py -v -k rescue
```
Expected : PASS.

- [ ] **Step 9.5 : Vérification visuelle (manuelle, optionnelle)**

```bash
python -m clio graph examples/critical_pipeline.clio --format html --output /tmp/rescue.html && open /tmp/rescue.html
```
(Cet example sera créé en Task 11. Sauter ce step si l'agent n'a pas d'env GUI.)

- [ ] **Step 9.6 : Commit**

```bash
git add clio/graph_render.py tests/test_graph_render.py
git commit -m "$(cat <<'EOF'
feat(graph): RESCUE cluster + accent rouge dans mermaid + html viewer

Nouveau hue rescue=#d73a49. Edge dotted "fails" depuis le STEP protégé
vers son nœud rescue. Body rendu comme sub-chain, terminant par un
nœud abort. rescue_meta exposé au JS pour future enrichissement (panel
side-bar du clic).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 : Documentation — SPEC + manual + CHANGELOG

**Files:**
- Modify: `docs/LANGUAGE_SPEC.md`
- Modify: `docs/manual/02-language-tour.md`
- Modify: `docs/manual/03-cookbook.md`
- Modify: `docs/manual/06-troubleshooting.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 10.1 : LANGUAGE_SPEC — section RESCUE**

Dans `docs/LANGUAGE_SPEC.md`, dans la section `## Failure strategies (ON_FAIL)`, après le bloc de syntaxe ON_FAIL existant, ajouter :

````markdown
## RESCUE handler (v0.8)

Top-level handler attached to a STEP. Runs only if that STEP raises
after its `ON_FAIL` chain (if any) exhausts itself.

```
RESCUE <step_name>:
    <body>
```

The body is a chain of step calls. The **last item of the top-level
chain** must be `abort("message")`. Use intermediate items to notify,
log, or otherwise side-effect before aborting.

Composition with `ON_FAIL`:

| `ON_FAIL` last clause | RESCUE present | Behaviour |
| --- | --- | --- |
| _(no ON_FAIL)_ | no | Exception propagates. |
| retry/escalate/fallback (no abort) | no | Exception propagates after exhaustion. |
| `... then abort("msg")` | no | `FlowAborted("msg")` after exhaustion. |
| _(no ON_FAIL)_ | yes | Exception caught, handler runs, ends with abort. |
| retry/escalate/fallback | yes | Exhaustion → handler runs → abort. |
| `... then abort("msg")` | yes | **Compile error**: redundant `abort` final. |

Targets: python, mcp-server. **langgraph rejects** RESCUE in v0.8.
**claude-cli rejects** RESCUE.

v0.8 limitations:
- One RESCUE per STEP.
- The protected STEP must appear in the top-level FLOW chain (not in
  a FOR EACH / IF / MATCH / WHILE body).
- The handler body cannot inspect the captured error message
  (`step_name.error` reserved for v0.9+).
- Body must end with `abort(...)` — `RESUME` keyword for fall-through
  is reserved for v0.9+.

Example:

```
STEP detect_churn
  TAKES:    rows: List<int>
  GIVES:    risks: List<{client: str, score: float}>
  MODE:     judgment
  ON_FAIL:  retry(3) then escalate

FLOW pipeline
  load_csv(path="data.csv")
    -> detect_churn(rows=load_csv)
    -> route_alerts(risks=detect_churn)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts", reason="churn detection failed")
    -> abort("churn detection failed — see #alerts")
```

Runtime sequence on failure:
1. `detect_churn` raises.
2. `ON_FAIL: retry(3) then escalate` exhausts itself.
3. `RESCUE detect_churn` body runs: `notify_slack` then `abort`.
4. The chain item after `detect_churn` (`route_alerts`) is **not**
   executed.
````

Mettre à jour la ligne 448 (actuellement « no `.FAILS` shorthand ... those are deferred ») par :

```markdown
ELSE is optional. No boolean conjunction (`and`/`or`) in v0.7.
For failure-aware branching (`.FAILS` shorthand), see RESCUE
handlers (v0.8) below.
```

Et remplacer l'exemple narratif lignes 649-657 par :

```markdown
FLOW rétention_clients
  charger_clients(fichier="clients.csv")
    -> détecter_churn(clients)
    -> FOR EACH risque IN risques:
         vérifier_ticket_zendesk(risque.client)
           -> rédiger_mail_rétention(risque, dernier_ticket)

  RESCUE détecter_churn:
    -> abort("Impossible de détecter le churn — vérifier le format du CSV")
```

- [ ] **Step 10.2 : Manual — language tour**

Dans `docs/manual/02-language-tour.md`, après la section WHILE, ajouter une section RESCUE de ~30 lignes : présentation 2-3 phrases, exemple « load → detect → route + RESCUE detect: notify + abort », mention « one rescue per step ».

- [ ] **Step 10.3 : Manual — cookbook**

Dans `docs/manual/03-cookbook.md`, ajouter la recipe « pipeline LLM critique avec ON_FAIL × RESCUE » : code complet `examples/critical_pipeline.clio` (créé en Task 11), explication 5-10 lignes du pattern « auto-recovery first, human handler last ».

- [ ] **Step 10.4 : Manual — troubleshooting**

Dans `docs/manual/06-troubleshooting.md`, ajouter deux entrées :

```markdown
### "Rescue body for 'X' must end with abort(...)"

Le dernier item du body de votre `RESCUE X:` doit être `abort("message")`
au top-level (pas dans une branche IF/MATCH). Hoisting :

```
RESCUE detect:
  -> IF detect.ok == true:
       -> abort("ok-branch")
     ELSE:
       -> abort("ko-branch")
```

→ rejeté. Réécrire :

```
RESCUE detect:
  -> IF detect.ok == true:
       -> log_ok()
     ELSE:
       -> log_ko()
  -> abort("done")
```

### "'abort(...)' final clause in ON_FAIL is redundant when RESCUE 'X' is declared"

Vous avez déclaré à la fois `ON_FAIL: ... then abort(...)` sur le STEP X
et un `RESCUE X:` au niveau du FLOW. C'est ambigu (double abort).
Choisir : soit retirer `abort(...)` de la chaîne ON_FAIL (laisser
retry/escalate/fallback uniquement), soit retirer le `RESCUE X:`.
```

- [ ] **Step 10.5 : CHANGELOG**

Dans `CHANGELOG.md`, ajouter en haut (au-dessus de v0.7.0) :

```markdown
## v0.8.0 — RESCUE handler (unreleased)

### Added
- **RESCUE handler** : top-level block attached to a STEP that runs if
  the STEP raises after its `ON_FAIL` chain exhausts. Body is a chain of
  step calls ending in mandatory `abort("message")`. Targets: python,
  mcp-server. langgraph and claude-cli reject at compile time.
- New keyword `RESCUE`.
- Five new IR validations (unknown step / nested step / duplicate
  rescue / abort clash with ON_FAIL / non-terminal abort).
- Mermaid + HTML viewer cluster en rouge (#d73a49) avec edge dotted
  « fails » et `rescue_meta` exposé au JS.
- `examples/critical_pipeline.clio` showcasing ON_FAIL × RESCUE
  composition.

### Documentation
- LANGUAGE_SPEC §RESCUE handler with composition table.
- Manual : language-tour, cookbook, troubleshooting updated.
- Narrative example l.656 migré de `IF X.FAILS:` (deferred) à `RESCUE`.
```

- [ ] **Step 10.6 : Commit**

```bash
git add docs/ CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(v0.8): RESCUE handler — SPEC + manual + CHANGELOG + narrative ex

LANGUAGE_SPEC §RESCUE handler with composition table.
Manual : language-tour + cookbook + troubleshooting (2 erreurs).
Narrative example l.656 migré de `IF X.FAILS:` (deferred) à `RESCUE`.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11 : Nouvel exemple `examples/critical_pipeline.clio`

**Files:**
- Create: `examples/critical_pipeline.clio`
- Test: `tests/test_examples.py`

- [ ] **Step 11.1 : Créer l'exemple**

Créer `examples/critical_pipeline.clio` :

```
CONTRACT churn_report
  SHAPE:    { risks: List<{ client: str, score: float }> }

STEP load_clients
  TAKES:    path: str
  GIVES:    rows: List<int>
  MODE:     exact
  IMPL:
    code:
      lang: python
      body: |
        return [1, 2, 3]

STEP detect_churn
  TAKES:    rows: List<int>
  GIVES:    report: churn_report
  MODE:     judgment
  ON_FAIL:  retry(3) then escalate

STEP notify_slack
  TAKES:    channel: str, reason: str
  GIVES:    sent: bool
  MODE:     exact
  IMPL:
    code:
      lang: python
      body: |
        print(f"[slack] {channel}: {reason}")
        return True

FLOW pipeline
  load_clients(path="clients.csv")
    -> detect_churn(rows=load_clients)

  RESCUE detect_churn:
    -> notify_slack(channel="#alerts", reason="churn detection failed")
    -> abort("churn detection failed — see #alerts")

RESOURCES
  target:   python
```

- [ ] **Step 11.2 : Test : l'exemple compile sans erreur**

Append à `tests/test_examples.py` :

```python
def test_critical_pipeline_compiles_to_python(tmp_path):
    src = (Path("examples") / "critical_pipeline.clio").read_text()
    program = parse_clio(src)
    graph = build_ir(program)
    out = tmp_path / "out"
    out.mkdir()
    emit_python(graph, out)
    assert (out / "main.py").exists() or (out / "flow.py").exists()


def test_critical_pipeline_compiles_to_mcp(tmp_path):
    src = (Path("examples") / "critical_pipeline.clio").read_text().replace(
        "target:   python", "target:   mcp-server"
    )
    program = parse_clio(src)
    graph = build_ir(program)
    out = tmp_path / "out"
    out.mkdir()
    emit_mcp_server(graph, out)
    assert (out / "server.py").exists()
```

- [ ] **Step 11.3 : Run tests**

```bash
pytest tests/test_examples.py -v -k critical_pipeline
```
Expected : PASS.

- [ ] **Step 11.4 : Run full suite**

```bash
pytest tests/ -q
```
Expected : ~480 tests passed (les ~20 nouveaux + les 457 existants).

- [ ] **Step 11.5 : Commit**

```bash
git add examples/critical_pipeline.clio tests/test_examples.py
git commit -m "$(cat <<'EOF'
feat(examples): critical_pipeline.clio — ON_FAIL × RESCUE composition

Pipeline LLM critique avec retry(3) + escalate (auto) puis RESCUE
detect_churn pour notifier Slack + abort. Compile vers python et
mcp-server.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12 : Vérification finale

**Files:**
- Aucun changement attendu — vérification de cohérence.

- [ ] **Step 12.1 : Run full test suite**

```bash
pytest tests/ -v
```
Expected : tous PASS (~480 tests, dont ~20 nouveaux RESCUE-related).

- [ ] **Step 12.2 : Lint / format check**

```bash
ruff check clio/ tests/ 2>&1 | tail -20
```
Expected : 0 erreur. Si erreurs, fixer.

- [ ] **Step 12.3 : Smoke test CLI sur l'exemple**

```bash
python -m clio compile examples/critical_pipeline.clio --target python --output /tmp/rescue_out
ls /tmp/rescue_out/
```
Expected : génère un projet python valide. Vérifier visuellement la présence de `_rescue_detect_churn` dans `main.py` (ou nom du fichier équivalent).

- [ ] **Step 12.4 : Smoke test viewer**

```bash
python -m clio graph examples/critical_pipeline.clio --format mermaid
```
Expected : la sortie contient `rescue_detect_churn`, `fails`, `abort_detect_churn`.

- [ ] **Step 12.5 : Vérifier que langgraph rejette**

```bash
python -m clio compile examples/critical_pipeline.clio --target langgraph --output /tmp/rescue_lg 2>&1 | head
```
Expected : message d'erreur explicite mentionnant « RESCUE handlers are not supported by the langgraph target ».

- [ ] **Step 12.6 : Mettre à jour le memory et clore**

Append à `~/.claude/projects/-Users-jean-paulgavini-Documents-Dev-clio/memory/next_steps.md` (ou nouvelle entrée datée) :
- v0.8 sprint : RESCUE handler landed.
- Tests : ~477+ passed.
- Targets : python ✓, mcp-server ✓, langgraph ✗ (rejette), claude-cli ✗ (rejette).
- Roadmap restant : LangGraph multi-step branches (incl. RESCUE), `and`/`or` in conditions, `.error` access in RESCUE body, `RESUME` keyword.

Pas de commit séparé pour ce step — c'est de la mise à jour du memory hors-repo.

---

## Récapitulatif

| Task | Sortie | Tests ajoutés (env.) |
|---|---|---|
| 1 | RESCUE keyword + RescueBlock AST | 3 |
| 2 | parse_rescue_block + abort synthétique | 3 |
| 3 | RescueBlockIR + FlowIR.rescues | 1 |
| 4 | 5 validations IR | 6 |
| 5 | _walk descent | 1 |
| 6 | python emitter | 2 |
| 7 | mcp-server emitter | 1 |
| 8 | langgraph + claude-cli reject | 2 |
| 9 | Viewer cluster | 2 |
| 10 | SPEC + manual + CHANGELOG | 0 |
| 11 | Nouvel exemple + 2 tests | 2 |
| 12 | Vérification finale | 0 |
| **Total** | | **~23 nouveaux tests** |

Total estimé : 12 commits, ~480 tests verts à la fin (vs 457 actuels).
