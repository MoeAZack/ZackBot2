# ZackBot installer (T03b). Started by build_app.bat, which first clears PSModulePath and then runs:
#   powershell -NoProfile -ExecutionPolicy Bypass -File installer.ps1 <mode>
# Modes (same steps, same fail-closed rules and same log markers as the former CMD installer):
#   install     normal build + install. The running ZackBot is not touched until the new build has passed every check;
#               if the new version does not start correctly, the previous one is restored and proven running.
#   drill       rollback drill: install, make the new exe fail its launch on purpose (--simulate-failed-launch), and require
#               the installer to restore the previous version (same SHA-256) and prove it runs again.
#   preflight   non-destructive (tests): own staging folder + log, checksum helper check, then exit.
#   buildcheck  verify full on Windows: steps 1-6 in their own staging folder + log, then exit. Nothing is installed.
# ZB_NOPAUSE=1 (set by verify.py) = never wait for a key press.
# Besides the readable log (build.log / build_preflight.log / build_check.log) every run writes a structured record next to
# it (build.json / build_preflight.json / build_check.json): Cairo timestamps, mode, build ids and hashes, one entry per step,
# rollback result and final verdict. It is rewritten after every step, so even a crash leaves the steps done so far. It
# never contains keys, tokens or settings.
#
# Like installer_check.ps1 this script uses ONLY .NET and the language - NO cmdlets: a module problem in the caller's
# environment (T03: a foreign PSModulePath made Get-FileHash disappear) cannot break it. Every outside effect (processes,
# copies, the checksum/ping helper, stopping the bot, shortcuts, pauses) goes through one small function, so the tests can
# dot-source this file with -NoRun, replace those functions with fakes and run the whole flow on any machine.
param([string]$Mode = 'install', [switch]$NoRun, [switch]$FromLauncher)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:Utf8 = [Text.UTF8Encoding]::new($false)

# ------------------------------------------------------------------ state, log and console
function New-InstallerState([string]$mode, [string]$src, [string]$localAppData) {
    $root = [IO.Path]::Combine($localAppData, 'ZackBot')
    $s = @{
        Mode = $mode; Src = $src; Root = $root
        Dst = [IO.Path]::Combine($root, 'src'); AppDir = [IO.Path]::Combine($root, 'app')
        Stage = [IO.Path]::Combine($root, 'staging'); Venv = [IO.Path]::Combine($root, 'buildenv')
        Log = [IO.Path]::Combine($root, 'build.log')
        Drill = ($mode -eq 'drill'); Preflight = ($mode -eq 'preflight'); BuildCheck = ($mode -eq 'buildcheck')
        BuildId = ''; NewHash = ''; OldHash = ''; OldBuild = ''; HaveOld = $false; Stopped = $false; InRollback = $false
        Warn = ''; RbOk = $false; RbNote = ''; RbHash = ''; RbTried = $false; StopCalled = $false; StopConfirmed = $false; DrillInvalid = $false
        RecTried = $false; RecOk = $false; RecNote = ''; FileHash = ''; FilePresent = $null; TouchesInstall = ($mode -eq 'install' -or $mode -eq 'drill')
        WarnList = [Collections.Generic.List[string]]::new()
        Steps = [Collections.Generic.List[object]]::new(); Current = ''; CurrentAt = ''
        Started = ''; Finished = ''; Verdict = ''; Reason = ''; ExitCode = $null
    }
    if ($s.Preflight) { $s.Stage = [IO.Path]::Combine($root, 'staging_preflight'); $s.Log = [IO.Path]::Combine($root, 'build_preflight.log') }
    if ($s.BuildCheck) { $s.Stage = [IO.Path]::Combine($root, 'staging_buildcheck'); $s.Log = [IO.Path]::Combine($root, 'build_check.log') }
    $s.Json = [IO.Path]::ChangeExtension($s.Log, '.json')
    $s.StageSrc = [IO.Path]::Combine($s.Stage, 'src')
    $s.Exe = [IO.Path]::Combine($s.AppDir, 'ZackBot.exe')
    $s.Prev = [IO.Path]::Combine($s.AppDir, 'ZackBot.prev.exe')
    $s.NewExe = [IO.Path]::Combine($s.Stage, 'dist', 'ZackBot.exe')
    return $s
}

function Write-Log([string]$line) { [IO.File]::AppendAllText($script:S.Log, $line + "`r`n", $script:Utf8) }
function Write-Say([string]$line) { [Console]::Out.WriteLine($line) }
function Test-NoPause { return [bool]$env:ZB_NOPAUSE }

# A failure with the operator-facing reason. Caught once in Invoke-Main.
function Stop-Install([string]$why) {
    $script:FailWhy = $why
    if ($script:S.Current) { Complete-Step $false $why }
    throw 'ZB_INSTALL_FAILED'
}

# ------------------------------------------------------------------ structured record (JSON, no cmdlets)
function Get-CairoTime {
    $utc = [DateTime]::UtcNow
    foreach ($id in @('Egypt Standard Time', 'Africa/Cairo')) {
        try {
            $tz = [TimeZoneInfo]::FindSystemTimeZoneById($id)
            $o = $tz.GetUtcOffset($utc); $sign = '+'; if ($o -lt [TimeSpan]::Zero) { $sign = '-'; $o = $o.Negate() }
            return [TimeZoneInfo]::ConvertTimeFromUtc($utc, $tz).ToString('yyyy-MM-ddTHH:mm:ss') + $sign + $o.ToString('hh\:mm')
        } catch { }
    }
    return $utc.ToString('yyyy-MM-ddTHH:mm:ss') + 'Z'
}

