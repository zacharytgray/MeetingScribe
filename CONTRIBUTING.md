# Contributing to MeetingScribe

Thanks for your interest in contributing! MeetingScribe is an early-stage project and all kinds of contributions are welcome — bug reports, feature ideas, documentation improvements, and code.

---

## Opening Issues

### Bug Reports

Please include:
- Your OS and Python version (`python --version`)
- MeetingScribe version or commit hash
- Steps to reproduce
- What you expected vs. what happened
- Any relevant output from the terminal (with API keys redacted)

Use the **Bug report** issue template when filing.

### Feature Requests

Describe the use case you're trying to solve, not just the implementation you have in mind. This makes it easier to find the right solution and discuss trade-offs. Use the **Feature request** template.

---

## Pull Requests

1. **Open an issue first** for anything beyond small bug fixes or typos. This avoids duplicate work and lets us align before you invest time coding.

2. **Fork the repo** and create a branch from `main`:
   ```bash
   git checkout -b fix/short-description
   # or
   git checkout -b feat/short-description
   ```

3. **Keep PRs focused.** One logical change per PR. If you find unrelated issues while working, open a separate issue or PR.

4. **Test your change** manually — there are no automated tests yet. Describe in the PR body what you tested and how.

5. **Follow the coding style** described below.

6. **Update the README** if your change affects user-visible behavior.

---

## Coding Conventions

These match what's already in the codebase:

- Python 3.10+ syntax and type hints (`Optional`, `list[...]`, `tuple[...]`)
- All shared state protected by `threading.Lock()`; background threads are daemon threads
- Non-fatal errors (diarization failure, silent chunk) are printed and skipped; fatal errors raise or call `sys.exit(1)`
- Temp files created with `tempfile.mkdtemp(prefix="meetingscribe_")` and cleaned up by `recorder.cleanup()`
- No secrets or config files committed — `~/.meetingscribe/config.json` lives outside the repo
- `KMP_DUPLICATE_LIB_OK=TRUE` set at the very top of `cli.py` and `tray.py` (before any imports) to suppress the PyTorch + ctranslate2 OpenMP conflict on macOS

**File layout:**

| File | Responsibility |
|---|---|
| `cli.py` | CLI entry point; `argparse` commands |
| `tray.py` | System tray app (`pystray`) |
| `meetingscribe/config.py` | `Config` dataclass + load/save |
| `meetingscribe/recorder.py` | `AudioRecorder`: sounddevice → WAV chunks → queue |
| `meetingscribe/transcriber.py` | `Transcriber`: faster-whisper + pyannote + cross-chunk tracking |
| `meetingscribe/session.py` | `MeetingSession`: orchestrates dual streams, echo dedup, summarizer |
| `meetingscribe/summarizer.py` | Claude / OpenRouter API calls + file save |

---

## Areas That Would Benefit Most From Help

- **Tests** — there are none yet. Unit tests for `_remove_echo_segments()`, `CrossChunkSpeakerTracker.resolve()`, `find_loopback_device()`, and slug sanitization would be a great start.
- **Language support** — `language="en"` is currently hardcoded in `transcriber.py`. Adding a config option and auto-detection via `faster-whisper`'s `info.language` would make MeetingScribe useful internationally.
- **Linux testing** — macOS is the primary development platform; Linux support is best-effort. Bug reports and fixes for PipeWire/PulseAudio edge cases are very welcome.
- **Windows support** — currently untested/unsupported. A Windows audio routing approach (WASAPI loopback) would unlock a large user base.
- **Cross-session speaker memory** — speaker identity resets between sessions. A persistent embedding store would let "Speaker 1" mean the same person across recordings.
- **Packaged installer** — a `brew install` formula or a standalone `.app`/`.AppImage` would dramatically lower the barrier to entry.

---

## Development Setup

```bash
git clone https://github.com/zacharytgray/MeetingScribe
cd MeetingScribe
python3.12 -m venv .venv && source .venv/bin/activate
pip install torch        # macOS: no --index-url flag
pip install -e .
python cli.py setup

# Claude Code users: symlink AGENTS.md so Claude picks it up automatically
ln -s AGENTS.md CLAUDE.md
```

---

## License

By submitting a pull request you agree that your contribution will be licensed under the [MIT License](LICENSE).
