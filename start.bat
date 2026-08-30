@echo off
REM Launch WheelHat, creating the virtual environment on first run.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up WheelHat for the first time...
    py -3 -m venv .venv || python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment. Install Python 3.10 or newer
        echo from python.org and try again.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    REM The desktop extra pulls in PySide6, which is what draws the window.
    ".venv\Scripts\python.exe" -m pip install -e ".[desktop]"
    if errorlevel 1 (
        echo.
        echo Installing dependencies failed. See the messages above.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m wheelhat %*
if errorlevel 1 pause
