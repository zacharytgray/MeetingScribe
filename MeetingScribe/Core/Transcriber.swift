import Foundation
import SwiftWhisper

// handles whisper model lifecycle and chunk transcription
class Transcriber {
    private var whisper: Whisper?
    private let modelName: String

    static let modelsDir: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("MeetingScribe/models")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    // huggingface ggml model URLs
    private static let modelURLs: [String: String] = [
        "tiny":    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin",
        "base":    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin",
        "small":   "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin",
        "medium":  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.en.bin",
        "large":   "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin",
    ]

    init(modelName: String = "small") {
        self.modelName = modelName
    }

    // MARK: - model management

    var modelPath: URL {
        let filename = modelName.contains("large") ? "ggml-\(modelName)-v3.bin" : "ggml-\(modelName).en.bin"
        return Self.modelsDir.appendingPathComponent(filename)
    }

    var isModelDownloaded: Bool {
        FileManager.default.fileExists(atPath: modelPath.path)
    }

    /// download model from huggingface, calls progress(0.0...1.0) periodically
    func downloadModel(progress: @escaping (Double) -> Void) async throws {
        guard let urlStr = Self.modelURLs[modelName],
              let url = URL(string: urlStr) else {
            throw TranscriberError.unknownModel(modelName)
        }

        if isModelDownloaded {
            progress(1.0)
            return
        }

        print("[Transcriber] downloading \(modelName) model from \(urlStr)")

        let dest = modelPath
        // use delegate-based download with continuation — avoids async API + delegate conflicts
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let delegate = DownloadProgressDelegate(
                progress: progress,
                destination: dest,
                continuation: cont
            )
            let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
            delegate.session = session
            session.downloadTask(with: url).resume()
        }

        progress(1.0)
        print("[Transcriber] model saved to \(dest.path)")
    }

    // MARK: - transcription

    func loadModel() throws {
        guard isModelDownloaded else {
            throw TranscriberError.modelNotFound(modelPath.path)
        }

        let params = WhisperParams(strategy: .greedy)
        params.language = .english
        params.no_context = true      // each chunk is independent
        params.single_segment = false
        params.print_progress = false
        params.print_timestamps = false

        // use most available cores for CPU-only inference
        let cores = ProcessInfo.processInfo.activeProcessorCount
        params.n_threads = Int32(max(cores - 1, 1))
        print("[Transcriber] using \(params.n_threads) threads (of \(cores) cores)")

        // warn about slow models on Intel (no CoreML/ANE acceleration)
        #if !arch(arm64)
        if modelName == "medium" || modelName == "large" {
            print("[Transcriber] WARNING: \(modelName) model on Intel will be very slow — consider 'small' or 'base'")
        }
        #endif

        whisper = Whisper(fromFileURL: modelPath, withParams: params)
        print("[Transcriber] loaded \(modelName) model from \(modelPath.path)")
    }

    /// transcribe a WAV file, returns segments with times relative to chunk start
    func transcribe(wavURL: URL) async throws -> [TranscriptSegment] {
        guard let whisper else {
            throw TranscriberError.modelNotLoaded
        }

        let frames = try Self.loadWAVAsFloat(url: wavURL)
        print("[Transcriber] transcribing \(String(format: "%.1f", Double(frames.count) / 16000.0))s of audio")

        let segments = try await whisper.transcribe(audioFrames: frames)

        return segments.map { seg in
            TranscriptSegment(
                start: Double(seg.startTime) / 1000.0,
                end: Double(seg.endTime) / 1000.0,
                speaker: "Speaker",
                text: seg.text.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }.filter { !$0.text.isEmpty }
    }

    func unloadModel() {
        whisper = nil
    }

    // MARK: - WAV loading

    // read 16-bit mono PCM WAV and convert to normalized float array
    static func loadWAVAsFloat(url: URL) throws -> [Float] {
        let data = try Data(contentsOf: url)
        guard data.count > 44 else {
            throw TranscriberError.invalidWAV
        }

        // skip 44-byte WAV header, read PCM samples
        let pcmData = data.dropFirst(44)
        let sampleCount = pcmData.count / 2

        return pcmData.withUnsafeBytes { raw in
            let int16s = raw.bindMemory(to: Int16.self)
            var floats = [Float](repeating: 0, count: sampleCount)
            for i in 0..<sampleCount {
                floats[i] = Float(int16s[i]) / 32768.0
            }
            return floats
        }
    }
}

// MARK: - download progress delegate

private class DownloadProgressDelegate: NSObject, URLSessionDownloadDelegate {
    let progress: (Double) -> Void
    let destination: URL
    var session: URLSession?
    private var continuation: CheckedContinuation<Void, Error>?
    private var resumed = false

    // known model sizes (bytes) for progress when Content-Length missing after redirect
    private static let expectedSizes: [String: Int64] = [
        "ggml-tiny.en.bin": 77_691_713,
        "ggml-base.en.bin": 147_951_465,
        "ggml-small.en.bin": 487_601_967,
        "ggml-medium.en.bin": 1_533_774_781,
        "ggml-large-v3.bin": 3_094_623_691,
    ]

    init(progress: @escaping (Double) -> Void, destination: URL, continuation: CheckedContinuation<Void, Error>) {
        self.progress = progress
        self.destination = destination
        self.continuation = continuation
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData bytesWritten: Int64, totalBytesWritten: Int64,
                    totalBytesExpectedToWrite: Int64) {
        var total = totalBytesExpectedToWrite
        // HF redirects can lose Content-Length — fall back to known sizes
        if total <= 0 {
            let filename = destination.lastPathComponent
            total = Self.expectedSizes[filename] ?? 0
        }
        guard total > 0 else { return }
        let pct = min(Double(totalBytesWritten) / Double(total), 0.99)
        DispatchQueue.main.async { self.progress(pct) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        do {
            // remove any partial leftover from a previous attempt
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)
            resume(with: .success(()))
        } catch {
            resume(with: .failure(error))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        self.session?.invalidateAndCancel()
        if let error {
            resume(with: .failure(error))
        }
    }

    private func resume(with result: Result<Void, Error>) {
        guard !resumed else { return }
        resumed = true
        switch result {
        case .success: continuation?.resume()
        case .failure(let e): continuation?.resume(throwing: e)
        }
        continuation = nil
    }
}

enum TranscriberError: LocalizedError {
    case unknownModel(String)
    case downloadFailed
    case modelNotFound(String)
    case modelNotLoaded
    case invalidWAV

    var errorDescription: String? {
        switch self {
        case .unknownModel(let name): return "Unknown model: \(name)"
        case .downloadFailed: return "Model download failed"
        case .modelNotFound(let path): return "Model not found at \(path)"
        case .modelNotLoaded: return "Whisper model not loaded"
        case .invalidWAV: return "Invalid WAV file"
        }
    }
}
