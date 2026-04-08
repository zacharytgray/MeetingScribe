import Foundation

// per-project metadata stored in <project>/project.json
struct ProjectMeta: Codable {
    var participants: [String] = []
    var repos: [String] = []      // paths to related source repos
    var resources: [String] = []  // paths to research materials, e.g. Google Drive folders

    // MARK: - persistence

    static func load(from projectURL: URL) -> ProjectMeta {
        let url = projectURL.appendingPathComponent("project.json")
        guard let data = try? Data(contentsOf: url),
              let meta = try? JSONDecoder().decode(ProjectMeta.self, from: data) else {
            return ProjectMeta()
        }
        return meta
    }

    func save(to projectURL: URL) {
        let url = projectURL.appendingPathComponent("project.json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return }
        try? data.write(to: url, options: .atomic)
    }
}
