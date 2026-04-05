import SwiftUI

struct RecordingIndicator: View {
    @EnvironmentObject var session: MeetingSession

    var body: some View {
        switch session.state {
        case .recording:
            Image(systemName: "mic.fill")
                .symbolRenderingMode(.palette)
                .foregroundStyle(.red)
        case .transcribing, .processing:
            Image(systemName: "mic.badge.ellipsis")
        default:
            Image(systemName: "mic.fill")
        }
    }
}
