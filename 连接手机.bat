@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   连接手机（USB 调试 + 端口映射 + 垫片）
echo ============================================
echo.
echo 请确认：
echo   1. 手机已用数据线插好
echo   2. 手机上已允许 USB 调试
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\connect_phone.ps1"
echo.
echo 完成后打开手机游戏，选择 "1服-幻世单机"。
pause
