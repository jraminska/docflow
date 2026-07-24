@echo off
REM Run the program without building an .exe (for testing / development).
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set "PY=py") || (set "PY=python")
if not exist ".venv" (
  %PY% -m venv .venv
  call ".venv\Scripts\activate.bat"
  pip install -r requirements.txt >nul
) else (
  call ".venv\Scripts\activate.bat"
)
python app.py
