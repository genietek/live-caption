import queue
import re
import threading
import time
import warnings

import keyboard
import numpy as np
import pyautogui
import pyperclip
import soundcard as sc
from faster_whisper import WhisperModel
from scipy.signal import resample_poly


# ============================================================
# CONFIGURATION
# ============================================================

# Start with base.en for real-time CPU use.
# If your PC can keep up comfortably, try "small.en" for better accuracy.
MODEL = "base.en"

# Windows output devices commonly run at 48 kHz.
CAPTURE_RATE = 48000

# Whisper expects 16 kHz mono audio.
WHISPER_RATE = 16000

# The recorder thread grabs very small slices continuously.
# This prevents Whisper inference from blocking WASAPI capture.
CAPTURE_SLICE_SECONDS = 0.25

# Whisper sees a longer window for better sentence context.
WINDOW_SECONDS = 5.0

# Keep a little audio from the previous window so words near
# chunk boundaries are less likely to be lost.
OVERLAP_SECONDS = 1.0

# Skip windows that are essentially silent.
SILENCE_THRESHOLD = 0.0015

# WASAPI buffer size. Increasing this can help Bluetooth devices.
BLOCKSIZE = 8192

# Maximum queued capture slices. At 0.25 sec each, 240 slices
# gives about 60 seconds of backlog before old audio is dropped.
AUDIO_QUEUE_MAXSIZE = 240

# Leave None to auto-select the loopback device corresponding
# to the current Windows default output device.
#
# If auto-selection is wrong, set this to the number shown under
# "Available loopback devices", e.g.:
# LOOPBACK_DEVICE_INDEX = 0
LOOPBACK_DEVICE_INDEX = None

# Start with no vocabulary prompt. A strong prompt can cause
# hallucinations such as "JavaScript.js.js.js".
INITIAL_PROMPT = None

# If True, text is pasted with Ctrl+V into the focused text box
# whenever F8 has enabled pasting.
ENABLE_CLIPBOARD_PASTE = True


# ============================================================
# WARNING FILTER
# ============================================================

# SoundCard may emit many "data discontinuity in recording"
# warnings on Windows/Bluetooth. With the new capture thread,
# occasional discontinuities should be much less harmful.
try:
    from soundcard.mediafoundation import SoundcardRuntimeWarning

    warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
except Exception:
    warnings.filterwarnings(
        "ignore",
        message=r".*data discontinuity in recording.*"
    )


# ============================================================
# GLOBAL STATE
# ============================================================

stop_event = threading.Event()
audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)

armed = False
last_emitted_text = ""

capture_exception = None
worker_exception = None


# ============================================================
# HOTKEYS
# ============================================================

def toggle_arm():
    global armed

    armed = not armed

    if armed:
        print(
            "\n[ACTIVE] Recognized speech will be pasted into "
            "the currently focused text box."
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
    """
    Normalize a word only for overlap comparison.
    The original punctuation/casing is preserved in output.
    """
    return re.sub(r"(^\W+|\W+$)", "", word).lower()


def remove_text_overlap(previous_text, current_text, max_words=25):
    """
    Windows overlap means the next Whisper result may begin with
    words already emitted from the previous window.

    Remove the largest exact normalized suffix/prefix overlap.
    """
    if not previous_text or not current_text:
        return current_text.strip()

    previous_words = previous_text.split()
    current_words = current_text.split()

    max_check = min(
        max_words,
        len(previous_words),
        len(current_words)
    )

    previous_norm = [normalize_word(x) for x in previous_words]
    current_norm = [normalize_word(x) for x in current_words]

    for count in range(max_check, 1, -1):
        if previous_norm[-count:] == current_norm[:count]:
            return " ".join(current_words[count:]).strip()

    return current_text.strip()


def looks_like_bad_hallucination(text):
    """
    Reject a few obvious repetitive hallucination patterns.
    This is intentionally conservative.
    """
    if not text:
        return True

    cleaned = text.strip()
    if len(cleaned) < 2:
        return True

    # Repeated ".js.js.js" style hallucination.
    if cleaned.lower().count(".js") >= 5:
        return True

    words = [normalize_word(w) for w in cleaned.split()]
    words = [w for w in words if w]

    if len(words) >= 6:
        unique_ratio = len(set(words)) / len(words)

        # Extremely repetitive output is often a hallucination.
        if unique_ratio < 0.25:
            return True

    return False


def emit_text(text):
    """
    Print and optionally paste a finalized text fragment.
    """
    global last_emitted_text

    text = text.strip()

    if not text:
        return

    text = remove_text_overlap(last_emitted_text, text)

    if not text:
        return

    if looks_like_bad_hallucination(text):
        print(f"[SKIPPED suspicious transcription] {text}")
        return

    print(f"INTERVIEWER: {text}")

    # Keep enough previous text for overlap comparison.
    combined = (last_emitted_text + " " + text).strip()
    last_emitted_text = " ".join(combined.split()[-80:])

    if armed and ENABLE_CLIPBOARD_PASTE:
        try:
            pyperclip.copy(text + " ")
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.03)
        except Exception as exc:
            print(f"[Paste error] {exc}")


