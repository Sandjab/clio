from dataclasses import dataclass

from clio.parser.ast_nodes import TypeExpr


@dataclass(frozen=True)
class FieldIR:
    name: str
    type: TypeExpr


@dataclass(frozen=True)
class CacheConfigIR:
    mode: str               # "on" | "off" | "ttl"
    ttl_seconds: int | None


@dataclass(frozen=True)
class OnFailStrategyIR:
    """IR-level strategy. `fallback_step_name` is set by the builder; `fallback_step`
    is populated in slice G after a resolution pass."""
    kind: str
    max_retries: int | None = None
    fallback_step_name: str | None = None
    fallback_step: "StepIR | None" = None
    abort_message: str | None = None


@dataclass(frozen=True)
class OnFailChainIR:
    strategies: tuple[OnFailStrategyIR, ...]


@dataclass(frozen=True)
class ImplIR:
    """Sealed base for the per-step impl: block. Subtypes: CodeImplIR, RestImplIR."""


@dataclass(frozen=True)
class CodeImplIR(ImplIR):
    """impl.mode: code — inline function in the target language."""
    lang: str | None


@dataclass(frozen=True)
class ShellImplIR(ImplIR):
    """impl.mode: shell — argv-style command. `argv` is the shlex-split
    template; tokens may contain `${var}` placeholders that the emitters
    substitute at runtime."""
    argv: tuple[str, ...]
    timeout_seconds: int | None
    parse: str = "none"


@dataclass(frozen=True)
class RetryPolicyIR:
    """IR for `retry: {...}` on a REST step. See LANGUAGE_SPEC.md §impl.mode: rest / retry."""
    attempts: int
    backoff: str
    base: float
    cap: float
    on: tuple[str, ...]


@dataclass(frozen=True)
class RestBodyIR:
    """Sealed base for the IR body of an impl.rest step.
    Subtypes: JsonBodyIR, RawBodyIR, FileBodyIR, FormBodyIR, MultipartBodyIR."""


@dataclass(frozen=True)
class JsonBodyIR(RestBodyIR):
    """body as application/json — flat scalar dict (v1)."""
    fields: tuple[tuple[str, "JsonScalarIR"], ...]


@dataclass(frozen=True)
class RawBodyIR(RestBodyIR):
    """body as text/plain (overridable via headers)."""
    template: str


@dataclass(frozen=True)
class FileBodyIR(RestBodyIR):
    """body loaded from a path at runtime; content-type inferred from extension."""
    path: str


@dataclass(frozen=True)
class FormBodyIR(RestBodyIR):
    """body as application/x-www-form-urlencoded."""
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MultipartBodyIR(RestBodyIR):
    """body as multipart/form-data. Values starting with `@` are file paths."""
    fields: tuple[tuple[str, str], ...]


JsonScalarIR = str | int | float | bool | None


@dataclass(frozen=True)
class RestImplIR(ImplIR):
    """impl.mode: rest — HTTP call to an external endpoint."""
    method: str
    url: str
    query: tuple[tuple[str, "JsonScalarIR"], ...] | None
    headers: tuple[tuple[str, str], ...] | None
    body: RestBodyIR | None
    response_path: str | None
    timeout_seconds: int | None
    retry: RetryPolicyIR | None


# Sealed McpServerSpecIR hierarchy. One per transport.
@dataclass(frozen=True)
class McpServerSpecIR:
    """Sealed base for one entry in RESOURCES.mcp_servers (IR side).
    Subtypes: StdioServerSpecIR, SseServerSpecIR, HttpServerSpecIR."""
    name: str


@dataclass(frozen=True)
class StdioServerSpecIR(McpServerSpecIR):
    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class SseServerSpecIR(McpServerSpecIR):
    url: str
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HttpServerSpecIR(McpServerSpecIR):
    url: str
    headers: tuple[tuple[str, str], ...]


# Recursive type alias for tool-args values (mirrors ast_nodes.McpArgValue).
McpArgScalarIR = str | int | float | bool | None
McpArgValueIR = (
    McpArgScalarIR
    | dict[str, "McpArgValueIR"]
    | list["McpArgValueIR"]
)


@dataclass(frozen=True)
class McpToolImplIR(ImplIR):
    """impl.mode: mcp_tool — call a tool exposed by a configured MCP server.
    `server` references a name from `ResourcesIR.mcp_servers` (validated at
    build-time). Each `args` value conforms to `McpArgValueIR` (scalar |
    nested dict | nested list); annotated as `object` to keep IR-construction
    simple — the recursive validation is enforced upstream at parse time."""
    server: str
    tool: str
    args: tuple[tuple[str, object], ...]
    timeout_seconds: int
    parse: str   # "json" | "text"


@dataclass(frozen=True)
class DatabaseSpecIR:
    """One entry in RESOURCES.databases (IR side). See LANGUAGE_SPEC.md
    §RESOURCES.databases. `driver` is one of `sqlite` / `postgres` /
    `mysql`; `url` may be a literal connection string or `env:NAME`
    (resolved at runtime by `clio_runtime.sql`). All databases share the
    same shape — there are no per-driver subtypes the way McpServerSpecIR
    needs."""
    name: str
    driver: str                               # sqlite | postgres | mysql
    url: str                                  # path/URL or env:NAME


