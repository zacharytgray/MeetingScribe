import SwiftUI

// shown on first launch if prerequisites are missing
struct SetupView: View {
    var audioteeInstalled: Bool
    var claudeInstalled: Bool
    var modelReady: Bool
    var onDownloadModel: (@escaping (Double) -> Void) async throws -> Void
    var onDismiss: () -> Void

    @State private var downloadProgress: Double? = nil
    @State private var downloadError: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("MeetingScribe Setup")
                .font(.title2.bold())

            prerequisiteRow(
                "audiotee",
                installed: audioteeInstalled,
                help: "Build from source: git clone https://github.com/makeusabrew/audiotee && cd audiotee && swift build -c release && cp .build/release/audiotee ~/.local/bin/"
            )

            prerequisiteRow(
                "Claude CLI",
                installed: claudeInstalled,
                help: "Install Claude Code: https://docs.anthropic.com/en/docs/claude-code"
            )

            // whisper model
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Image(systemName: modelReady ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(modelReady ? .green : .red)
                    Text("Whisper model")
                        .fontWeight(.medium)
                    if modelReady {
                        Text("downloaded")
                            .foregroundStyle(.secondary)
                    }
                }
                if !modelReady {
                    if let progress = downloadProgress {
                        HStack {
                            ProgressView(value: progress)
                                .frame(maxWidth: 200)
                            Text("\(Int(progress * 100))%")
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Button("Download Model") { downloadModel() }
                    }
                    if let downloadError {
                        Text(downloadError)
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
            }

            if !audioteeInstalled {
                Text("After installing audiotee, grant it Screen & System Audio Recording permission in System Settings → Privacy & Security.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            HStack {
                Spacer()
                Button("Done") { onDismiss() }
                    .keyboardShortcut(.return)
            }
        }
        .padding(24)
        .frame(width: 480, height: 360)
    }

    private func prerequisiteRow(_ name: String, installed: Bool, help: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Image(systemName: installed ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(installed ? .green : .red)
                Text(name)
                    .fontWeight(.medium)
                if installed {
                    Text("installed")
                        .foregroundStyle(.secondary)
                }
            }
            if !installed {
                Text(help)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
        }
    }

    private func downloadModel() {
        guard downloadProgress == nil else { return }
        downloadProgress = 0
        downloadError = nil
        Task {
            do {
                try await onDownloadModel { pct in
                    downloadProgress = pct
                }
                downloadProgress = nil
            } catch {
                downloadProgress = nil
                downloadError = error.localizedDescription
            }
        }
    }
}
