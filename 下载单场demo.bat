@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
title FACEIT 单场 demo 下载

rem Python 解释器查找顺序：CS2CUT_PY 环境变量，其次项目内 .venv，最后 PATH 里的 python
set "PY=%CS2CUT_PY%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"
rem 项目根 = 本文件所在目录（%~dp0 末尾带反斜杠，去掉它）
set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"
set "SCRIPT=%REPO%\src\fetch_one_demo.py"
rem 控制台是 GBK(936)，强制 Python 也用 GBK 输出，否则中文乱码
set "PYTHONIOENCODING=gbk:replace"
set "PYTHONUTF8=0"

echo ============================================================
echo            FACEIT 单场 demo 下载 / 诊断
echo ============================================================
echo.
echo  用法：把 FACEIT 比赛页链接粘进来（或直接给 match_id）
echo  例：  https://www.faceit.com/en/cs2/room/1-xxxxxxxx-xxxx-...
echo  直接回车 = 用上次测试的那场做一次 DNS 自检
echo.

set "LINK="
set /p "LINK=粘贴比赛链接/match_id，然后回车: "

if "%LINK%"=="" (
    set "LINK=1-a91d6a0f-390e-4d39-ae0f-0efc622b12f1"
    echo   [未输入，改用示例场次做自检]
)

"%PY%" -c "import sys" 2>nul
if errorlevel 1 (
    echo [X] 找不到可用的 Python: %PY%
    echo     请安装 Python 3.10+ 并加入 PATH，或设置 CS2CUT_PY 指向解释器。
    pause
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo [X] 找不到脚本: %SCRIPT%
    pause
    exit /b 1
)

echo.
echo ------------------------------------------------------------
"%PY%" "%SCRIPT%" --match "%LINK%"
echo ------------------------------------------------------------
echo.
echo  提示：若显示「公网没有 A 记录」，说明是 FACEIT 侧的域名问题，
echo        换代理/换网络都没用，只能等它修复或换其它来源。
echo.
pause