function ConvertTo-JsonText($v, [int]$ind = 0) {
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $pad = '  ' * ($ind + 1); $end = '  ' * $ind
    if ($null -eq $v) { return 'null' }
    if ($v -is [bool]) { if ($v) { return 'true' } else { return 'false' } }
    if ($v -is [int] -or $v -is [long] -or $v -is [double]) { return $v.ToString($inv) }
    if ($v -is [Collections.IDictionary]) {
        if ($v.Count -eq 0) { return '{}' }
        $items = foreach ($k in $v.Keys) { $pad + (ConvertTo-JsonText ([string]$k)) + ': ' + (ConvertTo-JsonText $v[$k] ($ind + 1)) }
        return "{`n" + ($items -join ",`n") + "`n$end}"
    }
    if ($v -isnot [string] -and $v -is [Collections.IEnumerable]) {
        $items = @(foreach ($x in $v) { $pad + (ConvertTo-JsonText $x ($ind + 1)) })
        if ($items.Count -eq 0) { return '[]' }
        return "[`n" + ($items -join ",`n") + "`n$end]"
    }
    $sb = [Text.StringBuilder]::new('"')
    foreach ($ch in ([string]$v).ToCharArray()) {
        switch ([int]$ch) {
            34 { [void]$sb.Append('\"') } 92 { [void]$sb.Append('\\') } 10 { [void]$sb.Append('\n') } 13 { [void]$sb.Append('\r') } 9 { [void]$sb.Append('\t') }
            default { if ([int]$ch -lt 32) { [void]$sb.Append('\u' + ([int]$ch).ToString('x4')) } else { [void]$sb.Append($ch) } }
        }
    }
    return $sb.Append('"').ToString()
}

function Get-Record {
    $s = $script:S
    # installed_after = the build PROVEN running at the end (HMAC ping with its build id); null when nothing was proven.
    # installed_file_present / installed_file_sha256 = the installed exe as it is at the END of the run (re-read right
    # before the verdict; null/false when there is no file), independent of whether it runs.
    $installed = $null
    if ($s.Verdict -eq 'BUILD_DONE') { $installed = [ordered]@{ build = $s.BuildId; sha256 = $s.NewHash } }
    elseif ($s.RbOk) { $installed = [ordered]@{ build = $s.OldBuild; sha256 = $s.RbHash } }
    elseif ($s.RecOk) { $installed = [ordered]@{ build = $s.OldBuild; sha256 = $s.OldHash } }
    $nz = { param($x) if ($x) { $x } else { $null } }
    return [ordered]@{
        schema = 1; tool = 'installer.ps1'; mode = $s.Mode
        started = $s.Started; finished = (& $nz $s.Finished)
        source_dir = $s.Src; log = $s.Log
        build_id = (& $nz $s.BuildId); new_exe_sha256 = (& $nz $s.NewHash)
        previous = [ordered]@{ build = (& $nz $s.OldBuild); sha256 = (& $nz $s.OldHash) }
        bot_stop_attempted = $s.StopCalled; bot_stop_confirmed = $s.StopConfirmed
        installed_after = $installed; installed_file_present = $s.FilePresent; installed_file_sha256 = (& $nz $s.FileHash)
        steps = $s.Steps
        warnings = $s.WarnList
        rollback = [ordered]@{ attempted = $s.RbTried; verified = $s.RbOk; restored_sha256 = (& $nz $s.RbHash); note = (& $nz $s.RbNote) }
        stop_recovery = [ordered]@{ attempted = $s.RecTried; verified = $s.RecOk; note = (& $nz $s.RecNote) }
        verdict = (& $nz $s.Verdict); reason = (& $nz $s.Reason); exit_code = $s.ExitCode
    }
}

function Save-Record {
    try { [IO.File]::WriteAllText($script:S.Json, (ConvertTo-JsonText (Get-Record)) + "`n", $script:Utf8) }
    catch { try { Write-Log ('  structured log not written: ' + $_.Exception.Message) } catch { } }
}

function Start-Step([string]$name) {
    if ($script:S.Current) { Complete-Step $true }
    $script:S.Current = $name; $script:S.CurrentAt = Get-CairoTime
}

function Complete-Step([bool]$ok = $true, [string]$detail = '') {
    $s = $script:S
    if (-not $s.Current) { return }
    $s.Steps.Add([ordered]@{ step = $s.Current; ok = $ok; started = $s.CurrentAt; finished = (Get-CairoTime); detail = $detail })
    $s.Current = ''
    Save-Record
}

function Update-InstalledFileState {
    # Re-read the installed exe (install/drill only): present + its actual hash, or absent. Never throws.
    $s = $script:S
    if (-not $s.TouchesInstall) { return }
    try {
        if (-not [IO.File]::Exists($s.Exe)) { $s.FilePresent = $false; $s.FileHash = ''; return }
        $s.FilePresent = $true; $s.FileHash = Get-FileSha $s.Exe          # '' (-> null) if it cannot be read
    } catch { $s.FileHash = ''; try { Write-Log ('  installed exe state not read: ' + $_.Exception.Message) } catch { } }
}

