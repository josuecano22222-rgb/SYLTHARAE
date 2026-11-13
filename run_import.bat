@echo off
REM =====================================================================
REM  SYLTHARAE - Windows one-click import
REM  Double-click this file every time you want to import Category Keyword Category-word.
REM =====================================================================
title SYLTHARAE - import Category Keyword Category-word
cd /d "%~dp0"


echo.
echo  ============================================================
echo   Starting SYLTHARAE...


echo  ============================================================
echo.

python run_import.py

echo.
echo.
pause
