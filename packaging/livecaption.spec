# PyInstaller spec for Live Caption (one-folder build).
#   CPU build:  pyinstaller packaging\livecaption.spec
#   GPU build:  set LIVECAPTION_GPU=1, then the same command (needs requirements-gpu.txt installed)
# One-folder starts faster than one-file (nothing is unpacked to %TEMP% on each launch)
# and is less likely to be flagged by antivirus. Model weights are never bundled.
import glob
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
GPU = os.environ.get("LIVECAPTION_GPU") == "1"
ICON = os.path.join(SPECPATH, "livecaption.ico")

binaries = collect_dynamic_libs("ctranslate2")
datas = collect_data_files("faster_whisper")      # Silero VAD model (assets/*.onnx)

if GPU:
    import nvidia
    for base in nvidia.__path__:
        for dll in glob.glob(os.path.join(base, "*", "bin", "*.dll")):
            binaries.append((dll, "cuda"))         # found by hardware.add_cuda_dll_dirs()

excludes = [
    "tkinter", "scipy", "matplotlib", "pandas", "IPython", "pytest", "torch", "hf_xet",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtSql",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
]

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=["pyaudiowpatch"],
    excludes=excludes,
    noarchive=False,
)

# Drop Qt pieces a widgets-only app never loads (software OpenGL, QML, translations).
UNUSED = ("opengl32sw.dll", "Qt6Qml", "Qt6Quick", "Qt6Pdf", "Qt6Network", "Qt6VirtualKeyboard",
          "qtvirtualkeyboard", "qpdf", "qwebp", "qtiff", "qgif", "qjpeg", "qicns", "qwbmp", "qtga")
a.binaries = [b for b in a.binaries if not any(u.lower() in b[0].lower() for u in UNUSED)]
a.datas = [d for d in a.datas if "/translations/" not in d[0].replace("\\", "/")]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LiveCaption",
    icon=ICON if os.path.exists(ICON) else None,
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False,
               name="LiveCaption-GPU" if GPU else "LiveCaption")
