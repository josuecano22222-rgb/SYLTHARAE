@echo off
REM =====================================================================
REM  SYLTHARAE - Windows one-click setup
REM  Run this ONCE after installing Python and PostgreSQL.
REM  It creates the virtual environment, installs dependencies and
REM  prepares the .env configuration file. No prior knowledge required.
REM =====================================================================
title SYLTHARAE - Setup
cd /d "%~dp0"

echo.
echo  ============================================================
echo   SYLTHARAE - First-time setup
echo  ============================================================
echo.

REM ---- 1. Find Python ------------------------------------------------
set "PYCMD="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYCMD=py -3"
) else (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYCMD=python"
    )
)
if "%PYCMD%"=="" (
    echo  [ERROR] Python was not found.
    echo.
    echo  Please install Python 3.11 from https://www.python.org/downloads/
    echo  IMPORTANT: tick the box "Add python.exe to PATH" during install,
    echo  then close this window, open a new one and run setup.bat again.
    echo.
    pause
    exit /b 1
)
echo  [OK] Using Python: %PYCMD%
%PYCMD% --version


REM ---- 3. Install dependencies ---------------------------------------
echo.
set "WHEEL_DIR="
set "REQ_FILE=requirements.txt"
if exist "wheels\" set "WHEEL_DIR=wheels"
if not defined WHEEL_DIR if exist "offline-bundle\wheels\" set "WHEEL_DIR=offline-bundle\wheels"
if exist "requirements.offline.txt" set "REQ_FILE=requirements.offline.txt"
if not exist "requirements.offline.txt" if exist "offline-bundle\requirements.offline.txt" set "REQ_FILE=offline-bundle\requirements.offline.txt"

if defined WHEEL_DIR (
    echo  Installing required packages from the local offline wheelhouse...
) else (
    echo  Installing required packages from PyPI (this takes a few minutes)...
)
call ".venv\Scripts\activate.bat"
if defined WHEEL_DIR (
    python -m pip install --no-index --find-links="%CD%\%WHEEL_DIR%" -r "%REQ_FILE%"
) else (
    python -m pip install --upgrade pip setuptools wheel
    if errorlevel 1 (
        echo  [WARNING] Could not upgrade pip - continuing anyway.
    )
    python -m pip install -r requirements.txt
)
if errorlevel 1 (
    echo.
    echo  [ERROR] Package installation failed.
    if defined WHEEL_DIR (
        echo  The offline wheelhouse is incomplete for this Python version.
        echo  Rebuild it on an internet-connected machine with:
        echo    powershell -ExecutionPolicy Bypass -File scripts\prepare_offline_bundle.ps1
    ) else (
        echo  Check your internet connection and run setup.bat again.
    )
    echo.
    pause
    exit /b 1
)
echo  [OK] All packages installed.

findstr /R /I /C:"^[ ]*libpff-python" "%REQ_FILE%" >nul
if not errorlevel 1 (
    python -c "import pypff" >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] libpff-python installed, but the pypff module cannot be imported.
        echo  Confirm that this bundle matches CPython 3.11 x64 and that the wheel was built successfully.
        pause
        exit /b 1
    )
    echo  [OK] PST support verified (pypff import succeeded).
)

REM ---- 4. Prepare .env configuration ---------------------------------
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo  [OK] Created .env configuration file from the template.
    ) else (
        echo  [WARNING] .env.example not found - skipping .env creation.
    )
) else (
    echo  [OK] .env already exists - leaving it untouched.
)

echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.
echo   NEXT STEPS:
echo.
echo   1. Make sure PostgreSQL is installed and running
echo      (see INSTALL.md Step 2 if you have not done this yet).
echo.
echo   2. Double-click  start.bat  to launch the application.
echo.
echo   3. Open your browser at:  http://127.0.0.1:5000
echo      The first visit shows a Database Setup page - just enter
echo      the PostgreSQL 'postgres' password you chose during install.
echo      The database and all tables are created automatically.
echo.
echo  Full guide for absolute beginners: INSTALL.md
echo.
pause
