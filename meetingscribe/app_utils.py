"""Utility helpers for the native macOS app — icon generation, thread dispatch."""
from __future__ import annotations

from pathlib import Path

from AppKit import NSImage
from Foundation import NSBundle


# menu bar icon size (points)
_PT = 18


def create_status_icon(recording: bool) -> NSImage:
    """Return an NSImage for the menu bar using SF Symbols."""
    name = "mic.fill" if not recording else "mic.badge.plus"
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        name, "MeetingScribe"
    )
    if img is None:
        # fallback for older macOS without SF Symbols — plain text icon
        img = NSImage.alloc().initWithSize_((_PT, _PT))

    if not recording:
        img.setTemplate_(True)
    return img


def setup_app_icon(app) -> None:
    """Set the dock icon from bundle Resources or the assets/ folder."""
    bundle = NSBundle.mainBundle()
    icon_path = bundle.pathForResource_ofType_("AppIcon", "icns")
    if not icon_path:
        # dev mode: load from project assets/
        project_icon = Path(__file__).resolve().parent.parent / "assets" / "AppIcon.icns"
        if project_icon.exists():
            icon_path = str(project_icon)
    if icon_path:
        img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if img:
            app.setApplicationIconImage_(img)


def dispatch_to_main(fn):
    """Schedule fn() on the main thread. Use for UI updates from background threads."""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn)
