@echo off
echo ======================================
echo   Building EZMovr...
echo ======================================

pip install pyinstaller --quiet
pyinstaller SDCardMover.spec --clean --noconfirm

echo.
echo ======================================
echo   Build complete!
echo   Executable: dist\EZMovr.exe
echo ======================================

if /I "%~1"=="/sign" (
    echo.
    echo   Signing executable for Smart App Control...
    powershell -ExecutionPolicy Bypass -File "%~dp0scripts\sign-dev.ps1"
)

pause