import Foundation

enum RecordingState: Equatable {
    case idle
    case recording(since: Date)
    case stopping
    case transcribing
    case processing  // claude code running
    case error(String)

    var isRecording: Bool {
        if case .recording = self { return true }
        return false
    }

    var isBusy: Bool {
        switch self {
        case .idle, .error: return false
        default: return true
        }
    }

    var label: String {
        switch self {
        case .idle: return "Idle"
        case .recording: return "Recording"
        case .stopping: return "Stopping…"
        case .transcribing: return "Transcribing…"
        case .processing: return "Processing with Claude…"
        case .error(let msg): return "Error: \(msg)"
        }
    }
}
