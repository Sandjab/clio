"""target: swift — graph-bound renderers + compile-time validation."""
from __future__ import annotations

import json

from clio.emitters._shared_utils import _collect_contract_refs, _shape_from_schema, _to_class_name
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


# ---------------------------------------------------------------------------
# Contract rendering
# ---------------------------------------------------------------------------

def _json_schema_to_swift(schema: dict) -> str:
    """Map a JSON Schema subschema (as emitted by type_to_json_schema) to a
    Swift type expression for struct field declarations.

    Handles the subset CLIO emits:
      - primitive types, enum, array, optional (anyOf), $ref, object/dict.
    Unrecognised shapes fall back to 'AnyCodable' — which won't compile; the
    caller (render_contracts_swift) is responsible for rejecting such schemas
    via validate_graph_for_swift before reaching this point.
    """
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _to_class_name(name)
    if "enum" in schema:
        return "String"
    # anyOf → Optional<T> pattern: {"anyOf": [<T-schema>, {"type": "null"}]}
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        null_b: dict | None = None
        inner_b: dict | None = None
        for branch in any_of:
            if isinstance(branch, dict) and branch.get("type") == "null":
                null_b = branch
            elif isinstance(branch, dict):
                inner_b = branch
        if null_b is not None and inner_b is not None:
            return f"{_json_schema_to_swift(inner_b)}?"
    t = schema.get("type")
    if t == "string":
        return "String"
    if t == "integer":
        return "Int"
    if t == "number":
        return "Double"
    if t == "boolean":
        return "Bool"
    if t == "array":
        items = schema.get("items", {})
        return f"[{_json_schema_to_swift(items)}]"
    if t == "object":
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            return f"[String: {_json_schema_to_swift(ap)}]"
        return "[String: Any]"
    return "Any"


def render_contracts_swift(graph: FlowGraph) -> str | None:
    """Render Sources/ClioFlow/Contracts.swift. Returns None when no contracts
    are referenced by any step in the graph.

    Emits one `struct <Name>: Codable, Sendable` per ContractIR, with:
      - fields from the CONTRACT SHAPE (via _json_schema_to_swift)
      - `static let jsonSchema` containing the full JSON Schema (incl. x-clio-assert)
      - `func validate() throws` that delegates to Validate.check
    """
    contracts_used: set[str] = set()
    for step in graph.steps:
        contracts_used |= _collect_contract_refs(step)
    if not contracts_used:
        return None

    contracts_by_name = {c.name: c for c in graph.contracts}

    parts: list[str] = [
        "// Auto-generated by CLIO. Do not edit by hand.",
        "import Foundation",
        "",
    ]

    for name in sorted(contracts_used):
        contract = contracts_by_name[name]
        struct_name = _to_class_name(name)

        parts.append(f"struct {struct_name}: Codable, Sendable {{")
        for fname, fschema in _shape_from_schema(contract.json_schema):
            swift_type = _json_schema_to_swift(fschema)
            parts.append(f"    var {fname}: {swift_type}")
        parts.append("")

        # Embed JSON Schema as a Swift multi-line string literal.
        # Each content line is prefixed with 4 spaces; the closing """ is also
        # at 4 spaces indent, so Swift strips exactly 4 spaces from each line,
        # recovering the original JSON string.
        schema_json = json.dumps(contract.json_schema, indent=2)
        indented_lines = "\n".join("    " + line for line in schema_json.splitlines())
        parts.append('    static let jsonSchema = """')
        parts.append(indented_lines)
        parts.append('    """')
        parts.append("")

        parts.append("    func validate() throws {")
        parts.append("        try Validate.check(self, against: Self.jsonSchema)")
        parts.append("    }")
        parts.append("}")
        parts.append("")

    return "\n".join(parts)
