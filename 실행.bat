@echo off
rem Launch with the private runtime, no console window.
rem Runs setup.bat first if this is the first time.
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\python\pythonw.exe

if not exist "%ROOT%runtime\.deps-ok" (
  echo First run - setting up. This happens only once.
  call "%ROOT%setup.bat"
  if errorlevel 1 exit /b 1
)
if not exist "%PYW%" (
  echo Runtime is missing. Run setup.bat first.
  pause
  exit /b 1
)
start "" "%PYW%" "%ROOT%mabi_voice.pyw"
