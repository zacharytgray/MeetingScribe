"""NSStatusItem (menu bar icon + menu) for the native macOS app."""
from __future__ import annotations

from typing import TYPE_CHECKING

from AppKit import (
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSWorkspace,
    NSURL,
)

from .app_utils import create_status_icon

if TYPE_CHECKING:
    from .app import AppController


class StatusBarController:
    """Owns the NSStatusItem and builds/rebuilds the dropdown menu."""

    def __init__(self, controller: AppController) -> None:
        self._ctrl = controller
        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.setImage_(create_status_icon(False))
        self._status_item.setToolTip_("MeetingScribe")

        # target/action for the menu items — we need an ObjC-compatible target,
        # so we use a small helper that routes selectors to python methods
        self._target = _MenuTarget.alloc().initWithController_statusBar_(controller, self)
        self._rebuild_menu()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Refresh icon and menu to reflect current controller state."""
        self._status_item.setImage_(create_status_icon(self._ctrl._recording))
        if self._ctrl._recording:
            self._status_item.setToolTip_("MeetingScribe \u2014 recording")
        elif self._ctrl._loading:
            self._status_item.setToolTip_("MeetingScribe \u2014 loading models\u2026")
        else:
            self._status_item.setToolTip_("MeetingScribe")
        self._rebuild_menu()

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _rebuild_menu(self) -> None:
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        ctrl = self._ctrl
        t = self._target

        # start / stop
        start = _item("Start Recording", t, "onStart:", not ctrl._recording and not ctrl._loading)
        menu.addItem_(start)

        stop = _item("Stop & Summarize", t, "onStop:", ctrl._recording)
        menu.addItem_(stop)

        menu.addItem_(NSMenuItem.separatorItem())

        # transcript / notes
        menu.addItem_(_item("Show Live Transcript", t, "onTranscript:", ctrl._recording))
        menu.addItem_(_item("Open Last Note", t, "onOpenLast:", ctrl._last_note_path is not None))
        menu.addItem_(_item("Open Notes Folder", t, "onOpenFolder:", True))

        menu.addItem_(NSMenuItem.separatorItem())

        # quick settings submenu
        settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Settings", None, "")
        settings_sub = NSMenu.alloc().init()
        settings_sub.setAutoenablesItems_(False)

        # model
        model_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Model: {ctrl._config.whisper_model}", None, ""
        )
        model_sub = NSMenu.alloc().init()
        for m in ("tiny", "base", "small", "medium", "large-v3"):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(m, "onSetModel:", "")
            mi.setTarget_(t)
            mi.setRepresentedObject_(m)
            if m == ctrl._config.whisper_model:
                mi.setState_(1)
            model_sub.addItem_(mi)
        model_item.setSubmenu_(model_sub)
        settings_sub.addItem_(model_item)

        # diarization toggle
        diar_title = f"Diarization: {'on' if ctrl._config.use_diarization else 'off'}"
        settings_sub.addItem_(_item(diar_title, t, "onToggleDiarization:", True))

        # meeting size
        dt = ctrl._config.diarization_threshold
        if dt <= 0.50:
            size_label = "1-on-1"
        elif dt <= 0.60:
            size_label = "small team"
        elif dt <= 0.68:
            size_label = "medium"
        else:
            size_label = "large"
        size_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Meeting size: {size_label}", None, ""
        )
        size_sub = NSMenu.alloc().init()
        for label, tag in (
            ("1-on-1 (2 people)", 1),
            ("Small team (3-4) \u2190 default", 2),
            ("Medium meeting (5-7)", 3),
            ("Large meeting (8+)", 4),
        ):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "onSetMeetingSize:", "")
            mi.setTarget_(t)
            mi.setTag_(tag)
            size_sub.addItem_(mi)
        size_item.setSubmenu_(size_sub)
        settings_sub.addItem_(size_item)

        # chunk duration
        chunk_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Chunk: {ctrl._config.chunk_seconds}s", None, ""
        )
        chunk_sub = NSMenu.alloc().init()
        for sec, label in ((30, "30s \u2014 low latency"), (60, "60s \u2014 better quality"), (90, "90s \u2014 best quality")):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, "onSetChunk:", "")
            mi.setTarget_(t)
            mi.setTag_(sec)
            if sec == ctrl._config.chunk_seconds:
                mi.setState_(1)
            chunk_sub.addItem_(mi)
        chunk_item.setSubmenu_(chunk_sub)
        settings_sub.addItem_(chunk_item)

        # provider
        active = ctrl._config.active_providers
        prov_label = active[0] if active else "none"
        prov_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            f"Summary via: {prov_label}", None, ""
        )
        prov_sub = NSMenu.alloc().init()
        if active:
            for name in active:
                mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(name, "onSetProvider:", "")
                mi.setTarget_(t)
                mi.setRepresentedObject_(name)
                if name == active[0]:
                    mi.setState_(1)
                prov_sub.addItem_(mi)
        else:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("No providers configured", None, "")
            mi.setEnabled_(False)
            prov_sub.addItem_(mi)
        prov_item.setSubmenu_(prov_sub)
        settings_sub.addItem_(prov_item)

        settings_item.setSubmenu_(settings_sub)
        menu.addItem_(settings_item)

        menu.addItem_(NSMenuItem.separatorItem())

        menu.addItem_(_item("Preferences\u2026", t, "onPreferences:", True))

        menu.addItem_(NSMenuItem.separatorItem())

        menu.addItem_(_item("Quit", t, "onQuit:", True))

        self._status_item.setMenu_(menu)


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _item(title: str, target, action: str, enabled: bool) -> NSMenuItem:
    mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
    mi.setTarget_(target)
    mi.setEnabled_(enabled)
    return mi


# ---------------------------------------------------------------------------
# ObjC target that routes menu actions to AppController
# ---------------------------------------------------------------------------

import objc
from Foundation import NSObject


class _MenuTarget(NSObject):

    def initWithController_statusBar_(self, controller, status_bar):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._ctrl = controller
        self._sb = status_bar
        return self

    @objc.typedSelector(b"v@:@")
    def onStart_(self, sender):
        self._ctrl.start_recording()

    @objc.typedSelector(b"v@:@")
    def onStop_(self, sender):
        self._ctrl.stop_recording()

    @objc.typedSelector(b"v@:@")
    def onTranscript_(self, sender):
        self._ctrl.show_live_transcript()

    @objc.typedSelector(b"v@:@")
    def onOpenLast_(self, sender):
        self._ctrl.open_last_note()

    @objc.typedSelector(b"v@:@")
    def onOpenFolder_(self, sender):
        self._ctrl.open_notes_folder()

    @objc.typedSelector(b"v@:@")
    def onPreferences_(self, sender):
        self._ctrl.show_preferences()

    @objc.typedSelector(b"v@:@")
    def onQuit_(self, sender):
        self._ctrl.quit_app()

    @objc.typedSelector(b"v@:@")
    def onSetModel_(self, sender):
        model = sender.representedObject()
        self._ctrl.set_model(model)

    @objc.typedSelector(b"v@:@")
    def onToggleDiarization_(self, sender):
        self._ctrl.toggle_diarization()

    @objc.typedSelector(b"v@:@")
    def onSetMeetingSize_(self, sender):
        presets = {
            1: (0.45, 0.60, 60),
            2: (0.55, 0.65, 30),
            3: (0.65, 0.70, 30),
            4: (0.72, 0.75, 30),
        }
        vals = presets.get(sender.tag(), (0.55, 0.65, 30))
        self._ctrl.set_meeting_size(*vals)

    @objc.typedSelector(b"v@:@")
    def onSetChunk_(self, sender):
        self._ctrl.set_chunk(sender.tag())

    @objc.typedSelector(b"v@:@")
    def onSetProvider_(self, sender):
        name = sender.representedObject()
        self._ctrl.set_primary_provider(name)
