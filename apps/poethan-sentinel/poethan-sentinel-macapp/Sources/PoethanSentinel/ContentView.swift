import SwiftUI

struct ContentView: View {
 @EnvironmentObject var store: SentinelStore
 @State var serverSheet = false
 @State var pluginSheet = false
 @State var settingsSheet = false
 @State var configurationPlugin: InspectionPlugin?
 @State var aiReportError: String?
 var body: some View {
  NavigationSplitView {
   VStack(spacing: 0) {
    Button { serverSheet = true } label: { Label("添加服务器", systemImage: "plus") }
     .buttonStyle(.borderedProminent)
     .controlSize(.large)
     .frame(maxWidth: .infinity, alignment: .leading)
     .padding(12)
    Divider()
    List(selection: $store.selectedServer) { Section("服务器") { ForEach(store.servers) { Text($0.name).tag($0.id) } } }
   }
  } detail: {
   ScrollView { VStack(alignment: .leading, spacing: 20) {
    HStack { VStack(alignment: .leading) { Text("Poethan Sentinel").font(.largeTitle.bold()); Text("脚本负责检查，App 负责执行、采集和报告").foregroundStyle(.secondary) }; Spacer(); Button { store.run() } label: { Label(store.running ? "检查中…" : "运行检查", systemImage: "play.fill") }.buttonStyle(.borderedProminent).disabled(store.running || store.aiAnalyzing || store.selectedServer == nil || store.selectedPlugins.isEmpty) }
    GroupBox("选择检查脚本") { VStack(alignment: .leading) { ForEach(store.plugins.filter(\.enabled)) { plugin in PluginToggle(plugin: plugin) { configurationPlugin = plugin } } }.frame(maxWidth: .infinity, alignment: .leading) }
    HStack {
     Toggle("使用 AI 增强分析", isOn: Binding(get: { store.enhanceWithAI }, set: { enabled in
      if enabled && !store.aiConfigurationReady { settingsSheet = true }
      else { store.setAIEnhancement(enabled) }
     }))
     if !store.aiConfigurationReady { Text("请先完成 AI 配置").font(.caption).foregroundStyle(.secondary) }
     if store.enhanceWithAI {
      if store.aiAnalyzing { ProgressView().controlSize(.small); Text("AI 正在分析…").font(.caption).foregroundStyle(.secondary) }
      Button("打开 AI 报告") { if let report = store.aiReport { aiReportError = ReportExporter.openHTML(report) } }.disabled(store.aiReport == nil || store.aiAnalyzing)
      if let error = store.aiAnalysisError { Text(error).font(.caption).foregroundStyle(.red) }
     }
     Spacer(); Button("管理检查脚本") { pluginSheet = true }; Button("打开设置") { settingsSheet = true }
    }
    if let report = store.report { ReportView(report: report) } else { VStack(spacing: 8) { Image(systemName: "waveform.path.ecg").font(.largeTitle).foregroundStyle(.secondary); Text("等待检查").font(.title3); Text("选择服务器与脚本，然后运行检查。").foregroundStyle(.secondary) }.frame(maxWidth: .infinity).padding(.vertical, 70) }
   }.padding(28) }
  }
  .sheet(isPresented: $serverSheet) { ServerEditorView().environmentObject(store) }
  .sheet(isPresented: $pluginSheet) { PluginEditorView().environmentObject(store) }
  .sheet(isPresented: $settingsSheet) { SettingsView().environmentObject(store) }
  .sheet(item: $configurationPlugin) { plugin in if let serverID = store.selectedServer, let server = store.servers.first(where: { $0.id == serverID }) { PluginRunConfigurationView(server: server, plugin: plugin).environmentObject(store) } }
  .alert("无法打开 AI 报告", isPresented: Binding(get: { aiReportError != nil }, set: { if !$0 { aiReportError = nil } })) { Button("好") { aiReportError = nil } } message: { Text(aiReportError ?? "未知错误") }
 }
}

private struct PluginToggle: View { @EnvironmentObject var store: SentinelStore; let plugin: InspectionPlugin; let configure: () -> Void; var body: some View { HStack { Toggle(isOn: Binding(get: { store.selectedPlugins.contains(plugin.id) }, set: { on in if on { store.selectedPlugins.insert(plugin.id) } else { store.selectedPlugins.remove(plugin.id) } })) { VStack(alignment: .leading) { Text(plugin.name).fontWeight(.medium); Text("\(plugin.language.label) · \(plugin.execution.label) · \(plugin.description)").font(.caption).foregroundStyle(.secondary) } }.toggleStyle(.checkbox); if plugin.execution == .pluginPackage { Button("配置") { configure() }.disabled(store.selectedServer == nil) } } } }
private struct ReportView: View {
    @EnvironmentObject private var store: SentinelStore
    let report: DiagnosticReport
    @State private var reportError: String?
    var body: some View {
        GroupBox("诊断报告") {
            VStack(alignment: .leading, spacing: 12) {
                Text(report.summary).font(.title3.weight(.semibold))
                ForEach(report.findings) { finding in Text("\(finding.title)：\(finding.evidence)").font(.caption) }
                Divider()
                HStack {
                    Button("复制原始输出") { ReportExporter.copy(ReportExporter.rawText(report)) }
                    Button("复制报告 JSON") { ReportExporter.copy((try? String(data: JSONEncoder.pretty.encode(report), encoding: .utf8)) ?? "") }
                    Button("打开综合 HTML 报告") { reportError = ReportExporter.openHTML(report) }
                }
                ForEach(report.outputs) { output in
                    DisclosureGroup("\(output.pluginName) · exit \(output.exitCode)") {
                        VStack(alignment: .leading, spacing: 8) {
                            if let plugin = store.plugins.first(where: { $0.id == output.pluginID }), hasPluginReport(plugin) {
                                Button("打开插件专属报告") { reportError = ReportExporter.openHTML(report, plugin: plugin) }
                            }
                            Text(output.text).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        }
                    }
                }
            }.frame(maxWidth: .infinity, alignment: .leading)
        }
        .alert("无法打开报告", isPresented: Binding(get: { reportError != nil }, set: { if !$0 { reportError = nil } })) { Button("好") { reportError = nil } } message: { Text(reportError ?? "未知错误") }
    }
    private func hasPluginReport(_ plugin: InspectionPlugin) -> Bool { PluginManifestLoader.load(plugin: plugin)?.report != nil }
}
