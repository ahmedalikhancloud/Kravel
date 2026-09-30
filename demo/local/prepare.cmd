@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare.ps1" %*
exit /b %ERRORLEVEL%
