<#
.SYNOPSIS
    Start SYLTHARAE offline: bundled PostgreSQL first, then the application.

.DESCRIPTION
    No network access is needed or attempted. The bundled PostgreSQL cluster
    under the data directory is (re)started from its files, its DB_* settings
    are exported for the application, and run_web.py serves the interface on
    http://127.0.0.1:5000 in production mode (Waitress).
#>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\SYLTHARAE",
    [string]$DataDir = "$env:LOCALAPPDATA\SYLTHARAE"
)
$ErrorActionPreference = "Stop"
$py = "$InstallDir\.venv\Scripts\python.exe"
$appDir = "$InstallDir\app"

Write-Host "== Bundled PostgreSQL =="
& $py "$appDir\tools\windows\local_postgres.py" --data-dir "$DataDir\pgdata" --uri | Tee-Object -Variable dbLines
if ($LASTEXITCODE -ne 0) { throw "bundled PostgreSQL failed to start" }

Write-Host "== SYLTHARAE =="
foreach ($line in $dbLines) {
    $k, $v = $line -split '=', 2
    Set-Item -Path "env:$k" -Value $v
}
$env:APP_DATA_DIR = $DataDir
Set-Location $appDir
& $py run_web.py
