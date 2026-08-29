import SwiftUI
import AppKit

struct ServerEditorView: View {
    @EnvironmentObject private var store: SentinelStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var auth: AuthenticationKind = .sshAlias
    @State private var alias = ""
    @State private var host = ""
    @State private var user = ""
    @State private var port = 22
    @State private var identity = ""
    @State private var password = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("添加服务器").font(.title2.bold())
            Form {
                TextField("名称", text: $name)
                Picker("登录方式", selection: $auth) {
                    ForEach(AuthenticationKind.allCases) { Text($0.label).tag($0) }
                }
                if auth == .sshAlias {
                    TextField("SSH 别名", text: $alias)
                    Text("使用 ~/.ssh/config 中已有配置。").font(.caption).foregroundStyle(.secondary)
                } else {
                    TextField("主机 / 域名", text: $host)
                    TextField("用户名", text: $user)
                    TextField("端口", value: $port, format: .number)
                    if auth == .identityFile { TextField("私钥路径", text: $identity) }
                    else { SecureField("密码（仅存本机钥匙串）", text: $password) }
                }
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!isValid)
            }
        }.padding(24).frame(width: 540)
    }

    private var isValid: Bool { auth == .sshAlias ? !alias.trimmingCharacters(in: .whitespaces).isEmpty : !host.trimmingCharacters(in: .whitespaces).isEmpty && (1...65535).contains(port) }
    private func save() {
        let server = ServerProfile(name: name.isEmpty ? (auth == .sshAlias ? alias : host) : name, authentication: auth, alias: alias, host: host, user: user, port: port, identityFile: identity)
        if auth == .password, !password.isEmpty { Keychain.save(password, service: "dev.poethan.sentinel.ssh", account: server.id.uuidString) }
        store.servers.append(server); store.selectedServer = server.id; store.saveServers(); dismiss()
    }
}

