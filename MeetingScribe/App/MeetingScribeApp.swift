import SwiftUI
import Sparkle

@main
struct MeetingScribeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @Environment(\.openWindow) private var openWindow

    private let updaterController = SPUStandardUpdaterController(
        startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil
    )

    var body: some Scene {
        MenuBarExtra {
            MenuBarView(updater: updaterController.updater)
                .environmentObject(appDelegate.session)
                .environmentObject(appDelegate.projectManager)
                .task {
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
                apiKeyConfigured: appDelegate.session.isApiKeyConfigured,
                onDismiss: {
                    NSApp.keyWindow?.close()
                }
            )
        }
        .windowResizability(.contentSize)
    }
}
