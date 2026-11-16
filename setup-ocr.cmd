@echo off
setlocal
cd /d "%~dp0"
python -m venv .venv-ocr
if errorlevel 1 exit /b 1
.venv-ocr\Scripts\python.exe -m pip install -r requirements-ocr.txt