struct PluginEditorView: View {
    @EnvironmentObject private var store: SentinelStore
    @Environment(\.dismiss) private var dismiss
    @State private var selection: String?
    @State private var creatingPlugin = false
    @State private var operationError: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("检查脚本").font(.title2.bold()); Spacer()
                Menu {
                    Button("新增脚本插件") { creatingPlugin = true }
                    Button("导入插件包 / 配置模板…") { chooseAndImportPackage() }
                } label: { Label("新增", systemImage: "plus") }
                Button { store.refreshPluginCatalog(); selectFirstIfNeeded() } label: { Label("重新扫描", systemImage: "arrow.clockwise") }
            }.padding(20)
            Divider()
            HStack(spacing: 0) {
                List(selection: $selection) {
                    ForEach(store.pluginScanResults) { result in
                        HStack(spacing: 8) {
                            Image(systemName: result.isValid ? "checkmark.circle.fill" : "xmark.octagon.fill")
                                .foregroundStyle(result.isValid ? Color.green : Color.red)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(result.manifest?.name ?? result.directory.lastPathComponent).lineLimit(1)
                                Text(result.isValid ? (result.manifest?.version ?? "") : "校验失败").font(.caption).foregroundStyle(.secondary)
                            }
                        }.tag(result.id)
                    }
                }.frame(width: 250)
                Divider()
                detail.frame(minWidth: 570, maxWidth: .infinity, maxHeight: .infinity)
            }
            Divider()
            HStack {
                Text("有效 \(store.pluginScanResults.filter(\.isValid).count) · 失败 \(store.pluginScanResults.filter { !$0.isValid }.count)").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(16)
        }.frame(width: 880, height: 650)
        .onAppear { store.refreshPluginCatalog(); selectFirstIfNeeded() }
        .sheet(isPresented: $creatingPlugin) { ScriptPluginCreatorView().environmentObject(store) }
        .alert("插件操作失败", isPresented: Binding(get: { operationError != nil }, set: { if !$0 { operationError = nil } })) { Button("好") { operationError = nil } } message: { Text(operationError ?? "未知错误") }
    }

    @ViewBuilder private var detail: some View {
        if let result = selectedResult {
            if let manifest = result.manifest, let plugin = store.plugins.first(where: { $0.packagePath == result.directory.standardizedFileURL.path }) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        HStack {
                            VStack(alignment: .leading, spacing: 4) { Text(manifest.name).font(.title2.bold()); Text(manifest.description ?? "无说明").foregroundStyle(.secondary) }
                            Spacer()
                            Toggle("启用", isOn: Binding(get: { plugin.enabled }, set: { store.setPluginEnabled(path: result.directory.standardizedFileURL.path, enabled: $0) })).toggleStyle(.switch)
                        }
                        GroupBox("plugin.yaml") {
                            VStack(alignment: .leading, spacing: 9) {
                                manifestValue("插件 ID", manifest.id); manifestValue("版本", manifest.version); manifestValue("入口", manifest.entrypoint)
                                manifestValue("语言", manifest.language ?? "bash"); manifestValue("运行模式", manifest.modes.map { "\($0.label) [\($0.id)]" }.joined(separator: "、")); manifestValue("默认模式", manifest.defaultMode)
                                manifestValue("最长输出", "\(manifest.outputLimit ?? 1_000_000) 字符"); manifestValue("报告", manifest.report.map { "Schema: \($0.schema) · 模板: \($0.template)" } ?? "使用 App 通用报告")
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                        GroupBox("配置表单字段") {
                            VStack(alignment: .leading, spacing: 10) {
                                if manifest.configuration.fields.isEmpty { Text("plugin.yaml 未声明配置字段。\n").foregroundStyle(.secondary) }
                                ForEach(manifest.configuration.fields) { field in
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(field.label).fontWeight(.medium)
                                        Text("\(field.key) · \(field.type) · \(field.section)\((field.required ?? false) ? " · 必填" : "")\(field.defaultValue.map { " · 默认 \($0)" } ?? "")").font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary).textSelection(.enabled)
                                        if let help = field.help { Text(help).font(.caption).foregroundStyle(.secondary) }
                                    }
                                }
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                        LabeledContent("插件目录") { Text(result.directory.path).font(.caption.monospaced()).lineLimit(2).textSelection(.enabled) }
                        HStack {
                            Button("打开插件目录") { NSWorkspace.shared.open(result.directory) }
                            Button("清除运行配置", role: .destructive) { store.deletePluginConfiguration(plugin) }.help("清除各服务器的填写值并停用该插件；不会删除本地插件包或服务器上的版本缓存。")
                        }
                    }.padding(20)
                }
            } else {
                VStack(alignment: .leading, spacing: 14) {
                    Label("插件校验失败", systemImage: "xmark.octagon.fill").font(.title2.bold()).foregroundStyle(.red)
                    Text(result.error ?? "未知错误").textSelection(.enabled)
                    LabeledContent("检查目录") { Text(result.directory.path).font(.caption.monospaced()).textSelection(.enabled) }
                    Text("修正 plugin.yaml 或缺失文件后点击“重新扫描”。无效插件不会出现在运行检查列表中。") .font(.caption).foregroundStyle(.secondary)
                    Button("打开目录") { NSWorkspace.shared.open(result.directory) }
                    Spacer()
                }.padding(24)
            }
        } else {
            VStack(spacing: 12) {
                Image(systemName: "puzzlepiece.extension").font(.largeTitle).foregroundStyle(.secondary)
                Text("插件目录中没有可检查的子目录").font(.title3)
                Text(store.pluginRoot.path).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                Button("打开插件目录") { try? PluginRepository.ensureRoot(store.pluginRoot); NSWorkspace.shared.open(store.pluginRoot) }
            }
        }
    }
    private var selectedResult: PluginScanResult? { store.pluginScanResults.first { $0.id == selection } }
    private func manifestValue(_ label: String, _ value: String) -> some View { LabeledContent(label) { Text(value).font(.system(.caption, design: .monospaced)).textSelection(.enabled) } }
    private func selectFirstIfNeeded() {
        if selection.flatMap({ id in store.pluginScanResults.first { $0.id == id } }) == nil { selection = store.pluginScanResults.first?.id }
    }
    private func chooseAndImportPackage() {
        let panel = NSOpenPanel(); panel.title = "选择插件根目录"; panel.prompt = "导入"; panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let source = panel.url else { return }
        do { let plugin = try PluginRepository.importPackage(from: source, into: store.pluginRoot); store.refreshPluginCatalog(); selection = plugin.packagePath }
        catch { operationError = error.localizedDescription }
    }
}

