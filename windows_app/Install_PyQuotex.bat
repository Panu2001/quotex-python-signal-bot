@echo off
title SignalDesk - Install Community pyquotex Adapter
echo ============================================================
echo   SignalDesk - Optional Community pyquotex Installer
echo ============================================================
echo.

set "VENV_PY=%LOCALAPPDATA%\SignalDesk\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo [ERROR] Please run Install_SignalDesk.bat first so SignalDesk is installed.
    pause
    exit /b 1
)

where git >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git is required to install pyquotex from GitHub.
    echo Download Git for Windows: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo [1/2] Installing pyquotex and HTTP dependencies into SignalDesk Python environment...
"%VENV_PY%" -m pip install --upgrade httpx requests websocket-client git+https://github.com/cleitonleonel/pyquotex.git
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install pyquotex. Check your internet connection or GitHub availability.
    pause
    exit /b 1
)

echo [2/2] Installing Playwright Chromium browser runtime (if used by pyquotex)...
"%VENV_PY%" -m playwright install chromium 2>nul

echo.
echo [DONE] pyquotex installed. Restart SignalDesk and use Connection -> Quotex Login.
pause
