# MeetingScribe — Claude Code Project Context

## What This Project Is

MeetingScribe is a Python CLI + system tray app that:
1. Captures system audio from any meeting (Teams, Zoom, etc.) via a virtual loopback device
2. Optionally captures the user's microphone as a separate parallel stream, attributed by name
3. Transcribes locally using `faster-whisper` (free, runs on CPU; no audio leaves the machine)
4. Identifies speakers using `pyannote.audio` diarization (free, requires HuggingFace token)
5. Removes acoustic echoes (mic picking up speaker output) via word-overlap deduplication
6. Summarizes the meeting using the Claude API or any OpenRouter model (including free ones)
7. Saves a structured markdown file with AI-generated filename and appended raw transcript

Target platforms: **macOS and Linux**. Python 3.10–3.12.

---

## Project Structure

```
meetingscribe/
├── CLAUDE.md                        ← this file
├── README.md                        ← end-user docs
├── cli.py                           ← CLI entry point (run directly or via `meetingscribe` command)
├── tray.py                          ← System tray / menu bar app (pystray)
├── pyproject.toml                   ← package definition; console script entry points
├── meetingscribe/
│   ├── __init__.py                  ← public API exports
│   ├── cli_entry.py                 ← shim so installed `meetingscribe` / `meetingscribe-tray` commands work
│   ├── config.py                    ← Config dataclass + load/save from ~/.meetingscribe/config.json
│   ├── recorder.py                  ← AudioRecorder: sounddevice capture → 30s WAV chunks → queue
│   ├── transcriber.py               ← Transcriber: faster-whisper + pyannote diarization + CrossChunkSpeakerTracker
│   ├── summarizer.py                ← summarize() via Claude or OpenRouter; save_summary()
│   └── session.py                   ← MeetingSession: orchestrates dual streams + echo dedup + summarizer
└── scripts/
    ├── install_mac.sh               ← macOS installer
    └── install_linux.sh             ← Linux installer
```

---

## Architecture & Data Flow

```
System audio (loopback)
    ↓  BlackHole (macOS) / PulseAudio monitor (Linux)
AudioRecorder [recorder.py]
    - sounddevice InputStream at 16kHz mono
    - Buffers audio; every 30s saves WAV chunk to temp dir
    - Puts WAV paths into a queue.Queue
    ↓
Transcriber [transcriber.py] — diarization=True
    - Runs faster-whisper (vad_filter=True, beam_size=5, language="en")
    - Runs pyannote per chunk; maps Whisper segments → speaker by overlap
    - CrossChunkSpeakerTracker resolves consistent labels via cosine similarity
    - Segments labeled "Speaker 1", "Speaker 2", …

Microphone (optional, when mic_device_index is configured)
    ↓
AudioRecorder [recorder.py]
    ↓
Transcriber [transcriber.py] — diarization=False, default_speaker=user_name
    - Whisper only; all segments labeled with user's name (e.g. "Zach")

MeetingSession [session.py]
    - load_models(): blocking load of Whisper + pyannote before session
    - start(): creates both AudioRecorders, wires queues, starts both Transcribers
    - _merge_transcripts():
        1. Gets segments from both Transcribers
        2. Labels unlabeled loopback segments "Remote" (when mic is active)
        3. Runs _remove_echo_segments() — drops mic segments that echo loopback audio
        4. Sorts combined list by timestamp
    - stop(): stops both streams, drains queues, calls _merge_transcripts(), summarizes, saves
    ↓
summarize() [summarizer.py]
    - Calls Anthropic Claude API or OpenRouter (OpenAI-compatible endpoint via httpx)
    - System prompt enforces exact markdown structure + SLUG: prefix
    - Returns (slug, markdown) tuple
    ↓
save_summary() [summarizer.py]
    - Writes to output_dir/YYYY-MM-DD_<slug>.md
    - Appends raw transcript at bottom with double-newline spacing
    - Handles filename collisions with _2, _3 suffix
```

---

## Key Design Decisions

### Dual-stream audio
Two `AudioRecorder` + `Transcriber` pairs run in parallel within one `MeetingSession`. Both start at the same wall-clock time, so their `_elapsed_offset` values track the same timeline. Merging by `seg.start` produces a correctly interleaved transcript. No explicit synchronization is needed beyond sorting.

### Microphone attribution
The mic stream always gets `default_speaker=config.user_name`. Diarization is intentionally **never run on mic audio** — the mic captures only the user, so diarization is unnecessary and would produce misleading labels.

