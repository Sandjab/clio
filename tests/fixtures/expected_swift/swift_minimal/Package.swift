// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "minimal",
    targets: [
        .target(name: "ClioFlow"),
        .executableTarget(name: "minimal", dependencies: ["ClioFlow"]),
    ]
)