# ============================================================
# LOAD WHISPER
# ============================================================

print("Loading Whisper model...")

model = WhisperModel(
    MODEL,
    device="cpu",
    compute_type="int8"
)

print("Model loaded.")


# ============================================================
# CHOOSE WINDOWS LOOPBACK DEVICE
# ============================================================

speaker = sc.default_speaker()

print("\nDefault output device:")
print(f"  {speaker.name}")

loopbacks = [
    device
    for device in sc.all_microphones(include_loopback=True)
    if getattr(device, "isloopback", False)
]

if not loopbacks:
    raise RuntimeError(
        "No Windows loopback devices were found. "
        "Make sure Windows has an active speaker/headset output."
    )

print("\nAvailable loopback devices:")
for index, device in enumerate(loopbacks):
    print(
        f"  {index}: {device.name} "
        f"({device.channels} channel(s))"
    )


if LOOPBACK_DEVICE_INDEX is not None:
    if not 0 <= LOOPBACK_DEVICE_INDEX < len(loopbacks):
        raise RuntimeError(
            f"LOOPBACK_DEVICE_INDEX={LOOPBACK_DEVICE_INDEX} is invalid. "
            f"Choose 0 to {len(loopbacks) - 1}."
        )

    loopback = loopbacks[LOOPBACK_DEVICE_INDEX]

else:
    # First try the underlying Windows device ID.
    loopback = next(
        (
            device
            for device in loopbacks
            if device.id == speaker.id
        ),
        None
    )

    # Then exact device-name match.
    if loopback is None:
        loopback = next(
            (
                device
                for device in loopbacks
                if device.name.lower() == speaker.name.lower()
            ),
            None
        )

    # Last resort.
    if loopback is None:
        loopback = loopbacks[0]


print("\nUsing loopback device:")
print(f"  {loopback.name}")
print(f"  Channels: {loopback.channels}")

print(
    "\n"
    "============================================================\n"
    "CONTROLS\n"
    "============================================================\n"
    "F8 = Start / pause automatic pasting\n"
    "F9 = Quit\n"
    "\n"
    "Recommended test:\n"
    "1. Leave F8 paused at first.\n"
    "2. Play a clear spoken sentence through your headset.\n"
    "3. Confirm that INTERVIEWER: text is accurate.\n"
    "4. Click the ChatGPT prompt box.\n"
    "5. Press F8 to begin pasting recognized text.\n"
    "============================================================\n"
)


# ============================================================
# AUDIO CAPTURE THREAD
# ============================================================