function Set-Verdict([string]$verdict, [string]$reason, [int]$code) {
    $s = $script:S
    if ($s.Current) { Complete-Step ($code -eq 0) $reason }
    Update-InstalledFileState
    $s.Verdict = $verdict; $s.Reason = $reason; $s.ExitCode = $code; $s.Finished = Get-CairoTime
    Save-Record
}

# ------------------------------------------------------------------ outside effects (replaced by fakes in the tests)
function ConvertTo-ArgString([string[]]$argv) {
    # Windows command-line quoting (CommandLineToArgvW rules) for ProcessStartInfo.Arguments.
    $parts = foreach ($a in $argv) {
        if ($a -ne '' -and $a -notmatch '[\s"]') { $a; continue }
        $sb = [Text.StringBuilder]::new('"'); $bs = 0
        foreach ($ch in $a.ToCharArray()) {
            if ($ch -eq '\') { $bs++; continue }
            if ($ch -eq '"') { [void]$sb.Append('\', 2 * $bs + 1); [void]$sb.Append('"'); $bs = 0; continue }
            if ($bs) { [void]$sb.Append('\', $bs); $bs = 0 }
            [void]$sb.Append($ch)
        }
        if ($bs) { [void]$sb.Append('\', 2 * $bs) }
        [void]$sb.Append('"'); $sb.ToString()
    }
    return ($parts -join ' ')
}

function Invoke-Logged([string]$file, [string[]]$argv, [string]$cwd = '', [switch]$Quiet) {
    # Runs a console program, appends its stdout+stderr to the log, returns @{ Code; Out; Err }.
    $psi = [Diagnostics.ProcessStartInfo]::new($file, (ConvertTo-ArgString $argv))
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($cwd) { $psi.WorkingDirectory = $cwd }
    $p = [Diagnostics.Process]::Start($psi)
    $errTask = $p.StandardError.ReadToEndAsync()
    $out = $p.StandardOutput.ReadToEnd(); $p.WaitForExit(); $err = $errTask.Result
    if (-not $Quiet) {
        if ($out) { [IO.File]::AppendAllText($script:S.Log, $out, $script:Utf8) }
        if ($err) { [IO.File]::AppendAllText($script:S.Log, $err, $script:Utf8) }
    }
    return @{ Code = $p.ExitCode; Out = $out; Err = $err }
}

function Invoke-ExeWait([string]$exe, [string[]]$argv) {
    # start "" /wait <exe> <args>: a windowed exe (the self-tests), no output capture.
    $psi = [Diagnostics.ProcessStartInfo]::new($exe, (ConvertTo-ArgString $argv))
    $psi.UseShellExecute = $false
    $p = [Diagnostics.Process]::Start($psi); $p.WaitForExit(); return $p.ExitCode
}

function Start-App([string]$exe, [string[]]$argv) {
    # start "" <exe> <args>: launch and do not wait (the running bot).
    $psi = [Diagnostics.ProcessStartInfo]::new($exe, (ConvertTo-ArgString $argv))
    $psi.UseShellExecute = $true; $psi.WorkingDirectory = $script:S.Src
    [void][Diagnostics.Process]::Start($psi)
}

function Get-PowerShellExe { return [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName }

function Invoke-Helper([string[]]$argv) {
    $chk = [IO.Path]::Combine($script:S.StageSrc, 'installer_check.ps1')
    return Invoke-Logged (Get-PowerShellExe) (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $chk) + $argv) -Quiet
}

function Get-FileSha([string]$path) {
    # SHA-256 via installer_check.ps1 (staging copy, as before). '' on any failure; the reason goes to the log.
    $r = Invoke-Helper @('hash', $path)
    if ($r.Err) { Write-Log $r.Err.TrimEnd() }
    if ($r.Code -ne 0) { return '' }
    return $r.Out.Trim()
}

function Wait-Ping([string]$buildId, [int]$seconds) {
    # The RUNNING ZackBot must answer with this build id and prove it holds the session token (installer_check.ps1 ping).
    $r = Invoke-Helper @('ping', $buildId, [string]$seconds)
    if ($r.Out) { Write-Log $r.Out.TrimEnd() }
    if ($r.Err) { Write-Log $r.Err.TrimEnd() }
    return ($r.Code -eq 0)
}

function Copy-FileSafe([string]$from, [string]$to) {
    try { [IO.File]::Copy($from, $to, $true); return $true } catch { Write-Log ('  copy failed: ' + $_.Exception.Message); return $false }
}

function Remove-FileSafe([string]$path) {
    try { if ([IO.File]::Exists($path)) { [IO.File]::Delete($path) } } catch { Write-Log ('  delete failed: ' + $_.Exception.Message) }
}

function Remove-Stage([string]$dir) {
    # rmdir /s /q (also clears read-only files, like the CMD installer did).
    if (-not [IO.Directory]::Exists($dir)) { return }
    [void](Invoke-Logged 'cmd.exe' @('/d', '/c', 'rmdir', '/s', '/q', $dir))
}

function Find-Python {
    # "py" launcher if installed, else "python" (as before).
    foreach ($d in ([string]$env:PATH).Split([IO.Path]::PathSeparator)) {
        if ($d -and [IO.File]::Exists([IO.Path]::Combine($d.Trim('"'), 'py.exe'))) { return 'py' }
    }
    return 'python'
}

function Get-ZackBotProcessCount { return @([Diagnostics.Process]::GetProcessesByName('ZackBot')).Count }

function Stop-Bot {
    # Stop ZackBot.exe and a source-run "ZackBot\src\app.py", then wait up to 15 s. $false = it did not stop.
    foreach ($p in [Diagnostics.Process]::GetProcessesByName('ZackBot')) {
        try { $p.Kill() } catch { Write-Log ('  stop: ' + $_.Exception.Message) }
    }
    try {
        [void][Reflection.Assembly]::LoadWithPartialName('System.Management')
        $q = [Management.ManagementObjectSearcher]::new('SELECT ProcessId, CommandLine FROM Win32_Process')
        foreach ($o in $q.Get()) {
            if ([string]$o['CommandLine'] -match 'ZackBot\\src\\app\.py') {
                try { [Diagnostics.Process]::GetProcessById([int]$o['ProcessId']).Kill() } catch { Write-Log ('  stop: ' + $_.Exception.Message) }
            }
        }
    } catch { Write-Log ('  stop (source-run check): ' + $_.Exception.Message) }
    for ($i = 0; $i -lt 15; $i++) {
        if ((Get-ZackBotProcessCount) -eq 0) { return $true }
        Wait-Seconds 1
    }
    return $false
}

function Update-Shortcuts {
    try {
        $w = [Activator]::CreateInstance([Type]::GetTypeFromProgID('WScript.Shell'))
        foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
            $sc = $w.CreateShortcut([IO.Path]::Combine($d, 'ZackBot.lnk'))
            $sc.TargetPath = $script:S.Exe; $sc.Arguments = ''; $sc.WorkingDirectory = $script:S.AppDir
            $sc.IconLocation = $script:S.Exe + ',0'; $sc.Description = 'ZackBot trading app'; $sc.Save()
        }
        return $true
    } catch { Write-Log ('  shortcuts: ' + $_.Exception.Message); return $false }
}

function Wait-Seconds([int]$n) { [Threading.Thread]::Sleep(1000 * $n) }
function Invoke-Pause { [void](Invoke-ExeWait 'cmd.exe' @('/d', '/c', 'pause')) }   # prompt goes straight to the console
function New-BuildId { return [DateTime]::Now.ToString('yyyyMMdd-HHmmss') }

function Read-JsonString([string]$path, [string]$key) {
    try {
        $m = [regex]::Match([IO.File]::ReadAllText($path), '"' + [regex]::Escape($key) + '"\s*:\s*"([^"]*)"')
        if ($m.Success) { return $m.Groups[1].Value }
    } catch { }
    return ''
}

# ------------------------------------------------------------------ steps
function Invoke-Banner {
    $s = $script:S
    [void][IO.Directory]::CreateDirectory($s.Root)
    $s.Started = Get-CairoTime
    [IO.File]::WriteAllText($s.Log, "==== ZackBot build $([DateTime]::Now.ToString('ddd MM/dd/yyyy HH:mm:ss.ff')) ====`r`n", $script:Utf8)
    Write-Say ''
    Write-Say ' The running ZackBot is NOT touched until the new build has passed every check.'
    Write-Say ' If the new version does not start correctly, the previous one is put back automatically.'
    Write-Say ''
    if ($s.Drill) {
        Write-Say ' *** ROLLBACK DRILL ***'
        Write-Say ' The new build is installed and then deliberately made to fail its launch. The installer must put the'
        Write-Say ' current version back and prove it is running again. The bot is stopped for about 1-2 minutes;'
        Write-Say ' your stops stay on Binance during that time.'
        Write-Say ''
        Write-Log '  ROLLBACK DRILL requested'
        Start-Step 'drill_precheck'
        if (-not [IO.File]::Exists($s.Exe)) { Stop-Install 'rollback drill needs an installed ZackBot to roll back to - install normally first; nothing was changed' }
    }
}

function Invoke-Step1Stage {
    $s = $script:S
    Write-Say '[1/8] Copying the source to a clean staging folder...'
    Start-Step 'stage'
    Remove-Stage $s.Stage
    if ([IO.Directory]::Exists($s.Stage)) { Stop-Install "could not clear the staging folder $($s.Stage) (a file is open there?)" }
    # Keep the manifest-controlled candle datasets through the safety-test step. T04d's CI tests validate every file in
    # DATA_MANIFEST.json; excluding these folders made a normal installer fail before touching the running bot. They are
    # removed from this temporary staging tree immediately after the tests and are never bundled or mirrored.
    $r = Invoke-Logged 'robocopy' @(($s.Src.TrimEnd('\') + '\.'), $s.StageSrc, '/MIR',
        '/XD', '__pycache__', '.git', '.git_failed_*', 'dev_out', 'data_market',
        '/XF', 'build_app.bat', 'rollback_drill.bat', 'installer.ps1', 'setup_git.bat', 'config.env', '*.log', 'session.json',
        '*.tmp', '*.pkl', 'build_info.py', '/NFL', '/NDL', '/NJH', '/NJS')
    if ($r.Code -ge 8) { Stop-Install 'copying the source failed' }
    $s.BuildId = New-BuildId
    if (-not $s.BuildId) { Stop-Install 'copying the source failed' }
    [IO.File]::WriteAllText([IO.Path]::Combine($s.StageSrc, 'build_info.py'), "BUILD_ID = '$($s.BuildId)'`r`n", [Text.Encoding]::ASCII)
    Write-Log "  build id $($s.BuildId)"
    # The checksum helper must work in THIS environment before anything else (backup, swap and rollback need it).
    Start-Step 'checksum_helper'
    $pre = Get-FileSha ([IO.Path]::Combine($s.StageSrc, 'app.py'))
    if (-not $pre) { Stop-Install 'the checksum helper does not work in this window - nothing was changed; the reason is in the log' }
    Write-Log '  checksum helper ok'
    Complete-Step $true "app.py sha256 $pre"
    return $pre
}

function Invoke-BuildSteps {
    $s = $script:S
    Write-Say '[2/8] Preparing the private build environment (pinned versions only)...'
    Start-Step 'build_env'
    $bpy = [IO.Path]::Combine($s.Venv, 'Scripts', 'python.exe')
    if (-not [IO.File]::Exists($bpy)) { [void](Invoke-Logged (Find-Python) @('-m', 'venv', $s.Venv)) }
    if (-not [IO.File]::Exists($bpy)) { Stop-Install 'could not create the build environment - is Python installed?' }
    $r = Invoke-Logged $bpy @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', [IO.Path]::Combine($s.StageSrc, 'requirements-dev.txt'))
    if ($r.Code -ne 0) { Stop-Install 'installing the pinned libraries failed (internet / pip problem)' }

    Write-Say '[3/8] Checking the build libraries...'
    Start-Step 'libs'
    $r = Invoke-Logged $bpy @('-c', "import pandas, numpy, requests, PyInstaller, pytest; print('libs ok', pandas.__version__, numpy.__version__, PyInstaller.__version__)")
    if ($r.Code -ne 0) { Stop-Install 'the build libraries do not load' }

    Write-Say '[4/8] Running the safety tests...'
    Start-Step 'safety_tests'
    $r = Invoke-Logged $bpy @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-m', 'not slow', 'tests') $s.StageSrc
    if ($r.Code -ne 0) { Stop-Install 'the safety tests FAILED - this build is not safe to install' }
    foreach ($name in @('data', 'data1h', 'data_long')) {
        $path = [IO.Path]::Combine($s.StageSrc, $name)
        try { if ([IO.Directory]::Exists($path)) { [IO.Directory]::Delete($path, $true) } }
        catch { Stop-Install "could not remove temporary test data from staging ($name)" }
        if ([IO.Directory]::Exists($path)) { Stop-Install "could not remove temporary test data from staging ($name)" }
    }

    Write-Say '[5/8] Building ZackBot.exe (takes 1-3 minutes)...'
    Start-Step 'pyinstaller'
    $src = $s.StageSrc
    $r = Invoke-Logged $bpy @('-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed', '--name', 'ZackBot',
        '--icon', [IO.Path]::Combine($src, 'zackbot.ico'),
        '--add-data', ([IO.Path]::Combine($src, 'panel.html') + ';.'),
        '--add-data', ([IO.Path]::Combine($src, 'research') + ';research'),
        '--collect-data', 'tzdata',
        '--distpath', [IO.Path]::Combine($s.Stage, 'dist'), '--workpath', [IO.Path]::Combine($s.Stage, 'work'),
        '--specpath', [IO.Path]::Combine($s.Stage, 'work'), [IO.Path]::Combine($src, 'app.py'))
    if ($r.Code -ne 0 -or -not [IO.File]::Exists($s.NewExe)) { Stop-Install 'PyInstaller failed to build the exe' }

    Write-Say '[6/8] Self-test of the NEW exe (bundle, data files, time zones, version)...'
    Start-Step 'selftest_new'
    $st = [IO.Path]::Combine($s.Stage, 'selftest.json')
    [void](Invoke-ExeWait $s.NewExe @('--selftest', $st))
    if (-not [IO.File]::Exists($st)) { Stop-Install 'the new exe failed its self-test (missing files or wrong version)' }
    $raw = [IO.File]::ReadAllText($st)
    Write-Log ('selftest: ' + ($raw -replace '\s*[\r\n]+\s*', ' ').Trim())
    $okFlag = [regex]::IsMatch($raw, '"ok"\s*:\s*true')
    if (-not ($okFlag -and (Read-JsonString $st 'build') -eq $s.BuildId)) { Stop-Install 'the new exe failed its self-test (missing files or wrong version)' }
    Start-Step 'hash_new'
    $s.NewHash = Get-FileSha $s.NewExe
    if (-not $s.NewHash) { Stop-Install 'could not checksum the new exe - the reason is in the log' }
    Write-Log "  new exe sha256 $($s.NewHash)"
    Complete-Step $true $s.NewHash
}

function Invoke-Step7Backup {
    $s = $script:S
    Write-Say '[7/8] Backing up the current version (verified) before touching it...'
    Start-Step 'backup'
    [void][IO.Directory]::CreateDirectory($s.AppDir)
    Remove-FileSafe $s.Prev
    if ([IO.File]::Exists($s.Prev)) { Stop-Install 'could not remove the old backup ZackBot.prev.exe (is it running?)' }
    if (-not [IO.File]::Exists($s.Exe)) { Complete-Step $true 'no installed version (first install)'; return }
    if (-not (Copy-FileSafe $s.Exe $s.Prev)) { Stop-Install 'backing up the current ZackBot.exe failed or the copy does not match - nothing was changed' }
    $s.OldHash = Get-FileSha $s.Exe
    $prevHash = Get-FileSha $s.Prev
    if (-not $s.OldHash -or $s.OldHash -ne $prevHash) { Stop-Install 'backing up the current ZackBot.exe failed or the copy does not match - nothing was changed' }
    $s.HaveOld = $true
    $s.FileHash = $s.OldHash
    Write-Log "  backup ok sha256 $($s.OldHash)"
    # Which build is the previous version? Needed to PROVE a rollback really brought it back (ping with its build id).
    $ost = [IO.Path]::Combine($s.Stage, 'old_selftest.json')
    Remove-FileSafe $ost
    [void](Invoke-ExeWait $s.Prev @('--selftest', $ost))
    $s.OldBuild = Read-JsonString $ost 'build'
    Write-Log "  previous build $($s.OldBuild)"
    if ($s.Drill -and -not $s.OldBuild) { Stop-Install "rollback drill: could not read the installed version's build id, so a rollback could not be proven - nothing was changed" }
    Complete-Step $true "previous build $($s.OldBuild) sha256 $($s.OldHash)"
}

function Invoke-Step8Install {
    # Returns $true when the new build is installed and confirmed running; $false = roll back.
    $s = $script:S
    Write-Say '[8/8] Installing: stopping the old ZackBot, swapping the exe, starting and checking the new one...'
    Start-Step 'stop'
    $s.Stopped = $true; $s.StopCalled = $true            # from here on, any unexpected error rolls back
    if (-not (Stop-Bot)) {
        Complete-Step $false 'ZackBot did not stop within 15 s (a stop was attempted)'
        Invoke-StopRecovery                                  # never swaps; proves the old build runs, then fails
    }
    $s.StopConfirmed = $true
    Start-Step 'swap'
    $copied = $false
    for ($try = 0; $try -lt 5; $try++) {
        if (Copy-FileSafe $s.NewExe $s.Exe) { $copied = $true; break }
        if ($try -lt 4) { Wait-Seconds 2 }
    }
    if (-not $copied) { Complete-Step $false 'the new exe could not be copied into place (5 tries)'; return $false }
    $inst = Get-FileSha $s.Exe
    if ($inst) { $s.FileHash = $inst }
    if ($inst -ne $s.NewHash) { Complete-Step $false 'the installed exe does not match the new build hash'; return $false }
    Start-Step 'launch'
    $launch = @(); $wait = 60
    if ($s.Drill) { $launch = @('--simulate-failed-launch'); $wait = 20; Write-Log '  DRILL: launching the new exe with --simulate-failed-launch' }
    Start-App $s.Exe $launch
    if (-not (Wait-Ping $s.BuildId $wait)) { Complete-Step $s.Drill "build $($s.BuildId) did not answer within $wait s (the drill requires exactly this)"; return $false }
    if ($s.Drill) {
        # The untrusted new build answered although it was told to fail: never leave it running - restore the previous
        # verified build (Invoke-Rollback), then fail as an INVALID drill.
        $s.DrillInvalid = $true
        Write-Log "  DRILL INVALID: build $($s.BuildId) answered although it was launched with --simulate-failed-launch - rolling back"
        Complete-Step $false "INVALID: build $($s.BuildId) answered although it was told to fail"
        return $false
    }
    Complete-Step $true "build $($s.BuildId) answered with HMAC proof"
    return $true
}

function Invoke-StopRecovery {
    # A stop was attempted but ZackBot did not (fully) stop. The exe was NOT replaced, but the runtime state is unknown:
    # prove the previous build still answers; if not, make ONE bounded restart of the verified old exe and require its
    # build-specific HMAC answer. Always ends the run as a failure.
    $s = $script:S
    $s.Stopped = $false; $s.RecTried = $true
    Start-Step 'stop_recovery'
    $why = 'the running ZackBot did not stop - the exe was NOT replaced'
    if (-not $s.OldBuild) {
        $s.RecNote = 'no verifiable previous build id, so whether ZackBot is running could not be proven - open ZackBot and check'
    } elseif (Wait-Ping $s.OldBuild 20) {
        $s.RecOk = $true; $s.RecNote = "the previous version $($s.OldBuild) is still running and answered with HMAC proof"
    } elseif ((Get-FileSha $s.Exe) -ne $s.OldHash) {
        $s.RecNote = 'the previous version did not answer and the installed exe no longer matches its verified hash, so it was not restarted - open ZackBot and check'
    } else {
        Start-App $s.Exe @()
        if (Wait-Ping $s.OldBuild 60) { $s.RecOk = $true; $s.RecNote = "the previous version $($s.OldBuild) was restarted once and confirmed running" }
        else { $s.RecNote = 'the previous version did NOT confirm it is running after one restart - open ZackBot and check' }
    }
    Write-Log "STOP_RECOVERY verified=$([int]$s.RecOk) - $($s.RecNote)"
    Complete-Step $s.RecOk $s.RecNote
    Stop-Install "$why; $($s.RecNote)"
}

function Invoke-Finish {
    # Only now, with the new version confirmed running, update the source mirror and the shortcuts (a rollback must not
    # leave the mirror describing a build that is not installed).
    $s = $script:S
    $s.Stopped = $false
    Start-Step 'mirror'
    $r = Invoke-Logged 'robocopy' @($s.StageSrc, $s.Dst, '/MIR', '/XD', 'tests', 'data_market', '/NFL', '/NDL', '/NJH', '/NJS')
    if ($r.Code -ge 8) { $s.Warn += " [source copy in $($s.Dst) failed]"; $s.WarnList.Add("source copy in $($s.Dst) failed") }
    Complete-Step ($r.Code -lt 8) "robocopy exit $($r.Code)"
    Start-Step 'shortcuts'
    $okSc = Update-Shortcuts
    if (-not $okSc) { $s.Warn += ' [desktop/start-menu shortcut not updated]'; $s.WarnList.Add('desktop/start-menu shortcut not updated') }
    Complete-Step $okSc ''
    Write-Log "BUILD_DONE build=$($s.BuildId) sha256=$($s.NewHash) target=$($s.Exe)"
    if ($s.Warn) { Write-Log "BUILD_WARNINGS$($s.Warn)" }
    Set-Verdict 'BUILD_DONE' '' 0
    Write-Say ''
    Write-Say "Done - build $($s.BuildId) installed and confirmed running (Settings shows the same build id)."
    if ($s.Warn) {
        Write-Say "Warnings:$($s.Warn)"
        if (-not (Test-NoPause)) { Invoke-Pause }
    }
    Wait-Seconds 5
    return 0
}

function Invoke-Rollback {
    $s = $script:S
    $s.InRollback = $true; $s.RbTried = $true
    if ($s.Current) { Complete-Step $false 'interrupted - rolling back' }
    Start-Step 'rollback'
    if ($s.DrillInvalid) { Write-Log "ROLLBACK: invalid drill - build $($s.BuildId) answered although it was told to fail" }
    else { Write-Log "ROLLBACK: the new version did not install or did not answer with build $($s.BuildId)" }
    [void](Stop-Bot)
    if (-not $s.HaveOld) {
        Remove-FileSafe $s.Exe
        if ([IO.File]::Exists($s.Exe)) {
            Stop-Install "the new version did not start correctly (there was no previous version to restore) and the failed exe could NOT be removed - delete $($s.Exe) before starting ZackBot"
        }
        Stop-Install 'the new version did not start correctly (there was no previous version to restore)'
    }
    [void](Copy-FileSafe $s.Prev $s.Exe)
    $s.RbHash = Get-FileSha $s.Exe
    if ($s.RbHash) { $s.FileHash = $s.RbHash }
    if (-not $s.RbHash -or $s.RbHash -ne $s.OldHash) {
        Stop-Install "the new version failed AND restoring the previous exe failed - ZackBot.prev.exe is still in $($s.AppDir)"
    }
    Write-Log "  restored exe sha256 $($s.RbHash) equals the pre-install hash"
    Start-App $s.Exe @()
    if (-not $s.OldBuild) {
        $s.RbNote = 'the previous version was restored and restarted, but its build id could not be read so it was not verified'
    } elseif (-not (Wait-Ping $s.OldBuild 60)) {
        $s.RbNote = 'the previous version was restored but did NOT confirm it is running - open ZackBot and check'
    } else {
        $s.RbOk = $true; $s.RbNote = "the previous version $($s.OldBuild) was restored and confirmed running"
    }
    Write-Log "ROLLBACK_RESULT verified=$([int]$s.RbOk) - $($s.RbNote)"
    Complete-Step $s.RbOk $s.RbNote
    if ($s.DrillInvalid) { Stop-Install "ROLLBACK DRILL INVALID: the new exe answered although it was told to fail (check --simulate-failed-launch) - $($s.RbNote)" }
    if (-not $s.Drill) { Stop-Install "the new version did not start correctly - $($s.RbNote)" }
    if (-not $s.RbOk) { Stop-Install "ROLLBACK DRILL FAILED - $($s.RbNote)" }
    Set-Verdict 'DRILL_PASSED' '' 0
    Write-Log "DRILL_PASSED new=$($s.BuildId) failed its launch on purpose, restored=$($s.OldBuild) sha256=$($s.RbHash) confirmed running"
    Write-Say ''
    Write-Say " ROLLBACK DRILL PASSED: the new build $($s.BuildId) failed its launch on purpose, and the installer"
    Write-Say " restored build $($s.OldBuild) - same SHA-256 as before - and proved it is running again."
    Write-Say " Details: $($s.Log)"
    Write-Say ''
    if (-not (Test-NoPause)) { Invoke-Pause }
    return 0
}

function Invoke-Fail([string]$why) {
    $s = $script:S
    Write-Log "BUILD_FAILED: $why"
    Set-Verdict 'BUILD_FAILED' $why 1
    Write-Say ''
    Write-Say " *** BUILD FAILED: $why"
    Write-Say " Details: $($s.Log)"
    Write-Say ''
    if (-not ($s.Preflight -or $s.BuildCheck -or (Test-NoPause))) { Invoke-Pause }
    return 1
}

function Invoke-Flow {
    $s = $script:S
    Invoke-Banner
    $pre = Invoke-Step1Stage
    if ($s.Preflight) {
        Write-Log "PREFLIGHT_OK build=$($s.BuildId) app.py sha256=$pre"
        Write-Say "PREFLIGHT_OK app.py sha256=$pre"
        Remove-Stage $s.Stage
        Set-Verdict 'PREFLIGHT_OK' '' 0
        return 0
    }
    Invoke-BuildSteps
    if ($s.BuildCheck) {
        Write-Log "BUILDCHECK_OK build=$($s.BuildId) sha256=$($s.NewHash) exe=$($s.NewExe)"
        Write-Say "BUILDCHECK_OK build $($s.BuildId) sha256 $($s.NewHash)"
        Set-Verdict 'BUILDCHECK_OK' '' 0
        return 0
    }
    Invoke-Step7Backup
    if (Invoke-Step8Install) { return Invoke-Finish }
    return Invoke-Rollback
}

function Split-CmdLine([string]$line) {
    # Tokens of a CMD-style command line: whitespace separates outside quotes, quotes toggle and are dropped.
    $tokens = [Collections.Generic.List[string]]::new(); $cur = [Text.StringBuilder]::new(); $q = $false; $have = $false
    foreach ($ch in $line.ToCharArray()) {
        if ($ch -eq '"') { $q = -not $q; $have = $true; continue }
        if (-not $q -and ($ch -eq ' ' -or $ch -eq "`t")) {
            if ($have) { $tokens.Add($cur.ToString()); [void]$cur.Clear(); $have = $false }
            continue
        }
        [void]$cur.Append($ch); $have = $true
    }
    if ($have) { $tokens.Add($cur.ToString()) }
    return ,$tokens
}

function Get-LauncherMode([string]$cmdline) {
    # build_app.bat never expands its arguments; it hands over its own raw command line (CMDCMDLINE, via delayed
    # expansion = not re-parsed). The launcher is the FIRST token that is build_app.bat itself (a path ending in
    # \build_app.bat); everything after it must be nothing (= install) or exactly ONE non-destructive mode
    # (preflight / buildcheck). The drill has its own fixed launcher (rollback_drill.bat). Anything else -> $null.
    # Hostile raw CMD syntax (e.g. x"&cmd) is parsed by the CALLING shell before this file runs - out of scope.
    $t = Split-CmdLine $cmdline
    $k = -1
    for ($n = 0; $n -lt $t.Count; $n++) {
        $tok = $t[$n].Trim().ToLowerInvariant()
        if ($tok -eq 'build_app.bat' -or $tok.EndsWith('\build_app.bat') -or $tok.EndsWith('/build_app.bat')) { $k = $n; break }
    }
    if ($k -lt 0) { return $null }
    $rest = @(for ($n = $k + 1; $n -lt $t.Count; $n++) { if ($t[$n].Trim()) { $t[$n].Trim() } })
    if ($rest.Count -eq 0) { return 'install' }
    if ($rest.Count -eq 1 -and @('preflight', 'buildcheck') -contains $rest[0].ToLowerInvariant()) { return $rest[0].ToLowerInvariant() }
    return $null
}

function Invoke-Main([string]$mode, [string]$src, [string]$localAppData) {
    if (@('install', 'drill', 'preflight', 'buildcheck') -notcontains $mode) {
        Write-Say "usage: installer.ps1 install|drill|preflight|buildcheck (got '$mode')"
        if ($mode.StartsWith('refused: ')) {
            Write-Say 'build_app.bat takes no argument (install) or exactly one of: preflight, buildcheck. Nothing was changed.'
            Write-Say 'The rollback drill is rollback_drill.bat. From an open Command Prompt use: cmd /c build_app.bat [preflight|buildcheck]'
        }
        return 2
    }
    $script:S = New-InstallerState $mode $src $localAppData
    $script:FailWhy = ''
    try {
        return (Invoke-Flow)
    } catch {
        $why = $script:FailWhy
        if (-not $why) { $why = 'unexpected installer error: ' + $_.Exception.Message; if ($script:S.Current) { Complete-Step $false ('unexpected: ' + $_.Exception.Message) } }
        if (-not $script:FailWhy -and $script:S.Stopped -and -not $script:S.InRollback) {
            # An unexpected error after the old bot was stopped: never leave it down - restore the previous version.
            Write-Log "UNEXPECTED: $($_.Exception.Message) at $($_.InvocationInfo.PositionMessage -replace '\s+', ' ')"
            $script:FailWhy = ''
            try { return (Invoke-Rollback) } catch {
                $why2 = $script:FailWhy; if (-not $why2) { $why2 = 'unexpected installer error during rollback: ' + $_.Exception.Message }
                return (Invoke-Fail "$why; $why2")
            }
        }
        return (Invoke-Fail $why)
    }
}

if (-not $NoRun) {
    $src = [IO.Path]::GetDirectoryName($MyInvocation.MyCommand.Path)
    if ($FromLauncher) {
        $Mode = Get-LauncherMode ([string]$env:ZB_CMDLINE)
        if (-not $Mode) { $Mode = 'refused: ' + [string]$env:ZB_CMDLINE }          # Invoke-Main prints usage, exit 2
    }
    exit (Invoke-Main $Mode $src $env:LOCALAPPDATA)
}
