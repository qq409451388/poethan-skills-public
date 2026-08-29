import Foundation

enum PluginRepositoryError: LocalizedError {
    case invalid(String)
    case versionExists(String)
    var errorDescription: String? { switch self { case .invalid(let message), .versionExists(let message): message } }
}

struct PluginScanResult: Identifiable {
    let directory: URL
    let manifest: PluginManifest?
    let plugin: InspectionPlugin?
    let error: String?
    var id: String { directory.standardizedFileURL.path }
    var isValid: Bool { manifest != nil && plugin != nil && error == nil }
}

enum PluginRepository {
    static var dataRoot: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Poethan Sentinel", isDirectory: true)
    }
    static var defaultRoot: URL { dataRoot.appendingPathComponent("plugins", isDirectory: true) }

    static func ensureRoot(_ root: URL = defaultRoot) throws { try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true) }

    static func scanPackages(at root: URL) -> [PluginScanResult] {
        do { try ensureRoot(root) }
        catch { return [PluginScanResult(directory: root, manifest: nil, plugin: nil, error: "无法创建或读取插件目录：\(error.localizedDescription)")] }
        let candidates = packageCandidates(in: root)
        var results: [PluginScanResult] = []
        var seen = Set<String>()
        for directory in candidates {
            do {
                let manifest = try validatePackage(at: directory)
                let key = "\(manifest.id)@\(manifest.version)"
                guard seen.insert(key).inserted else {
                    results.append(PluginScanResult(directory: directory, manifest: manifest, plugin: nil, error: "插件 ID 与版本重复：\(key)。每个版本只能安装一次。"))
                    continue
                }
                results.append(PluginScanResult(directory: directory, manifest: manifest, plugin: template(at: directory, manifest: manifest), error: nil))
            } catch {
                results.append(PluginScanResult(directory: directory, manifest: nil, plugin: nil, error: error.localizedDescription))
            }
        }
        return results.sorted { lhs, rhs in
            let left = lhs.manifest?.name ?? lhs.directory.lastPathComponent
            let right = rhs.manifest?.name ?? rhs.directory.lastPathComponent
            return left.localizedStandardCompare(right) == .orderedAscending
        }
    }

    static func installedPackages(at root: URL) -> [InspectionPlugin] { scanPackages(at: root).compactMap(\.plugin) }

    static func importPackage(from source: URL, into root: URL) throws -> InspectionPlugin {
        let manifest = try validatePackage(at: source)
        try ensureRoot(root)
        if scanPackages(at: root).contains(where: { $0.manifest?.id == manifest.id && $0.manifest?.version == manifest.version }) { throw PluginRepositoryError.versionExists("插件 \(manifest.id)@\(manifest.version) 已存在。请提升 version 后再导入。") }
        let destination = root.appendingPathComponent(manifest.id, isDirectory: true).appendingPathComponent(manifest.version, isDirectory: true)
        guard !FileManager.default.fileExists(atPath: destination.path) else { throw PluginRepositoryError.versionExists("插件 \(manifest.id)@\(manifest.version) 已存在。请提升 plugin.yaml 中的 version 后重新导入。") }
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        do { try FileManager.default.copyItem(at: source, to: destination) }
        catch { throw PluginRepositoryError.invalid("复制插件包失败：\(error.localizedDescription)") }
        let copiedManifest = try validatePackage(at: destination)
        return template(at: destination, manifest: copiedManifest)
    }

    static func createScriptPackage(name: String, description: String, language: PluginLanguage, script: String, in root: URL) throws -> InspectionPlugin {
        let base = slug(name); var id = base; var suffix = 2
        let existingIDs = Set(scanPackages(at: root).compactMap { $0.manifest?.id })
        while existingIDs.contains(id) { id = "\(base)-\(suffix)"; suffix += 1 }
        let manifest = PluginManifest(id: id, name: name, description: description, version: "1.0.0", entrypoint: "run.sh", language: language.rawValue, outputLimit: 200_000, defaultMode: "standard", modes: [PluginModeDefinition(id: "standard", label: "标准 · Standard", help: "执行脚本并采集输出。")], configuration: PluginConfigurationDefinition(fields: [PluginFieldDefinition(key: "POETHAN_PLUGIN_HOME", label: "服务器插件目录", type: "path", section: "插件运行", defaultValue: "/opt/poethan-sentinel/plugins", required: true, help: "插件按 ID 和版本缓存到服务器。", placeholder: nil, options: nil)]), report: nil)
        try ensureRoot(root); let directory = root.appendingPathComponent(id).appendingPathComponent("1.0.0"); try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try encoder.encode(manifest).write(to: directory.appendingPathComponent("plugin.yaml"), options: .atomic)
        if language == .python {
            try script.write(to: directory.appendingPathComponent("main.py"), atomically: true, encoding: .utf8)
            try wrapper(command: "exec python3 \"$SCRIPT_DIR/main.py\" \"$MODE\"").write(to: directory.appendingPathComponent("run.sh"), atomically: true, encoding: .utf8)
        } else {
            try script.write(to: directory.appendingPathComponent("script.sh"), atomically: true, encoding: .utf8)
            try wrapper(command: "exec bash \"$SCRIPT_DIR/script.sh\" \"$MODE\"").write(to: directory.appendingPathComponent("run.sh"), atomically: true, encoding: .utf8)
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: directory.appendingPathComponent("run.sh").path)
        return template(at: directory, manifest: manifest)
    }

    static func validatePackage(at directory: URL) throws -> PluginManifest {
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory), isDirectory.boolValue else { throw PluginRepositoryError.invalid("请选择插件根目录，而不是单个文件。") }
        let manifestURL = directory.appendingPathComponent("plugin.yaml")
        guard FileManager.default.fileExists(atPath: manifestURL.path) else { throw PluginRepositoryError.invalid("插件根目录缺少 plugin.yaml。") }
        let manifest: PluginManifest
        do { manifest = try PluginYAMLDecoder.decode(Data(contentsOf: manifestURL)) }
        catch { throw PluginRepositoryError.invalid("plugin.yaml 格式错误：\(error.localizedDescription)") }
        guard manifest.id.range(of: "^[a-z0-9][a-z0-9.-]{1,63}$", options: .regularExpression) != nil else { throw PluginRepositoryError.invalid("id 只能使用小写字母、数字、点和连字符，长度 2～64。") }
        guard manifest.version.range(of: "^[0-9]+\\.[0-9]+\\.[0-9]+([+-][0-9A-Za-z.-]+)?$", options: .regularExpression) != nil else { throw PluginRepositoryError.invalid("version 必须使用语义化版本，例如 1.2.0。") }
        guard !manifest.modes.isEmpty, manifest.modes.contains(where: { $0.id == manifest.defaultMode }) else { throw PluginRepositoryError.invalid("modes 不能为空，且必须包含 defaultMode。") }
        guard safeFile(manifest.entrypoint, below: directory) != nil else { throw PluginRepositoryError.invalid("entrypoint 不存在或路径越过插件根目录。") }
        let supported = Set(["text", "path", "integer", "url", "password", "boolean", "choice"])
        var keys = Set<String>()
        for field in manifest.configuration.fields {
            guard field.key.range(of: "^[A-Z][A-Z0-9_]*$", options: .regularExpression) != nil else { throw PluginRepositoryError.invalid("配置字段 \(field.key) 不是合法环境变量名。") }
            guard keys.insert(field.key).inserted else { throw PluginRepositoryError.invalid("配置字段 \(field.key) 重复。") }
            guard supported.contains(field.type) else { throw PluginRepositoryError.invalid("字段 \(field.key) 使用了不支持的类型 \(field.type)。") }
            if field.type == "choice", field.options?.isEmpty != false { throw PluginRepositoryError.invalid("choice 字段 \(field.key) 必须提供 options。") }
        }
        if let report = manifest.report { guard safeFile(report.schema, below: directory) != nil, safeFile(report.template, below: directory) != nil else { throw PluginRepositoryError.invalid("报告 Schema 或模板不存在，或路径越过插件根目录。") }; guard (try? JSONSerialization.jsonObject(with: Data(contentsOf: directory.appendingPathComponent(report.schema)))) != nil else { throw PluginRepositoryError.invalid("插件报告 Schema 不是有效 JSON。") } }
        var bytes: Int64 = 0
        if let enumerator = FileManager.default.enumerator(at: directory, includingPropertiesForKeys: [.isSymbolicLinkKey, .isRegularFileKey, .fileSizeKey]) {
            for case let url as URL in enumerator { let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey, .fileSizeKey]); if values.isSymbolicLink == true { throw PluginRepositoryError.invalid("插件包不能包含符号链接：\(url.lastPathComponent)") }; if values.isRegularFile == true { bytes += Int64(values.fileSize ?? 0) } }
        }
        guard bytes <= 100 * 1024 * 1024 else { throw PluginRepositoryError.invalid("插件包超过 100 MB 限制。") }
        return manifest
    }

    private static func packageCandidates(in root: URL) -> [URL] {
        let children = directories(in: root)
        return children.flatMap { child -> [URL] in
            if FileManager.default.fileExists(atPath: child.appendingPathComponent("plugin.yaml").path) { return [child] }
            let nested = directories(in: child)
            let versionLayout = nested.contains { FileManager.default.fileExists(atPath: $0.appendingPathComponent("plugin.yaml").path) }
            return versionLayout ? nested : [child]
        }
    }
    private static func directories(in directory: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles])) ?? []).filter {
            (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
        }
    }
    private static func template(at directory: URL, manifest: PluginManifest) -> InspectionPlugin {
        InspectionPlugin(name: manifest.name, description: manifest.description ?? "", language: manifest.language == "python" ? .python : .bash, execution: .pluginPackage, outputLimit: manifest.outputLimit ?? 1_000_000, packageResource: nil, packagePath: directory.standardizedFileURL.path, packageVersion: manifest.version, manifestID: manifest.id, supportedModes: manifest.modes.map(\.id))
    }
    private static func safeFile(_ relative: String, below directory: URL) -> URL? { guard !relative.hasPrefix("/") else { return nil }; let root = directory.standardizedFileURL.path + "/"; let file = directory.appendingPathComponent(relative).standardizedFileURL; return file.path.hasPrefix(root) && FileManager.default.fileExists(atPath: file.path) ? file : nil }
    private static func slug(_ value: String) -> String { let lowered = value.lowercased(); let mapped = lowered.unicodeScalars.map { CharacterSet.alphanumerics.contains($0) && $0.isASCII ? Character(String($0)) : Character("-") }; let result = String(mapped).replacingOccurrences(of: "-+", with: "-", options: .regularExpression).trimmingCharacters(in: CharacterSet(charactersIn: "-")); return result.count >= 2 ? result : "script-plugin" }
    private static func wrapper(command: String) -> String { """
    #!/usr/bin/env bash
    set -uo pipefail
    MODE="${1:-standard}"
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    CONFIG_FILE="${POETHAN_CONFIG_FILE:-$SCRIPT_DIR/config.env}"
    if [[ -f "$CONFIG_FILE" ]]; then set -a; source "$CONFIG_FILE"; set +a; fi
    \(command)
    """ }
}

