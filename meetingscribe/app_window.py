"""Settings window for the native macOS app — toolbar-based preferences."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import objc
from AppKit import (
    NSAlert,
    NSApp,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSBox,
    NSButton,
    NSColor,
    NSFont,
    NSImage,
    NSOffState,
    NSOnState,
    NSOpenPanel,
    NSPopUpButton,
    NSResizableWindowMask,
    NSScrollView,
    NSSecureTextField,
    NSTextField,
    NSTextView,
    NSToolbar,
    NSToolbarItem,
    NSView,
    NSWindow,
)
from Foundation import NSMakeRect, NSObject, NSSize

from .config import KNOWN_PROVIDERS, save_config

if TYPE_CHECKING:
    from .app import AppController

# layout
_W = 620
_H = 520
_PAD = 24
_LABEL_W = 140
_FIELD_X = _PAD + _LABEL_W + 10
_ROW = 26
_GAP = 8
_SECTION_GAP = 18

# toolbar
_TABS = ["general", "audio", "transcription", "summarization", "transcript"]
_TAB_LABELS = {
    "general": "General",
    "audio": "Audio",
    "transcription": "Transcription",
    "summarization": "Summarization",
    "transcript": "Transcript",
}
_TAB_ICONS = {
    "general": "gearshape",
    "audio": "waveform",
    "transcription": "text.bubble",
    "summarization": "brain.head.profile",
    "transcript": "doc.text",
}


# ---------------------------------------------------------------------------
# Flipped view — y=0 at top, natural top-down layout
# ---------------------------------------------------------------------------

class _FlippedView(NSView):
    def isFlipped(self):
        return True


# ---------------------------------------------------------------------------
# Layout builder — accumulates controls top-down in a flipped view
# ---------------------------------------------------------------------------

class _LayoutBuilder:
    """Lays out controls sequentially in a flipped NSView (top to bottom)."""

    def __init__(self, width: int) -> None:
        self._w = width
        self._y = _PAD
        self._view = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, width, 2000))
        self._field_w = width - _FIELD_X - _PAD

    @property
    def view(self) -> NSView:
        # trim view height to content
        self._view.setFrameSize_(NSSize(self._w, self._y + _PAD))
        return self._view

    def section(self, title: str) -> None:
        """Bold section header with separator below."""
        if self._y > _PAD:
            self._y += _SECTION_GAP
        lbl = _make_label(title, _PAD, self._y, self._w - _PAD * 2, bold=True)
        self._view.addSubview_(lbl)
        self._y += 22
        sep = NSBox.alloc().initWithFrame_(NSMakeRect(_PAD, self._y, self._w - _PAD * 2, 1))
        sep.setBoxType_(2)  # NSBoxSeparator type
        self._view.addSubview_(sep)
        self._y += 8

    def row_field(self, label: str, value: str, target, action: str,
                  identifier: str = "", placeholder: str = "", secure: bool = False,
                  enabled: bool = True) -> NSTextField:
        """Label + text field row. Returns the field."""
        self._add_label(label)
        cls = NSSecureTextField if secure else NSTextField
        f = cls.alloc().initWithFrame_(NSMakeRect(_FIELD_X, self._y, self._field_w, _ROW))
        f.setStringValue_(value)
        f.setEditable_(True)
        f.setEnabled_(enabled)
        f.setFont_(NSFont.systemFontOfSize_(13))
        f.setTarget_(target)
        f.setAction_(action)
        if placeholder:
            f.setPlaceholderString_(placeholder)
        if identifier:
            f.setIdentifier_(identifier)
        self._view.addSubview_(f)
        self._y += _ROW + _GAP
        return f

    def row_field_with_button(self, label: str, value: str, target, action: str,
                              btn_title: str, btn_action: str,
                              placeholder: str = "") -> NSTextField:
        """Label + text field + button row."""
        self._add_label(label)
        bw = 78
        f = NSTextField.alloc().initWithFrame_(
            NSMakeRect(_FIELD_X, self._y, self._field_w - bw - 6, _ROW)
        )
        f.setStringValue_(value)
        f.setEditable_(True)
        f.setFont_(NSFont.systemFontOfSize_(13))
        f.setTarget_(target)
        f.setAction_(action)
        if placeholder:
            f.setPlaceholderString_(placeholder)
        self._view.addSubview_(f)

        btn = _make_button(btn_title, _FIELD_X + self._field_w - bw, self._y, bw, target, btn_action)
        self._view.addSubview_(btn)
        self._y += _ROW + _GAP
        return f

    def row_popup(self, label: str, items: list[str], selected: str,
                  target, action: str, width: int = 0, enabled: bool = True) -> NSPopUpButton:
        """Label + popup button row."""
        self._add_label(label)
        w = width or self._field_w
        p = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(_FIELD_X, self._y, w, _ROW), False
        )
        for item in items:
            p.addItemWithTitle_(item)
        if selected:
            p.selectItemWithTitle_(selected)
        p.setTarget_(target)
        p.setAction_(action)
        p.setEnabled_(enabled)
        self._view.addSubview_(p)
        self._y += _ROW + _GAP
        return p

    def row_popup_with_button(self, label: str, popup: NSPopUpButton,
                              target, btn_title: str, btn_action: str) -> None:
        """Label + already-built popup + button."""
        self._add_label(label)
        bw = 78
        popup.setFrame_(NSMakeRect(_FIELD_X, self._y, self._field_w - bw - 6, _ROW))
        self._view.addSubview_(popup)
        btn = _make_button(btn_title, _FIELD_X + self._field_w - bw, self._y, bw, target, btn_action)
        self._view.addSubview_(btn)
        self._y += _ROW + _GAP

    def row_checkbox(self, label: str, title: str, checked: bool,
                     target, action: str) -> NSButton:
        """Label + checkbox row."""
        self._add_label(label)
        cb = NSButton.alloc().initWithFrame_(NSMakeRect(_FIELD_X, self._y, self._field_w, _ROW))
        cb.setButtonType_(3)  # NSSwitchButton type
        cb.setTitle_(title)
        cb.setState_(NSOnState if checked else NSOffState)
        cb.setFont_(NSFont.systemFontOfSize_(13))
        cb.setTarget_(target)
        cb.setAction_(action)
        self._view.addSubview_(cb)
        self._y += _ROW + _GAP
        return cb

    def hint(self, text: str) -> None:
        """Small grey hint text, indented to field column."""
        h = _make_label(text, _FIELD_X, self._y - 4, self._field_w, size=11,
                        color=NSColor.tertiaryLabelColor())
        self._view.addSubview_(h)
        self._y += 16

    def spacer(self, height: int = 8) -> None:
        self._y += height

    def _add_label(self, text: str) -> None:
        lbl = _make_label(text, _PAD, self._y + 2, _LABEL_W, align_right=True,
                          color=NSColor.secondaryLabelColor())
        self._view.addSubview_(lbl)


# ---------------------------------------------------------------------------
# SettingsWindow
# ---------------------------------------------------------------------------

class SettingsWindow:

    def __init__(self, controller: AppController) -> None:
        self._ctrl = controller
        self._window: NSWindow | None = None
        self._delegate = _Delegate.alloc().initWithOwner_(self)
        self._fields: dict[str, object] = {}
        self._tab_views: dict[str, NSView] = {}
        self._current_tab = "general"
        self._transcript_view: NSTextView | None = None

    def show(self) -> None:
        if self._window is None:
            self._build()
        from .app_utils import setup_app_icon
        setup_app_icon(NSApp)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self._window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def hide(self) -> None:
        if self._window:
            self._window.orderOut_(None)
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    @property
    def is_visible(self) -> bool:
        return self._window is not None and self._window.isVisible()

    def update_transcript(self, text: str) -> None:
        """Update the live transcript tab content."""
        if self._transcript_view:
            self._transcript_view.setString_(text or "(no transcript yet)")

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # NSTitledWindowMask | NSClosableWindowMask | NSMiniaturizableWindowMask | NSResizableWindowMask
        mask = 1 | 2 | 4 | 8
        self._window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(200, 150, _W, _H), mask, NSBackingStoreBuffered, False
        )
        self._window.setTitle_("MeetingScribe")
        self._window.setReleasedWhenClosed_(False)
        self._window.setDelegate_(self._delegate)
        self._window.setMinSize_(NSSize(500, 400))
        self._window.center()

        toolbar = NSToolbar.alloc().initWithIdentifier_("MSPrefs")
        toolbar.setDelegate_(self._delegate)
        toolbar.setAllowsUserCustomization_(False)
        toolbar.setDisplayMode_(1)  # NSToolbarDisplayModeIconAndLabel
        self._window.setToolbar_(toolbar)

        self._tab_views["general"] = self._build_general()
        self._tab_views["audio"] = self._build_audio()
        self._tab_views["transcription"] = self._build_transcription()
        self._tab_views["summarization"] = self._build_summarization()
        self._tab_views["transcript"] = self._build_transcript_tab()

        self._switch_tab("general")

    def _switch_tab(self, tab_id: str) -> None:
        content = self._window.contentView()
        for sub in list(content.subviews()):
            sub.removeFromSuperview()
        view = self._tab_views[tab_id]
        view.setFrame_(content.bounds())
        # for scroll views, also update the frame on resize
        if isinstance(view, NSScrollView):
            view.setAutoresizingMask_(18)  # NSViewWidthSizable | NSViewHeightSizable
        content.addSubview_(view)
        self._current_tab = tab_id
        self._window.toolbar().setSelectedItemIdentifier_(tab_id)

    def _wrap_in_scroll(self, inner: NSView) -> NSScrollView:
        """Wrap a flipped content view in an NSScrollView."""
        sv = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, _W, _H))
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(False)
        sv.setAutohidesScrollers_(True)
        sv.setDocumentView_(inner)
        sv.setDrawsBackground_(False)
        return sv

    # ------------------------------------------------------------------
    # tab: General
    # ------------------------------------------------------------------

    def _build_general(self) -> NSScrollView:
        lb = _LayoutBuilder(_W)
        cfg = self._ctrl._config
        d = self._delegate

        lb.section("Output")
        self._fields["output_dir"] = lb.row_field_with_button(
            "Notes folder:", cfg.output_dir, d, "onOutputDir:", "Browse\u2026", "onBrowse:")

        lb.section("Identity")
        self._fields["user_name"] = lb.row_field("Your name:", cfg.user_name, d, "onUserName:")
        lb.hint("Speaker label in transcripts when mic is active.")

        return self._wrap_in_scroll(lb.view)

    # ------------------------------------------------------------------
    # tab: Audio
    # ------------------------------------------------------------------

    def _build_audio(self) -> NSScrollView:
        lb = _LayoutBuilder(_W)
        cfg = self._ctrl._config
        d = self._delegate

        lb.section("Capture")
        self._fields["audio_backend"] = lb.row_popup(
            "Audio backend:", ["auto", "audiotee", "sounddevice"],
            cfg.audio_backend, d, "onBackend:", width=200)

        popup = self._device_popup(cfg.audio_device_index, False)
        popup.setTarget_(d)
        popup.setAction_("onLoopback:")
        lb.row_popup_with_button("Loopback device:", popup, d, "Test", "onTestLoopback:")
        self._fields["loopback_device"] = popup

        popup = self._device_popup(cfg.mic_device_index, True)
        popup.setTarget_(d)
        popup.setAction_("onMic:")
        lb.row_popup_with_button("Microphone:", popup, d, "Test", "onTestMic:")
        self._fields["mic_device"] = popup

        lb.section("Processing")
        self._fields["chunk_seconds"] = lb.row_popup(
            "Chunk duration:", ["30s", "60s", "90s"],
            f"{cfg.chunk_seconds}s", d, "onChunk:", width=120)
        lb.hint("Longer chunks improve diarization but increase latency.")

        return self._wrap_in_scroll(lb.view)

    # ------------------------------------------------------------------
    # tab: Transcription
    # ------------------------------------------------------------------

    def _build_transcription(self) -> NSScrollView:
        lb = _LayoutBuilder(_W)
        cfg = self._ctrl._config
        d = self._delegate
        diar = cfg.use_diarization

        lb.section("Speech-to-Text")
        self._fields["whisper_model"] = lb.row_popup(
            "Whisper model:", ["tiny", "base", "small", "medium", "large-v3"],
            cfg.whisper_model, d, "onModel:", width=180)
        lb.hint("Larger = more accurate but slower. 'base' is a good default.")

        lb.section("Speaker Diarization")
        self._fields["diarization"] = lb.row_checkbox(
            "Enabled:", "Identify different speakers", diar, d, "onDiarization:")

        self._fields["meeting_size"] = lb.row_popup(
            "Meeting size:",
            ["1-on-1 (2 people)", "Small team (3\u20134)", "Medium (5\u20137)", "Large (8+)"],
            self._meeting_size_label(cfg), d, "onMeetingSize:", width=220, enabled=diar)

        self._fields["hf_token"] = lb.row_field(
            "HuggingFace token:", cfg.hf_token, d, "onHFToken:",
            placeholder="hf_...", secure=True, enabled=diar)
        lb.hint("Required for diarization. Get one at huggingface.co/settings/tokens")

        return self._wrap_in_scroll(lb.view)

    # ------------------------------------------------------------------
    # tab: Summarization
    # ------------------------------------------------------------------

    def _build_summarization(self) -> NSScrollView:
        lb = _LayoutBuilder(_W)
        cfg = self._ctrl._config
        d = self._delegate

        lb.section("Provider")
        active = cfg.active_providers
        self._fields["primary_provider"] = lb.row_popup(
            "Primary:", KNOWN_PROVIDERS, active[0] if active else "anthropic",
            d, "onPrimaryProvider:", width=200)
        lb.hint("First configured provider in this list is used for summaries.")

        # per-provider credentials
        providers = [
            ("Anthropic (Claude)", "anthropic_api_key", None, None, ""),
            ("OpenAI", "openai_api_key", "openai_model", None, "sk-..."),
            ("Google Gemini", "gemini_api_key", "gemini_model", None, ""),
            ("OpenRouter", "openrouter_api_key", "openrouter_model", None, "sk-or-..."),
            ("Ollama (local)", None, "ollama_model", "ollama_host", ""),
        ]

        for label, key_attr, model_attr, host_attr, key_placeholder in providers:
            lb.section(label)

            if key_attr:
                self._fields[key_attr] = lb.row_field(
                    "API key:", getattr(cfg, key_attr), d, "onProviderField:",
                    identifier=key_attr, placeholder=key_placeholder, secure=True)

            if host_attr:
                self._fields[host_attr] = lb.row_field(
                    "Host:", getattr(cfg, host_attr), d, "onProviderField:",
                    identifier=host_attr, placeholder="http://localhost:11434")

            if model_attr:
                self._fields[model_attr] = lb.row_field(
                    "Model:", getattr(cfg, model_attr), d, "onProviderField:",
                    identifier=model_attr)

        return self._wrap_in_scroll(lb.view)

    # ------------------------------------------------------------------
    # tab: Live Transcript
    # ------------------------------------------------------------------

    def _build_transcript_tab(self) -> NSView:
        container = _FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, _W, _H))

        # header with refresh button
        lbl = _make_label("Live Transcript", _PAD, _PAD, 200, bold=True)
        container.addSubview_(lbl)

        btn = _make_button("Refresh", _W - _PAD - 80, _PAD - 2, 80, self._delegate, "onRefreshTranscript:")
        container.addSubview_(btn)

        # text view in scroll view
        top = _PAD + 30
        sv = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(_PAD, top, _W - _PAD * 2, _H - top - _PAD)
        )
        sv.setHasVerticalScroller_(True)
        sv.setAutohidesScrollers_(True)
        sv.setAutoresizingMask_(18)  # NSViewWidthSizable | NSViewHeightSizable

        tv = NSTextView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _W - _PAD * 2 - 16, _H - top - _PAD)
        )
        tv.setEditable_(False)
        tv.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12, 0))
        tv.setTextColor_(NSColor.labelColor())
        tv.setString_("(start a recording to see the live transcript)")
        tv.setAutoresizingMask_(2)  # NSViewWidthSizable

        sv.setDocumentView_(tv)
        container.addSubview_(sv)

        self._transcript_view = tv
        return container

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _device_popup(self, selected_index, include_disabled=False):
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 300, _ROW), False
        )
        if include_disabled:
            popup.addItemWithTitle_("Disabled")
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev["max_input_channels"] > 0:
                    name = f"{i}: {dev['name']}"
                    popup.addItemWithTitle_(name)
                    if selected_index is not None and i == selected_index:
                        popup.selectItemWithTitle_(name)
        except Exception:
            popup.addItemWithTitle_("(could not list devices)")

        if selected_index is None and include_disabled:
            popup.selectItemWithTitle_("Disabled")
        elif selected_index is None and not include_disabled:
            popup.addItemWithTitle_("Auto-detect")
            popup.selectItemWithTitle_("Auto-detect")
        return popup

    @staticmethod
    def _meeting_size_label(cfg) -> str:
        dt = cfg.diarization_threshold
        if dt <= 0.50: return "1-on-1 (2 people)"
        if dt <= 0.60: return "Small team (3\u20134)"
        if dt <= 0.68: return "Medium (5\u20137)"
        return "Large (8+)"

    def _save(self) -> None:
        save_config(self._ctrl._config)
        if self._ctrl._status_bar:
            self._ctrl._status_bar.update()

    def _update_diarization_state(self) -> None:
        """Enable/disable diarization-dependent fields."""
        enabled = self._ctrl._config.use_diarization
        for key in ("meeting_size", "hf_token"):
            f = self._fields.get(key)
            if f:
                f.setEnabled_(enabled)


# ---------------------------------------------------------------------------
# widget factories
# ---------------------------------------------------------------------------

def _make_label(text: str, x: int, y: int, w: int, bold: bool = False,
                size: float = 13, align_right: bool = False, color=None) -> NSTextField:
    lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 20))
    lbl.setStringValue_(text)
    lbl.setEditable_(False)
    lbl.setBezeled_(False)
    lbl.setDrawsBackground_(False)
    lbl.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
    if align_right:
        lbl.setAlignment_(2)  # NSTextAlignmentRight
    if color:
        lbl.setTextColor_(color)
    return lbl


def _make_button(title: str, x: int, y: int, w: int, target, action: str) -> NSButton:
    b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, _ROW))
    b.setTitle_(title)
    b.setBezelStyle_(NSBezelStyleRounded)
    b.setFont_(NSFont.systemFontOfSize_(12))
    b.setTarget_(target)
    b.setAction_(action)
    return b


def _parse_device_index(title: str):
    if not title or title in ("Disabled", "Auto-detect", "(could not list devices)"):
        return None
    try:
        return int(title.split(":")[0])
    except (ValueError, IndexError):
        return None


def _show_alert(title: str, message: str) -> None:
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.runModal()


def _run_audio_test(device_index) -> None:
    def _test():
        try:
            import sounddevice as sd
            import numpy as np
            rec = sd.rec(int(3 * 16000), samplerate=16000, channels=1, dtype="float32", device=device_index)
            sd.wait()
            peak = float(np.max(np.abs(rec)))
            rms = float(np.sqrt(np.mean(rec ** 2)))
            if peak < 0.001:
                result = f"No signal detected.\nPeak: {peak:.6f}  RMS: {rms:.6f}"
            elif peak < 0.01:
                result = f"Weak signal.\nPeak: {peak:.4f}  RMS: {rms:.4f}"
            else:
                result = f"Signal detected!\nPeak: {peak:.4f}  RMS: {rms:.4f}"
            from .app_utils import dispatch_to_main
            dispatch_to_main(lambda: _show_alert("Audio Test", result))
        except Exception as e:
            from .app_utils import dispatch_to_main
            dispatch_to_main(lambda: _show_alert("Audio Test Failed", str(e)))

    threading.Thread(target=_test, daemon=True).start()
    _show_alert("Recording\u2026", "Capturing 3 seconds of audio.")


# ---------------------------------------------------------------------------
# ObjC delegate — toolbar + window + all control actions
# ---------------------------------------------------------------------------

class _Delegate(NSObject):

    def initWithOwner_(self, owner):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self._sw = owner
        return self

    # -- window --

    def windowWillClose_(self, notification):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    # -- toolbar delegate --

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return _TABS

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return _TABS

    def toolbarSelectableItemIdentifiers_(self, toolbar):
        return _TABS

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, item_id, flag):
        item = NSToolbarItem.alloc().initWithItemIdentifier_(item_id)
        item.setLabel_(_TAB_LABELS.get(item_id, item_id))
        sf_name = _TAB_ICONS.get(item_id)
        if sf_name:
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sf_name, None)
            if img:
                item.setImage_(img)
        item.setTarget_(self)
        item.setAction_("onToolbarTab:")
        return item

    @objc.typedSelector(b"v@:@")
    def onToolbarTab_(self, sender):
        tab_id = sender.itemIdentifier()
        if tab_id in _TABS:
            self._sw._switch_tab(tab_id)

    # -- General --

    @objc.typedSelector(b"v@:@")
    def onOutputDir_(self, sender):
        self._sw._ctrl._config.output_dir = sender.stringValue()
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onBrowse_(self, sender):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() == 1:
            path = panel.URLs()[0].path()
            self._sw._ctrl._config.output_dir = path
            f = self._sw._fields.get("output_dir")
            if f:
                f.setStringValue_(path)
            self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onUserName_(self, sender):
        self._sw._ctrl._config.user_name = sender.stringValue()
        self._sw._save()

    # -- Audio --

    @objc.typedSelector(b"v@:@")
    def onBackend_(self, sender):
        self._sw._ctrl._config.audio_backend = sender.titleOfSelectedItem()
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onLoopback_(self, sender):
        self._sw._ctrl._config.audio_device_index = _parse_device_index(sender.titleOfSelectedItem())
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onMic_(self, sender):
        self._sw._ctrl._config.mic_device_index = _parse_device_index(sender.titleOfSelectedItem())
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onChunk_(self, sender):
        try:
            self._sw._ctrl._config.chunk_seconds = int(sender.titleOfSelectedItem().rstrip("s"))
        except ValueError:
            pass
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onTestLoopback_(self, sender):
        idx = _parse_device_index(self._sw._fields["loopback_device"].titleOfSelectedItem())
        _run_audio_test(idx)

    @objc.typedSelector(b"v@:@")
    def onTestMic_(self, sender):
        idx = _parse_device_index(self._sw._fields["mic_device"].titleOfSelectedItem())
        if idx is None:
            _show_alert("No microphone", "Select a microphone device first.")
            return
        _run_audio_test(idx)

    # -- Transcription --

    @objc.typedSelector(b"v@:@")
    def onModel_(self, sender):
        self._sw._ctrl._config.whisper_model = sender.titleOfSelectedItem()
        self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onDiarization_(self, sender):
        self._sw._ctrl._config.use_diarization = sender.state() == NSOnState
        self._sw._save()
        self._sw._update_diarization_state()

    @objc.typedSelector(b"v@:@")
    def onMeetingSize_(self, sender):
        presets = [(0.45, 0.60, 60), (0.55, 0.65, 30), (0.65, 0.70, 30), (0.72, 0.75, 30)]
        idx = sender.indexOfSelectedItem()
        if 0 <= idx < len(presets):
            dt, st, cs = presets[idx]
            cfg = self._sw._ctrl._config
            cfg.diarization_threshold = dt
            cfg.speaker_tracker_threshold = st
            cfg.chunk_seconds = cs
            self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onHFToken_(self, sender):
        self._sw._ctrl._config.hf_token = sender.stringValue()
        self._sw._save()

    # -- Summarization --

    @objc.typedSelector(b"v@:@")
    def onProviderField_(self, sender):
        attr = sender.identifier()
        if attr and hasattr(self._sw._ctrl._config, attr):
            setattr(self._sw._ctrl._config, attr, sender.stringValue())
            self._sw._save()

    @objc.typedSelector(b"v@:@")
    def onPrimaryProvider_(self, sender):
        name = sender.titleOfSelectedItem()
        if name:
            order = list(self._sw._ctrl._config.provider_order)
            if name in order:
                order.remove(name)
            self._sw._ctrl._config.provider_order = [name] + order
            self._sw._save()

    # -- Transcript --

    @objc.typedSelector(b"v@:@")
    def onRefreshTranscript_(self, sender):
        ctrl = self._sw._ctrl
        text = ""
        if ctrl._session:
            text = ctrl._session.get_live_transcript()
        self._sw.update_transcript(text)
