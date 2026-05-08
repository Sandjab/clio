from dataclasses import dataclass


@dataclass(frozen=True)
class TypeExpr:
    """Base class for type expression nodes."""


@dataclass(frozen=True)
class PrimitiveType(TypeExpr):
    name: str       # one of: int, float, str, bool


@dataclass(frozen=True)
class ListType(TypeExpr):
    inner: TypeExpr


@dataclass(frozen=True)
class RecordType(TypeExpr):
    fields: tuple[tuple[str, TypeExpr], ...]   # ((name, type), ...)


@dataclass(frozen=True)
class EnumType(TypeExpr):
    values: tuple[str, ...]


@dataclass(frozen=True)
class Field:
    name: str
    type: TypeExpr
    line: int
    col: int


@dataclass(frozen=True)
class StepDecl:
    name: str
    mode: str
    takes: tuple[Field, ...]
    gives: Field | None
    cache: "CacheConfig | None"
    on_fail: "OnFailChain | None"
    lang: str | None              # one of python|rust|go|node|bash|auto, exact-only
    impl: "ImplBlock | None"      # impl: block (code | rest), exact-only
    invoke: "InvokeBlock | None"  # invoke: block (cli | api), judgment-only
    line: int
    col: int


@dataclass(frozen=True)
class ContractRef(TypeExpr):
    name: str          # the contract being referenced
    line: int
    col: int


@dataclass(frozen=True)
class ContractDecl:
    name: str
    shape: TypeExpr    # always a RecordType in v0.1
    assert_expr: "ExprNode | None"
    line: int
    col: int


@dataclass(frozen=True)
class ConstrainedType(TypeExpr):
    base: TypeExpr            # always PrimitiveType("str") in v0.1
    constraints: tuple[tuple[str, int], ...]   # e.g. (("max", 300),)


@dataclass(frozen=True)
class StepCall:
    name: str                                   # which STEP
    kwargs: tuple[tuple[str, object], ...]
    line: int
    col: int


@dataclass(frozen=True)
class ForEachBlock:
    """FOR EACH <loop_var> IN <collection>:
        <body>

    `collection` is the name of a state field (the GIVES of an upstream step).
    `body` is a chain of FlowItems executed for each element.

    `parallel=True` + `collector=<name>` means the body runs concurrently for
    each item, and results are collected into `state[<collector>]` as a list."""
    loop_var: str
    collection: str
    body: "tuple[StepCall | ForEachBlock, ...]"
    line: int
    col: int
    parallel: bool = False
    collector: str | None = None


@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock, ...]"   # sequential composition; ForEachBlock = nested loop
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    decls: tuple[object, ...]    # StepDecl | ContractDecl | FlowDecl


@dataclass(frozen=True)
class ExprNode:
    """Base for ASSERT expression AST nodes."""


@dataclass(frozen=True)
class IdentExpr(ExprNode):
    name: str


@dataclass(frozen=True)
class IntExpr(ExprNode):
    value: int


@dataclass(frozen=True)
class FloatExpr(ExprNode):
    value: float


@dataclass(frozen=True)
class StrExpr(ExprNode):
    value: str


@dataclass(frozen=True)
class CallExpr(ExprNode):
    func: str                       # only "len" allowed in v0.1
    args: tuple["ExprNode", ...]


@dataclass(frozen=True)
class CompareExpr(ExprNode):
    left: "ExprNode"
    op: str                         # one of: ==, !=, >=, <=, >, <
    right: "ExprNode"


@dataclass(frozen=True)
class ResourcesDecl:
    target: str
    models: tuple[str, ...]
    line: int
    col: int


@dataclass(frozen=True)
class CacheConfig:
    """Cache directive on a STEP. Mode is one of 'on', 'off', 'ttl'.
    For 'ttl', `ttl_seconds` is the parsed duration in seconds; for 'on' / 'off' it is None."""
    mode: str           # "on" | "off" | "ttl"
    ttl_seconds: int | None
    line: int
    col: int


@dataclass(frozen=True)
class OnFailStrategy:
    """One clause in an ON_FAIL chain. `kind` is one of:
       'retry'    → max_retries: int
       'escalate' → no extra fields
       'fallback' → fallback_step_name: str    (resolved in slice G)
       'abort'    → abort_message: str
    """
    kind: str
    max_retries: int | None = None
    fallback_step_name: str | None = None
    abort_message: str | None = None
    line: int = 0
    col: int = 0


@dataclass(frozen=True)
class OnFailChain:
    strategies: tuple[OnFailStrategy, ...]
    line: int
    col: int


@dataclass(frozen=True)
class ImplBlock:
    """Sealed base for the per-step impl: block. Subtypes: CodeImpl, RestImpl.
    Specced in LANGUAGE_SPEC.md §EXACT implementations."""
    line: int
    col: int


@dataclass(frozen=True)
class CodeImpl(ImplBlock):
    """impl.mode: code — inline function in the target language."""
    lang: str | None              # python | rust | go | node | bash | auto


@dataclass(frozen=True)
class ShellImpl(ImplBlock):
    """impl.mode: shell — argv-style invocation of a shell command. The
    `cmd` is shlex-split at compile time; templating substitutes TAKES
    into per-token slots. No pipes/redirections (those need shell=True
    which is unsafe with user-provided strings)."""
    cmd: str
    timeout_seconds: int | None
    parse: str = "none"   # "none" (stdout as str) | "json" (json.loads at runtime)


@dataclass(frozen=True)
class RestImpl(ImplBlock):
    """impl.mode: rest — HTTP call to an external endpoint."""
    method: str                    # GET | POST | PUT | PATCH | DELETE
    url: str
    response_path: str | None      # e.g. "results[0].geometry.location"
    timeout_seconds: int | None
    retries: int | None


@dataclass(frozen=True)
class InvokeBlock:
    """Sealed base for the per-step invoke: block. Subtypes: CliInvoke, ApiInvoke.
    Specced in LANGUAGE_SPEC.md §JUDGMENT invocation."""
    line: int
    col: int


@dataclass(frozen=True)
class CliInvoke(InvokeBlock):
    """invoke.mode: cli — subprocess to a locally installed LLM CLI."""
    cli: str | None                # e.g. "claude" (default if None)
    model: str | None              # CLI alias, e.g. "haiku"|"sonnet"|"opus"
    output_format: str | None      # e.g. "json" (default), "text", "stream-json"
    max_turns: int | None


@dataclass(frozen=True)
class ApiInvoke(InvokeBlock):
    """invoke.mode: api — SDK or HTTP call to a network endpoint."""
    protocol: str                  # anthropic | openai | bedrock | vertex
    model: str
    base_url: str | None           # required for proxies / local servers
    auth: str | None               # env:VAR | aws-profile:NAME | gcp-sa:PATH | none
    temperature: float | None
    max_tokens: int | None
    timeout_seconds: int | None
    retries: int | None
