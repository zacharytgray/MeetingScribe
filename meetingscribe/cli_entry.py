"""Shim so the installed `meetingscribe` and `meetingscribe-tray` commands work."""


def main() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from cli import main as cli_main  # type: ignore
    cli_main()


def tray_main() -> None:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from tray import main as tray_main_fn  # type: ignore
    tray_main_fn()
