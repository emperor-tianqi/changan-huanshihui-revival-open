param()

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== 长安幻世绘 复原服务端 环境检查 ==" -ForegroundColor Cyan

# Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[FAIL] 找不到 python，请安装 Python 3.11+ 并勾选 Add to PATH" -ForegroundColor Red
} else {
    $ver = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
    $ok = & python -c "import sys; print(1 if sys.version_info >= (3,11) else 0)" 2>$null
    if ($ok -eq "1") {
        Write-Host "[OK] Python $ver"
    } else {
        Write-Host "[FAIL] Python 版本过低：$ver（需要 3.11+）" -ForegroundColor Red
    }
}

# ADB
$adb = Get-Command adb -ErrorAction SilentlyContinue
if (-not $adb) {
    Write-Host "[WARN] 找不到 adb，可稍后安装 platform-tools 并加入 PATH" -ForegroundColor Yellow
} else {
    Write-Host "[OK] adb: $($adb.Source)"
    $devices = & adb devices 2>$null | Select-Object -Skip 1 | Where-Object { $_ -match "\S" }
    if ($devices) {
        Write-Host "[OK] 已连接设备："
        $devices | ForEach-Object { Write-Host "      $_" }
    } else {
        Write-Host "[WARN] 没有检测到已授权设备（打开 USB 调试并允许授权）" -ForegroundColor Yellow
    }
}

# Ports
foreach ($port in 46002, 38083) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $proc = Get-Process -Id $conn[0].OwningProcess -ErrorAction SilentlyContinue
        Write-Host "[WARN] 端口 $port 已被占用：$($proc.ProcessName) (PID $($proc.Id))" -ForegroundColor Yellow
    } else {
        Write-Host "[OK] 端口 $port 空闲"
    }
}

Write-Host ""
Write-Host "下一步：powershell -ExecutionPolicy Bypass -File scripts\start_server.ps1 -FirstRun"
