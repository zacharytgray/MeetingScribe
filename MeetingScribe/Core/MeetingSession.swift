import Foundation
import Combine
import AVFoundation

// orchestrates recording, transcription, and claude processing
@MainActor
class MeetingSession: ObservableObject {
    @Published var state: RecordingState = .idle
    @Published var segments: [TranscriptSegment] = []
    @Published var duration: TimeInterval = 0
    @Published var transcriptionProgress: String = ""  // "3/5 chunks"
    @Published var claudeStatus: ClaudeProcessor.Status = .idle

    private var config: AppConfig
    private var projectURL: URL?
    private var sessionRecordingsDir: URL?
    private var recorder: AudioRecorder?
    private var micRecorder: MicRecorder?
    private var transcriber: Transcriber?
    private var claudeProcessor: ClaudeProcessor?
    private var durationTimer: Timer?
    private var recordingStart: Date?

    // chunks queued for transcription — tagged with source stream
    private enum ChunkSource { case loopback, mic }
    private var pendingChunks: [(url: URL, offset: TimeInterval, source: ChunkSource)] = []
    private var transcribedCount = 0
    private var isTranscribing = false

    // persistent recordings dir for raw audio preservation
    static let recordingsBaseDir: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MeetingScribe/recordings")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    init(config: AppConfig) {
        self.config = config
    }

    // MARK: - token check

    var isApiKeyConfigured: Bool {
        config.resolvedGroqApiKey != nil
    }

    // MARK: - recording

    func startRecording(projectURL: URL) throws {
        guard !state.isBusy else { return }

        self.projectURL = projectURL
        self.segments = []
        self.pendingChunks = []
        self.transcribedCount = 0
        self.isTranscribing = false
        self.transcriptionProgress = ""
        self.claudeStatus = .idle

        // create session recordings directory for raw audio preservation
        let dateStr: String = {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd_HH-mm-ss"
            return f.string(from: Date())
        }()
        let sessionDir = Self.recordingsBaseDir.appendingPathComponent(dateStr)
        try? FileManager.default.createDirectory(at: sessionDir, withIntermediateDirectories: true)
        self.sessionRecordingsDir = sessionDir
        print("[MeetingSession] raw audio will be saved to \(sessionDir.path)")

        // init transcriber with HF token
        if let apiKey = config.resolvedGroqApiKey {
            self.transcriber = Transcriber(apiKey: apiKey)
        } else {
            print("[MeetingSession] no Groq API key, transcription will be skipped")
            self.transcriber = nil
        }

        // start system audio capture
        let recorder = AudioRecorder(chunkSeconds: config.chunkSeconds, outputDir: sessionDir) { [weak self] url, offset in
            DispatchQueue.main.async {
                self?.handleChunk(url: url, offset: offset, source: .loopback)
            }
        }
        try recorder.start()
        self.recorder = recorder

        // start mic capture if enabled — request permission async so we don't block main thread
        if config.micEnabled {
            let micSessionDir = sessionDir
            let micChunkSeconds = config.chunkSeconds
            Task { @MainActor in
                let granted = await AVCaptureDevice.requestAccess(for: .audio)
                guard granted else {
                    print("[MeetingSession] mic permission denied")
                    return
                }
                let mic = MicRecorder(chunkSeconds: micChunkSeconds, outputDir: micSessionDir) { [weak self] url, offset in
                    DispatchQueue.main.async {
                        self?.handleChunk(url: url, offset: offset, source: .mic)
                    }
                }
                do {
                    try mic.start()
                    self.micRecorder = mic
                    print("[MeetingSession] mic recording enabled")
                } catch {
                    print("[MeetingSession] mic failed to start: \(error)")
                }
            }
        }

        let now = Date()
        self.recordingStart = now
        self.state = .recording(since: now)
        self.duration = 0

        durationTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let start = self.recordingStart else { return }
                self.duration = Date().timeIntervalSince(start)
            }
        }

        print("[MeetingSession] recording started")
    }

    func stopRecording() {
        guard state.isRecording else { return }

        state = .stopping
        durationTimer?.invalidate()
        durationTimer = nil

        // stop recorders and collect flush chunks synchronously
        let loopbackFlush = recorder?.stop()
        recorder = nil
        let micFlush = micRecorder?.stop()
        micRecorder = nil

        if let flush = loopbackFlush {
            pendingChunks.append((url: flush.url, offset: flush.offset, source: .loopback))
        }
        if let flush = micFlush {
            pendingChunks.append((url: flush.url, offset: flush.offset, source: .mic))
        }

        print("[MeetingSession] stopped, \(pendingChunks.count) chunks queued")

        // transcribe any remaining chunks
        if transcriber != nil && !pendingChunks.isEmpty {
            state = .transcribing
            transcribeNext()
        } else {
            finishSession()
        }
    }

    var formattedDuration: String {
        let m = Int(duration) / 60
        let s = Int(duration) % 60
        return String(format: "%02d:%02d", m, s)
    }

    // MARK: - transcript output

    func saveTranscript() -> URL? {
        guard let projectURL else { return nil }

        let datePrefix: String = {
            let f = DateFormatter()
            f.dateFormat = "yyyy-MM-dd"
            return f.string(from: Date())
        }()

        var filename = "\(datePrefix)_meeting.md"
        var url = projectURL.appendingPathComponent(filename)

        var counter = 2
        while FileManager.default.fileExists(atPath: url.path) {
            filename = "\(datePrefix)_meeting_\(counter).md"
            url = projectURL.appendingPathComponent(filename)
            counter += 1
        }

        // echo dedup if mic was active
        let finalSegments: [TranscriptSegment]
        if config.micEnabled {
            finalSegments = EchoDedup.dedup(segments: segments, userName: config.userName)
            if segments.count != finalSegments.count {
                print("[MeetingSession] echo dedup: \(segments.count) → \(finalSegments.count) segments")
            }
        } else {
            finalSegments = segments
        }

        if finalSegments.isEmpty {
            print("[MeetingSession] WARNING: no segments to save — raw audio preserved at \(sessionRecordingsDir?.path ?? "unknown")")
        }

        var content = "## Raw Transcript\n\n"
        if finalSegments.isEmpty {
            content += "_No transcription available._\n"
        } else {
            for seg in finalSegments.sorted(by: { $0.start < $1.start }) {
                content += "\(seg.formattedLine)\n\n"
            }
        }

        do {
            try content.write(to: url, atomically: true, encoding: .utf8)
            print("[MeetingSession] saved transcript to \(url.path)")
            return url
        } catch {
            print("[MeetingSession] failed to save: \(error)")
            return nil
        }
    }

    // MARK: - private

    private func handleChunk(url: URL, offset: TimeInterval, source: ChunkSource) {
        pendingChunks.append((url: url, offset: offset, source: source))
        print("[MeetingSession] \(source) chunk at \(String(format: "%.1f", offset))s: \(url.lastPathComponent)")
        transcribeNext()
    }

    private func transcribeNext() {
        guard !isTranscribing, let transcriber, transcribedCount < pendingChunks.count else { return }

        isTranscribing = true
        let idx = transcribedCount
        let chunk = pendingChunks[idx]

        transcriptionProgress = "\(idx + 1)/\(pendingChunks.count) chunks"

        // speaker label: mic = userName, loopback = "Remote" if mic active, else "Speaker"
        let speaker: String
        switch chunk.source {
        case .mic: speaker = config.userName
        case .loopback: speaker = config.micEnabled ? "Remote" : "Speaker"
        }

        Task {
            do {
                let newSegments = try await transcriber.transcribe(wavURL: chunk.url)

                let adjusted = newSegments.map { seg in
                    TranscriptSegment(
                        start: seg.start + chunk.offset,
                        end: seg.end + chunk.offset,
                        speaker: speaker,
                        text: seg.text
                    )
                }

                segments.append(contentsOf: adjusted)
                transcribedCount = idx + 1
                transcriptionProgress = "\(transcribedCount)/\(pendingChunks.count) chunks"
                print("[MeetingSession] chunk \(idx) done — \(newSegments.count) segments")
            } catch {
                print("[MeetingSession] transcription failed for chunk \(idx): \(error)")
                transcribedCount = idx + 1
            }

            isTranscribing = false

            if transcribedCount < pendingChunks.count {
                transcribeNext()
            } else if !state.isRecording && state != .idle {
                finishSession()
            }
        }
    }

    private func finishSession() {
        let url = saveTranscript()
        transcriber = nil
        transcriptionProgress = ""

        if let url, config.autoProcess {
            processWithClaude(transcriptURL: url)
        } else {
            state = .idle
        }
    }

    private func processWithClaude(transcriptURL: URL) {
        guard config.resolvedClaudePath != nil else {
            print("[MeetingSession] claude not found, skipping processing")
            state = .idle
            return
        }

        state = .processing
        claudeStatus = .running
        let planURL = transcriptURL.deletingPathExtension()
            .appendingPathExtension("plan.md")

        let processor = ClaudeProcessor(config: config)
        self.claudeProcessor = processor
        processor.process(transcriptPath: transcriptURL, planPath: planURL) { [weak self] status in
            guard let self else { return }
            self.claudeProcessor = nil
            self.claudeStatus = status
            self.state = .idle

            switch status {
            case .completed(let summary):
                print("[MeetingSession] claude done: \(summary)")
            case .failed(let msg):
                print("[MeetingSession] claude failed: \(msg)")
            default:
                break
            }
        }
    }
}

