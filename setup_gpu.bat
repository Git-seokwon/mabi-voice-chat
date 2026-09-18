@echo off
rem Optional: GPU acceleration for speech recognition.
rem CTranslate2 needs cuBLAS (cublas64_12.dll). A CUDA Toolkit install
rem provides it, but most people do not have one, so we fetch the pip
rem package into our own runtime instead.
setlocal
set ROOT=%~dp0
set PY=%ROOT%runtime\python\python.exe

if not exist "%PY%" (
  echo Runtime is missing. Run setup.bat first.
  pause
  exit /b 1
)

where nvidia-smi >nul 2>&1
if errorlevel 1 (
  echo No NVIDIA GPU was found on this PC.
  echo GPU acceleration is not possible, so the program will use the CPU.
  echo With a CPU, pick the "small" model in the window.
  pause
  exit /b 0
)

echo Installing GPU support. This downloads about 550 MB.
"%PY%" -m pip install nvidia-cublas-cu12
if errorlevel 1 goto fail

"%PY%" -c "import sys; sys.path.insert(0, r'%ROOT%'); import core; ok, note = core.cuda_ready(); print(note); sys.exit(0 if ok else 1)"
if errorlevel 1 goto fail

echo.
echo Done. Restart the program to use the GPU.
pause
exit /b 0

:fail
echo.
echo FAILED. The program still works on the CPU.
echo In that case pick the "small" model in the window.
pause
exit /b 1
