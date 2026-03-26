from __future__ import annotations

import os
import queue
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from .config import CONFIG_DIR

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
# audiotee persistent-process (FIFO architecture)
# ---------------------------------------------------------------------------
# On macOS 16 (Tahoe), CoreAudio Process Taps exhibit a one-shot behavior:
# audio is captured only on the first run after TCC permission is granted.
# Each subsequent audiotee invocation starts a new tap, which macOS silences.
#
# Fix: keep audiotee alive as a *detached background process* writing to a
# named FIFO (~/.meetingscribe/audiotee.fifo).  A second detached process
# (the "drain") reads and discards audio between recording sessions to
# prevent the FIFO buffer from filling up (which would block audiotee's
# CoreAudio callback and stall audio playback).
#
# Both processes are started with start_new_session=True so they survive
# Python exit.  PID files coordinate multiple Python processes sharing the
# same audiotee instance.  The CoreAudio Tap is created once and never
# destroyed, avoiding the one-shot restriction entirely.

_AUDIOTEE_FIFO = CONFIG_DIR / "audiotee.fifo"
_AUDIOTEE_PID = CONFIG_DIR / "audiotee.pid"
_DRAIN_PID = CONFIG_DIR / "drain.pid"


def _is_pid_alive(pid: int, expected_name: str) -> bool:
    """Check if *pid* is alive and its command name contains *expected_name*."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=2,
        )
        return expected_name in result.stdout.strip().lower()
    except Exception:
        # If ps fails for any reason, trust the os.kill check.
        return True


def _read_pid(path: Path, expected_name: str) -> Optional[int]:
    """Read and validate a PID file.  Returns the PID if alive, else None."""
    try:
        pid = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
    if _is_pid_alive(pid, expected_name):
        return pid
    # Stale PID file — remove it.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return None


def _write_pid(path: Path, pid: int) -> None:
    """Write a PID to file atomically (write tmp then rename)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(pid))
    tmp.rename(path)


def _start_drain_process() -> int:
    """Spawn a detached subprocess that reads from the FIFO and discards data.

    The drain prevents the FIFO buffer from filling up while no recording
    session is active, which would otherwise block audiotee's CoreAudio
    callback and stall system audio playback.

    Returns the drain process PID.
    """
    drain_script = (
        "import os,sys,signal\n"
        "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\n"
        "f=os.open(sys.argv[1],os.O_RDONLY)\n"
        "while True:\n"
        " try:\n"
        "  d=os.read(f,65536)\n"
        "  if not d:break\n"
        " except:break\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", drain_script, str(_AUDIOTEE_FIFO)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _write_pid(_DRAIN_PID, proc.pid)
    return proc.pid


def _stop_drain_process() -> None:
    """Kill the drain subprocess if it is running."""
    pid = _read_pid(_DRAIN_PID, "python")
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):          # wait up to 2 s
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except ProcessLookupError:
        pass
    try:
        _DRAIN_PID.unlink()
    except FileNotFoundError:
        pass


