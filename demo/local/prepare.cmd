@echo off
set "BASH_EXE="
if not defined BASH_EXE if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH_EXE where git.exe >nul 2>nul && for /f "delims=" %%G in ('where git.exe') do if not defined BASH_EXE if exist "%%~dpG..\bin\bash.exe" set "BASH_EXE=%%~dpG..\bin\bash.exe"
if not defined BASH_EXE (
  echo Git Bash is required. Install Git for Windows, then retry.
  exit /b 1
)
"%BASH_EXE%" "%~dp0prepare.sh" %*
exit /b %ERRORLEVEL%