@dataclass(frozen=True)
class SqlImplIR(ImplIR):
    """impl.mode: sql — parameterized query against a database declared in
    `ResourcesIR.databases` (validated at build-time). The runtime maps the
    SQL `:name` bindings onto the step's TAKES dict and decides between a
    SELECT (rows) and a DML (rowcount) result based on the GIVES shape."""
    db: str
    query: str


@dataclass(frozen=True)
class InvokeIR:
    """Sealed base for the per-step invoke: block. Subtypes: CliInvokeIR, ApiInvokeIR."""


@dataclass(frozen=True)
class CliInvokeIR(InvokeIR):
    """invoke.mode: cli — subprocess to a locally installed LLM CLI."""
    cli: str | None
    model: str | None
    output_format: str | None
    max_turns: int | None


@dataclass(frozen=True)
class ApiInvokeIR(InvokeIR):
    """invoke.mode: api — SDK or HTTP call to a network endpoint."""
    protocol: str
    model: str
    base_url: str | None
    auth: str | None
    temperature: float | None
    max_tokens: int | None
    timeout_seconds: int | None
    retries: int | None


@dataclass(frozen=True)
class StepIR:
    name: str
    mode: str
    takes: tuple[FieldIR, ...]
    gives: FieldIR | None
    cache: CacheConfigIR | None
    on_fail: OnFailChainIR | None
    lang: str | None              # one of python|rust|go|node|bash|auto, exact-only; None if unset
    impl: ImplIR | None           # impl: block (code | rest), exact-only; None if unset
    invoke: InvokeIR | None       # invoke: block (cli | api), judgment-only; None if unset
    line: int


@dataclass(frozen=True)
class ContractIR:
    name: str
    json_schema: dict
    assert_json_ast: "dict | None"
    line: int


@dataclass(frozen=True)
class CallIR:
    step_name: str
    kwargs: tuple[tuple[str, object], ...]
    line: int


@dataclass(frozen=True)
class ForEachIR:
    """IR mirror of ForEachBlock: iterate `loop_var` over `collection` (a state
    field name), executing `body` for each element.

    `parallel=True` + `collector=<name>` means the body runs concurrently for
    each item, and results are collected into `state[<collector>]` as a list."""
    loop_var: str
    collection: str
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int
    parallel: bool = False
    collector: str | None = None


@dataclass(frozen=True)
class ConditionIR:
    """A single comparison `<step_name>.<field> <op> <literal_value>` used as
    the condition of an IF / WHILE block. The literal_kind tags the runtime
    type of `literal_value` so emitters can format it correctly."""
    step_name: str
    field: str
    op: str                            # ==, !=, >, >=, <, <=
    literal_value: object              # str | int | float | bool
    literal_kind: str                  # "str" | "int" | "float" | "bool" | "ident"


@dataclass(frozen=True)
class IfBlockIR:
    """IR mirror of IfBlock. `else_body` is `()` when no ELSE branch was
    declared in the source."""
    condition: ConditionIR
    then_body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    else_body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int


@dataclass(frozen=True)
class MatchCaseIR:
    """One CASE / DEFAULT arm of a MATCH block. `value` is None for DEFAULT,
    otherwise the literal string the runtime compares the scrutinee against."""
    value: str | None
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int


@dataclass(frozen=True)
class MatchBlockIR:
    """IR mirror of MatchBlock. The scrutinee is split into `state_field` /
    `sub_field` (as in ConditionIR) so emitters can render `state[X].Y`
    without re-walking the AST. `cases` is the tuple of arms in source order;
    a DEFAULT arm, if present, is always last."""
    state_field: str
    sub_field: str
    cases: "tuple[MatchCaseIR, ...]"
    line: int


@dataclass(frozen=True)
class WhileBlockIR:
    """IR mirror of WhileBlock. `max_iters` is the mandatory MAX bound; the
    runtime stops the loop when EITHER the condition turns false OR the body
    has executed `max_iters` times."""
    condition: ConditionIR
    max_iters: int
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int


@dataclass(frozen=True)
class RescueBlockIR:
    """IR mirror of RescueBlock. Bound to a StepIR by name (no direct
    pointer because StepIR is frozen). The handler runs only if the
    referenced STEP raises after its ON_FAIL chain (if any) exhausts."""
    step_name: str
    body: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    line: int


@dataclass(frozen=True)
class FlowIR:
    name: str
    chain: "tuple[CallIR | ForEachIR | IfBlockIR | MatchBlockIR | WhileBlockIR, ...]"
    rescues: "tuple[RescueBlockIR, ...]"
    line: int


@dataclass(frozen=True)
class ResourcesIR:
    target: str
    models: tuple[str, ...]
    mcp_servers: tuple[McpServerSpecIR, ...] = ()
    databases: tuple[DatabaseSpecIR, ...] = ()


@dataclass(frozen=True)
class FlowGraph:
    steps: tuple[StepIR, ...]
    contracts: tuple[ContractIR, ...] = ()
    flow: FlowIR | None = None
    resources: ResourcesIR | None = None
