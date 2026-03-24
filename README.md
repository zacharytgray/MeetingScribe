# MeetingScribe

**Open-source, privacy-first meeting transcription and summarization.** MeetingScribe records your system audio during any meeting (Teams, Zoom, Google Meet, etc.), transcribes it locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper), identifies speakers, and produces a structured markdown summary using an AI model of your choice. No audio ever leaves your machine — transcription is fully local. Only the text transcript is sent to your chosen summarization API, and only if you configure one.

---

## Features

- **100% local transcription** — Whisper runs on your CPU; no audio sent to any server
- **Speaker diarization** — identifies who said what (optional, requires free HuggingFace account)
- **Mic attribution** — captures your microphone as a separate stream, labeled with your name; acoustic echo deduplication removes mic segments that are just your speakers bleeding into the mic
- **AI summarization** — structured markdown notes with action items, key points, and participants
- **Your choice of AI provider** — Anthropic Claude or any OpenRouter model, including free ones
- **Works with any meeting app** — captures system audio; no integrations or plugins needed
- **macOS and Linux** support
- **System tray app** for menu-bar control, or use the CLI directly

---

## How It Works

```
System audio (meeting output)
    ↓  BlackHole (macOS) / PulseAudio monitor (Linux)
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
Summarizer  —  sends transcript to Claude or OpenRouter
    ↓
~/MeetingNotes/2025-06-12_q3_planning.md
```

---

## Requirements

