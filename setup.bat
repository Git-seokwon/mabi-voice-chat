@echo off
rem First-run setup: bring in a private Python and install the packages.
rem The official "embeddable" Python has no tkinter, so this uses the
rem python-build-standalone build, which does include it.
setlocal
set ROOT=%~dp0
set RT=%ROOT%runtime
set PY=%RT%\python\python.exe
set URL=https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.11.12+20250409-x86_64-pc-windows-msvc-install_only.tar.gz

echo ============================================
echo   Mabinogi Voice Chat - first run setup
echo ============================================
echo.

echo [1/3] Python runtime
if exist "%PY%" (
  echo       already here, skipping
) else (
  if not exist "%RT%" mkdir "%RT%"
  echo       downloading about 45 MB ...
  curl -L --fail --progress-bar -o "%RT%\python.tar.gz" "%URL%"
  if errorlevel 1 goto fail
  echo       extracting ...
  tar -xzf "%RT%\python.tar.gz" -C "%RT%"
  if errorlevel 1 goto fail
  del "%RT%\python.tar.gz"
)
"%PY%" --version
if errorlevel 1 goto fail
echo.

echo [2/3] packages (this downloads a few hundred MB, please wait)
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install -r "%ROOT%requirements.txt"
if errorlevel 1 goto fail
echo.

echo [3/4] checking
"%PY%" -c "import tkinter, sounddevice, numpy, faster_whisper, pystray, PIL; print('all imports OK')"
if errorlevel 1 goto fail
echo ok> "%RT%\.deps-ok"
echo.

echo [4/4] GPU acceleration (optional)
where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo       No NVIDIA GPU found. The program will use the CPU.
  echo       On a CPU, pick the "small" model in the window.
  goto done
)
echo       An NVIDIA GPU was found.
echo       GPU makes recognition several times faster, but it needs
echo       a 550 MB library that is not installed yet.
choice /C YN /T 30 /D Y /M "Install it now (30s -> yes)"
if errorlevel 2 (
  echo       Skipped. You can run setup_gpu.bat later.
  goto done
)
"%PY%" -m pip install nvidia-cublas-cu12
if errorlevel 1 (
  echo       GPU support failed. The program still works on the CPU.
)

:done
echo.
echo Setup finished. You can run the program now.
exit /b 0

:fail
echo.
echo Setup FAILED. Check your internet connection and try again.
pause
exit /b 1