def _bootstrap_audiotee() -> int:
    """Start audiotee + drain as detached processes writing/reading a FIFO.

    Returns the audiotee PID.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Create FIFO if it doesn't exist (or replace a non-FIFO file at the path).
    if _AUDIOTEE_FIFO.exists():
        if not stat.S_ISFIFO(os.stat(str(_AUDIOTEE_FIFO)).st_mode):
            _AUDIOTEE_FIFO.unlink()
            os.mkfifo(str(_AUDIOTEE_FIFO))
    else:
        os.mkfifo(str(_AUDIOTEE_FIFO))

    # O_RDWR opens a FIFO without blocking (POSIX-guaranteed).  This avoids
    # the deadlock where O_WRONLY blocks until a reader opens the other end.
    fd = os.open(str(_AUDIOTEE_FIFO), os.O_RDWR)

    try:
        proc = subprocess.Popen(
            ["audiotee", "--sample-rate", "16000"],
            stdout=fd,
            stderr=None,
            start_new_session=True,
        )
    finally:
        os.close(fd)

    _write_pid(_AUDIOTEE_PID, proc.pid)
    _start_drain_process()
    return proc.pid


def _ensure_audiotee_fifo() -> int:
    """Ensure audiotee is running as a persistent FIFO-backed process.

    Returns the audiotee PID (starting it if necessary).
    """
    pid = _read_pid(_AUDIOTEE_PID, "audiotee")
    if pid is not None:
        # Verify the FIFO still exists on disk.
        if not _AUDIOTEE_FIFO.exists():
            # FIFO was deleted — audiotee still holds its fd, but new readers
            # cannot connect.  Kill everything and bootstrap fresh.
            cleanup_audiotee(quiet=True)
            return _bootstrap_audiotee()
        # Ensure drain is running (may have crashed).
        if _read_pid(_DRAIN_PID, "python") is None:
            _start_drain_process()
        return pid
    return _bootstrap_audiotee()


def cleanup_audiotee(*, quiet: bool = False) -> None:
    """Kill persistent audiotee + drain processes and remove state files.

    Public API — called by ``meetingscribe cleanup``.
    """
    drain_pid = _read_pid(_DRAIN_PID, "python")
    audiotee_pid = _read_pid(_AUDIOTEE_PID, "audiotee")

    if drain_pid is not None:
        _stop_drain_process()
        if not quiet:
            print(f"  Stopped drain process (PID {drain_pid}).")

    if audiotee_pid is not None:
        try:
            os.kill(audiotee_pid, signal.SIGTERM)
            for _ in range(50):       # wait up to 5 s
                try:
                    os.kill(audiotee_pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    break
            else:
                try:
                    os.kill(audiotee_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except ProcessLookupError:
            pass
        try:
            _AUDIOTEE_PID.unlink()
        except FileNotFoundError:
            pass
        if not quiet:
            print(f"  Stopped audiotee process (PID {audiotee_pid}).")

    if _AUDIOTEE_FIFO.exists():
        try:
            _AUDIOTEE_FIFO.unlink()
        except FileNotFoundError:
            pass
        if not quiet:
            print("  Removed FIFO.")

    if not quiet:
        if not drain_pid and not audiotee_pid:
            print("  No persistent audiotee processes found.")
        else:
            print("  Cleanup complete.")


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
    Captures system audio via a persistent ``audiotee`` process writing to a
    named FIFO (macOS 14.2+).  audiotee uses CoreAudio Taps to record all
    system output without a virtual driver — audio still plays through the
    user's speakers/headphones normally.

    The audiotee process runs detached (``start_new_session=True``) and
    survives Python exit.  This avoids the macOS 16 (Tahoe) one-shot TCC
    permission issue where each new process invocation receives only silence
    after the first.  Between recording sessions a lightweight drain process
    reads and discards from the FIFO so audiotee never blocks.

    Implements the same public interface as AudioRecorder so it can be used
    as a drop-in replacement for the loopback stream.

    Requires the ``audiotee`` binary in PATH.  See: github.com/makeusabrew/audiotee
    """

    def __init__(self, chunk_seconds: int = CHUNK_SECONDS) -> None:
        self.chunk_seconds = chunk_seconds
        self.chunk_queue: queue.Queue[tuple[Optional[Path], float]] = queue.Queue()
        self._tmpdir = Path(tempfile.mkdtemp(prefix="meetingscribe_"))
        self._chunk_index = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fifo_file: Optional[object] = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop_event.clear()

        # Ensure the persistent audiotee process is running.
        _ensure_audiotee_fifo()

        # Open the FIFO for reading BEFORE killing the drain so the FIFO
        # always has at least one reader (prevents audiotee from blocking
        # or receiving SIGPIPE).
        fd = os.open(str(_AUDIOTEE_FIFO), os.O_RDONLY)
        self._fifo_file = os.fdopen(fd, "rb", buffering=0)

        # Now safe to stop drain — we are the reader.
        _stop_drain_process()

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

        # Start drain BEFORE closing our fd so the FIFO always has at
        # least one reader.
        _start_drain_process()

        if self._fifo_file is not None:
            try:
                self._fifo_file.close()
            except Exception:
                pass
            self._fifo_file = None
        self._started = False

    def cleanup(self) -> None:
        import shutil
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Read raw PCM from the FIFO, buffer into chunks, save WAV files."""
        assert self._fifo_file is not None
        # audiotee --sample-rate 16000: 16-bit signed int, mono, little-endian
        # Default chunk duration from audiotee is 200 ms.
        # 16000 samples/s × 0.2 s × 1 ch × 2 bytes/sample = 6400 bytes.
        BYTES_PER_READ = int(SAMPLE_RATE * 0.2 * 2)
        samples_per_chunk = SAMPLE_RATE * self.chunk_seconds

        buffer: list[np.ndarray] = []
        buffer_samples = 0
        leftover = b""   # byte-alignment guard for 16-bit samples

        # Silence detection: warn if the first 15 s of audio are all silent.
        _SILENCE_WARN_SAMPLES = SAMPLE_RATE * 15
        _total_samples = 0
        _non_silent_samples = 0
        _silence_warned = False

        while not self._stop_event.is_set():
            try:
                ready = select.select([self._fifo_file], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready[0]:
                continue
            raw = self._fifo_file.read(BYTES_PER_READ)
            if not raw:
                break  # audiotee exited

            # 16-bit samples require an even byte count.
            if leftover:
                raw = leftover + raw
                leftover = b""
            if len(raw) % 2 != 0:
                leftover = raw[-1:]
                raw = raw[:-1]
            if not raw:
                continue

            audio = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0

            if not _silence_warned:
                _total_samples += len(audio)
                if np.abs(audio).mean() >= SILENCE_THRESHOLD:
                    _non_silent_samples += len(audio)
                if _total_samples >= _SILENCE_WARN_SAMPLES and _non_silent_samples == 0:
                    _silence_warned = True
                    print(
                        "\n[audiotee] No system audio detected in the first 15 seconds.\n"
                        "  This is normal if no audio is playing through your speakers yet.\n"
                        "  The loopback stream will start capturing once system audio begins.\n"
                        "  Your microphone stream (if configured) is unaffected.\n"
                        "\n"
                        "  If you DO expect system audio and this persists, audiotee may\n"
                        "  need to be re-signed:  codesign --sign - --force $(which audiotee)\n"
                        "  Then run:  meetingscribe cleanup  and start a new session.\n"
                    )

            buffer.append(audio)
            buffer_samples += len(audio)

            if buffer_samples >= samples_per_chunk:
                self._flush_buffer(buffer, final=False)
                buffer = []
                buffer_samples = 0

        # Flush any remaining audio.
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
