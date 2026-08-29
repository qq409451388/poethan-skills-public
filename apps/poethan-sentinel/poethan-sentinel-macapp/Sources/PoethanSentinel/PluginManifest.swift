import Foundation

struct PluginManifest: Codable {
    var id: String
    var name: String
    var description: String?
    var version: String
    var entrypoint: String
    var language: String?
    var outputLimit: Int?
    var defaultMode: String
    var modes: [PluginModeDefinition]
    var configuration: PluginConfigurationDefinition
    var report: PluginReportDefinition?
}

struct PluginReportDefinition: Codable {
    var schema: String
    var template: String
}

struct PluginModeDefinition: Codable, Identifiable {
    var id: String
    var label: String
    var help: String?
}

struct PluginConfigurationDefinition: Codable {
    var fields: [PluginFieldDefinition]
}

struct PluginFieldDefinition: Codable, Identifiable {
    var key: String
    var label: String
    var type: String
    var section: String
    var defaultValue: String?
    var required: Bool?
    var help: String?
    var placeholder: String?
    var options: [PluginFieldOption]?
    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, label, type, section, required, help, placeholder, options
        case defaultValue = "default"
    }
}

struct PluginFieldOption: Codable, Identifiable {
    var value: String
    var label: String
    var id: String { value }
}

enum PluginManifestLoader {
    static func load(plugin: InspectionPlugin) -> PluginManifest? {
        if let path = plugin.packagePath { return load(directory: URL(fileURLWithPath: path)) }
        return nil
    }

    static func load(directory: URL) -> PluginManifest? {
        guard FileManager.default.fileExists(atPath: directory.appendingPathComponent("plugin.yaml").path) else { return nil }
        return try? PluginRepository.validatePackage(at: directory)
    }

    static func reportFiles(plugin: InspectionPlugin, definition: PluginReportDefinition) -> (schema: URL, template: URL)? {
        guard let directory = packageDirectory(plugin) else { return nil }
        return reportFiles(directory: directory, definition: definition)
    }

    static func packageDirectory(_ plugin: InspectionPlugin) -> URL? {
        if let path = plugin.packagePath { let url = URL(fileURLWithPath: path); return FileManager.default.fileExists(atPath: url.path) ? url : nil }
        return nil
    }

    private static func reportFiles(directory: URL, definition: PluginReportDefinition) -> (schema: URL, template: URL)? {
        let root = directory.standardizedFileURL
        let schema = root.appendingPathComponent(definition.schema).standardizedFileURL
        let template = root.appendingPathComponent(definition.template).standardizedFileURL
        let prefix = root.path.hasSuffix("/") ? root.path : root.path + "/"
        guard schema.path.hasPrefix(prefix), template.path.hasPrefix(prefix),
              FileManager.default.fileExists(atPath: schema.path),
              FileManager.default.fileExists(atPath: template.path) else { return nil }
        return (schema, template)
    }

    static func catalog(in directory: URL) -> [InspectionPlugin] { PluginRepository.installedPackages(at: directory) }
}
