// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PoethanSentinel",
    platforms: [.macOS(.v13)],
    targets: [.executableTarget(name: "PoethanSentinel", resources: [
        .process("Resources/report-schema.json"),
        .process("Resources/report-template.html")
    ]), .testTarget(name: "PoethanSentinelTests", dependencies: ["PoethanSentinel"])]
)
