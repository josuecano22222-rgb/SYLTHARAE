@echo off
REM =====================================================================
REM  SYLTHARAE - Windows one-click start
REM  Double-click this file every time you want to use the application.
REM =====================================================================
title SYLTHARAE - Server
cd /d "%~dp0"


echo.
echo  ============================================================
echo   Starting SYLTHARAE...
echo   Open your browser at:  http://127.0.0.1:5000
echo   Press CTRL+C to stop the server.
echo  ============================================================
echo.

python run_web.py

echo.
echo.
pause
