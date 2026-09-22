@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title LinkX Workbench - 生产模式本机测试 (waitress)

rem 用途：在家里用 Windows 模拟线上生产模式启动，验证 wsgi.py + waitress 能正常跑起来。
rem 说明：本机走 http，故 LS_COOKIE_SECURE 保持 false（开了会登录掉线）；线上 HTTPS 上线后才置 true。
set "LS_ENV=production"
set "LS_COOKIE_SECURE=false"

cd /d "%~dp0workbench"

rem —— 定位 Python 解释器（与 启动 Demo.bat 一致的策略）——
set "PYTHON_EXE="
set "PYTHON_ARGS="
if defined LINGSHI_PYTHON if exist "%LINGSHI_PYTHON%" set "PYTHON_EXE=%LINGSHI_PYTHON%"
if not defined PYTHON_EXE for /f "delims=" %%I in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE (
    where py >nul 2>&1
    if not errorlevel 1 ( set "PYTHON_EXE=py" & set "PYTHON_ARGS=-3" )
)
if not defined PYTHON_EXE (
    echo [ERROR] 未找到 Python 3，请先安装并勾选 Add Python to PATH。
    pause & exit /b 1
)

rem —— 确保 waitress 已安装 ——
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import waitress" >nul 2>&1
if errorlevel 1 (
    echo [SETUP] 正在安装 waitress ...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install --user waitress==3.0.2
    if errorlevel 1 ( echo [ERROR] waitress 安装失败。 & pause & exit /b 1 )
)

rem —— 会话密钥：未设置则本机临时生成一个（生产线上必须用固定强密钥）——
if not defined LS_SESSION_SECRET (
    for /f "delims=" %%S in ('"%PYTHON_EXE%" %PYTHON_ARGS% -c "import secrets;print(secrets.token_urlsafe(48))"') do set "LS_SESSION_SECRET=%%S"
    echo [INFO] 已为本机测试临时生成 LS_SESSION_SECRET（线上请改为固定值）。
)

echo.
echo 以生产模式 + waitress 启动，监听 0.0.0.0:8770
echo 本机访问： http://127.0.0.1:8770
echo 关闭本窗口即停止。
echo.

"%PYTHON_EXE%" %PYTHON_ARGS% wsgi.py

echo.
echo 服务已停止。
pause
endlocal
