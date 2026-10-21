import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from livecaption import audio, engine, hardware, winapi

# -- text helpers (behaviour carried over from the original scripts) -----------


def test_remove_overlap():
    prev = "we will review the quarterly results".split()
    assert engine.remove_overlap(prev, "quarterly results, and then the plan") == "and then the plan"
    assert engine.remove_overlap(prev, "something new entirely") == "something new entirely"
    assert engine.remove_overlap([], "hello there") == "hello there"


def test_suspicious_text():
    assert engine.suspicious_text("React.js.js.js.js.js")
    assert engine.suspicious_text("the the the the the the the the the the")
    assert not engine.suspicious_text("Hola, ¿cómo estás? Muy bien, gracias por preguntar.")


# -- language choice ---------------------------------------------------------------

@pytest.mark.parametrize("probs, expected", [
    ([("en", 0.9), ("es", 0.05)], "en"),
    ([("es", 0.8), ("pt", 0.1)], "es"),
    ([("pt", 0.45), ("es", 0.40), ("en", 0.15)], "es"),    # en+es mass >= 0.5
    ([("fr", 0.9), ("en", 0.05), ("es", 0.01)], None),     # French is not forced into en/es
    ([("de", 0.6), ("en", 0.3), ("es", 0.1)], None),
])
def test_pick_language(probs, expected):
    assert engine.pick_language(probs)[0] == expected


def test_language_tracker_caches_and_redetects():
    t = engine.LanguageTracker("auto", redetect_seconds=30, silence_reset=10)
    assert t.needs_detection(0)
    assert t.update("es", 0.9, now=0) == "es"
    t.last_speech = 1
    assert not t.needs_detection(5)          # cached
    assert t.needs_detection(31)             # interval elapsed
    assert t.needs_detection(12)             # long silence since last speech
    assert t.update("en", 0.4, now=31) == "es"   # weak evidence does not switch
    assert t.update("en", 0.9, now=32) == "en"   # strong evidence does
    assert t.update(None, 0.1, now=33) is None   # other language -> skip chunk
    assert t.current == "en"
    fixed = engine.LanguageTracker("es")
    assert not fixed.needs_detection(0) and fixed.current == "es"


# -- model loading / CPU fallback -----------------------------------------------------

class FakeModel:
    def __init__(self, path, device="cpu", fail_on_cuda=False, **kw):
        if device == "cuda" and fail_on_cuda:
            raise RuntimeError("Library cublas64_12.dll is not found")
        self.device, self.kw = device, kw

    def detect_language(self, audio):
        return "en", 1.0, [("en", 1.0)]


def test_auto_falls_back_to_cpu_when_gpu_load_fails(monkeypatch):
    monkeypatch.setattr(hardware, "cuda_runtime_check", lambda: (True, "1 device", {"float16"}))
    loaded = engine.load_model("small", "x", "auto", "auto",
                               factory=lambda p, **kw: FakeModel(p, fail_on_cuda=True, **kw))
    assert loaded.device == "cpu" and loaded.compute_type == "int8"
    assert "cublas" in loaded.note


def test_auto_uses_gpu_float16(monkeypatch):
    monkeypatch.setattr(hardware, "cuda_runtime_check", lambda: (True, "1 device", {"float16", "int8"}))
    loaded = engine.load_model("small", "x", "auto", "auto", factory=FakeModel)
    assert (loaded.device, loaded.compute_type) == ("cuda", "float16")


def test_explicit_gpu_reports_instead_of_silent_fallback(monkeypatch):
    monkeypatch.setattr(hardware, "cuda_runtime_check", lambda: (False, "No CUDA device", set()))
    with pytest.raises(engine.GpuUnavailable, match="No CUDA device"):
        engine.load_model("small", "x", "cuda", "auto", factory=FakeModel)


def test_cpu_never_touches_cuda(monkeypatch):
    monkeypatch.setattr(hardware, "cuda_runtime_check", lambda: pytest.fail("CUDA checked"))
    loaded = engine.load_model("small", "x", "cpu", "float16", factory=FakeModel)
    assert (loaded.device, loaded.compute_type) == ("cpu", "int8")   # float16 is GPU-only


# -- transcription pipeline -------------------------------------------------------------

RATE = 48000


