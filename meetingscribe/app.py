"""Native macOS app for MeetingScribe — NSApplication + menu bar + settings window."""
from __future__ import annotations

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import platform
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSMenu,
    NSMenuItem,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

from .config import Config, load_config, save_config
from .session import MeetingSession
from .transcriber import TranscriptSegment
from .app_utils import create_status_icon, dispatch_to_main, setup_app_icon


class AppController:
    """Central state holder — referenced by both the menu bar and settings window."""

    def __init__(self) -> None:
        self._config: Config = load_config()
        self._session: Optional[MeetingSession] = None
        self._recording = False
        self._loading = False
        self._last_note_path: Optional[Path] = None
        self._status_msg = "Idle"
        self._lock = threading.Lock()

        # set after construction by main()
        self._status_bar = None       # StatusBarController
        self._settings_window = None  # SettingsWindow

    # ------------------------------------------------------------------
    # recording lifecycle (called from menu bar or future app controls)
    # ------------------------------------------------------------------

    def start_recording(self) -> None:
        with self._lock:
            if self._recording or self._loading:
                return
            self._loading = True
        self._refresh_ui()
        threading.Thread(target=self._load_and_start, daemon=True).start()

    def stop_recording(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        self._refresh_ui()
        threading.Thread(target=self._stop_and_save, daemon=True).start()

    def _load_and_start(self) -> None:
        try:
            self._session = MeetingSession(
                self._config,
                on_segment=self._on_segment,
                on_status=self._on_status,
            )
            self._session.load_models()
            self._session.start()
            with self._lock:
                self._recording = True
                self._loading = False
            dispatch_to_main(self._refresh_ui)
        except Exception as e:
            with self._lock:
                self._loading = False
            dispatch_to_main(lambda: self._show_error(f"Failed to start: {e}"))
            dispatch_to_main(self._refresh_ui)

    def _stop_and_save(self) -> None:
        try:
            path = self._session.stop()
            if path:
                self._last_note_path = path
            dispatch_to_main(self._refresh_ui)
        except Exception as e:
            dispatch_to_main(lambda: self._show_error(f"Error saving: {e}"))
        finally:
            dispatch_to_main(self._refresh_ui)

    # ------------------------------------------------------------------
    # quick settings (from menu bar)
    # ------------------------------------------------------------------

    def set_model(self, model: str) -> None:
        self._config.whisper_model = model
        save_config(self._config)
        self._refresh_ui()

    def toggle_diarization(self) -> None:
        self._config.use_diarization = not self._config.use_diarization
        save_config(self._config)
        self._refresh_ui()

    def set_meeting_size(self, dt: float, st: float, cs: int) -> None:
        self._config.diarization_threshold = dt
        self._config.speaker_tracker_threshold = st
        self._config.chunk_seconds = cs
        save_config(self._config)
        self._refresh_ui()

    def set_chunk(self, seconds: int) -> None:
        self._config.chunk_seconds = seconds
        save_config(self._config)
        self._refresh_ui()

    def set_primary_provider(self, name: str) -> None:
        order = list(self._config.provider_order)
        if name in order:
            order.remove(name)
        self._config.provider_order = [name] + order
        save_config(self._config)
        self._refresh_ui()

    # ------------------------------------------------------------------
    # window / file actions
    # ------------------------------------------------------------------

    def show_preferences(self) -> None:
        if self._settings_window:
            self._settings_window.show()

    def show_live_transcript(self) -> None:
        transcript = self._session.get_live_transcript() if self._session else ""
        if not transcript:
            transcript = "(no transcript yet)"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="meetingscribe_transcript_",
            delete=False, encoding="utf-8",
        ) as f:
            f.write(transcript)
            _open_file(f.name)

    def open_last_note(self) -> None:
        if self._last_note_path and self._last_note_path.exists():
            _open_file(str(self._last_note_path))

    def open_notes_folder(self) -> None:
        folder = self._config.resolved_output_dir
        folder.mkdir(parents=True, exist_ok=True)
        _open_file(str(folder))

    def quit_app(self) -> None:
        if self._recording and self._session:
            try:
                self._session.stop()
            except Exception:
                pass
        NSApp.terminate_(None)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _on_segment(self, seg: TranscriptSegment) -> None:
        pass  # required callback; could update live transcript here later

    def _on_status(self, msg: str) -> None:
        self._status_msg = msg

    def _refresh_ui(self) -> None:
        if self._status_bar:
            self._status_bar.update()

    def _show_error(self, msg: str) -> None:
        from AppKit import NSAlert
        alert = NSAlert.alloc().init()
        alert.setMessageText_("MeetingScribe Error")
        alert.setInformativeText_(msg)
        alert.runModal()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _open_file(path: str) -> None:
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# app delegate — handles reopen (clicking dock icon when window is closed)
# ---------------------------------------------------------------------------

class _AppDelegate(NSObject):

    def initWithController_(self, controller):
        self = objc.super(_AppDelegate, self).init()
        if self is None:
            return None
        self._ctrl = controller
        return self

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_visible):
        if not has_visible:
            self._ctrl.show_preferences()
        return True

    def applicationWillTerminate_(self, notification):
        # clean up any active recording session
        if self._ctrl._recording and self._ctrl._session:
            try:
                self._ctrl._session.stop()
            except Exception:
                pass

    # Cmd+, menu action
    @objc.typedSelector(b"v@:@")
    def showPreferences_(self, sender):
        self._ctrl.show_preferences()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _setup_main_menu(controller) -> None:
    """Create a minimal app menu (visible when preferences window is open)."""
    menubar = NSMenu.alloc().init()

    # app menu
    app_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(app_menu_item)
    app_menu = NSMenu.alloc().init()

    about = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("About MeetingScribe", "orderFrontStandardAboutPanel:", "")
    app_menu.addItem_(about)
    app_menu.addItem_(NSMenuItem.separatorItem())

    prefs = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Preferences\u2026", "showPreferences:", ",")
    app_menu.addItem_(prefs)
    app_menu.addItem_(NSMenuItem.separatorItem())

    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit MeetingScribe", "terminate:", "q")
    app_menu.addItem_(quit_item)

    app_menu_item.setSubmenu_(app_menu)
    NSApp.setMainMenu_(menubar)


def main() -> None:
    # pre-init torch before AppKit (prevents segfault on macOS)
    import torch
    torch.zeros(1)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    controller = AppController()

    # app icon (dock + about panel)
    setup_app_icon(app)

    # main menu bar (visible when preferences window is open and dock icon shows)
    _setup_main_menu(controller)

    # status bar (menu bar icon)
    from .app_statusitem import StatusBarController
    controller._status_bar = StatusBarController(controller)

    # settings window
    from .app_window import SettingsWindow
    controller._settings_window = SettingsWindow(controller)

    # app delegate for dock icon clicks + Cmd+, + quit
    delegate = _AppDelegate.alloc().initWithController_(controller)
    app.setDelegate_(delegate)

    # always open the main window on launch — behave like a normal app
    controller.show_preferences()

    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
