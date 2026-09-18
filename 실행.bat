@echo off
rem The one file you double-click. No console window.
rem Runs first-time setup if it has not been done yet.
setlocal
set ROOT=%~dp0
set PYW=%ROOT%runtime\python\pythonw.exe

if not exist "%ROOT%runtime\.deps-ok" (
  echo First run - setting up. This happens only once.
  call "%ROOT%scripts\setup.bat"
  if errorlevel 1 exit /b 1
)
if not exist "%PYW%" (
  echo Runtime is missing. Run scripts\setup.bat first.
  pause
  exit /b 1
)
start "" "%PYW%" "%ROOT%src\mabi_voice.pyw"
