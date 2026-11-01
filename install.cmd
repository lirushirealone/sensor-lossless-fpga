@echo off
setlocal
chcp 65001 >nul
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\install.ps1"
set "install_status=%ERRORLEVEL%"
if not "%install_status%"=="0" echo Installation failed. See the message above and installer/README.md.
pause
exit /b %install_status%
