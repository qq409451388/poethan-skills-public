import XCTest
@testable import PoethanSentinel

final class PluginRepositoryTests: XCTestCase {
    func testValidatesStandardYAMLManifestAndChoiceOptions() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-plugin-test-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let yaml = """
        id: sample-check
        name: 示例检查
        description: 用于验证 YAML 导入
        version: 1.2.0
        entrypoint: run.sh
        language: bash
        defaultMode: standard
        modes:
          - id: standard
            label: 标准
        configuration:
          fields:
            - key: TARGET_PORT
              label: 目标端口
              type: integer
              section: 连接
              default: "8080"
              required: true
            - key: LEVEL
              label: 级别
              type: choice
              section: 检查
              options:
                - value: normal
                  label: 普通
                - value: deep
                  label: 深度
        report:
          schema: report-schema.json
          template: report.html
        """
        try yaml.write(to: root.appendingPathComponent("plugin.yaml"), atomically: true, encoding: .utf8)
        try "#!/usr/bin/env bash\necho ok\n".write(to: root.appendingPathComponent("run.sh"), atomically: true, encoding: .utf8)
        try "{\"type\":\"object\"}".write(to: root.appendingPathComponent("report-schema.json"), atomically: true, encoding: .utf8)
        try "<script>const report=__REPORT_JSON__;</script>".write(to: root.appendingPathComponent("report.html"), atomically: true, encoding: .utf8)

        let manifest = try PluginRepository.validatePackage(at: root)
        XCTAssertEqual(manifest.id, "sample-check")
        XCTAssertEqual(manifest.version, "1.2.0")
        XCTAssertEqual(manifest.configuration.fields.count, 2)
        XCTAssertEqual(manifest.configuration.fields[1].options?.map(\.value), ["normal", "deep"])
        XCTAssertEqual(manifest.report?.template, "report.html")
    }

    func testRejectsPackageWithoutManifest() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-plugin-test-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        XCTAssertThrowsError(try PluginRepository.validatePackage(at: root))
    }

    func testScanReportsInvalidManualPackageReason() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-plugin-scan-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let broken = root.appendingPathComponent("broken-plugin")
        try FileManager.default.createDirectory(at: broken, withIntermediateDirectories: true)
        try "name: 缺少关键字段\n".write(to: broken.appendingPathComponent("plugin.yaml"), atomically: true, encoding: .utf8)

        let results = PluginRepository.scanPackages(at: root)
        XCTAssertEqual(results.count, 1)
        XCTAssertFalse(results[0].isValid)
        XCTAssertTrue(results[0].error?.contains("plugin.yaml 格式错误") == true)
        XCTAssertTrue(results[0].error?.contains("缺少 id、name、version 或 entrypoint") == true)
    }

    func testScanSupportsDirectAndVersionedLayouts() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("sentinel-plugin-layout-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let direct = root.appendingPathComponent("direct")
        let versioned = root.appendingPathComponent("versioned/2.0.0")
        try makeMinimalPlugin(at: direct, id: "direct-plugin", version: "1.0.0")
        try makeMinimalPlugin(at: versioned, id: "versioned-plugin", version: "2.0.0")

        let results = PluginRepository.scanPackages(at: root)
        XCTAssertEqual(results.filter(\.isValid).count, 2)
        XCTAssertEqual(Set(results.compactMap { $0.manifest?.id }), Set(["direct-plugin", "versioned-plugin"]))
    }

    func testProjectExamplePluginsAreExternalAndValid() throws {
        let appRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let pluginRoot = appRoot.deletingLastPathComponent().appendingPathComponent("poethan-sentinel-plugins")
        let results = PluginRepository.scanPackages(at: pluginRoot)
        XCTAssertEqual(results.count, 3)
        XCTAssertTrue(results.allSatisfy(\.isValid), results.compactMap(\.error).joined(separator: "\n"))
        XCTAssertEqual(Set(results.compactMap { $0.manifest?.id }), Set(["doris-diagnostic", "host-performance", "network-diagnostic"]))
    }

    private func makeMinimalPlugin(at directory: URL, id: String, version: String) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let yaml = """
        id: \(id)
        name: \(id)
        version: \(version)
        entrypoint: run.sh
        language: bash
        defaultMode: standard
        modes:
          - id: standard
            label: 标准
        configuration:
          fields: []
        """
        try yaml.write(to: directory.appendingPathComponent("plugin.yaml"), atomically: true, encoding: .utf8)
        try "#!/usr/bin/env bash\necho ok\n".write(to: directory.appendingPathComponent("run.sh"), atomically: true, encoding: .utf8)
    }
}
