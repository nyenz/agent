@echo off
rem FanOut installer for Windows. Double-click it. Safe to run again at any time (it also repairs).
setlocal EnableExtensions
cd /d "%~dp0"
title FanOut installer
echo.
echo   FanOut installer
echo   ================
echo   This sets everything up. The first time it can take a few minutes.
echo   You do not need to type anything.
echo.

call :findpy
if defined PY goto havepy

echo   Python 3.10 or newer was not found on this computer. Installing Python 3.12 now...
where winget >nul 2>nul
if errorlevel 1 goto officialpy
winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
call :refreshpath
call :findpy
if defined PY goto havepy

:officialpy
echo   Downloading the official Python installer from python.org...
set "PYEXE=%TEMP%\python-3.12.8-amd64.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe' -OutFile '%PYEXE%'"
if not exist "%PYEXE%" goto nopy
"%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_test=0
call :refreshpath
call :findpy
if defined PY goto havepy

:nopy
echo.
echo   I could not install Python by myself.
echo   Please install it from https://www.python.org/downloads/
echo   Tick "Add python.exe to PATH" on the first screen, then double-click INSTALL.bat again.
echo.
pause
exit /b 1

:havepy
echo   Using Python: %PY%
echo.
"%PY%" bootstrap.py install
set "CODE=%errorlevel%"
if "%CODE%"=="0" exit /b 0
echo.
echo   The install did not finish. Read the last lines above, fix what they say, then double-click INSTALL.bat again.
echo.
pause
exit /b %CODE%

:findpy
set "PY="
call :probe "py" "-3"
if defined PY goto :eof
call :probe "python"
if defined PY goto :eof
call :probe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto :eof
call :probe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY goto :eof
call :probe "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
goto :eof

:probe
"%~1" %~2 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :eof
"%~1" %~2 -c "import sys; open(sys.argv[1], 'w').write(sys.executable)" "%TEMP%\fanout_py.txt" >nul 2>nul
if not exist "%TEMP%\fanout_py.txt" goto :eof
set /p PY=<"%TEMP%\fanout_py.txt"
del "%TEMP%\fanout_py.txt" >nul 2>nul
goto :eof

:refreshpath
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P"
goto :eof
