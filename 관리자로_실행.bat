@echo off
rem Last resort if the hotkey still does not reach us: run at the same
rem privilege level as the game. Triggers a UAC prompt.
powershell -NoProfile -Command "Start-Process -FilePath 'C:\Users\suck7\miniconda3\pythonw.exe' -ArgumentList '%~dp0mabi_voice.pyw' -Verb RunAs"
