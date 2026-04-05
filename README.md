<p align="center">
  <img src="assets/header.png" width="600" alt="MeetingScribe">
</p>

# MeetingScribe

Native macOS menu bar app that records meetings, transcribes locally with whisper.cpp, and post-processes with Claude Code.

## What it does

1. **Records** system audio (and optionally mic) via [audiotee](https://github.com/makeusabrew/audiotee)
2. **Transcribes** on-device using [SwiftWhisper](https://github.com/exPHAT/SwiftWhisper) (whisper.cpp)
3. **Saves** raw transcript to a project folder in `~/OpenClaude/Vault/Meeting Notes/`
4. **Invokes Claude Code** (`claude -p`) to summarize, extract action items, create Todoist tasks, check Google Calendar, and generate an action plan

## Requirements

- macOS 14.2+ (Sonoma)
- [audiotee](https://github.com/makeusabrew/audiotee) — system audio capture
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI — post-processing (optional)

## Install

```bash
# build audiotee
git clone https://github.com/makeusabrew/audiotee
cd audiotee && swift build -c release
cp .build/release/audiotee ~/.local/bin/

# grant audiotee permission:
# System Settings → Privacy & Security → Screen & System Audio Recording
```

## Build

```bash
xcodegen generate
xcodebuild -project MeetingScribe.xcodeproj -scheme MeetingScribe -configuration Debug \
  -derivedDataPath build CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=NO build
```

## Usage

The app lives in the menu bar. Select a project folder, click Record, and stop when done. If Claude Code is installed and auto-processing is enabled, it will summarize the transcript, create Todoist tasks, and generate an action plan automatically.

### Dual-stream recording

Enable mic in Settings to capture your voice separately. System audio is labeled "Remote" and mic audio is labeled with your name. Echo deduplication removes mic segments that are just speaker playback.

## Output

```
~/OpenClaude/Vault/Meeting Notes/
├── Lab Meetings/
│   ├── 2026-04-04_meeting.md        # transcript + summary
│   └── 2026-04-04_meeting.plan.md   # action plan
└── Advisor 1-on-1/
    └── ...
```
