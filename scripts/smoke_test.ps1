param(
    [int]$HttpPort = 47083,
    [int]$TcpPort = 47002
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$tempRoot = Join-Path $env:TEMP ("changan_open_smoke_" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    Write-Host "启动临时服务端（$tempRoot）..."
    $proc = Start-Process -FilePath "python" -ArgumentList @(
        "-m", "revival_stub",
        "--role-mode", "play_profile",
        "--play-profile", "smoke",
        "--profile-mode", "init",
        "--profile-root", $tempRoot,
        "--http-port", $HttpPort,
        "--tcp-port", $TcpPort
    ) -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $tempRoot "out.log") -RedirectStandardError (Join-Path $tempRoot "err.log")

    Start-Sleep -Seconds 3

    try {
        $resp = Invoke-WebRequest "http://127.0.0.1:$HttpPort/enter.txt" -UseBasicParsing -TimeoutSec 3
        Write-Host "[OK] HTTP /enter.txt -> $($resp.StatusCode)"
    } catch {
        Write-Host "[FAIL] HTTP 引导失败：$($_.Exception.Message)" -ForegroundColor Red
        Get-Content (Join-Path $tempRoot "err.log") -Tail 20
        exit 1
    }

    Write-Host "运行模拟登录..."
    & python -m revival_stub.sim_client --port $TcpPort
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] 模拟登录失败" -ForegroundColor Red
        exit 1
    }

    Write-Host "[OK] 冒烟测试通过（服务端可启动、HTTP 与 TCP 协议有响应）" -ForegroundColor Green
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
