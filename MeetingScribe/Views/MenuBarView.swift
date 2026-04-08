import SwiftUI
import Sparkle

struct MenuBarView: View {
    @EnvironmentObject var session: MeetingSession
    @EnvironmentObject var projectManager: ProjectManager
    @Environment(\.openWindow) private var openWindow

    let updater: SPUUpdater

    @State private var newProjectName = ""
    @State private var showingNewProject = false
    @State private var newParticipant = ""
    @State private var showingParticipants = false
    @State private var newRepo = ""
    @State private var showingRepos = false
    @State private var newResource = ""
    @State private var showingResources = false

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

            // participants
            if projectManager.selectedProject != nil {
                HStack {
                    Text("Participants")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                    Spacer()
                    Button(action: { showingParticipants.toggle() }) {
                        Image(systemName: showingParticipants ? "chevron.up" : "chevron.down")
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }

                if showingParticipants {
                    // tag-style display
                    FlowLayout(spacing: 4) {
                        ForEach(projectManager.projectMeta.participants, id: \.self) { name in
                            HStack(spacing: 2) {
                                Text(name)
                                    .font(.caption)
                                Button(action: { projectManager.removeParticipant(name) }) {
                                    Image(systemName: "xmark")
                                        .font(.system(size: 8, weight: .bold))
                                }
                                .buttonStyle(.borderless)
                            }
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.quaternary)
                            .cornerRadius(4)
                        }
                    }

                    HStack {
                        TextField("Add participant", text: $newParticipant)
                            .textFieldStyle(.roundedBorder)
                            .font(.caption)
                            .onSubmit { addParticipant() }
                        Button("Add") { addParticipant() }
                            .font(.caption)
                            .disabled(newParticipant.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                // repos
                HStack {
                    Text("Repos")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                    Spacer()
                    Button(action: { showingRepos.toggle() }) {
                        Image(systemName: showingRepos ? "chevron.up" : "chevron.down")
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }

                if showingRepos {
                    ForEach(projectManager.projectMeta.repos, id: \.self) { repo in
                        HStack(spacing: 4) {
                            Image(systemName: "folder")
                                .font(.system(size: 9))
                                .foregroundStyle(.secondary)
                            Text(repo)
                                .font(.caption)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer()
                            Button(action: { projectManager.removeRepo(repo) }) {
                                Image(systemName: "xmark")
                                    .font(.system(size: 8, weight: .bold))
                            }
                            .buttonStyle(.borderless)
                        }
                    }

                    HStack {
                        TextField("~/path/to/repo", text: $newRepo)
                            .textFieldStyle(.roundedBorder)
                            .font(.caption)
                            .onSubmit { addRepo() }
                        Button("Add") { addRepo() }
                            .font(.caption)
                            .disabled(newRepo.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }

                // resources
                HStack {
                    Text("Resources")
                        .foregroundStyle(.secondary)
                        .font(.caption)
                    Spacer()
                    Button(action: { showingResources.toggle() }) {
                        Image(systemName: showingResources ? "chevron.up" : "chevron.down")
                    }
                    .buttonStyle(.borderless)
                    .font(.caption)
                }

                if showingResources {
                    ForEach(projectManager.projectMeta.resources, id: \.self) { resource in
                        HStack(spacing: 4) {
                            Image(systemName: "doc.on.doc")
                                .font(.system(size: 9))
                                .foregroundStyle(.secondary)
                            Text(resource)
                                .font(.caption)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer()
                            Button(action: { projectManager.removeResource(resource) }) {
                                Image(systemName: "xmark")
                                    .font(.system(size: 8, weight: .bold))
                            }
                            .buttonStyle(.borderless)
                        }
                    }

                    HStack {
                        TextField("~/Google Drive/Research/...", text: $newResource)
                            .textFieldStyle(.roundedBorder)
                            .font(.caption)
                            .onSubmit { addResource() }
                        Button("Add") { addResource() }
                            .font(.caption)
                            .disabled(newResource.trimmingCharacters(in: .whitespaces).isEmpty)
                    }
                }
            }

            Divider()

            // record / stop
            if session.state.isRecording {
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

                if !session.isApiKeyConfigured {
                    Label("Groq API key not configured", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                        .font(.caption)
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

            Button(action: { updater.checkForUpdates() }) {
                Label("Check for Updates…", systemImage: "arrow.clockwise.circle")
            }

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
            try session.startRecording(projectURL: url, projectMeta: projectManager.projectMeta)
        } catch {
            print("[MenuBarView] failed to start: \(error)")
        }
    }

    private func createProject() {
        let name = newProjectName.trimmingCharacters(in: .whitespaces)
        if projectManager.createProject(name: name) {
            newProjectName = ""
            showingNewProject = false
        }
    }

    private func addParticipant() {
        let name = newParticipant.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        projectManager.addParticipant(name)
        newParticipant = ""
    }

    private func addRepo() {
        let path = newRepo.trimmingCharacters(in: .whitespaces)
        guard !path.isEmpty else { return }
        projectManager.addRepo(path)
        newRepo = ""
    }

    private func addResource() {
        let path = newResource.trimmingCharacters(in: .whitespaces)
        guard !path.isEmpty else { return }
        projectManager.addResource(path)
        newResource = ""
    }
}

// simple horizontal flow layout for tags
struct FlowLayout: Layout {
    var spacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = computeRows(proposal: proposal, subviews: subviews)
        let height = rows.reduce(CGFloat(0)) { acc, row in
            let rowH = row.map { $0.sizeThatFits(.unspecified).height }.max() ?? 0
            return acc + rowH + (acc > 0 ? spacing : 0)
        }
        return CGSize(width: proposal.width ?? 0, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = computeRows(proposal: proposal, subviews: subviews)
        var y = bounds.minY
        for row in rows {
            let rowH = row.map { $0.sizeThatFits(.unspecified).height }.max() ?? 0
            var x = bounds.minX
            for view in row {
                let size = view.sizeThatFits(.unspecified)
                view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += rowH + spacing
        }
    }

    private func computeRows(proposal: ProposedViewSize, subviews: Subviews) -> [[LayoutSubviews.Element]] {
        let maxW = proposal.width ?? .infinity
        var rows: [[LayoutSubviews.Element]] = [[]]
        var rowW: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if rowW + size.width + spacing > maxW && !rows[rows.count - 1].isEmpty {
                rows.append([])
                rowW = 0
            }
            rows[rows.count - 1].append(view)
            rowW += size.width + spacing
        }
        return rows
    }
}
