@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem One-click pet launcher. Arguments are passed through:
rem   start.bat --no-server
rem   start.bat --panel
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0main.py" %*
) else (
  python "%~dp0main.py" %*
  if errorlevel 1 pause
)
endlocal
