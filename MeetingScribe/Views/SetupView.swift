import SwiftUI

// shown on first launch if prerequisites are missing
struct SetupView: View {
    var audioteeInstalled: Bool
    var claudeInstalled: Bool
    var apiKeyConfigured: Bool
    var onDismiss: () -> Void

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

            // groq api key
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Image(systemName: apiKeyConfigured ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(apiKeyConfigured ? .green : .red)
                    Text("Groq API key")
                        .fontWeight(.medium)
                    if apiKeyConfigured {
                        Text("configured")
                            .foregroundStyle(.secondary)
                    }
                }
                if !apiKeyConfigured {
                    Text("Get a free key at console.groq.com/keys, or add GROQ_API_KEY to ~/OpenClaude/Secrets/.env")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }

            if !audioteeInstalled {
                Text("After installing audiotee, grant it Screen & System Audio Recording permission in System Settings \u{2192} Privacy & Security.")
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
        .frame(width: 480, height: 320)
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
}
