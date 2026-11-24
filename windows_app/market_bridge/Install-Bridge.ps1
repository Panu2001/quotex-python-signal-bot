param([Parameter(Mandatory=$true)][ValidatePattern('^[a-p]{32}$')][string]$ExtensionId)
$ErrorActionPreference = 'Stop'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$homeDir = Join-Path $env:LOCALAPPDATA 'SignalDesk Data\Chrome Bridge'
New-Item -ItemType Directory -Path $homeDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'host.py') -Destination $homeDir -Force
$launcher = Join-Path $homeDir 'host.cmd'
Set-Content -LiteralPath $launcher -Value @('@echo off', ('"{0}" -u "%~dp0host.py"' -f $python)) -Encoding ASCII
$manifest = Join-Path $homeDir 'host.json'
@{
 name = 'com.signaldesk.market_bridge'
 description = 'SignalDesk read-only market bridge'
 path = $launcher
 type = 'stdio'
 allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json | Set-Content -LiteralPath $manifest -Encoding ASCII
$key = 'HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.signaldesk.market_bridge'
New-Item -Path $key -Force | Out-Null
Set-Item -Path $key -Value $manifest
Write-Host 'SignalDesk market bridge registered.'
