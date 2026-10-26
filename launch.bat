@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0;%PYTHONPATH%
python -m app %*
