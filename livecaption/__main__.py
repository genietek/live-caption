"""Entry point: python -m livecaption (or LiveCaption.exe).

Options:
  --minimized          start hidden/minimized (used by Start with Windows)
  --self-test          import every runtime dependency, build the window, exit 0/1
  --benchmark MODEL WAV [--device auto|cpu|cuda]
                       measure model load time and transcription speed on a WAV file
  --write-icon PATH    save the app icon as .ico (used by the build script)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys

from . import DISPLAY_NAME, __version__, config


def setup_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        config.log_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            config.log_dir() / "livecaption.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def self_test() -> int:
    """Smoke test for packaged builds: every import the app needs at runtime."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    failures = []
    for mod in ("numpy", "psutil", "huggingface_hub", "ctranslate2", "faster_whisper",
                "onnxruntime", "av", "tokenizers"):
        try:
            __import__(mod)
        except Exception as exc:
            failures.append(f"{mod}: {exc}")
    if sys.platform == "win32":
        try:
            import pyaudiowpatch  # noqa: F401
        except Exception as exc:
            failures.append(f"pyaudiowpatch: {exc}")
    try:
        from faster_whisper.vad import get_vad_model
        get_vad_model()
    except Exception as exc:
        failures.append(f"Silero VAD model: {exc}")
    try:
        from PySide6.QtWidgets import QApplication

        from .ui.controller import Controller
        from .ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        ctl = Controller(config.Settings())
        win = MainWindow(ctl)
        ctl.rescan()
        win.close()
        ctl.shutdown()
        app.processEvents()
    except Exception as exc:
        failures.append(f"UI: {exc}")
    lines = [f"FAIL {f}" for f in failures] + ["SELF-TEST " + ("FAILED" if failures else "OK")]
    if sys.stdout is not None:      # windowed builds have no console
        print("\n".join(lines))
    try:
        config.log_dir().mkdir(parents=True, exist_ok=True)
        (config.log_dir() / "self-test.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 1 if failures else 0


def benchmark(model_id: str, wav: str, device: str) -> int:
    """Feed a WAV file through the same chunking and transcribe() settings as
    live captioning (as fast as possible) and print measured numbers."""
    import time
    import wave

    import numpy as np
    import psutil

    from . import catalog, engine
    from .audio import Resampler, pcm16_to_mono_float32

    s = config.load_settings()
    scan = catalog.scan(s.resolved_model_dir(), s.imported_models)
    if model_id not in scan.installed:
        print(f"{model_id} is not installed in {s.resolved_model_dir()}")
        return 1
    proc = psutil.Process()
    loaded = engine.load_model(model_id, scan.installed[model_id].path, device, s.compute_type)
    print(f"model={model_id} device={loaded.device} compute={loaded.compute_type} "
          f"load_seconds={loaded.load_seconds:.2f} {loaded.note}")
    with wave.open(wav, "rb") as w:
        if w.getsampwidth() != 2:
            print("WAV must be 16-bit PCM")
            return 1
        rate, ch = w.getframerate(), w.getnchannels()
        audio = pcm16_to_mono_float32(w.readframes(w.getnframes()), ch)
    res = Resampler(rate)
    n, adv = int(rate * s.chunk_seconds), int(rate * (s.chunk_seconds - s.overlap_seconds))
    lang = None if s.language == "auto" else s.language
    times, peak = [], proc.memory_info().rss
    proc.cpu_percent(None)
    t_all = time.monotonic()
    for start in range(0, max(1, len(audio) - n + 1), adv):
        chunk = res(audio[start:start + n])
        t0 = time.monotonic()
        if lang is None:
            lang = engine.pick_language(loaded.model.detect_language(chunk)[2])[0] or "en"
        segs, _ = loaded.model.transcribe(chunk, language=lang, beam_size=s.beam_size, temperature=0.0,
                                          vad_filter=True, vad_parameters=dict(min_silence_duration_ms=150),
                                          condition_on_previous_text=False)
        text = " ".join(x.text.strip() for x in segs)
        times.append(time.monotonic() - t0)
        peak = max(peak, proc.memory_info().rss)
        print(f"chunk {len(times):3d}: {times[-1] * 1000:7.0f} ms  {text[:70]}")
    wall = time.monotonic() - t_all
    if times:
        arr = np.array(times)
        print(f"chunks={len(arr)} chunk_seconds={s.chunk_seconds} mean_ms={arr.mean() * 1000:.0f} "
              f"p95_ms={np.percentile(arr, 95) * 1000:.0f} max_ms={arr.max() * 1000:.0f} "
              f"realtime_factor={arr.mean() / s.chunk_seconds:.3f}")
    print(f"process_cpu_percent_avg={proc.cpu_percent(None) / (psutil.cpu_count() or 1):.0f} "
          f"peak_rss_mb={peak / 1024 ** 2:.0f} wall_seconds={wall:.1f}")
    return 0


def run_app(minimized: bool) -> int:
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .ui import theme
    from .ui.controller import Controller
    from .ui.main_window import MainWindow

    if sys.platform == "win32":
        import ctypes
        try:  # own taskbar icon instead of python.exe's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiveCaption.App")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(theme.app_icon())
    app.setQuitOnLastWindowClosed(False)

    config.config_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config.config_dir() / "instance.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, DISPLAY_NAME, f"{DISPLAY_NAME} is already running "
                                "(check the system tray).")
        return 0

    settings = config.load_settings()
    theme.apply(app, settings.theme)
    ctl = Controller(settings)
    win = MainWindow(ctl)
    ctl.start_up()
    if minimized or settings.start_minimized:
        win.start_minimized()
    else:
        win.show()
    if settings.auto_start:
        QTimer.singleShot(300, ctl.start)
    code = app.exec()
    lock.unlock()
    return code


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="LiveCaption", description=f"{DISPLAY_NAME} {__version__}")
    p.add_argument("--minimized", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--benchmark", nargs=2, metavar=("MODEL", "WAV"))
    p.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    p.add_argument("--write-icon", metavar="PATH")
    args = p.parse_args(argv)
    if args.write_icon:
        from PySide6.QtWidgets import QApplication

        from .ui import theme
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        _app = QApplication.instance() or QApplication([])
        return 0 if theme.write_ico(args.write_icon) else 1
    setup_logging()
    if args.self_test:
        return self_test()
    if args.benchmark:
        return benchmark(args.benchmark[0], args.benchmark[1], args.device)
    return run_app(args.minimized)


if __name__ == "__main__":
    sys.exit(main())
