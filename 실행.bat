@echo off
rem Launch without a console window. Uses the miniconda python explicitly,
rem because this PC has three python installs and plain "python" may pick
rem one that lacks the packages. Comments kept ASCII on purpose: cmd reads
rem .bat files in the OEM codepage and mangled UTF-8 can break parsing.
start "" "C:\Users\suck7\miniconda3\pythonw.exe" "%~dp0mabi_voice.pyw"
