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
    description: str | None = None    # v0.15 — free-text intent; injected into judgment prompts
    strategies: str | None = None     # v0.15 — heuristics for edge cases; injected into judgment prompts


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
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    line: int
    col: int
    parallel: bool = False
    collector: str | None = None


@dataclass(frozen=True)
class IfBlock:
    """IF <condition>:
           <then_body>
       ELSE:
           <else_body>      # optional

    Since v0.12 the condition may compose multiple comparisons with
    `and` / `or` and optional parentheses; the AST is a `CompareExpr`,
    a `BoolAndExpr`, or a `BoolOrExpr`. `else_body` is `()` when no ELSE
    branch is provided."""
    condition: "ExprNode"
    then_body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    else_body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class MatchCase:
    """One CASE <value>: <body> arm of a MATCH block. `value` is None for the
    DEFAULT arm; otherwise it's the bare-ident or string literal to match."""
    value: str | None
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class MatchBlock:
    """MATCH <state_field>.<sub_field>:
           CASE <value>: <body>
           CASE <value>: <body>
           DEFAULT:      <body>      # optional, must come last

    The scrutinee is a FieldRefExpr referencing an enum sub-field of an
    upstream contract. CASE values match the enum variants. The DEFAULT arm
    is optional but strongly recommended (langgraph target requires it)."""
    scrutinee: "FieldRefExpr"
    cases: "tuple[MatchCase, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class WhileBlock:
    """WHILE <condition> MAX <int>:
           <body>

    Bounded loop: re-evaluates <condition> after each body iteration; stops
    when the condition becomes false OR after MAX iterations (whichever
    comes first). MAX is mandatory — unbounded loops are forbidden. Since
    v0.12 the condition shares the IF grammar (`and` / `or` with optional
    parentheses)."""
    condition: "ExprNode"
    max_iters: int
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class RescueBlock:
    """RESCUE step_name:
           <chain ending with abort(message)>

    Top-level handler attached to a STEP from the FLOW's main chain. The
    body runs if step_name raises after its ON_FAIL chain is exhausted.
    The body's last top-level item must be a StepCall to `abort`."""
    step_name: str
    body: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class FlowDecl:
    name: str
    chain: "tuple[StepCall | ForEachBlock | IfBlock | MatchBlock | WhileBlock | ResumeAst, ...]"
    rescues: "tuple[RescueBlock, ...]"
    line: int
    col: int


@dataclass(frozen=True)
class Predicate:
    """v0.15 — a TEST assertion predicate.

    `kind` is one of:
      - "not_empty" / "empty"      → no value
      - "eq" / "ne"                → value: str | int | float | bool
      - "gt" / "ge" / "lt" / "le"  → value: int | float
      - "contains"                 → value: str | int | float | bool
    """
    kind: str
    value: object = None


@dataclass(frozen=True)
class TestDecl:
    """v0.15 — top-level TEST block.

    `flow_name` references a FLOW declared in the same source. `with_kwargs`
    is the kwargs dict passed to the flow's `run()`. `expects` / `expects_not`
    are tuples of (state_field, Predicate) assertions evaluated against the
    state dict returned by `run()`. The python target emits these as pytest
    files under <output>/tests/.
    """
    name: str
    flow_name: str
    with_kwargs: tuple[tuple[str, object], ...]
    expects: tuple[tuple[str, Predicate], ...]
    expects_not: tuple[tuple[str, Predicate], ...]
    line: int
    col: int


@dataclass(frozen=True)
class Program:
    decls: tuple[object, ...]    # StepDecl | ContractDecl | FlowDecl | TestDecl


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
class FieldRefExpr(ExprNode):
    """`<step_name>.<field>` — used inside IF/MATCH/WHILE conditions to read
    a step's GIVES field from runtime state. Bound to the step's gives type
    by the IR builder."""
    step_name: str
    field: str


@dataclass(frozen=True)
class ErrorAccessExpr(ExprNode):
    """`<step_name>.error.<field>` — kwarg value reference to the captured
    error of a step protected by a RESCUE handler. Valid only as a kwarg
    value inside a step call that itself lives in a RESCUE body. Bound to
    the rescued step's identity by the IR builder.

    `field` is parsed as-is; the IR builder validates membership in
    {"message", "type"}."""
    step_name: str
    field: str
    line: int


@dataclass(frozen=True)
class ResumeAst:
    """`RESUME(<fallback_step>.<field>)` — alternative terminator of a
    RESCUE handler chain (next to `abort(...)`). `fallback_step` is
    the name of a step called earlier in the same RESCUE chain;
    `field_name` is one of that step's GIVES fields.

    The IR builder validates that the field type matches the rescued
    step's GIVES type."""
    fallback_step: str
    field_name: str
    line: int
    col: int


@dataclass(frozen=True)
class CompareExpr(ExprNode):
    left: "ExprNode"
    op: str                         # one of: ==, !=, >=, <=, >, <
    right: "ExprNode"


@dataclass(frozen=True)
class BoolAndExpr(ExprNode):
    """Conjunction of two boolean sub-expressions. Produced by
    chained-comparator desugaring inside ASSERT (`a <= b <= c` →
    `(a <= b) and (b <= c)`) and by the explicit `and` keyword in
    IF/WHILE conditions (v0.12+)."""
    left: "ExprNode"
    right: "ExprNode"


@dataclass(frozen=True)
class BoolOrExpr(ExprNode):
    """Disjunction of two boolean sub-expressions. Produced by the
    explicit `or` keyword in IF/WHILE conditions (v0.12+)."""
    left: "ExprNode"
    right: "ExprNode"


