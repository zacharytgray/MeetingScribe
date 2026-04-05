import SwiftUI

@main
struct MeetingScribeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        MenuBarExtra {
            MenuBarView()
                .environmentObject(appDelegate.session)
                .environmentObject(appDelegate.projectManager)
                .task {
                    // auto-show setup on first launch if prerequisites missing
                    if appDelegate.needsSetup {
                        openWindow(id: "setup")
                        NSApp.activate(ignoringOtherApps: true)
                    }
                }
        } label: {
            RecordingIndicator()
                .environmentObject(appDelegate.session)
        }
        .menuBarExtraStyle(.window)

        Window("MeetingScribe Settings", id: "settings") {
            SettingsView()
        }
        .defaultSize(width: 500, height: 400)

        Window("Setup", id: "setup") {
            SetupView(
                audioteeInstalled: AudioRecorder.isAudioteeInstalled,
                claudeInstalled: ClaudeProcessor.isClaudeInstalled,
                modelReady: appDelegate.session.isModelReady,
                onDownloadModel: { progress in
                    try await appDelegate.session.downloadModel(progress: progress)
                },
                onDismiss: {
                    NSApp.keyWindow?.close()
                }
            )
        }
        .windowResizability(.contentSize)
    }
}
