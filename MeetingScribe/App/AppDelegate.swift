import Cocoa

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate {
    var config = AppConfig.load()
    lazy var projectManager = ProjectManager(config: config)
    lazy var session = MeetingSession(config: config)

    var needsSetup: Bool {
        !AudioRecorder.isAudioteeInstalled
            || !ClaudeProcessor.isClaudeInstalled
            || !session.isModelReady
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        print("[MeetingScribe] launched, notes root: \(config.resolvedNotesRoot.path)")
        if !AudioRecorder.isAudioteeInstalled { print("[MeetingScribe] warning: audiotee not found") }
        if !ClaudeProcessor.isClaudeInstalled { print("[MeetingScribe] warning: claude CLI not found") }
        print("[MeetingScribe] projects: \(projectManager.projects)")
    }

    func applicationWillTerminate(_ notification: Notification) {
        if session.state.isRecording {
            session.stopRecording()
        }
        AudioRecorder.cleanupOrphans()
    }
}
