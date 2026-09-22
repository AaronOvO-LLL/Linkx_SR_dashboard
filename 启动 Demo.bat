@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem 本地开发模式：监听 127.0.0.1、允许配置内演示密钥、Cookie 不加 Secure，双击即可用。
set "LS_ENV=development"
title LinkX Workbench Demo

cd /d "%~dp0workbench"

rem Prefer an explicit interpreter, then PATH python, then the Windows py launcher.
rem Never hard-code a Windows user profile path here.
set "PYTHON_EXE="
set "PYTHON_ARGS="

if defined LINGSHI_PYTHON if exist "%LINGSHI_PYTHON%" (
    set "PYTHON_EXE=%LINGSHI_PYTHON%"
)

if not defined PYTHON_EXE (
    for /f "delims=" %%I in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
)

if not defined PYTHON_EXE (
    where py >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_EXE=py"
        set "PYTHON_ARGS=-3"
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] Python 3 was not found.
    echo Install Python 3.11 or newer and enable "Add Python to PATH".
    echo Alternatively, set LINGSHI_PYTHON to the full python.exe path.
    pause
    exit /b 1
)

"%PYTHON_EXE%" %PYTHON_ARGS% -c "import flask, jinja2, qcloud_cos, tencentcloud" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Installing dependencies from requirements.txt ...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed. Run this command in workbench:
        echo "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install --user -r requirements.txt
        pause
        exit /b 1
    )
)

netstat -ano | findstr ":8770 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo Demo is already running. Opening the browser...
    start "" http://127.0.0.1:8770
    exit /b 0
)

echo Starting LinkX Workbench Demo...
echo The browser will open http://127.0.0.1:8770 after startup.
echo Close this window to stop the service.
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% app.py

echo.
echo The service has stopped.
pause
endlocal
