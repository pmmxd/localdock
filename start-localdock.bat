@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH.
  pause
  exit /b 1
)

python app.py
if errorlevel 1 pause
