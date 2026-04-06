import Foundation

// invokes claude CLI to post-process transcripts
class ClaudeProcessor {
    // claude -p --output-format json response
    struct Response: Codable {
        let result: String?
        let is_error: Bool?
        let num_turns: Int?
        let cost_usd: Double?
        let duration_ms: Int?
        let session_id: String?
    }

    enum Status: Equatable {
        case idle
        case running
        case completed(summary: String)
        case failed(String)
    }

    private var process: Process?
    private var timeoutWork: DispatchWorkItem?
    private let config: AppConfig
    private static let timeoutSeconds: TimeInterval = 300

    init(config: AppConfig) {
        self.config = config
    }

    static var isClaudeInstalled: Bool {
        AppConfig().resolvedClaudePath != nil
    }

    func process(transcriptPath: URL, planPath: URL, completion: @escaping (Status) -> Void) {
        guard let claudePath = config.resolvedClaudePath else {
            completion(.failed("claude CLI not found"))
            return
        }

        let skillPath = NSHomeDirectory() + "/OpenClaude/.claude/skills/meeting-processor.md"

        let prompt = """
        Read the meeting transcript at \(transcriptPath.path).

        Your tasks:
        1. Add a polished summary section at the TOP of the transcript file (keep raw transcript below under a "## Raw Transcript" heading)
        2. Extract action items from the meeting. First create a parent Todoist task named "Meeting: <title> (YYYY-MM-DD)", then create each action item as a sub-task under it with:
           - Clear, actionable title
           - Appropriate priority (p1=urgent, p2=important, p3=normal, p4=low)
           - Due date if mentioned or inferable
           - Description with relevant context
        3. Generate a detailed action plan at \(planPath.path) with:
           - Each action item with full context
           - Relevant file paths, repos, or resources mentioned
           - Source transcript path for reference
           - Suggested execution order and dependencies
           - Decisions made and rationale

        Format the summary with: date, duration, participants (if identifiable), then 2-4 paragraph summary, then bulleted action items, then key decisions.
        """

        var args = [
            "-p", prompt,
            "--allowedTools", "Read,Write,Edit,Bash(ls *),Bash(cat *),mcp__todoist__*,mcp__vault__*",
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
            "--max-turns", "15"
        ]

        if let model = config.claudeModel, !model.isEmpty {
            args.append(contentsOf: ["--model", model])
        }

        // read skill file and append as system prompt context
        if let skillContent = try? String(contentsOfFile: skillPath, encoding: .utf8) {
            args.append(contentsOf: ["--append-system-prompt", skillContent])
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: claudePath)
        proc.arguments = args
        proc.environment = Self.shellEnvironment()

        let stdout = Pipe()
        let stderr = Pipe()
        proc.standardOutput = stdout
        proc.standardError = stderr

        proc.terminationHandler = { [weak self] proc in
            let data = stdout.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                self?.timeoutWork?.cancel()
                self?.process = nil
                self?.handleTermination(exitCode: proc.terminationStatus, data: data, completion: completion)
            }
        }

        do {
            try proc.run()
            self.process = proc
            startTimeout(completion: completion)
        } catch {
            completion(.failed("failed to launch claude: \(error.localizedDescription)"))
        }
    }

    func cancel() {
        timeoutWork?.cancel()
        process?.terminate()
        process = nil
    }

    // MARK: - private

    private func startTimeout(completion: @escaping (Status) -> Void) {
        let work = DispatchWorkItem { [weak self] in
            DispatchQueue.main.async {
                guard let self, self.process != nil else { return }
                self.process?.terminate()
                self.process = nil
                completion(.failed("timed out after \(Int(Self.timeoutSeconds))s"))
            }
        }
        timeoutWork = work
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.timeoutSeconds, execute: work)
    }

    private func handleTermination(exitCode: Int32, data: Data, completion: (Status) -> Void) {
        guard exitCode == 0 else {
            let msg = String(data: data, encoding: .utf8)?.prefix(200) ?? ""
            completion(.failed("claude exited with status \(exitCode)\(msg.isEmpty ? "" : ": \(msg)")"))
            return
        }

        // parse json response from claude -p --output-format json
        if let response = try? JSONDecoder().decode(Response.self, from: data) {
            if response.is_error == true {
                completion(.failed(response.result ?? "unknown error"))
                return
            }

            let result = response.result ?? ""
            let turns = response.num_turns ?? 0
            let cost = response.cost_usd.map { String(format: "$%.2f", $0) }
            let taskCount = Self.countTasks(in: result)

            var parts: [String] = []
            if taskCount > 0 { parts.append("\(taskCount) task\(taskCount == 1 ? "" : "s") created") }
            if turns > 0 { parts.append("\(turns) turns") }
            if let cost { parts.append(cost) }

            completion(.completed(summary: parts.isEmpty ? "done" : parts.joined(separator: ", ")))
        } else {
            // non-json output — still probably succeeded
            completion(.completed(summary: "done"))
        }
    }

    // count todoist task creations in claude's result text
    private static func countTasks(in text: String) -> Int {
        let lower = text.lowercased()
        // count "✓ created" markers from plan template
        let checkmarks = lower.components(separatedBy: "✓ created").count - 1
        if checkmarks > 0 { return checkmarks }
        // count lines mentioning task creation
        return lower.components(separatedBy: .newlines).filter { line in
            line.contains("created") && (line.contains("task") || line.contains("todoist"))
        }.count
    }

    // get user's shell env so claude can find node/npm for MCP servers
    private static func shellEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        // gui apps have minimal PATH — pull from login shell if needed
        if env["PATH"]?.contains("/usr/local/bin") != true {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
            proc.arguments = ["-lc", "echo $PATH"]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = FileHandle.nullDevice
            try? proc.run()
            proc.waitUntilExit()
            if let path = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines), !path.isEmpty {
                env["PATH"] = path
            }
        }
        return env
    }
}
