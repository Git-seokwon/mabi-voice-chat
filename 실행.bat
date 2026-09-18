@echo off
rem cmd 에 파이썬이 여러 개 깔려 있어서 python 만 쓰면 엉뚱한 걸 집는다.
rem 패키지가 들어 있는 miniconda 파이썬을 경로째 못 박는다.
rem pythonw 라서 검은 콘솔 창이 뜨지 않고 배경에서 돈다.
start "" "C:\Users\suck7\miniconda3\pythonw.exe" "%~dp0mabi_voice.pyw"
