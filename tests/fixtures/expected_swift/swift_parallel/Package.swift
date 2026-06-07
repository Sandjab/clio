// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "pipeline",
    platforms: [.macOS(.v12)],
    targets: [
        .target(name: "ClioFlow"),
        .executableTarget(name: "pipeline", dependencies: ["ClioFlow"]),
    ]
)
