#!/usr/bin/env python3
"""MeetingScribe system tray / menu bar app. Run: python tray.py"""
from __future__ import annotations

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
except ImportError:
    print("pystray and Pillow are required for the tray app. pip install pystray Pillow")
    sys.exit(1)

from meetingscribe.config import load_config, save_config
from meetingscribe.session import MeetingSession
from meetingscribe.transcriber import TranscriptSegment


# ---------------------------------------------------------------------------
# Icon drawing
# ---------------------------------------------------------------------------

ICON_SIZE = 64


def _draw_mic_icon(recording: bool) -> Image.Image:
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (220, 50, 50) if recording else (140, 140, 140)

    # Mic body (rounded rectangle)
    mx, my = ICON_SIZE // 2, ICON_SIZE // 2
    w, h = 16, 24
    draw.rounded_rectangle(
        [mx - w // 2, my - h // 2 - 4, mx + w // 2, my + h // 2 - 4],
        radius=8,
        fill=color,
    )

    # Stand arc
    arc_box = [mx - 20, my - 4, mx + 20, my + 20]
    draw.arc(arc_box, start=0, end=180, fill=color, width=3)

    # Stem
    draw.line([mx, my + 18, mx, my + 28], fill=color, width=3)
    draw.line([mx - 8, my + 28, mx + 8, my + 28], fill=color, width=3)

    return img


# ---------------------------------------------------------------------------
# Tray App
# ---------------------------------------------------------------------------

class TrayApp:
    def __init__(self) -> None:
        self._config = load_config()
        self._session: Optional[MeetingSession] = None
        self._recording = False
        self._loading = False
        self._last_note_path: Optional[Path] = None
        self._transcript_tmpfile: Optional[str] = None
        self._status_msg = "Idle"
        self._lock = threading.Lock()

        self._icon = pystray.Icon(
            "MeetingScribe",
            icon=_draw_mic_icon(False),
            title="MeetingScribe",
            menu=self._build_menu(),
        )

    def run(self) -> None:
        self._icon.run(setup=self._on_setup)

    # ------------------------------------------------------------------
    # Setup callback (called once icon is ready)
    # ------------------------------------------------------------------

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            item("Start Recording", self._on_start, enabled=lambda _: not self._recording and not self._loading),
            item("Stop & Summarize", self._on_stop, enabled=lambda _: self._recording),
            pystray.Menu.SEPARATOR,
            item("Show Live Transcript", self._on_show_transcript, enabled=lambda _: self._recording),
            item("Open Last Note", self._on_open_last, enabled=lambda _: self._last_note_path is not None),
            item("Open Notes Folder", self._on_open_folder),
            pystray.Menu.SEPARATOR,
            item("Settings", pystray.Menu(
                item(
                    lambda _: f"Model: {self._config.whisper_model}",
                    pystray.Menu(
                        *(item(m, self._make_model_setter(m)) for m in ["tiny", "base", "small", "medium", "large-v3"])
                    ),
                ),
                item(
                    lambda _: f"Diarization: {'on' if self._config.use_diarization else 'off'}",
                    self._toggle_diarization,
                ),
            )),
            pystray.Menu.SEPARATOR,
            item("Quit", self._on_quit),
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_start(self, icon, menu_item) -> None:
        with self._lock:
            if self._recording or self._loading:
                return
            self._loading = True

        self._icon.icon = _draw_mic_icon(False)
        self._icon.title = "MeetingScribe — loading models…"
        threading.Thread(target=self._load_and_start, daemon=True).start()

    def _load_and_start(self) -> None:
        try:
            self._session = MeetingSession(
                self._config,
                on_segment=self._on_segment,
                on_status=self._on_status_update,
            )
            self._session.load_models()
            self._session.start()
            with self._lock:
                self._recording = True
                self._loading = False
            self._icon.icon = _draw_mic_icon(True)
            self._icon.title = "MeetingScribe — recording"
            self._icon.notify("MeetingScribe", "Recording started.")
        except Exception as e:
            with self._lock:
                self._loading = False
            self._icon.notify("MeetingScribe", f"Failed to start: {e}")
            self._icon.icon = _draw_mic_icon(False)
            self._icon.title = "MeetingScribe"

    def _on_stop(self, icon, menu_item) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        self._icon.icon = _draw_mic_icon(False)
        self._icon.title = "MeetingScribe — summarizing…"
        threading.Thread(target=self._stop_and_save, daemon=True).start()

    def _stop_and_save(self) -> None:
        try:
            path = self._session.stop()
            if path:
                self._last_note_path = path
                self._icon.notify("MeetingScribe", f"Saved: {path.name}")
            else:
                self._icon.notify("MeetingScribe", "Session ended — no speech detected.")
        except Exception as e:
            self._icon.notify("MeetingScribe", f"Error saving: {e}")
        finally:
            self._icon.title = "MeetingScribe"

    def _on_show_transcript(self, icon, menu_item) -> None:
        transcript = self._session.get_live_transcript() if self._session else ""
        if not transcript:
            transcript = "(no transcript yet)"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="meetingscribe_transcript_", delete=False, encoding="utf-8"
        ) as f:
            f.write(transcript)
            tmp = f.name

        self._transcript_tmpfile = tmp
        _open_file(tmp)

    def _on_open_last(self, icon, menu_item) -> None:
        if self._last_note_path and self._last_note_path.exists():
            _open_file(str(self._last_note_path))

    def _on_open_folder(self, icon, menu_item) -> None:
        folder = self._config.resolved_output_dir
        folder.mkdir(parents=True, exist_ok=True)
        _open_file(str(folder))

    def _toggle_diarization(self, icon, menu_item) -> None:
        self._config.use_diarization = not self._config.use_diarization
        save_config(self._config)

    def _make_model_setter(self, model: str):
        def _set(icon, menu_item):
            self._config.whisper_model = model
            save_config(self._config)
        return _set

    def _on_quit(self, icon, menu_item) -> None:
        if self._recording and self._session:
            self._session.stop()
        icon.stop()

    # ------------------------------------------------------------------
    # Callbacks from session
    # ------------------------------------------------------------------

    def _on_segment(self, seg: TranscriptSegment) -> None:
        pass  # Could append to transcript file for live view

    def _on_status_update(self, msg: str) -> None:
        self._status_msg = msg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _open_file(path: str) -> None:
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app = TrayApp()
    app.run()


if __name__ == "__main__":
    main()
