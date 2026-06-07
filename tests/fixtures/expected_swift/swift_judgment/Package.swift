// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "classifier",
    platforms: [.macOS(.v12)],
    targets: [
        .target(name: "ClioFlow"),
        .executableTarget(name: "classifier", dependencies: ["ClioFlow"]),
    ]
)
