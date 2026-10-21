"""Model catalog, installed-model detection and validation.

Only multilingual Faster-Whisper (CTranslate2) models are listed. Repository ids
match faster-whisper 1.2.1's own table (faster_whisper/utils.py, _MODELS); its
"turbo" alias points at the same repository as "large-v3-turbo".

Sizes, memory and speed figures here are estimates for display, not benchmarks:
- Parameter counts and the speed ordering come from OpenAI's published Whisper
  model table.
- Download sizes are approximate float16 weight sizes; the downloader replaces
  them with exact sizes from the Hub when online.
- Memory estimates assume int8 weights on CPU (1 byte/param) and float16 on GPU
  (2 bytes/param), times 1.5 for activations and buffers, plus 0.3 GB runtime.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = ".livecaption.json"
PARTIAL_DIR = ".partial"
TRASH_DIR = ".trash"

# Files faster-whisper downloads for a model (download_model allow_patterns).
DOWNLOAD_PATTERNS = ("config.json", "preprocessor_config.json", "model.bin",
                     "tokenizer.json", "vocabulary.*")

# Default location used by the original scripts.
LEGACY_MODEL_ROOTS = (Path(r"C:\live-caption\models"),)


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    repo: str
    params_m: int
    description: str
    speed: str
    accuracy: str
    approx_download_mb: int
    needs_preprocessor_config: bool = False

    @property
    def languages(self) -> str:
        return "English and Spanish (multilingual model)"

    @property
    def est_ram_gb(self) -> float:
        return round(self.params_m * 1e6 * 1 * 1.5 / 1e9 + 0.3, 1)

    @property
    def est_vram_gb(self) -> float:
        return round(self.params_m * 1e6 * 2 * 1.5 / 1e9 + 0.3, 1)


CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(
        "tiny", "Whisper Tiny", "Systran/faster-whisper-tiny", 39,
        "Very small and fast. Runs on almost any computer, but makes noticeably "
        "more mistakes, especially with accents, names and noisy audio.",
        "Fastest", "Basic", 75),
    ModelInfo(
        "base", "Whisper Base", "Systran/faster-whisper-base", 74,
        "Lightweight multilingual model with low memory use. A good choice for "
        "older or low-power CPUs.",
        "Very fast", "Fair", 145),
    ModelInfo(
        "small", "Whisper Small", "Systran/faster-whisper-small", 244,
        "Balanced accuracy, speed and memory for English and Spanish. A good "
        "default for most CPUs.",
        "Fast", "Good", 484),
    ModelInfo(
        "medium", "Whisper Medium", "Systran/faster-whisper-medium", 769,
        "Higher accuracy with clearly higher CPU/GPU use. Comfortable on a GPU; "
        "may fall behind real time on slower CPUs.",
        "Moderate", "High", 1530),
    ModelInfo(
        "large-v3", "Whisper Large V3", "Systran/faster-whisper-large-v3", 1550,
        "Most accurate multilingual model, with much higher memory and compute "
        "needs. Best used with an NVIDIA GPU.",
        "Slow", "Highest", 3090, needs_preprocessor_config=True),
    ModelInfo(
        "large-v3-turbo", "Whisper Large V3 Turbo",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo", 809,
        "Large V3 with a much smaller decoder: far faster than Large V3 while "
        "keeping strong multilingual accuracy. Recommended for NVIDIA GPUs.",
        "Fast", "High", 1620, needs_preprocessor_config=True),
)

BY_ID = {m.id: m for m in CATALOG}
BY_REPO = {m.repo.lower(): m for m in CATALOG}


def get(model_id: str) -> ModelInfo:
    if model_id == "turbo":
        model_id = "large-v3-turbo"
    return BY_ID[model_id]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def required_files(info: ModelInfo) -> list[str]:
    files = ["config.json", "model.bin", "tokenizer.json"]
    if info.needs_preprocessor_config:
        files.append("preprocessor_config.json")
    return files


def read_manifest(path: Path) -> dict | None:
    try:
        data = json.loads((path / MANIFEST_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def validate_model_dir(path: Path, info: ModelInfo) -> tuple[bool, str]:
    """Check that a folder holds a complete CTranslate2 Whisper model.

    A folder existing is not enough: every required file must be present and
    non-empty, config.json must parse, model.bin must be plausibly sized, and
    when a manifest from our downloader exists, file sizes must match it.
    """
    path = Path(path)
    if not path.is_dir():
        return False, "Folder not found"
    for name in required_files(info):
        f = path / name
        if not f.is_file() or f.stat().st_size == 0:
            return False, f"Missing {name}"
    if not any((path / n).is_file() for n in ("vocabulary.txt", "vocabulary.json")):
        return False, "Missing vocabulary file"
    try:
        json.loads((path / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "config.json is unreadable"
    # Weights are at least ~1 byte per parameter even when quantized.
    min_bytes = info.params_m * 1_000_000 * 0.9
    if (path / "model.bin").stat().st_size < min_bytes:
        return False, "model.bin is incomplete"
    manifest = read_manifest(path)
    if manifest:
        if manifest.get("model_id") not in (None, info.id):
            return False, "Folder belongs to a different model"
        for name, meta in (manifest.get("files") or {}).items():
            f = path / name
            size = meta.get("size") if isinstance(meta, dict) else None
            if not f.is_file():
                return False, f"Missing {name}"
            if isinstance(size, int) and f.stat().st_size != size:
                return False, f"{name} has the wrong size"
    return True, "OK"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@dataclass
class InstalledModel:
    info: ModelInfo
    path: Path
    managed: bool          # True = inside our model folder (we may delete it)
    revision: str | None = None


@dataclass
class ScanResult:
    installed: dict[str, InstalledModel] = field(default_factory=dict)
    invalid: dict[str, str] = field(default_factory=dict)    # model id -> reason
    notes: list[str] = field(default_factory=list)


def hf_cache_roots() -> list[Path]:
    roots = []
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    hf_home = os.environ.get("HF_HOME")
    roots.append(Path(hf_home) / "hub" if hf_home else Path.home() / ".cache" / "huggingface" / "hub")
    return roots


def _snapshot_dirs(root: Path, repo: str) -> list[Path]:
    folder = root / ("models--" + repo.replace("/", "--")) / "snapshots"
    try:
        snaps = [p for p in folder.iterdir() if p.is_dir()]
    except OSError:
        return []
    return sorted(snaps, key=lambda p: p.stat().st_mtime, reverse=True)


def find_existing_models(roots: list[Path]) -> tuple[dict[str, Path], list[str]]:
    """Find valid Hugging Face cache snapshots of catalog models under roots.

    Returns (model id -> snapshot path, notes). English-only or distilled
    snapshots are reported in notes but never offered.
    """
    found: dict[str, Path] = {}
    notes: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for info in CATALOG:
            if info.id in found:
                continue
            for snap in _snapshot_dirs(root, info.repo):
                ok, _ = validate_model_dir(snap, info)
                if ok:
                    found[info.id] = snap
                    break
        try:
            for child in root.iterdir():
                name = child.name.lower()
                if name.startswith("models--") and ("distil" in name or name.endswith(".en")):
                    notes.append(
                        f"Skipped {child.name.split('--', 2)[-1]} in {root}: "
                        "English-only, not usable for Spanish.")
        except OSError:
            pass
    return found, notes


def scan(model_dir: Path, imported: dict[str, str] | None = None) -> ScanResult:
    """Detect installed models: managed folders first, then imported paths."""
    result = ScanResult()
    model_dir = Path(model_dir)
    for info in CATALOG:
        managed = model_dir / info.id
        if managed.exists():
            ok, reason = validate_model_dir(managed, info)
            if ok:
                manifest = read_manifest(managed) or {}
                result.installed[info.id] = InstalledModel(
                    info, managed, True, manifest.get("revision"))
                continue
            result.invalid[info.id] = reason
        ext = (imported or {}).get(info.id)
        if ext:
            ok, reason = validate_model_dir(Path(ext), info)
            if ok:
                result.installed[info.id] = InstalledModel(info, Path(ext), False)
                result.invalid.pop(info.id, None)
            else:
                result.invalid.setdefault(info.id, f"Imported folder: {reason}")
    return result


def delete_managed_model(model_dir: Path, model_id: str) -> None:
    """Delete a model folder we manage. Moves it aside first so a failure
    half-way never leaves a folder that looks installed."""
    model_dir = Path(model_dir)
    target = model_dir / model_id
    if not target.is_dir():
        return
    trash = model_dir / TRASH_DIR
    trash.mkdir(exist_ok=True)
    moved = trash / f"{model_id}-{int(time.time() * 1000)}"
    os.replace(target, moved)
    shutil.rmtree(moved, ignore_errors=True)


def cleanup_trash(model_dir: Path) -> None:
    shutil.rmtree(Path(model_dir) / TRASH_DIR, ignore_errors=True)


def recommend(has_cuda_gpu: bool, vram_total_mb: int | None, ram_gb: float,
              logical_cores: int) -> str:
    """Rule-of-thumb recommendation from detected hardware (not a benchmark)."""
    if has_cuda_gpu and vram_total_mb:
        if vram_total_mb >= 4000:
            return "large-v3-turbo"
        if vram_total_mb >= 2000:
            return "small"
    if logical_cores >= 8 and ram_gb >= 8:
        return "small"
    if ram_gb >= 4:
        return "base"
    return "tiny"
