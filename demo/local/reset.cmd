@echo off
set "BASH_EXE="
where bash.exe >nul 2>nul && set "BASH_EXE=bash.exe"
if not defined BASH_EXE if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH_EXE echo Git Bash is required. Install Git for Windows, then retry. & exit /b 1
"%BASH_EXE%" "%~dp0reset.sh" %*
exit /b %ERRORLEVEL%
