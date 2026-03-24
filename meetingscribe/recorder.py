from __future__ import annotations

import queue
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

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
        on_chunk: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self.device_index = device_index
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk  # optional extra callback beyond queue

        self.chunk_queue: queue.Queue[Path] = queue.Queue()
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

        # Discard silent chunks
        if np.abs(audio).mean() < SILENCE_THRESHOLD and not final:
            return

        path = self._tmpdir / f"chunk_{self._chunk_index:04d}.wav"
        self._chunk_index += 1
        sf.write(str(path), audio, SAMPLE_RATE)
        self.chunk_queue.put(path)
        if self.on_chunk:
            self.on_chunk(path)
