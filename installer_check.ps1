# Helper for build_app.bat (run with -ExecutionPolicy Bypass). Two commands:
#   hash   <file>                 -> prints the SHA-256 of the file (nothing on error, exit 1)
#   ping   <build_id> [seconds]   -> waits until the RUNNING ZackBot answers /api/ping with this build id AND proves it holds
#                                    the token in session.json (HMAC of a fresh nonce), i.e. it is the process just started
param([string]$cmd, [string]$arg1, [int]$arg2 = 60)
$ErrorActionPreference = 'Stop'
if ($cmd -eq 'hash') {
    try { (Get-FileHash -Algorithm SHA256 -LiteralPath $arg1).Hash; exit 0 } catch { exit 1 }
}
if ($cmd -eq 'ping') {
    $sf = Join-Path $env:LOCALAPPDATA 'ZackBot\session.json'
    for ($i = 0; $i -lt $arg2; $i++) {
        Start-Sleep -Seconds 1
        try {
            $tok = (Get-Content -Raw -LiteralPath $sf | ConvertFrom-Json).token
            $n = [guid]::NewGuid().ToString('N')
            $r = Invoke-RestMethod -TimeoutSec 3 -Uri ("http://127.0.0.1:8765/api/ping?nonce=" + $n)
            $h = New-Object System.Security.Cryptography.HMACSHA256 (, [Text.Encoding]::UTF8.GetBytes($tok))
            $p = -join ($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($n)) | ForEach-Object { $_.ToString('x2') })
            if ($r.app -eq 'zackbot' -and $r.build -eq $arg1 -and $r.proof -eq $p) {
                Write-Output ("ping ok: version " + $r.version + " build " + $r.build); exit 0
            }
            $last = "answered version " + $r.version + " build " + $r.build
        } catch { $last = $_.Exception.Message }
    }
    Write-Output ("ping FAILED after " + $arg2 + " s: " + $last); exit 1
}
Write-Output "usage: installer_check.ps1 hash <file> | ping <build_id> [seconds]"; exit 2
