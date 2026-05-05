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
class RestImplIR(ImplIR):
    """impl.mode: rest — HTTP call to an external endpoint."""
    method: str
    url: str
    response_path: str | None
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
class FlowIR:
    name: str
    chain: tuple[CallIR, ...]
    line: int


@dataclass(frozen=True)
class ResourcesIR:
    target: str
    models: tuple[str, ...]


@dataclass(frozen=True)
class FlowGraph:
    steps: tuple[StepIR, ...]
    contracts: tuple[ContractIR, ...] = ()
    flow: FlowIR | None = None
    resources: ResourcesIR | None = None