- Python 3.10–3.12
- macOS 12+ or Linux (Ubuntu 20.04+, Fedora, Arch)
- **macOS**: [BlackHole](https://existential.audio/blackhole/) virtual audio driver + [Background Music](https://github.com/kyleneideck/BackgroundMusic) (for volume control)
- **Linux**: PulseAudio or PipeWire with monitor sources

---

## Installation

### macOS (Quick Start)

```bash
# 1. Install audio dependencies
brew install blackhole-2ch
brew install --cask background-music

# 2. Clone the repo
git clone https://github.com/YOUR_USERNAME/meetingscribe
cd meetingscribe

# 3. Create a Python 3.12 venv and install
python3.12 -m venv .venv
source .venv/bin/activate
pip install torch          # macOS: PyTorch installs directly from PyPI
pip install -e .

# 4. Configure
python cli.py setup
```

**One-time audio routing setup (macOS):**
1. Open **Audio MIDI Setup** (Applications → Utilities)
2. Click **+** → **Create Multi-Output Device**
3. Check both **BlackHole 2ch** and your speakers (e.g. "Audioengine 2+")
4. Set **Primary Device** to your speakers
5. Set this Multi-Output Device as your system output in System Settings → Sound
6. Launch **Background Music** — it restores volume control while the Multi-Output Device is active

### Linux (Quick Start)

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get install portaudio19-dev libsndfile1 ffmpeg

# Clone, create venv, and install
git clone https://github.com/YOUR_USERNAME/meetingscribe
cd meetingscribe
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e .

python cli.py setup
```

### Or use the install script

```bash
bash scripts/install_mac.sh    # macOS
bash scripts/install_linux.sh  # Linux
```

---

## Configuration

Run the interactive setup wizard:

```bash
python cli.py setup
```

You'll be prompted for:

| Setting | Description |
|---|---|
| Notes output directory | Where markdown files are saved (default: `~/MeetingNotes`) |
| Anthropic API key | For Claude summarization (optional) |
| OpenRouter API key | Free alternative for summarization (optional) |
| OpenRouter model | Which model to use (see free options below) |
| HuggingFace token | For speaker diarization (free account) |
| Whisper model | Transcription quality/speed tradeoff |
| Audio device | The BlackHole / loopback device index |
| Microphone device | Your mic, for attributing your own voice (optional) |
| Your name | How your voice appears in the transcript |

Configuration is saved to `~/.meetingscribe/config.json` (chmod 600, never committed to git).

All keys can also be set as environment variables:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENROUTER_API_KEY="sk-or-..."
export HF_TOKEN="hf_..."
```

---

## API Key Options

MeetingScribe only needs an API key for **summarization** — transcription is always local. You can also skip summarization entirely and save the raw transcript.

### Option A: Anthropic Claude (Paid)

1. Sign up at [console.anthropic.com](https://console.anthropic.com)
2. Add credits ($5 minimum) and create an API key
3. Paste the key into `python cli.py setup`

Uses `claude-sonnet-4-20250514` by default. Produces the highest quality summaries.

### Option B: OpenRouter (Free models available)

[OpenRouter](https://openrouter.ai) provides access to dozens of models through a single API, including several free-tier options that work well for meeting summarization.

1. Sign up at [openrouter.ai](https://openrouter.ai)
2. Create a free API key
3. Paste it into `python cli.py setup`

**Recommended free models** (set as OpenRouter model in config):

| Model | Speed | Quality | Notes |
|---|---|---|---|
| `meta-llama/llama-3.3-70b-instruct:free` | Fast | Excellent | Best free option overall |
| `nvidia/llama-3.1-nemotron-70b-instruct:free` | Fast | Excellent | Strong instruction following |
| `google/gemma-3-27b-it:free` | Fast | Very good | Good for structured output |
| `microsoft/phi-4:free` | Fast | Good | Lightweight, reliable |
| `deepseek/deepseek-r1:free` | Slower | Excellent | Good for complex summaries |
| `mistralai/mistral-7b-instruct:free` | Very fast | Good | Low latency |

Free models on OpenRouter have rate limits but are sufficient for typical meeting usage (one meeting = one API call).

### Option C: No API key

Skip summarization entirely — MeetingScribe will save the raw timestamped transcript as a markdown file.

---

## Usage

### CLI

```bash
# Start a recording session
python cli.py start

# With specific options
python cli.py start -m small           # use a larger Whisper model
python cli.py start -d 6               # specify audio device index
python cli.py start --no-diarization   # skip speaker identification

# List available audio input devices
python cli.py devices

# Test audio capture from a device (run while audio is playing)
python cli.py test-audio -d 6 -t 5 -s

# Show current configuration
python cli.py config

# Re-run setup wizard
python cli.py setup
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
python tray.py
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
- [ ] Finalize headcount proposal — @Sarah
- [ ] Send revised budget to finance by Friday — @Zach

## 📋 Summary
Prose summary of the discussion...

## 🗣️ Key Discussion Points
- Budget gap of ~$200K identified in engineering headcount
- Decision to defer two contractor renewals until Q4

## 👥 Participants
- Speaker 1 (Sarah)
- Speaker 2

---
*Transcribed and summarized by MeetingScribe*

---

## 📝 Raw Transcript

[00:00–00:05] [Zach] Let's get started — can someone walk me through the numbers?
[00:05–00:18] [Speaker 1] Sure, so the total budget request is...
```

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
4. Paste your token into `python cli.py setup`

Speaker labels persist across 30-second chunks using embedding-based matching, so "Speaker 1" refers to the same person throughout the recording.

---

## Microphone Attribution & Echo Handling

When a microphone device is configured, MeetingScribe runs two audio streams in parallel:

- **Loopback stream** (BlackHole/PulseAudio) — captures all meeting audio; speakers identified by pyannote diarization as "Speaker 1", "Speaker 2", etc.
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

- **Audio never leaves your machine** — Whisper runs entirely locally; only the text transcript is sent to the summarization API (if configured)
- **Only the text transcript** is sent to the summarization API (if configured)
- **API keys** are stored in `~/.meetingscribe/config.json` with `chmod 600` permissions
- **No telemetry** — MeetingScribe makes no network calls except to the summarization API you explicitly configure
- This file (`~/.meetingscribe/config.json`) is outside the project directory and is never committed to git

---

## Known Limitations

- Speaker identity resets if the same person joins a second session (no cross-session speaker memory)
- Transcription accuracy depends on audio quality through the loopback device
- `large-v3` model requires ~6 GB of RAM and is slow on CPU
- Diarization accuracy decreases with more than 4 simultaneous speakers
- Linux tray app may require `gnome-shell-extension-appindicator` on GNOME desktops

---

## License

MIT
