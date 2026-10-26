@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0;%PYTHONPATH%
set PY=python
if exist "%~dp0.venv\Scripts\python.exe" set PY="%~dp0.venv\Scripts\python.exe"
if "%1"=="--setup" (
  python -m venv --system-site-packages "%~dp0.venv" ^&^& "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-extras.txt"
  exit /b
)
%PY% -m app %*
