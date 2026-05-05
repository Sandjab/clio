from clio.parser.ast_nodes import (
    ApiInvoke,
    CacheConfig,
    CliInvoke,
    CodeImpl,
    ConstrainedType,
    ContractDecl,
    ContractRef,
    EnumType,
    Field,
    FlowDecl,
    ForEachBlock,
    ImplBlock,
    InvokeBlock,
    ListType,
    OnFailChain,
    OnFailStrategy,
    PrimitiveType,
    Program,
    RecordType,
    RestImpl,
    StepCall,
    StepDecl,
    TypeExpr,
)
from clio.parser.lexer import lex
from clio.parser.tokens import Token, TokenType


class ParseError(Exception):
    def __init__(self, msg: str, line: int, col: int) -> None:
        super().__init__(f"line {line}:{col}: {msg}")
        self.line = line
        self.col = col


_PRIMITIVE_TYPES = {"int", "float", "str", "bool"}
_VALID_MODES = {"exact", "judgment"}
_VALID_LANGS = {"python", "rust", "go", "node", "bash", "auto"}
_VALID_IMPL_MODES = {"code", "rest"}
_VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_VALID_INVOKE_MODES = {"cli", "api"}
_VALID_PROTOCOLS = {"anthropic", "openai", "bedrock", "vertex"}


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
        self.skip_newlines()
        while self.peek().type != TokenType.EOF:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "STEP":
                decls.append(self.parse_step())
            elif t.type == TokenType.KEYWORD and t.value == "CONTRACT":
                decls.append(self.parse_contract())
            elif t.type == TokenType.KEYWORD and t.value == "FLOW":
                decls.append(self.parse_flow())
            elif t.type == TokenType.KEYWORD and t.value == "RESOURCES":
                decls.append(self.parse_resources())
            else:
                raise ParseError(
                    f"expected STEP / CONTRACT / FLOW / RESOURCES, got {t.type.value} {t.value!r}",
                    t.line, t.col,
                )
            self.skip_newlines()
        return Program(tuple(decls))

    def parse_resources(self) -> "ResourcesDecl":
        from clio.parser.ast_nodes import ResourcesDecl
        kw = self.expect(TokenType.KEYWORD, "RESOURCES")
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        target: str | None = None
        models: tuple[str, ...] = ()
        while self.peek().type != TokenType.DEDENT:
            t = self.peek()
            if t.type == TokenType.KEYWORD and t.value == "target":
                self.advance()
                self.expect(TokenType.COLON)
                value_tok = self.expect(TokenType.KEYWORD)
                if value_tok.value != "claude-cli":
                    raise ParseError(
                        f"target {value_tok.value!r} is not supported in v0.1 (only claude-cli)",
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
        if not models:
            raise ParseError("RESOURCES is missing required `models` field", kw.line, kw.col)

        return ResourcesDecl(target=target, models=models, line=kw.line, col=kw.col)

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
        # unreachable: mode validation happened above
        raise ParseError(f"impl.mode {mode_value!r} not yet implemented", mode_line, mode_col)

    def _parse_impl_field_value(self) -> object:
        t = self.peek()
        if t.type == TokenType.STRING:
            self.advance()
            return t.value
        if t.type == TokenType.NUMBER:
            self.advance()
            return int(t.value) if "." not in t.value else float(t.value)
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

    def _build_rest_impl(
        self,
        fields: dict[str, tuple[object, int, int]],
        line: int, col: int,
        mode_line: int, mode_col: int,
    ) -> RestImpl:
        allowed = {"method", "url", "response_path", "timeout", "retries"}
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

        retries = None
        if "retries" in fields:
            rv, rline, rcol = fields["retries"]
            if not isinstance(rv, int):
                raise ParseError(
                    f"impl.retries must be an integer, got {rv!r}",
                    rline, rcol,
                )
            retries = rv

        return RestImpl(
            line=line, col=col,
            method=method, url=url,
            response_path=response_path,
            timeout_seconds=timeout_seconds,
            retries=retries,
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

    def parse_contract(self) -> "ContractDecl":
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
        if base.name != "str":
            t = self.peek()
            raise ParseError(
                f"constrained types are only supported on `str` in v0.1, got {base.name!r}",
                t.line, t.col,
            )
        self.expect(TokenType.LPAREN)
        constraints: list[tuple[str, int]] = []
        constraints.append(self._parse_one_constraint())
        while self.peek().type == TokenType.COMMA:
            self.advance()
            constraints.append(self._parse_one_constraint())
        self.expect(TokenType.RPAREN)
        return ConstrainedType(base=base, constraints=tuple(constraints))

    def _parse_one_constraint(self) -> tuple[str, int]:
        name_tok = self.expect(TokenType.IDENT)
        if name_tok.value != "max":
            raise ParseError(
                f"only the `max` constraint is supported in v0.1, got {name_tok.value!r}",
                name_tok.line, name_tok.col,
            )
        self.expect(TokenType.EQUALS)
        num_tok = self.expect(TokenType.NUMBER)
        try:
            value = int(num_tok.value)
        except ValueError:
            raise ParseError(
                f"`max` requires an integer, got {num_tok.value!r}",
                num_tok.line, num_tok.col,
            )
        return (name_tok.value, value)

    def parse_list_type(self) -> ListType:
        self.expect(TokenType.KEYWORD, "List")
        self.expect(TokenType.LANGLE)
        inner = self.parse_type_expr()
        self.expect(TokenType.RANGLE)
        return ListType(inner=inner)

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

    def parse_flow(self) -> FlowDecl:
        kw = self.expect(TokenType.KEYWORD, "FLOW")
        ident = self.expect(TokenType.IDENT)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        chain: list[StepCall | ForEachBlock] = [self.parse_flow_item()]
        # Skip newlines and indent/dedent changes between chain elements,
        # then look for ARROW. The -> may appear on a more-indented continuation line.
        while True:
            while self.peek().type in (TokenType.NEWLINE, TokenType.INDENT):
                self.advance()
            if self.peek().type == TokenType.ARROW:
                self.advance()
                chain.append(self.parse_flow_item())
            else:
                break

        # Consume any remaining newlines and dedents to close the FLOW block
        while self.peek().type in (TokenType.NEWLINE, TokenType.DEDENT):
            self.advance()

        return FlowDecl(name=ident.value, chain=tuple(chain), line=kw.line, col=kw.col)

    def parse_flow_item(self) -> "StepCall | ForEachBlock":
        """A FLOW (or a FOR EACH body) item: either a step call or a nested FOR EACH."""
        t = self.peek()
        if t.type == TokenType.KEYWORD and t.value == "FOR":
            return self.parse_for_each()
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
        self.expect(TokenType.COLON)
        self.expect(TokenType.NEWLINE)
        self.expect(TokenType.INDENT)

        body: list[StepCall | ForEachBlock] = [self.parse_flow_item()]
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
        )

    def parse_step_call(self) -> StepCall:
        name_tok = self.expect(TokenType.IDENT)
        self.expect(TokenType.LPAREN)
        kwargs: list[tuple[str, object]] = []
        if self.peek().type != TokenType.RPAREN:
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
                # State reference: kwarg=identifier resolves to state[identifier]
                # at runtime. Same convention as the shorthand `step(name)` form.
                self.advance()
                return (name_tok.value, f"@{value_tok.value}")
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
