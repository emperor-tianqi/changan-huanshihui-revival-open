param(
    [int]$HttpPort = 38083,
    [int]$TcpPort = 46002
)

$ErrorActionPreference = "Continue"

$stopped = 0
foreach ($port in $HttpPort, $TcpPort) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -match "python") {
            Write-Host "停止 PID $($proc.Id) ($($proc.ProcessName))，端口 $port" -ForegroundColor Yellow
            Stop-Process -Id $proc.Id -Force
            $stopped++
        } elseif ($proc) {
            Write-Host "端口 $port 被非 Python 进程占用：$($proc.ProcessName) (PID $($proc.Id))，未自动结束" -ForegroundColor Yellow
        }
    }
}

if ($stopped -eq 0) {
    Write-Host "没有发现占用端口的 Python 服务端进程。"
} else {
    Write-Host "已停止 $stopped 个进程。"
}
