@echo off
REM ============================================================
REM  Build versioned DocFlow executable (portable, no admin rights required)
REM  Run ONCE on a PC with Python 3 installed.
REM  Result: dist\DocFlow-<version>.exe
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
  echo Existing environment is invalid. Recreating .venv...
  if exist ".venv" rmdir /s /q ".venv"
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Failed to create venv.
    pause
    exit /b 1
  )
)
set "VENV_PY=%CD%\.venv\Scripts\python.exe"
for /f "delims=" %%V in ('"%VENV_PY%" -c "from docflow import __version__; print(__version__)"') do set "VERSION=%%V"
set "APP_NAME=DocFlow-%VERSION%"

echo.
echo [3/4] Installing dependencies...
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Install failed.
  pause
  exit /b 1
)

echo.
echo [4/4] Building %APP_NAME%.exe...
REM Чистим кэш предыдущей сборки — иначе PyInstaller может переиспользовать
REM старые скомпилированные модули (.pyc / build\) и версия в .exe не обновится.
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "__pycache__" rmdir /s /q "__pycache__"
if exist "docflow\__pycache__" rmdir /s /q "docflow\__pycache__"
"%VENV_PY%" -m PyInstaller --noconfirm --clean DocFlow.spec

echo.
if exist "dist\%APP_NAME%.exe" (
  copy /Y config.example.json dist\config.example.json >nul
  echo ============================================================
  echo  DONE. File: dist\%APP_NAME%.exe
  echo  Copy the dist folder to the assistants' PCs.
  echo  On first run open Settings and set the project root.
  echo ============================================================
) else (
  echo Build failed. See messages above.
)
pause
