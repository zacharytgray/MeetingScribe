# MeetingScribe

![MeetingScribe](assets/header.png)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Platform: macOS | Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#requirements)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**Open-source, privacy-first meeting transcription and summarization.** MeetingScribe records your system audio during any meeting (Teams, Zoom, Google Meet, etc.), transcribes it locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper), identifies speakers, and produces a structured markdown summary using an AI model of your choice. No audio ever leaves your machine — transcription is fully local. Only the text transcript is sent to your chosen summarization API, and only if you configure one. With [Ollama](https://ollama.ai), even summarization stays fully on-device — free and completely private.

---

## Features

- **100% local transcription** — Whisper runs on your CPU; no audio sent to any server
- **Fully local summarization** — use [Ollama](https://ollama.ai) to keep everything on-device; no API key or internet required
- **Speaker diarization** — identifies who said what (optional, requires free HuggingFace account)
- **Mic attribution** — captures your microphone as a separate stream, labeled with your name; acoustic echo deduplication removes mic segments that are just your speakers bleeding into the mic
- **AI summarization** — structured markdown notes with action items, key points, and participants
- **Five summarization providers** — Ollama (local), Anthropic, OpenAI, Gemini, or OpenRouter (free models available); configurable priority order
- **Driver-free audio on macOS 14.2+** — uses CoreAudio Taps via [audiotee](https://github.com/makeusabrew/audiotee); no BlackHole, no Audio MIDI Setup, volume control works normally
- **Works with any meeting app** — captures system audio; no integrations or plugins needed
- **macOS and Linux** support
- **System tray app** for menu-bar control, or use the CLI directly

---

## How It Works

```
System audio (meeting output)
    ↓  audiotee / CoreAudio Tap (macOS 14.2+ — no driver needed)
    ↓    OR BlackHole (macOS ≤13) / PulseAudio monitor (Linux)
AudioRecorder (loopback)  —  captures 30s WAV chunks
    ↓
Transcriber  —  faster-whisper (local, CPU)
             +  pyannote speaker diarization (optional)
             →  segments labeled "Speaker 1", "Speaker 2", …

Microphone (optional)
AudioRecorder (mic)  —  captures 30s WAV chunks in parallel
    ↓
Transcriber  —  faster-whisper (no diarization)
             →  all segments labeled with your name

    ↓
Echo filter  —  removes mic segments that are acoustic
                echoes of the loopback audio
    ↓
Merge & sort both streams by timestamp
    ↓
Summarizer  —  Ollama (local) / Claude / OpenAI / Gemini / OpenRouter
    ↓
~/MeetingNotes/2025-06-12_q3_planning.md
```

---

## Requirements

- Python 3.10–3.12
- macOS 12+ or Linux (Ubuntu 20.04+, Fedora, Arch)
- **macOS 14.2+ (Sonoma)**: [audiotee](https://github.com/makeusabrew/audiotee) — no virtual driver, no Audio MIDI Setup, volume works normally ✨
- **macOS ≤13**: [BlackHole](https://existential.audio/blackhole/) virtual audio driver (see setup steps below)
- **Linux**: PulseAudio or PipeWire with monitor sources

---

## Installation

### Recommended Flow

Use the platform installer script first. It handles the managed venv, Python dependencies, launcher scripts, and platform-specific audio prerequisites. Then run `meetingscribe setup` to configure the app.

```bash
git clone https://github.com/zacharytgray/MeetingScribe
cd MeetingScribe

# macOS
bash scripts/install_mac.sh

# Linux
# bash scripts/install_linux.sh

meetingscribe setup
```

Both installer scripts create `meetingscribe` and `meetingscribe-tray` launchers in `~/.local/bin`.
Those wrappers resolve the venv from `$HOME/.meetingscribe/venv` at runtime, and if `~/.local/bin` is not already on `PATH`, the installer adds it to your shell startup file automatically.
`meetingscribe ...` is the primary CLI interface and has the same behavior as running `python cli.py ...` from the project root; the wrapper just dispatches to the same code with the managed venv.

`meetingscribe setup` does not install dependencies, create the venv, install launchers, or set up OS-level audio requirements. It only configures MeetingScribe after installation is complete.
Do not run both the installer script and the manual install steps for the same setup unless you intentionally want two separate environments. Manual install is an alternative workflow, not an additional required step.

### Manual macOS Install

Use this only if you want to manage your own project-local venv instead of the installer-managed environment above.
With this approach, `meetingscribe` works while that venv is activated; otherwise run `python cli.py ...` from the repo root.

```bash
# 1. Clone the repo
git clone https://github.com/zacharytgray/MeetingScribe
cd MeetingScribe

# 2. Create a Python 3.12 venv and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install torch          # macOS: PyTorch installs directly from PyPI
pip install -e .

# 3. Configure after installation
meetingscribe setup
```

**macOS 14.2+ (Sonoma) — driver-free setup:**

[audiotee](https://github.com/makeusabrew/audiotee) uses CoreAudio Taps to capture system audio. No BlackHole, no Audio MIDI Setup, volume works normally. Build it from source (requires Xcode Command Line Tools, installed by default on most Macs):

```bash
# Quick install via the installer script (handles everything)
bash scripts/install_mac.sh

# Or build manually
git clone https://github.com/makeusabrew/audiotee
cd audiotee && swift build -c release
cp .build/release/audiotee /usr/local/bin/
```

On first recording, macOS prompts for System Audio Recording permission — grant it once.

**macOS 13 and earlier — BlackHole setup:**

```bash
brew install blackhole-2ch
```
1. Open **Audio MIDI Setup** (Applications → Utilities)
2. Click **+** → **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your speakers
4. Set this Multi-Output Device as your system output in System Settings → Sound

### Manual Linux Install

Use this only if you want to manage your own project-local venv instead of the installer-managed environment above.
With this approach, `meetingscribe` works while that venv is activated; otherwise run `python cli.py ...` from the repo root.

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get install portaudio19-dev libsndfile1 ffmpeg

# Clone, create venv, and install
git clone https://github.com/zacharytgray/MeetingScribe
cd MeetingScribe
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e .

# Configure after installation
meetingscribe setup
```

---

## Configuration

Run the interactive setup wizard:

```bash
meetingscribe setup
```

You'll be prompted for:

| Setting | Description |
|---|---|
| Notes output directory | Where markdown files are saved (default: `~/MeetingNotes`) |
| AI provider(s) | Pick from: Ollama, Anthropic, OpenAI, Gemini, OpenRouter (prompted for keys/models of selected providers only) |
| HuggingFace token | For speaker diarization (free account) |
| Whisper model | Transcription quality/speed tradeoff |
| Audio backend | `auto` (audiotee on macOS 14.2+), `sounddevice` (BlackHole), or `audiotee` |
| Audio device | Loopback device index (sounddevice backend only) |
| Microphone device | Your mic, for attributing your own voice (optional) |
| Your name | How your voice appears in the transcript |
| Meeting size preset | Sets diarization thresholds for your typical group size |
| Chunk duration | Audio window per transcription pass (30 / 60 / 90 seconds) |

Configuration is saved to `~/.meetingscribe/config.json` (chmod 600, never committed to git).

All keys can also be set as environment variables:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."
export HF_TOKEN="hf_..."
```

---

## API Key Options

MeetingScribe only needs a provider for **summarization** — transcription is always fully local. Audio never leaves your machine; only the text transcript is sent to whichever provider you choose (or you can skip summarization entirely).

During `meetingscribe setup` you'll see a numbered menu to pick which provider(s) to configure — only the ones you select will prompt for credentials. If you configure multiple providers you'll set a priority order; the first active provider in that order is used each session. You can also change priority at any time from the tray **Settings → Summary via** submenu.

### Option A: Ollama — fully local, no API key

[Ollama](https://ollama.ai) runs open-source LLMs on your own hardware. Nothing leaves your machine — fully air-gapped summarization.

1. Install Ollama from [ollama.ai](https://ollama.ai)
2. Pull a model: `ollama pull llama3.2`
3. Run `meetingscribe setup` and select **Ollama**; enter the model name

Popular models for summarization: `llama3.2`, `mistral`, `gemma3`, `phi4`.

### Option B: Anthropic Claude (Paid)

1. Sign up at [console.anthropic.com](https://console.anthropic.com)
2. Add credits ($5 minimum) and create an API key
3. Run `meetingscribe setup` and select **Anthropic**

Uses `claude-sonnet-4-20250514`. Produces the highest quality summaries.

### Option C: OpenAI (Paid)

1. Sign up at [platform.openai.com](https://platform.openai.com)
2. Create an API key and add credits
3. Run `meetingscribe setup` and select **OpenAI**

Uses `gpt-4o-mini` by default — fast and cost-effective. Change to `gpt-4o` for higher quality.

### Option D: Google Gemini (Paid, generous free tier)

1. Get a free API key at [aistudio.google.com](https://aistudio.google.com)
2. Run `meetingscribe setup` and select **Google Gemini**

Uses `gemini-2.0-flash`. The free tier is sufficient for typical meeting usage.

### Option E: OpenRouter (Free models available)

[OpenRouter](https://openrouter.ai) provides access to dozens of models through a single API, including free-tier options.

1. Sign up at [openrouter.ai](https://openrouter.ai) and create a free API key
2. Run `meetingscribe setup` and select **OpenRouter**

**Recommended free models:**

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `meta-llama/llama-3.3-70b-instruct:free` | Fast | Excellent | Best free option overall |
| `nvidia/llama-3.1-nemotron-70b-instruct:free` | Fast | Excellent | Strong instruction following |
| `google/gemma-3-27b-it:free` | Fast | Very good | Good for structured output |
| `microsoft/phi-4:free` | Fast | Good | Lightweight, reliable |
| `deepseek/deepseek-r1:free` | Slower | Excellent | Good for complex summaries |
| `mistralai/mistral-7b-instruct:free` | Very fast | Good | Low latency |

Free models on OpenRouter have rate limits but are sufficient for typical meeting usage (one meeting = one API call).

### Option F: No provider

Skip summarization entirely — MeetingScribe saves the raw timestamped transcript as a markdown file.

---

## Usage

### CLI

```bash
# Start a recording session
meetingscribe start

# With specific options
meetingscribe start -m small           # use a larger Whisper model
meetingscribe start -d 6               # specify audio device index
meetingscribe start --no-diarization   # skip speaker identification

# List available audio input devices
meetingscribe devices

# Test audio capture from a device (run while audio is playing)
meetingscribe test-audio -d 6 -t 5 -s

# Show current configuration
meetingscribe config

# Re-run setup wizard
meetingscribe setup
```

**During a recording session (type + Enter):**

| Command | Action |
|---|---|
| `s` or `stop` | Stop recording and summarize |
| `t` or `transcript` | Print live transcript so far |
| `q` or `quit` | Exit without saving |
| `h` | Show help |
| Ctrl+C | Fallback stop (same as `s`) |

### System Tray App

```bash
meetingscribe-tray
```

A microphone icon appears in your menu bar (grey = idle, red = recording). Menu options: Start / Stop & Summarize / Show Live Transcript / Open Last Note / Open Notes Folder / Settings.

---

## Output Format

Files are saved as `~/MeetingNotes/YYYY-MM-DD_<slug>.md`:

```markdown
# Q3 Budget Review and Headcount Planning
**Date:** June 12, 2025 at 2:00 PM
**Duration:** 47m

## ✅ Action Items
- [ ] Follow up with finance on the revised budget
- [ ] Send updated headcount proposal by Friday

## 📋 Summary
Prose summary of the discussion...

## 🗣️ Key Discussion Points
- Budget gap of ~$200K identified in engineering headcount
- Decision to defer two contractor renewals until Q4

## 👥 Participants
- Me
- Speaker 1
- Speaker 2

---
*Transcribed and summarized by MeetingScribe*

---

## 📝 Raw Transcript

[00:00–00:05] [Me] Let's get started — can someone walk me through the numbers?
[00:05–00:18] [Speaker 1] Sure, so the total budget request is...
[00:18–00:30] [Speaker 2] And if we defer the contractor renewals to Q4...
```

Speaker labels in the raw transcript reflect what MeetingScribe can actually identify: your own voice is labeled with your configured name (e.g. `Me`), and remote participants are labeled `Speaker 1`, `Speaker 2`, etc. by pyannote's diarization. Individual speaker names are not resolved automatically — the AI summarizer may infer names from context if participants introduce themselves during the meeting.

---

## Whisper Model Guide

| Model | VRAM / RAM | Speed | Quality | Recommended for |
|---|---|---|---|---|
| `tiny` | ~400 MB | Fastest | Basic | Quick tests |
| `base` | ~750 MB | Fast | Good | Most meetings |
| `small` | ~1.5 GB | Moderate | Better | Important meetings |
| `medium` | ~3 GB | Slow | Great | High accuracy needed |
| `large-v3` | ~6 GB | Slowest | Best | Maximum accuracy |

All models run on CPU. `base` is the recommended default.

---

## Speaker Diarization Setup

Speaker identification requires a free [HuggingFace](https://huggingface.co) account and model access:

1. Sign up at [huggingface.co](https://huggingface.co)
2. Create an access token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) (Read access only)
3. Accept the model terms at [huggingface.co/pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
4. Paste your token into `meetingscribe setup`

Speaker labels persist across 30-second chunks using embedding-based matching, so "Speaker 1" refers to the same person throughout the recording.

---

## Diarization Tuning

Speaker diarization involves two thresholds that trade off between **fewer false splits** (same speaker labeled as two) and **fewer false merges** (two speakers labeled as one). Longer chunks also improve accuracy by giving pyannote more audio context per window.

| Meeting size | Speakers | `diarization_threshold` | `speaker_tracker_threshold` | `chunk_seconds` |
|---|---|---|---|---|
| 1-on-1 | 2 | 0.45 | 0.60 | 60 |
| Small team | 3–4 | **0.55** | **0.65** | **30** ← default |
| Medium meeting | 5–7 | 0.65 | 0.70 | 30 |
| Large meeting | 8+ | 0.72 | 0.75 | 30 |

**`diarization_threshold`** — controls pyannote's within-chunk clustering. Lower values merge acoustic embeddings more aggressively, reducing false splits at the risk of blending distinct voices.

**`speaker_tracker_threshold`** — cosine similarity required for the same speaker to be recognised across 30-second chunk boundaries. Lower values track the same voice more loosely across chunks.

**`chunk_seconds`** — audio window per transcription pass. Longer windows give pyannote more context and generally improve accuracy, but delay when the first transcript line appears (a 60s chunk produces no output for 60 seconds).

Set these via `meetingscribe setup` (shows an interactive preset table) or via the **Settings → Meeting size** and **Settings → Chunk** menus in the tray app.

> **Note:** Changes made in the tray menu take effect for the *next* recording session. The thresholds and chunk size are read when you click **Start Recording** — adjusting them mid-session has no effect on the current session.

---

## Microphone Attribution & Echo Handling

When a microphone device is configured, MeetingScribe runs two audio streams in parallel:

- **Loopback stream** (audiotee on macOS 14.2+ / BlackHole on macOS ≤13 / PulseAudio on Linux) — captures all meeting audio; speakers identified by pyannote diarization as "Speaker 1", "Speaker 2", etc.
- **Microphone stream** — captures your voice directly from your mic; every segment labeled with your name (e.g. `[Zach]`)

Both streams are transcribed independently using faster-whisper, then merged and sorted by timestamp before the summarizer sees the transcript.

### The Echo Problem

If you use speakers (not headphones), your microphone will pick up the audio coming out of your speakers. This creates near-duplicate segments in the transcript — the same sentence appearing once labeled as a remote speaker and again labeled as you. MeetingScribe automatically removes these acoustic echoes before saving.

The echo filter compares every mic segment against every loopback segment. If a mic segment:
1. Overlaps in time (or starts within 2.5 seconds of) a loopback segment, **and**
2. Shares ≥ 70% word overlap with that loopback segment

…it is identified as an echo and dropped from the transcript.

### Headphones Recommended

Headphones eliminate the echo problem entirely and produce the cleanest transcripts. The echo filter is a software fallback for situations where headphones aren't practical.

---

## Privacy & Security

- **Audio never leaves your machine** — Whisper runs entirely locally on your CPU
- **Only the text transcript** is sent to the summarization API you configure (if any)
- **API keys** are stored in `~/.meetingscribe/config.json` with `chmod 600` permissions
- **No telemetry** — MeetingScribe makes no network calls except to the summarization API you explicitly configure
- `~/.meetingscribe/config.json` is outside the project directory and is never committed to git

---

## Known Limitations

- Speaker identity resets between sessions (no cross-session speaker memory)
- Transcription accuracy depends on audio quality through the loopback device
- `large-v3` model requires ~6 GB of RAM and is slow on CPU
- Diarization accuracy decreases with more than 4 simultaneous speakers
- Linux tray app may require `gnome-shell-extension-appindicator` on GNOME desktops
- **In-person meetings not yet supported** — the current design assumes a loopback device for remote audio. Fully in-person meetings (everyone in the same room) would need mic-side diarization to split multiple voices from a single microphone
- **Hybrid meetings not yet supported** — a mix of in-room and remote participants would require simultaneous diarization on both the loopback and mic streams with coordinated speaker labels
- **Windows not supported** — WASAPI loopback could enable Windows support; no testing has been done
- Installation requires manual Python environment setup; a `brew` formula or standalone app is planned

---

## AI Agent Context

[`AGENTS.md`](AGENTS.md) contains a detailed description of the codebase — architecture, data flow, design decisions, configuration, and known issues. It is intended for:

- **AI coding assistants** working on this repo (OpenAI Codex, Cursor, and others that read `AGENTS.md` natively)
- **Claude Code** users: symlink it so Claude picks it up automatically:
  ```bash
  ln -s AGENTS.md CLAUDE.md
  ```
- **New contributors** who want a fast, structured overview of how everything fits together before diving into the code

If you're opening a PR and using an AI assistant, pointing it at `AGENTS.md` first will give it the context it needs to work effectively.

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on opening issues, submitting pull requests, and the project's coding conventions.

---

## License

MIT — see [LICENSE](LICENSE) for details.
