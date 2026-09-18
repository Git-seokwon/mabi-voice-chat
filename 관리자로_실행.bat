@echo off
rem Last resort if the hotkey still does not reach us: run at the same
rem privilege level as the game. Triggers a UAC prompt.
setlocal
set ROOT=%~dp0
powershell -NoProfile -Command "Start-Process -FilePath '%ROOT%runtime\python\pythonw.exe' -ArgumentList '%ROOT%mabi_voice.pyw' -Verb RunAs"
