@echo off
setlocal
title SignalDesk - Build Standalone Windows EXE
echo ============================================================
echo   Building Standalone SignalDesk.exe (PyInstaller + Inno)
echo ============================================================
echo.

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python 3.10+ is required on your Windows machine to compile the EXE.
    pause
    exit /b 1
)

if not exist ".build_venv" (
    echo [1/4] Creating build virtual environment...
    python -m venv .build_venv
)

call .build_venv\Scripts\activate.bat
echo [2/4] Installing PyInstaller, pywebview, and bot dependencies...
python -m pip install --upgrade pip --quiet
pip install -r python_bot\requirements.txt pyinstaller pywebview --quiet

echo [3/4] Compiling dist\SignalDesk.exe ...
pyinstaller --clean --noconfirm SignalDesk.spec
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Built standalone app: dist\SignalDesk.exe

where iscc >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [4/4] Inno Setup detected! Compiling Output\SignalDesk_Setup.exe ...
    iscc SignalDesk_Setup.iss
    echo [SUCCESS] Windows Setup Wizard ready: Output\SignalDesk_Setup.exe
) else (
    echo [4/4] Optional: Install Inno Setup (https://jrsoftware.org/isinfo.php)
    echo       and double-click SignalDesk_Setup.iss to create SignalDesk_Setup.exe.
)

echo.
pause
