import SwiftUI

struct RecordingIndicator: View {
    @EnvironmentObject var session: MeetingSession

    var body: some View {
        switch session.state {
        case .recording:
            Image(systemName: "mic.fill")
                .symbolRenderingMode(.palette)
                .foregroundStyle(.red)
        case .transcribing:
            Image(systemName: "waveform")
        case .processing:
            Image(systemName: "brain.head.profile")
        default:
            Image(systemName: "mic.fill")
        }
    }
}