@dataclass(frozen=True)
class ResourcesDecl:
    target: str
    models: tuple[str, ...]
    mcp_servers: "tuple[McpServerSpec, ...]"
    line: int
    col: int
    databases: "tuple[DatabaseSpec, ...]" = ()


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
class RetryPolicy:
    """`retry: {...}` policy attached to an impl.rest step.
    See LANGUAGE_SPEC.md §impl.mode: rest / retry."""
    attempts: int
    backoff: str = "exponential"        # "exponential" | "constant"
    base: float = 0.1                   # seconds
    cap: float = 30.0                   # seconds
    on: tuple[str, ...] = ("5xx", "429", "timeout")


# Sealed RestBody hierarchy.
@dataclass(frozen=True)
class RestBody:
    """Sealed base for impl.rest body. Subtypes: JsonBody, RawBody, FileBody,
    FormBody, MultipartBody. See LANGUAGE_SPEC.md §impl.mode: rest / body."""


@dataclass(frozen=True)
class JsonBody(RestBody):
    """body: {field: value, ...}  → application/json (flat dict, v1)."""
    fields: tuple[tuple[str, "JsonScalar"], ...]


@dataclass(frozen=True)
class RawBody(RestBody):
    """body: "raw text ${var}"  → text/plain (overridable via headers)."""
    template: str


@dataclass(frozen=True)
class FileBody(RestBody):
    """body: "@./payload.json"  → file body, content-type inferred from extension."""
    path: str


@dataclass(frozen=True)
class FormBody(RestBody):
    """body: {form: {...}}  → x-www-form-urlencoded."""
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MultipartBody(RestBody):
    """body: {multipart: {...}}  → multipart/form-data. Values starting with `@` are file paths."""
    fields: tuple[tuple[str, str], ...]


# Type alias for JSON-body scalar values (flat in v1).
JsonScalar = str | int | float | bool | None


@dataclass(frozen=True)
class RestImpl(ImplBlock):
    """impl.mode: rest — HTTP call to an external endpoint."""
    method: str                    # GET | POST | PUT | PATCH | DELETE
    url: str
    query: tuple[tuple[str, "JsonScalar"], ...] | None
    headers: tuple[tuple[str, str], ...] | None
    body: RestBody | None
    response_path: str | None      # e.g. "results[0].geometry.location"
    timeout_seconds: int | None
    retry: RetryPolicy | None


# Sealed McpServerSpec hierarchy. One per transport.
@dataclass(frozen=True)
class McpServerSpec:
    """Sealed base for one entry in RESOURCES.mcp_servers. Subtypes:
    StdioServerSpec, SseServerSpec, HttpServerSpec. See LANGUAGE_SPEC.md
    §RESOURCES.mcp_servers."""
    name: str
    line: int
    col: int


@dataclass(frozen=True)
class StdioServerSpec(McpServerSpec):
    """transport: stdio — subprocess MCP server. `env` values may use
    `env:NAME` to inherit from the host env at runtime."""
    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SseServerSpec(McpServerSpec):
    """transport: sse — Server-Sent Events MCP server. `headers` values
    may use `env:NAME`."""
    url: str
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HttpServerSpec(McpServerSpec):
    """transport: http — HTTP MCP server (streamable). `headers` values
    may use `env:NAME`."""
    url: str
    headers: tuple[tuple[str, str], ...]


# Type alias for tool-args scalar values.
McpArgScalar = str | int | float | bool | None


@dataclass(frozen=True)
class McpToolImpl(ImplBlock):
    """impl.mode: mcp_tool — call a tool exposed by a configured MCP server.
    See LANGUAGE_SPEC.md §impl.mode: mcp_tool. Each `args` entry value
    conforms to `McpArgValue` (scalar | nested dict | nested list); the
    annotation is `object` to keep the parser-side construction simple,
    and the recursive validation is enforced at parse time."""
    server: str                              # name from RESOURCES.mcp_servers
    tool: str                                # tool name on the server
    args: tuple[tuple[str, object], ...]     # may be ()
    timeout_seconds: int                     # default 60
    parse: str = "json"                      # "json" | "text"


# Recursive type alias for tool-args values: scalars, dicts (str → value),
# or lists of values. String values can carry `${var}` substitutions
# (resolved at runtime by the bundled clio_runtime.mcp_client). Nested
# dicts and lists stay as native Python types — the top-level
# `McpToolImpl.args` is an immutable tuple of (key, value) pairs but the
# nested values keep dict/list shape so the runtime can json.dumps them
# directly.
McpArgValue = (
    McpArgScalar
    | dict[str, "McpArgValue"]
    | list["McpArgValue"]
)


@dataclass(frozen=True)
class DatabaseSpec:
    """One entry in RESOURCES.databases. See LANGUAGE_SPEC.md
    §RESOURCES.databases. `driver` is one of `sqlite` / `postgres` /
    `mysql`; `url` may be a literal connection string or `env:NAME`
    (resolved at runtime in the emitted runtime helper). All databases
    in a flow share the same shape — there are no transport-specific
    subtypes the way McpServerSpec needs."""
    name: str
    driver: str                              # sqlite | postgres | mysql
    url: str                                 # path/URL or env:NAME
    line: int
    col: int


@dataclass(frozen=True)
class SqlImpl(ImplBlock):
    """impl.mode: sql — parameterized query against a database declared
    in RESOURCES.databases. See LANGUAGE_SPEC.md §impl.mode: sql.
    `db` references a name from RESOURCES.databases (validated at IR
    build time). `query` is the raw SQL body with `:name` bindings;
    the runtime maps `:name` to driver-native named-binding form and
    builds the params dict from the step's TAKES."""
    db: str
    query: str


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
