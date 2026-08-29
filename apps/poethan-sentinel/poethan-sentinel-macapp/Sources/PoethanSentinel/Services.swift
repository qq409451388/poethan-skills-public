import Foundation
import Security
import AppKit

enum CacheManager {
    static var root: URL { FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("Poethan Sentinel", isDirectory: true) }
    static func size() -> Int64 {
        guard let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey], options: [.skipsHiddenFiles]) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in enumerator { if let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]), values.isRegularFile == true { total += Int64(values.fileSize ?? 0) } }
        return total
    }
    static func clear() throws {
        guard FileManager.default.fileExists(atPath: root.path) else { return }
        for item in try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil) { try FileManager.default.removeItem(at: item) }
    }
    static func storeRunArchive(_ source: URL) {
        let directory = root.appendingPathComponent("runs", isDirectory: true); try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? FileManager.default.copyItem(at: source, to: directory.appendingPathComponent("run-\(UUID().uuidString).tgz"))
    }
    static func storeAIReport(_ report: DiagnosticReport) {
        let directory = root.appendingPathComponent("ai-reports", isDirectory: true); try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let data = try? JSONEncoder.pretty.encode(report) { try? data.write(to: directory.appendingPathComponent("ai-\(UUID().uuidString).json"), options: .atomic) }
    }
    static func reportFile() -> URL {
        let directory = root.appendingPathComponent("reports", isDirectory: true); try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("report-\(UUID().uuidString).html")
    }
}

enum Keychain {
    static func save(_ value: String, service: String, account: String) { let query = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account] as CFDictionary; SecItemDelete(query); SecItemAdd([kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account, kSecValueData: Data(value.utf8)] as CFDictionary, nil) }
    static func value(service: String, account: String) -> String? { var item: CFTypeRef?; let query = [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account, kSecReturnData: true] as CFDictionary; guard SecItemCopyMatching(query, &item) == errSecSuccess, let data = item as? Data else { return nil }; return String(data: data, encoding: .utf8) }
    static func delete(service: String, account: String) { SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: account] as CFDictionary) }
}

enum SSHRunner {
    static func run(server: ServerProfile, plugin: InspectionPlugin, configuration: PluginRunConfiguration) async -> PluginOutput {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                if plugin.execution == .pluginPackage { continuation.resume(returning: PackageRunner.run(server: server, plugin: plugin, configuration: configuration)); return }
                do {
                    let session = try RemoteSession(server: server)
                    let input = plugin.execution == .sendToStandardInput ? Data(plugin.script.utf8) : nil
                    let result = try session.ssh(command: plugin.remoteCommand, input: input)
                    continuation.resume(returning: PluginOutput(pluginID: plugin.id, pluginName: plugin.name, exitCode: result.status, text: String(result.output.prefix(max(1000, plugin.outputLimit)))))
                } catch { continuation.resume(returning: PluginOutput(pluginID: plugin.id, pluginName: plugin.name, exitCode: 1, text: "SSH 检查失败：\(error.localizedDescription)")) }
            }
        }
    }
}

private final class RemoteSession {
    let server: ServerProfile
    private var askPassURL: URL?
    private var environment: [String: String]?
    init(server: ServerProfile) throws {
        self.server = server
        if server.authentication == .password {
            guard Keychain.value(service: "dev.poethan.sentinel.ssh", account: server.id.uuidString) != nil else { throw NSError(domain: "PoethanSentinel", code: 1, userInfo: [NSLocalizedDescriptionKey: "该服务器尚未在钥匙串中保存密码。"] ) }
            let file = FileManager.default.temporaryDirectory.appendingPathComponent("poethan-sentinel-askpass-\(UUID().uuidString)")
            let script = "#!/bin/sh\n/usr/bin/security find-generic-password -s 'dev.poethan.sentinel.ssh' -a '\(server.id.uuidString)' -w\n"
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
            askPassURL = file
            var env = ProcessInfo.processInfo.environment; env["SSH_ASKPASS"] = file.path; env["SSH_ASKPASS_REQUIRE"] = "force"; env["DISPLAY"] = "poethan-sentinel"; environment = env
        }
    }
    deinit { if let askPassURL { try? FileManager.default.removeItem(at: askPassURL) } }
    func ssh(command: String, input: Data? = nil) throws -> (status: Int32, output: String) { try execute("/usr/bin/ssh", arguments: sshOptions(portFlag: "-p") + [server.target, command], input: input) }
    func scp(local: URL, remotePath: String, download: Bool = false) throws -> (status: Int32, output: String) {
        let remote = "\(server.target):\(remotePath)"
        return try execute("/usr/bin/scp", arguments: sshOptions(portFlag: "-P") + (download ? [remote, local.path] : [local.path, remote]))
    }
    private func sshOptions(portFlag: String) -> [String] {
        var args = ["-o", "ConnectTimeout=12", "-o", "BatchMode=no", portFlag, String(server.port)]
        if server.authentication == .identityFile, !server.identityFile.isEmpty { args += ["-i", server.identityFile] }
        if server.authentication == .password { args += ["-o", "PreferredAuthentications=password,keyboard-interactive", "-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1"] }
        return args
    }
    private func execute(_ executable: String, arguments: [String], input: Data? = nil) throws -> (status: Int32, output: String) {
        let process = Process(); process.executableURL = URL(fileURLWithPath: executable); process.arguments = arguments; if let environment { process.environment = environment }
        if let input { let pipe = Pipe(); pipe.fileHandleForWriting.write(input); try? pipe.fileHandleForWriting.close(); process.standardInput = pipe }
        let output = Pipe(); process.standardOutput = output; process.standardError = output
        try process.run(); let data = output.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
        return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }
}

