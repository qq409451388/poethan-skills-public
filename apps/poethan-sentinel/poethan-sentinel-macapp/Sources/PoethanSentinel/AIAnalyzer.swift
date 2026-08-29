import Foundation

struct AIServiceError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private struct AIHTTPResponse {
    let statusCode: Int
    let json: [String: Any]
    let rawBody: String
}

enum AIAnalyzer {
    static func testConnection(settings: AISettings, apiKey: String?) async throws -> String {
        let key = try resolvedKey(apiKey)
        let body: [String: Any] = [
            "model": settings.model,
            "messages": [
                ["role": "system", "content": "You are a connectivity test. Reply with exactly OK and nothing else."],
                ["role": "user", "content": "Reply OK"]
            ],
            "temperature": 0,
            "max_tokens": 16,
            "stream": false
        ]
        let response = try await send(body: body, settings: settings, key: key)
        return "HTTP \(response.statusCode)\n\(response.rawBody)"
    }

    static func enhance(report: DiagnosticReport, settings: AISettings) async throws -> DiagnosticReport {
        let key = try resolvedKey(nil)
        let prompt = "你是服务器运维分析师。依据检查脚本输出返回 JSON，字段仅为 summary 和 findings。finding 含 severity(info|warning|critical)、title、evidence、recommendation。不要编造未出现在输出中的事实。输出：\n\(ReportExporter.rawText(report))"
        let body: [String: Any] = [
            "model": settings.model,
            "response_format": ["type": "json_object"],
            "messages": [
                ["role": "system", "content": "请只输出 JSON object，不要使用 Markdown 代码块。"],
                ["role": "user", "content": prompt]
            ],
            "stream": false
        ]
        let response = try await send(body: body, settings: settings, key: key)
        let content = try messageContent(response.json, rawBody: response.rawBody)
        let cleaned = stripCodeFence(content)
        guard let data = cleaned.data(using: .utf8), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw AIServiceError(message: "模型已回复，但内容不是有效 JSON：\(String(content.prefix(300)))") }
        let summary = object["summary"] as? String ?? report.summary
        let rawFindings = object["findings"] as? [[String: Any]] ?? []
        let findings = rawFindings.map { Finding(severity: $0["severity"] as? String ?? "info", title: $0["title"] as? String ?? "AI 分析", evidence: $0["evidence"] as? String ?? "", recommendation: $0["recommendation"] as? String ?? "") }
        return DiagnosticReport(server: report.server, summary: summary, findings: findings, outputs: report.outputs, enhancedByAI: true)
    }

    private static func resolvedKey(_ override: String?) throws -> String {
        if let override, !override.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return override.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard let stored = Keychain.value(service: "dev.poethan.sentinel.ai", account: "api-key"), !stored.isEmpty else { throw AIServiceError(message: "未填写 API Key，钥匙串中也没有已保存的 Key。") }
        return stored
    }

    private static func send(body: [String: Any], settings: AISettings, key: String) async throws -> AIHTTPResponse {
        guard !settings.model.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw AIServiceError(message: "模型名称不能为空。") }
        guard let url = chatCompletionsURL(settings.endpoint) else { throw AIServiceError(message: "接口地址无效。请填写类似 https://api.deepseek.com 的地址。") }
        let data: Data
        do { data = try JSONSerialization.data(withJSONObject: body) }
        catch { throw AIServiceError(message: "无法生成请求：\(error.localizedDescription)") }
        var request = URLRequest(url: url); request.httpMethod = "POST"; request.timeoutInterval = 90
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization"); request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = data
        let responseData: Data; let response: URLResponse
        do { (responseData, response) = try await URLSession.shared.data(for: request) }
        catch { throw AIServiceError(message: "网络请求失败：\(error.localizedDescription)") }
        guard let http = response as? HTTPURLResponse else { throw AIServiceError(message: "服务未返回 HTTP 响应。") }
        guard (200..<300).contains(http.statusCode) else { let detail = String(data: responseData, encoding: .utf8) ?? "无响应正文"; throw AIServiceError(message: "HTTP \(http.statusCode)：\(String(detail.prefix(800)))") }
        let raw = String(data: responseData, encoding: .utf8) ?? "<非 UTF-8 响应，共 \(responseData.count) 字节>"
        guard let envelope = try? JSONSerialization.jsonObject(with: responseData) as? [String: Any] else { throw AIServiceError(message: "服务返回 HTTP \(http.statusCode)，但响应不是 JSON。原始响应：\n\(String(raw.prefix(2000)))") }
        return AIHTTPResponse(statusCode: http.statusCode, json: envelope, rawBody: prettyJSON(envelope) ?? raw)
    }

    private static func messageContent(_ envelope: [String: Any], rawBody: String) throws -> String {
        if let choices = envelope["choices"] as? [[String: Any]], let message = choices.first?["message"] as? [String: Any], let content = message["content"] as? String, !content.isEmpty { return content }
        if let outputText = envelope["output_text"] as? String, !outputText.isEmpty { return outputText }
        if let output = envelope["output"] as? [[String: Any]] {
            for item in output { if let content = item["content"] as? [[String: Any]] { for part in content { if let text = part["text"] as? String, !text.isEmpty { return text } } } }
        }
        throw AIServiceError(message: "接口返回的不是可识别的 Chat Completions/Responses 文本结构。原始响应：\n\(String(rawBody.prefix(2000)))")
    }

    private static func chatCompletionsURL(_ endpoint: String) -> URL? {
        let base = endpoint.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let full = base.hasSuffix("/chat/completions") ? base : base + "/chat/completions"
        guard let url = URL(string: full), let scheme = url.scheme, ["http", "https"].contains(scheme), url.host != nil else { return nil }
        return url
    }

    private static func stripCodeFence(_ value: String) -> String {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.hasPrefix("```") else { return trimmed }
        var lines = trimmed.split(separator: "\n", omittingEmptySubsequences: false)
        if !lines.isEmpty { lines.removeFirst() }; if lines.last?.trimmingCharacters(in: .whitespacesAndNewlines) == "```" { lines.removeLast() }
        return lines.joined(separator: "\n")
    }

    private static func prettyJSON(_ value: Any) -> String? { guard JSONSerialization.isValidJSONObject(value), let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) else { return nil }; return String(data: data, encoding: .utf8) }
}
