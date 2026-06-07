// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "summarizer",
    platforms: [.macOS(.v12)],
    targets: [
        .target(name: "ClioFlow"),
        .executableTarget(name: "summarizer", dependencies: ["ClioFlow"]),
    ]
)
