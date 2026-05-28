from urllib.parse import urlparse

from clio.parser.ast_nodes import (
    ApiInvoke,
    BoolAndExpr,
    BoolOrExpr,
    CacheConfig,
    CliInvoke,
    CodeImpl,
    CompareExpr,
    ConstrainedType,
    ContractDecl,
    ContractRef,
    DatabaseSpec,
    DictType,
    EnumType,
    ErrorAccessExpr,
    Field,
    FieldRefExpr,
    FileBody,
    FloatExpr,
    FlowDecl,
    ForEachBlock,
    FormBody,
    HttpServerSpec,
    IdentExpr,
    IfBlock,
    ImplBlock,
    ImportDecl,
    ImportItem,
    IntExpr,
    InvokeBlock,
    JsonBody,
    ListType,
    MatchBlock,
    MatchCase,
    McpServerSpec,
    McpToolImpl,
    MultipartBody,
    OnFailChain,
    OnFailStrategy,
    OptionalType,
    Predicate,
    PrimitiveType,
    Program,
    RawBody,
    RecordType,
    ReexportDecl,
    RescueBlock,
    ResourcesDecl,
    RestBody,
    RestImpl,
    ResumeAst,
    RetryPolicy,
    ShellImpl,
    SqlImpl,
    SseServerSpec,
    StdioServerSpec,
    StepCall,
    StepDecl,
    StrExpr,
    TestDecl,
    TypeExpr,
    WhileBlock,
)
from clio.parser.lexer import lex
from clio.parser.tokens import Token, TokenType


class ParseError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


_PRIMITIVE_TYPES = {"int", "float", "str", "bool"}

