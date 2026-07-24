@echo off
REM ============================================================
REM  Build DocFlow.exe (portable, no admin rights required)
REM  Run ONCE on a PC with Python 3 installed.
REM  Result: dist\DocFlow.exe
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo [1/4] Checking Python...
where py >nul 2>nul && (set "PY=py") || (set "PY=python")
%PY% --version
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ from python.org, then retry.
  pause
  exit /b 1
)

echo.
echo [2/4] Creating local environment (.venv)...
%PY% -m venv .venv
if errorlevel 1 (
  echo Failed to create venv.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"

echo.
echo [3/4] Installing dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Install failed.
  pause
  exit /b 1
)

echo.
echo [4/4] Building DocFlow.exe...
REM Чистим кэш предыдущей сборки — иначе PyInstaller может переиспользовать
REM старые скомпилированные модули (.pyc / build\) и версия в .exe не обновится.
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "docflow\__pycache__" rmdir /s /q "docflow\__pycache__"
pyinstaller --noconfirm --clean --onefile --windowed --name DocFlow --collect-submodules openpyxl app.py

echo.
if exist "dist\DocFlow.exe" (
  copy /Y config.example.json dist\config.example.json >nul
  echo ============================================================
  echo  DONE. File: dist\DocFlow.exe
  echo  Copy the dist folder to the assistants' PCs.
  echo  On first run open Settings and set the project root.
  echo ============================================================
) else (
  echo Build failed. See messages above.
)
pause
