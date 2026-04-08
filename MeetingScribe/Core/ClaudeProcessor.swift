import Foundation

// pipeline of focused claude agents for post-processing transcripts
class ClaudeProcessor {
    struct AgentError: Error { let message: String }

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

    private var processes: [Process] = []
    private var timeoutWork: DispatchWorkItem?
    private let config: AppConfig
    private static let timeoutSeconds: TimeInterval = 600 // total pipeline timeout

    init(config: AppConfig) {
        self.config = config
    }

    static var isClaudeInstalled: Bool {
        AppConfig().resolvedClaudePath != nil
    }

    func process(transcriptPath: URL, planPath: URL, projectMeta: ProjectMeta, completion: @escaping (Status) -> Void) {
        guard let claudePath = config.resolvedClaudePath else {
            completion(.failed("claude CLI not found"))
            return
        }

        let env = Self.shellEnvironment()
        let skillPath = NSHomeDirectory() + "/OpenClaude/.claude/skills/meeting-processor.md"
        let skillContent = try? String(contentsOfFile: skillPath, encoding: .utf8)
        let projectDir = transcriptPath.deletingLastPathComponent()
        let readmePath = projectDir.appendingPathComponent("README.md").path

        startTimeout(completion: completion)

        // participant name correction instructions
        let participantBlock: String
        if !projectMeta.participants.isEmpty {
            let names = projectMeta.participants.joined(separator: ", ")
            participantBlock = """

            IMPORTANT — Participant name correction:
            The known participants for this project are: \(names).
            The transcription may have misspelled or misheard names. When writing the summary, action items, and plan, use the correct participant names from this list. If you see an unfamiliar name that closely resembles one of these participants, replace it with the correct name. Apply this correction everywhere you write — summary, action items, key decisions, and the action plan file.
            """
        } else {
            participantBlock = ""
        }

        // phase 1: planner — summarize transcript, generate action plan, update project readme
        let plannerPrompt = """
        Read the meeting transcript at \(transcriptPath.path).
        \(participantBlock)
        Your tasks:
        1. Add a polished summary section at the TOP of the transcript file (keep raw transcript below under a "## Raw Transcript" heading)
        2. Generate a detailed action plan at \(planPath.path) with:
           - Each action item with full context
           - Relevant file paths, repos, or resources mentioned
           - Source transcript path for reference
           - Suggested execution order and dependencies
           - Decisions made and rationale
        3. Update the project README at \(readmePath):
           - If the file doesn't exist, create it with a "# <Project Name>" heading
           - The top section "## Project Overview" should be a living summary of what this project is about — its goals, current status, key participants, and approach. Refine this each meeting (don't rewrite from scratch, just adjust framing/status as needed based on new info)
           - Append a new "### YYYY-MM-DD" entry at the bottom under "## Meeting Log" with a 2-3 sentence summary of this meeting

        Format the summary with: date, duration, participants (if identifiable), then 2-4 paragraph summary, then bulleted action items, then key decisions.
        """

        runAgent(
            claudePath: claudePath,
            prompt: plannerPrompt,
            allowedTools: "Read,Write,Edit",
            maxTurns: 10,
            env: env,
            skillContent: skillContent
        ) { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let err):
                self.finishPipeline(completion: completion, status: .failed("planner: \(err.message)"))
            case .success:
                // phase 2: launch todoist + calendar agents concurrently
                self.runPhase2(
                    claudePath: claudePath,
                    transcriptPath: transcriptPath,
                    planPath: planPath,
                    env: env,
                    skillContent: skillContent,
                    completion: completion
                )
            }
        }
    }

    func cancel() {
        timeoutWork?.cancel()
        for p in processes { p.terminate() }
        processes.removeAll()
    }

    // MARK: - pipeline phases

    private func runPhase2(
        claudePath: String,
        transcriptPath: URL,
        planPath: URL,
        env: [String: String],
        skillContent: String?,
        completion: @escaping (Status) -> Void
    ) {
        let group = DispatchGroup()
        var todoistResult: String?
        var calendarResult: String?
        var errors: [String] = []

        // todoist agent
        group.enter()
        let todoistPrompt = """
        Read the action plan at \(planPath.path) and the meeting transcript at \(transcriptPath.path).

        Create Todoist tasks for every action item:
        1. Create a parent task named "Meeting: <title> (YYYY-MM-DD)" in Inbox
        2. Create each action item as a sub-task under it with:
           - Clear, actionable title
           - Appropriate priority (p1=urgent, p2=important, p3=normal, p4=low)
           - Due date if mentioned or inferable
           - Description with relevant context from the plan
        """

        runAgent(
            claudePath: claudePath,
            prompt: todoistPrompt,
            allowedTools: "Read,mcp__todoist__*",
            maxTurns: 20,
            env: env,
            skillContent: nil
        ) { result in
            switch result {
            case .success(let resp):
                let count = Self.countTasks(in: resp?.result ?? "")
                todoistResult = count > 0 ? "\(count) task\(count == 1 ? "" : "s")" : "tasks created"
            case .failure(let err):
                errors.append("todoist: \(err.message)")
            }
            group.leave()
        }

        // calendar + prep folder agent (only if calendar enabled)
        if config.calendarEnabled {
            group.enter()
            let calParam = config.calendarName.map { "&calendarName=\($0)" } ?? ""
            let projectDirPath = transcriptPath.deletingLastPathComponent().path
            let calendarPrompt = """
            Read the meeting summary at \(transcriptPath.path) (just the summary section at the top, not the full transcript).

            Check if the summary or action items mention a next meeting, follow-up meeting, or recurring meeting time.

            If a specific date/time is mentioned or clearly inferable:
            1. Create a calendar event by running:
               open "x-fantastical3://parse?sentence=<natural language event description>&add=1\(calParam)"
               URL-encode the sentence parameter. Include the meeting title, date, start time, and duration.
            2. Create a prep directory for that next meeting:
               mkdir -p "\(projectDirPath)/YYYY-MM-DD_prep"
               Use the date of the next meeting (not today's date).

            If no next meeting is mentioned, do nothing — just say "no next meeting found".
            """

            runAgent(
                claudePath: claudePath,
                prompt: calendarPrompt,
                allowedTools: "Read,Bash(open *),Bash(mkdir *)",
                maxTurns: 5,
                env: env,
                skillContent: nil
            ) { result in
                switch result {
                case .success(let resp):
                    let text = resp?.result?.lowercased() ?? ""
                    if text.contains("no next meeting") || text.contains("no follow-up") {
                        calendarResult = nil // nothing to report
                    } else {
                        calendarResult = "event created"
                    }
                case .failure(let err):
                    errors.append("calendar: \(err.message)")
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self else { return }

            var parts: [String] = []
            if let t = todoistResult { parts.append(t) }
            if let c = calendarResult { parts.append(c) }

            if !errors.isEmpty && parts.isEmpty {
                self.finishPipeline(completion: completion, status: .failed(errors.joined(separator: "; ")))
            } else {
                let summary = parts.isEmpty ? "done" : parts.joined(separator: ", ")
                let warn = errors.isEmpty ? "" : " (warnings: \(errors.joined(separator: "; ")))"
                self.finishPipeline(completion: completion, status: .completed(summary: summary + warn))
            }
        }
    }

    // MARK: - agent runner

    private func runAgent(
        claudePath: String,
        prompt: String,
        allowedTools: String,
        maxTurns: Int,
        env: [String: String],
        skillContent: String?,
        completion: @escaping (Result<Response?, AgentError>) -> Void
    ) {
        var args = [
            "-p", prompt,
            "--allowedTools", allowedTools,
            "--permission-mode", "acceptEdits",
            "--output-format", "json",
            "--max-turns", String(maxTurns)
        ]

        if let model = config.claudeModel, !model.isEmpty {
            args.append(contentsOf: ["--model", model])
        }

        if let skill = skillContent {
            args.append(contentsOf: ["--append-system-prompt", skill])
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: claudePath)
        proc.arguments = args
        proc.environment = env

        let stdout = Pipe()
        let stderr = Pipe()
        proc.standardOutput = stdout
        proc.standardError = stderr

        proc.terminationHandler = { [weak self] proc in
            let data = stdout.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.async {
                self?.processes.removeAll { $0 === proc }
                Self.handleAgentResult(exitCode: proc.terminationStatus, data: data, completion: completion)
            }
        }

        do {
            try proc.run()
            processes.append(proc)
        } catch {
            completion(.failure(AgentError(message: "launch failed: \(error.localizedDescription)")))
        }
    }

    private static func handleAgentResult(
        exitCode: Int32,
        data: Data,
        completion: (Result<Response?, AgentError>) -> Void
    ) {
        guard exitCode == 0 else {
            let msg = String(data: data, encoding: .utf8)?.prefix(200) ?? ""
            completion(.failure(AgentError(message: "exit \(exitCode)\(msg.isEmpty ? "" : ": \(msg)")")))
            return
        }

        let response = try? JSONDecoder().decode(Response.self, from: data)
        if response?.is_error == true {
            completion(.failure(AgentError(message: response?.result ?? "unknown error")))
            return
        }

        completion(.success(response))
    }

    // MARK: - helpers

    private func startTimeout(completion: @escaping (Status) -> Void) {
        let work = DispatchWorkItem { [weak self] in
            DispatchQueue.main.async {
                guard let self, !self.processes.isEmpty else { return }
                for p in self.processes { p.terminate() }
                self.processes.removeAll()
                completion(.failed("timed out after \(Int(Self.timeoutSeconds))s"))
            }
        }
        timeoutWork = work
        DispatchQueue.global().asyncAfter(deadline: .now() + Self.timeoutSeconds, execute: work)
    }

    private func finishPipeline(completion: @escaping (Status) -> Void, status: Status) {
        timeoutWork?.cancel()
        completion(status)
    }

    // count todoist task creations in claude's result text
    private static func countTasks(in text: String) -> Int {
        let lower = text.lowercased()
        let checkmarks = lower.components(separatedBy: "✓ created").count - 1
        if checkmarks > 0 { return checkmarks }
        return lower.components(separatedBy: .newlines).filter { line in
            line.contains("created") && (line.contains("task") || line.contains("todoist"))
        }.count
    }

    // get user's shell env so claude can find node/npm for MCP servers
    private static func shellEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
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
