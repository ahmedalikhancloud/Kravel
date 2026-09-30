@echo off
set "BASH_EXE="
if not defined BASH_EXE if exist "%ProgramFiles%\Git\bin\bash.exe" set "BASH_EXE=%ProgramFiles%\Git\bin\bash.exe"
if not defined BASH_EXE if exist "%LocalAppData%\Programs\Git\bin\bash.exe" set "BASH_EXE=%LocalAppData%\Programs\Git\bin\bash.exe"
if not defined BASH_EXE where git.exe >nul 2>nul && for /f "delims=" %%G in ('where git.exe') do if not defined BASH_EXE if exist "%%~dpG..\bin\bash.exe" set "BASH_EXE=%%~dpG..\bin\bash.exe"
if not defined BASH_EXE (
  echo Git Bash is required. Install Git for Windows, then retry.
  exit /b 1
)
"%BASH_EXE%" "%~dp0demo.sh" %*
set "BASH_STATUS=%ERRORLEVEL%"
if not "%BASH_STATUS%"=="0" exit /b %BASH_STATUS%
set "STATE_FILE=%~dp0..\..\.kravel-local-state.env"
set /a STATE_TRIES=0
:wait_for_state
if exist "%STATE_FILE%" goto show_urls
set /a STATE_TRIES+=1
if %STATE_TRIES% GEQ 60 (
  echo Demo started, but its URL file was not ready. Run demo.cmd again to show it.
  exit /b 0
)
timeout /t 1 /nobreak >nul
goto wait_for_state
:show_urls
for /f "usebackq tokens=1,* delims==" %%A in ("%STATE_FILE%") do if "%%A"=="APPROVAL_URL" set "APPROVAL_URL=%%B"
echo.
echo Browser pages:
echo   Kravel:     http://127.0.0.1:8080
echo   Local Slack: %APPROVAL_URL%
echo   Grafana:    http://127.0.0.1:3000
echo   MLflow:     http://127.0.0.1:5000
exit /b 0
