import os
import queue
import re
import threading
import time

import keyboard
import numpy as np
import pyautogui
import pyperclip
import pyaudiowpatch as pyaudio
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

try:
    import ctranslate2
except Exception:
    ctranslate2 = None


# ============================================================
# LIVE CAPTION V2 - IMPROVED
# Simple architecture:
# Poly BT700 -> WASAPI loopback -> capture thread -> queue
# -> Whisper worker -> ChatGPT input
# ============================================================


# ----------------------------
# AUDIO SETTINGS
# ----------------------------

PREFERRED_DEVICE_NAME = "Poly BT700"

# Read small blocks continuously so the Windows audio buffer is
# always drained even while Whisper is transcribing.
CAPTURE_BLOCK_FRAMES = 1024

# Whisper gets enough context for full sentences.
CHUNK_SECONDS = 5.0

# Keep 1 second from the previous chunk to protect words at
# chunk boundaries.
OVERLAP_SECONDS = 1.0

WHISPER_RATE = 16000

# Skip chunks that are almost silent.
SILENCE_THRESHOLD = 0.0012

# Queue capacity measured in audio blocks.
# If Whisper is slower than real time for a long period, the oldest
# block is dropped rather than blocking WASAPI capture.
AUDIO_QUEUE_MAXSIZE = 1200


# ----------------------------
# WHISPER SETTINGS
# ----------------------------

# On your RTX 5060 this is the preferred model.
GPU_MODEL = "distil-large-v3"

# CPU fallback if CUDA/CTranslate2 is not ready.
CPU_MODEL = "small.en"

# Beam 1 gives much lower live latency.
BEAM_SIZE = 1

# Do not force technical vocabulary here.
# The earlier prompt was causing .js.js.js hallucinations.
INITIAL_PROMPT = None


# ----------------------------
# OUTPUT SETTINGS
# ----------------------------

ENABLE_CLIPBOARD_PASTE = True

# F8 toggles pasting.
# F9 quits.
armed = False
stop_event = threading.Event()

audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)

capture_error = None
worker_error = None

recent_output_words = []


# ============================================================
# HOTKEYS
# ============================================================

def toggle_arm():
    global armed
    armed = not armed

    if armed:
        print(
            "\n[ACTIVE] Recognized speech will be pasted "
            "into the focused text box."
        )
    else:
        print(
            "\n[PAUSED] Transcription continues, but nothing "
            "will be pasted."
        )


def request_stop():
    print("\nStopping...")
    stop_event.set()


keyboard.add_hotkey("F8", toggle_arm)
keyboard.add_hotkey("F9", request_stop)


# ============================================================
# TEXT HELPERS
# ============================================================

def normalize_word(word):
    return re.sub(r"(^\W+|\W+$)", "", word).lower()


def remove_overlap(previous_words, current_text, max_words=24):
    """
    Remove duplicated text caused by the 1-second audio overlap.
    """
    words = current_text.split()

    if not previous_words or not words:
        return current_text.strip()

    prev_norm = [normalize_word(w) for w in previous_words]
    curr_norm = [normalize_word(w) for w in words]

    max_check = min(max_words, len(prev_norm), len(curr_norm))

    for count in range(max_check, 1, -1):
        if prev_norm[-count:] == curr_norm[:count]:
            return " ".join(words[count:]).strip()

    return current_text.strip()


def suspicious_text(text):
    """
    Very conservative hallucination filter.
    """
    if not text:
        return True

    low = text.lower()

    if low.count(".js") >= 4:
        return True

    words = [normalize_word(w) for w in text.split()]
    words = [w for w in words if w]

    if len(words) >= 8:
        if len(set(words)) / len(words) < 0.20:
            return True

    return False


def emit_text(text):
    global recent_output_words

    text = remove_overlap(recent_output_words, text)

    if not text:
        return

    if suspicious_text(text):
        print(f"[SKIPPED suspicious transcription] {text}")
        return

    print(f"INTERVIEWER: {text}")

    recent_output_words.extend(text.split())
    recent_output_words = recent_output_words[-80:]

    if armed and ENABLE_CLIPBOARD_PASTE:
        try:
            pyperclip.copy(text + " ")
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.02)
        except Exception as exc:
            print(f"[Paste error] {exc}")


# ============================================================
# GPU / MODEL SETUP
# ============================================================

