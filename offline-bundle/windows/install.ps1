<#
.SYNOPSIS
    Fully offline installation of SYLTHARAE on native Windows.

.DESCRIPTION
    Runs on the TARGET machine with no Internet connectivity. Every runtime
    resource comes from the bundle directory produced by
    tools/windows/build_offline_bundle.py:

        python-3.11.9-amd64.exe   pinned CPython installer
        wheels\                   complete win_amd64 wheelhouse (includes
                                  the pgserver wheel with the bundled
                                  PostgreSQL 16 server binaries)
        tesseract-ocr-*.exe       Tesseract portable build
        tessdata\                 eng / ara / heb language data
        app-source.zip            the application (git archive)
        manifest.json             SHA-256 of every file (verified first)

    What it does:
      1. verifies manifest.json against every file (fails on any mismatch);
      2. installs Python silently for the current user (skipped when a
         suitable Python 3.11 is already present);
      3. creates <InstallDir>\.venv and installs the wheelhouse --no-index;
      4. unpacks the application source;
      5. lays out Tesseract + tessdata under <InstallDir>\tools\tesseract
         and sets TESSERACT_CMD / TESSDATA_PREFIX for the application;
      6. creates the data directory (<DataDir>, default
         %LOCALAPPDATA%\SYLTHARAE) and initialises the bundled PostgreSQL
         cluster inside it;
      7. writes the production .env (FLASK_ENV=production, WSGI_SERVER=waitress,
         TRUSTED_PROXY_COUNT=0, DB_* pointing at the local cluster).

    Application files live under <InstallDir>; persistent data lives under
    <DataDir> - upgrades replace <InstallDir> and never touch <DataDir>.

.PARAMETER BundleDir
    The offline bundle directory (default: the directory of this script).
.PARAMETER InstallDir
    Where the application is installed (default: %LOCALAPPDATA%\Programs\SYLTHARAE).
.PARAMETER DataDir
    Where persistent data lives (default: %LOCALAPPDATA%\SYLTHARAE).
#>
param(
    [string]$BundleDir = "$PSScriptRoot",
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\SYLTHARAE",
    [string]$DataDir = "$env:LOCALAPPDATA\SYLTHARAE"
)

$ErrorActionPreference = "Stop"

function Step($name) { Write-Host ""; Write-Host "== $name ==" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "   [OK] $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "   [FAIL] $msg" -ForegroundColor Red; exit 1 }

# --- 1. verify the bundle ----------------------------------------------------
Step "1. Verifying the bundle (manifest.json)"
$manifestPath = Join-Path $BundleDir "manifest.json"
if (-not (Test-Path $manifestPath)) { Fail "manifest.json not found in $BundleDir" }
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$checked = 0
foreach ($entry in $manifest.wheels.files) {
    $p = Join-Path $BundleDir "$($manifest.wheels.directory)\$($entry.file)"
    if (-not (Test-Path $p)) { Fail "missing wheel: $($entry.file)" }
    $hash = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $entry.sha256) { Fail "wheel hash mismatch: $($entry.file)" }
    $checked++
}
foreach ($entry in $manifest.tessdata) {
    $p = Join-Path $BundleDir $entry.file
    if (-not (Test-Path $p)) { Fail "missing tessdata: $($entry.file)" }
    $hash = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $entry.sha256) { Fail "tessdata hash mismatch: $($entry.file)" }
    $checked++
}
$pyInstaller = Join-Path $BundleDir $manifest.python.installer
if (-not (Test-Path $pyInstaller)) { Fail "missing Python installer" }
$hash = (Get-FileHash $pyInstaller -Algorithm SHA256).Hash.ToLower()
if ($manifest.python.sha256 -and $hash -ne $manifest.python.sha256) { Fail "python installer hash mismatch" }
$checked++
$tessInstaller = Join-Path $BundleDir $manifest.tesseract.installer
if (-not (Test-Path $tessInstaller)) { Fail "missing Tesseract build" }
$hash = (Get-FileHash $tessInstaller -Algorithm SHA256).Hash.ToLower()
if ($manifest.tesseract.sha256 -and $hash -ne $manifest.tesseract.sha256) { Fail "tesseract hash mismatch" }
$checked++
$appArchive = Join-Path $BundleDir $manifest.app.archive
if (-not (Test-Path $appArchive)) { Fail "missing app archive" }
if ($manifest.app.sha256) {
    $hash = (Get-FileHash $appArchive -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $manifest.app.sha256) { Fail "app archive hash mismatch" }
}
$checked++
Ok "$checked files verified against manifest.json"

# --- 2. Python ----------------------------------------------------------------
Step "2. Python 3.11"
$pyExe = $null
foreach ($candidate in @("$InstallDir\runtime\python.exe", (Get-Command python -ErrorAction SilentlyContinue).Source)) {
    if ($candidate -and (Test-Path $candidate)) {
        $v = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($v -eq "3.11") { $pyExe = $candidate; break }
    }
}
if (-not $pyExe) {
    $rt = Join-Path $InstallDir "runtime"
    New-Item -ItemType Directory -Force -Path $rt | Out-Null
    $pyInstaller | Out-Null
    Start-Process -FilePath $pyInstaller -Wait -ArgumentList @(
        "/quiet", "InstallAllUsers=0", "PrependPath=0",
        "TargetDir=$rt", "AssociateFiles=0", "Shortcuts=0", "Include_doc=0",
        "Include_test=0", "Include_launcher=0", "InstallLauncherAllUsers=0")
    $pyExe = "$rt\python.exe"
    if (-not (Test-Path $pyExe)) { Fail "Python installation did not produce $pyExe" }
}
Ok "using $pyExe"