private struct ScriptPluginCreatorView: View {
    @EnvironmentObject private var store: SentinelStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = "新检查脚本"
    @State private var description = ""
    @State private var language = PluginLanguage.python
    @State private var script = ""
    @State private var errorMessage: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("新增脚本插件").font(.title2.bold())
            Form {
                TextField("名称", text: $name)
                TextField("说明", text: $description)
                Picker("语言", selection: $language) { ForEach(PluginLanguage.allCases) { Text($0.label).tag($0) } }
                TextEditor(text: $script).font(.system(.body, design: .monospaced)).frame(minHeight: 280).border(Color.secondary.opacity(0.25))
                Text("保存后会在插件目录中生成完整插件包和 plugin.yaml，管理页再从清单重新读取所有属性。") .font(.caption).foregroundStyle(.secondary)
            }
            HStack { Spacer(); Button("取消") { dismiss() }; Button("创建") { create() }.buttonStyle(.borderedProminent).disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || script.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
        }.padding(24).frame(width: 620, height: 520)
        .alert("创建失败", isPresented: Binding(get: { errorMessage != nil }, set: { if !$0 { errorMessage = nil } })) { Button("好") { errorMessage = nil } } message: { Text(errorMessage ?? "未知错误") }
    }
    private func create() {
        do { _ = try PluginRepository.createScriptPackage(name: name, description: description, language: language, script: script, in: store.pluginRoot); store.refreshPluginCatalog(); dismiss() }
        catch { errorMessage = error.localizedDescription }
    }
}

