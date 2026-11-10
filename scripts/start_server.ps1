param(
    [switch]$FirstRun,
    [string]$ProfileName = "main",
    [int]$HttpPort = 38083,
    [int]$TcpPort = 46002,
    [string]$ProfileRoot = ""
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 python，请先安装 Python 3.11+ 并加入 PATH" -ForegroundColor Red
    exit 1
}

if ([string]::IsNullOrWhiteSpace($ProfileRoot)) {
    $ProfileRoot = Join-Path $env:LOCALAPPDATA "chang_an_revival_profiles"
}

foreach ($port in $HttpPort, $TcpPort) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "端口 $port 已被占用，请先运行 scripts\stop_server.ps1" -ForegroundColor Red
        exit 1
    }
}

$mode = if ($FirstRun) { "init" } else { "resume" }
Write-Host "存档目录: $ProfileRoot" -ForegroundColor DarkGray
Write-Host "模式: $mode   HTTP $HttpPort   TCP $TcpPort" -ForegroundColor DarkGray
Write-Host "按 Ctrl+C 停止服务端" -ForegroundColor DarkGray
Write-Host ""

& python -m revival_stub `
    --role-mode play_profile `
    --play-profile $ProfileName `
    --profile-mode $mode `
    --profile-root $ProfileRoot `
    --http-port $HttpPort `
    --tcp-port $TcpPort

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "服务端退出，代码 $LASTEXITCODE。常见原因：第一次启动要加 -FirstRun；或端口被占用。" -ForegroundColor Yellow
}