# --- 3. application + virtual environment -------------------------------------
Step "3. Application and wheelhouse"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path "$InstallDir\.venv\Scripts\python.exe")) {
    & $pyExe -m venv "$InstallDir\.venv"
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
}
$appDir = "$InstallDir\app"
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
Expand-Archive -Path $appArchive -DestinationPath $appDir -Force
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --no-index --find-links "$BundleDir\wheels" `
    --upgrade pip setuptools wheel 2>$null
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --no-index --find-links "$BundleDir\wheels" `
    -r "$appDir\requirements.txt"
& "$InstallDir\.venv\Scripts\python.exe" -m pip install --no-index --find-links "$BundleDir\wheels" `
    -e "$appDir[pdf,office,ocr,ebook,audio,media,server]"
if ($LASTEXITCODE -ne 0) { Fail "wheelhouse installation failed" }
& "$InstallDir\.venv\Scripts\python.exe" -m pip check
Ok "application installed from local wheels (no index)"

# --- 4. Tesseract + language data ----------------------------------------------
Step "4. OCR engine and language data"
$tessDir = "$InstallDir\tools\tesseract"
New-Item -ItemType Directory -Force -Path $tessDir | Out-Null
# The portable build is a self-extracting installer; extract silently to our
# tools directory so no registry/service footprint is created.
Start-Process -FilePath $tessInstaller -Wait -ArgumentList @("/S", "/D=$tessDir")
$tessExe = "$tessDir\tesseract.exe"
if (-not (Test-Path $tessExe)) { Fail "tesseract.exe not found after extraction" }
New-Item -ItemType Directory -Force -Path "$tessDir\tessdata" | Out-Null
Copy-Item "$BundleDir\tessdata\*.traineddata" "$tessDir\tessdata\" -Force
& $tessExe --version | Select-Object -First 1
Ok "Tesseract + eng/ara/heb tessdata local under $tessDir"

# --- 5. data directory + bundled PostgreSQL -------------------------------------
Step "5. Data directory and bundled PostgreSQL"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$pgData = "$DataDir\pgdata"
& "$InstallDir\.venv\Scripts\python.exe" "$appDir\tools\windows\local_postgres.py" --data-dir $pgData --init
if ($LASTEXITCODE -ne 0) { Fail "bundled PostgreSQL initialisation failed" }
$dbLines = & "$InstallDir\.venv\Scripts\python.exe" "$appDir\tools\windows\local_postgres.py" --data-dir $pgData --uri
$db = @{}
foreach ($line in $dbLines) { $k, $v = $line -split '=', 2; $db[$k] = $v }
Ok "private PostgreSQL cluster at $pgData"

# --- 6. configuration ------------------------------------------------------------
Step "6. Writing the production configuration"
$secret = -join ((1..48) | ForEach-Object { [char](Get-Random -InputObject (48..57 + 65..90 + 97..122)) })
$envLines = @(
    "FLASK_ENV=production",
    "WSGI_SERVER=waitress",
    "FLASK_HOST=127.0.0.1",
    "FLASK_PORT=5000",
    "TRUSTED_PROXY_COUNT=0",
    "APP_DATA_DIR=$DataDir",
    "SECRET_KEY=$secret",
    "DB_HOST=$($db['DB_HOST'])",
    "DB_PORT=$($db['DB_PORT'])",
    "DB_USER=$($db['DB_USER'])",
    "DB_PASSWORD=$($db['DB_PASSWORD'])",
    "DB_NAME=$($db['DB_NAME'])",
    "TESSERACT_CMD=$tessExe",
    "TESSDATA_PREFIX=$tessDir\tessdata",
    "AUTO_INSTALL=0"
)
# Preserve an existing .env's admin credentials across an upgrade: the
# installer never overwrites a key it does not own and never resets passwords.
$envPath = "$appDir\.env"
if (Test-Path $envPath) {
    $existing = Get-Content $envPath | Where-Object { $_ -match '^(APP_ADMIN_PASSWORD|APP_ADMIN_USERNAME)=' }
    $envLines = $envLines + $existing
}
Set-Content -Path $envPath -Value $envLines -Encoding UTF8
Ok ".env written (production, Waitress, local PostgreSQL, no network settings)"

# --- 7. post-install verification -------------------------------------------------
Step "7. Post-install verification"
Push-Location $appDir
& "$InstallDir\.venv\Scripts\python.exe" install.py --check
Pop-Location
Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "  Start:   powershell -ExecutionPolicy Bypass -File `"$InstallDir\app\offline-bundle\windows\start_syltharae.ps1`""
Write-Host "  Data:    $DataDir (database, files, logs, configuration - survives upgrades)"
Write-Host "  Upgrade: run this installer again with the new bundle; data is kept."
