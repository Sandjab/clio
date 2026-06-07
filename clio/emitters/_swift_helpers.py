"""target: swift — graph-bound renderers + compile-time validation."""
from __future__ import annotations

from clio.emitters._shared_utils import _to_class_name
from clio.ir.graph import ContractIR, FlowGraph
from clio.parser.ast_nodes import (
    ConstrainedType,
    ContractRef,
    DictType,
    EnumType,
    ListType,
    OptionalType,
    PrimitiveType,
    RecordType,
    TypeExpr,
)

E_SWIFT_004 = "E_SWIFT_004: source declares no FLOW; nothing to orchestrate."

_SWIFT_PRIMITIVES: dict[str, str] = {
    "str": "String",
    "int": "Int",
    "float": "Double",
    "bool": "Bool",
    "any": "Any",
}


def _type_to_swift(t: TypeExpr, contracts: dict[str, ContractIR]) -> str:
    """Render a CLIO TypeExpr as a Swift type expression."""
    if isinstance(t, ConstrainedType):
        return _type_to_swift(t.base, contracts)
    if isinstance(t, PrimitiveType):
        return _SWIFT_PRIMITIVES[t.name]
    if isinstance(t, EnumType):
        return "String"
    if isinstance(t, ListType):
        return f"[{_type_to_swift(t.inner, contracts)}]"
    if isinstance(t, DictType):
        return (
            f"[{_type_to_swift(t.key, contracts)}: "
            f"{_type_to_swift(t.value, contracts)}]"
        )
    if isinstance(t, OptionalType):
        return f"{_type_to_swift(t.inner, contracts)}?"
    if isinstance(t, ContractRef):
        return _to_class_name(t.name)
    if isinstance(t, RecordType):
        # Anonymous record: [String: Any] for Phase 1; typed structs deferred.
        return "[String: Any]"
    raise ValueError(f"unsupported TypeExpr for Swift target: {type(t).__name__}")


def _swift_module_name(graph: FlowGraph, default: str = "flow") -> str:
    name = graph.flow.name if graph.flow else default
    cleaned = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    return cleaned or default


def validate_graph_for_swift(graph: FlowGraph) -> None:
    if graph.flow is None:
        raise ValueError(E_SWIFT_004)
    # further E_SWIFT_* gates added in a later task


def render_package_swift(graph: FlowGraph) -> str:
    exe = _swift_module_name(graph)
    return (
        "// swift-tools-version:6.0\n"
        "import PackageDescription\n\n"
        "let package = Package(\n"
        f'    name: "{exe}",\n'
        "    targets: [\n"
        '        .target(name: "ClioFlow"),\n'
        f'        .executableTarget(name: "{exe}", dependencies: ["ClioFlow"]),\n'
        "    ]\n"
        ")\n"
    )


def render_main_swift(graph: FlowGraph) -> str:
    """Render Sources/<exe>/Main.swift — CLI entry point."""
    return (
        "import Foundation\n"
        "import ClioFlow\n"
        "\n"
        "@main\n"
        "struct CLI {\n"
        "    static func main() async throws {\n"
        "        var kwargs: [String: Any] = [:]\n"
        "        let args = CommandLine.arguments\n"
        '        if let i = args.firstIndex(of: "--kwargs"),\n'
        "           i + 1 < args.count,\n"
        "           let data = args[i + 1].data(using: .utf8),\n"
        "           let obj = try? JSONSerialization.jsonObject(with: data)"
        " as? [String: Any] {\n"
        "            kwargs = obj\n"
        "        }\n"
        "        let result = try await Flow.run(kwargs: kwargs)\n"
        "        let out = try JSONSerialization.data(\n"
        "            withJSONObject: result, options: [.sortedKeys]\n"
        "        )\n"
        '        print(String(data: out, encoding: .utf8) ?? "{}")\n'
        "    }\n"
        "}\n"
    )