### Cross-chunk speaker identity
`CrossChunkSpeakerTracker` in `transcriber.py` maintains consistent global speaker IDs across 30-second chunks. For each chunk, pyannote embeddings are extracted per speaker, normalized, and compared via cosine similarity (threshold 0.75) against the running registry. If similarity ≥ 0.75, the local speaker maps to the existing global label (and the embedding is averaged). Otherwise a new "Speaker N" label is created. Falls back to sequential labels if embedding extraction is unavailable.

### Acoustic echo deduplication
When speakers (not headphones) are used, the mic picks up audio playing through them, creating near-duplicate transcript entries. `_remove_echo_segments()` in `session.py` compares every user-labeled mic segment against every loopback segment. If temporal proximity (overlap or within 2.5s) **and** word overlap ≥ 0.70 — the mic segment is an acoustic echo and is dropped. Headphones eliminate the problem entirely; this is a software fallback.

### Summarization — dual provider
`summarizer.py` supports:
- **Anthropic Claude** (`_call_anthropic`) — uses `anthropic` SDK, `claude-sonnet-4-20250514`
- **OpenRouter** (`_call_openrouter`) — uses `httpx` against OpenAI-compatible endpoint; default model `meta-llama/llama-3.3-70b-instruct:free`

If `openrouter_api_key` is set, OpenRouter is used. Otherwise falls back to `anthropic_api_key`. If neither is set, raw transcript is saved.

When `user_name` is provided to `summarize()`, the system prompt instructs the LLM to: write from the user's perspective, assign action items to them by name, and **not** include them in the Participants list.

### Chunked processing
Audio is processed in 30-second chunks. `vad_filter=True` in faster-whisper skips silence. Silent chunks (mean amplitude < 0.001) are discarded by the recorder before writing to disk. `_elapsed_offset` in Transcriber tracks cumulative time so timestamps are absolute from session start.

### Queue wiring
`AudioRecorder` owns the `queue.Queue`. In `session.py`, `start()` creates the recorder and then wires `self._transcriber.chunk_queue = self._recorder.chunk_queue`. This late-wiring is a design smell — the transcriber is created with `chunk_queue=None` in `__init__`. A future refactor should have `MeetingSession` own the queue and inject it at Transcriber construction time.

---

## Configuration

Config stored at `~/.meetingscribe/config.json` (chmod 600 to protect API keys). Never committed to git.

```python
@dataclass
class Config:
    output_dir: str = "~/MeetingNotes"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    hf_token: str = ""
    whisper_model: str = "base"       # tiny | base | small | medium | large-v3
    use_diarization: bool = True
    audio_device_index: Optional[int] = None   # None = auto-detect loopback
    mic_device_index: Optional[int] = None     # None = disabled
    user_name: str = "Me"                      # speaker label for mic audio
    chunk_seconds: int = 30
```

Environment variable fallbacks: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `HF_TOKEN`.

`resolved_output_dir` strips surrounding quotes from the path string (handles setup wizard input quoting edge case) then calls `.expanduser()`.

---

## Output Format

Files saved as `output_dir/YYYY-MM-DD_<slug>.md`. Collision handling: appends `_2`, `_3`, etc.

```markdown
# Q3 Budget Review and Headcount Planning
**Date:** June 12, 2025 at 2:00 PM
**Duration:** 47m

## ✅ Action Items
- [ ] Finalize headcount proposal — @Sarah
- [ ] Send revised budget to finance by Friday — @Zach

## 📋 Summary
Prose summary of discussion...

## 🗣️ Key Discussion Points
- Bullet points...

## 👥 Participants
- Speaker 1 (Sarah)
- Speaker 2

---
*Transcribed and summarized by MeetingScribe*

---

## 📝 Raw Transcript

[00:00–00:08] [Remote] Let's get started...
[00:05–00:14] [Zach] Sure, so the total budget...
```

The raw transcript section uses double-newline spacing between lines so each line renders as a separate paragraph in markdown. The `_fix_list_formatting()` post-processor in `summarizer.py` ensures LLM-generated list items always start on their own line.

---

## Dependencies

### Python packages (pyproject.toml)
```
faster-whisper>=1.0.0      # Whisper transcription (CTranslate2 backend)
sounddevice>=0.4.6          # Audio capture via sounddevice InputStream
soundfile>=0.12.1           # WAV I/O
numpy>=1.24,<2              # Pinned <2 for ctranslate2 compatibility
anthropic>=0.25.0           # Claude API client
pyannote.audio>=3.1.0       # Speaker diarization
torch>=2.0.0                # Required by pyannote
pystray>=0.19.4             # System tray app
Pillow>=10.0.0              # Icon rendering for tray
```

`httpx` is used by `_call_openrouter()` in `summarizer.py` but is not explicitly listed in `pyproject.toml` — it is available as a transitive dependency of `anthropic`. If `anthropic` is ever removed, `httpx` must be added explicitly.

