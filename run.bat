@echo off
rem Starts Nyenz Agent. Checks its packages first and repairs them quietly if needed.
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto needinstall
".venv\Scripts\python.exe" bootstrap.py check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0

:needinstall
echo   Nyenz Agent is not installed yet. Starting the installer...
call "%~dp0INSTALL.bat"
exit /b %errorlevel%

:failed
echo.
echo   Nyenz Agent stopped because of a problem.
echo   Double-click INSTALL.bat once to repair it. Your settings and results are kept.
echo   If it keeps happening, see docs\TROUBLESHOOTING.md and attach fanout.log (see the README for where it is) to a bug report.
echo.
pause
exit /b 1
