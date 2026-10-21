@echo off
setlocal

REM ============================================================
REM Live Caption Launcher
REM - Goes to C:\live-caption
REM - Creates venv if it does not exist
REM - Installs Python dependencies on first setup
REM - Runs live_caption_v4_gpu.py
REM - Does NOT require PowerShell activation
REM ============================================================

set "APP_DIR=C:\live-caption"
set "APP_FILE=live_caption_v2_speed_optimized.py"
set "VENV_DIR=%APP_DIR%\venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "SETUP_MARKER=%VENV_DIR%\.live_caption_setup_done"

echo.
echo ==========================================
echo        Live Caption GPU Launcher
echo ==========================================
echo.

REM Go to the live-caption folder.
if not exist "%APP_DIR%" (
    echo ERROR: Folder does not exist:
    echo %APP_DIR%
    echo.
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

REM Make sure the Python app exists.
if not exist "%APP_FILE%" (
    echo ERROR: Python app not found:
    echo %APP_DIR%\%APP_FILE%
    echo.
    pause
    exit /b 1
)

REM Create the virtual environment if needed.
if not exist "%VENV_PYTHON%" (
    echo Creating Python virtual environment...

    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.11 -m venv "%VENV_DIR%"
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo ERROR: Python was not found.
            echo Install Python 3.11 and make sure it is available in PATH.
            echo.
            pause
            exit /b 1
        )

        python -m venv "%VENV_DIR%"
    )

    if not exist "%VENV_PYTHON%" (
        echo ERROR: Failed to create the virtual environment.
        echo.
        pause
        exit /b 1
    )
)

REM Install/update packages only on the first setup.
if not exist "%SETUP_MARKER%" (
    echo.
    echo Installing required Python packages...
    echo This can take several minutes on the first run.
    echo.

    "%VENV_PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :pip_error

    "%VENV_PYTHON%" -m pip install faster-whisper soundcard numpy pyperclip pyautogui keyboard scipy ctranslate2
    if errorlevel 1 goto :pip_error

    echo setup complete>"%SETUP_MARKER%"
)

echo.
echo Starting %APP_FILE%...
echo.
echo F8 = enable/pause automatic pasting
echo F9 = quit
echo.

REM Run directly with the venv Python.
REM No Activate.ps1 is needed, so PowerShell ExecutionPolicy is irrelevant.
"%VENV_PYTHON%" "%APP_DIR%\%APP_FILE%"

set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo Live Caption exited with error code %EXIT_CODE%.
) else (
    echo Live Caption finished.
)

echo.
pause
exit /b %EXIT_CODE%

:pip_error
echo.
echo ERROR: Python package installation failed.
echo Check your internet connection and the error messages above.
echo.
pause
exit /b 1
