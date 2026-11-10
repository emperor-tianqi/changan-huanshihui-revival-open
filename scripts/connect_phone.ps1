param(
    [string]$Serial = "",
    [string]$PackageName = "com.aligames.xs.lingxi",
    [int]$HttpPort = 38083,
    [int]$TcpPort = 46002,
    [switch]$SkipShim
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Adb {
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $local = Join-Path $root "platform-tools\adb.exe"
    if (Test-Path $local) { return $local }
    return $null
}

$adb = Find-Adb
if (-not $adb) {
    Write-Host "找不到 adb。请安装 Android platform-tools 并加入 PATH，或把 platform-tools 放到项目目录。" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Serial)) {
    $lines = & $adb devices 2>$null | Select-Object -Skip 1 | Where-Object { $_ -match "device$" }
    if (-not $lines) {
        Write-Host "没有检测到已授权设备。请检查 USB 调试授权。" -ForegroundColor Red
        exit 1
    }
    $Serial = ($lines[0] -split "\s+")[0]
}

Write-Host "使用设备: $Serial"

& $adb -s $Serial reverse tcp:$TcpPort tcp:$TcpPort
if ($LASTEXITCODE -ne 0) {
    Write-Host "端口反向映射失败（TCP $TcpPort）" -ForegroundColor Red
    exit 1
}
& $adb -s $Serial reverse tcp:$HttpPort tcp:$HttpPort
if ($LASTEXITCODE -ne 0) {
    Write-Host "端口反向映射失败（HTTP $HttpPort）" -ForegroundColor Red
    exit 1
}

if (-not $SkipShim) {
    $shim = Join-Path $root "client_shim\luaScript\sdk\ejoysdk\pkgEjoySdkMgr.lua"
    if (Test-Path $shim) {
        $destDir = "/sdcard/Android/data/$PackageName/files/update/luaScript/sdk/ejoysdk"
        & $adb -s $Serial shell mkdir -p $destDir
        & $adb -s $Serial push $shim "$destDir/pkgEjoySdkMgr.lua"
        if ($LASTEXITCODE -eq 0) {
            Write-Host "已推送 Lua 垫片到 $destDir" -ForegroundColor Green
        } else {
            Write-Host "推送垫片失败；如果你已经手动部署过可以加 -SkipShim" -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "当前反向映射：" -ForegroundColor DarkGray
& $adb -s $Serial reverse --list

Write-Host ""
Write-Host "完成。现在打开游戏，选择“1服-幻世单机”进入。" -ForegroundColor Green
