import Foundation

struct TranscriptSegment: Identifiable {
    let id = UUID()
    let start: TimeInterval   // seconds from session start
    let end: TimeInterval
    let speaker: String        // "Remote", user name, or "Speaker"
    let text: String

    var timestampLabel: String {
        let fmt = { (t: TimeInterval) -> String in
            let m = Int(t) / 60
            let s = Int(t) % 60
            return String(format: "%02d:%02d", m, s)
        }
        return "[\(fmt(start))–\(fmt(end))]"
    }

    var formattedLine: String {
        "\(timestampLabel) [\(speaker)] \(text)"
    }
}
