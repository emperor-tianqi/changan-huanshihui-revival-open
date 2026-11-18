@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   长安幻世绘 复原服务端 - 一键启动
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python。
  echo 请安装 Python 3.11+，安装时勾选 "Add python.exe to PATH"。
  echo 下载: https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
set PROFILE=%LOCALAPPDATA%\chang_an_revival_profiles\main
if exist "%PROFILE%" (
  echo 检测到已有存档，将进入上次的进度。
  set MODE=
) else (
  echo 第一次启动，将创建新存档。
  set MODE=-FirstRun
)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\check_env.ps1"
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_server.ps1" %MODE%
echo.
echo 服务端已退出。如果上面有报错，把窗口内容截图发给 AI。
pause
