"""Hardware detection.

detect() is cheap and never creates a CUDA context: it reads the CPU name from
the registry, memory from psutil and GPU details from nvidia-smi. Whether
CTranslate2 can really use the GPU is only checked when a model is loaded
(cuda_runtime_check), and even then the model load itself is the final test.
"""

from __future__ import annotations

import glob
import logging
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class HardwareInfo:
    cpu_name: str = "Unknown CPU"
    logical_cores: int = 1
    ram_total_gb: float = 0.0
    gpu_name: str | None = None
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    driver: str | None = None

    @property
    def has_nvidia_gpu(self) -> bool:
        return self.gpu_name is not None


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Unknown CPU"


def _nvidia_smi() -> tuple[str, int, int, str] | None:
    exe = "nvidia-smi"
    if sys.platform == "win32":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if candidate.exists():
            exe = str(candidate)
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return parse_nvidia_smi(out.stdout)


def parse_nvidia_smi(text: str) -> tuple[str, int, int, str] | None:
    line = text.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        return parts[0], int(float(parts[1])), int(float(parts[2])), parts[3]
    except ValueError:
        return None


def detect() -> HardwareInfo:
    info = HardwareInfo(cpu_name=_cpu_name(), logical_cores=os.cpu_count() or 1)
    try:
        import psutil

        info.ram_total_gb = round(psutil.virtual_memory().total / 1024 ** 3, 1)
    except Exception:
        pass
    smi = _nvidia_smi()
    if smi:
        info.gpu_name, info.vram_total_mb, info.vram_free_mb, info.driver = smi
    return info


def add_cuda_dll_dirs() -> list[str]:
    """Make NVIDIA cuBLAS/cuDNN DLLs from pip wheels (nvidia-*-cu12) or from a
    GPU build's bundled 'cuda' folder findable by CTranslate2 on Windows."""
    if sys.platform != "win32":
        return []
    roots = [Path(sys.executable).parent / "cuda"]
    if hasattr(sys, "_MEIPASS"):
        roots.append(Path(sys._MEIPASS) / "cuda")
    for base in sys.path:
        roots.extend(Path(p) for p in glob.glob(os.path.join(base, "nvidia", "*", "bin")))
    added = []
    for root in roots:
        if root.is_dir() and any(root.glob("*.dll")):
            try:
                os.add_dll_directory(str(root))
            except OSError:
                continue
            os.environ["PATH"] = str(root) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(root))
    return added


def cuda_runtime_check() -> tuple[bool, str, set[str]]:
    """Ask CTranslate2 whether it sees a CUDA device and which compute types it
    supports there. This loads the CUDA runtime, so only call it before loading
    a model on GPU."""
    add_cuda_dll_dirs()
    try:
        import ctranslate2
    except Exception as exc:
        return False, f"CTranslate2 could not be imported: {exc}", set()
    try:
        count = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        return False, f"CUDA is not usable: {exc}", set()
    if count < 1:
        return False, "No CUDA device is visible to CTranslate2 (driver missing or CPU-only build).", set()
    try:
        types = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as exc:
        return False, f"CUDA compute types could not be queried: {exc}", set()
    return True, f"{count} CUDA device(s)", types


def cpu_threads() -> int:
    """Same rule as the original scripts: leave two cores for capture and UI."""
    return max(2, (os.cpu_count() or 6) - 2)
