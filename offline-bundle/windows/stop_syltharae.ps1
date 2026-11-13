<#
.SYNOPSIS
    Stop the offline deployment: the application first (Ctrl-C or close its
    window stops Waitress cleanly), then the bundled PostgreSQL cluster.
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\SYLTHARAE",
    [string]$DataDir = "$env:LOCALAPPDATA\SYLTHARAE"
)
$ErrorActionPreference = "Continue"
$py = "$InstallDir\.venv\Scripts\python.exe"
$appDir = "$InstallDir\app"

Write-Host "== Stopping the bundled PostgreSQL cluster =="
& $py "$appDir\tools\windows\local_postgres.py" --data-dir "$DataDir\pgdata" --stop
Write-Host "Stopped. (The application itself stops with its console window / Ctrl-C.)"