struct PluginRunConfigurationView: View {
    @EnvironmentObject private var store: SentinelStore
    @Environment(\.dismiss) private var dismiss
    let server: ServerProfile
    let plugin: InspectionPlugin
    @State private var configuration = PluginRunConfiguration()
    @State private var manifest: PluginManifest?
    @State private var secrets: [String: String] = [:]
    @State private var storedSecrets: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("\(plugin.name) · \(server.name)").font(.title2.bold())
            if let manifest {
                Form {
                    Picker("运行模式", selection: $configuration.mode) { ForEach(manifest.modes) { Text($0.label).tag($0.id) } }
                    if let help = manifest.modes.first(where: { $0.id == configuration.mode })?.help { Text(help).font(.caption).foregroundStyle(.secondary) }
                    ForEach(sections(in: manifest), id: \.self) { section in
                        Section(section) { ForEach(manifest.configuration.fields.filter { $0.section == section }) { fieldRow($0) } }
                    }
                }
                HStack { Spacer(); Button("取消") { dismiss() }.keyboardShortcut(.cancelAction); Button("保存") { save(manifest) }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!isValid(manifest)) }
            } else {
                Text("无法读取插件包中的 plugin.yaml，不能生成配置表单。").foregroundStyle(.red)
                HStack { Spacer(); Button("关闭") { dismiss() } }
            }
        }.padding(24).frame(width: 620)
        .onAppear { load() }
    }

    private func value(_ key: String, default defaultValue: String = "") -> Binding<String> { Binding(get: { configuration.values[key] ?? defaultValue }, set: { configuration.values[key] = $0 }) }
    private func boolValue(_ key: String) -> Binding<Bool> { Binding(get: { configuration.values[key, default: "false"] == "true" }, set: { configuration.values[key] = $0 ? "true" : "false" }) }
    private func secretBinding(_ key: String) -> Binding<String> { Binding(get: { secrets[key, default: ""] }, set: { secrets[key] = $0 }) }
    private func secretAccount(_ key: String) -> String { "\(server.id.uuidString):\(plugin.id.uuidString):\(key)" }
    private func sections(in manifest: PluginManifest) -> [String] { manifest.configuration.fields.reduce(into: []) { if !$0.contains($1.section) { $0.append($1.section) } } }
    @ViewBuilder private func fieldRow(_ field: PluginFieldDefinition) -> some View {
        if field.type == "boolean" { Toggle(field.label, isOn: boolValue(field.key)) }
        else if field.type == "password" { SecureField(storedSecrets.contains(field.key) ? "\(field.label)（已保存，留空不修改）" : field.label, text: secretBinding(field.key)) }
        else if field.type == "choice", let options = field.options { Picker(field.label, selection: value(field.key, default: field.defaultValue ?? options.first?.value ?? "")) { ForEach(options) { Text($0.label).tag($0.value) } } }
        else { LabeledContent(field.label) { TextField(field.placeholder ?? "", text: value(field.key, default: field.defaultValue ?? "")) } }
        if let help = field.help { Text(help).font(.caption).foregroundStyle(.secondary) }
    }
    private func load() {
        guard let loaded = PluginManifestLoader.load(plugin: plugin) else { return }
        manifest = loaded; configuration = store.configuration(serverID: server.id, pluginID: plugin.id)
        if !loaded.modes.contains(where: { $0.id == configuration.mode }) { configuration.mode = loaded.defaultMode }
        for field in loaded.configuration.fields where field.type == "password" { if Keychain.value(service: "dev.poethan.sentinel.plugin-secret", account: secretAccount(field.key)) != nil { storedSecrets.insert(field.key) } }
    }
    private func isValid(_ manifest: PluginManifest) -> Bool {
        manifest.configuration.fields.filter { $0.required == true }.allSatisfy { field in
            if field.type == "password" { return storedSecrets.contains(field.key) || !(secrets[field.key] ?? "").isEmpty }
            return !(configuration.values[field.key] ?? field.defaultValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }
    private func save(_ manifest: PluginManifest) {
        for field in manifest.configuration.fields where field.type == "password" { let secret = secrets[field.key] ?? ""; if !secret.isEmpty { Keychain.save(secret, service: "dev.poethan.sentinel.plugin-secret", account: secretAccount(field.key)) }; configuration.values.removeValue(forKey: field.key) }
        store.runConfigurations[store.configurationKey(serverID: server.id, pluginID: plugin.id)] = configuration; store.saveRunConfigurations(); dismiss()
    }
}

struct SettingsView: View {
    @EnvironmentObject private var store: SentinelStore
    @Environment(\.dismiss) private var dismiss
    @State private var draft = AISettings()
    @State private var apiKey = ""
    @State private var hasStoredKey = false
    @State private var testingAI = false
    @State private var aiTestSucceeded = false
    @State private var aiTestMessage: String?
    @State private var aiTestRawResponse: String?
    @State private var pluginDirectory = ""
    @State private var pluginDirectoryMessage: String?
    @State private var cacheSize: Int64 = 0
    @State private var confirmClearCache = false
    @State private var cacheMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("设置").font(.title2.bold())
            Form {
                Section("AI 增强分析") {
                    TextField("接口地址", text: $draft.endpoint)
                    TextField("模型", text: $draft.model)
                    SecureField(hasStoredKey ? "API Key（已保存，留空则不修改）" : "API Key", text: $apiKey)
                    Text("启用增强时，所选脚本的原始输出会发送给配置的接口。API Key 仅存本机钥匙串。").font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("测试连接") { testAIConnection() }.disabled(testingAI || !aiConnectionFieldsValid)
                        if testingAI { ProgressView().controlSize(.small); Text("正在等待模型回复 OK…").font(.caption).foregroundStyle(.secondary) }
                        else if let aiTestMessage { Image(systemName: aiTestSucceeded ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(aiTestSucceeded ? Color.green : Color.red); Text(aiTestMessage).font(.caption).foregroundStyle(aiTestSucceeded ? Color.green : Color.red).textSelection(.enabled) }
                    }
                    if let aiTestRawResponse {
                        DisclosureGroup("查看原始响应") { ScrollView { Text(aiTestRawResponse).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.top, 6) }.frame(maxHeight: 220) }
                    }
                }
                Section("插件目录") {
                    LabeledContent("位置") { TextField("插件目录", text: $pluginDirectory).font(.caption.monospaced()) }
                    HStack {
                        Button("选择…") { choosePluginDirectory() }
                        Button("打开目录") { openPluginDirectory() }
                        Button("重新扫描") { applyPluginDirectory(); pluginDirectoryMessage = scanSummary }
                        if let pluginDirectoryMessage { Text(pluginDirectoryMessage).font(.caption).foregroundStyle(.secondary) }
                    }
                    Text("App 只扫描这个外部目录，不内置检查插件。支持 <插件>/plugin.yaml 或 <id>/<version>/plugin.yaml；导入和新建插件也会写入这里。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("本机缓存") {
                    LabeledContent("缓存占用") { Text(ByteCountFormatter.string(fromByteCount: cacheSize, countStyle: .file)).monospacedDigit() }
                    Text("包含下载的诊断结果包、AI 报告 JSON 和生成的 HTML。不会删除服务器 /opt 插件缓存、服务器配置或钥匙串。")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack { Button("重新统计") { refreshCacheSize() }; Button("清空缓存", role: .destructive) { confirmClearCache = true }.disabled(cacheSize == 0); if let cacheMessage { Text(cacheMessage).font(.caption).foregroundStyle(.secondary) } }
                }
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!isValid)
            }
        }
        .padding(24).frame(width: 580)
        .onAppear { draft = store.aiSettings; pluginDirectory = store.pluginDirectory; hasStoredKey = Keychain.value(service: "dev.poethan.sentinel.ai", account: "api-key") != nil; refreshCacheSize() }
        .onChange(of: draft.endpoint) { _ in resetAITest() }
        .onChange(of: draft.model) { _ in resetAITest() }
        .onChange(of: apiKey) { _ in resetAITest() }
        .confirmationDialog("清空本机缓存？", isPresented: $confirmClearCache, titleVisibility: .visible) {
            Button("清空缓存", role: .destructive) { clearCache() }
            Button("取消", role: .cancel) {}
        } message: { Text("只删除可重新生成的诊断结果与报告文件。") }
    }

    private var isValid: Bool { URL(string: draft.endpoint) != nil && !draft.model.trimmingCharacters(in: .whitespaces).isEmpty && !pluginDirectory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var aiConnectionFieldsValid: Bool { URL(string: draft.endpoint) != nil && !draft.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && (hasStoredKey || !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) }
    private func save() { draft.enabled = true; store.aiSettings = draft; store.saveAISettings(); if !apiKey.isEmpty { Keychain.save(apiKey, service: "dev.poethan.sentinel.ai", account: "api-key") }; applyPluginDirectory(); dismiss() }
    private func refreshCacheSize() { cacheSize = CacheManager.size(); cacheMessage = nil }
    private func clearCache() { do { try CacheManager.clear(); cacheSize = CacheManager.size(); cacheMessage = "已清空" } catch { cacheMessage = "清理失败：\(error.localizedDescription)" } }
    private var editedPluginRoot: URL { URL(fileURLWithPath: (pluginDirectory as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL }
    private var scanSummary: String { "有效 \(store.pluginScanResults.filter(\.isValid).count)，失败 \(store.pluginScanResults.filter { !$0.isValid }.count)" }
    private func applyPluginDirectory() { store.setPluginDirectory(pluginDirectory); pluginDirectory = store.pluginDirectory }
    private func choosePluginDirectory() {
        let panel = NSOpenPanel(); panel.title = "选择插件目录"; panel.prompt = "选择"; panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true; panel.allowsMultipleSelection = false; panel.directoryURL = editedPluginRoot
        if panel.runModal() == .OK, let url = panel.url { pluginDirectory = url.path; applyPluginDirectory(); pluginDirectoryMessage = scanSummary }
    }
    private func openPluginDirectory() { do { try PluginRepository.ensureRoot(editedPluginRoot); NSWorkspace.shared.open(editedPluginRoot) } catch { pluginDirectoryMessage = "无法打开：\(error.localizedDescription)" } }
    private func resetAITest() { if !testingAI { aiTestSucceeded = false; aiTestMessage = nil; aiTestRawResponse = nil } }
    private func testAIConnection() {
        testingAI = true; aiTestSucceeded = false; aiTestMessage = nil; aiTestRawResponse = nil
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : apiKey
        Task {
            do { let raw = try await AIAnalyzer.testConnection(settings: draft, apiKey: key); aiTestSucceeded = true; aiTestMessage = "连接成功，已收到服务响应"; aiTestRawResponse = raw }
            catch { aiTestSucceeded = false; aiTestMessage = error.localizedDescription }
            testingAI = false
        }
    }
}