private enum PackageRunner {
    static func run(server: ServerProfile, plugin: InspectionPlugin, configuration: PluginRunConfiguration) -> PluginOutput {
        let result: (Int32, String)
        do { result = try execute(server: server, plugin: plugin, configuration: configuration) }
        catch { result = (1, "插件包执行失败：\(error.localizedDescription)") }
        return PluginOutput(pluginID: plugin.id, pluginName: plugin.name, exitCode: result.0, text: String(result.1.prefix(max(1000, plugin.outputLimit))))
    }
    private static func execute(server: ServerProfile, plugin: InspectionPlugin, configuration: PluginRunConfiguration) throws -> (Int32, String) {
        guard let manifest = PluginManifestLoader.load(plugin: plugin), let source = PluginManifestLoader.packageDirectory(plugin) else { throw NSError(domain: "PoethanSentinel", code: 2, userInfo: [NSLocalizedDescriptionKey: "找不到或无法解析插件包清单。"] ) }
        let version = manifest.version
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("poethan-sentinel-package-\(UUID().uuidString)"); defer { try? FileManager.default.removeItem(at: root) }
        let stage = root.appendingPathComponent("plugin"); try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true); try FileManager.default.copyItem(at: source, to: stage)
        var values = configuration.values
        let pluginHome = values.removeValue(forKey: "POETHAN_PLUGIN_HOME") ?? "/opt/poethan-sentinel/plugins"
        for field in manifest.configuration.fields where field.type == "password" {
            let account = "\(server.id.uuidString):\(plugin.id.uuidString):\(field.key)"
            if let secret = Keychain.value(service: "dev.poethan.sentinel.plugin-secret", account: account) { values[field.key] = secret }
        }
        let config = values.sorted(by: { $0.key < $1.key }).map { "\($0.key)=\(shellQuote($0.value))" }.joined(separator: "\n") + "\n"
        let localConfig = root.appendingPathComponent("config.env"); try config.write(to: localConfig, atomically: true, encoding: .utf8)
        let archive = root.appendingPathComponent("plugin.tgz"); let localResult = root.appendingPathComponent("result.tgz")
        let tar = try localProcess("/usr/bin/tar", ["-czf", archive.path, "-C", root.path, "plugin"]); guard tar.0 == 0 else { throw NSError(domain: "PoethanSentinel", code: 3, userInfo: [NSLocalizedDescriptionKey: tar.1]) }
        let session = try RemoteSession(server: server)
        let remoteRun = "/tmp/poethan-sentinel-run-\(UUID().uuidString)"
        let remotePlugin = "\(pluginHome)/\(manifest.id)/\(version)"
        let remoteParent = "\(pluginHome)/\(manifest.id)"
        let createRun = try session.ssh(command: "mkdir -p \(shellQuote(remoteRun))"); guard createRun.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 4, userInfo: [NSLocalizedDescriptionKey: createRun.output]) }
        defer { _ = try? session.ssh(command: "rm -rf \(shellQuote(remoteRun))") }
        let configUpload = try session.scp(local: localConfig, remotePath: "\(remoteRun)/config.env"); guard configUpload.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 5, userInfo: [NSLocalizedDescriptionKey: configUpload.output]) }
        let cached = try session.ssh(command: "test -x \(shellQuote(remotePlugin + "/" + manifest.entrypoint))").status == 0
        if !cached {
            let upload = try session.scp(local: archive, remotePath: "\(remoteRun)/plugin.tgz"); guard upload.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 6, userInfo: [NSLocalizedDescriptionKey: upload.output]) }
            let temporaryInstall = "\(remoteParent)/.\(version).installing-\(UUID().uuidString)"
            let temporaryEntrypoint = temporaryInstall + "/" + manifest.entrypoint
            let install = "set -e; if mkdir -p \(shellQuote(remoteParent)) 2>/dev/null && test -w \(shellQuote(remoteParent)); then rm -rf \(shellQuote(remotePlugin)); mkdir -p \(shellQuote(temporaryInstall)); tar -xzf \(shellQuote(remoteRun + "/plugin.tgz")) -C \(shellQuote(temporaryInstall)) --strip-components=1; chmod -R a+rX \(shellQuote(temporaryInstall)); chmod a+rx \(shellQuote(temporaryEntrypoint)); mv \(shellQuote(temporaryInstall)) \(shellQuote(remotePlugin)); else sudo -n mkdir -p \(shellQuote(remoteParent)); sudo -n rm -rf \(shellQuote(remotePlugin)); sudo -n mkdir -p \(shellQuote(temporaryInstall)); sudo -n tar -xzf \(shellQuote(remoteRun + "/plugin.tgz")) -C \(shellQuote(temporaryInstall)) --strip-components=1; sudo -n chmod -R a+rX \(shellQuote(temporaryInstall)); sudo -n chmod a+rx \(shellQuote(temporaryEntrypoint)); sudo -n mv \(shellQuote(temporaryInstall)) \(shellQuote(remotePlugin)); fi"
            let installed = try session.ssh(command: install); guard installed.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 7, userInfo: [NSLocalizedDescriptionKey: "无法安装插件到 \(remotePlugin)。请给当前账号该目录写权限、配置免密 sudo，或改用用户可写目录。\n\(installed.output)"]) }
        }
        let mode = manifest.modes.contains(where: { $0.id == configuration.mode }) ? configuration.mode : manifest.defaultMode
        let entrypoint = remotePlugin + "/" + manifest.entrypoint
        let command = "mkdir -p \(shellQuote(remoteRun + "/result")); chmod 600 \(shellQuote(remoteRun + "/config.env")); POETHAN_CONFIG_FILE=\(shellQuote(remoteRun + "/config.env")) POETHAN_RESULT_DIR=\(shellQuote(remoteRun + "/result")) \(shellQuote(entrypoint)) \(shellQuote(mode)) > \(shellQuote(remoteRun + "/result/diagnostic.txt")) 2> \(shellQuote(remoteRun + "/result/stderr.txt")); code=$?; printf '%s' \"$code\" > \(shellQuote(remoteRun + "/result/exit-code")); tar -czf \(shellQuote(remoteRun + "/result.tgz")) -C \(shellQuote(remoteRun + "/result")) .; exit 0"
        let executed = try session.ssh(command: command); guard executed.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 8, userInfo: [NSLocalizedDescriptionKey: executed.output]) }
        let download = try session.scp(local: localResult, remotePath: "\(remoteRun)/result.tgz", download: true); guard download.status == 0 else { throw NSError(domain: "PoethanSentinel", code: 9, userInfo: [NSLocalizedDescriptionKey: download.output]) }
        CacheManager.storeRunArchive(localResult)
        let resultDirectory = root.appendingPathComponent("result"); try FileManager.default.createDirectory(at: resultDirectory, withIntermediateDirectories: true); let unpack = try localProcess("/usr/bin/tar", ["-xzf", localResult.path, "-C", resultDirectory.path]); guard unpack.0 == 0 else { throw NSError(domain: "PoethanSentinel", code: 10, userInfo: [NSLocalizedDescriptionKey: unpack.1]) }
        let diagnostic = (try? String(contentsOf: resultDirectory.appendingPathComponent("diagnostic.txt"))) ?? ""; let stderr = (try? String(contentsOf: resultDirectory.appendingPathComponent("stderr.txt"))) ?? ""; let code = Int32((try? String(contentsOf: resultDirectory.appendingPathComponent("exit-code")))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "1") ?? 1
        let deployment = "===== SECTION: PLUGIN_DEPLOYMENT =====\nplugin_id=\(manifest.id)\nplugin_version=\(version)\nremote_directory=\(remotePlugin)\ndeployment=\(cached ? "cached" : "installed")\n\n"
        return (code, deployment + diagnostic + (stderr.isEmpty ? "" : "\n\n===== SECTION: STDERR =====\n" + stderr))
    }
    private static func localProcess(_ executable: String, _ args: [String]) throws -> (Int32, String) { let process = Process(); process.executableURL = URL(fileURLWithPath: executable); process.arguments = args; let pipe = Pipe(); process.standardOutput = pipe; process.standardError = pipe; try process.run(); let data = pipe.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit(); return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "") }
}

