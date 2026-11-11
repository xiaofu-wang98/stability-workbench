@echo off
setlocal
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.lock
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -e . --no-deps
if errorlevel 1 exit /b 1
call npm ci --prefix frontend\v3\univer
if errorlevel 1 exit /b 1
call npm run --prefix frontend\v3\univer build
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe frontend\v3\build.py
