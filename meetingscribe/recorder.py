from __future__ import annotations

import atexit
import queue
import select
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SECONDS = 30
SILENCE_THRESHOLD = 0.001

# Keywords used to auto-detect loopback / virtual audio devices
_LOOPBACK_KEYWORDS = [
    "blackhole",
    "loopback",
    "monitor",
    "virtual",
    "soundflower",
    "stereo mix",
]


# ---------------------------------------------------------------------------
# audiotee persistent-process singleton
# ---------------------------------------------------------------------------
# On macOS 16 (Tahoe), CoreAudio Process Taps exhibit a one-shot behavior:
# audio is captured only on the first run after TCC permission is granted.
# Each subsequent audiotee invocation starts a new tap, which macOS silences.
#
# Fix: keep audiotee alive as a module-level singleton across recording
# sessions. The tap is created once, stays open, and sessions share the same
# running process. A drain thread reads and discards audio between sessions
# to prevent the pipe buffer from filling up (which would block audiotee's
# CoreAudio callback and stall audio playback).

_audiotee_proc: Optional[subprocess.Popen] = None
_audiotee_lock = threading.Lock()
_drain_thread: Optional[threading.Thread] = None
_drain_stop = threading.Event()


def _ensure_audiotee() -> subprocess.Popen:
    """Return the running audiotee process, starting it if needed."""
    global _audiotee_proc
    with _audiotee_lock:
        if _audiotee_proc is None or _audiotee_proc.poll() is not None:
            _audiotee_proc = subprocess.Popen(
                ["audiotee", "--sample-rate", "16000"],
                stdout=subprocess.PIPE,
                stderr=None,
            )
            atexit.register(_shutdown_audiotee)
        return _audiotee_proc


def _shutdown_audiotee() -> None:
    """Terminate audiotee when the Python process exits."""
    global _audiotee_proc
    _stop_drain()
    if _audiotee_proc is not None and _audiotee_proc.poll() is None:
        _audiotee_proc.terminate()
        try:
            _audiotee_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _audiotee_proc.kill()
    _audiotee_proc = None


def _start_drain(proc: subprocess.Popen) -> None:
    """Start a background thread that reads and discards audiotee output.

    This keeps the pipe buffer from filling up between sessions, which would
    otherwise block audiotee's CoreAudio callback and stall audio playback.
    """
    global _drain_thread
    _stop_drain()
    _drain_stop.clear()
    _drain_thread = threading.Thread(
        target=_drain_loop, args=(proc,), daemon=True, name="audiotee-drain"
    )
    _drain_thread.start()


def _stop_drain() -> None:
    global _drain_thread
    _drain_stop.set()
    if _drain_thread is not None and _drain_thread.is_alive():
        _drain_thread.join(timeout=2)
    _drain_thread = None