def load_whisper():
    """
    Prefer RTX/CUDA. Fall back to CPU automatically if CUDA is not
    visible to CTranslate2.
    """
    use_cuda = False

    if ctranslate2 is not None:
        try:
            gpu_count = ctranslate2.get_cuda_device_count()
            use_cuda = gpu_count > 0
        except Exception:
            use_cuda = False

    if use_cuda:
        print("CUDA GPU detected by CTranslate2.")
        print(f"Loading {GPU_MODEL} on GPU (float16)...")

        try:
            model = WhisperModel(
                GPU_MODEL,
                device="cuda",
                device_index=0,
                compute_type="float16",
                num_workers=1
            )
            print("Whisper GPU model loaded.")
            return model, "GPU"

        except Exception as exc:
            print("\nGPU model failed to load:")
            print(exc)
            print("\nFalling back to CPU.\n")

    cpu_threads = max(2, (os.cpu_count() or 6) - 2)

    print(f"Loading {CPU_MODEL} on CPU (int8)...")

    model = WhisperModel(
        CPU_MODEL,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        num_workers=1
    )

    print("Whisper CPU model loaded.")

    return model, "CPU"


model, inference_device = load_whisper()


# ============================================================
# FIND POLY BT700 WASAPI LOOPBACK
# ============================================================

def choose_loopback_device(p):
    """
    Prefer a loopback device whose name contains 'Poly BT700'.
    Otherwise use the default WASAPI loopback output.
    """

    loopbacks = list(p.get_loopback_device_info_generator())

    if not loopbacks:
        raise RuntimeError(
            "No WASAPI loopback devices were found.\n"
            "Run: python -m pyaudiowpatch\n"
            "to inspect available devices."
        )

    print("\nAvailable WASAPI loopback devices:")

    for device in loopbacks:
        print(
            f"  {device['index']}: {device['name']} | "
            f"{device['maxInputChannels']} ch | "
            f"{int(device['defaultSampleRate'])} Hz"
        )

    preferred = next(
        (
            device
            for device in loopbacks
            if PREFERRED_DEVICE_NAME.lower()
            in device["name"].lower()
        ),
        None
    )

    if preferred is not None:
        return preferred

    print(
        f"\n[WARNING] Could not find '{PREFERRED_DEVICE_NAME}' "
        "by name. Using the default WASAPI loopback."
    )

    return p.get_default_wasapi_loopback()


# ============================================================
# AUDIO CAPTURE THREAD
# ============================================================

def capture_audio():
    """
    Continuously read the Poly BT700 WASAPI loopback.

    Whisper inference runs in a DIFFERENT thread, so transcription
    cannot stop Windows audio capture.
    """
    global capture_error

    try:
        with pyaudio.PyAudio() as p:
            device = choose_loopback_device(p)

            device_index = int(device["index"])
            native_rate = int(device["defaultSampleRate"])
            channels = int(device["maxInputChannels"])

            if channels < 1:
                raise RuntimeError(
                    "Selected loopback device has no input channels."
                )

            print("\nUsing WASAPI loopback:")
            print(f"  {device['name']}")
            print(f"  Device index: {device_index}")
            print(f"  Native sample rate: {native_rate} Hz")
            print(f"  Channels: {channels}")

            # Share actual capture format with worker.
            capture_format["rate"] = native_rate
            capture_format["channels"] = channels
            capture_ready.set()

            with p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=native_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=CAPTURE_BLOCK_FRAMES
            ) as stream:

                print("\nAudio capture started.")

                while not stop_event.is_set():

                    data = stream.read(
                        CAPTURE_BLOCK_FRAMES,
                        exception_on_overflow=False
                    )

                    if not data:
                        continue

                    # Never allow Whisper to block the capture thread.
                    try:
                        audio_queue.put_nowait(data)

                    except queue.Full:
                        try:
                            audio_queue.get_nowait()
                        except queue.Empty:
                            pass

                        try:
                            audio_queue.put_nowait(data)
                        except queue.Full:
                            pass

    except Exception as exc:
        capture_error = exc
        capture_ready.set()
        stop_event.set()


# ============================================================
# TRANSCRIPTION WORKER
# ============================================================

capture_format = {
    "rate": None,
    "channels": None
}

capture_ready = threading.Event()


