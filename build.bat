@echo off
echo ======================================
echo   Building SD Card Photo Mover...
echo ======================================

pip install pyinstaller --quiet
pyinstaller SDCardMover.spec --clean --noconfirm

echo.
echo ======================================
echo   Build complete!
echo   Executable: dist\SD Card Photo Mover.exe
echo ======================================
pause
