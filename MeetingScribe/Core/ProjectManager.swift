import Foundation

class ProjectManager: ObservableObject {
    @Published var projects: [String] = []
    @Published var selectedProject: String?
    @Published var projectMeta: ProjectMeta = ProjectMeta()

    private let config: AppConfig

    init(config: AppConfig) {
        self.config = config
        self.selectedProject = config.lastProject
        refresh()
    }

    var notesRoot: URL { config.resolvedNotesRoot }

    var selectedProjectURL: URL? {
        guard let name = selectedProject else { return nil }
        return notesRoot.appendingPathComponent(name)
    }

    func refresh() {
        let fm = FileManager.default
        guard let contents = try? fm.contentsOfDirectory(
            at: notesRoot,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else {
            projects = []
            return
        }

        projects = contents
            .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
            .map { $0.lastPathComponent }
            .sorted()

        // if no selection or selection gone, pick first
        if selectedProject == nil || !projects.contains(selectedProject ?? "") {
            selectedProject = projects.first
        }

        loadMeta()
    }

    func createProject(name: String) -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }

        let url = notesRoot.appendingPathComponent(trimmed)
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            // seed project.json and README
            ProjectMeta().save(to: url)
            let readme = "# \(trimmed)\n\n## Project Overview\n\n_To be updated after the first meeting._\n\n## Meeting Log\n"
            try? readme.write(to: url.appendingPathComponent("README.md"), atomically: true, encoding: .utf8)
            refresh()
            selectedProject = trimmed
            loadMeta()
            return true
        } catch {
            print("[ProjectManager] failed to create \(trimmed): \(error)")
            return false
        }
    }

    func select(_ project: String) {
        selectedProject = project
        loadMeta()
        var cfg = config
        cfg.lastProject = project
        cfg.save()
    }

    func saveMeta() {
        guard let url = selectedProjectURL else { return }
        projectMeta.save(to: url)
    }

    func addParticipant(_ name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !projectMeta.participants.contains(trimmed) else { return }
        projectMeta.participants.append(trimmed)
        saveMeta()
    }

    func removeParticipant(_ name: String) {
        projectMeta.participants.removeAll { $0 == name }
        saveMeta()
    }

    func addRepo(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !projectMeta.repos.contains(trimmed) else { return }
        projectMeta.repos.append(trimmed)
        saveMeta()
    }

    func removeRepo(_ path: String) {
        projectMeta.repos.removeAll { $0 == path }
        saveMeta()
    }

    func addResource(_ path: String) {
        let trimmed = path.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !projectMeta.resources.contains(trimmed) else { return }
        projectMeta.resources.append(trimmed)
        saveMeta()
    }

    func removeResource(_ path: String) {
        projectMeta.resources.removeAll { $0 == path }
        saveMeta()
    }

    private func loadMeta() {
        guard let url = selectedProjectURL else {
            projectMeta = ProjectMeta()
            return
        }
        projectMeta = ProjectMeta.load(from: url)
    }
}
