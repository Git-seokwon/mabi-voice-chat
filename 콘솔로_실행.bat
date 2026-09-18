@echo off
rem Same program with a console, so errors are visible.
setlocal
set ROOT=%~dp0
set PY=%ROOT%runtime\python\python.exe
if not exist "%PY%" (
  echo Runtime is missing. Run setup.bat first.
  pause
  exit /b 1
)
"%PY%" "%ROOT%mabi_voice.pyw"
pause
