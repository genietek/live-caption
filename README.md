# Live Caption

A Windows desktop app that shows real-time English and Spanish captions for
system audio (anything playing through your speakers or headset) or a
microphone. Speech recognition runs locally with
[Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) on the CPU or an
NVIDIA GPU. No audio leaves the computer.

## Features

- Live captions with pause, clear, copy, save to TXT, timestamps and a history limit
- English, Spanish, or automatic English/Spanish detection (no translation)
- Model library: Whisper Tiny, Base, Small, Medium, Large V3 and Large V3 Turbo,
  with download, cancel/resume, verification, import of existing models, update
  check and delete
- Automatic GPU/CPU selection with a clear fallback when CUDA does not work
- WASAPI loopback capture with device selection, a preferred-device name
  (defaults to "Poly BT700"), and automatic reconnection
- Optional typing of captions into the focused app (off by default)
- Light/dark theme, system tray, optional global shortcuts, start with Windows (opt-in)

## Run from source

Requires Windows 10/11 x64 and Python 3.11 or 3.12.

```bat
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m livecaption
```

For an NVIDIA GPU, install `requirements-gpu.txt` instead (adds the CUDA 12
cuBLAS and cuDNN 9 libraries CTranslate2 needs).

Open **Models**, download a model (Small is a good CPU default; Large V3 Turbo
for a GPU), press **Select**, then **Start captions** on the Dashboard.
Already-downloaded models in `C:\live-caption\models` or the Hugging Face cache
can be added with **Import existing models** without copying them.

## Where files go

| What | Location |
| --- | --- |
| Settings | `%APPDATA%\LiveCaption\settings.json` |
| Models (default, changeable in Settings) | `%LOCALAPPDATA%\LiveCaption\models\<model>\` |
| Logs | `%LOCALAPPDATA%\LiveCaption\logs\` |

No administrator rights are needed. After a model is installed the app works offline.

## Build the Windows app

```bat
packaging\build.bat        :: CPU build  -> dist\LiveCaption\LiveCaption.exe
packaging\build.bat gpu    :: GPU build  -> dist\LiveCaption-GPU\LiveCaption.exe
```

The script installs dependencies into `.venv-build`, runs the tests, builds a
PyInstaller one-folder app (faster startup than one-file, nothing unpacked to
`%TEMP%`), and runs the packaged exe with `--self-test`. Model weights are not
bundled. The GPU build adds the NVIDIA DLLs in a `cuda` folder and is much
larger, so it is a separate build. Ship the whole `dist\LiveCaption` folder
(for example as a zip).

The GitHub Actions workflow `.github/workflows/windows-build.yml` builds and
self-tests the CPU package on every pull request and uploads it as an artifact.

## Tests and measurements

```bat
pip install -r requirements-dev.txt
python -m pytest -q
```

Unit tests mock audio devices, model loading and Hugging Face downloads, so they
run anywhere. To measure real performance on your hardware:

```bat
python -m livecaption --benchmark small speech.wav --device cpu
```

This loads the installed model, runs a 16-bit WAV through the same chunking
and decoding settings as live captioning, and prints load time, per-chunk
inference time, real-time factor, average process CPU and peak RAM. Turn on
**Settings > Show performance metrics** to see live inference time, delay,
queued and dropped audio while captioning.

## Project layout

```
livecaption/
  __main__.py   entry point (--minimized, --self-test, --benchmark)
  config.py     paths and persistent settings
  catalog.py    model catalog, validation, discovery
  downloads.py  model download manager
  hardware.py   CPU/GPU/RAM detection, CUDA checks
  audio.py      devices, WASAPI capture, resampling
  engine.py     model loading and the real-time transcription worker
  winapi.py     typing into other apps, global hotkeys, start with Windows
  ui/           window, pages, theme
packaging/      PyInstaller spec and build script
tests/
```

The original scripts (`live_caption*.py`, `start_live_caption.bat`) are kept
unchanged; the app is based on `live_caption_v2_speed_optimized.py`.