def tone_block(frames=audio.BLOCK_FRAMES, amp=0.2, channels=2):
    t = np.arange(frames) / RATE
    mono = (amp * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    return np.repeat(mono, channels).tobytes()


class FakeCapture:
    def __init__(self, out, *args):
        self.out = out
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self, timeout=0):
        self.stopped = True

    def feed(self, seconds, amp=0.2, channels=2, rate=RATE):
        for _ in range(int(seconds * rate / audio.BLOCK_FRAMES)):
            self.out.put((rate, channels, tone_block(amp=amp, channels=channels), time.monotonic()))


class ScriptedModel:
    def __init__(self, texts, lang_probs=(("es", 0.9),), delay=0.0):
        self.texts = list(texts)
        self.lang_probs = list(lang_probs)
        self.delay = delay
        self.calls = []
        self.detections = 0

    def detect_language(self, audio):
        self.detections += 1
        return self.lang_probs[0][0], self.lang_probs[0][1], self.lang_probs

    def transcribe(self, audio, **kw):
        assert audio.dtype == np.float32 and audio.ndim == 1
        self.calls.append((len(audio), kw["language"]))
        time.sleep(self.delay)
        text = self.texts.pop(0) if self.texts else ""
        return [SimpleNamespace(text=text)], None


def make_transcriber(model, **opts):
    events = []
    lock = threading.Lock()

    def emit(kind, value):
        with lock:
            events.append((kind, value))
    loaded = engine.LoadedModel(model, "small", None, "cpu", "int8", 0.0)
    options = engine.StreamOptions(**{"chunk_seconds": 1.0, "overlap_seconds": 0.25, **opts})
    t = engine.Transcriber(loaded, options, emit, capture_factory=FakeCapture)
    return t, events


def wait_for(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def captions(events):
    return [v["text"] for k, v in events if k == "caption"]


def test_pipeline_emits_captions_and_removes_overlap():
    model = ScriptedModel(["hola a todos bienvenidos", "todos bienvenidos a la reunión"])
    t, events = make_transcriber(model)
    t.start()
    t._capture.feed(2.0)
    assert wait_for(lambda: len(captions(events)) >= 2)
    t.stop()
    assert captions(events) == ["hola a todos bienvenidos", "a la reunión"]
    n16, lang = model.calls[0]
    assert n16 == 16000 and lang == "es"          # 1 s chunk resampled 48k -> 16k
    assert model.detections == 1                  # detection cached across chunks
    assert ("stopped", t) in events


def test_silence_is_not_transcribed():
    model = ScriptedModel(["should not appear"])
    t, events = make_transcriber(model)
    t.start()
    t._capture.feed(2.0, amp=0.0)
    assert wait_for(lambda: t.metrics.skipped_silent >= 2)
    t.stop()
    assert model.calls == [] and captions(events) == []


def test_other_language_is_not_captioned():
    model = ScriptedModel(["bonjour"], lang_probs=[("fr", 0.95), ("en", 0.02), ("es", 0.01)])
    t, events = make_transcriber(model)
    t.start()
    t._capture.feed(1.2)
    assert wait_for(lambda: t.metrics.skipped_other_language >= 1)
    t.stop()
    assert model.calls == [] and ("language", None) in events


def test_fixed_language_skips_detection():
    model = ScriptedModel(["hello"])
    t, events = make_transcriber(model, language="en")
    t.start()
    t._capture.feed(1.2)
    assert wait_for(lambda: captions(events))
    t.stop()
    assert model.detections == 0 and model.calls[0][1] == "en"


def test_backlog_is_bounded_and_counted():
    model = ScriptedModel(["x"] * 100, delay=0.5)
    t, events = make_transcriber(model, language="en", max_backlog_seconds=2.0)
    t.start()
    t._capture.feed(1.1)
    assert wait_for(lambda: model.calls)       # worker is now busy in a slow inference
    t._capture.feed(8.0)                       # 8 s arrive at once
    assert wait_for(lambda: t.metrics.dropped_seconds > 0)
    t.stop()
    assert t.metrics.dropped_seconds >= 4.0
    assert any(k == "status" and "skipped" in v for k, v in events)


def test_pause_discards_audio():
    model = ScriptedModel(["x"])
    t, events = make_transcriber(model, language="en")
    t.paused.set()
    t.start()
    t._capture.feed(2.0)
    assert wait_for(lambda: t._queue.empty())
    time.sleep(0.2)
    t.stop()
    assert model.calls == []


def test_device_format_change_is_handled():
    model = ScriptedModel(["one", "two"])
    t, events = make_transcriber(model, language="en")
    t.start()
    t._capture.feed(1.1)
    assert wait_for(lambda: len(model.calls) == 1)
    t._capture.feed(1.1, channels=1, rate=44100)   # e.g. headset reconnected at 44.1 kHz
    assert wait_for(lambda: len(model.calls) == 2)
    t.stop()
    assert model.calls[1][0] == 16000


def test_worker_error_is_reported_and_stops_cleanly():
    class Broken(ScriptedModel):
        def transcribe(self, audio, **kw):
            raise RuntimeError("CUDA out of memory")
    t, events = make_transcriber(Broken([]), language="en")
    t.start()
    t._capture.feed(1.2)
    assert wait_for(lambda: ("stopped", t) in events)
    assert any(k == "error" and "out of memory" in v for k, v in events)
    t.stop(timeout=1)
    assert not t.running


# -- audio helpers -------------------------------------------------------------------------

def test_pcm16_to_mono():
    stereo = np.array([1000, 3000, -32768, -32768], dtype=np.int16).tobytes()
    out = audio.pcm16_to_mono_float32(stereo, 2)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [2000 / 32768, -1.0])