enum LocalReport {
    static func make(server: ServerProfile, outputs: [PluginOutput]) -> DiagnosticReport {
        let failed = outputs.filter { $0.exitCode != 0 }
        let findings = failed.map { Finding(severity: "warning", title: "\($0.pluginName) 未正常结束", evidence: "退出码 \($0.exitCode)。", recommendation: "打开原始输出检查脚本依赖、权限、解释器和连接配置。") }
        let summary = failed.isEmpty ? "已采集 \(outputs.count) 项检查输出，等待查看或交由 AI 增强分析。" : "有 \(failed.count) 项检查未正常结束，报告保留了完整输出供排查。"
        return DiagnosticReport(server: server.name, summary: summary, findings: findings, outputs: outputs)
    }
}

enum ReportExporter {
    static func copy(_ text: String) { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) }
    static func rawText(_ report: DiagnosticReport) -> String { report.outputs.map { "===== \($0.pluginName) · exit \($0.exitCode) =====\n\($0.text)" }.joined(separator: "\n\n") }
    static func openHTML(_ report: DiagnosticReport, plugin: InspectionPlugin? = nil) -> String? {
        var renderedReport = report
        if let plugin { renderedReport.outputs = report.outputs.filter { $0.pluginID == plugin.id } }
        let files: (schema: URL, template: URL)
        if let plugin,
           let manifest = PluginManifestLoader.load(plugin: plugin),
           let definition = manifest.report,
           let pluginFiles = PluginManifestLoader.reportFiles(plugin: plugin, definition: definition) { files = pluginFiles }
        else if let schema = Bundle.main.url(forResource: "report-schema", withExtension: "json"), let template = Bundle.main.url(forResource: "report-template", withExtension: "html") { files = (schema, template) }
        else { return "找不到报告 Schema 或 HTML 模板。" }
        guard let data = try? JSONEncoder.pretty.encode(renderedReport),
              let schemaData = try? Data(contentsOf: files.schema),
              let template = try? String(contentsOf: files.template),
              let json = String(data: data, encoding: .utf8),
              let schemaJSON = String(data: schemaData, encoding: .utf8) else { return "无法读取或编码报告资源。" }
        if let error = validate(reportData: data, schemaData: schemaData) { return "JSON Schema 校验失败：\(error)" }
        let escapedReport = json.replacingOccurrences(of: "</script>", with: "<\\/script>")
        let escapedSchema = schemaJSON.replacingOccurrences(of: "</script>", with: "<\\/script>")
        let html = template.replacingOccurrences(of: "__REPORT_JSON__", with: escapedReport).replacingOccurrences(of: "__REPORT_SCHEMA__", with: escapedSchema)
        let file = CacheManager.reportFile()
        do { try html.write(to: file, atomically: true, encoding: .utf8); NSWorkspace.shared.open(file); return nil }
        catch { return "无法生成 HTML 报告：\(error.localizedDescription)" }
    }
    private static func validate(reportData: Data, schemaData: Data) -> String? {
        guard let report = try? JSONSerialization.jsonObject(with: reportData) as? [String: Any], let schema = try? JSONSerialization.jsonObject(with: schemaData) as? [String: Any] else { return "Schema 不是有效 JSON。" }
        for key in schema["required"] as? [String] ?? [] where report[key] == nil { return "缺少必填字段 \(key)" }
        if let properties = schema["properties"] as? [String: Any], let version = properties["schemaVersion"] as? [String: Any], let expected = version["const"] as? String, report["schemaVersion"] as? String != expected { return "schemaVersion 应为 \(expected)" }
        return nil
    }
}
extension JSONEncoder { static var pretty: JSONEncoder { let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]; encoder.dateEncodingStrategy = .iso8601; return encoder } }
