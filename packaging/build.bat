@echo off
REM Build Live Caption for Windows.
REM   packaging\build.bat        CPU build  -> dist\LiveCaption\LiveCaption.exe
REM   packaging\build.bat gpu    GPU build  -> dist\LiveCaption-GPU\LiveCaption.exe
setlocal
cd /d "%~dp0\.."

set "REQ=requirements.txt"
if /i "%1"=="gpu" (
    set "REQ=requirements-gpu.txt"
    set "LIVECAPTION_GPU=1"
)

if not exist ".venv-build\Scripts\python.exe" (
    py -3.11 -m venv .venv-build || python -m venv .venv-build || goto :error
)
set "PY=.venv-build\Scripts\python.exe"

"%PY%" -m pip install --upgrade pip || goto :error
"%PY%" -m pip install -r %REQ% -r requirements-dev.txt || goto :error

echo Running tests...
"%PY%" -m pytest -q || goto :error

"%PY%" -m livecaption --write-icon packaging\livecaption.ico || goto :error
"%PY%" -m PyInstaller --noconfirm --clean packaging\livecaption.spec || goto :error

set "OUT=dist\LiveCaption"
if defined LIVECAPTION_GPU set "OUT=dist\LiveCaption-GPU"

echo Testing the packaged app...
set "LIVECAPTION_HOME=%TEMP%\livecaption-selftest"
start "" /wait "%OUT%\LiveCaption.exe" --self-test
if errorlevel 1 (
    type "%LIVECAPTION_HOME%\data\logs\self-test.txt"
    goto :error
)
type "%LIVECAPTION_HOME%\data\logs\self-test.txt"
echo.
echo Build OK: %OUT%\LiveCaption.exe
exit /b 0

:error
echo Build FAILED.
exit /b 1
