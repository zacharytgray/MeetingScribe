import SwiftUI

struct SettingsView: View {
    @State private var config = AppConfig.load()
    @State private var saved = false

    var body: some View {
        Form {
            Section("Transcription") {
                SecureField("Groq API Key", text: Binding(
                    get: { config.groqApiKey ?? "" },
                    set: { config.groqApiKey = $0.isEmpty ? nil : $0 }
                ))
                if config.resolvedGroqApiKey != nil {
                    Label("API key configured", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption)
                } else {
                    Text("Get a free key at console.groq.com/keys")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Section("Microphone") {
                Toggle("Enable mic recording", isOn: $config.micEnabled)
                if config.micEnabled {
                    TextField("Your name", text: $config.userName)
                }
            }

            Section("Recording") {
                Stepper("Chunk length: \(config.chunkSeconds)s", value: $config.chunkSeconds, in: 10...120, step: 10)
            }

            Section("Paths") {
                TextField("Meeting notes root", text: $config.meetingNotesRoot)
                HStack {
                    TextField("Claude CLI path (auto-detected if empty)", text: Binding(
                        get: { config.claudePath ?? "" },
                        set: { config.claudePath = $0.isEmpty ? nil : $0 }
                    ))
                    if let resolved = config.resolvedClaudePath {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                            .help(resolved)
                    } else {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.red)
                            .help("claude not found")
                    }
                }
            }

            Section("Processing") {
                Toggle("Auto-process with Claude after recording", isOn: $config.autoProcess)
            }

            HStack {
                Spacer()
                if saved {
                    Text("Saved").foregroundStyle(.green)
                }
                Button("Save") {
                    config.save()
                    saved = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { saved = false }
                }
                .keyboardShortcut("s", modifiers: .command)
            }
        }
        .formStyle(.grouped)
        .frame(minWidth: 450, minHeight: 350)
    }
}