def pcm16_bytes_to_mono_float32(data, channels):
    audio = np.frombuffer(data, dtype=np.int16)

    if channels > 1:
        usable = (len(audio) // channels) * channels
        audio = audio[:usable]
        audio = audio.reshape(-1, channels)
        audio = audio.astype(np.float32).mean(axis=1)
    else:
        audio = audio.astype(np.float32)

    # Convert int16 PCM to approximately [-1, +1].
    audio /= 32768.0

    return np.ascontiguousarray(audio, dtype=np.float32)


def transcription_worker():
    global worker_error

    try:
        if not capture_ready.wait(timeout=10):
            raise RuntimeError(
                "Timed out waiting for the audio capture device."
            )

        if stop_event.is_set():
            return

        native_rate = capture_format["rate"]
        channels = capture_format["channels"]

        target_native_samples = int(
            native_rate * CHUNK_SECONDS
        )

        overlap_native_samples = int(
            native_rate * OVERLAP_SECONDS
        )

        if overlap_native_samples >= target_native_samples:
            raise ValueError(
                "OVERLAP_SECONDS must be smaller than CHUNK_SECONDS."
            )

        raw_buffer = np.empty(0, dtype=np.float32)

        print(
            "\n============================================================\n"
            "LIVE CAPTION V2 IMPROVED\n"
            "============================================================\n"
            f"Inference: {inference_device}\n"
            f"Chunk: {CHUNK_SECONDS:.1f}s\n"
            f"Overlap: {OVERLAP_SECONDS:.1f}s\n"
            "\n"
            "F8 = enable / pause automatic pasting\n"
            "F9 = quit\n"
            "\n"
            "Test with F8 paused first.\n"
            "============================================================\n"
        )

        while not stop_event.is_set():

            try:
                data = audio_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            block = pcm16_bytes_to_mono_float32(
                data,
                channels
            )

            raw_buffer = np.concatenate(
                (raw_buffer, block)
            )

            if len(raw_buffer) < target_native_samples:
                continue

            # Take one continuous native-rate chunk.
            native_chunk = np.ascontiguousarray(
                raw_buffer[:target_native_samples],
                dtype=np.float32
            )

            # Preserve overlap for the next chunk.
            advance = (
                target_native_samples
                - overlap_native_samples
            )

            raw_buffer = raw_buffer[advance:]

            rms = float(
                np.sqrt(np.mean(native_chunk ** 2))
            )

            if rms < SILENCE_THRESHOLD:
                continue

            # IMPORTANT:
            # Resample the entire continuous chunk ONCE.
            if native_rate != WHISPER_RATE:
                whisper_audio = resample_poly(
                    native_chunk,
                    up=WHISPER_RATE,
                    down=native_rate
                )
            else:
                whisper_audio = native_chunk

            whisper_audio = np.ascontiguousarray(
                whisper_audio,
                dtype=np.float32
            )

            start = time.monotonic()

            segments, _ = model.transcribe(
                whisper_audio,
                language="en",
                beam_size=BEAM_SIZE,
                temperature=0.0,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=250
                ),
                condition_on_previous_text=False,
                initial_prompt=INITIAL_PROMPT,
                no_speech_threshold=0.55,
                compression_ratio_threshold=2.4
            )

            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()

            inference_seconds = (
                time.monotonic() - start
            )

            if text:
                emit_text(text)

            backlog_seconds = (
                audio_queue.qsize()
                * CAPTURE_BLOCK_FRAMES
                / native_rate
            )

            if inference_seconds > 1.5 or backlog_seconds > 1.0:
                print(
                    f"[PERF] inference={inference_seconds:.2f}s, "
                    f"audio_backlog={backlog_seconds:.2f}s"
                )

    except Exception as exc:
        worker_error = exc
        stop_event.set()


# ============================================================
# START THREADS
# ============================================================

capture_thread = threading.Thread(
    target=capture_audio,
    name="WASAPICapture",
    daemon=True
)

worker_thread = threading.Thread(
    target=transcription_worker,
    name="WhisperWorker",
    daemon=True
)

capture_thread.start()
worker_thread.start()


# ============================================================
# MAIN LOOP
# ============================================================

try:
    while not stop_event.is_set():
        time.sleep(0.2)

except KeyboardInterrupt:
    print("\nStopped with Ctrl+C.")
    stop_event.set()

finally:
    stop_event.set()

    capture_thread.join(timeout=2.0)
    worker_thread.join(timeout=2.0)

    keyboard.unhook_all_hotkeys()

    if capture_error is not None:
        print("\nAUDIO CAPTURE ERROR:")
        print(capture_error)

    if worker_error is not None:
        print("\nWHISPER ERROR:")
        print(worker_error)

    print("\nFinished.")
