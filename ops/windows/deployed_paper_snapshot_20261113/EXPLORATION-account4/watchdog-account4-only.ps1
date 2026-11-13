$ErrorActionPreference='Stop'
$accountRoot='F:\ZackBot2_data\EXPLORATION-account4'
$pythonPath='C:\Users\princ\AppData\Local\Programs\Python\Python312\python.exe'
$launcherPath=Join-Path $accountRoot 'start_account4.py'
while($true){
 try {
  $listening=Get-NetTCPConnection -State Listen -LocalPort 8768 -ErrorAction SilentlyContinue
  if(!$listening){
   $sessionPath=Join-Path $accountRoot 'runtime\ZackBot\session.json'
   $ownerAlive=$false
   if(Test-Path -LiteralPath $sessionPath){
    $accountSession=Get-Content -LiteralPath $sessionPath -Raw | ConvertFrom-Json
    if($accountSession.port -ne 8768){throw 'Wrong instance session port'}
    $ownerAlive=[bool](Get-Process -Id $accountSession.pid -ErrorAction SilentlyContinue)
   }
   if(!$ownerAlive){
    Start-Process -FilePath $pythonPath -ArgumentList @($launcherPath,'--no-window') -WorkingDirectory $accountRoot -WindowStyle Hidden | Out-Null
    Start-Sleep -Seconds 30
   }
  }
 } catch { }
 Start-Sleep -Seconds 8
}