### System dependencies
- **macOS**: BlackHole virtual audio driver (`brew install blackhole-2ch`) + Background Music (`brew install --cask background-music`) + Multi-Output Device in Audio MIDI Setup
- **Linux**: PulseAudio or PipeWire monitor sources; `portaudio19-dev`, `libsndfile1`, `ffmpeg` via apt/dnf/pacman

### macOS note on PyTorch
On macOS, install PyTorch with plain `pip install torch` (no `--index-url`). The `--index-url https://download.pytorch.org/whl/cpu` flag is Linux-only; it breaks macOS installs.

### OpenMP conflict fix
PyTorch and ctranslate2 both bundle `libiomp5.dylib` on macOS. Both `cli.py` and `tray.py` set `os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")` at the very top of the file, before any imports, to suppress the crash.

---

## CLI Commands

```bash
python cli.py setup              # Interactive first-time wizard
python cli.py start              # Start recording session
python cli.py start -m small     # Use a specific Whisper model
python cli.py start -d 6         # Use audio device index 6
python cli.py start --no-diarization
python cli.py devices            # List available audio input devices
python cli.py config             # Show current configuration
python cli.py test-audio -d 6 -t 5 -s   # Record 5s from device 6, report amplitude, save WAV
```

**During a session (type + Enter):**
- `s` or `stop` — stop recording and summarize
- `t` or `transcript` — print live transcript so far
- `q` or `quit` — exit without saving
- `h` — show help
- Ctrl+C — fallback stop (same as `s`)

---

## Tray App (`tray.py`)

Run with `python tray.py` or `meetingscribe-tray`. Uses `pystray` with a custom mic icon drawn via Pillow. Grey = idle, Red = recording.

Menu: Start Recording / Stop & Summarize / Show Live Transcript / Open Last Note / Open Notes Folder / Settings / Quit

Model loading happens in a background thread on Start. Live transcript is written to a temp `.txt` file and opened with `open` (macOS) or `xdg-open` (Linux).

---

## Known Issues & Future Work

1. **Queue wiring design smell** — `Transcriber` is constructed with `chunk_queue=None` then assigned in `start()`. Should be refactored so the queue is owned and injected by `MeetingSession` at construction.

2. **Language hardcoded** — `language="en"` in `transcriber.py`. Should be a config option; faster-whisper supports `language="auto"` via `info.language`.

3. **No live summary** — Summarization only happens on stop. Could add a "summarize so far" command.

4. **Tray icon on GNOME** — May need `gnome-shell-extension-appindicator` on some Linux desktops.

5. **pyannote `use_auth_token` deprecation** — Newer pyannote versions use `token=`. Both are tried via try/except TypeError; the except branch can be removed once pyannote <3.1 is dropped.

6. **No tests** — No unit or integration tests. Priority: `find_loopback_device()` with mocked sounddevice, `_diarize()` overlap logic, `_remove_echo_segments()` echo detection, slug sanitization.

7. **`httpx` implicit dependency** — See Dependencies section above.

8. **Cross-session speaker memory** — Speaker identity resets between sessions. A persistent embedding store would allow "Speaker 1" to mean the same person across multiple recordings.

---

## Environment Variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key (fallback if not in config) |
| `OPENROUTER_API_KEY` | OpenRouter API key (fallback if not in config) |
| `HF_TOKEN` | HuggingFace token for pyannote diarization (fallback if not in config) |
| `KMP_DUPLICATE_LIB_OK` | Set to `TRUE` at top of cli.py and tray.py to suppress OpenMP duplicate lib crash |

---

## How to Run During Development

```bash
# From project root (no install needed)
python cli.py setup
python cli.py start
python cli.py devices
python cli.py test-audio -d 6 -t 5

# Tray app
python tray.py

# Or install in editable mode
python3.12 -m venv .venv && source .venv/bin/activate
pip install torch        # macOS: no --index-url
pip install -e .
meetingscribe start
```

---

## Coding Conventions

- Python 3.10+ type hints (`Optional`, `list[...]`, `tuple[...]`)
- Threading: all shared state protected by `threading.Lock()`. Background threads are daemon threads.
- ANSI color helpers in `cli.py`: `c(COLOR, text)` + named constants `RED`, `GREEN`, etc.
- Error handling: non-fatal errors (diarization failure, silent chunk) are printed and skipped; fatal errors raise or call `sys.exit(1)`.
- Temp files: `tempfile.mkdtemp(prefix="meetingscribe_")`, cleaned up by `recorder.cleanup()` after session ends.
- API keys: stored in `~/.meetingscribe/config.json` with `chmod 600`. Never in the repo. `.gitignore` excludes `config.json` and `.env*`.
