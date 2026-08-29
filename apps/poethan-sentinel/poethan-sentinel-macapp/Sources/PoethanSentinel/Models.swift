import Foundation

enum AuthenticationKind: String, Codable, CaseIterable, Identifiable {
    case sshAlias, identityFile, password
    var id: Self { self }
    var label: String { switch self { case .sshAlias: "本机 SSH 别名"; case .identityFile: "密钥文件"; case .password: "用户名与密码" } }
}
enum PluginLanguage: String, Codable, CaseIterable, Identifiable { case bash, python; var id: Self { self }; var label: String { self == .bash ? "Bash" : "Python 3" }; var remoteInterpreter: String { self == .bash ? "bash" : "python3" } }
enum PluginExecution: String, Codable, CaseIterable, Identifiable {
    case sendToStandardInput, existingRemoteFile, pluginPackage
    var id: Self { self }
    var label: String { switch self { case .sendToStandardInput: "通过 SSH 标准输入执行"; case .existingRemoteFile: "执行服务器已有文件"; case .pluginPackage: "上传插件包到临时目录" } }
}

struct InspectionPlugin: Codable, Identifiable, Hashable {
    var id = UUID(); var name: String; var description: String; var language: PluginLanguage = .python; var execution: PluginExecution = .sendToStandardInput; var script = ""; var remotePath = ""; var arguments = ""; var outputLimit = 16000; var enabled = true; var packageResource: String?; var packagePath: String?; var packageVersion: String?; var manifestID: String?; var supportedModes: [String]?
    var remoteCommand: String { let target = execution == .sendToStandardInput ? "-" : shellQuote(remotePath); return "\(language.remoteInterpreter) \(target) \(arguments)" }
}
struct ServerProfile: Codable, Identifiable, Hashable { var id = UUID(); var name: String; var authentication: AuthenticationKind = .sshAlias; var alias = ""; var host = ""; var user = ""; var port = 22; var identityFile = ""; var target: String { authentication == .sshAlias ? alias : (user.isEmpty ? host : "\(user)@\(host)") } }
struct PluginOutput: Codable, Identifiable { var id = UUID(); var pluginID: UUID; var pluginName: String; var exitCode: Int32; var text: String; var collectedAt = Date() }
struct Finding: Codable, Identifiable { var id = UUID(); var severity: String; var title: String; var evidence: String; var recommendation: String }
struct DiagnosticReport: Codable { var schemaVersion = "1.0"; var server: String; var generatedAt = Date(); var summary: String; var findings: [Finding]; var outputs: [PluginOutput]; var enhancedByAI = false }
struct AISettings: Codable { var endpoint = "https://api.openai.com/v1"; var model = "gpt-5-mini"; var enabled = false }
struct ApplicationSettings: Codable { var pluginDirectory: String }
struct PluginRunConfiguration: Codable { var mode = "standard"; var values: [String: String] = [:] }
func shellQuote(_ value: String) -> String { "'\(value.replacingOccurrences(of: "'", with: "'\\\"'\\\"'"))'" }