# v0.21 — allowed constraint names per primitive base. `bool` carries no
# constraints. See LANGUAGE_SPEC.md §Constrained types for the semantics
# (length for `str`, value for `int` / `float`, decimal places for
# `float(precision=N)` which renders to JSON Schema `multipleOf: 10**-N`).
_ALLOWED_CONSTRAINTS: dict[str, frozenset[str]] = {
    "str": frozenset({"max", "min"}),
    "int": frozenset({"max", "min"}),
    "float": frozenset({"max", "min", "precision"}),
}
_VALID_MODES = {"exact", "judgment"}
_VALID_LANGS = {"python", "rust", "go", "node", "bash", "auto"}
_VALID_IMPL_MODES = {"code", "rest", "shell", "mcp_tool", "sql"}
_VALID_MCP_TRANSPORTS = {"stdio", "sse", "http"}
_VALID_SQL_DRIVERS = {"sqlite", "postgres", "mysql"}
_VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_VALID_INVOKE_MODES = {"cli", "api"}
_VALID_PROTOCOLS = {"anthropic", "openai", "bedrock", "vertex"}
_VALID_PARSE_MODES = {"none", "json"}


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def expect(self, ttype: TokenType, value: str | None = None) -> Token:
        t = self.peek()
        if t.type != ttype or (value is not None and t.value != value):
            want = f"{ttype.value}" + (f" {value!r}" if value else "")
            raise ParseError(f"expected {want}, got {t.type.value} {t.value!r}", t.line, t.col)
        return self.advance()

    def skip_newlines(self) -> None:
        while self.peek().type == TokenType.NEWLINE:
            self.advance()

    def parse_program(self) -> Program:
        decls: list[object] = []
        imports: list[ImportDecl] = []
        self.skip_newlines()
        while self.peek().type != TokenType.EOF:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "FROM":
                imports.append(self.parse_import_decl())
                self.skip_newlines()
                continue

            # Visibility prefix detection (EXPOSE / INTERNAL)
            exposed: bool | None = None
            vis_tok = None
            if t.type == TokenType.KEYWORD and t.value in ("EXPOSE", "INTERNAL"):
                vis_tok = t
                exposed = (t.value == "EXPOSE")
                self.advance()
                nxt = self.peek()
                if nxt.type == TokenType.KEYWORD and nxt.value in ("EXPOSE", "INTERNAL"):
                    raise ParseError(
                        "only one visibility marker allowed before FLOW/CONTRACT",
                        nxt.line, nxt.col,
                    )
                # Re-export form: EXPOSE <IDENT> (no FLOW/CONTRACT keyword, no body).
                # Only valid with EXPOSE; INTERNAL <name> stays an error (E_VIS_002).
                if exposed is True and nxt.type == TokenType.IDENT:
                    name_tok = self.expect(TokenType.IDENT)
                    decls.append(ReexportDecl(
                        name=name_tok.value, line=vis_tok.line, col=vis_tok.col,
                    ))
                    self.skip_newlines()
                    continue
                if not (nxt.type == TokenType.KEYWORD and nxt.value in ("FLOW", "CONTRACT")):
                    raise ParseError(
                        f"{vis_tok.value} applies only to FLOW and CONTRACT (got {nxt.value!r})",
                        vis_tok.line, vis_tok.col,
                    )
                t = nxt

            if t.type == TokenType.KEYWORD and t.value == "STEP":
                decls.append(self.parse_step())
            elif t.type == TokenType.KEYWORD and t.value == "CONTRACT":
                decls.append(self.parse_contract(exposed=exposed or False))
            elif t.type == TokenType.KEYWORD and t.value == "FLOW":
                decls.append(self.parse_flow(exposed=exposed or False))
            elif t.type == TokenType.KEYWORD and t.value == "RESOURCES":
                decls.append(self.parse_resources())
            elif t.type == TokenType.KEYWORD and t.value == "TEST":
                decls.append(self.parse_test())
            else:
                raise ParseError(
                    f"expected FROM / STEP / CONTRACT / FLOW / RESOURCES / TEST, "
                    f"got {t.type.value} {t.value!r}",
                    t.line, t.col,
                )
            self.skip_newlines()
        return Program(tuple(decls), imports=tuple(imports))

    def parse_test(self) -> TestDecl:
        """Parse a `TEST <name>: ... ` top-level block.

        Grammar:
            TEST IDENT NEWLINE INDENT
              "FLOW" ":" IDENT NEWLINE
              ("WITH" ":" NEWLINE INDENT (IDENT ":" literal NEWLINE)* DEDENT)?
              ("EXPECTS"    ":" NEWLINE INDENT (IDENT ":" predicate NEWLINE)+ DEDENT)?
              ("EXPECTS_NOT" ":" NEWLINE INDENT (IDENT ":" predicate NEWLINE)+ DEDENT)?
            DEDENT
        At least one of EXPECTS / EXPECTS_NOT must be present.
        """
        kw = self.expect(TokenType.KEYWORD, "TEST")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        flow_name: str | None = None
        with_kwargs: tuple[tuple[str, object], ...] = ()
        expects: tuple[tuple[str, Predicate], ...] = ()
        expects_not: tuple[tuple[str, Predicate], ...] = ()
        seen: set[str] = set()

        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type != TokenType.KEYWORD:
                raise ParseError(
                    f"unexpected {t.type.value} {t.value!r} in TEST body",
                    t.line, t.col,
                )
            if t.value in seen:
                raise ParseError(
                    f"TEST {ident.value} has duplicate {t.value} section",
                    t.line, t.col,
                )
            seen.add(t.value)
            if t.value == "FLOW":
                self.advance()
                self.expect(TokenType.COLON)
                name_tok = self.expect(TokenType.IDENT)
                flow_name = name_tok.value
                self.expect(TokenType.NEWLINE)
            elif t.value == "WITH":
                self.advance()
                self.expect(TokenType.COLON)
                with_kwargs = self._parse_test_kwargs_block()
            elif t.value == "EXPECTS":
                self.advance()
                self.expect(TokenType.COLON)
                expects = self._parse_test_predicate_block()
            elif t.value == "EXPECTS_NOT":
                self.advance()
                self.expect(TokenType.COLON)
                expects_not = self._parse_test_predicate_block()
            else:
                raise ParseError(
                    f"unexpected TEST field {t.value!r} "
                    f"(expected FLOW / WITH / EXPECTS / EXPECTS_NOT)",
                    t.line, t.col,
                )

        self.expect(TokenType.DEDENT)

        if flow_name is None:
            raise ParseError(
                f"TEST {ident.value} is missing required FLOW field",
                kw.line, kw.col,
            )
        if not expects and not expects_not:
            raise ParseError(
                f"TEST {ident.value} requires at least one EXPECTS or EXPECTS_NOT entry",
                kw.line, kw.col,
            )

        return TestDecl(
            name=ident.value,
            flow_name=flow_name,
            with_kwargs=with_kwargs,
            expects=expects,
            expects_not=expects_not,
            line=kw.line, col=kw.col,
        )

    def _parse_test_kwargs_block(self) -> tuple[tuple[str, object], ...]:
        """Parse WITH: <indented kwarg: literal lines>. Empty block ok."""
        self.expect(TokenType.NEWLINE)
        if self.peek().type != TokenType.INDENT:
            return ()
        self.expect(TokenType.INDENT)
        out: list[tuple[str, object]] = []
        while self.peek().type != TokenType.DEDENT:
            name_tok = self.expect(TokenType.IDENT)
            self.expect(TokenType.COLON)
            value = self._parse_literal()
            self.expect(TokenType.NEWLINE)
            out.append((name_tok.value, value))
        self.expect(TokenType.DEDENT)
        return tuple(out)

    def _parse_test_predicate_block(self) -> tuple[tuple[str, Predicate], ...]:
        """Parse EXPECTS: / EXPECTS_NOT: <indented field: predicate lines>."""
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        out: list[tuple[str, Predicate]] = []
        while self.peek().type != TokenType.DEDENT:
            name_tok = self.expect(TokenType.IDENT)
            self.expect(TokenType.COLON)
            pred = self._parse_predicate()
            self.expect(TokenType.NEWLINE)
            out.append((name_tok.value, pred))
        self.expect(TokenType.DEDENT)
        if not out:
            raise ParseError("predicate block cannot be empty",
                             self.peek().line, self.peek().col)
        return tuple(out)

    def _parse_literal(self) -> object:
        """Parse a single literal: string, number, or bool."""
        t = self.peek()
        if t.type == TokenType.STRING:
            self.advance()
            return t.value
        if t.type == TokenType.NUMBER:
            self.advance()
            return float(t.value) if "." in t.value else int(t.value)
        if t.type == TokenType.IDENT and t.value in {"true", "false"}:
            self.advance()
            return t.value == "true"
        raise ParseError(
            f"expected literal (string, number, true/false), got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def _parse_predicate(self) -> Predicate:
        """Parse a TEST predicate. See `Predicate` in ast_nodes for kinds.

        Inline grammar (token-level):
            "not_empty" | "empty"
            | OP_EQ literal | OP_NE literal
            | OP_GT NUMBER  | OP_GE NUMBER  | OP_LT NUMBER | OP_LE NUMBER
            | "contains" literal
        """
        t = self.peek()
        # Bare-ident predicates (not in keyword set to avoid global pollution).
        if t.type == TokenType.IDENT and t.value in {"not_empty", "empty"}:
            self.advance()
            return Predicate(kind=t.value)
        if t.type == TokenType.IDENT and t.value == "contains":
            self.advance()
            return Predicate(kind="contains", value=self._parse_literal())
        op_map = {
            TokenType.OP_EQ: "eq", TokenType.OP_NE: "ne",
            TokenType.RANGLE: "gt", TokenType.OP_GE: "ge",
            TokenType.LANGLE: "lt", TokenType.OP_LE: "le",
        }
        if t.type in op_map:
            self.advance()
            kind = op_map[t.type]
            value: object
            if kind in {"gt", "ge", "lt", "le"}:
                num_tok = self.expect(TokenType.NUMBER)
                value = float(num_tok.value) if "." in num_tok.value else int(num_tok.value)
            else:
                value = self._parse_literal()
            return Predicate(kind=kind, value=value)
        raise ParseError(
            f"expected predicate (not_empty / empty / == … / != … / > … / >= … / "
            f"< … / <= … / contains …), got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def parse_import_decl(self) -> ImportDecl:
        """Parse a top-level FROM "<path>" IMPORT <item>, <item>, ... line.

        Grammar:
          FROM STRING_LIT IMPORT IDENT [AS IDENT] ("," IDENT [AS IDENT])* NEWLINE
        """
        tok_from = self.expect(TokenType.KEYWORD, "FROM")
        path_tok = self.expect(TokenType.STRING)
        path = path_tok.value
        if not (path.startswith("./") or path.startswith("../")):
            raise ParseError(
                f"path must start with './' or '../', got {path!r}",
                path_tok.line, path_tok.col,
            )
        if not path.endswith(".clio"):
            raise ParseError(
                f"path must end with '.clio', got {path!r}",
                path_tok.line, path_tok.col,
            )
        self.expect(TokenType.KEYWORD, "IMPORT")
        items: list[ImportItem] = []
        seen_names: set[str] = set()
        while True:
            if self.peek().type in (TokenType.NEWLINE, TokenType.EOF):
                if not items:
                    raise ParseError(
                        "expected at least one symbol after IMPORT",
                        tok_from.line, tok_from.col,
                    )
                break
            name_tok = self.expect(TokenType.IDENT)
            alias: str | None = None
            if self.peek().type == TokenType.KEYWORD and self.peek().value == "AS":
                self.advance()  # consume AS
                if self.peek().type != TokenType.IDENT:
                    raise ParseError(
                        "expected identifier after AS",
                        self.peek().line, self.peek().col,
                    )
                alias_tok = self.advance()
                alias = alias_tok.value
            if name_tok.value in seen_names:
                raise ParseError(
                    f"duplicate symbol {name_tok.value!r} in same IMPORT statement",
                    name_tok.line, name_tok.col,
                )
            seen_names.add(name_tok.value)
            items.append(ImportItem(
                name=name_tok.value, alias=alias,
                line=name_tok.line, col=name_tok.col,
            ))
            if self.peek().type == TokenType.COMMA:
                self.advance()
                continue
            break
        self.skip_newlines()
        return ImportDecl(
            path=path, items=tuple(items),
            line=tok_from.line, col=tok_from.col,
        )

    def parse_resources(self) -> ResourcesDecl:
        kw = self.expect(TokenType.KEYWORD, "RESOURCES")
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        target: str | None = None
        models: tuple[str, ...] = ()
        mcp_servers: tuple[McpServerSpec, ...] = ()
        databases: tuple[DatabaseSpec, ...] = ()
        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "target":
                self.advance()
                self.expect(TokenType.COLON)
                value_tok = self.expect(TokenType.KEYWORD)
                if value_tok.value not in {"claude-cli", "python", "mcp-server", "langgraph", "claude-skill", "go"}:
                    raise ParseError(
                        f"target {value_tok.value!r} is not supported "
                        "(valid targets: claude-cli, python, mcp-server, langgraph, claude-skill, go)",
                        value_tok.line, value_tok.col,
                    )
                target = value_tok.value
                self.expect(TokenType.NEWLINE)
            elif t.type == TokenType.KEYWORD and t.value == "models":
                self.advance()
                self.expect(TokenType.COLON)
                self.expect(TokenType.LBRACKET)
                vals: list[str] = []
                vals.append(self.expect(TokenType.KEYWORD).value)
                while self.peek().type == TokenType.COMMA:
                    self.advance()
                    vals.append(self.expect(TokenType.KEYWORD).value)
                self.expect(TokenType.RBRACKET)
                models = tuple(vals)
                self.expect(TokenType.NEWLINE)
            elif t.type == TokenType.KEYWORD and t.value == "mcp_servers":
                self.advance()
                self.expect(TokenType.COLON)
                mcp_servers = self._parse_mcp_servers_block()
            elif t.type == TokenType.KEYWORD and t.value == "databases":
                self.advance()
                self.expect(TokenType.COLON)
                databases = self._parse_databases_block()
            elif t.type == TokenType.KEYWORD and t.value in {"budget", "prefer", "strategy"}:
                raise ParseError(
                    f"RESOURCES field {t.value!r} is not supported in v0.1 "
                    f"(planned for a later milestone)",
                    t.line, t.col,
                )
            else:
                raise ParseError(
                    f"unexpected RESOURCES field {t.value!r}",
                    t.line, t.col,
                )
        self.expect(TokenType.DEDENT)

        if target is None:
            raise ParseError("RESOURCES is missing required `target` field", kw.line, kw.col)
        # `models:` is only meaningful for the claude-cli target (it drives the
        # haiku→sonnet→opus escalation chain). Python, mcp-server, and langgraph
        # targets take per-step model overrides via invoke.api.model, so `models:`
        # is optional for them.
        if target == "claude-cli" and not models:
            raise ParseError(
                "RESOURCES with target: claude-cli requires a `models` field",
                kw.line, kw.col,
            )

        return ResourcesDecl(
            target=target,
            models=models,
            mcp_servers=mcp_servers,
            databases=databases,
            line=kw.line,
            col=kw.col,
        )

    def parse_step(self) -> StepDecl:
        kw = self.expect(TokenType.KEYWORD, "STEP")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        if self.peek().type != TokenType.INDENT:
            raise ParseError(
                f"STEP {ident.value} is missing required MODE field",
                kw.line, kw.col,
            )
        self.expect(TokenType.INDENT)

        takes: tuple[Field, ...] = ()
        gives: Field | None = None
        mode: str | None = None
        cache: CacheConfig | None = None
        on_fail: OnFailChain | None = None
        lang: str | None = None
        lang_line: int = 0
        lang_col: int = 0
        impl: ImplBlock | None = None
        invoke: InvokeBlock | None = None
        description: str | None = None
        strategies: str | None = None

        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type != TokenType.KEYWORD:
                raise ParseError(f"unexpected {t.type.value} {t.value!r}", t.line, t.col)

            if t.value == "TAKES":
                if takes:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate TAKES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                takes = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            elif t.value == "GIVES":
                if gives is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate GIVES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                fields = self.parse_field_list()
                if len(fields) != 1:
                    raise ParseError("GIVES must declare exactly one field", t.line, t.col)
                gives = fields[0]
                self.expect(TokenType.NEWLINE)
            elif t.value == "MODE":
                if mode is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate MODE field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                value_tok = self.expect(TokenType.KEYWORD)
                if value_tok.value not in _VALID_MODES:
                    raise ParseError(
                        f"unknown MODE {value_tok.value!r}, expected one of {sorted(_VALID_MODES)}",
                        value_tok.line, value_tok.col,
                    )
                mode = value_tok.value
                self.expect(TokenType.NEWLINE)
            elif t.value == "CACHE":
                if cache is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate CACHE field", t.line, t.col,
                    )
                cache = self.parse_cache(t.line, t.col)
            elif t.value == "ON_FAIL":
                if on_fail is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate ON_FAIL field", t.line, t.col,
                    )
                on_fail = self.parse_on_fail(t.line, t.col)
            elif t.value == "LANG":
                if lang is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate LANG field", t.line, t.col,
                    )
                lang_line, lang_col = t.line, t.col
                self.advance()
                self.expect(TokenType.COLON)
                value_tok = self.expect(TokenType.KEYWORD)
                if value_tok.value not in _VALID_LANGS:
                    raise ParseError(
                        f"unknown LANG {value_tok.value!r}, expected one of {sorted(_VALID_LANGS)}",
                        value_tok.line, value_tok.col,
                    )
                lang = value_tok.value
                self.expect(TokenType.NEWLINE)
            elif t.value == "impl":
                if impl is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate impl field", t.line, t.col,
                    )
                impl = self.parse_impl_block(t.line, t.col)
            elif t.value == "invoke":
                if invoke is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate invoke field", t.line, t.col,
                    )
                invoke = self.parse_invoke_block(t.line, t.col)
            elif t.value == "DESCRIPTION":
                if description is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate DESCRIPTION field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                description = self._parse_text_scalar(t.line, t.col, "DESCRIPTION")
            elif t.value == "STRATEGIES":
                if strategies is not None:
                    raise ParseError(
                        f"STEP {ident.value} has duplicate STRATEGIES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                strategies = self._parse_text_scalar(t.line, t.col, "STRATEGIES")
            else:
                raise ParseError(f"unexpected step field {t.value!r}", t.line, t.col)

        self.expect(TokenType.DEDENT)
        if mode is None:
            raise ParseError(
                f"STEP {ident.value} is missing required MODE field", kw.line, kw.col,
            )
        if cache is not None and mode != "judgment":
            raise ParseError(
                f"'CACHE' is only supported on judgment steps in v0.2 (got mode {mode!r})",
                cache.line, cache.col,
            )
        if on_fail is not None and mode != "judgment":
            raise ParseError(
                f"'ON_FAIL' is only supported on judgment steps in v0.2 (got mode {mode!r})",
                on_fail.line, on_fail.col,
            )
        if lang is not None and mode != "exact":
            raise ParseError(
                f"'LANG' is only supported on exact steps (got mode {mode!r})",
                lang_line, lang_col,
            )
        if impl is not None and mode != "exact":
            raise ParseError(
                f"'impl' is only supported on exact steps (got mode {mode!r})",
                impl.line, impl.col,
            )
        if invoke is not None and mode != "judgment":
            raise ParseError(
                f"'invoke' is only supported on judgment steps (got mode {mode!r})",
                invoke.line, invoke.col,
            )

        return StepDecl(
            name=ident.value, mode=mode, takes=takes, gives=gives,
            cache=cache, on_fail=on_fail, lang=lang, impl=impl, invoke=invoke,
            line=kw.line, col=kw.col,
            description=description, strategies=strategies,
        )

    def _parse_text_scalar(self, line: int, col: int, field: str) -> str:
        """Parse a free-text value: either a quoted STRING or a `|` block scalar.

        Returns the text with leading/trailing whitespace stripped. Used by
        DESCRIPTION and STRATEGIES on STEP. Anything else is a parse error
        with a useful hint."""
        t = self.peek()
        if t.type == TokenType.STRING:
            self.advance()
            self.expect(TokenType.NEWLINE)
            return t.value.strip()
        if t.type == TokenType.BLOCK_SCALAR:
            self.advance()
            self.expect(TokenType.NEWLINE)
            return t.value.strip()
        raise ParseError(
            f"{field} expects a quoted string or `|` block scalar",
            line, col,
        )

    def parse_cache(self, line: int, col: int) -> CacheConfig:
        self.expect(TokenType.KEYWORD, "CACHE")
        self.expect(TokenType.COLON)
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value == "on":
            self.advance()
            self.expect(TokenType.NEWLINE)
            return CacheConfig(mode="on", ttl_seconds=None, line=line, col=col)
        if t.type == TokenType.KEYWORD and t.value == "off":
            self.advance()
            self.expect(TokenType.NEWLINE)
            return CacheConfig(mode="off", ttl_seconds=None, line=line, col=col)
        if t.type == TokenType.KEYWORD and t.value == "ttl":
            self.advance()
            self.expect(TokenType.LPAREN)
            dur_tok = self.expect(TokenType.DURATION)
            self.expect(TokenType.RPAREN)
            self.expect(TokenType.NEWLINE)
            return CacheConfig(
                mode="ttl",
                ttl_seconds=_duration_to_seconds(dur_tok.value),
                line=line, col=col,
            )
        raise ParseError(
            f"expected CACHE value (on | off | ttl(<dur>)), got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def parse_impl_block(self, line: int, col: int) -> ImplBlock:
        """Parse an indented `impl:` block. Dispatches on `mode:` to CodeImpl or RestImpl."""
        self.expect(TokenType.KEYWORD, "impl")
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        mode_value: str | None = None
        mode_line, mode_col = line, col
        fields: dict[str, tuple[object, int, int]] = {}

        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            # Field names accepted as either IDENT or KEYWORD — keeps the
            # surface syntax flexible without polluting the global keyword set.
            if t.type not in (TokenType.IDENT, TokenType.KEYWORD):
                raise ParseError(
                    f"unexpected impl block field {t.type.value} {t.value!r}",
                    t.line, t.col,
                )
            field_name = t.value
            if field_name == "mode":
                if mode_value is not None:
                    raise ParseError(
                        "impl block has duplicate mode field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                v = self.expect(TokenType.KEYWORD)
                if v.value not in _VALID_IMPL_MODES:
                    raise ParseError(
                        f"unknown impl.mode {v.value!r}, "
                        f"expected one of {sorted(_VALID_IMPL_MODES)}",
                        v.line, v.col,
                    )
                mode_value = v.value
                mode_line, mode_col = v.line, v.col
                self.expect(TokenType.NEWLINE)
            else:
                if field_name in fields:
                    raise ParseError(
                        f"impl block has duplicate field {field_name!r}",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                value = self._parse_impl_field_value()
                fields[field_name] = (value, t.line, t.col)
                self.expect(TokenType.NEWLINE)

        self.expect(TokenType.DEDENT)

        if mode_value is None:
            raise ParseError("impl block is missing required 'mode' field", line, col)

        if mode_value == "code":
            return self._build_code_impl(fields, line, col)
        if mode_value == "rest":
            return self._build_rest_impl(fields, line, col, mode_line, mode_col)
        if mode_value == "shell":
            return self._build_shell_impl(fields, line, col, mode_line, mode_col)
        if mode_value == "mcp_tool":
            return self._build_mcp_tool_impl(fields, line, col, mode_line, mode_col)
        if mode_value == "sql":
            return self._build_sql_impl(fields, line, col, mode_line, mode_col)
        # unreachable: mode validation happened above
        raise ParseError(f"impl.mode {mode_value!r} not yet implemented", mode_line, mode_col)

    def _parse_impl_field_value(self) -> object:
        """Top-level impl field value: scalar (string-preserving), inline
        dict/list, or a `|` block scalar (multi-line raw text, used for
        `impl.sql.query`). Bareword identifiers like `parse: none` keep
        their string value here; bool/null literals are only resolved
        inside inline dicts/lists, where JSON-style typing is expected."""
        t = self.peek()
        if t.type == TokenType.BLOCK_SCALAR:
            self.advance()
            return t.value
        if t.type == TokenType.LBRACE:
            return self._parse_inline_dict()
        if t.type == TokenType.LBRACKET:
            return self._parse_inline_list()
        if t.type == TokenType.STRING:
            self.advance()
            return t.value
        if t.type == TokenType.NUMBER:
            self.advance()
            return float(t.value) if "." in t.value or "e" in t.value.lower() else int(t.value)
        if t.type == TokenType.DURATION:
            self.advance()
            return _duration_to_seconds(t.value)
        if t.type == TokenType.KEYWORD or t.type == TokenType.IDENT:
            self.advance()
            return t.value
        raise ParseError(
            f"expected impl field value, got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def _parse_dict_value(self) -> object:
        """Value inside an inline dict/list — JSON-style typing: bareword
        `true`/`false` become bools, `null`/`none` become None."""
        t = self.peek()
        if t.type == TokenType.LBRACE:
            return self._parse_inline_dict()
        if t.type == TokenType.LBRACKET:
            return self._parse_inline_list()
        if t.type == TokenType.STRING:
            self.advance()
            return t.value
        if t.type == TokenType.NUMBER:
            self.advance()
            return float(t.value) if "." in t.value or "e" in t.value.lower() else int(t.value)
        if t.type == TokenType.DURATION:
            self.advance()
            return _duration_to_seconds(t.value)
        if t.type == TokenType.KEYWORD or t.type == TokenType.IDENT:
            self.advance()
            if t.value == "true":
                return True
            if t.value == "false":
                return False
            if t.value in ("null", "none"):
                return None
            return t.value
        raise ParseError(
            f"expected inline dict/list value, got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def _parse_inline_dict(self) -> dict[str, object]:
        """{ key: value, key: value, ... } — keys are IDENT/KEYWORD, values
        are `_parse_dict_value`. Empty dict allowed."""
        open_brace = self.expect(TokenType.LBRACE)
        result: dict[str, object] = {}
        if self.peek().type == TokenType.RBRACE:
            self.advance()
            return result
        while True:
            key_tok = self.peek()
            # Bareword keys (IDENT/KEYWORD) cover the common case; quoted-string
            # keys let users write headers with non-identifier characters
            # (`Content-Type`, `X-Forwarded-For`, etc.).
            if key_tok.type not in (TokenType.IDENT, TokenType.KEYWORD, TokenType.STRING):
                raise ParseError(
                    f"expected inline dict key, got {key_tok.type.value} {key_tok.value!r}",
                    key_tok.line, key_tok.col,
                )
            if key_tok.value in result:
                raise ParseError(
                    f"duplicate key {key_tok.value!r} in inline dict",
                    key_tok.line, key_tok.col,
                )
            self.advance()
            self.expect(TokenType.COLON)
            result[key_tok.value] = self._parse_dict_value()
            if self.peek().type == TokenType.COMMA:
                self.advance()
                continue
            break
        if self.peek().type != TokenType.RBRACE:
            t = self.peek()
            raise ParseError(
                f"expected ',' or '}}' in inline dict opened at "
                f"{open_brace.line}:{open_brace.col}, got {t.type.value} {t.value!r}",
                t.line, t.col,
            )
        self.advance()
        return result

    def _parse_inline_list(self) -> list[object]:
        """[ value, value, ... ] — values are `_parse_dict_value`."""
        open_bracket = self.expect(TokenType.LBRACKET)
        result: list[object] = []
        if self.peek().type == TokenType.RBRACKET:
            self.advance()
            return result
        while True:
            result.append(self._parse_dict_value())
            if self.peek().type == TokenType.COMMA:
                self.advance()
                continue
            break
        if self.peek().type != TokenType.RBRACKET:
            t = self.peek()
            raise ParseError(
                f"expected ',' or ']' in inline list opened at "
                f"{open_bracket.line}:{open_bracket.col}, got {t.type.value} {t.value!r}",
                t.line, t.col,
            )
        self.advance()
        return result

    def _build_code_impl(
        self, fields: dict[str, tuple[object, int, int]], line: int, col: int,
    ) -> CodeImpl:
        allowed = {"lang"}
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for impl.mode: code "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        lang = None
        if "lang" in fields:
            lang_value, fline, fcol = fields["lang"]
            if not isinstance(lang_value, str) or lang_value not in _VALID_LANGS:
                raise ParseError(
                    f"unknown impl.lang {lang_value!r}, "
                    f"expected one of {sorted(_VALID_LANGS)}",
                    fline, fcol,
                )
            lang = lang_value
        return CodeImpl(line=line, col=col, lang=lang)

    def _build_shell_impl(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> ShellImpl:
        allowed = {"cmd", "timeout", "parse"}
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for impl.mode: shell "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        if "cmd" not in fields:
            raise ParseError(
                "impl.mode: shell requires 'cmd' (a quoted string, e.g. \"pdftotext ${file} -\")",
                mode_line, mode_col,
            )
        cmd_value, cline, ccol = fields["cmd"]
        if not isinstance(cmd_value, str):
            raise ParseError(
                f"impl.cmd must be a quoted string, got {type(cmd_value).__name__}",
                cline, ccol,
            )

        timeout_seconds = None
        if "timeout" in fields:
            to, tline, tcol = fields["timeout"]
            if not isinstance(to, int):
                raise ParseError(
                    f"impl.timeout must be a duration (e.g. 30s, 2m), got {to!r}",
                    tline, tcol,
                )
            timeout_seconds = to

        parse_value = "none"
        if "parse" in fields:
            pv, pline, pcol = fields["parse"]
            if not isinstance(pv, str) or pv not in _VALID_PARSE_MODES:
                raise ParseError(
                    f"unknown impl.parse {pv!r}, expected one of {sorted(_VALID_PARSE_MODES)}",
                    pline, pcol,
                )
            parse_value = pv

        return ShellImpl(
            line=line, col=col,
            cmd=cmd_value,
            timeout_seconds=timeout_seconds,
            parse=parse_value,
        )

    def _build_rest_impl(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> RestImpl:
        allowed = {
            "method", "url", "query", "headers", "body",
            "response_path", "timeout", "retry",
        }
        # Reject the legacy scalar form upfront, with a clear migration hint.
        if "retries" in fields:
            _, fline, fcol = fields["retries"]
            raise ParseError(
                "impl.retries (scalar) is no longer accepted; use "
                "`retry: {attempts: N}` instead — see LANGUAGE_SPEC.md "
                "§impl.mode: rest / retry",
                fline, fcol,
            )
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for impl.mode: rest "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        if "method" not in fields:
            raise ParseError("impl.mode: rest requires 'method'", mode_line, mode_col)
        if "url" not in fields:
            raise ParseError("impl.mode: rest requires 'url'", mode_line, mode_col)

        method, mline, mcol = fields["method"]
        if not isinstance(method, str) or method not in _VALID_HTTP_METHODS:
            raise ParseError(
                f"unknown HTTP method {method!r}, "
                f"expected one of {sorted(_VALID_HTTP_METHODS)}",
                mline, mcol,
            )

        url, uline, ucol = fields["url"]
        if not isinstance(url, str):
            raise ParseError(
                f"impl.url must be a string, got {type(url).__name__}",
                uline, ucol,
            )

        query = None
        if "query" in fields:
            qv, qline, qcol = fields["query"]
            query = self._build_rest_scalar_dict("impl.query", qv, qline, qcol)

        headers = None
        if "headers" in fields:
            hv, hline, hcol = fields["headers"]
            headers = self._build_rest_headers_dict(hv, hline, hcol)

        body = None
        if "body" in fields:
            bv, bline, bcol = fields["body"]
            if method == "GET":
                raise ParseError(
                    "impl.body is not allowed on GET — use impl.query instead",
                    bline, bcol,
                )
            body = self._build_rest_body(bv, bline, bcol)

        response_path = None
        if "response_path" in fields:
            rp, rline, rcol = fields["response_path"]
            if not isinstance(rp, str):
                raise ParseError(
                    f"impl.response_path must be a string, got {type(rp).__name__}",
                    rline, rcol,
                )
            response_path = rp

        timeout_seconds = None
        if "timeout" in fields:
            to, tline, tcol = fields["timeout"]
            if not isinstance(to, int):
                raise ParseError(
                    f"impl.timeout must be a duration (e.g. 30s, 2m), got {to!r}",
                    tline, tcol,
                )
            timeout_seconds = to

        retry = None
        if "retry" in fields:
            rv, rline, rcol = fields["retry"]
            retry = self._build_retry_policy(rv, rline, rcol)

        return RestImpl(
            line=line, col=col,
            method=method, url=url,
            query=query,
            headers=headers,
            body=body,
            response_path=response_path,
            timeout_seconds=timeout_seconds,
            retry=retry,
        )

    def _build_rest_scalar_dict(
        self, field_name: str, value: object, line: int, col: int,
    ) -> tuple[tuple[str, str | int | float | bool | None], ...]:
        """Validate that `value` is a flat dict of scalar values
        (str | int | float | bool | None). Returns a stable-ordered tuple."""
        if not isinstance(value, dict):
            raise ParseError(
                f"{field_name} must be an inline dict {{key: value, ...}}, "
                f"got {type(value).__name__}",
                line, col,
            )
        out: list[tuple[str, str | int | float | bool | None]] = []
        for k, v in value.items():
            if not isinstance(v, (str, int, float, bool)) and v is not None:
                raise ParseError(
                    f"{field_name}.{k} must be a scalar (string, number, bool, null), "
                    f"got {type(v).__name__}",
                    line, col,
                )
            out.append((k, v))
        return tuple(out)

    def _build_rest_headers_dict(
        self, value: object, line: int, col: int,
    ) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, dict):
            raise ParseError(
                f"impl.headers must be an inline dict, got {type(value).__name__}",
                line, col,
            )
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            if not isinstance(v, str):
                raise ParseError(
                    f"impl.headers.{k} must be a string "
                    f"(quote it: \"{v}\" if needed), got {type(v).__name__}",
                    line, col,
                )
            out.append((k, v))
        return tuple(out)

    def _build_rest_body(
        self, value: object, line: int, col: int,
    ) -> RestBody:
        # Form 2 — raw string  /  Form 3 — file (`@./path`)
        if isinstance(value, str):
            if value.startswith("@"):
                path = value[1:]
                if not path:
                    raise ParseError(
                        "impl.body file reference must specify a path after `@`",
                        line, col,
                    )
                return FileBody(path=path)
            return RawBody(template=value)

        # Forms 1 / 4 / 5 — dict-shaped
        if isinstance(value, dict):
            keys = set(value.keys())
            if keys == {"form"}:
                inner = value["form"]
                return FormBody(fields=self._build_rest_form_dict("impl.body.form", inner, line, col))
            if keys == {"multipart"}:
                inner = value["multipart"]
                return MultipartBody(fields=self._build_rest_form_dict(
                    "impl.body.multipart", inner, line, col, allow_at_prefix=True))
            if keys & {"form", "multipart"}:
                raise ParseError(
                    "impl.body cannot combine 'form' and 'multipart' (or other top-level keys); "
                    "pick one form — see LANGUAGE_SPEC.md §impl.mode: rest / body",
                    line, col,
                )
            # Otherwise it's a JSON dict — flat scalars only in v1.
            jfields: list[tuple[str, str | int | float | bool | None]] = []
            for k, v in value.items():
                if not isinstance(v, (str, int, float, bool)) and v is not None:
                    raise ParseError(
                        f"impl.body JSON dict supports flat scalar values in v1; "
                        f"field {k!r} is {type(v).__name__}",
                        line, col,
                    )
                jfields.append((k, v))
            return JsonBody(fields=tuple(jfields))

        raise ParseError(
            f"impl.body must be a string, an inline dict, or `\"@./file\"`; "
            f"got {type(value).__name__}",
            line, col,
        )

    def _build_rest_form_dict(
        self, field_name: str, value: object, line: int, col: int,
        allow_at_prefix: bool = False,
    ) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, dict):
            raise ParseError(
                f"{field_name} must be an inline dict, got {type(value).__name__}",
                line, col,
            )
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            if not isinstance(v, str):
                raise ParseError(
                    f"{field_name}.{k} must be a string, got {type(v).__name__}",
                    line, col,
                )
            if v.startswith("@") and not allow_at_prefix:
                raise ParseError(
                    f"{field_name}.{k}: file uploads (@./path) are only "
                    "allowed inside multipart bodies",
                    line, col,
                )
            out.append((k, v))
        return tuple(out)

    def _build_retry_policy(
        self, value: object, line: int, col: int,
    ) -> RetryPolicy:
        if not isinstance(value, dict):
            raise ParseError(
                f"impl.retry must be an inline dict {{attempts: N, ...}}, "
                f"got {type(value).__name__}",
                line, col,
            )
        allowed = {"attempts", "backoff", "base", "cap", "on"}
        unknown = set(value.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            raise ParseError(
                f"unknown field {sample!r} in impl.retry "
                f"(allowed: {sorted(allowed)})",
                line, col,
            )
        if "attempts" not in value:
            raise ParseError(
                "impl.retry requires 'attempts' (e.g. retry: {attempts: 3})",
                line, col,
            )
        attempts = value["attempts"]
        if not isinstance(attempts, int) or attempts < 1:
            raise ParseError(
                f"impl.retry.attempts must be a positive integer, got {attempts!r}",
                line, col,
            )
        backoff = value.get("backoff", "exponential")
        if backoff not in ("exponential", "constant"):
            raise ParseError(
                f"impl.retry.backoff must be 'exponential' or 'constant', got {backoff!r}",
                line, col,
            )
        base_raw = value.get("base", 0.1)
        if not isinstance(base_raw, (int, float)) or base_raw <= 0:
            raise ParseError(
                f"impl.retry.base must be a positive number, got {base_raw!r}",
                line, col,
            )
        cap_raw = value.get("cap", 30.0)
        if not isinstance(cap_raw, (int, float)) or cap_raw <= 0:
            raise ParseError(
                f"impl.retry.cap must be a positive number, got {cap_raw!r}",
                line, col,
            )
        on_value: tuple[str, ...]
        if "on" in value:
            on_raw = value["on"]
            if not isinstance(on_raw, list) or not all(isinstance(x, str) for x in on_raw):
                raise ParseError(
                    f"impl.retry.on must be a list of strings, got {on_raw!r}",
                    line, col,
                )
            valid = {"5xx", "429", "timeout", "network"}
            for x in on_raw:
                if x not in valid:
                    raise ParseError(
                        f"impl.retry.on contains unknown token {x!r}; "
                        f"allowed: {sorted(valid)}",
                        line, col,
                    )
            on_value = tuple(on_raw)
        else:
            on_value = ("5xx", "429", "timeout")
        return RetryPolicy(
            attempts=attempts,
            backoff=backoff,
            base=float(base_raw),
            cap=float(cap_raw),
            on=on_value,
        )

    # ---- mcp_tool / mcp_servers builders -----------------------------------

    def _parse_mcp_servers_block(self) -> tuple[McpServerSpec, ...]:
        """Parse the indented `mcp_servers:` block. Each entry is a
        `<name>:` header followed by an indented field block (transport /
        command / args / env / url / headers)."""
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        out: list[McpServerSpec] = []
        seen: set[str] = set()
        while self.peek().type != TokenType.DEDENT:
            name_tok = self.peek()
            if name_tok.type not in (TokenType.IDENT, TokenType.KEYWORD):
                raise ParseError(
                    f"expected MCP server name, got {name_tok.type.value} "
                    f"{name_tok.value!r}",
                    name_tok.line, name_tok.col,
                )
            if name_tok.value in seen:
                raise ParseError(
                    f"RESOURCES.mcp_servers has duplicate server name "
                    f"{name_tok.value!r}",
                    name_tok.line, name_tok.col,
                )
            seen.add(name_tok.value)
            self.advance()
            self.expect(TokenType.COLON)
            self.expect(TokenType.NEWLINE)
            self.expect(TokenType.INDENT)
            spec_fields: dict[str, object] = {}
            while self.peek().type != TokenType.DEDENT:
                field_tok = self.peek()
                if field_tok.type not in (TokenType.IDENT, TokenType.KEYWORD):
                    raise ParseError(
                        f"expected MCP server spec field, got "
                        f"{field_tok.type.value} {field_tok.value!r}",
                        field_tok.line, field_tok.col,
                    )
                field_name = field_tok.value
                if field_name in spec_fields:
                    raise ParseError(
                        f"RESOURCES.mcp_servers.{name_tok.value} has duplicate "
                        f"field {field_name!r}",
                        field_tok.line, field_tok.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                spec_fields[field_name] = self._parse_impl_field_value()
                self.expect(TokenType.NEWLINE)
            self.expect(TokenType.DEDENT)
            out.append(self._build_mcp_server_spec(
                name_tok.value, spec_fields, name_tok.line, name_tok.col,
            ))
        self.expect(TokenType.DEDENT)
        return tuple(out)

    def _build_mcp_server_spec(
        self, name: str, spec: object, line: int, col: int,
    ) -> McpServerSpec:
        if not isinstance(spec, dict):
            raise ParseError(
                f"RESOURCES.mcp_servers.{name} must be an inline dict, "
                f"got {type(spec).__name__}",
                line, col,
            )
        transport = spec.get("transport", "stdio")
        if transport not in _VALID_MCP_TRANSPORTS:
            raise ParseError(
                f"RESOURCES.mcp_servers.{name}.transport must be one of "
                f"{sorted(_VALID_MCP_TRANSPORTS)}, got {transport!r}",
                line, col,
            )

        stdio_keys = {"command", "args", "env"}
        net_keys = {"url", "headers"}
        provided = set(spec.keys()) - {"transport"}
        unknown = provided - stdio_keys - net_keys
        if unknown:
            sample = sorted(unknown)[0]
            raise ParseError(
                f"RESOURCES.mcp_servers.{name} has unknown field {sample!r} "
                f"(allowed: transport, command, args, env, url, headers)",
                line, col,
            )

        if transport == "stdio":
            forbidden = provided & net_keys
            if forbidden:
                sample = sorted(forbidden)[0]
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name} uses transport: stdio "
                    f"but declares {sample!r} (only valid on sse/http)",
                    line, col,
                )
            if "command" not in spec:
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name} (transport: stdio) is "
                    f"missing required field 'command'",
                    line, col,
                )
            command = spec["command"]
            if not isinstance(command, str) or not command:
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name}.command must be a non-empty string",
                    line, col,
                )
            args_raw = spec.get("args", [])
            if not isinstance(args_raw, list):
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name}.args must be an inline list, "
                    f"got {type(args_raw).__name__}",
                    line, col,
                )
            for a in args_raw:
                if not isinstance(a, str):
                    raise ParseError(
                        f"RESOURCES.mcp_servers.{name}.args entries must be "
                        f"strings, got {type(a).__name__}",
                        line, col,
                    )
            env_raw = spec.get("env", {})
            if not isinstance(env_raw, dict):
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name}.env must be an inline dict, "
                    f"got {type(env_raw).__name__}",
                    line, col,
                )
            env_pairs: list[tuple[str, str]] = []
            for k, v in env_raw.items():
                if not isinstance(v, str):
                    raise ParseError(
                        f"RESOURCES.mcp_servers.{name}.env.{k} must be a string "
                        f"(use \"env:NAME\" to reference the host env)",
                        line, col,
                    )
                env_pairs.append((k, v))
            return StdioServerSpec(
                name=name,
                line=line, col=col,
                command=command,
                args=tuple(args_raw),
                env=tuple(env_pairs),
            )

        # transport: sse | http
        forbidden = provided & stdio_keys
        if forbidden:
            sample = sorted(forbidden)[0]
            raise ParseError(
                f"RESOURCES.mcp_servers.{name} uses transport: {transport} "
                f"but declares {sample!r} (only valid on stdio)",
                line, col,
            )
        if "url" not in spec:
            raise ParseError(
                f"RESOURCES.mcp_servers.{name} (transport: {transport}) is "
                f"missing required field 'url'",
                line, col,
            )
        url = spec["url"]
        if not isinstance(url, str) or not url:
            raise ParseError(
                f"RESOURCES.mcp_servers.{name}.url must be a non-empty string",
                line, col,
            )
        # Use urlparse + hostname check (not startswith) — `http://localhost.x.com`
        # would otherwise pass the prefix check and open SSRF surface.
        parsed = urlparse(url)
        if not (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
        ):
            raise ParseError(
                f"RESOURCES.mcp_servers.{name}.url must be https:// "
                f"(or http:// for localhost / 127.0.0.1), got {url!r}",
                line, col,
            )
        headers_raw = spec.get("headers", {})
        if not isinstance(headers_raw, dict):
            raise ParseError(
                f"RESOURCES.mcp_servers.{name}.headers must be an inline dict, "
                f"got {type(headers_raw).__name__}",
                line, col,
            )
        header_pairs: list[tuple[str, str]] = []
        for k, v in headers_raw.items():
            if not isinstance(v, str):
                raise ParseError(
                    f"RESOURCES.mcp_servers.{name}.headers.{k} must be a string "
                    f"(use \"env:NAME\" for secrets)",
                    line, col,
                )
            header_pairs.append((k, v))
        cls = SseServerSpec if transport == "sse" else HttpServerSpec
        return cls(name=name, line=line, col=col, url=url, headers=tuple(header_pairs))

    def _build_mcp_tool_impl(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> McpToolImpl:
        allowed = {"server", "tool", "args", "timeout", "parse"}
        unknown = set(fields.keys()) - allowed
        if "retry" in fields:
            _, rline, rcol = fields["retry"]
            raise ParseError(
                "impl.mcp_tool does not support 'retry:' in v0.10 — "
                "use a RESCUE handler for retry-then-abort flows "
                "(see LANGUAGE_SPEC.md §RESCUE handler)",
                rline, rcol,
            )
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for impl.mode: mcp_tool "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        if "server" not in fields:
            raise ParseError(
                "impl.mcp_tool is missing required field 'server'",
                mode_line, mode_col,
            )
        if "tool" not in fields:
            raise ParseError(
                "impl.mcp_tool is missing required field 'tool'",
                mode_line, mode_col,
            )

        server, sline, scol = fields["server"]
        if not isinstance(server, str) or not server:
            raise ParseError(
                f"impl.mcp_tool.server must be a non-empty identifier, got {server!r}",
                sline, scol,
            )
        tool, tline, tcol = fields["tool"]
        if not isinstance(tool, str) or not tool:
            raise ParseError(
                f"impl.mcp_tool.tool must be a non-empty identifier, got {tool!r}",
                tline, tcol,
            )

        args_pairs: tuple[tuple[str, object], ...] = ()
        if "args" in fields:
            av, aline, acol = fields["args"]
            if not isinstance(av, dict):
                raise ParseError(
                    f"impl.mcp_tool.args must be an inline dict, got {type(av).__name__}",
                    aline, acol,
                )
            self._validate_mcp_args(av, "impl.mcp_tool.args", aline, acol)
            args_pairs = tuple(av.items())

        timeout_seconds = 60
        if "timeout" in fields:
            to, toline, tocol = fields["timeout"]
            if not isinstance(to, int) or to <= 0:
                raise ParseError(
                    f"impl.mcp_tool.timeout must be a positive duration "
                    f"(e.g. 30s, 2m), got {to!r}",
                    toline, tocol,
                )
            timeout_seconds = to

        parse = "json"
        if "parse" in fields:
            pv, pline, pcol = fields["parse"]
            if pv not in ("json", "text"):
                raise ParseError(
                    f"impl.mcp_tool.parse must be 'json' or 'text', got {pv!r}",
                    pline, pcol,
                )
            parse = pv

        return McpToolImpl(
            line=line, col=col,
            server=server, tool=tool, args=args_pairs,
            timeout_seconds=timeout_seconds, parse=parse,
        )

    # ---- sql / databases builders ------------------------------------------

    def _parse_databases_block(self) -> tuple[DatabaseSpec, ...]:
        """Parse the indented `databases:` block. Each entry is a `<name>:`
        header followed by an indented `driver:` / `url:` block. Mirrors
        `_parse_mcp_servers_block` (same surface, simpler shape — there are
        no per-driver field variations)."""
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        out: list[DatabaseSpec] = []
        seen: set[str] = set()
        while self.peek().type != TokenType.DEDENT:
            name_tok = self.peek()
            if name_tok.type not in (TokenType.IDENT, TokenType.KEYWORD):
                raise ParseError(
                    f"expected database name, got {name_tok.type.value} "
                    f"{name_tok.value!r}",
                    name_tok.line, name_tok.col,
                )
            if name_tok.value in seen:
                raise ParseError(
                    f"RESOURCES.databases has duplicate database name "
                    f"{name_tok.value!r}",
                    name_tok.line, name_tok.col,
                )
            seen.add(name_tok.value)
            self.advance()
            self.expect(TokenType.COLON)
            self.expect(TokenType.NEWLINE)
            self.expect(TokenType.INDENT)
            spec_fields: dict[str, object] = {}
            while self.peek().type != TokenType.DEDENT:
                field_tok = self.peek()
                if field_tok.type not in (TokenType.IDENT, TokenType.KEYWORD):
                    raise ParseError(
                        f"expected database spec field, got "
                        f"{field_tok.type.value} {field_tok.value!r}",
                        field_tok.line, field_tok.col,
                    )
                field_name = field_tok.value
                if field_name in spec_fields:
                    raise ParseError(
                        f"RESOURCES.databases.{name_tok.value} has duplicate "
                        f"field {field_name!r}",
                        field_tok.line, field_tok.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                spec_fields[field_name] = self._parse_impl_field_value()
                self.expect(TokenType.NEWLINE)
            self.expect(TokenType.DEDENT)
            out.append(self._build_database_spec(
                name_tok.value, spec_fields, name_tok.line, name_tok.col,
            ))
        self.expect(TokenType.DEDENT)
        return tuple(out)

    def _build_database_spec(
        self, name: str, spec: dict[str, object], line: int, col: int,
    ) -> DatabaseSpec:
        allowed = {"driver", "url"}
        unknown = set(spec.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            raise ParseError(
                f"RESOURCES.databases.{name} has unknown field {sample!r} "
                f"(allowed: {sorted(allowed)})",
                line, col,
            )
        if "driver" not in spec:
            raise ParseError(
                f"RESOURCES.databases.{name} is missing required field 'driver' "
                f"(one of {sorted(_VALID_SQL_DRIVERS)})",
                line, col,
            )
        if "url" not in spec:
            raise ParseError(
                f"RESOURCES.databases.{name} is missing required field 'url'",
                line, col,
            )
        driver = spec["driver"]
        if not isinstance(driver, str) or driver not in _VALID_SQL_DRIVERS:
            raise ParseError(
                f"RESOURCES.databases.{name}.driver must be one of "
                f"{sorted(_VALID_SQL_DRIVERS)}, got {driver!r}",
                line, col,
            )
        url = spec["url"]
        if not isinstance(url, str) or not url:
            raise ParseError(
                f"RESOURCES.databases.{name}.url must be a non-empty string",
                line, col,
            )
        return DatabaseSpec(name=name, driver=driver, url=url, line=line, col=col)

    def _build_sql_impl(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> SqlImpl:
        allowed = {"db", "query"}
        if "retry" in fields:
            _, rline, rcol = fields["retry"]
            raise ParseError(
                "impl.sql does not support 'retry:' in v0.11 — "
                "use a RESCUE handler for retry-then-abort flows "
                "(see LANGUAGE_SPEC.md §RESCUE handler)",
                rline, rcol,
            )
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for impl.mode: sql "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        if "db" not in fields:
            raise ParseError(
                "impl.sql is missing required field 'db'",
                mode_line, mode_col,
            )
        if "query" not in fields:
            raise ParseError(
                "impl.sql is missing required field 'query'",
                mode_line, mode_col,
            )

        db, dline, dcol = fields["db"]
        if not isinstance(db, str) or not db:
            raise ParseError(
                f"impl.sql.db must be a non-empty identifier, got {db!r}",
                dline, dcol,
            )
        query, qline, qcol = fields["query"]
        if not isinstance(query, str) or not query.strip():
            raise ParseError(
                "impl.sql.query must be a non-empty SQL string "
                "(use a `|` block scalar for multi-line queries)",
                qline, qcol,
            )
        # env: in the query body would be an SQL-injection vector if the
        # env var ever contained untrusted text. Reject at parse time;
        # secrets belong in RESOURCES.databases.<name>.url.
        if "env:" in query:
            raise ParseError(
                "impl.sql.query may not contain 'env:NAME' substitutions — "
                "secrets belong in RESOURCES.databases.<name>.url, not in "
                "query bodies (see LANGUAGE_SPEC.md §impl.mode: sql)",
                qline, qcol,
            )

        return SqlImpl(line=line, col=col, db=db, query=query)

    def _validate_mcp_args(
        self, value: object, path: str, line: int, col: int,
    ) -> None:
        """Recursive walk on tool args. Allowed leaf types: str, int, float,
        bool, None. Dict and list nodes recurse. No env: substitution check
        here — that's a runtime-only concept; the spec disallows env: in
        args but enforcing it statically would block a future relaxation."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, dict):
            for k, v in value.items():
                self._validate_mcp_args(v, f"{path}.{k}", line, col)
            return
        if isinstance(value, list):
            for i, v in enumerate(value):
                self._validate_mcp_args(v, f"{path}[{i}]", line, col)
            return
        raise ParseError(
            f"{path} contains an unsupported value of type {type(value).__name__} "
            f"(allowed: str, int, float, bool, null, dict, list)",
            line, col,
        )

    def parse_invoke_block(self, line: int, col: int) -> InvokeBlock:
        """Parse an indented `invoke:` block. Dispatches on `mode:` to CliInvoke or ApiInvoke."""
        self.expect(TokenType.KEYWORD, "invoke")
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        mode_value: str | None = None
        mode_line, mode_col = line, col
        fields: dict[str, tuple[object, int, int]] = {}

        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type not in (TokenType.IDENT, TokenType.KEYWORD):
                raise ParseError(
                    f"unexpected invoke block field {t.type.value} {t.value!r}",
                    t.line, t.col,
                )
            field_name = t.value
            if field_name == "mode":
                if mode_value is not None:
                    raise ParseError(
                        "invoke block has duplicate mode field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                v = self.expect(TokenType.KEYWORD)
                if v.value not in _VALID_INVOKE_MODES:
                    raise ParseError(
                        f"unknown invoke.mode {v.value!r}, "
                        f"expected one of {sorted(_VALID_INVOKE_MODES)}",
                        v.line, v.col,
                    )
                mode_value = v.value
                mode_line, mode_col = v.line, v.col
                self.expect(TokenType.NEWLINE)
            else:
                if field_name in fields:
                    raise ParseError(
                        f"invoke block has duplicate field {field_name!r}",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                value = self._parse_impl_field_value()
                fields[field_name] = (value, t.line, t.col)
                self.expect(TokenType.NEWLINE)

        self.expect(TokenType.DEDENT)

        if mode_value is None:
            raise ParseError("invoke block is missing required 'mode' field", line, col)

        if mode_value == "cli":
            return self._build_cli_invoke(fields, line, col)
        if mode_value == "api":
            return self._build_api_invoke(fields, line, col, mode_line, mode_col)
        raise ParseError(
            f"invoke.mode {mode_value!r} not yet implemented", mode_line, mode_col,
        )

    def _build_cli_invoke(
        self, fields: dict[str, tuple[object, int, int]], line: int, col: int,
    ) -> CliInvoke:
        allowed = {"cli", "model", "output_format", "max_turns"}
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for invoke.mode: cli "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )

        def _opt_str(name: str) -> str | None:
            if name not in fields:
                return None
            v, fline, fcol = fields[name]
            if not isinstance(v, str):
                raise ParseError(
                    f"invoke.{name} must be a string, got {type(v).__name__}",
                    fline, fcol,
                )
            return v

        max_turns = None
        if "max_turns" in fields:
            v, fline, fcol = fields["max_turns"]
            if not isinstance(v, int):
                raise ParseError(
                    f"invoke.max_turns must be an integer, got {v!r}",
                    fline, fcol,
                )
            max_turns = v

        return CliInvoke(
            line=line, col=col,
            cli=_opt_str("cli"),
            model=_opt_str("model"),
            output_format=_opt_str("output_format"),
            max_turns=max_turns,
        )

    def _build_api_invoke(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> ApiInvoke:
        allowed = {
            "protocol", "model", "base_url", "auth",
            "temperature", "max_tokens", "timeout", "retries",
        }
        unknown = set(fields.keys()) - allowed
        if unknown:
            sample = sorted(unknown)[0]
            _, fline, fcol = fields[sample]
            raise ParseError(
                f"unknown field {sample!r} for invoke.mode: api "
                f"(allowed: {sorted(allowed)})",
                fline, fcol,
            )
        if "protocol" not in fields:
            raise ParseError(
                "invoke.mode: api requires 'protocol'", mode_line, mode_col,
            )
        if "model" not in fields:
            raise ParseError(
                "invoke.mode: api requires 'model'", mode_line, mode_col,
            )

        protocol, pline, pcol = fields["protocol"]
        if not isinstance(protocol, str) or protocol not in _VALID_PROTOCOLS:
            raise ParseError(
                f"unknown invoke.protocol {protocol!r}, "
                f"expected one of {sorted(_VALID_PROTOCOLS)}",
                pline, pcol,
            )

        model, mline, mcol = fields["model"]
        if not isinstance(model, str):
            raise ParseError(
                f"invoke.model must be a string, got {type(model).__name__}",
                mline, mcol,
            )

        def _opt_str(name: str) -> str | None:
            if name not in fields:
                return None
            v, fline, fcol = fields[name]
            if not isinstance(v, str):
                raise ParseError(
                    f"invoke.{name} must be a string, got {type(v).__name__}",
                    fline, fcol,
                )
            return v

        temperature = None
        if "temperature" in fields:
            v, fline, fcol = fields["temperature"]
            if not isinstance(v, (int, float)):
                raise ParseError(
                    f"invoke.temperature must be a number, got {v!r}",
                    fline, fcol,
                )
            temperature = float(v)

        max_tokens = None
        if "max_tokens" in fields:
            v, fline, fcol = fields["max_tokens"]
            if not isinstance(v, int):
                raise ParseError(
                    f"invoke.max_tokens must be an integer, got {v!r}",
                    fline, fcol,
                )
            max_tokens = v

        timeout_seconds = None
        if "timeout" in fields:
            v, fline, fcol = fields["timeout"]
            if not isinstance(v, int):
                raise ParseError(
                    f"invoke.timeout must be a duration (e.g. 60s, 2m), got {v!r}",
                    fline, fcol,
                )
            timeout_seconds = v

        retries = None
        if "retries" in fields:
            v, fline, fcol = fields["retries"]
            if not isinstance(v, int):
                raise ParseError(
                    f"invoke.retries must be an integer, got {v!r}",
                    fline, fcol,
                )
            retries = v

        return ApiInvoke(
            line=line, col=col,
            protocol=protocol,
            model=model,
            base_url=_opt_str("base_url"),
            auth=_opt_str("auth"),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )

    def parse_on_fail(self, line: int, col: int) -> OnFailChain:
        self.expect(TokenType.KEYWORD, "ON_FAIL")
        self.expect(TokenType.COLON)
        strategies = [self.parse_strategy()]
        while self.peek().type == TokenType.KEYWORD and self.peek().value == "then":
            self.advance()
            strategies.append(self.parse_strategy())
        self.expect(TokenType.NEWLINE)
        return OnFailChain(strategies=tuple(strategies), line=line, col=col)

    def parse_strategy(self) -> OnFailStrategy:
        t = self.expect(TokenType.KEYWORD)
        if t.value == "retry":
            self.expect(TokenType.LPAREN)
            n_tok = self.expect(TokenType.NUMBER)
            self.expect(TokenType.RPAREN)
            return OnFailStrategy(
                kind="retry", max_retries=int(n_tok.value),
                line=t.line, col=t.col,
            )
        if t.value == "escalate":
            return OnFailStrategy(kind="escalate", line=t.line, col=t.col)
        if t.value == "fallback":
            self.expect(TokenType.LPAREN)
            name_tok = self.expect(TokenType.IDENT)
            self.expect(TokenType.RPAREN)
            return OnFailStrategy(
                kind="fallback", fallback_step_name=name_tok.value,
                line=t.line, col=t.col,
            )
        if t.value == "abort":
            self.expect(TokenType.LPAREN)
            msg_tok = self.expect(TokenType.STRING)
            self.expect(TokenType.RPAREN)
            return OnFailStrategy(
                kind="abort", abort_message=msg_tok.value,
                line=t.line, col=t.col,
            )
        raise ParseError(
            f"unknown ON_FAIL strategy {t.value!r} "
            f"(expected retry / escalate / fallback / abort)",
            t.line, t.col,
        )

    def parse_contract(self, exposed: bool = False) -> "ContractDecl":
        from clio.parser.expressions import parse_expression
        kw = self.expect(TokenType.KEYWORD, "CONTRACT")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        shape: TypeExpr | None = None
        assert_expr = None
        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "SHAPE":
                self.advance()
                self.expect(TokenType.COLON)
                shape = self.parse_type_expr()
                self.expect(TokenType.NEWLINE)
            elif t.type == TokenType.KEYWORD and t.value == "ASSERT":
                self.advance()
                self.expect(TokenType.COLON)
                start = self.pos
                while self.tokens[self.pos].type != TokenType.NEWLINE:
                    self.pos += 1
                expr_tokens = self.tokens[start:self.pos]
                expr, consumed = parse_expression(expr_tokens)
                if consumed != len(expr_tokens):
                    leftover = expr_tokens[consumed]
                    raise ParseError(
                        f"unexpected token {leftover.value!r} after ASSERT expression",
                        leftover.line, leftover.col,
                    )
                assert_expr = expr
                self.expect(TokenType.NEWLINE)
            else:
                raise ParseError(
                    f"unsupported contract field {t.value!r} (v0.1: SHAPE, ASSERT)",
                    t.line, t.col,
                )
        self.expect(TokenType.DEDENT)

        if shape is None:
            raise ParseError(
                f"CONTRACT {ident.value} is missing required SHAPE field",
                kw.line, kw.col,
            )
        return ContractDecl(
            name=ident.value,
            shape=shape,
            assert_expr=assert_expr,
            line=kw.line,
            col=kw.col,
            exposed=exposed,
        )

    def parse_field_list(self) -> tuple[Field, ...]:
        fields = [self.parse_field()]
        while self.peek().type == TokenType.COMMA:
            self.advance()
            fields.append(self.parse_field())
        return tuple(fields)

    def parse_field(self) -> Field:
        name_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        type_expr = self.parse_type_expr()
        return Field(name=name_tok.value, type=type_expr, line=name_tok.line, col=name_tok.col)

    def parse_type_expr(self) -> TypeExpr:
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value in _PRIMITIVE_TYPES:
            self.advance()
            base = PrimitiveType(name=t.value)
            if self.peek().type == TokenType.LPAREN:
                return self._parse_constraints(base)
            return base
        if t.type == TokenType.KEYWORD and t.value == "CSV":
            self.advance()
            return PrimitiveType(name="str")    # v0.1 domain-alias: CSV ≡ str
        if t.type == TokenType.KEYWORD and t.value == "List":
            return self.parse_list_type()
        if t.type == TokenType.KEYWORD and t.value == "Dict":
            return self.parse_dict_type()
        if t.type == TokenType.KEYWORD and t.value == "Optional":
            return self.parse_optional_type()
        if t.type == TokenType.KEYWORD and t.value == "enum":
            return self.parse_enum_type()
        if t.type == TokenType.LBRACE:
            return self.parse_record_type()
        if t.type == TokenType.IDENT:
            self.advance()
            return ContractRef(name=t.value, line=t.line, col=t.col)
        raise ParseError(
            f"expected a type expression, got {t.type.value} {t.value!r}",
            t.line, t.col,
        )

    def _parse_constraints(self, base: PrimitiveType) -> "ConstrainedType":
        allowed = _ALLOWED_CONSTRAINTS.get(base.name)
        if allowed is None:
            t = self.peek()
            raise ParseError(
                f"constraints not supported on `{base.name}` in v0.21 "
                f"(allowed bases: str, int, float)",
                t.line, t.col,
            )
        self.expect(TokenType.LPAREN)
        constraints: list[tuple[str, int | float]] = []
        constraints.append(self._parse_one_constraint(base.name, allowed))
        while self.peek().type == TokenType.COMMA:
            self.advance()
            constraints.append(self._parse_one_constraint(base.name, allowed))
        self.expect(TokenType.RPAREN)
        return ConstrainedType(base=base, constraints=tuple(constraints))

    def _parse_one_constraint(
        self, base_name: str, allowed: frozenset[str]
    ) -> tuple[str, int | float]:
        name_tok = self.expect(TokenType.IDENT)
        name = name_tok.value
        if name not in allowed:
            if name == "precision":
                raise ParseError(
                    f"`precision` constraint is only valid on `float`, "
                    f"not `{base_name}`",
                    name_tok.line, name_tok.col,
                )
            raise ParseError(
                f"constraint `{name}` not supported on `{base_name}` "
                f"(allowed: {', '.join(sorted(allowed))})",
                name_tok.line, name_tok.col,
            )
        self.expect(TokenType.EQUALS)
        num_tok = self.expect(TokenType.NUMBER)
        # Value type per (base, constraint):
        #   str(min/max):       int (length)
        #   int(min/max):       int (value)
        #   float(min/max):     float (value)
        #   float(precision):   int (decimal places — a count, not a value)
        wants_float = base_name == "float" and name in {"min", "max"}
        try:
            value: int | float = (
                float(num_tok.value) if wants_float else int(num_tok.value)
            )
        except ValueError as err:
            kind = "a number" if wants_float else "an integer"
            raise ParseError(
                f"`{name}` requires {kind}, got {num_tok.value!r}",
                num_tok.line, num_tok.col,
            ) from err
        return (name, value)

    def parse_list_type(self) -> ListType:
        self.expect(TokenType.KEYWORD, "List")
        self.expect(TokenType.LANGLE)
        inner = self.parse_type_expr()
        self.expect(TokenType.RANGLE)
        return ListType(inner=inner)

    def parse_dict_type(self) -> DictType:
        kw = self.expect(TokenType.KEYWORD, "Dict")
        self.expect(TokenType.LANGLE)
        key = self.parse_type_expr()
        if not (isinstance(key, PrimitiveType) and key.name == "str"):
            raise ParseError(
                "Dict<K, V> key must be `str` in v0.21 "
                "(JSON object keys are strings; iterate via "
                "`List<{key: str, val: V}>` if you need non-string keys)",
                kw.line, kw.col,
            )
        self.expect(TokenType.COMMA)
        value = self.parse_type_expr()
        self.expect(TokenType.RANGLE)
        return DictType(key=key, value=value)

    def parse_optional_type(self) -> OptionalType:
        self.expect(TokenType.KEYWORD, "Optional")
        self.expect(TokenType.LANGLE)
        inner = self.parse_type_expr()
        self.expect(TokenType.RANGLE)
        return OptionalType(inner=inner)

    def parse_record_type(self) -> RecordType:
        self.expect(TokenType.LBRACE)
        fields: list[tuple[str, TypeExpr]] = []
        fields.append(self._parse_record_field())
        while self.peek().type == TokenType.COMMA:
            self.advance()
            fields.append(self._parse_record_field())
        self.expect(TokenType.RBRACE)
        return RecordType(fields=tuple(fields))

    def _parse_record_field(self) -> tuple[str, TypeExpr]:
        name_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        type_expr = self.parse_type_expr()
        return (name_tok.value, type_expr)

    def parse_enum_type(self) -> EnumType:
        self.expect(TokenType.KEYWORD, "enum")
        self.expect(TokenType.LPAREN)
        values: list[str] = []
        first = self.expect(TokenType.IDENT)
        values.append(first.value)
        while self.peek().type == TokenType.PIPE:
            self.advance()
            tok = self.expect(TokenType.IDENT)
            values.append(tok.value)
        self.expect(TokenType.RPAREN)
        return EnumType(values=tuple(values))

    def parse_flow(self, exposed: bool = False) -> FlowDecl:
        kw = self.expect(TokenType.KEYWORD, "FLOW")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        takes: tuple[Field, ...] = ()
        gives: tuple[Field, ...] = ()
        description: str | None = None
        # v0.16: optional FLOW.TAKES / FLOW.GIVES blocks before the chain.
        # v0.17.x: optional FLOW.DESCRIPTION (mirrors STEP.DESCRIPTION).
        # Either order; duplicates rejected; absent fields default to ()/None.
        while True:
            t = self.peek()
            if t.type != TokenType.KEYWORD:
                break
            if t.value == "TAKES":
                if takes:
                    raise ParseError(
                        f"FLOW {ident.value} has duplicate TAKES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                takes = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            elif t.value == "GIVES":
                if gives:
                    raise ParseError(
                        f"FLOW {ident.value} has duplicate GIVES field", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                gives = self.parse_field_list()
                self.expect(TokenType.NEWLINE)
            elif t.value == "DESCRIPTION":
                if description is not None:
                    raise ParseError(
                        f"FLOW {ident.value} has duplicate DESCRIPTION field",
                        t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                description = self._parse_text_scalar(t.line, t.col, "DESCRIPTION")
            else:
                break

        chain: list[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst] = [self.parse_flow_item()]
        # Skip newlines and indent/dedent changes between chain elements,
        # then look for ARROW. The -> may appear on a more-indented continuation line.
        # Track the net INDENT count consumed here so we can pair each one
        # with a matching DEDENT BEFORE the rescue-collection loop runs;
        # otherwise the rescue loop would conflate chain-continuation
        # DEDENTs with the FLOW's own closing DEDENT.
        chain_indent_depth = 0
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.INDENT):
                if self.peek().type == TokenType.INDENT:
                    chain_indent_depth += 1
                self.advance()
            if self.peek().type == TokenType.ARROW:
                self.advance()
                chain.append(self.parse_flow_item())
            else:
                break

        # Pair the chain-continuation INDENTs with their closing DEDENTs
        # so the rescue-collection loop sees only the chain-item indent
        # (the FLOW body's INDENT level) and the FLOW's own closing DEDENT.
        while chain_indent_depth > 0:
            while self.peek().type == TokenType.NEWLINE:
                self.advance()
            if self.peek().type != TokenType.DEDENT:
                break
            self.advance()
            chain_indent_depth -= 1

        # Collect RESCUE blocks after the chain at the chain-item indent
        # level INSIDE the FLOW block (per LANGUAGE_SPEC § flow_decl). Only
        # NEWLINE trivia is consumed between the chain and the rescues; a
        # DEDENT here closes the FLOW block and stops rescue collection so
        # the next top-level decl (RESOURCES / STEP / CONTRACT / EOF) is
        # parsed by parse_program.
        rescues: list[RescueBlock] = []
        while True:
            while self.peek().type == TokenType.NEWLINE:
                self.advance()
            tok = self.peek()
            if tok.type == TokenType.KEYWORD and tok.value == "RESCUE":
                rescues.append(self.parse_rescue_block())
                continue
            break

        # Close the FLOW's INDENT block: consume any DEDENTs (and
        # interleaved NEWLINEs) that the lexer emitted to close out
        # nested bodies and the FLOW itself, leaving the next top-level
        # decl token in place for parse_program.
        while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
            self.advance()

        return FlowDecl(
            name=ident.value,
            chain=tuple(chain),
            rescues=tuple(rescues),
            line=kw.line, col=kw.col,
            takes=takes,
            gives=gives,
            description=description,
            exposed=exposed,
        )

    def parse_flow_item(self) -> "StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst":
        """A FLOW (or any nested body) item: step call, FOR EACH, IF/ELSE, MATCH, WHILE, or RESUME."""
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value == "FOR":
            return self.parse_for_each()
        if t.type == TokenType.KEYWORD and t.value == "IF":
            return self.parse_if_block()
        if t.type == TokenType.KEYWORD and t.value == "MATCH":
            return self.parse_match_block()
        if t.type == TokenType.KEYWORD and t.value == "WHILE":
            return self.parse_while_block()
        return self.parse_step_call()

    def parse_for_each(self) -> ForEachBlock:
        """FOR EACH <loop_var> IN <collection>:
            <flow_item>
              -> <flow_item>
              -> ...
        """
        kw = self.expect(TokenType.KEYWORD, "FOR")
        self.expect(TokenType.KEYWORD, "EACH")
        var_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.KEYWORD, "IN")
        collection_tok = self.expect(TokenType.IDENT)

        parallel = False
        collector: str | None = None

        # Optional `PARALLEL AS <ident>` between collection and ':'
        next_tok = self.peek()
        if next_tok.type == TokenType.KEYWORD and next_tok.value == "PARALLEL":
            self.advance()
            as_tok = self.peek()
            if not (as_tok.type == TokenType.KEYWORD and as_tok.value == "AS"):
                raise ParseError(
                    "PARALLEL requires an AS <name> binding",
                    next_tok.line, next_tok.col,
                )
            self.advance()
            collector_tok = self.expect(TokenType.IDENT)
            parallel = True
            collector = collector_tok.value
        elif next_tok.type == TokenType.KEYWORD and next_tok.value == "AS":
            raise ParseError(
                "AS binding is only valid with PARALLEL — sequential FOR EACH discards results",
                next_tok.line, next_tok.col,
            )

        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        body: list[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst] = [self.parse_flow_item()]
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.INDENT):
                self.advance()
            if self.peek().type == TokenType.ARROW:
                self.advance()
                body.append(self.parse_flow_item())
            else:
                break

        # Close the FOR EACH block
        while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
            # Stop at DEDENT once we've consumed the loop body's
            if self.peek().type == TokenType.DEDENT:
                self.advance()
                break
            self.advance()

        return ForEachBlock(
            loop_var=var_tok.value,
            collection=collection_tok.value,
            body=tuple(body),
            line=kw.line, col=kw.col,
            parallel=parallel,
            collector=collector,
        )

    def parse_if_block(self) -> IfBlock:
        """IF <condition>:
            <flow_item> -> <flow_item> -> ...
           ELSE:                                # optional, peer indent of IF
            <flow_item> -> <flow_item> -> ...

        Condition: `<step_name>.<field> <op> <literal>`. Single comparison
        only in v0.7 — no `and`/`or`, no `.FAILS`. Validated by IR builder."""
        kw = self.expect(TokenType.KEYWORD, "IF")
        condition = self.parse_condition()
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        then_body = self._parse_block_chain()

        else_body: tuple = ()
        # Inside the IF body's indent we've consumed up to the DEDENT below.
        # Accept ELSE on the very next non-empty token (peer indent), parsed
        # the same way as the THEN branch.
        else_kw = self.peek()
        if else_kw.type == TokenType.KEYWORD and else_kw.value == "ELSE":
            self.advance()
            self.expect(TokenType.COLON)
            self.expect(TokenType.NEWLINE)
            self.expect(TokenType.INDENT)
            else_body = self._parse_block_chain()

        return IfBlock(
            condition=condition,
            then_body=then_body,
            else_body=else_body,
            line=kw.line, col=kw.col,
        )

    def _parse_block_chain(self) -> tuple:
        """Parse an indented chain `item -> item -> ...` and consume the
        trailing DEDENT. Used by FOR EACH / IF / ELSE bodies."""
        chain: list = [self.parse_flow_item()]
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.INDENT):
                self.advance()
            if self.peek().type == TokenType.ARROW:
                self.advance()
                chain.append(self.parse_flow_item())
            else:
                break
        while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
            if self.peek().type == TokenType.DEDENT:
                self.advance()
                break
            self.advance()
        return tuple(chain)

    def parse_while_block(self) -> WhileBlock:
        """WHILE <condition> MAX <int>:
            <flow_item> -> <flow_item> -> ...

        MAX is mandatory — bounds the loop iterations to keep LLM-driven
        flows terminating."""
        kw = self.expect(TokenType.KEYWORD, "WHILE")
        condition = self.parse_condition()
        max_kw = self.peek()
        if not (max_kw.type == TokenType.KEYWORD and max_kw.value == "MAX"):
            raise ParseError(
                "WHILE requires a `MAX <int>` iteration bound",
                max_kw.line, max_kw.col,
            )
        self.advance()
        max_tok = self.expect(TokenType.NUMBER)
        if "." in max_tok.value:
            raise ParseError(
                f"WHILE MAX must be an integer, got {max_tok.value!r}",
                max_tok.line, max_tok.col,
            )
        max_iters = int(max_tok.value)
        if max_iters <= 0:
            raise ParseError(
                f"WHILE MAX must be > 0, got {max_iters}",
                max_tok.line, max_tok.col,
            )
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        body = self._parse_block_chain()
        return WhileBlock(
            condition=condition,
            max_iters=max_iters,
            body=body,
            line=kw.line, col=kw.col,
        )

    def parse_rescue_block(self) -> RescueBlock:
        """RESCUE <step_name>:
            <flow_item> -> <flow_item> -> ...

        Top-level handler attached to a STEP from the FLOW main chain.
        The last item of the body's top-level chain MUST be a call to
        `abort("message")` (validated at IR build time, not in this parser).

        A leading `->` is accepted (and discarded) so the body reads as a
        natural continuation of the rescued step in the source: e.g.
        `RESCUE step_a:\\n    -> abort("...")`.
        """
        kw = self.expect(TokenType.KEYWORD, "RESCUE")
        step_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        # Optional leading arrow: the spec example uses `RESCUE step:\n -> ...`
        # to evoke "continuing from the rescued step".
        if self.peek().type == TokenType.ARROW:
            self.advance()
        body = self._parse_block_chain()
        return RescueBlock(
            step_name=step_tok.value,
            body=body,
            line=kw.line, col=kw.col,
        )

    def parse_match_block(self) -> MatchBlock:
        """MATCH <state_field>.<sub_field>:
            CASE <value>: <flow_chain>
            CASE <value>: <flow_chain>
            DEFAULT:      <flow_chain>      # optional, must come last

        Each CASE / DEFAULT body is a chain like a FLOW body. CASE values are
        bare-idents (enum variants) or string literals; DEFAULT has no value."""
        kw = self.expect(TokenType.KEYWORD, "MATCH")
        scrutinee_step = self.expect(TokenType.IDENT)
        self.expect(TokenType.DOT)
        scrutinee_field = self.expect(TokenType.IDENT)
        scrutinee = FieldRefExpr(
            step_name=scrutinee_step.value, field=scrutinee_field.value,
        )
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        cases: list[MatchCase] = []
        seen_default = False
        while True:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "CASE":
                if seen_default:
                    raise ParseError(
                        "CASE arm must come before DEFAULT", t.line, t.col,
                    )
                self.advance()
                value_tok = self.peek()
                if value_tok.type == TokenType.STRING:
                    self.advance()
                    value = value_tok.value
                elif value_tok.type == TokenType.IDENT:
                    self.advance()
                    value = value_tok.value
                else:
                    raise ParseError(
                        f"expected CASE value (bare-ident or string), got "
                        f"{value_tok.type.value} {value_tok.value!r}",
                        value_tok.line, value_tok.col,
                    )
                self.expect(TokenType.COLON)
                body = self._parse_match_arm_body()
                cases.append(MatchCase(value=value, body=body, line=t.line, col=t.col))
            elif t.type == TokenType.KEYWORD and t.value == "DEFAULT":
                if seen_default:
                    raise ParseError(
                        "MATCH has duplicate DEFAULT arm", t.line, t.col,
                    )
                self.advance()
                self.expect(TokenType.COLON)
                body = self._parse_match_arm_body()
                cases.append(MatchCase(value=None, body=body, line=t.line, col=t.col))
                seen_default = True
            else:
                break

        # Consume the trailing DEDENT closing the MATCH block.
        while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
            if self.peek().type == TokenType.DEDENT:
                self.advance()
                break
            self.advance()

        if not cases:
            raise ParseError(
                "MATCH must have at least one CASE arm", kw.line, kw.col,
            )

        return MatchBlock(
            scrutinee=scrutinee, cases=tuple(cases), line=kw.line, col=kw.col,
        )

    def _parse_match_arm_body(self) -> tuple:
        """A CASE / DEFAULT body: either a single inline step call (`CASE x: step()`)
        on the same line, or an indented `step -> step -> ...` chain on the next
        lines. Returns a tuple of FlowItems."""
        # Inline form: same-line item until NEWLINE.
        if self.peek().type != TokenType.NEWLINE:
            item = self.parse_flow_item()
            self.expect(TokenType.NEWLINE)
            return (item,)
        # Block form: NEWLINE INDENT chain DEDENT
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)
        return self._parse_block_chain()

    def parse_condition(self):
        """Top-level IF/WHILE condition. Since v0.12 the grammar supports
        explicit `and` / `or` keywords with Python-like precedence (`and`
        binds tighter than `or`) and optional parentheses. The returned
        node is one of: CompareExpr, BoolAndExpr, BoolOrExpr."""
        return self._parse_cond_or()

    def _parse_cond_or(self):
        left = self._parse_cond_and()
        while self.peek().type == TokenType.KEYWORD and self.peek().value == "or":
            self.advance()
            right = self._parse_cond_and()
            left = BoolOrExpr(left=left, right=right)
        return left

    def _parse_cond_and(self):
        left = self._parse_cond_primary()
        while self.peek().type == TokenType.KEYWORD and self.peek().value == "and":
            self.advance()
            right = self._parse_cond_primary()
            left = BoolAndExpr(left=left, right=right)
        return left

    def _parse_cond_primary(self):
        """Parenthesised sub-expression or an atomic comparison."""
        if self.peek().type == TokenType.LPAREN:
            self.advance()
            inner = self._parse_cond_or()
            self.expect(TokenType.RPAREN)
            return inner
        return self._parse_atomic_compare()

    def _parse_atomic_compare(self) -> CompareExpr:
        """`<step_name>.<field> <op> <literal>`. <op> ∈ {==, !=, >, >=, <, <=}.
        <literal> ∈ string | int | float | bare-ident (treated as enum value)."""
        step_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.DOT)
        field_tok = self.expect(TokenType.IDENT)
        left = FieldRefExpr(step_name=step_tok.value, field=field_tok.value)

        op_tok = self.peek()
        op_map = {
            TokenType.OP_EQ: "==",
            TokenType.OP_NE: "!=",
            TokenType.OP_GE: ">=",
            TokenType.OP_LE: "<=",
            TokenType.LANGLE: "<",
            TokenType.RANGLE: ">",
        }
        if op_tok.type not in op_map:
            raise ParseError(
                f"expected comparison operator after {step_tok.value}.{field_tok.value}, "
                f"got {op_tok.type.value} {op_tok.value!r}",
                op_tok.line, op_tok.col,
            )
        self.advance()
        op = op_map[op_tok.type]

        rhs_tok = self.peek()
        right: StrExpr | FloatExpr | IntExpr | IdentExpr
        if rhs_tok.type == TokenType.STRING:
            self.advance()
            right = StrExpr(value=rhs_tok.value)
        elif rhs_tok.type == TokenType.NUMBER:
            self.advance()
            txt = rhs_tok.value
            right = FloatExpr(value=float(txt)) if "." in txt else IntExpr(value=int(txt))
        elif rhs_tok.type == TokenType.IDENT:
            self.advance()
            right = IdentExpr(name=rhs_tok.value)
        else:
            raise ParseError(
                f"expected literal (string, number, or identifier) after {op!r}, "
                f"got {rhs_tok.type.value} {rhs_tok.value!r}",
                rhs_tok.line, rhs_tok.col,
            )

        return CompareExpr(left=left, op=op, right=right)

    def parse_step_call(self) -> "StepCall | ResumeAst":
        # `abort` is a reserved keyword (see clio/keywords.py), but inside
        # a rescue body it appears as a synthetic step call. The parser is
        # permissive about where it can appear; the IR builder restricts
        # it to rescue bodies. The single positional STRING argument is
        # synthesised as `message=<str>` so downstream stages can treat
        # abort uniformly with other step calls.
        #
        # `RESUME(<step>.<field>)` is a sibling syntactic form: it takes a
        # dotted path rather than kwargs, so we branch to a dedicated helper
        # before touching IDENT expectations.
        tok = self.peek()
        is_abort = tok.type == TokenType.KEYWORD and tok.value == "abort"
        is_resume = tok.type == TokenType.KEYWORD and tok.value == "RESUME"
        if is_resume:
            return self._parse_resume_call()
        if is_abort:
            name_tok = self.advance()
        else:
            name_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.LPAREN)
        kwargs: list[tuple[str, object]] = []
        if is_abort and self.peek().type == TokenType.STRING:
            msg_tok = self.advance()
            kwargs.append(("message", msg_tok.value))
        elif self.peek().type != TokenType.RPAREN:
            kwargs.append(self._parse_call_arg())
            while self.peek().type == TokenType.COMMA:
                self.advance()
                kwargs.append(self._parse_call_arg())
        self.expect(TokenType.RPAREN)
        return StepCall(
            name=name_tok.value,
            kwargs=tuple(kwargs),
            line=name_tok.line,
            col=name_tok.col,
        )

    def _parse_resume_call(self) -> ResumeAst:
        """`RESUME ( IDENT . IDENT )` — terminator of a rescue chain.
        Caller has already peeked the RESUME keyword."""
        kw_tok = self.expect(TokenType.KEYWORD, "RESUME")
        self.expect(TokenType.LPAREN)
        step_tok = self.peek()
        if step_tok.type != TokenType.IDENT:
            raise ParseError(
                f"RESUME requires '<step>.<field>', got {step_tok.type.value} {step_tok.value!r}",
                kw_tok.line, kw_tok.col,
            )
        self.advance()
        self.expect(TokenType.DOT)
        field_tok = self.expect(TokenType.IDENT)
        # Allow exactly one (step.field) pair: refuse extra args / extra dots.
        if self.peek().type == TokenType.DOT:
            raise ParseError(
                "RESUME accepts exactly '<step>.<field>'; no further dotted path",
                kw_tok.line, kw_tok.col,
            )
        if self.peek().type == TokenType.COMMA:
            raise ParseError(
                "RESUME takes a single '<step>.<field>' argument; remove the comma",
                kw_tok.line, kw_tok.col,
            )
        self.expect(TokenType.RPAREN)
        return ResumeAst(
            fallback_step=step_tok.value,
            field_name=field_tok.value,
            line=kw_tok.line,
            col=kw_tok.col,
        )

    def _parse_call_arg(self) -> tuple[str, object]:
        first = self.peek()
        if first.type == TokenType.IDENT and self.tokens[self.pos + 1].type == TokenType.EQUALS:
            name_tok = self.advance()
            self.expect(TokenType.EQUALS)
            value_tok = self.peek()
            if value_tok.type == TokenType.STRING:
                self.advance()
                return (name_tok.value, value_tok.value)
            if value_tok.type == TokenType.NUMBER:
                self.advance()
                txt = value_tok.value
                return (name_tok.value, float(txt) if "." in txt else int(txt))
            if value_tok.type == TokenType.IDENT:
                # Single IDENT: state-ref shorthand returned as "@<name>".
                # Two-segment "<step>.<field>" is NOT accepted here (kwarg values
                # don't reference sub-fields in v0.12).
                # Three-segment "<step>.error.<field>" IS accepted in v0.13:
                # it produces an ErrorAccessExpr (validated by the IR builder
                # to be inside a RESCUE body referencing the rescued step).
                step_tok = self.advance()
                if self.peek().type != TokenType.DOT:
                    # plain shorthand
                    return (name_tok.value, f"@{step_tok.value}")
                # Saw a DOT — must be exactly "<step>.error.<field>"
                self.advance()  # consume DOT
                mid = self.expect(TokenType.IDENT)
                if mid.value != "error":
                    raise ParseError(
                        f"unknown 2-segment kwarg value '{step_tok.value}.{mid.value}'; "
                        f"only <step>.error.<message|type> is supported as a dotted kwarg value",
                        step_tok.line, step_tok.col,
                    )
                if self.peek().type != TokenType.DOT:
                    raise ParseError(
                        f"incomplete error access '{step_tok.value}.error'; "
                        f"expected '.message' or '.type'",
                        step_tok.line, step_tok.col,
                    )
                self.advance()  # consume DOT
                field_tok = self.expect(TokenType.IDENT)
                return (
                    name_tok.value,
                    ErrorAccessExpr(
                        step_name=step_tok.value,
                        field=field_tok.value,
                        line=step_tok.line,
                    ),
                )
            raise ParseError(
                f"expected literal value or state reference for kwarg, "
                f"got {value_tok.type.value}",
                value_tok.line, value_tok.col,
            )
        if first.type == TokenType.IDENT:
            self.advance()
            return (first.value, f"@{first.value}")
        raise ParseError(
            f"expected call argument, got {first.type.value} {first.value!r}",
            first.line, first.col,
        )


def parse(source: str) -> Program:
    return _Parser(lex(source)).parse_program()


_DURATION_FACTORS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _duration_to_seconds(dur: str) -> int:
    """`24h` → 86400. The lexer guarantees the format `\\d+[smhd]`."""
    suffix = dur[-1]
    return int(dur[:-1]) * _DURATION_FACTORS[suffix]
