@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY_EXE=.venv\Scripts\python.exe"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python or set up .venv first.
    echo See README-Python.md for instructions.
    pause
    exit /b 1
  )
  set "PY_EXE=python"
)

echo Starting ColorLab (Calman SDR Workbench)...
"%PY_EXE%" -m calman_py %*
if errorlevel 1 pause
