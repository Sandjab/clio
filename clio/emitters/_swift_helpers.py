"""target: swift — graph-bound renderers + compile-time validation."""
from __future__ import annotations

from clio.ir.graph import FlowGraph

E_SWIFT_004 = "E_SWIFT_004: source declares no FLOW; nothing to orchestrate."


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
