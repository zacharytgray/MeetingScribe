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

    func save() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return }
        try? data.write(to: Self.configURL, options: .atomic)
    }
}
