$ErrorActionPreference = "Stop"

$AppName = "SignalDesk"
$SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallDir = Join-Path $env:LOCALAPPDATA $AppName
$VenvDir = Join-Path $InstallDir ".venv"
$DesktopDir = [Environment]::GetFolderPath("Desktop")
$StartMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) $AppName

Write-Host "[1/5] Checking Python 3 installation..." -ForegroundColor Cyan
$PythonCmd = $null
foreach ($candidate in @("py", "python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonCmd = $candidate
        break
    }
}

if (-not $PythonCmd) {
    Write-Host "Python 3 was not detected on this PC." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = Read-Host "Install Python 3.12 automatically via winget now? (Y/N)"
        if ($answer -match "^[Yy]") {
            winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
            Write-Host "Python installed. Please re-run Install_SignalDesk.bat once PATH refreshes." -ForegroundColor Green
            exit 0
        }
    }
    throw "Please install Python 3.10+ from https://www.python.org/downloads/ (check 'Add Python to PATH') and run Install_SignalDesk.bat again."
}

Write-Host "[2/5] Stopping any running SignalDesk instance and installing files to $InstallDir ..." -ForegroundColor Cyan
foreach ($p in 8080..8089) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$p/api/shutdown" -Method POST -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
    } catch {}
}
try {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*SignalDesk*desktop_app.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {}
Start-Sleep -Milliseconds 500

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$TargetPy = Join-Path $InstallDir "python_bot"
$TargetUi = Join-Path $InstallDir "ui_dist"
$SharedRoot = Join-Path $env:LOCALAPPDATA "SignalDesk Data"
$SharedData = Join-Path $SharedRoot "paper_lab"
$LegacyData = Join-Path $InstallDir "data\paper_lab"
if (!(Test-Path -LiteralPath $SharedData) -and (Test-Path -LiteralPath (Join-Path $LegacyData "paper_lab.sqlite3"))) {
    New-Item -ItemType Directory -Force -Path $SharedRoot | Out-Null
    Copy-Item -LiteralPath $LegacyData -Destination $SharedData -Recurse -ErrorAction Stop
}
$SourcePy = Join-Path $SourceRoot "python_bot"
$SavedConfig = Join-Path $TargetPy "paper_lab_config.json"
if (Test-Path -LiteralPath $SavedConfig) {
    New-Item -ItemType Directory -Force -Path $SharedRoot | Out-Null
    $SharedConfig = Join-Path $SharedRoot "paper_lab_config.json"
    if (!(Test-Path -LiteralPath $SharedConfig)) { Copy-Item -LiteralPath $SavedConfig -Destination $SharedConfig }
}
New-Item -ItemType Directory -Force -Path $TargetPy | Out-Null
Get-ChildItem -LiteralPath $SourcePy -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $TargetPy -Recurse -Force
}
if (Test-Path (Join-Path $SourceRoot "ui_dist")) {
    if (Test-Path $TargetUi) { Remove-Item -LiteralPath $TargetUi -Recurse -Force }
    Copy-Item -Path (Join-Path $SourceRoot "ui_dist") -Destination $TargetUi -Recurse -Force
}
if (Test-Path (Join-Path $SourceRoot "signaldesk.ico")) {
    Copy-Item -Path (Join-Path $SourceRoot "signaldesk.ico") -Destination $InstallDir -Force
}
if (Test-Path (Join-Path $SourceRoot "Install_PyQuotex.bat")) {
    Copy-Item -Path (Join-Path $SourceRoot "Install_PyQuotex.bat") -Destination $InstallDir -Force
}
$PackageScripts = Join-Path $InstallDir "windows_app"
New-Item -ItemType Directory -Force -Path $PackageScripts | Out-Null
Get-ChildItem -LiteralPath $SourceRoot -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $PackageScripts -Force
}

