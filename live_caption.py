import threading

import numpy as np
import soundcard as sc
import pyperclip
import pyautogui
import keyboard
from faster_whisper import WhisperModel


# ============================================================
# CONFIGURATION
# ============================================================

MODEL = "base.en"
# Options:
#   "tiny.en"  = fastest, lowest accuracy
#   "base.en"  = good starting point
#   "small.en" = better accuracy, slower

SAMPLE_RATE = 16000
CHUNK_SECONDS = 3.0
SILENCE_THRESHOLD = 0.002

# Leave as None to automatically use the loopback device that
# corresponds to your current Windows default speaker/headset.
#
# If auto-detection chooses the wrong device, set this to the
# number printed under "Available loopback devices".
# Example: LOOPBACK_DEVICE_INDEX = 0
LOOPBACK_DEVICE_INDEX = None

# Optional hint for technical interview vocabulary.
INITIAL_PROMPT = (
    "React, TypeScript, JavaScript, Next.js, Redux, REST API, GraphQL, "
    "HTML, CSS, Git, GitHub, AWS, Azure, Docker, Kubernetes."
)


# ============================================================
# GLOBAL STATE / HOTKEYS
# ============================================================

armed = False
stop_event = threading.Event()


def toggle_arm():
    global armed
    armed = not armed

    if armed:
        print("\n[ACTIVE] Recognized speech will be pasted into the focused text box.")
    else:
        print("\n[PAUSED] Transcription continues, but nothing will be pasted.")


def quit_program():
    print("\nStopping...")
    stop_event.set()


keyboard.add_hotkey("F8", toggle_arm)
keyboard.add_hotkey("F9", quit_program)


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
# FIND WINDOWS LOOPBACK DEVICE
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
        "Check that Windows has an active speaker/headset output device."
    )

print("\nAvailable loopback devices:")
for i, device in enumerate(loopbacks):
    print(f"  {i}: {device.name} ({device.channels} channel(s))")


if LOOPBACK_DEVICE_INDEX is not None:
    if not 0 <= LOOPBACK_DEVICE_INDEX < len(loopbacks):
        raise RuntimeError(
            f"LOOPBACK_DEVICE_INDEX={LOOPBACK_DEVICE_INDEX} is invalid. "
            f"Choose a number from 0 to {len(loopbacks) - 1}."
        )
    loopback = loopbacks[LOOPBACK_DEVICE_INDEX]
else:
    # On Windows, the speaker and its loopback device normally share
    # the same WASAPI device ID, so this is the most reliable match.
    loopback = next(
        (device for device in loopbacks if device.id == speaker.id),
        None
    )

    # Fallback to matching the displayed device name.
    if loopback is None:
        loopback = next(
            (
                device
                for device in loopbacks
                if device.name.lower() == speaker.name.lower()
            ),
            None
        )

    # Last-resort fallback.
    if loopback is None:
        loopback = loopbacks[0]


print("\nUsing loopback device:")
print(f"  {loopback.name}")
print(f"  Channels: {loopback.channels}")

print(
    "\n"
    "------------------------------------------------------------\n"
    "F8 = Start / pause automatic pasting\n"
    "F9 = Quit\n"
    "\n"
    "Test procedure:\n"
    "1. Play spoken audio through the selected Windows output device.\n"
    "2. Watch this window for INTERVIEWER: ... text.\n"
    "3. Open ChatGPT and click inside the prompt box.\n"
    "4. Press F8 to enable automatic pasting.\n"
    "------------------------------------------------------------\n"
)


# ============================================================
# LIVE TRANSCRIPTION
# ============================================================

frames = int(SAMPLE_RATE * CHUNK_SECONDS)

# SoundCard/WASAPI on Windows requires channels such as 2 or [0, 1].
# Do NOT use channels=[-1].
#
# SoundCard also documents a Windows issue with recording only one
# channel, so use stereo when the loopback device supports it.
record_channels = 2 if loopback.channels >= 2 else loopback.channels

try:
    with loopback.recorder(
        samplerate=SAMPLE_RATE,
        channels=record_channels
    ) as recorder:

        while not stop_event.is_set():
            audio = recorder.record(numframes=frames)
            audio = np.asarray(audio, dtype=np.float32)

            # Convert stereo/multi-channel audio to mono for Whisper.
            if audio.ndim == 2:
                audio = audio.mean(axis=1)

            audio = np.ascontiguousarray(audio, dtype=np.float32)

            if audio.size == 0:
                continue

            # Ignore near-silence.
            rms = float(np.sqrt(np.mean(audio ** 2)))
            if rms < SILENCE_THRESHOLD:
                continue

            segments, _ = model.transcribe(
                audio,
                language="en",
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=INITIAL_PROMPT
            )

            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()

            if not text:
                continue

            print(f"INTERVIEWER: {text}")

            if armed:
                # Clipboard paste is more reliable than simulated
                # character-by-character typing for punctuation.
                pyperclip.copy(text + " ")
                pyautogui.hotkey("ctrl", "v")

except KeyboardInterrupt:
    print("\nStopped with Ctrl+C.")

finally:
    keyboard.unhook_all_hotkeys()
    print("Finished.")
