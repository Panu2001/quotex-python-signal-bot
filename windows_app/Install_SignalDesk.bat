@echo off
title SignalDesk - Windows Installer
echo ============================================================
echo   SignalDesk (Quotex Signal Bot) - Windows Desktop Installer
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install_SignalDesk.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Installation encountered an issue. Review the message above.
    pause
)