def _drain_loop(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    while not _drain_stop.is_set():
        try:
            ready = select.select([proc.stdout], [], [], 0.1)
            if ready[0]:
                data = proc.stdout.read(6400)
                if not data:
                    break  # process exited
        except (OSError, ValueError):
            break


def _restart_audiotee(old_proc: subprocess.Popen) -> subprocess.Popen:
    """Reset the AudioCapture TCC permission and start a fresh audiotee process.

    On macOS 16 (Tahoe), exclusive CoreAudio Process Taps have a one-shot
    permission behavior: audio flows only on the first audiotee invocation
    after TCC permission is granted; subsequent invocations receive silence.
    Resetting the AudioCapture TCC entry forces macOS to re-prompt, granting
    access for the new process.
    """
    global _audiotee_proc
    subprocess.run(["tccutil", "reset", "AudioCapture"], capture_output=True)
    if old_proc.poll() is None:
        old_proc.terminate()
        try:
            old_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            old_proc.kill()
    with _audiotee_lock:
        _audiotee_proc = subprocess.Popen(
            ["audiotee", "--sample-rate", "16000"],
            stdout=subprocess.PIPE,
            stderr=None,
        )
        return _audiotee_proc


def list_devices() -> list[dict]:
    """Return a list of all sounddevice input devices."""
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            devices.append({"index": i, "name": dev["name"], "channels": dev["max_input_channels"]})
    return devices


def find_loopback_device() -> Optional[int]:
    """Return the device index of the first loopback/virtual audio device found, or None."""
    for dev in list_devices():
        name_lower = dev["name"].lower()
        if any(kw in name_lower for kw in _LOOPBACK_KEYWORDS):
            return dev["index"]
    return None


class AudioRecorder:
    """
    Captures audio from a sounddevice input stream and emits 30-second WAV chunks
    into a queue.Queue for downstream transcription.
    """

    def __init__(
        self,
        device_index: Optional[int] = None,
        chunk_seconds: int = CHUNK_SECONDS,
    ) -> None:
        self.device_index = device_index
        self.chunk_seconds = chunk_seconds

        # Queue items are (path, duration_seconds).
        # path=None means a silent chunk: the transcriber should advance its clock
        # by duration_seconds without running Whisper. This keeps both streams'
        # timestamps in sync even when one stream has silent periods.
        self.chunk_queue: queue.Queue[tuple[Optional[Path], float]] = queue.Queue()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="meetingscribe_"))
        self._chunk_index = 0
        self._buffer: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._flush_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_event.clear()

        device = self.device_index if self.device_index is not None else find_loopback_device()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=device,
            callback=self._audio_callback,
            blocksize=int(SAMPLE_RATE * 0.1),  # 100 ms blocks
        )
        self._stream.start()

        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True, name="recorder-flush")
        self._flush_thread.start()

    def stop(self) -> None:
        """Stop recording. Flushes any remaining buffered audio as a final chunk."""
        if not self._started:
            return
        self._stop_event.set()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._flush_thread:
            self._flush_thread.join(timeout=10)
        # Flush remaining buffer
        self._save_chunk(final=True)
        self._started = False

    def cleanup(self) -> None:
        """Delete all temporary WAV files created during this session."""
        import shutil
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        with self._lock:
            self._buffer.append(indata.copy().flatten())

    def _flush_loop(self) -> None:
        """Periodically save buffered audio as WAV chunks."""
        samples_per_chunk = SAMPLE_RATE * self.chunk_seconds
        accumulated = 0

        while not self._stop_event.is_set():
            time.sleep(0.5)
            with self._lock:
                total = sum(len(b) for b in self._buffer)
                accumulated = total

            if accumulated >= samples_per_chunk:
                self._save_chunk()

    def _save_chunk(self, final: bool = False) -> None:
        with self._lock:
            if not self._buffer:
                return
            audio = np.concatenate(self._buffer)
            self._buffer.clear()

        duration = len(audio) / SAMPLE_RATE

        # Silent chunks: don't write to disk or run Whisper, but still enqueue
        # a clock-advance sentinel so the transcriber's elapsed offset stays in
        # sync with wall time.
        if np.abs(audio).mean() < SILENCE_THRESHOLD and not final:
            self.chunk_queue.put((None, duration))
            return

        path = self._tmpdir / f"chunk_{self._chunk_index:04d}.wav"
        self._chunk_index += 1
        sf.write(str(path), audio, SAMPLE_RATE)
        self.chunk_queue.put((path, duration))


# ---------------------------------------------------------------------------
# AudioTee backend (macOS 14.2+ — no virtual driver required)
# ---------------------------------------------------------------------------

