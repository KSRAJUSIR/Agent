@echo off
REM ===========================================================================
REM  College Assistant - one click start script (Windows)
REM
REM  Safe to run as many times as you like:
REM    * creates .venv only if it is missing
REM    * installs requirements only when requirements.txt has changed
REM    * picks the next free port if the usual one is busy
REM
REM  Usage:  start.bat            (uses port 8501, or the next free one)
REM          start.bat 8600       (start on a specific port)
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title College Assistant

set "VENV_DIR=.venv"
set "PY=%VENV_DIR%\Scripts\python.exe"
set "STAMP=%VENV_DIR%\requirements-installed.txt"
set "REQS=requirements.txt"
set "MIN_PORT=8501"
set "MAX_PORT=8599"

echo.
echo  ==============================================
echo    College Assistant
echo  ==============================================
echo.

REM ---------------------------------------------------------------- python ---
set "BASE_PY="
py -3 -c "import sys" >nul 2>&1 && set "BASE_PY=py -3"
if not defined BASE_PY (
    python -c "import sys" >nul 2>&1 && set "BASE_PY=python"
)
if not defined BASE_PY (
    echo  [X] Python was not found on this computer.
    echo      Install Python 3.10 or newer from https://www.python.org/downloads/
    echo      and tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

%BASE_PY% -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo  [X] Python 3.10 or newer is required. You have:
    %BASE_PY% --version
    echo.
    pause
    exit /b 1
)
for /f "usebackq delims=" %%V in (`%BASE_PY% -c "import sys;print(sys.version.split()[0])"`) do set "PYVER=%%V"
echo  [1/5] Python !PYVER! found.

REM ------------------------------------------------------------- app files ---
if not exist "app.py" (
    echo  [X] app.py is missing. Keep start.bat in the same folder as app.py.
    echo.
    pause
    exit /b 1
)
if not exist "%REQS%" (
    echo  [X] requirements.txt is missing. Keep start.bat in the same folder as app.py.
    echo.
    pause
    exit /b 1
)

REM ----------------------------------------------------------------- .venv ---
if exist "%PY%" (
    "%PY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo      The existing .venv is broken, rebuilding it...
        rmdir /s /q "%VENV_DIR%" >nul 2>&1
    )
)
if not exist "%PY%" (
    echo  [2/5] Creating virtual environment in .venv ^(first run only^)...
    %BASE_PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [X] Could not create the virtual environment.
        echo.
        pause
        exit /b 1
    )
) else (
    echo  [2/5] Reusing the existing .venv.
)

REM ---------------------------------------------------------- requirements ---
set "NEED_INSTALL=1"
if exist "%STAMP%" (
    fc /b "%REQS%" "%STAMP%" >nul 2>&1
    if not errorlevel 1 set "NEED_INSTALL=0"
)
if "!NEED_INSTALL!"=="1" (
    echo  [3/5] Installing packages ^(only when requirements.txt changes^)...
    "%PY%" -m pip install --disable-pip-version-check --quiet --upgrade pip
    "%PY%" -m pip install --disable-pip-version-check --quiet -r "%REQS%"
    if errorlevel 1 (
        echo  [X] Package installation failed. Try running this again.
        echo.
        pause
        exit /b 1
    )
    copy /y "%REQS%" "%STAMP%" >nul
    echo      Packages installed.
) else (
    echo  [3/5] All packages already installed - skipping.
)

REM -------------------------------------------------------------------- key ---
if not exist ".env" if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo      Created .env from .env.example - you can paste your Groq key in it.
)
set "HASKEY=0"
if defined GROQ_API_KEY set "HASKEY=1"
if "!HASKEY!"=="0" if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="GROQ_API_KEY" if not "%%B"=="" set "HASKEY=1"
    )
)

REM ------------------------------------------------------------------- port ---
set "PORT=%~1"
if not defined PORT set "PORT=%MIN_PORT%"
if !PORT! LSS 1 set "PORT=%MIN_PORT%"
set /a TRIES=0
:PORTLOOP
"%PY%" -c "import socket,sys;s=socket.socket();r=s.connect_ex(('127.0.0.1',!PORT!));s.close();sys.exit(0 if r==0 else 1)" >nul 2>&1
if errorlevel 1 goto PORTFOUND
set /a TRIES+=1
set /a PORT+=1
if !TRIES! LSS 30 goto PORTLOOP
echo  [X] No free port found between %MIN_PORT% and %MAX_PORT%.
echo.
pause
exit /b 1
:PORTFOUND

REM ------------------------------------------------------------------ start ---
echo  [4/5] Starting the server on port !PORT! ...
echo.
if "!HASKEY!"=="1" (
    echo      GROQ_API_KEY found - the AI agent is enabled.
) else (
    echo      No GROQ_API_KEY yet. The Add Data and View Data tabs still work.
    echo      To enable the AI agent, put your key in the .env file and start again.
)
echo.
echo  ------------------------------------------------------------
echo    Open this URL in your browser:
echo.
echo        http://localhost:!PORT!
echo.
echo  ------------------------------------------------------------
echo    Press Ctrl+C in this window to stop the server.
echo.
echo  [5/5] Server running.
echo.

"%PY%" -m streamlit run app.py --server.port !PORT! --browser.gatherUsageStats false

echo.
echo  The server has stopped. You can close this window,
echo  or run start.bat again whenever you like.
echo.
endlocal
