import Foundation

struct AppConfig: Codable {
    var groqApiKey: String? = nil
    var micEnabled: Bool = false
    var micDeviceID: String? = nil
    var userName: String = "Me"
    var chunkSeconds: Int = 30
    var lastProject: String? = nil
    var meetingNotesRoot: String = "~/OpenClaude/Vault/Meeting Notes"
    var autoProcess: Bool = true
    var claudePath: String? = nil
    var claudeModel: String? = nil  // e.g. "sonnet", "opus", "claude-sonnet-4-20250514"
    var calendarName: String? = nil  // fantastical calendar name for event creation
    var calendarEnabled: Bool = true  // create calendar events for next meetings
    var todoistApiKey: String? = nil  // synced to ~/.claude.json todoist MCP server

    // resolve groq key: config first, then ~/OpenClaude/Secrets/.env fallback
    var resolvedGroqApiKey: String? {
        if let k = groqApiKey, !k.isEmpty { return k }
        let envPath = NSString(string: "~/OpenClaude/Secrets/.env").expandingTildeInPath
        guard let contents = try? String(contentsOfFile: envPath, encoding: .utf8) else { return nil }
        for line in contents.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            let stripped = trimmed.hasPrefix("export ") ? String(trimmed.dropFirst(7)) : trimmed
            if stripped.hasPrefix("GROQ_API_KEY=") {
                let value = String(stripped.dropFirst(13))
                    .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                if !value.isEmpty { return value }
            }
        }
        return nil
    }

    // resolved paths
    var resolvedNotesRoot: URL {
        let expanded = NSString(string: meetingNotesRoot).expandingTildeInPath
        return URL(fileURLWithPath: expanded)
    }

    var resolvedClaudePath: String? {
        if let path = claudePath, !path.isEmpty { return path }
        // check common locations
        let candidates = [
            "/usr/local/bin/claude",
            "/opt/homebrew/bin/claude",
            "\(NSHomeDirectory())/.local/bin/claude",
            "\(NSHomeDirectory())/.claude/local/claude"
        ]
        for c in candidates {
            if FileManager.default.isExecutableFile(atPath: c) { return c }
        }
        // try which
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        task.arguments = ["claude"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        try? task.run()
        task.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let path = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let path, !path.isEmpty, FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return nil
    }

    // persistence
    private static var configURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MeetingScribe")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("config.json")
    }

    static func load() -> AppConfig {
        guard let data = try? Data(contentsOf: configURL),
              let config = try? JSONDecoder().decode(AppConfig.self, from: data) else {
            return AppConfig()
        }
        return config
    }

    // resolve todoist key: config first, then ~/.claude.json MCP server fallback
    var resolvedTodoistApiKey: String? {
        if let k = todoistApiKey, !k.isEmpty { return k }
        guard let json = Self.readClaudeJson(),
              let servers = json["mcpServers"] as? [String: Any],
              let todoist = servers["todoist"] as? [String: Any],
              let env = todoist["env"] as? [String: String] else { return nil }
        return env["TODOIST_API_TOKEN"]
    }

    func save() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return }
        try? data.write(to: Self.configURL, options: .atomic)
        syncTodoistKeyToClaudeJson()
    }

    // sync todoist api key to ~/.claude.json so the MCP server picks it up
    private func syncTodoistKeyToClaudeJson() {
        guard let key = todoistApiKey, !key.isEmpty else { return }
        let path = NSHomeDirectory() + "/.claude.json"
        guard var json = Self.readClaudeJson() else { return }
        var servers = json["mcpServers"] as? [String: Any] ?? [:]
        var todoist = servers["todoist"] as? [String: Any] ?? [
            "type": "stdio",
            "command": "/usr/local/bin/npx",
            "args": ["-y", "@greirson/mcp-todoist"]
        ]
        var env = todoist["env"] as? [String: String] ?? ["PATH": "/usr/local/bin:/usr/bin:/bin"]
        env["TODOIST_API_TOKEN"] = key
        todoist["env"] = env
        servers["todoist"] = todoist
        json["mcpServers"] = servers
        guard let data = try? JSONSerialization.data(withJSONObject: json, options: [.prettyPrinted, .sortedKeys]) else { return }
        try? data.write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    private static func readClaudeJson() -> [String: Any]? {
        let path = NSHomeDirectory() + "/.claude.json"
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return json
    }
}
