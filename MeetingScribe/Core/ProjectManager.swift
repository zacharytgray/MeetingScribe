import Foundation

class ProjectManager: ObservableObject {
    @Published var projects: [String] = []
    @Published var selectedProject: String?

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
    }

    func createProject(name: String) -> Bool {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }

        let url = notesRoot.appendingPathComponent(trimmed)
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            refresh()
            selectedProject = trimmed
            return true
        } catch {
            print("[ProjectManager] failed to create \(trimmed): \(error)")
            return false
        }
    }

    func select(_ project: String) {
        selectedProject = project
        var cfg = config
        cfg.lastProject = project
        cfg.save()
    }
}