private enum PluginYAMLDecoder {
    static func decode(_ data: Data) throws -> PluginManifest {
        if let manifest = try? JSONDecoder().decode(PluginManifest.self, from: data) { return manifest }
        guard let text = String(data: data, encoding: .utf8) else { throw PluginRepositoryError.invalid("文件不是 UTF-8。") }
        return try decodeSimpleYAML(text)
    }

    private static func decodeSimpleYAML(_ text: String) throws -> PluginManifest {
        var top: [String: String] = [:], report: [String: String] = [:], modes: [[String: String]] = [], fields: [[String: String]] = [], fieldOptions: [Int: [[String: String]]] = [:]
        var section = "top", subsection = "", itemIndex: Int?, optionIndex: Int?, optionIndent = 0
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = String(raw); let trimmed = stripComment(line).trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed == "---" { continue }
            let indent = line.prefix { $0 == " " }.count
            if indent == 0 {
                itemIndex = nil; subsection = ""
                if trimmed == "modes:" { section = "modes"; continue }
                if trimmed == "configuration:" { section = "configuration"; continue }
                if trimmed == "report:" { section = "report"; continue }
                if trimmed.hasPrefix("modes:") { section = "top"; top["modes"] = scalar(afterColon: trimmed); continue }
                if let pair = pair(trimmed) { top[pair.0] = pair.1 }; continue
            }
            if section == "configuration", trimmed == "fields:" { subsection = "fields"; continue }
            if section == "modes" {
                if trimmed.hasPrefix("- ") { modes.append([:]); itemIndex = modes.count - 1; if let pair = pair(String(trimmed.dropFirst(2))) { modes[itemIndex!][pair.0] = pair.1 } }
                else if let index = itemIndex, let pair = pair(trimmed) { modes[index][pair.0] = pair.1 }
            } else if section == "configuration", subsection == "fields" {
                if trimmed.hasPrefix("- ") { fields.append([:]); itemIndex = fields.count - 1; optionIndex = nil; if let pair = pair(String(trimmed.dropFirst(2))) { fields[itemIndex!][pair.0] = pair.1 } }
                else if let index = itemIndex, let pair = pair(trimmed) { fields[index][pair.0] = pair.1 }
            } else if section == "configuration", subsection == "options", let fieldIndex = itemIndex {
                if trimmed.hasPrefix("- key:") { fields.append([:]); itemIndex = fields.count - 1; optionIndex = nil; subsection = "fields"; if let pair = pair(String(trimmed.dropFirst(2))) { fields[itemIndex!][pair.0] = pair.1 } }
                else if trimmed.hasPrefix("- ") { var options = fieldOptions[fieldIndex, default: []]; options.append([:]); optionIndex = options.count - 1; optionIndent = indent; if let pair = pair(String(trimmed.dropFirst(2))) { options[optionIndex!][pair.0] = pair.1 }; fieldOptions[fieldIndex] = options }
                else if let optionIndex, indent > optionIndent, let pair = pair(trimmed) { fieldOptions[fieldIndex]?[optionIndex][pair.0] = pair.1 }
            } else if section == "report", let pair = pair(trimmed) { report[pair.0] = pair.1 }
            if section == "configuration", trimmed == "options:", itemIndex != nil { subsection = "options" }
        }
        if modes.isEmpty, let inline = top["modes"] { modes = inline.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).split(separator: ",").map { ["id": clean(String($0)), "label": clean(String($0))] } }
        let modeDefinitions = modes.compactMap { dict -> PluginModeDefinition? in guard let id = dict["id"] else { return nil }; return PluginModeDefinition(id: id, label: dict["label"] ?? id, help: dict["help"]) }
        let fieldDefinitions = fields.enumerated().compactMap { index, dict -> PluginFieldDefinition? in guard let key = dict["key"] else { return nil }; let options = fieldOptions[index]?.compactMap { option -> PluginFieldOption? in guard let value = option["value"] else { return nil }; return PluginFieldOption(value: value, label: option["label"] ?? value) }; return PluginFieldDefinition(key: key, label: dict["label"] ?? key, type: dict["type"] ?? "text", section: dict["section"] ?? "配置", defaultValue: dict["default"], required: bool(dict["required"]), help: dict["help"], placeholder: dict["placeholder"], options: options) }
        guard let id = top["id"], let name = top["name"], let version = top["version"], let entrypoint = top["entrypoint"] else { throw PluginRepositoryError.invalid("缺少 id、name、version 或 entrypoint。") }
        let reportDefinition = report["schema"].flatMap { schema in report["template"].map { PluginReportDefinition(schema: schema, template: $0) } }
        return PluginManifest(id: id, name: name, description: top["description"], version: version, entrypoint: entrypoint, language: top["language"], outputLimit: top["outputLimit"].flatMap(Int.init), defaultMode: top["defaultMode"] ?? modeDefinitions.first?.id ?? "standard", modes: modeDefinitions, configuration: PluginConfigurationDefinition(fields: fieldDefinitions), report: reportDefinition)
    }
    private static func stripComment(_ line: String) -> String { var quote: Character?; for (index, char) in line.enumerated() { if char == "\"" || char == "'" { quote = quote == nil ? char : (quote == char ? nil : quote) }; if char == "#", quote == nil { return String(line.prefix(index)) } }; return line }
    private static func pair(_ value: String) -> (String, String)? { guard let colon = value.firstIndex(of: ":") else { return nil }; return (value[..<colon].trimmingCharacters(in: .whitespaces), clean(String(value[value.index(after: colon)...]))) }
    private static func scalar(afterColon value: String) -> String { guard let colon = value.firstIndex(of: ":") else { return "" }; return clean(String(value[value.index(after: colon)...])) }
    private static func clean(_ value: String) -> String { let text = value.trimmingCharacters(in: .whitespaces); if text.count >= 2, (text.hasPrefix("\"") && text.hasSuffix("\"")) || (text.hasPrefix("'") && text.hasSuffix("'")) { return String(text.dropFirst().dropLast()) }; return text }
    private static func bool(_ value: String?) -> Bool? { guard let value else { return nil }; return ["true", "yes", "on"].contains(value.lowercased()) }
}
