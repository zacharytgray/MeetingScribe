import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var session: MeetingSession
    @EnvironmentObject var projectManager: ProjectManager
    @Environment(\.openWindow) private var openWindow

    @State private var newProjectName = ""
    @State private var showingNewProject = false
    @State private var downloadProgress: Double? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // project picker
            HStack {
                Text("Project:")
                    .foregroundStyle(.secondary)
                Picker("", selection: Binding(
                    get: { projectManager.selectedProject ?? "" },
                    set: { projectManager.select($0) }
                )) {
                    ForEach(projectManager.projects, id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: .infinity)

                Button(action: { showingNewProject.toggle() }) {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
            }

            if showingNewProject {
                HStack {
                    TextField("New project name", text: $newProjectName)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { createProject() }
                    Button("Create") { createProject() }
                        .disabled(newProjectName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }

            Divider()

            // model download
            if let progress = downloadProgress {
                HStack {
                    ProgressView(value: progress)
                        .frame(maxWidth: .infinity)
                    Text("\(Int(progress * 100))%")
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
                Text("Downloading whisper model…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            // record / stop
            else if session.state.isRecording {
                HStack {
                    Image(systemName: "record.circle.fill")
                        .foregroundStyle(.red)
                    Text(session.formattedDuration)
                        .monospacedDigit()
                    Spacer()
                }

                if !session.transcriptionProgress.isEmpty {
                    Text("Transcribing: \(session.transcriptionProgress)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Button(action: { session.stopRecording() }) {
                    Label("Stop Recording", systemImage: "stop.fill")
                }
                .keyboardShortcut("r", modifiers: .command)
            } else if session.state.isBusy {
                HStack {
                    ProgressView()
                        .controlSize(.small)
                    Text(session.state.label)
                        .foregroundStyle(.secondary)
                }
                if !session.transcriptionProgress.isEmpty {
                    Text(session.transcriptionProgress)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                // idle or error
                if case .error(let msg) = session.state {
                    Text(msg)
                        .foregroundStyle(.red)
                        .font(.caption)
                }

                // last claude processing result
                switch session.claudeStatus {
                case .completed(let summary):
                    Label(summary, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption)
                case .failed(let msg):
                    Label(msg, systemImage: "xmark.circle.fill")
                        .foregroundStyle(.red)
                        .font(.caption)
                        .lineLimit(2)
                default:
                    EmptyView()
                }

                if !session.isModelReady {
                    Button(action: { downloadModel() }) {
                        Label("Download Whisper Model", systemImage: "arrow.down.circle")
                    }
                }

                Button(action: { startRecording() }) {
                    Label("Start Recording", systemImage: "record.circle")
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(projectManager.selectedProject == nil)

                if !AudioRecorder.isAudioteeInstalled {
                    Label("audiotee not installed", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                        .font(.caption)
                }
            }

            Divider()

            Button(action: {
                openWindow(id: "settings")
                NSApp.activate(ignoringOtherApps: true)
            }) {
                Label("Settings…", systemImage: "gear")
            }
            .keyboardShortcut(",", modifiers: .command)

            Button(action: { NSApp.terminate(nil) }) {
                Label("Quit", systemImage: "power")
            }
            .keyboardShortcut("q", modifiers: .command)
        }
        .padding(12)
        .frame(width: 280)
    }

    private func startRecording() {
        guard let url = projectManager.selectedProjectURL else { return }
        do {
            try session.startRecording(projectURL: url)
        } catch {
            print("[MenuBarView] failed to start: \(error)")
        }
    }

    private func downloadModel() {
        guard downloadProgress == nil else { return }
        downloadProgress = 0
        Task {
            do {
                try await session.downloadModel { pct in
                    downloadProgress = pct
                }
                downloadProgress = nil
            } catch {
                print("[MenuBarView] model download failed: \(error)")
                downloadProgress = nil
            }
        }
    }

    private func createProject() {
        let name = newProjectName.trimmingCharacters(in: .whitespaces)
        if projectManager.createProject(name: name) {
            newProjectName = ""
            showingNewProject = false
        }
    }
}
