import Foundation

// transcribes audio chunks via groq API (whisper-large-v3)
class Transcriber {
    private let apiKey: String
    private let endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"

    init(apiKey: String) {
        self.apiKey = apiKey
    }

    /// transcribe a WAV file via Groq API
    func transcribe(wavURL: URL) async throws -> [TranscriptSegment] {
        let audioData = try Data(contentsOf: wavURL)
        guard audioData.count > 44 else {
            throw TranscriberError.invalidWAV
        }

        let duration = Double(audioData.count - 44) / (16000.0 * 2) // 16kHz 16-bit mono
        print("[Transcriber] sending \(String(format: "%.1f", duration))s chunk to Groq")

        let boundary = UUID().uuidString
        var body = Data()

        // file field
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"file\"; filename=\"chunk.wav\"\r\n")
        body.appendUTF8("Content-Type: audio/wav\r\n\r\n")
        body.append(audioData)
        body.appendUTF8("\r\n")

        // model
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"model\"\r\n\r\n")
        body.appendUTF8("whisper-large-v3\r\n")

        // language hint for better accuracy
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"language\"\r\n\r\n")
        body.appendUTF8("en\r\n")

        // verbose_json gives us segment timestamps
        body.appendUTF8("--\(boundary)\r\n")
        body.appendUTF8("Content-Disposition: form-data; name=\"response_format\"\r\n\r\n")
        body.appendUTF8("verbose_json\r\n")

        body.appendUTF8("--\(boundary)--\r\n")

        var request = URLRequest(url: URL(string: endpoint)!)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 120

        let (data, response) = try await URLSession.shared.upload(for: request, from: body)

        guard let http = response as? HTTPURLResponse else {
            throw TranscriberError.apiFailed("no response")
        }

        guard http.statusCode == 200 else {
            let msg = String(data: data, encoding: .utf8) ?? "unknown"
            throw TranscriberError.apiFailed("HTTP \(http.statusCode): \(msg)")
        }

        let result = try JSONDecoder().decode(GroqResponse.self, from: data)

        // use segment-level timestamps when available
        if let segments = result.segments, !segments.isEmpty {
            return segments.compactMap { seg in
                let text = seg.text.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return nil }
                return TranscriptSegment(
                    start: seg.start,
                    end: seg.end,
                    speaker: "Speaker",
                    text: text
                )
            }
        }

        // fallback: single segment from full text
        let text = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return [] }
        return [TranscriptSegment(start: 0, end: duration, speaker: "Speaker", text: text)]
    }
}

// MARK: - response types

private struct GroqResponse: Decodable {
    let text: String
    let segments: [GroqSegment]?
}

private struct GroqSegment: Decodable {
    let start: Double
    let end: Double
    let text: String
}

// MARK: - multipart helper

private extension Data {
    mutating func appendUTF8(_ string: String) {
        append(string.data(using: .utf8)!)
    }
}

// MARK: - errors

enum TranscriberError: LocalizedError {
    case invalidWAV
    case noApiKey
    case apiFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidWAV: return "Invalid WAV file"
        case .noApiKey: return "Groq API key not configured"
        case .apiFailed(let msg): return "Transcription API failed: \(msg)"
        }
    }
}
