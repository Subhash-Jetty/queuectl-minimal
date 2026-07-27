@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%run_tests.ps1"
exit /b %ERRORLEVEL%