// MARK: - echo deduplication

enum EchoDedup {
    // remove mic segments that are echoes of loopback audio
    // criteria: temporal overlap (<=8s gap) AND text similarity (>=65% word overlap OR >=50% sequence ratio)
    static func dedup(segments: [TranscriptSegment], userName: String) -> [TranscriptSegment] {
        let loopback = segments.filter { $0.speaker != userName }
        let mic = segments.filter { $0.speaker == userName }

        let filtered = mic.filter { micSeg in
            // check if any loopback segment overlaps and has similar text
            let isEcho = loopback.contains { loopSeg in
                temporalOverlap(micSeg, loopSeg, maxGap: 8.0)
                    && textSimilar(micSeg.text, loopSeg.text)
            }
            return !isEcho
        }

        return loopback + filtered
    }

    private static func temporalOverlap(_ a: TranscriptSegment, _ b: TranscriptSegment, maxGap: TimeInterval) -> Bool {
        let gap = max(a.start - b.end, b.start - a.end)
        return gap <= maxGap
    }

    private static func textSimilar(_ a: String, _ b: String) -> Bool {
        wordOverlap(a, b) >= 0.65 || sequenceRatio(a, b) >= 0.50
    }

    private static func wordOverlap(_ a: String, _ b: String) -> Double {
        let wordsA = Set(a.lowercased().split(separator: " "))
        let wordsB = Set(b.lowercased().split(separator: " "))
        guard !wordsA.isEmpty || !wordsB.isEmpty else { return 1.0 }
        let intersection = wordsA.intersection(wordsB).count
        let smaller = min(wordsA.count, wordsB.count)
        return smaller == 0 ? 0 : Double(intersection) / Double(smaller)
    }

    // simple sequence similarity (longest common subsequence ratio)
    private static func sequenceRatio(_ a: String, _ b: String) -> Double {
        let a = Array(a.lowercased())
        let b = Array(b.lowercased())
        guard !a.isEmpty || !b.isEmpty else { return 1.0 }

        var prev = [Int](repeating: 0, count: b.count + 1)
        var curr = [Int](repeating: 0, count: b.count + 1)

        for i in 1...a.count {
            for j in 1...b.count {
                if a[i - 1] == b[j - 1] {
                    curr[j] = prev[j - 1] + 1
                } else {
                    curr[j] = max(prev[j], curr[j - 1])
                }
            }
            prev = curr
            curr = [Int](repeating: 0, count: b.count + 1)
        }

        let lcs = prev[b.count]
        return Double(2 * lcs) / Double(a.count + b.count)
    }
}
