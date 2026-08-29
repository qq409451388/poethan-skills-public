import SwiftUI

@main struct PoethanSentinelApp: App { @StateObject private var store = SentinelStore(); var body: some Scene { WindowGroup { ContentView().environmentObject(store) }.defaultSize(width: 1100, height: 720); Settings { SettingsView().environmentObject(store) } } }

@MainActor final class SentinelStore: ObservableObject {
    @Published var servers: [ServerProfile] = []; @Published var plugins: [InspectionPlugin] = []; @Published var pluginScanResults: [PluginScanResult] = []; @Published var pluginDirectory = PluginRepository.defaultRoot.path; @Published var selectedServer: UUID?; @Published var selectedPlugins: Set<UUID> = []; @Published var report: DiagnosticReport?; @Published var aiReport: DiagnosticReport?; @Published var running = false; @Published var aiAnalyzing = false; @Published var aiAnalysisError: String?; @Published var enhanceWithAI = false; @Published var aiSettings = AISettings(); @Published var runConfigurations: [String: PluginRunConfiguration] = [:]
    private let serversURL: URL; private let pluginsURL: URL; private let aiURL: URL; private let runConfigurationsURL: URL; private let settingsURL: URL
    private var storedPluginStates: [InspectionPlugin] = []
    init() {
        let directory = PluginRepository.dataRoot
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        serversURL = directory.appendingPathComponent("servers.json")
        pluginsURL = directory.appendingPathComponent("plugins.json")
        aiURL = directory.appendingPathComponent("ai.json")
        runConfigurationsURL = directory.appendingPathComponent("plugin-runs.json")
        settingsURL = directory.appendingPathComponent("settings.json")
        servers = (try? JSONDecoder().decode([ServerProfile].self, from: Data(contentsOf: serversURL))) ?? []
        storedPluginStates = (try? JSONDecoder().decode([InspectionPlugin].self, from: Data(contentsOf: pluginsURL))) ?? []
        plugins = storedPluginStates
        pluginDirectory = ((try? JSONDecoder().decode(ApplicationSettings.self, from: Data(contentsOf: settingsURL)))?.pluginDirectory).flatMap { $0.isEmpty ? nil : $0 } ?? PluginRepository.defaultRoot.path
        let configuredPluginRoot = URL(fileURLWithPath: (pluginDirectory as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL
        if configuredPluginRoot.lastPathComponent == "poethan-sentinel-plugins",
           !FileManager.default.fileExists(atPath: configuredPluginRoot.path) {
            let migratedRoot = configuredPluginRoot.deletingLastPathComponent().appendingPathComponent("poethan-sentinel/poethan-sentinel-plugins", isDirectory: true)
            if FileManager.default.fileExists(atPath: migratedRoot.path) { pluginDirectory = migratedRoot.standardizedFileURL.path }
        }
        aiSettings = (try? JSONDecoder().decode(AISettings.self, from: Data(contentsOf: aiURL))) ?? AISettings()
        runConfigurations = (try? JSONDecoder().decode([String: PluginRunConfiguration].self, from: Data(contentsOf: runConfigurationsURL))) ?? [:]
        selectedServer = servers.first?.id
        selectedPlugins = Set(plugins.filter(\.enabled).map(\.id))
        saveApplicationSettings()
        refreshPluginCatalog()
    }
    var pluginRoot: URL { URL(fileURLWithPath: (pluginDirectory as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL }
    func saveServers() { try? JSONEncoder.pretty.encode(servers).write(to: serversURL) }
    func savePlugins() {
        for plugin in plugins {
            if let index = storedPluginStates.firstIndex(where: { samePackageState($0, plugin) }) { storedPluginStates[index] = plugin }
            else { storedPluginStates.append(plugin) }
        }
        try? JSONEncoder.pretty.encode(storedPluginStates).write(to: pluginsURL)
    }
    func saveAISettings() { try? JSONEncoder.pretty.encode(aiSettings).write(to: aiURL) }; func saveRunConfigurations() { try? JSONEncoder.pretty.encode(runConfigurations).write(to: runConfigurationsURL) }; func saveApplicationSettings() { try? JSONEncoder.pretty.encode(ApplicationSettings(pluginDirectory: pluginDirectory)).write(to: settingsURL) }
    func configurationKey(serverID: UUID, pluginID: UUID) -> String { "\(serverID.uuidString):\(pluginID.uuidString)" }
    func configuration(serverID: UUID, pluginID: UUID) -> PluginRunConfiguration { runConfigurations[configurationKey(serverID: serverID, pluginID: pluginID)] ?? PluginRunConfiguration() }
    func setPluginDirectory(_ path: String) { pluginDirectory = URL(fileURLWithPath: (path as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL.path; saveApplicationSettings(); refreshPluginCatalog() }
    func refreshPluginCatalog() {
        let saved = storedPluginStates
        let previouslySelected = selectedPlugins
        pluginScanResults = PluginRepository.scanPackages(at: pluginRoot)
        var reconciled: [InspectionPlugin] = []
        var selected = Set<UUID>()
        for result in pluginScanResults {
            guard var package = result.plugin else { continue }
            let existing = saved.first { item in
                (item.manifestID != nil && item.manifestID == package.manifestID && item.packageVersion == package.packageVersion) ||
                (item.packageResource != nil && item.packageResource == package.manifestID && item.packageVersion == package.packageVersion) ||
                (item.name == package.name && item.packageVersion == package.packageVersion)
            }
            if let existing { package.id = existing.id; package.enabled = existing.enabled }
            reconciled.append(package)
            if package.enabled && (existing == nil || previouslySelected.contains(package.id)) { selected.insert(package.id) }
        }
        plugins = reconciled
        selectedPlugins = selected
        savePlugins()
    }
    private func samePackageState(_ lhs: InspectionPlugin, _ rhs: InspectionPlugin) -> Bool {
        if let leftID = lhs.manifestID, let rightID = rhs.manifestID { return leftID == rightID && lhs.packageVersion == rhs.packageVersion }
        if let legacy = lhs.packageResource, let rightID = rhs.manifestID { return legacy == rightID && lhs.packageVersion == rhs.packageVersion }
        return lhs.id == rhs.id || (lhs.name == rhs.name && lhs.packageVersion == rhs.packageVersion)
    }
    func setPluginEnabled(path: String, enabled: Bool) {
        guard let index = plugins.firstIndex(where: { $0.packagePath == path }) else { return }
        plugins[index].enabled = enabled
        if enabled { selectedPlugins.insert(plugins[index].id) } else { selectedPlugins.remove(plugins[index].id) }
        savePlugins()
    }
    func deletePluginConfiguration(_ plugin: InspectionPlugin) {
        if let index = plugins.firstIndex(where: { $0.id == plugin.id }) { plugins[index].enabled = false }; selectedPlugins.remove(plugin.id)
        for key in runConfigurations.keys.filter({ $0.hasSuffix(":\(plugin.id.uuidString)") }) { runConfigurations.removeValue(forKey: key) }
        if let manifest = PluginManifestLoader.load(plugin: plugin) {
            for server in servers { for field in manifest.configuration.fields where field.type == "password" { Keychain.delete(service: "dev.poethan.sentinel.plugin-secret", account: "\(server.id.uuidString):\(plugin.id.uuidString):\(field.key)") } }
        }
        savePlugins(); saveRunConfigurations()
    }
    var aiConfigurationReady: Bool {
        guard let url = URL(string: aiSettings.endpoint),
              let scheme = url.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              url.host != nil,
              !aiSettings.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        return !(Keychain.value(service: "dev.poethan.sentinel.ai", account: "api-key") ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    func setAIEnhancement(_ enabled: Bool) { enhanceWithAI = enabled; if enabled { aiReport = nil; aiAnalysisError = nil } }
    func run() {
        guard let server = servers.first(where: { $0.id == selectedServer }) else { return }
        let chosen = plugins.filter { selectedPlugins.contains($0.id) && $0.enabled }; guard !chosen.isEmpty else { return }
        running = true; aiAnalyzing = false; aiReport = nil; aiAnalysisError = nil
        let useAI = enhanceWithAI && aiConfigurationReady
        Task {
            var outputs: [PluginOutput] = []
            for plugin in chosen { let config = configuration(serverID: server.id, pluginID: plugin.id); outputs.append(await SSHRunner.run(server: server, plugin: plugin, configuration: config)) }
            let local = LocalReport.make(server: server, outputs: outputs); report = local; running = false
            guard useAI else { return }
            aiAnalyzing = true
            do { let enhanced = try await AIAnalyzer.enhance(report: local, settings: aiSettings); aiReport = enhanced; CacheManager.storeAIReport(enhanced) }
            catch { aiAnalysisError = "AI 分析失败：\(error.localizedDescription)" }
            aiAnalyzing = false
        }
    }
}
