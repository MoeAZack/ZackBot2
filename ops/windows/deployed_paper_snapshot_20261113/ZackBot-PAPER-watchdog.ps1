$ErrorActionPreference='Stop'
$base='F:\ZackBot2_data'
$pythonPath='C:\Users\princ\AppData\Local\Programs\Python\Python312\python.exe'
$instances=@(
 @{port=8765;root="$base\cohort_launch_20261108";exe='C:\Users\princ\AppData\Local\ZackBot\app\ZackBot.exe';args=@('--no-window')},
 @{port=8766;root="$base\OLD3-account2";exe=$pythonPath;args=@("$base\OLD3-account2\start_old3.py",'--no-window')},
 @{port=8767;root="$base\ACCOUNT3-breakeven";exe=$pythonPath;args=@("$base\ACCOUNT3-breakeven\start_account3.py",'--no-window')},
 @{port=8768;root="$base\EXPLORATION-account4";exe=$pythonPath;args=@("$base\EXPLORATION-account4\start_account4.py",'--no-window')}
)
$next=@{}
while($true){
 $listeners=@(Get-NetTCPConnection -State Listen | Select-Object -ExpandProperty LocalPort)
 foreach($item in $instances){
  $port=$item.port
  if($port -in $listeners -or ($next[$port] -and [DateTime]::UtcNow -lt $next[$port])){continue}
  $data=Join-Path $item.root 'runtime\ZackBot'
  $config=Join-Path $data 'config.env'
  if(!(Test-Path -LiteralPath $config) -or !(Test-Path -LiteralPath $item.exe)){continue}
  # The launcher enforces PAPER for accounts 2–4. For the installed account 1 app, check mode
  # without retrieving credential values. Select-String outputs only a boolean.
  if($port -eq 8765 -and !(Select-String -LiteralPath $config -Pattern '^MODE=paper\s*$' -Quiet)){continue}
  $session=Join-Path $data 'session.json'
  if(Test-Path -LiteralPath $session){
   try {$owner=Get-Content -LiteralPath $session -Raw | ConvertFrom-Json}
   catch {continue}
   if($owner.port -ne $port){continue}
   if(Get-Process -Id $owner.pid -ErrorAction SilentlyContinue){continue}
  }
  $next[$port]=[DateTime]::UtcNow.AddSeconds(60)
  $prior=$env:LOCALAPPDATA
  try {
   if($port -eq 8765){$env:LOCALAPPDATA=Join-Path $item.root 'runtime'}
   Start-Process -FilePath $item.exe -ArgumentList $item.args -WorkingDirectory $item.root -WindowStyle Hidden | Out-Null
  } finally {$env:LOCALAPPDATA=$prior}
 }
 Start-Sleep -Seconds 8
}
