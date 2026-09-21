@echo off
rem Uninstaller. The Korean wording lives in uninstall.ps1 -- bat files are
rem read in the OEM codepage and non-ASCII text in them gets mangled.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
