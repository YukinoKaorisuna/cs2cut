@echo off
chcp 936 >nul
title CS2 击杀集锦工作台
rem 自定位：本文件所在目录即 recorder/，项目根在其上一级
cd /d "%~dp0"

rem Python 解释器查找顺序：CS2CUT_PY 环境变量，其次项目内 .venv，最后 PATH 里的 python
set "PY=%CS2CUT_PY%"
if not defined PY if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
if not defined PY set "PY=python"
set "APP=%~dp0app.py"
if not exist "%APP%" (
    echo.
    echo [错误] 未找到 app.py：%APP%
    echo        请确认本文件位于项目的 recorder\ 目录内。
    echo.
    pause >nul
    exit /b 1
)

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo ==========================================
echo    CS2 击杀集锦录制工作台
echo ==========================================
echo.
echo   正在启动后端服务，浏览器会自动弹出...
echo   关闭本窗口即停止服务。
echo.
"%PY%" "%APP%"

echo.
echo [服务已停止] 按任意键关闭窗口...
pause >nul
