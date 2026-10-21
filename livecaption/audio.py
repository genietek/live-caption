"""Audio devices, capture and resampling.

Capture keeps the original scripts' approach: PyAudioWPatch opens the WASAPI
loopback device (or a microphone) at its native rate and channel count, so
Windows does no resampling, and 16-bit blocks are handed to the transcription
worker through a bounded queue. Capture never waits on inference.

This version uses PyAudio's callback mode (PortAudio's own thread delivers
blocks) and a small supervisor thread that reopens the device after an error
or disconnection.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

WHISPER_RATE = 16000
BLOCK_FRAMES = 1024
LOOPBACK_SUFFIX = " [Loopback]"


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    rate: int
    channels: int
    loopback: bool
    is_default: bool = False

    @property
    def label(self) -> str:
        name = self.name[: -len(LOOPBACK_SUFFIX)] if self.name.endswith(LOOPBACK_SUFFIX) else self.name
        return name + (" (default)" if self.is_default else "")


class AudioError(RuntimeError):
    pass


def _pyaudio():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:
        raise AudioError("PyAudioWPatch is not installed. System audio capture needs "
                         "Windows and the 'pyaudiowpatch' package.") from exc
    return pyaudio


def list_devices() -> list[AudioDevice]:
    """All WASAPI loopback and input devices. Creates a fresh PortAudio
    instance so newly connected devices show up."""
    pyaudio = _pyaudio()
    devices: list[AudioDevice] = []
    with pyaudio.PyAudio() as p:
        try:
            wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError as exc:
            raise AudioError("Windows WASAPI audio is not available.") from exc
        default_loop = None
        try:
            default_loop = p.get_default_wasapi_loopback()["index"]
        except Exception:
            pass
        default_in = wasapi.get("defaultInputDevice", -1)
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d.get("hostApi") != wasapi["index"] or d.get("maxInputChannels", 0) < 1:
                continue
            loop = bool(d.get("isLoopbackDevice"))
            devices.append(AudioDevice(
                index=i, name=d["name"], rate=int(d["defaultSampleRate"]),
                channels=int(d["maxInputChannels"]), loopback=loop,
                is_default=(i == default_loop) if loop else (i == default_in)))
    return devices


def choose_device(devices: list[AudioDevice], source: str, wanted: str,
                  preferred: str) -> AudioDevice:
    """Pick the capture device.

    wanted: exact device name chosen in Settings ("" = default).
    preferred: a name fragment preferred over the default when present
    (the original scripts preferred "Poly BT700"); empty disables it.
    """
    loopback = source == "loopback"
    pool = [d for d in devices if d.loopback == loopback]
    kind = "system audio (loopback)" if loopback else "microphone"
    if not pool:
        raise AudioError(f"No {kind} devices were found. Check that an output "
                         "device is enabled in Windows Sound settings."
                         if loopback else f"No {kind} devices were found.")
    if wanted:
        for d in pool:
            if d.name == wanted:
                return d
        raise AudioError(f"Audio device '{wanted}' is not connected.")
    if preferred:
        for d in pool:
            if preferred.lower() in d.name.lower():
                return d
    for d in pool:
        if d.is_default:
            return d
    return pool[0]


def pcm16_to_mono_float32(data: bytes, channels: int) -> np.ndarray:
    """int16 interleaved PCM -> mono float32 in [-1, 1] (from the original scripts)."""
    audio = np.frombuffer(data, dtype=np.int16)
    if channels > 1:
        usable = (len(audio) // channels) * channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1, dtype=np.float32)
    else:
        audio = audio.astype(np.float32)
    audio *= 1.0 / 32768.0
    return audio


class Resampler:
    """Resample mono float32 audio to 16 kHz with numpy only.

    A Kaiser-windowed sinc low-pass (cut-off 7.2 kHz, stop-band from 8 kHz)
    removes content Whisper cannot use, then integer ratios (48/96/32 kHz) are
    decimated and other rates (44.1 kHz) linearly interpolated. Replaces
    scipy.signal.resample_poly, which pulled in all of SciPy for one call.
    """

    def __init__(self, src_rate: int, dst_rate: int = WHISPER_RATE):
        self.src = int(src_rate)
        self.dst = int(dst_rate)
        self.taps = None
        if self.src == self.dst:
            return
        if self.src < self.dst:
            self.step = None
            return
        transition = 0.1 * self.dst / self.src                # 1.6 kHz wide, in cycles/sample
        n = int(math.ceil((80 - 8) / (2.285 * 2 * math.pi * transition))) | 1
        cutoff = 0.45 * self.dst / self.src                   # 7.2 kHz
        m = np.arange(n) - (n - 1) / 2
        taps = 2 * cutoff * np.sinc(2 * cutoff * m) * np.kaiser(n, 8.0)
        self.taps = (taps / taps.sum()).astype(np.float32)
        self.step = self.src // self.dst if self.src % self.dst == 0 else None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.src == self.dst or len(x) == 0:
            return np.ascontiguousarray(x, dtype=np.float32)
        y = np.convolve(x, self.taps, mode="same") if self.taps is not None else x
        if self.step:
            return np.ascontiguousarray(y[:: self.step], dtype=np.float32)
        n_out = int(len(x) * self.dst / self.src)
        t = np.arange(n_out, dtype=np.float64) * (self.src / self.dst)
        return np.interp(t, np.arange(len(y)), y).astype(np.float32)


class AudioCapture:
    """Captures audio into `out` as (rate, channels, bytes, monotonic time) tuples.

    If the queue is full the oldest block is dropped, so capture never blocks.
    The transcription worker applies the real latency limit; this cap only
    protects memory.
    """

    def __init__(self, out: queue.Queue, source: str, device_name: str, preferred: str,
                 on_status: Callable[[str, bool], None]):
        self.out = out
        self.source = source
        self.device_name = device_name
        self.preferred = preferred
        self.on_status = on_status
        self.device: AudioDevice | None = None
        self.overflow_blocks = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, name="AudioCapture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _push(self, item) -> None:
        try:
            self.out.put_nowait(item)
        except queue.Full:
            self.overflow_blocks += 1
            try:
                self.out.get_nowait()
            except queue.Empty:
                pass
            try:
                self.out.put_nowait(item)
            except queue.Full:
                pass

    def _supervise(self) -> None:
        retry = 1.0
        while not self._stop.is_set():
            try:
                self._run_once()
                retry = 1.0
            except Exception as exc:
                msg = str(exc) if isinstance(exc, AudioError) else f"Audio capture error: {exc}"
                log.warning("%s", msg)
                self.on_status(f"{msg} Retrying...", True)
                if self._stop.wait(retry):
                    return
                retry = min(retry * 2, 10.0)

    def _run_once(self) -> None:
        pyaudio = _pyaudio()
        device = choose_device(list_devices(), self.source, self.device_name, self.preferred)
        self.device = device
        rate, channels = device.rate, device.channels

        def callback(in_data, frame_count, time_info, status):
            if in_data:
                self._push((rate, channels, in_data, time.monotonic()))
            return (None, pyaudio.paContinue)

        with pyaudio.PyAudio() as p:
            stream = p.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=device.index, frames_per_buffer=BLOCK_FRAMES,
                            stream_callback=callback)
            try:
                self.on_status(f"Capturing {device.label} ({rate} Hz, {channels} ch)", False)
                while not self._stop.wait(0.25):
                    if not stream.is_active():
                        raise AudioError(f"Audio device '{device.label}' stopped or was disconnected.")
            finally:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