def capture_audio():
    """
    Continuously drain the Windows/WASAPI loopback device.

    Crucially, this thread NEVER waits for Whisper inference.
    """
    global capture_exception

    frames_per_slice = int(
        CAPTURE_RATE * CAPTURE_SLICE_SECONDS
    )

    record_channels = (
        2 if loopback.channels >= 2 else loopback.channels
    )

    try:
        with loopback.recorder(
            samplerate=CAPTURE_RATE,
            channels=record_channels,
            blocksize=BLOCKSIZE
        ) as recorder:

            while not stop_event.is_set():

                audio = recorder.record(
                    numframes=frames_per_slice
                )

                audio = np.asarray(
                    audio,
                    dtype=np.float32
                )

                if audio.size == 0:
                    continue

                # Convert stereo/multichannel capture to mono ASAP.
                if audio.ndim == 2:
                    audio = audio.mean(axis=1)

                audio = np.ascontiguousarray(
                    audio,
                    dtype=np.float32
                )

                # Do not block the capture thread waiting on Whisper.
                try:
                    audio_queue.put_nowait(audio)

                except queue.Full:
                    # If inference falls extremely far behind,
                    # drop the oldest quarter-second rather than
                    # letting WASAPI capture stall.
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass

                    try:
                        audio_queue.put_nowait(audio)
                    except queue.Full:
                        pass

    except Exception as exc:
        capture_exception = exc
        stop_event.set()


# ============================================================
# WHISPER WORKER THREAD
# ============================================================

def transcription_worker():
    """
    Consume continuously-captured audio from the queue, build
    overlapping windows, and transcribe them independently of WASAPI.
    """
    global worker_exception

    # Resampling ratio 48 kHz -> 16 kHz.
    up = WHISPER_RATE
    down = CAPTURE_RATE

    target_window_samples = int(
        WHISPER_RATE * WINDOW_SECONDS
    )

    overlap_samples = int(
        WHISPER_RATE * OVERLAP_SECONDS
    )

    advance_samples = (
        target_window_samples - overlap_samples
    )

    if advance_samples <= 0:
        raise ValueError(
            "OVERLAP_SECONDS must be smaller than WINDOW_SECONDS."
        )

    audio_buffer = np.empty(
        0,
        dtype=np.float32
    )

    try:
        while not stop_event.is_set():

            try:
                captured = audio_queue.get(timeout=0.25)
            except queue.Empty:
                continue

            # Resample this small slice from 48 kHz to 16 kHz.
            whisper_audio = resample_poly(
                captured,
                up=up,
                down=down
            )

            whisper_audio = np.ascontiguousarray(
                whisper_audio,
                dtype=np.float32
            )

            audio_buffer = np.concatenate(
                (audio_buffer, whisper_audio)
            )

            # Process every full analysis window currently available.
            while (
                len(audio_buffer) >= target_window_samples
                and not stop_event.is_set()
            ):
                window = np.ascontiguousarray(
                    audio_buffer[:target_window_samples],
                    dtype=np.float32
                )

                # Advance while keeping overlap for context.
                audio_buffer = audio_buffer[advance_samples:]

                rms = float(
                    np.sqrt(np.mean(window ** 2))
                )

                if rms < SILENCE_THRESHOLD:
                    continue

                segments, info = model.transcribe(
                    window,
                    language="en",
                    beam_size=5,
                    temperature=0.0,
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=300
                    ),
                    condition_on_previous_text=False,
                    initial_prompt=INITIAL_PROMPT,
                    no_speech_threshold=0.6,
                    compression_ratio_threshold=2.4
                )

                text = " ".join(
                    segment.text.strip()
                    for segment in segments
                    if segment.text.strip()
                ).strip()

                if text:
                    emit_text(text)

                # If Whisper is falling behind, display a warning.
                backlog_seconds = (
                    audio_queue.qsize()
                    * CAPTURE_SLICE_SECONDS
                )

                if backlog_seconds >= 5.0:
                    print(
                        f"[INFO] Whisper is about "
                        f"{backlog_seconds:.1f}s behind real time."
                    )

    except Exception as exc:
        worker_exception = exc
        stop_event.set()


# ============================================================
# START THREADS
# ============================================================

capture_thread = threading.Thread(
    target=capture_audio,
    name="AudioCapture",
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

    if capture_exception is not None:
        print("\nAUDIO CAPTURE ERROR:")
        print(capture_exception)

    if worker_exception is not None:
        print("\nWHISPER WORKER ERROR:")
        print(worker_exception)

    print("\nFinished.")
