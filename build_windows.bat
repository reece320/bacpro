@echo off
REM ===================================================================
REM  bacpro - Windows build script
REM  Produces: dist\bacpro.exe  (single file, no console window)
REM
REM  Requirements: Python 3.10+ on PATH.
REM  Usage: double-click, or run  build_windows.bat  from cmd.
REM ===================================================================

setlocal
cd /d "%~dp0"

echo.
echo === bacpro build ===
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found on PATH. Install Python 3.10+ first.
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment .venv ...
if not exist ".venv" (
    python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat

echo [2/4] Installing dependencies ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail

echo [3/4] Generating icon ...
python tools\make_icon.py || goto :fail

echo [4/4] Running PyInstaller ...
pyinstaller bacpro.spec --noconfirm --clean || goto :fail

echo.
echo === Build complete ===
echo Executable: %CD%\dist\bacpro.exe
echo.
echo Double-click dist\bacpro.exe to launch bacpro.
echo.
pause
exit /b 0

:fail
echo.
echo [BUILD FAILED] see the messages above.
pause
exit /b 1