class AudioTeeRecorder:
    """
    Captures system audio by spawning the `audiotee` subprocess (macOS 14.2+).
    audiotee uses CoreAudio Taps to record all system output without a virtual
    driver. Audio still plays through the user's speakers/headphones normally.

    Implements the same public interface as AudioRecorder so it can be used
    as a drop-in replacement for the loopback stream.

    Requires the `audiotee` binary in PATH. See: github.com/makeusabrew/audiotee
    """

    def __init__(self, chunk_seconds: int = CHUNK_SECONDS) -> None:
        self.chunk_seconds = chunk_seconds
        self.chunk_queue: queue.Queue[tuple[Optional[Path], float]] = queue.Queue()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="meetingscribe_"))
        self._chunk_index = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        # Get or reuse the persistent audiotee process. Keeping audiotee alive
        # across sessions avoids the macOS 16 one-shot permission issue where
        # each new process invocation receives only silence after the first run.
        _stop_drain()
        self._process = _ensure_audiotee()
        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="audiotee-reader"
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        # Keep audiotee alive — terminating it would destroy the CoreAudio Tap
        # and trigger the macOS 16 one-shot issue on the next session.
        # Start a drain thread to prevent the pipe buffer from filling up.
        if self._process is not None:
            _start_drain(self._process)
        self._started = False

    def cleanup(self) -> None:
        import shutil
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Read raw PCM from audiotee stdout, buffer into chunks, save WAV files."""
        assert self._process is not None and self._process.stdout is not None
        # audiotee --sample-rate 16000: 16-bit signed int, mono, little-endian
        # Default chunk duration from audiotee is 200ms.
        # 16000 samples/s × 0.2s × 1 channel × 2 bytes/sample = 6400 bytes per read.
        BYTES_PER_READ = int(SAMPLE_RATE * 0.2 * 2)
        samples_per_chunk = SAMPLE_RATE * self.chunk_seconds

        buffer: list[np.ndarray] = []
        buffer_samples = 0

        # Silence detection: warn if the first 15 seconds of audio are all silent.
        # On macOS 16 (Tahoe), an unsigned audiotee binary may receive only silence
        # because macOS cannot anchor a TCC (System Audio Recording) permission to
        # an unsigned subprocess. The fix is: codesign --sign - --force $(which audiotee)
        _SILENCE_WARN_SAMPLES = SAMPLE_RATE * 15
        _total_samples = 0
        _non_silent_samples = 0
        _silence_warned = False

        while not self._stop_event.is_set():
            # Use select() with a short timeout so the loop can check
            # _stop_event without blocking on read() indefinitely.
            # (We don't terminate audiotee on stop, so the pipe stays open.)
            try:
                ready = select.select([self._process.stdout], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready[0]:
                continue
            raw = self._process.stdout.read(BYTES_PER_READ)
            if not raw:
                break  # process exited unexpectedly
            audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0

            if not _silence_warned:
                _total_samples += len(audio)
                if np.abs(audio).mean() >= SILENCE_THRESHOLD:
                    _non_silent_samples += len(audio)
                if _total_samples >= _SILENCE_WARN_SAMPLES and _non_silent_samples == 0:
                    _silence_warned = True
                    print(
                        "\n[audiotee] WARNING: 15 seconds of silence — no system audio captured.\n"
                        "  On macOS 16 (Tahoe), audiotee must be code-signed to receive audio.\n"
                        "  Fix: run  codesign --sign - --force $(which audiotee)\n"
                        "       then quit MeetingScribe and start a new session.\n"
                        "  A System Audio Recording permission prompt may appear on the next run.\n"
                        "  Or re-run the installer:  bash scripts/install_mac.sh\n"
                    )

            buffer.append(audio)
            buffer_samples += len(audio)

            if buffer_samples >= samples_per_chunk:
                self._flush_buffer(buffer, final=False)
                buffer = []
                buffer_samples = 0

        # Flush any remaining audio
        if buffer:
            self._flush_buffer(buffer, final=True)

    def _flush_buffer(self, buffer: list[np.ndarray], final: bool) -> None:
        if not buffer:
            return
        audio = np.concatenate(buffer)
        duration = len(audio) / SAMPLE_RATE

        if np.abs(audio).mean() < SILENCE_THRESHOLD and not final:
            self.chunk_queue.put((None, duration))
            return

        path = self._tmpdir / f"chunk_{self._chunk_index:04d}.wav"
        self._chunk_index += 1
        sf.write(str(path), audio, SAMPLE_RATE)
        self.chunk_queue.put((path, duration))


# ---------------------------------------------------------------------------
# Backend detection helpers
# ---------------------------------------------------------------------------

def audiotee_available() -> bool:
    """True if the audiotee binary is present in PATH."""
    import shutil
    return shutil.which("audiotee") is not None


def macos_version() -> tuple[int, int]:
    """
    Returns the (major, minor) macOS version, e.g. (14, 4).
    Returns (0, 0) on non-macOS platforms.
    """
    import platform
    import sys
    if sys.platform != "darwin":
        return (0, 0)
    ver = platform.mac_ver()[0]  # e.g. "14.4.1"
    parts = ver.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)
