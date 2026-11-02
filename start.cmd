@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd once to install this application's own environment.
  pause
  exit /b 1
)
.venv\Scripts\python.exe frontend\v3\build.py
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe start.py %*
