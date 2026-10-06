# Helper for installer.ps1 (started from build_app.bat; run with -NoProfile -ExecutionPolicy Bypass -File). Uses ONLY .NET and the language - NO
# cmdlets at all (Get-FileHash, Invoke-RestMethod, ConvertFrom-Json, Get-Content): T03 review found Get-FileHash missing when the
# batch file was started from an environment with a foreign PSModulePath, which silently emptied the checksum.
# Commands:
#   hash   <file>                 -> SHA-256 (upper-case hex) of the file; retries 5x for scanner locks; exit 1 + reason on stderr
#   ping   <build_id> [seconds]   -> waits until the RUNNING ZackBot answers /api/ping with this build id AND proves it holds
#                                    the token in session.json (HMAC of a fresh nonce), i.e. it is the process just started
# Test hooks (never set by the installer): ZB_PING_PORT (default 8765).
param([string]$cmd, [string]$arg1, [int]$arg2 = 60)
$ErrorActionPreference = 'Stop'

function Write-Diag([string]$what, $err) {
    $m = if ($err) { $err.Exception.GetType().FullName + ': ' + $err.Exception.Message } else { '' }
    $mp = ([string]$env:PSModulePath).Split(';')[0]
    [Console]::Error.WriteLine("installer_check $what FAILED: $m | PowerShell " + $PSVersionTable.PSVersion + ' ' +
        $PSVersionTable.PSEdition + " | first PSModulePath entry: $mp")
}

function Get-Sha256Hex([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    if (-not [IO.File]::Exists($full)) { throw [IO.FileNotFoundException]::new("file not found: $full") }
    $last = $null
    for ($i = 0; $i -lt 5; $i++) {
        $fs = $null; $sha = $null
        try {
            $fs = [IO.FileStream]::new($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $sha = [Security.Cryptography.SHA256]::Create()
            $bytes = $sha.ComputeHash($fs)
            return ([BitConverter]::ToString($bytes) -replace '-', '').ToUpperInvariant()
        } catch {
            $last = $_; [Threading.Thread]::Sleep(1000)            # antivirus / indexer briefly locking a new exe
        } finally {
            if ($fs) { $fs.Dispose() }; if ($sha) { $sha.Dispose() }
        }
    }
    throw $last
}

function Get-JsonString([string]$json, [string]$key) {
    $m = [regex]::Match($json, '"' + [regex]::Escape($key) + '"\s*:\s*"([^"]*)"')
    if ($m.Success) { return $m.Groups[1].Value } else { return $null }
}

if ($cmd -eq 'hash') {
    try { [Console]::Out.WriteLine((Get-Sha256Hex $arg1)); exit 0 } catch { Write-Diag "hash '$arg1'" $_; exit 1 }
}

if ($cmd -eq 'ping') {
    $port = if ($env:ZB_PING_PORT) { [int]$env:ZB_PING_PORT } else { 8765 }
    $sf = [IO.Path]::Combine($env:LOCALAPPDATA, 'ZackBot', 'session.json')
    $last = 'no answer'
    for ($i = 0; $i -lt $arg2; $i++) {
        [Threading.Thread]::Sleep(1000)
        try {
            $tok = Get-JsonString ([IO.File]::ReadAllText($sf)) 'token'
            if (-not $tok) { throw 'session.json has no token yet' }
            $n = [guid]::NewGuid().ToString('N')
            $req = [Net.HttpWebRequest]::Create("http://127.0.0.1:$port/api/ping?nonce=$n")
            $req.Timeout = 3000; $req.ReadWriteTimeout = 3000; $req.Proxy = $null
            $resp = $req.GetResponse()
            try { $body = [IO.StreamReader]::new($resp.GetResponseStream(), [Text.Encoding]::UTF8).ReadToEnd() }
            finally { $resp.Dispose() }
            $h = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($tok))
            $p = ([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($n))) -replace '-', '').ToLowerInvariant()
            $h.Dispose()
            $app = Get-JsonString $body 'app'; $build = Get-JsonString $body 'build'; $ver = Get-JsonString $body 'version'
            if ($app -eq 'zackbot' -and $build -eq $arg1 -and (Get-JsonString $body 'proof') -eq $p) {
                [Console]::Out.WriteLine("ping ok: version $ver build $build"); exit 0
            }
            $last = "answered version $ver build $build"
        } catch { $last = $_.Exception.Message }
    }
    [Console]::Out.WriteLine("ping FAILED after $arg2 s: $last"); exit 1
}

[Console]::Out.WriteLine('usage: installer_check.ps1 hash <file> | ping <build_id> [seconds]'); exit 2
