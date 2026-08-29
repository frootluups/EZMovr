@echo off
echo ======================================
echo   Building EZMovr one-click installer...
echo ======================================

set ISCC="%LocalAppData%\Programs\Inno Setup 7\ISCC.exe"
if not exist %ISCC% set ISCC="C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
if not exist %ISCC% set ISCC="C:\Program Files\Inno Setup 7\ISCC.exe"

if not exist %ISCC% (
    echo ISCC.exe not found. Install Inno Setup 7 first.
    pause
    exit /b 1
)

if not exist "%~dp0..\dist\EZMovr.exe" (
    echo dist\EZMovr.exe not found. Run build.bat first.
    pause
    exit /b 1
)

%ISCC% "%~dp0installer.iss"
if errorlevel 1 goto :failed

echo.
echo ======================================
echo   Installer built!
echo   Output: installer\Output\EZMovr-setup.exe
echo ======================================
pause
exit /b 0

:failed
echo.
echo Build failed. See log above.
pause
exit /b 1