Write-Host "[3/5] Setting up local Python environment..." -ForegroundColor Cyan
if (-not (Test-Path $VenvDir)) {
    if ($PythonCmd -eq "py") {
        & py -3 -m venv $VenvDir
    } else {
        & $PythonCmd -m venv $VenvDir
    }
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPythonW = Join-Path $VenvDir "Scripts\pythonw.exe"
$ReqFile = Join-Path $InstallDir "python_bot\requirements.txt"
& $VenvPython -m pip install --upgrade pip --quiet
if (Test-Path $ReqFile) {
    Write-Host "Installing quantitative trading engine dependencies (pandas, pandas-ta, pyquotex)..." -ForegroundColor Cyan
    & $VenvPython -m pip install -r $ReqFile --quiet
    if (Get-Command git -ErrorAction SilentlyContinue) {
        try { & $VenvPython -m pip install --upgrade git+https://github.com/cleitonleonel/pyquotex.git --quiet 2>$null } catch {}
    }
    # Ensure MSVC runtime DLLs are linked for llvmlite/numba on Windows
    $pysideDir = Join-Path $VenvDir "Lib\site-packages\PySide6"
    $llvmDir = Join-Path $VenvDir "Lib\site-packages\llvmlite\binding"
    $scriptsDir = Join-Path $VenvDir "Scripts"
    if (Test-Path $pysideDir) {
        Get-ChildItem -Path $pysideDir -Filter "msvcp140*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $scriptsDir -Force -ErrorAction SilentlyContinue
            if (Test-Path $llvmDir) {
                Copy-Item -LiteralPath $_.FullName -Destination $llvmDir -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

Write-Host "[4/5] Creating uninstaller and Windows shortcuts..." -ForegroundColor Cyan
$DesktopScript = Join-Path $InstallDir "python_bot\desktop_app.py"
$LaunchExe = if (Test-Path $VenvPythonW) { $VenvPythonW } else { $VenvPython }

$UninstallBat = Join-Path $InstallDir "Uninstall_SignalDesk.bat"
$UninstallContent = @"
@echo off
title Uninstall SignalDesk
echo Stopping SignalDesk and removing shortcuts and files...
del /f /q "%USERPROFILE%\Desktop\SignalDesk.lnk" 2>nul
rmdir /s /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\SignalDesk" 2>nul
cd /d "%TEMP%"
rmdir /s /q "$InstallDir"
echo SignalDesk has been uninstalled. Stubay data stays in "%LOCALAPPDATA%\SignalDesk Data".
pause
"@
Set-Content -Path $UninstallBat -Value $UninstallContent -Encoding ASCII

$WshShell = New-Object -ComObject WScript.Shell
$IconPath = Join-Path $InstallDir "signaldesk.ico"

# Desktop shortcut (launches pythonw.exe directly - no console window, no VBScript needed)
$DesktopShortcut = $WshShell.CreateShortcut((Join-Path $DesktopDir "SignalDesk.lnk"))
$DesktopShortcut.TargetPath = $LaunchExe
$DesktopShortcut.Arguments = "`"$DesktopScript`""
$DesktopShortcut.WorkingDirectory = $InstallDir
if (Test-Path $IconPath) { $DesktopShortcut.IconLocation = $IconPath }
$DesktopShortcut.Description = "SignalDesk - Quotex Multi-Strategy Signal Bot"
$DesktopShortcut.Save()

# Start Menu folder & shortcuts
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$StartShortcut = $WshShell.CreateShortcut((Join-Path $StartMenuDir "SignalDesk.lnk"))
$StartShortcut.TargetPath = $LaunchExe
$StartShortcut.Arguments = "`"$DesktopScript`""
$StartShortcut.WorkingDirectory = $InstallDir
if (Test-Path $IconPath) { $StartShortcut.IconLocation = $IconPath }
$StartShortcut.Description = "SignalDesk - Quotex Multi-Strategy Signal Bot"
$StartShortcut.Save()

$UninstallShortcut = $WshShell.CreateShortcut((Join-Path $StartMenuDir "Uninstall SignalDesk.lnk"))
$UninstallShortcut.TargetPath = $UninstallBat
$UninstallShortcut.WorkingDirectory = $InstallDir
$UninstallShortcut.Description = "Uninstall SignalDesk"
$UninstallShortcut.Save()

Write-Host "[5/5] Installation complete! Launching SignalDesk..." -ForegroundColor Green
Start-Process -FilePath $LaunchExe -ArgumentList "`"$DesktopScript`"" -WorkingDirectory $InstallDir

