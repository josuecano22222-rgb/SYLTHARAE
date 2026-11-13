@echo off
REM =====================================================================
REM  SYLTHARAE - Windows one-click administrator password recovery
REM  Double-click this file when you cannot log in any more.
REM =====================================================================
title SYLTHARAE - Password recovery
cd /d "%~dp0"

set "PYCMD=python"
if exist ".venv\Scripts\python.exe" set "PYCMD=.venv\Scripts\python.exe"
if exist "venv\Scripts\python.exe" set "PYCMD=venv\Scripts\python.exe"
if exist "env\Scripts\python.exe" set "PYCMD=env\Scripts\python.exe"

echo.
echo  ============================================================
echo   SYLTHARAE - Administrator password recovery
echo  ============================================================
echo.
echo  This resets the password of ONE account and leaves every
echo  other account, category and audit entry alone.
echo.

if not "%~1"=="" goto passargs

REM Interactive: show who exists, ask which one, then reset it.
%PYCMD% scripts\reset_admin_password.py --list
if errorlevel 1 goto failed
echo.
set "WHO="
set /p "WHO=Account to reset [admin]: "
if "%WHO%"=="" set "WHO=admin"
echo.
%PYCMD% scripts\reset_admin_password.py --username "%WHO%"
if errorlevel 1 goto failed
goto done

:passargs
%PYCMD% scripts\reset_admin_password.py %*
if errorlevel 1 goto failed

:done
echo.
echo  The temporary password is written in the file printed above.
echo  Read it, log in, set your own password, then delete that file.
echo.
pause
exit /b 0

:failed
echo.
echo  Recovery did not complete - read the message above.
echo.
pause
exit /b 1