@pytest.mark.parametrize("rate", [48000, 44100, 96000, 32000, 16000])
def test_resampler_keeps_speech_band(rate):
    t = np.arange(rate * 2) / rate
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    y = audio.Resampler(rate)(x)
    assert y.dtype == np.float32 and abs(len(y) - 32000) <= 1
    spec = np.abs(np.fft.rfft(y[1000:-1000]))
    freqs = np.fft.rfftfreq(len(y) - 2000, 1 / 16000)
    assert abs(freqs[spec.argmax()] - 1000) < 5
    assert 0.3 < np.sqrt(np.mean(y[1000:-1000] ** 2)) < 0.4    # ~0.354 = no loss


def test_resampler_removes_aliasing():
    rate = 48000
    t = np.arange(rate * 2) / rate
    x = (0.5 * np.sin(2 * np.pi * 12000 * t)).astype(np.float32)   # would alias to 4 kHz
    y = audio.Resampler(rate)(x)
    assert np.sqrt(np.mean(y[1000:-1000] ** 2)) < 1e-3


def devices():
    D = audio.AudioDevice
    return [D(1, "Speakers (Realtek) [Loopback]", 48000, 2, True, True),
            D(2, "Headset (Poly BT700) [Loopback]", 48000, 2, True),
            D(3, "Microphone (Realtek)", 44100, 1, False, True)]


def test_choose_device():
    devs = devices()
    assert audio.choose_device(devs, "loopback", "", "Poly BT700").index == 2   # preferred when present
    assert audio.choose_device(devs, "loopback", "", "").index == 1             # Windows default
    assert audio.choose_device(devs[:1], "loopback", "", "Poly BT700").index == 1
    assert audio.choose_device(devs, "microphone", "", "Poly").index == 3
    assert audio.choose_device(devs, "loopback", "Speakers (Realtek) [Loopback]", "Poly").index == 1
    with pytest.raises(audio.AudioError, match="not connected"):
        audio.choose_device(devs, "loopback", "USB DAC [Loopback]", "")
    with pytest.raises(audio.AudioError, match="No system audio"):
        audio.choose_device([], "loopback", "", "")


def test_capture_reports_missing_pyaudio(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pyaudiowpatch":
            raise ImportError(name)
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    statuses = []
    cap = audio.AudioCapture(queue.Queue(), "loopback", "", "", lambda m, e: statuses.append((m, e)))
    cap.start()
    assert wait_for(lambda: statuses)
    cap.stop()
    assert statuses[0][1] is True and "PyAudioWPatch" in statuses[0][0]


# -- small Windows helpers -----------------------------------------------------------------

def test_parse_hotkey():
    assert winapi.parse_hotkey("F9") == (0, 0x78)
    assert winapi.parse_hotkey("Ctrl+Alt+C") == (0x3, ord("C"))
    assert winapi.parse_hotkey("Hyper+X") is None
    assert winapi.parse_hotkey("") is None


def test_nvidia_smi_parsing():
    assert hardware.parse_nvidia_smi("NVIDIA GeForce RTX 5060, 8151, 7300, 581.57\n") == \
        ("NVIDIA GeForce RTX 5060", 8151, 7300, "581.57")
    assert hardware.parse_nvidia_smi("garbage") is None
