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
pause
