<p align="center">
  <img src="assets/header.png" width="600" alt="MeetingScribe">
</p>

# MeetingScribe

Native macOS menu bar app that records meetings, transcribes locally with whisper.cpp, and post-processes with a multi-agent Claude Code pipeline.

## What it does

1. **Records** system audio (and optionally mic) via [audiotee](https://github.com/makeusabrew/audiotee)
2. **Transcribes** on-device using [SwiftWhisper](https://github.com/exPHAT/SwiftWhisper) (whisper.cpp)
3. **Saves** raw transcript to a project folder in `~/OpenClaude/Vault/Meeting Notes/`
4. **Post-processes** with a phased multi-agent Claude Code pipeline:
   - **Phase 1 (Planner)** — summarizes the meeting, extracts action items, corrects participant names, updates the project README, generates Obsidian-compatible frontmatter with wiki links
   - **Phase 2 (concurrent)** — Todoist agent creates tasks; Calendar agent creates Fantastical events and prep folders for the next meeting

## Per-project metadata

Each project stores a `project.json` with:
- **Participants** — used to correct transcription errors (e.g. "Faizal" → "Feyza")
- **Repos** — paths to related source code repositories
- **Resources** — paths to research materials (Google Drive folders, papers, etc.)

All three are editable directly from the menu bar.

## Requirements

- macOS 14.2+ (Sonoma)
- [audiotee](https://github.com/makeusabrew/audiotee) — system audio capture
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI — post-processing (optional)
- [Fantastical](https://flexibits.com/fantastical) — calendar event creation (optional)

## Install

### Homebrew

```bash
brew tap zacharytgray/meetingscribe
brew install --cask meetingscribe
```

### Build from source

```bash
# build audiotee
git clone https://github.com/makeusabrew/audiotee
cd audiotee && swift build -c release
cp .build/release/audiotee ~/.local/bin/

# grant audiotee permission:
# System Settings → Privacy & Security → Screen & System Audio Recording

# build MeetingScribe
xcodegen generate
xcodebuild -project MeetingScribe.xcodeproj -scheme MeetingScribe -configuration Debug \
  -derivedDataPath build CODE_SIGN_IDENTITY=- CODE_SIGNING_ALLOWED=NO build
```

## Usage

The app lives in the menu bar. Select a project folder, click Record, and stop when done. If Claude Code is installed and auto-processing is enabled, the multi-agent pipeline runs automatically after transcription.

### Dual-stream recording

Enable mic in Settings to capture your voice separately. System audio is labeled "Remote" and mic audio is labeled with your name. Echo deduplication removes mic segments that are just speaker playback.

### Meeting prep

The calendar agent auto-creates a `YYYY-MM-DD_prep/` directory when it detects a next meeting. Drop prep docs in there before the meeting — the planner agent will pick them up.

## Output

```
~/OpenClaude/Vault/Meeting Notes/
├── Procrastination Project/
│   ├── project.json                   # participants, repos, resources
│   ├── README.md                      # living project doc, auto-updated
│   ├── 2026-04-08_meeting.md          # transcript + summary (Obsidian frontmatter)
│   └── 2026-04-15_prep/              # auto-created prep folder
│       └── 2026-04-15_prep.md
└── K-12 AI Project/
    └── ...
```

## Updates

MeetingScribe uses [Sparkle](https://sparkle-project.org/) for auto-updates. The appcast is hosted at [zacharytgray.github.io/MeetingScribe/appcast.xml](https://zacharytgray.github.io/MeetingScribe/appcast.xml).
