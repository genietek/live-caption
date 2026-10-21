"""Application paths and persistent settings.

Settings live in %APPDATA%\\LiveCaption\\settings.json and models in
%LOCALAPPDATA%\\LiveCaption\\models. Both are user-writable, so the app never
needs administrator rights. LIVECAPTION_HOME overrides both (used by tests and
portable setups).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME

log = logging.getLogger(__name__)


def _env_dir(var: str, fallback: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else fallback


def config_dir() -> Path:
    home = os.environ.get("LIVECAPTION_HOME")
    if home:
        return Path(home) / "config"
    if sys.platform == "win32":
        return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_NAME
    return _env_dir("XDG_CONFIG_HOME", Path.home() / ".config") / APP_NAME


def data_dir() -> Path:
    home = os.environ.get("LIVECAPTION_HOME")
    if home:
        return Path(home) / "data"
    if sys.platform == "win32":
        return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / APP_NAME
    return _env_dir("XDG_DATA_HOME", Path.home() / ".local" / "share") / APP_NAME


def default_model_dir() -> Path:
    return data_dir() / "models"


def log_dir() -> Path:
    return data_dir() / "logs"


@dataclass
class Settings:
    # Models
    model_dir: str = ""                 # empty = default_model_dir()
    default_model: str = "small"
    auto_download: bool = False         # never download without consent unless enabled
    imported_models: dict = field(default_factory=dict)   # model id -> folder path

    # Language: "auto", "en" or "es"
    language: str = "auto"
    redetect_seconds: float = 30.0      # re-run language detection this often in auto mode

    # Inference: device "auto" | "cpu" | "cuda"; compute "auto" or a CTranslate2 type
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 1

    # Audio
    audio_source: str = "loopback"      # "loopback" (system audio) or "microphone"
    audio_device: str = ""              # empty = default device; else the device name
    preferred_device_name: str = "Poly BT700"   # used when audio_device is empty and present
    chunk_seconds: float = 3.0
    overlap_seconds: float = 0.5
    silence_threshold: float = 0.0012
    max_backlog_seconds: float = 6.0

    # Captions
    timestamps: bool = False
    history_limit: int = 500            # caption lines kept in the window
    autoscroll: bool = True
    auto_type: bool = False             # type captions into the focused app (opt-in)

    # Shortcuts. Global hotkeys work while other apps have focus; opt-in.
    global_hotkeys: bool = False
    hotkey_start_stop: str = "F9"
    hotkey_auto_type: str = "F8"

    # Startup and window
    auto_start: bool = False
    start_minimized: bool = False
    close_to_tray: bool = False
    start_with_windows: bool = False
    show_metrics: bool = False
    theme: str = "system"               # "system" | "light" | "dark"

    def resolved_model_dir(self) -> Path:
        return Path(self.model_dir) if self.model_dir else default_model_dir()


_CHOICES = {
    "language": {"auto", "en", "es"},
    "device": {"auto", "cpu", "cuda"},
    "audio_source": {"loopback", "microphone"},
    "theme": {"system", "light", "dark"},
}


def _coerce(name: str, value, default):
    """Return value if it has the right type (and allowed value), else default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return default
    if isinstance(default, str):
        if not isinstance(value, str):
            return default
        allowed = _CHOICES.get(name)
        return value if allowed is None or value in allowed else default
    if isinstance(default, dict):
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items() if isinstance(v, str)}
        return default
    return default


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    settings = Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as exc:
        log.warning("Settings file unreadable (%s); using defaults", exc)
        try:
            path.replace(path.with_suffix(".json.bad"))
        except OSError:
            pass
        return settings
    if not isinstance(raw, dict):
        return settings
    for f in dataclasses.fields(Settings):
        if f.name in raw:
            setattr(settings, f.name, _coerce(f.name, raw[f.name], getattr(settings, f.name)))
    if settings.overlap_seconds >= settings.chunk_seconds:
        settings.overlap_seconds = Settings.overlap_seconds
        settings.chunk_seconds = Settings.chunk_seconds
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Write settings atomically so a crash never leaves a half-written file."""
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dataclasses.asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, path)
